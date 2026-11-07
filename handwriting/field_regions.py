"""字段区域规划：从生产布局生成品类/单位识别区域。

任务书第四节的要求：
  · 品类：优先采用布局标题框，留适量边距且避免相邻栏；一个标题只识别一次，
    结果通过标题从属关系关联多条明细，不能为每行重复请求。
  · 单位：优先采用独立单位文字框；若「编码:数量单位」连在一个 OCR 框内，
    识别整段单行区域，再解析末尾单位。不按字符数猜像素位置强切最后一个字。

本模块只做几何与关联，不读图、不联网、不依赖 Tk，可离线单测。
坐标用「原图方向归一后」像素（与 `preprocess.original_image` 喂给 OCR 的图一致）。
bbox 约定：左上包含、右下排除。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FieldRegion:
    """一个待识别的字段区域。"""
    region_id: str
    field: str                          # category / unit
    bbox: tuple                          # 交给识别器的裁剪框（已加边距）
    source_bbox: tuple                   # 来源框（标题框 / 条目框），用于回配行
    match_kind: str                      # heading / entry
    row_ids: list = field(default_factory=list)
    heading_bbox: tuple | None = None
    code: str = ''                       # 单位区域所属的编码（仅作单位解析上下文）
    expected_unit: str = ''              # 基础 OCR 已读到的单位（仅作对照，不用于裁决）

    def as_dict(self):
        return {'region_id': self.region_id, 'field': self.field, 'bbox': list(self.bbox),
                'source_bbox': list(self.source_bbox), 'match_kind': self.match_kind,
                'row_ids': list(self.row_ids),
                'heading_bbox': list(self.heading_bbox) if self.heading_bbox else None,
                'code': self.code, 'expected_unit': self.expected_unit}


def _pad(bbox, margin):
    """四边各留 margin，不夹坏（负值会被 sidecar 夹回图内）。"""
    x0, y0, x1, y1 = (float(v) for v in bbox)
    return (x0 - margin, y0 - margin, x1 + margin, y1 + margin)


def plan_field_regions(layout, *, category_margin=4, unit_margin=2,
                       max_regions=32):
    """从 PageLayout 生成品类/单位识别区域。

    品类：每个标题框一个区域（去重），边距略大；
    单位：每个条目一行区域（整段「编码:数量单位」），识别后解析末尾单位。
    超过 max_regions 时按先品类后单位的顺序截断，剩下的留给人工。
    """
    regions = []
    seen_heading = set()

    # ① 品类标题区域（去重，一标题一次）
    for heading in layout.headings:
        if not heading.bbox:
            continue
        key = tuple(round(v, 1) for v in heading.bbox)
        if key in seen_heading:
            continue
        seen_heading.add(key)
        regions.append(FieldRegion(
            region_id='cat-%d' % len(regions),
            field='category',
            bbox=_pad(heading.bbox, category_margin),
            source_bbox=tuple(heading.bbox),
            match_kind='heading',
            heading_bbox=tuple(heading.bbox),
        ))

    # ② 单位区域：每个条目一行（整段识别后解析末尾单位）
    for row in layout.rows:
        for entry in row.get('entries', []):
            if not entry.bbox:
                continue
            regions.append(FieldRegion(
                region_id='unit-%d' % len(regions),
                field='unit',
                bbox=_pad(entry.bbox, unit_margin),
                source_bbox=tuple(entry.bbox),
                match_kind='entry',
                row_ids=list(row.get('row_ids', [])),
                code=entry.code or '',
                expected_unit=entry.unit or '',
            ))

    return regions[:max_regions]
