"""原图对照：点开一条待核对明细时能看到的东西。

《方案》第九节：「点击核对项查看原图区域、原文、候选和建议；
可切换增强图，但原图始终可见。」

本模块只组装对照信息（区域、原文、候选、建议），不碰界面控件，
方便界面补丁和离线报告共用同一套数据。
"""
from __future__ import annotations

from pathlib import Path

from .geometry import normalize_text
from .review import crop_region

FIELD_LABELS = {'category': '品类', 'customer_code': '编码', 'weight': '数量', 'unit': '单位'}


def _candidate_view(candidate):
    if isinstance(candidate, dict):
        field = candidate.get('field')
        value = candidate.get('value')
        source = candidate.get('source')
        confidence = candidate.get('confidence')
        evidence = candidate.get('evidence', '')
    else:
        field = candidate.field
        value = candidate.value
        source = candidate.source
        confidence = candidate.confidence
        evidence = candidate.evidence
    return {'field': field, 'label': FIELD_LABELS.get(field, field),
            'value': value, 'source': source, 'confidence': confidence, 'evidence': evidence}


def build_comparison(record, *, image_path=None, crop_dir=None, name='row'):
    """组装一条明细的对照信息。

    返回 dict：原图路径、区域框、裁剪图路径、原文、逐字段候选、建议、核对原因。
    """
    evidence = record.get('_evidence') or {}
    bbox = evidence.get('bbox')
    heading_bbox = evidence.get('heading_bbox')
    reasons = list(record.get('_review_reasons') or [])
    candidates = evidence.get('candidates') or {}
    view = []
    for field, items in sorted(candidates.items()):
        for candidate in items:
            view.append(_candidate_view(candidate))
    suggestion = _suggestion(record, candidates)
    crop_path = None
    if image_path and bbox and crop_dir:
        crop_path = crop_region(image_path, bbox, crop_dir, name=name)
    return {
        'row_id': evidence.get('row_id'),
        'image_path': str(image_path) if image_path else None,
        'bbox': list(bbox) if bbox else None,
        'heading_bbox': list(heading_bbox) if heading_bbox else None,
        'crop_path': crop_path,
        'heading_text': evidence.get('heading_text'),
        'entry_text': evidence.get('entry_text'),
        'values': {field: record.get(field) for field in FIELD_LABELS},
        'candidates': view,
        'suggestion': suggestion,
        'reasons': reasons,
        'observations': list(record.get('_observations') or []),
        'decisions': list(evidence.get('decisions') or []),
    }


def _suggestion(record, candidates):
    """给出「建议怎么改」的一句话建议；没有确证就明说不能确定。"""
    decisions = (record.get('_evidence') or {}).get('decisions') or []
    for decision in decisions:
        if decision.get('action') == 'replace':
            return '建议改成「%s」（%s）' % (decision.get('value'), decision.get('reason'))
    if candidates:
        parts = []
        for field, items in sorted(candidates.items()):
            values = []
            for candidate in items:
                if candidate.get('value') not in values:
                    values.append(candidate.get('value'))
            if len(values) > 1:
                parts.append('%s 有 %s 几种读法' % (FIELD_LABELS.get(field, field), '、'.join(map(str, values))))
        if parts:
            return '不能确定：' + '；'.join(parts) + '，请对照原图人工判断'
    return '两路一致，没有需要改动的地方'


def comparison_rows(records, *, image_path=None, crop_dir=None):
    """批量生成对照信息（只含有证据的记录）。"""
    out = []
    for index, record in enumerate(records or []):
        if not record.get('_evidence'):
            continue
        out.append(build_comparison(record, image_path=image_path, crop_dir=crop_dir,
                                    name='row-%04d' % (index + 1)))
    return out


def write_comparison(path, records, *, image_path=None, crop_dir=None):
    """把对照信息存成本地 JSON，供人工复核页面使用。"""
    import json
    from .storage import write_json
    payload = {'schema_version': 1, 'image_path': str(image_path) if image_path else None,
               'rows': comparison_rows(records, image_path=image_path, crop_dir=crop_dir)}
    write_json(Path(path), payload)
    return payload
