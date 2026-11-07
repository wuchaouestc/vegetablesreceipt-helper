"""完整框与行列关联：polygon → 轴对齐包围盒、分栏、行聚类、标题从属、明细条目解析。

设计约束（来自《手写单据图片识别修改方案》第八节）：
  · 编码、数量、单位必须属于**同一明细位置**或明确的**标题从属关系**；
    数字在全页出现不能确认本行数量。
  · 跨栏单据先确定栏，再确定行，不能全页按 y 坐标简单聚行。
  · 无法可靠定位时转人工，不猜区域反复识别。

本模块只做几何与文本解析：不联网、不读图片、不依赖 Tk，可离线单测。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# 单位表：覆盖现网出现的重量/计数单位，允许后缀修饰。
UNIT_PATTERN = (r'(?:公斤|千克|kg|斤|个|只|袋|包|件|箱|条|把|盒|支|瓶|片|棵|枚|扎|提|罐|卷|对|双|打)')
# 明细条目：编码:数量 单位。编码允许字母前缀与前导零，数量允许小数。
ENTRY_RE = re.compile(
    r'(?<![0-9A-Za-z.])([A-Za-z]{0,2}\d{1,6}[A-Za-z]{0,2})\s*[:：]\s*'
    r'(\d+(?:\.\d+)?)\s*(' + UNIT_PATTERN + r')', re.I)
# 无冒号写法：编码 数量 单位（手写单经常省略冒号，「1136 2.5斤」）。
ENTRY_RE_NOCOLON = re.compile(
    r'(?<![0-9A-Za-z.])([A-Za-z]{0,2}\d{1,6}[A-Za-z]{0,2})\s+'
    r'(\d+(?:\.\d+)?)\s*(' + UNIT_PATTERN + r')', re.I)
# 缺单位写法：编码:数量，单位读不出（「1136:2.5」）。
ENTRY_RE_NOUNIT = re.compile(
    r'(?<![0-9A-Za-z.])([A-Za-z]{0,2}\d{1,6}[A-Za-z]{0,2})\s*[:：]\s*'
    r'(\d+(?:\.\d+)?)\s*$', re.I)
# 数量写成区间（12:1-5个）：无法确定取哪个值，只能记疑点，不能猜。
RANGE_RE = re.compile(
    r'(?<![0-9A-Za-z.])([A-Za-z]{0,2}\d{1,6}[A-Za-z]{0,2})\s*[:：]\s*'
    r'(\d+\s*[-—~～]\s*\d+)\s*(' + UNIT_PATTERN + r')', re.I)
# 同行字段装配用：整块是纯编码 / 纯「数量+单位」，才能拼成一条明细。
CODE_ONLY_RE = re.compile(r'^[A-Za-z]{0,2}\d{1,6}[A-Za-z]{0,2}$')
QTY_UNIT_ONLY_RE = re.compile(r'^(\d+(?:\.\d+)?)\s*(' + UNIT_PATTERN + r')$')
DIGIT_RE = re.compile(r'\d')
CJK_RE = re.compile(r'[\u4e00-\u9fff]')

# 标题（品类）的常见后缀噪声，判定标题时忽略。
_TITLE_NOISE = re.compile(r'[\s()（）\[\]【】:：,，。.]+')


def normalize_text(value):
    """比较用的规范化：NFKC + 去掉所有空白。"""
    if value is None:
        return ''
    return ''.join(unicodedata.normalize('NFKC', str(value)).split())


@dataclass(frozen=True)
class TextBlock:
    """一个 OCR 文字块，携带**完整**包围盒（不是多边形首点）。"""
    text: str
    bbox: tuple                       # (x0, y0, x1, y1) 轴对齐包围盒
    polygon: tuple = ()               # 原始多边形点
    confidence: float | None = None
    source: str = 'ocr'
    index: int = 0

    @property
    def width(self):
        return max(0.0, self.bbox[2] - self.bbox[0])

    @property
    def height(self):
        return max(0.0, self.bbox[3] - self.bbox[1])

    @property
    def center_x(self):
        return (self.bbox[0] + self.bbox[2]) / 2.0

    @property
    def center_y(self):
        return (self.bbox[1] + self.bbox[3]) / 2.0

    def as_dict(self):
        return {'text': self.text, 'bbox': list(self.bbox), 'polygon': [list(p) for p in self.polygon],
                'confidence': self.confidence, 'source': self.source, 'index': self.index}


@dataclass
class Entry:
    """从文字块里解析出来的一条明细（编码/数量/单位）。"""
    code: str
    quantity: str | None
    unit: str | None
    text: str
    bbox: tuple
    block_index: int
    source: str = 'ocr'
    confidence: float | None = None
    ambiguous: str = ''               # 'range' 等：解析出来但不能当确值用
    heading: object = None           # 所属标题（build_layout 填充）
    heading_score: float = 0.0

    def as_dict(self):
        return {'code': self.code, 'quantity': self.quantity, 'unit': self.unit, 'text': self.text,
                'bbox': list(self.bbox), 'block_index': self.block_index, 'source': self.source,
                'confidence': self.confidence, 'ambiguous': self.ambiguous}


def _polygon_points(raw):
    points = []
    for point in raw or []:
        if isinstance(point, dict):
            x = point.get('X', point.get('x'))
            y = point.get('Y', point.get('y'))
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            x, y = point[0], point[1]
        else:
            continue
        try:
            x, y = float(x), float(y)
        except (TypeError, ValueError):
            continue
        if x != x or y != y:          # NaN
            continue
        points.append((x, y))
    return points


def normalize_blocks(raw, source='ocr'):
    """把各家 OCR 返回的块统一成 TextBlock，**补出完整包围盒**。

    兼容三种来源：
      · 腾讯云 polygon=[{'X':..,'Y':..}]（完整框的来源）
      · 现网缓存里的 {'text','x','y'}（只有首点，退化成零宽高的点框）
      · 直接给 bbox 的四元组
    """
    blocks = []
    for index, item in enumerate(raw or []):
        if not isinstance(item, dict):
            continue
        text = str(item.get('text', '') or '')
        points = _polygon_points(item.get('polygon'))
        if points:
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            bbox = (min(xs), min(ys), max(xs), max(ys))
        elif isinstance(item.get('bbox'), (list, tuple)) and len(item['bbox']) >= 4:
            try:
                x0, y0, x1, y1 = (float(v) for v in item['bbox'][:4])
            except (TypeError, ValueError):
                continue
            bbox = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        else:
            try:
                x = float(item.get('x', 0) or 0)
                y = float(item.get('y', 0) or 0)
            except (TypeError, ValueError):
                continue
            bbox = (x, y, x, y)
        confidence = item.get('confidence')
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            confidence = None
        blocks.append(TextBlock(text=text, bbox=bbox, polygon=tuple(points),
                                confidence=float(confidence) if confidence is not None else None,
                                source=str(item.get('source', source)), index=index))
    return blocks


def ov_ratio(a, b):
    """x 方向重合度 = 交集宽 / 较窄者宽。0~1，用于跨栏判定。"""
    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    narrow = min(a[2] - a[0], b[2] - b[0])
    return inter / narrow if narrow > 0 else 0.0


def v_overlap_ratio(a, b):
    """y 方向重合度 = 交集高 / 较矮者高。"""
    inter = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    short = min(a[3] - a[1], b[3] - b[1])
    return inter / short if short > 0 else 0.0


def is_heading(block, max_length=10):
    """标题（品类）判定：不含数字、含中文、长度适中。

    「1137:0.5斤」这种明细行、「南善」这种标题都符合文本特征，
    真正的区分靠下面的**标题从属**（x 重合 + 在明细行上方）。
    """
    text = normalize_text(block.text)
    if not text or len(text) > max_length:
        return False
    if DIGIT_RE.search(text):
        return False
    if not CJK_RE.search(text):
        return False
    return True


def _entry_bbox(block, start, end):
    """把条目在块文本中的字符位置映射成块内的 x 子区间。

    用途：一个 OCR 块可能横跨两栏（例如「1136:1个14:3个」），
    拆成两个条目后各自带自己的 x 区间，才能分别找到所属栏与标题。
    """
    length = max(1, len(block.text))
    x0, y0, x1, y1 = block.bbox
    width = x1 - x0
    left = x0 + width * (start / length)
    right = x0 + width * (end / length)
    return (left, y0, right, y1)


def parse_entries(block):
    """从一个文字块里解析明细条目（可多个），带 x 子区间。

    依次识别：区间写法 > 标准「编码:数量单位」> 无冒号「编码 数量单位」>
    缺单位「编码:数量」。后三种各自检查已占用区间，避免同一段文字被重复解析。
    缺单位的条目 `ambiguous='no_unit'`、无冒号的 `ambiguous='no_colon'`，
    都是「能解析但不确定」的状态，留给证据链标记，绝不静默当确定值用。
    """
    text = block.text or ''
    found, taken = [], []

    def _占用(start, end):
        return any(not (end <= s or start >= e) for s, e in taken)

    for match in RANGE_RE.finditer(text):
        taken.append((match.start(), match.end()))
        found.append(Entry(code=match.group(1), quantity=None, unit=match.group(3),
                           text=match.group(0), bbox=_entry_bbox(block, match.start(), match.end()),
                           block_index=block.index, source=block.source,
                           confidence=block.confidence, ambiguous='range'))
    for match in ENTRY_RE.finditer(text):
        if _占用(match.start(), match.end()):
            continue
        taken.append((match.start(), match.end()))
        found.append(Entry(code=match.group(1), quantity=match.group(2), unit=match.group(3),
                           text=match.group(0), bbox=_entry_bbox(block, match.start(), match.end()),
                           block_index=block.index, source=block.source, confidence=block.confidence))
    for match in ENTRY_RE_NOCOLON.finditer(text):
        if _占用(match.start(), match.end()):
            continue
        taken.append((match.start(), match.end()))
        found.append(Entry(code=match.group(1), quantity=match.group(2), unit=match.group(3),
                           text=match.group(0), bbox=_entry_bbox(block, match.start(), match.end()),
                           block_index=block.index, source=block.source,
                           confidence=block.confidence, ambiguous='no_colon'))
    for match in ENTRY_RE_NOUNIT.finditer(text):
        if _占用(match.start(), match.end()):
            continue
        taken.append((match.start(), match.end()))
        found.append(Entry(code=match.group(1), quantity=match.group(2), unit=None,
                           text=match.group(0), bbox=_entry_bbox(block, match.start(), match.end()),
                           block_index=block.index, source=block.source,
                           confidence=block.confidence, ambiguous='no_unit'))
    found.sort(key=lambda e: e.bbox[0])
    return found


def assemble_row_entries(blocks, entries):
    """同一行里编码和「数量+单位」被 OCR 拆成两个块时，把它们拼回一条明细。

    只兜底「没有任何块自己解析出条目」的残余块：把该行内残余的纯编码块与纯
    「数量+单位」块按 x 从左到右一一配对（编码必须在数量左边）。因为是在**同一栏
    的同一行**内配对，误把两栏拼一起的风险很低；拼出的条目标 `ambiguous='assembled'`，
    仍要过证据链与模型读数比对，拼不上或对不上的不猜，交给「缺少同行证据」转人工。
    """
    consumed = {entry.block_index for entry in entries}
    code_blocks, qty_blocks = [], []
    for block in blocks:
        if block.index in consumed:
            continue
        text = normalize_text(block.text)
        if CODE_ONLY_RE.fullmatch(text):
            code_blocks.append(block)
        elif QTY_UNIT_ONLY_RE.fullmatch(text):
            qty_blocks.append(block)
    code_blocks.sort(key=lambda b: b.bbox[0])
    qty_blocks.sort(key=lambda b: b.bbox[0])
    extra = []
    for code_block, qty_block in zip(code_blocks, qty_blocks):
        if code_block.center_x >= qty_block.center_x:
            continue                     # 编码必须在数量左边
        match = QTY_UNIT_ONLY_RE.fullmatch(normalize_text(qty_block.text))
        code = CODE_ONLY_RE.fullmatch(normalize_text(code_block.text)).group(0)
        bbox = (code_block.bbox[0],
                min(code_block.bbox[1], qty_block.bbox[1]),
                qty_block.bbox[2],
                max(code_block.bbox[3], qty_block.bbox[3]))
        extra.append(Entry(code=code, quantity=match.group(1), unit=match.group(2),
                           text='%s:%s%s' % (code, match.group(1), match.group(2)),
                           bbox=bbox, block_index=code_block.index, source=code_block.source,
                           confidence=code_block.confidence, ambiguous='assembled'))
    return extra


def assign_columns(blocks, threshold=0.5):
    """分栏：把每个块归到 x 方向最贴近的一栏。

    用「与栏内已有成员的**最大** x 重合度」判定，而不是把栏区间不断求并集 ——
    否则一个横跨两栏的宽块会把两栏粘成一栏。
    """
    columns = []                      # 每栏是块索引列表
    members = []                      # 每栏的成员索引
    for block in sorted(blocks, key=lambda b: (b.bbox[0], b.bbox[1])):
        best, best_score = -1, 0.0
        for index, column_members in enumerate(members):
            score = max((ov_ratio(block.bbox, blocks[m].bbox) for m in column_members), default=0.0)
            if score > best_score:
                best, best_score = index, score
        if best >= 0 and best_score >= threshold:
            columns.append(best)
            members[best].append(block.index)
        else:
            columns.append(len(members))
            members.append([block.index])
    assignment = {}
    for block in blocks:
        for index, column_members in enumerate(members):
            if block.index in column_members:
                assignment[block.index] = index
                break
    return assignment, members


def cluster_rows(blocks, indices=None, tolerance=None, overlap_ratio=0.6):
    """把同一栏内的块按 y 聚成行。

    同一行的判据：中心 y 距离小于容差，**或**竖直方向重合度过半。
    容差默认取该栏**块高中位数的一半**（不是相邻间距中位数的一半）——
    按间距算的话，整页分栏里大段空白会把容差撑大，
    标题会被并进它下面那条明细行，标题从属就全乱了。
    """
    indices = list(indices if indices is not None else [b.index for b in blocks])
    picked = [b for b in blocks if b.index in set(indices)]
    if not picked:
        return []
    picked.sort(key=lambda b: b.center_y)
    if tolerance is None:
        heights = sorted(b.height for b in picked if b.height > 0)
        median_height = heights[len(heights) // 2] if heights else 30.0
        tolerance = max(6.0, median_height * 0.5)
    rows = []
    for block in picked:
        placed = False
        for row in rows:
            if abs(block.center_y - row['center']) <= tolerance:
                row['blocks'].append(block)
                row['center'] = sum(b.center_y for b in row['blocks']) / len(row['blocks'])
                placed = True
                break
            if v_overlap_ratio(block.bbox, row['bbox']) >= overlap_ratio:
                row['blocks'].append(block)
                row['bbox'] = (min(row['bbox'][0], block.bbox[0]), min(row['bbox'][1], block.bbox[1]),
                               max(row['bbox'][2], block.bbox[2]), max(row['bbox'][3], block.bbox[3]))
                placed = True
                break
        if not placed:
            rows.append({'center': block.center_y, 'blocks': [block], 'bbox': block.bbox})
    rows.sort(key=lambda r: r['center'])
    return rows


def iou(a, b):
    """x 方向交并比。标题从属用它：窄标题落在宽行内部时不会被误判成「完全重合」。"""
    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    union = (a[2] - a[0]) + (b[2] - b[0]) - inter
    return inter / union if union > 0 else 0.0


def heading_for(bbox, headings, vertical_tolerance=40.0, min_overlap=0.3, exclude=None):
    """明细行的标题从属。

    规则：
      1. 标题必须在明细行**上方**（允许 40px 的竖直容差，手写标题常与首行重叠）；
      2. 标题与明细行的 x 区间要有足够重合（交并比 ≥ min_overlap）；
      3. 多个标题都合格时，取**离本行最近的上方标题**；同样近则取重合度更高的。

    第 3 条很关键：上一段落的标题 x 区间往往和本行几乎一样宽，
    只看重合度会把很久以前的标题认成当前标题。

    `exclude` 是要跳过的块（例如标题块自己所在的那一行）。
    """
    best, best_score = None, 0.0
    for heading in headings:
        if exclude is not None and heading.index == exclude:
            continue
        if heading.bbox[3] > bbox[1] + vertical_tolerance:
            continue
        score = iou(heading.bbox, bbox)
        if score < min_overlap:
            continue
        if best is None:
            best, best_score = heading, score
        elif heading.bbox[3] > best.bbox[3] or (heading.bbox[3] == best.bbox[3] and score > best_score):
            best, best_score = heading, score
    return best, best_score


@dataclass
class PageLayout:
    """一页的版面结构：栏、行、标题、明细条目。"""
    blocks: list = field(default_factory=list)
    columns: dict = field(default_factory=dict)          # 块索引 → 栏号
    column_members: list = field(default_factory=list)   # 栏号 → 块索引列表
    rows: list = field(default_factory=list)             # 行（每行含块与条目）
    headings: list = field(default_factory=list)         # 标题块

    def entries(self):
        out = []
        for row in self.rows:
            out.extend(row.get('entries', []))
        return out


def build_layout(blocks, column_threshold=0.5, row_tolerance=None,
                 vertical_tolerance=40.0, min_overlap=0.3):
    """把 OCR 块整理成版面结构：分栏 → 行 → 标题从属 → 明细条目。

    标题归属按**条目**算而不是按行算：一个 OCR 块可能横跨两栏
    （例如「1136:1个14:3个」），拆成两个条目后各自带 x 子区间，
    才能分别找到自己那一栏的标题。
    """
    blocks = list(blocks)
    assignment, members = assign_columns(blocks, threshold=column_threshold)
    headings = [b for b in blocks if is_heading(b)]
    rows = []
    for column_index, member_indices in enumerate(members):
        column_rows = cluster_rows(blocks, member_indices, tolerance=row_tolerance)
        for row in column_rows:
            row['column'] = column_index
            entries = []
            for block in row['blocks']:
                entries.extend(parse_entries(block))
            # 同行字段：编码和「数量+单位」被拆成两个块时，拼回一条明细。
            entries.extend(assemble_row_entries(row['blocks'], entries))
            for entry in entries:
                entry.heading, entry.heading_score = heading_for(
                    entry.bbox, headings, vertical_tolerance, min_overlap,
                    exclude=entry.block_index)
            row['entries'] = entries
            row['bbox'] = (min(b.bbox[0] for b in row['blocks']),
                           min(b.bbox[1] for b in row['blocks']),
                           max(b.bbox[2] for b in row['blocks']),
                           max(b.bbox[3] for b in row['blocks']))
            heading, score = heading_for(row['bbox'], headings, vertical_tolerance, min_overlap)
            row['heading'] = heading
            row['heading_score'] = score
            rows.append(row)
    rows.sort(key=lambda r: (r['bbox'][1], r['bbox'][0]))
    return PageLayout(blocks=blocks, columns=assignment, column_members=members,
                       rows=rows, headings=headings)
