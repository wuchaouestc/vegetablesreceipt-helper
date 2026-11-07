"""字段识别编排：区域规划 → 独立 rec 批量识别 → 候选挂入证据链（观察模式）。

任务书第六节：首版「观察模式」——产生可见候选、原图区域和冲突原因，不自动替换当前结果。
  · 品类与现有结果一致只增加证据；冲突时显示原值、Paddle 原文与标题区域，不跨栏改类；
  · 单位冲突（斤/个、斤/公斤）显示明确核对原因，不换算、不因常见选斤、不用危险别名裁决；
  · 未知值、空值、框错误、低分、服务不可用都要能区分，且都能降级。

本模块不 import paddle；重型依赖在独立 sidecar 进程里，这里只发 HTTP。
"""

from __future__ import annotations

import json
import re
import urllib.request

from .field_regions import plan_field_regions

# 单位表（与 geometry.UNIT_PATTERN 对齐），用于从整行 rec 文本解析末尾单位。
_UNIT_RE = re.compile(r'(公斤|千克|kg|斤|个|只|袋|包|件|箱|条|把|盒|支|瓶|片|棵|枚|扎|提|罐|卷|对|双|打)\s*$')


def parse_unit_from_line(raw_text):
    """从整行 rec 文本里解析末尾单位。解析不到返回 None（不猜、不填斤）。"""
    text = (raw_text or '').strip()
    if not text:
        return None
    match = _UNIT_RE.search(text)
    return match.group(1) if match else None


def recognize_fields_client(image_path, regions, *, sidecar_url, image_sha256='',
                            deadline=10.0, timeout=60):
    """调用 sidecar `/recognize_fields`，返回 (results, notes)。

    失败、超时、服务不可用一律返回空 results + 原因，绝不抛到上层。
    """
    payload = {
        'schema_version': 1,
        'task_id': '',
        'image_sha256': image_sha256 or '',
        'image_path': str(image_path),
        'regions': [r.as_dict() for r in regions],
        'model_version': 'PP-OCRv5_mobile_rec',
        'deadline': float(deadline),
    }
    try:
        request = urllib.request.Request(
            sidecar_url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode('utf-8'))
    except Exception as exc:  # noqa: BLE001
        return [], ['字段识别服务请求失败：%r' % (exc,)]
    if not isinstance(data, dict) or not data.get('ok'):
        return [], ['字段识别服务不可用：%s' % (data.get('error') if isinstance(data, dict) else 'unknown')]
    return data.get('results') or [], []


def parse_field_results(regions, raw_results):
    """把 sidecar 的原始结果解析成字段候选。

    品类取 raw_text；单位从整行 raw_text 解析末尾单位。
    返回 [{field, value, source_bbox, match_kind, row_ids, raw_text, rec_score, status}]。
    """
    by_id = {r.get('region_id'): r for r in raw_results or []}
    parsed = []
    for region in regions:
        raw = by_id.get(region.region_id)
        if not raw or raw.get('status') != 'ok':
            continue
        raw_text = str(raw.get('raw_text') or '').strip()
        if region.field == 'category':
            value = raw_text
        else:
            value = parse_unit_from_line(raw_text)
            if value is None:
                # 整行没解析出单位，保留原文供人工核对，不强制套词表。
                continue
        if not value:
            continue
        parsed.append({
            'field': region.field,
            'value': value,
            'source_bbox': region.source_bbox,
            'match_kind': region.match_kind,
            'row_ids': list(region.row_ids),
            'raw_text': raw_text,
            'rec_score': raw.get('rec_score'),
        })
    return parsed


def _bbox_key(bbox):
    return tuple(round(float(v), 1) for v in bbox) if bbox else None


def attach_field_candidates(page, field_values):
    """把字段候选挂进证据链（只加候选 + 冲突观察，不改值）。"""
    from .evidence import Candidate

    heading_rows = {}
    entry_rows = {}
    for row in page.rows:
        if row.heading is not None and row.heading.bbox:
            heading_rows.setdefault(_bbox_key(row.heading.bbox), []).append(row)
        if row.entry is not None and row.entry.bbox:
            entry_rows[_bbox_key(row.entry.bbox)] = row

    for fv in field_values:
        field_name = fv['field']
        key = _bbox_key(fv['source_bbox'])
        if field_name == 'category':
            rows = heading_rows.get(key, [])
        else:
            row = entry_rows.get(key)
            rows = [row] if row is not None else []
        for row in rows:
            row.add(Candidate(field=field_name, value=fv['value'], source='paddle_mobile_rec',
                              evidence=fv.get('raw_text', ''),
                              detail={'rec_score': fv.get('rec_score')}))
            _观察冲突(row, field_name, fv['value'])
    return page


def _观察冲突(row, field_name, paddle_value):
    """观察模式：Paddle 候选与现有结果不一致时，只记观察原因，不覆盖值。"""
    from .evidence import normalize_text
    candidates = row.candidates.get(field_name, [])
    model_value = next((c.value for c in candidates if c.source == 'model'), None)
    ocr_value = next((c.value for c in candidates if c.source in ('ocr_row', 'ocr_heading')), None)
    p = normalize_text(paddle_value)
    if field_name == 'category':
        if ocr_value is not None and p != normalize_text(ocr_value) and model_value is not None and p != normalize_text(model_value):
            row.observations.append('Paddle 读品类为「%s」，与现有结果不一致，请对照标题区域' % paddle_value)
    else:
        if ocr_value is not None and p != normalize_text(ocr_value) and model_value is not None and p != normalize_text(model_value):
            row.observations.append('Paddle 读单位为「%s」，与现有结果不一致（斤/个/公斤易混），请核对' % paddle_value)


def recognize_fields(image_path, layout, *, sidecar_url, image_sha256='',
                     deadline=10.0, timeout=60, max_regions=32):
    """字段识别总入口：规划区域 → 批量识别 → 解析候选。

    返回 {'values': [...], 'regions': [...], 'notes': [...]}。
    任何失败都返回空 values + 原因，供上层降级。
    """
    regions = plan_field_regions(layout, max_regions=max_regions)
    if not regions:
        return {'values': [], 'regions': [], 'notes': []}
    results, notes = recognize_fields_client(
        image_path, regions, sidecar_url=sidecar_url, image_sha256=image_sha256,
        deadline=deadline, timeout=timeout)
    values = parse_field_results(regions, results)
    return {'values': values, 'regions': [r.as_dict() for r in regions], 'notes': notes}
