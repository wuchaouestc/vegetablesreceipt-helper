"""data 标注的实际应用：只读审计、字段区域草稿、训练清单导出。

任务书第七节：
  · 对 data/annotations、data/learning 做只读审计（路径/哈希/confirmed/复核人/框合法性）；
  · 自动定位只生成草稿，人核对后进入 confirmed；「看不清」可拒识，不编造标签；
  · 导出裁剪图→完整文字标签训练清单，首版仅导出并校验，不训练、不替换权重。

本模块不联网、不改动现有标注文件，只产出草稿与导出清单。
"""

from __future__ import annotations

import json
from pathlib import Path

from .field_regions import plan_field_regions


def audit_annotations(annotations_dir, image_root=None):
    """只读审计标注文件。返回 {files, confirmed, drafts, issues}。

    issues 逐条列出：缺 image_sha256、路径不可解析、非 confirmed、坐标缺失、
    JSON 损坏等。不静默升级为有效样本。
    """
    directory = Path(annotations_dir)
    result = {'files': 0, 'confirmed': 0, 'drafts': 0, 'issues': []}
    if not directory.is_dir():
        result['issues'].append('标注目录不存在：%s' % directory)
        return result
    for path in sorted(directory.glob('*.json')):
        result['files'] += 1
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as exc:
            result['issues'].append('%s：JSON 无法解析（%s）' % (path.name, type(exc).__name__))
            continue
        if not isinstance(data, dict):
            result['issues'].append('%s：不是对象' % path.name)
            continue
        if not str(data.get('image_sha256') or '').strip():
            result['issues'].append('%s：缺 image_sha256（用路径当哈希不可靠）' % path.name)
        image_path = data.get('image_path')
        if image_root and image_path:
            candidate = Path(image_root) / image_path
            if not candidate.is_file():
                result['issues'].append('%s：image_path 在 %s 下解析不到' % (path.name, image_root))
        if data.get('review_status') == 'confirmed':
            result['confirmed'] += 1
        else:
            result['drafts'] += 1
            result['issues'].append('%s：状态是 %r，不是 confirmed（草稿）'
                                    % (path.name, data.get('review_status')))
        if 'fields' in data:
            # 区域标注结构（品类 / 单位区域）：校验区域字段与来源标记
            for field in data.get('fields') or []:
                if not isinstance(field, dict):
                    result['issues'].append('%s：fields 里有非字典条目' % path.name)
                    continue
                if not str(field.get('region_id') or '').strip():
                    result['issues'].append('%s：有区域缺 region_id' % path.name)
                if not str(field.get('transcript') or '').strip():
                    result['issues'].append('%s：区域 %s 的 transcript 为空'
                                            % (path.name, field.get('region_id')))
                bbox = field.get('bbox')
                if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
                    result['issues'].append('%s：区域 %s 的 bbox 非法'
                                            % (path.name, field.get('region_id')))
            continue
        records = data.get('records')
        if not isinstance(records, list):
            result['issues'].append('%s：缺 records 列表' % path.name)
            continue
        for index, record in enumerate(records):
            if not isinstance(record, dict) or not str(record.get('category') or '').strip():
                result['issues'].append('%s：第 %d 行品类缺失' % (path.name, index + 1))
        if not data.get('regions'):
            result['issues'].append('%s：regions 为空（只有整行五字段，无区域标注）' % path.name)
    return result


def build_region_annotation_draft(layout, max_regions=32):
    """从生产布局生成字段区域标注草稿（待人工确认）。

    品类 transcript=标题文字；单位 transcript=整行「编码:数量单位」原文，
    normalized_value 分别为品类名与末尾单位。visibility 默认 visible。
    """
    draft = []
    for region in plan_field_regions(layout, max_regions=max_regions):
        if region.field == 'category':
            text = ''
            for heading in layout.headings:
                if heading.bbox and tuple(round(v, 1) for v in heading.bbox) == tuple(round(v, 1) for v in region.source_bbox):
                    text = heading.text
                    break
            draft.append({'region_id': region.region_id, 'field': 'category',
                          'bbox': list(region.source_bbox), 'transcript': text,
                          'normalized_value': text, 'visibility': 'visible',
                          'source': 'auto_draft'})
        else:
            # 单位：整行原文做 transcript，末尾单位做 normalized_value
            text = ''
            for row in layout.rows:
                for entry in row.get('entries', []):
                    if entry.bbox and tuple(round(v, 1) for v in entry.bbox) == tuple(round(v, 1) for v in region.source_bbox):
                        text = entry.text
                        break
            draft.append({'region_id': region.region_id, 'field': 'unit',
                          'bbox': list(region.source_bbox), 'transcript': text,
                          'normalized_value': region.expected_unit, 'visibility': 'visible',
                          'source': 'auto_draft'})
    return draft


def export_training_manifest(draft_entries, image_path, output_dir, image_sha256='',
                             append=False):
    """导出裁剪图→完整文字标签训练清单（仅导出并校验，不训练）。

    每个草稿条目裁剪原图 bbox 存 PNG，写入 manifest.jsonl（每行一条）。
    region_id 只在单张图内唯一，多张图混在一起会互相覆盖，
    因此给了 image_sha256 时裁剪图落在 crops/<sha12>/ 下。
    append=True 时往已有清单追加（多张图共用一个清单时用）。
    返回 {'crops': n, 'manifest': 路径, 'skipped': [...]}。
    """
    try:
        from PIL import Image
    except ImportError:
        return {'crops': 0, 'manifest': None, 'skipped': ['Pillow 不可用']}
    output = Path(output_dir)
    sub = 'crops/%s' % image_sha256[:12] if image_sha256 else 'crops'
    crops_dir = output / sub
    crops_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output / 'manifest.jsonl'
    written, skipped = 0, []
    try:
        image = Image.open(image_path)
        width, height = image.size
    except Exception as exc:  # noqa: BLE001
        return {'crops': 0, 'manifest': None, 'skipped': ['图片打不开：%r' % (exc,)]}
    with manifest_path.open('a' if append else 'w', encoding='utf-8') as handle:
        for entry in draft_entries:
            if not str(entry.get('transcript') or '').strip():
                skipped.append('%s：transcript 为空，跳过' % entry.get('region_id'))
                continue
            bbox = entry.get('bbox')
            try:
                x0, y0, x1, y1 = (int(round(float(v))) for v in bbox)
            except (TypeError, ValueError):
                skipped.append('%s：bbox 非法' % entry.get('region_id'))
                continue
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(width, x1), min(height, y1)
            if x1 <= x0 or y1 <= y0:
                skipped.append('%s：裁剪框越界/空' % entry.get('region_id'))
                continue
            crop_path = crops_dir / ('%s.png' % entry['region_id'])
            image.crop((x0, y0, x1, y1)).convert('RGB').save(crop_path)
            handle.write(json.dumps({
                'image': str(crop_path), 'transcript': entry.get('transcript'),
                'field': entry.get('field'), 'normalized_value': entry.get('normalized_value'),
                'bbox': [x0, y0, x1, y1], 'source': entry.get('source'),
                'confirm_sources': entry.get('confirm_sources') or [],
                'image_sha256': image_sha256,
                'region_id': entry.get('region_id'),
            }, ensure_ascii=False) + '\n')
            written += 1
    return {'crops': written, 'manifest': str(manifest_path), 'skipped': skipped}
