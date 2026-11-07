"""局部复核：疑难区域裁剪 → 追加识别 → 保守合并。

《方案》第八、九节的要求：
  · 裁剪原图时保留上下相邻行、所属品类标题、编码、数量和单位；
  · 局部复核**不得覆盖**已确认或手动修改的结果；
  · 晚到响应须校验任务 ID、图片版本和行版本；
  · 无法可靠定位时转人工，不猜区域反复识别。

本模块不联网：追加识别由调用方注入 provider 函数，模块只管区域规划、
裁剪、证据合并与迟到校验。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .geometry import normalize_text
from .storage import fingerprint


@dataclass(frozen=True)
class ReviewToken:
    """一次复核请求的身份凭据。晚到响应必须三项全对上才准用。"""
    task_id: str
    image_version: str
    row_version: str


@dataclass
class ReviewRegion:
    """一个待复核区域。"""
    bbox: tuple
    row_ids: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    heading_bbox: tuple | None = None
    crop_path: str | None = None
    token: ReviewToken | None = None

    def as_dict(self):
        return {'bbox': list(self.bbox), 'row_ids': list(self.row_ids), 'reasons': list(self.reasons),
                'heading_bbox': list(self.heading_bbox) if self.heading_bbox else None,
                'crop_path': self.crop_path}


def row_version(row):
    """行版本号：候选集合的指纹。行内容一变，晚到的复核响应就作废。"""
    payload = {name: sorted({candidate.value for candidate in candidates})
               for name, candidates in sorted(row.candidates.items())}
    return fingerprint(payload)[:16]


def validate_late(token, current_row_version, task_id=None, image_version=None):
    """校验迟到的复核响应：任务 ID、图片版本、行版本都要对上。"""
    if token is None:
        return False, '缺少复核凭据'
    if task_id is not None and token.task_id != task_id:
        return False, '任务 ID 不一致'
    if image_version is not None and token.image_version != image_version:
        return False, '图片版本不一致'
    if not current_row_version:
        return False, '缺少当前行版本'
    if token.row_version != current_row_version:
        return False, '行版本已变（复核返回晚于本次修改）'
    return True, ''


def plan_regions(page, layout, max_regions=2, merge_gap=140.0, padding=12):
    """把疑点行规划成复核区域：相邻疑点合并，区域带上相邻行与所属标题。"""
    rows_by_id = {row.row_id: row for row in page.rows}
    suspicious = [row for row in page.rows if row.reasons]
    if not suspicious:
        return []
    groups = []
    for row in sorted(suspicious, key=lambda r: (r.column if r.column is not None else 0,
                                                 r.bbox[1] if r.bbox else 0)):
        if groups:
            last_row = rows_by_id[groups[-1]['row_ids'][-1]]
            same_column = last_row.column == row.column
            gap = (row.bbox[1] - last_row.bbox[3]) if (row.bbox and last_row.bbox) else 1e9
            if same_column and gap <= merge_gap:
                groups[-1]['row_ids'].append(row.row_id)
                groups[-1]['reasons'].extend(row.reasons)
                continue
        groups.append({'row_ids': [row.row_id], 'reasons': list(row.reasons)})
    groups.sort(key=lambda g: (-len(g['row_ids']), g['row_ids'][0]))
    regions = []
    for group in groups[:max(0, max_regions)]:
        members = [rows_by_id[rid] for rid in group['row_ids'] if rid in rows_by_id]
        boxes = [m.bbox for m in members if m.bbox]
        if not boxes:
            continue
        x0 = min(b[0] for b in boxes)
        y0 = min(b[1] for b in boxes)
        x1 = max(b[2] for b in boxes)
        y1 = max(b[3] for b in boxes)
        heading_bbox = None
        for member in members:
            if member.heading is not None:
                heading_bbox = member.heading.bbox
                x0 = min(x0, member.heading.bbox[0])
                x1 = max(x1, member.heading.bbox[2])
                y0 = min(y0, member.heading.bbox[1])
                break
        # 保留上下相邻行：同一栏里紧邻的行也要进画面，方便人工判断。
        for member in members:
            if not member.bbox or member.column is None:
                continue
            for row in layout.rows:
                if row.get('column') != member.column or not row.get('entries'):
                    continue
                if row['bbox'][3] < member.bbox[1] - merge_gap:
                    continue
                if row['bbox'][1] > member.bbox[3] + merge_gap:
                    continue
                x0 = min(x0, row['bbox'][0])
                x1 = max(x1, row['bbox'][2])
                y0 = min(y0, row['bbox'][1])
                y1 = max(y1, row['bbox'][3])
        regions.append(ReviewRegion(bbox=(x0 - padding, y0 - padding, x1 + padding, y1 + padding),
                                    row_ids=list(group['row_ids']),
                                    reasons=sorted(set(group['reasons'])),
                                    heading_bbox=heading_bbox))
    return regions


def crop_region(image_path, bbox, output_dir, name='review', padding=0):
    """裁剪原图区域，保留原分辨率；缺 Pillow 时返回 None（跳过复核）。"""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(image_path) as image:
            width, height = image.size
            x0 = max(0, int(bbox[0]) - padding)
            y0 = max(0, int(bbox[1]) - padding)
            x1 = min(width, int(bbox[2]) + padding)
            y1 = min(height, int(bbox[3]) + padding)
            if x1 <= x0 or y1 <= y0:
                return None
            directory = Path(output_dir)
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / ('%s.png' % name)
            image.convert('RGB').crop((x0, y0, x1, y1)).save(target)
            return str(target)
    except Exception:
        return None


def write_region_manifest(path, regions, image_path, task_id):
    """复核区域清单：原图坐标 ↔ 裁剪图，供「原图对照」界面使用。"""
    payload = {'schema_version': 1, 'task_id': task_id, 'image_path': str(image_path),
               'regions': [r.as_dict() for r in regions]}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return str(target)


def apply_review_results(page, results, records=None):
    """把复核结果并回证据链。

    保守规则：
      · 复核值与 OCR 行内文字一致 → 采信该值（原先「保留」才升级为「采信」）；
      · 复核值与模型读数一致 → 保留模型值，但仍保留疑点；
      · 复核值与两路都不一致 → 仍然保留疑点，转人工；
      · 已确认或手动修改的结果不被覆盖。
    """
    from .evidence import Candidate
    notes = []
    rows_by_id = {row.row_id: row for row in page.rows}
    for result in results or []:
        region = result.get('region')
        tokens = result.get('tokens') or {}
        values = result.get('values') or {}
        for row_id, row_values in values.items():
            row = rows_by_id.get(row_id)
            if row is None:
                continue
            ok, why = validate_late(tokens.get(row_id), row_version(row))
            if not ok:
                notes.append('复核响应已丢弃（%s）：%s' % (row_id, why))
                continue
            record = {}
            if records and 0 <= row.record_index < len(records):
                record = records[row.record_index] or {}
            if record.get('_user_confirmed') or record.get('_user_edited'):
                notes.append('复核响应已丢弃（%s）：该行已确认或手动修改' % row_id)
                continue
            for field_name, value in row_values.items():
                row.add(Candidate(field=field_name, value=str(value), source='review',
                                  evidence=(result.get('raw_text') or '')[:200],
                                  bbox=region.bbox if region else None))
            _re_decide(row, list(row_values))
    if notes:
        page.notes.extend(notes)
    return page


def _re_decide(row, field_names):
    """复核之后重新裁决受影响的字段。"""
    from .evidence import FIELD_LABELS
    for field_name in field_names:
        for conflict in [c for c in row.conflicts if c['field'] == field_name]:
            if conflict['reason'] in row.reasons:
                row.reasons.remove(conflict['reason'])
        row.conflicts = [c for c in row.conflicts if c['field'] != field_name]
        row.decisions = [d for d in row.decisions if d['field'] != field_name]
        candidates = row.candidates.get(field_name, [])
        model_value = next((c.value for c in candidates if c.source == 'model'), None)
        ocr_value = next((c.value for c in candidates if c.source in ('ocr_row', 'ocr_heading')), None)
        review_value = next((c.value for c in candidates if c.source == 'review'), None)
        if review_value is None:
            continue
        label = FIELD_LABELS.get(field_name, field_name)
        if ocr_value is not None and normalize_text(review_value) == normalize_text(ocr_value):
            action, value, reason = 'replace', ocr_value, '局部复核与原图行内文字一致，%s已采信' % label
        elif model_value is not None and normalize_text(review_value) == normalize_text(model_value):
            action, value, reason = 'keep', model_value, '局部复核与模型读数一致，%s仍请核对' % label
        else:
            action, value, reason = 'keep', model_value, '局部复核与两路都不一致，%s仍需人工核对' % label
        row.decisions.append({'field': field_name, 'action': action, 'value': value, 'reason': reason})
        row.conflicts.append({'field': field_name, 'reason': reason})
        row.reasons.append(reason)
