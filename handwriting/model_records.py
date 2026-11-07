"""模型明细归一化：把模型可能返回的结构统一成逐行扁平明细。

背景：模型读手写单据时，经常把一张纸理解成「若干个标题，每个标题下挂若干条明细」，
返回的是分组结构，例如：

    {"records": [{"name": "沙姜", "entries": ["1366: 1个", "935: 1.5个"]}]}

而证据链（`evidence.build_evidence`）按**逐行扁平结构**消费明细。
不归一化的话，每一行都会落成「缺少同行证据：原图这一行没找到可对应的文字」，
整条裁决链直接失效——这是实跑手写单据暴露出来的真实问题。

归一化只做**结构转换**，不改数值：
  · 模型读到的标题原样保留成该组明细的品类候选（模型的标题读数是独立第三方意见，
    对品类这个最大错误源很有价值）；
  · 明细文字里能明确读出的编码/数量/单位照抄，读不出的**留空**并标疑点，绝不猜；
  · 数量形如「1-2」「1~2」的区间写法保留原文并标记，不擅自取其一。
"""
from __future__ import annotations

import re

# 分组结构里可能出现的标题键与明细列表键。
_HEADING_KEYS = ('name', 'category', '品类', 'title', '标题', 'heading', '品名')
_ITEM_KEYS = ('entries', 'items', '明细', 'rows', 'lines', 'details', 'list')

# 明细文字：编码 + 分隔符 + 其余。「1368: 10个」「935: 1.5个(大)」
_ENTRY_RE = re.compile(r'^\s*([0-9A-Za-z][0-9A-Za-z\-_]{0,11})\s*[:：、,，]\s*(.*)$')
# 数量：整数或小数，区间写法单列
_QTY_RE = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*(.*)$')
_RANGE_RE = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*[-~－—至]\s*(\d+(?:\.\d+)?)\s*(.*)$')
# 括号里的补充说明，如「大」「小」
_REMARK_RE = re.compile(r'[（(]([^）)]*)[）)]')


class RecordNormalizer:
    """把模型的原始明细转成扁平列表，并保留来源信息。

    用法::

        flat, notes = RecordNormalizer().normalize(raw_records)
    """

    def __init__(self):
        self.notes = []
        self.group_count = 0
        self.flattened_count = 0
        self.unparsed = []

    # ------------------------------------------------------------------ 入口
    def normalize(self, raw_records):
        """返回 (扁平明细列表, 提示列表)。原始结构不被修改。"""
        self.notes = []
        self.group_count = 0
        self.flattened_count = 0
        self.unparsed = []
        flat = []
        for index, record in enumerate(raw_records or []):
            if not isinstance(record, dict):
                continue
            items, heading = self._split_group(record)
            if items is None:
                flat.append(self._flat_one(record, index, heading=None))
                continue
            self.group_count += 1
            for item in items:
                flat.append(self._flat_one(item, len(flat), heading=heading))
        if self.group_count:
            self.notes.append('模型按标题分组返回了 %d 组明细，已展开成 %d 行逐行明细'
                              % (self.group_count, len(flat)))
        if self.unparsed:
            self.notes.append('有 %d 条明细的文字读不出编码，已留空待人工核对：%s'
                              % (len(self.unparsed), '；'.join(self.unparsed[:6])))
        return flat, list(self.notes)

    # -------------------------------------------------------------- 结构拆分
    def _split_group(self, record):
        """判断一条记录是不是「标题 + 明细列表」。是则返回 (列表, 标题)。"""
        heading = None
        for key in _HEADING_KEYS:
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                heading = value.strip()
                break
        for key in _ITEM_KEYS:
            value = record.get(key)
            if isinstance(value, list) and value:
                return value, heading
        return None, heading

    # ------------------------------------------------------------ 单条明细
    def _flat_one(self, item, index, heading=None):
        """把一条明细转成扁平记录。字段读不出就留空。"""
        record = {}
        if isinstance(item, str):
            record = self._parse_entry_text(item)
            if not record.get('customer_code'):
                self.unparsed.append(item[:60])
        elif isinstance(item, dict):
            # 基于原记录拷贝，原记录上的其它键（如 _id）不能丢。
            record = dict(item)
            for key in ('category', '品类'):
                if isinstance(item.get(key), str) and item[key].strip():
                    record['category'] = item[key].strip()
                    break
            if item.get('row_id'):
                record['row_id'] = str(item['row_id'])
            for key in ('customer_code', 'code', '编码'):
                if item.get(key) not in (None, ''):
                    record['customer_code'] = str(item[key]).strip()
                    break
            for key in ('weight', 'quantity', '数量'):
                if item.get(key) not in (None, ''):
                    record['weight'] = str(item[key]).strip()
                    break
            for key in ('unit', '单位'):
                if item.get(key) not in (None, ''):
                    record['unit'] = str(item[key]).strip()
                    break
            for key in ('remark', '备注'):
                if item.get(key) not in (None, ''):
                    record['remark'] = str(item[key]).strip()
                    break
            # 字典形态但没有编码字段：试试「编码: 数量」写在备注里的情况
            if not record.get('customer_code'):
                for key in ('text', 'content', '原文', '明细'):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        parsed = self._parse_entry_text(value)
                        if parsed.get('customer_code'):
                            for field, text in parsed.items():
                                record.setdefault(field, text)
                        break
        if heading and not record.get('category'):
            record['category'] = heading
        # 五个字段始终存在（读不出就是空串），下游不必到处判键是否存在。
        for field in ('category', 'customer_code', 'weight', 'unit', 'remark'):
            record.setdefault(field, '')
        # 不往明细里塞内部键：位置由下标得到，标题读数已由 category 承载。
        # 这样「原样返回模型明细」的约定不被破坏。
        return record

    def _parse_entry_text(self, text):
        """解析「1368: 10个(大)」这类明细文字。读不出的部分留空。"""
        result = {}
        remark_match = _REMARK_RE.search(text)
        if remark_match:
            result['remark'] = remark_match.group(1).strip()
        body = _REMARK_RE.sub('', text).strip()
        match = _ENTRY_RE.match(body)
        if not match:
            return result
        result['customer_code'] = match.group(1).strip()
        rest = match.group(2).strip()
        range_match = _RANGE_RE.match(rest)
        if range_match:
            # 区间写法不擅自取其一，原样保留并标注
            result['weight'] = '%s-%s' % (range_match.group(1), range_match.group(2))
            result['unit'] = range_match.group(3).strip()
            result['_ambiguous'] = 'range'
            return result
        qty_match = _QTY_RE.match(rest)
        if qty_match:
            result['weight'] = qty_match.group(1)
            result['unit'] = qty_match.group(2).strip()
            return result
        result['_ambiguous'] = 'unparsed'
        return result


def normalize_records(raw_records):
    """便捷入口：返回 (扁平明细, 提示)。"""
    return RecordNormalizer().normalize(raw_records)


def strip_internal(record):
    """去掉归一化过程留下的内部字段，得到可以交付给界面的明细。"""
    return {key: value for key, value in record.items() if not key.startswith('_')}
