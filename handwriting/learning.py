"""人工核对结果的落盘与学习：让软件越用越智能。

一次人工核对产生三份东西：

  ① **标注文件** `data/annotations/<image_sha256>.json`
     与 `dataset.prepare` 同一套 schema：`review_status='confirmed'`、`complete=true`，
     可直接被 `dataset.annotation_audit` 和冻结评测消费，是后续训练/评测的真值来源。

  ② **词典别名** `data/dictionaries/handwriting.json`
     把「AI 读错的写法」记成「人工确认的规范值」的别名。下次再读到同样的错写法，
     词典就能提示正确的值——这是「越用越智能」最直接的一环。

  ③ **已确认示例** `data/learning/confirmed_examples.json`
     人工确认过的明细原样存下来，带原图坐标和纠错前后对照，
     供后续识别作对照候选与回归基线。

铁律（与阶段 3 的保守裁决一致，**不可违反**）：

  · **只从人工确认的数据学习**，草稿和未完成的一律不学；
  · 学到的东西**只作提示**，不静默改值——由 `vocabulary.preview_candidates` 保证，
    它只返回候选，`apply_evidence` 也不会因为词典候选而改写字段；
  · **数量不进词典**：数量是数不是词，把「10斤」记成别名会污染编码/单位语境；
  · 每条别名都能追溯到来源图片、行号与复核人；
  · 写盘前先校验，失败不动原文件。
"""
from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from .storage import read_json, write_json, digest
from .vocabulary import FIELDS as VOCABULARY_FIELDS
from .vocabulary import Entry, Vocabulary, load_vocabulary, save_vocabulary

# 复核页要展示和允许修改的字段。
REVIEW_FIELDS = ('category', 'customer_code', 'weight', 'unit', 'remark')
# 关键字段：不能为空才算完成核对。
REQUIRED_FIELDS = ('category', 'customer_code', 'weight', 'unit')
# 可以进词典的字段（与 vocabulary.FIELDS 对齐；数量不在其中）。
LEARNABLE_FIELDS = tuple(f for f in VOCABULARY_FIELDS)
# 一次纠错就能直接成为「启用别名」的字段：品类（形近字）与备注（开放文本），风险可控。
_DIRECT_ALIAS_FIELDS = ('category', 'remark')
# 不能凭一次纠错变成全局替换的字段：单位（跨语义，个≠斤）与编码（错读）。
# 这类纠错进入待审核区（pending），需二次人工确认才可启用。
_PENDING_ALIAS_FIELDS = ('customer_code', 'unit')

FIELD_LABELS = {'category': '品类', 'customer_code': '编码', 'weight': '数量',
                'unit': '单位', 'remark': '备注'}
SOURCE_LABELS = {'model': '模型', 'ocr_row': '原图行内文字', 'ocr_heading': '原图标题',
                 'vocabulary': '词典', 'review': '局部复核'}


class LearningError(ValueError):
    """核对结果不合法；不允许落盘。"""


def _now():
    return datetime.now().isoformat(timespec='seconds')


def _revision(prefix='r'):
    return '%s%s' % (prefix, datetime.now().strftime('%Y%m%d-%H%M%S'))


def _entry_id(field, canonical):
    raw = '%s|%s' % (field, canonical)
    return '%s-%s' % (field[:3], hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12])


def _pending_path(vocabulary_path):
    """待审核别名文件的路径：跟词典并排，名字带 .pending.json。"""
    return Path(vocabulary_path).with_suffix('.pending.json')


def load_pending(path):
    """读待审核别名列表；文件缺失或坏了当空，不影响核对。"""
    try:
        data = read_json(path)
    except (OSError, ValueError, UnicodeError):
        return []
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        return []
    return [item for item in data.get('pending') or [] if isinstance(item, dict)]


def _write_pending(path, items):
    """把待审核别名合并写入（按 字段/from/to 去重），原子替换。"""
    merged = {}
    for item in load_pending(path):
        merged[(item.get('field'), item.get('from'), item.get('to'))] = item
    for item in items:
        merged[(item.get('field'), item.get('from'), item.get('to'))] = item
    write_json(path, {'schema_version': 1, 'pending': list(merged.values())})


def load_examples(path):
    """读已确认示例库；文件缺失或坏了就当空库，不影响核对。"""
    try:
        data = read_json(path)
    except (OSError, ValueError, UnicodeError):
        return []
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        return []
    return [item for item in data.get('examples') or [] if isinstance(item, dict)]


def find_similar_examples(values, examples, limit=3):
    """按编码或品类找以前人工确认过的相似行。

    用途只有一个：**给人参考**。以前确认过「编码 1366 = 沙姜 1 个」，
    现在读到同样的编码，人一眼就知道该填什么，不用再翻原图。
    """
    code = str((values or {}).get('customer_code') or '').strip()
    category = str((values or {}).get('category') or '').strip()
    if not code and not category:
        return []
    scored = []
    for item in examples or []:
        item_values = item.get('values') or {}
        item_code = str(item_values.get('customer_code') or '').strip()
        item_category = str(item_values.get('category') or '').strip()
        score = 0
        if code and item_code:
            score += 6 if item_code == code else (3 if (code in item_code or item_code in code) else 0)
        if category and item_category == category:
            score += 3
        if score <= 0:
            continue
        scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get('id'))))
    return [{'id': item.get('id'), 'row_id': item.get('row_id'),
             'values': item.get('values'), 'heading_text': item.get('heading_text'),
             'confirmed_at': item.get('confirmed_at'), 'reviewer': item.get('reviewer'),
             'image_sha256': item.get('image_sha256')}
            for _score, item in scored[:max(0, limit)]]


# --------------------------------------------------------------- 复核载荷
def build_review_payload(records, *, image_path, image_sha256, task_id='', reviewer='',
                         customer=None, notes=None, unmatched=None, image_root=None,
                         examples=None):
    """把手写辅助通道的输出组装成核对页要用的载荷。

    每行同时保留「AI 读到的值」与「当前值」：核对就是改当前值，
    两者的差异正是学习的原料。
    """
    rows = []
    known = list(examples or [])
    for index, record in enumerate(records or []):
        evidence = record.get('_evidence') or {}
        candidates = evidence.get('candidates') or {}
        view = []
        for field, items in sorted(candidates.items()):
            for candidate in items:
                view.append({'field': field, 'label': FIELD_LABELS.get(field, field),
                             'value': candidate.get('value'), 'source': candidate.get('source'),
                             'source_label': SOURCE_LABELS.get(candidate.get('source'), candidate.get('source')),
                             'confidence': candidate.get('confidence'),
                             'evidence': candidate.get('evidence') or ''})
        ai = {field: str(record.get(field) or '') for field in REVIEW_FIELDS}
        reasons = list(record.get('_review_reasons') or [])
        rows.append({
            'row_id': evidence.get('row_id') or ('row-%04d' % (index + 1)),
            'record_index': index,
            'ai': ai,
            'final': dict(ai),
            'status': 'doubt' if reasons else 'ok',
            'bbox': list(evidence.get('bbox')) if evidence.get('bbox') else None,
            'heading_bbox': list(evidence.get('heading_bbox')) if evidence.get('heading_bbox') else None,
            'heading_text': evidence.get('heading_text'),
            'entry_text': evidence.get('entry_text'),
            'reasons': reasons,
            'observations': list(record.get('_observations') or []),
            'candidates': view,
            'suggestion': _suggestion(evidence, view),
            'similar': find_similar_examples(ai, known),
        })
    payload = {
        'schema_version': 1,
        'kind': 'handwriting-review',
        'task_id': task_id,
        'reviewer': reviewer,
        'customer': customer,
        'image_path': str(image_path),
        'image_sha256': image_sha256,
        'image_root': str(image_root) if image_root else None,
        'notes': list(notes or []),
        'rows': rows,
        'unmatched': list(unmatched or []),
        'created_at': _now(),
    }
    return payload


def _suggestion(evidence, view):
    """一句话建议：能裁决的说结论，不能裁决的说「不能确定」。"""
    for decision in evidence.get('decisions') or []:
        if decision.get('action') == 'replace':
            return '建议改成「%s」（%s）' % (decision.get('value'), decision.get('reason'))
    values = {}
    for candidate in view:
        values.setdefault(candidate['field'], [])
        if candidate.get('value') not in values[candidate['field']]:
            values[candidate['field']].append(candidate.get('value'))
    parts = []
    for field, items in sorted(values.items()):
        if len(items) > 1:
            parts.append('%s 有 %s 几种读法' % (FIELD_LABELS.get(field, field), '、'.join(map(str, items))))
    if parts:
        return '不能确定：' + '；'.join(parts) + '，请对照原图人工判断'
    return '两路一致，没有需要改动的地方'


# --------------------------------------------------------------- 校验
def validate_review_payload(payload):
    """返回错误列表；空列表表示可以落盘。"""
    errors = []
    if not isinstance(payload, dict):
        return ['核对结果不是对象']
    if payload.get('schema_version') != 1:
        errors.append('schema_version 必须是 1')
    if not str(payload.get('reviewer') or '').strip():
        errors.append('必须填写复核人')
    if not str(payload.get('image_sha256') or '').strip():
        errors.append('缺少原图 sha256')
    rows = payload.get('rows')
    if not isinstance(rows, list) or not rows:
        errors.append('没有可确认的明细')
        return errors
    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append('第 %d 行不是对象' % (index + 1))
            continue
        row_id = str(row.get('row_id') or '')
        if not row_id:
            errors.append('第 %d 行缺少行 ID' % (index + 1))
        elif row_id in seen:
            errors.append('行 ID 重复：%s' % row_id)
        else:
            seen.add(row_id)
        if row.get('status') == 'removed':
            continue
        final = row.get('final') or {}
        for field in REQUIRED_FIELDS:
            value = final.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append('第 %d 行（%s）的%s还没填' % (index + 1, row_id or '?',
                                                           FIELD_LABELS.get(field, field)))
    return errors


# --------------------------------------------------------------- 标注落盘
def build_annotation(payload, *, image_root=None, difficulties=None, doc_type='handwritten',
                     source_group=None, split='unassigned'):
    """按 `dataset.prepare` 的 schema 生成标注文件内容。"""
    rows = []
    for row in payload.get('rows') or []:
        if row.get('status') == 'removed':
            continue
        final = row.get('final') or {}
        rows.append({
            'row_id': row.get('row_id'),
            'category': str(final.get('category') or '').strip(),
            'customer_code': str(final.get('customer_code') or '').strip(),
            'weight': str(final.get('weight') or '').strip(),
            'unit': str(final.get('unit') or '').strip(),
            'remark': str(final.get('remark') or '').strip(),
            'prediction_index': row.get('record_index'),
        })
    image_path = str(payload.get('image_path') or '')
    if image_root:
        try:
            image_path = Path(image_path).resolve().relative_to(Path(image_root).resolve()).as_posix()
        except (ValueError, OSError):
            pass
    return {
        'schema_version': 1,
        'image_sha256': payload.get('image_sha256'),
        'image_path': image_path,
        'source_group': source_group or payload.get('source_group') or 'unresolved',
        'split': split,
        'review_status': 'confirmed',
        'reviewer': str(payload.get('reviewer') or '').strip(),
        'confirmed_at': payload.get('confirmed_at') or _now(),
        'doc_type': doc_type,
        'difficulties': list(difficulties or []),
        'records': rows,
        'regions': [],
        'complete': True,
        'notes': str(payload.get('notes_text') or '人工核对确认。'),
    }


def save_annotation(payload, *, annotations_dir, image_root=None, **kwargs):
    """把标注写进 `data/annotations/`。写盘前先校验。"""
    errors = validate_review_payload(payload)
    if errors:
        raise LearningError('；'.join(errors[:8]))
    annotation = build_annotation(payload, image_root=image_root, **kwargs)
    target = Path(annotations_dir) / ('%s.json' % annotation['image_sha256'])
    write_json(target, annotation)
    return str(target)


# --------------------------------------------------------------- 学习
def _corrections(row):
    """一行里「AI 读到的值」与「人工确认的值」的差异。"""
    ai = row.get('ai') or {}
    final = row.get('final') or {}
    changes = []
    for field in REVIEW_FIELDS:
        before = str(ai.get(field) or '').strip()
        after = str(final.get(field) or '').strip()
        if before != after:
            changes.append({'field': field, 'label': FIELD_LABELS.get(field, field),
                            'from': before, 'to': after})
    return changes


def learn_vocabulary(payload, *, vocabulary_path):
    """把「AI 读错 → 人工改正」学成词典别名。

    只学**读错**的写法：AI 读对了的直接成为规范值；
    数量不学（数量是数不是词）。
    """
    target = Path(vocabulary_path)
    vocabulary = load_vocabulary(target)
    entries = {entry.id: entry for entry in vocabulary.entries}
    canonical_of = {}
    for entry in vocabulary.entries:
        canonical_of[(entry.field, entry.canonical)] = entry
    changed, learned, pending = [], [], []
    confirmed_at = payload.get('confirmed_at') or _now()
    customer = payload.get('customer') or None
    for row in payload.get('rows') or []:
        if row.get('status') == 'removed':
            continue
        ai = row.get('ai') or {}
        final = row.get('final') or {}
        for field in LEARNABLE_FIELDS:
            confirmed = str(final.get(field) or '').strip()
            if not confirmed:
                continue
            observed = str(ai.get(field) or '').strip()
            key = (field, confirmed)
            entry = canonical_of.get(key)
            if entry is None:
                entry_id = _entry_id(field, confirmed)
                entry = entries.get(entry_id)
                if entry is None:
                    entry = _new_entry(entry_id, field, confirmed, customer, confirmed_at)
                    entries[entry_id] = entry
                    canonical_of[key] = entry
                    learned.append({'field': field, 'canonical': confirmed, 'aliases': []})
                else:
                    canonical_of[key] = entry
            if observed and observed != confirmed and observed not in entry.aliases:
                if field in _PENDING_ALIAS_FIELDS:
                    # 单位跨语义、编码错读：一次纠错不能证明等价，先进入待审核区。
                    pending.append({'field': field, 'from': observed, 'to': confirmed,
                                    'customer': customer,
                                    'source_image': str(payload.get('image_sha256') or ''),
                                    'row_id': row.get('row_id'),
                                    'reviewer': str(payload.get('reviewer') or '').strip(),
                                    'confirmed_at': confirmed_at,
                                    'reason': 'cross_semantic_unit' if field == 'unit'
                                    else 'code_misread'})
                    continue
                # Entry 是不可变的：重建一条带新别名的，别就地改。
                entry = replace(entry, aliases=entry.aliases + (observed,))
                entries[entry.id] = entry
                canonical_of[key] = entry
                changed.append({'field': field, 'canonical': confirmed, 'alias': observed,
                                'row_id': row.get('row_id')})
    if pending:
        _write_pending(_pending_path(target), pending)
    if changed or learned:
        payload_out = Vocabulary(revision=_revision(), entries=tuple(entries.values())).as_dict()
        save_vocabulary(target, payload_out)
        revision = payload_out['revision']
    else:
        revision = vocabulary.revision
    return {'learned': learned, 'changed': changed, 'pending': pending,
            'vocabulary_entries': len(entries), 'revision': revision}


def _new_entry(entry_id, field, canonical, customer, confirmed_at):
    return Entry(id=entry_id, field=field, canonical=canonical, aliases=(),
                 customers=(customer,) if customer else (),
                 source='human_correction', confirmed_at=confirmed_at, enabled=True)


def learn_examples(payload, *, examples_path):
    """把人工确认过的明细存成示例库，带原图坐标和纠错前后对照。"""
    target = Path(examples_path)
    existing = read_json(target) if target.exists() else {}
    if not isinstance(existing, dict) or existing.get('schema_version') != 1:
        existing = {'schema_version': 1, 'revision': _revision('e'), 'examples': []}
    examples = {item.get('id'): item for item in existing.get('examples', []) if isinstance(item, dict)}
    confirmed_at = payload.get('confirmed_at') or _now()
    added = 0
    for row in payload.get('rows') or []:
        if row.get('status') == 'removed':
            continue
        final = row.get('final') or {}
        values = {field: str(final.get(field) or '').strip() for field in REVIEW_FIELDS}
        if not any(values.values()):
            continue
        item_id = '%s-%s' % (str(payload.get('image_sha256') or '')[:12], row.get('row_id'))
        examples[item_id] = {
            'id': item_id,
            'image_sha256': payload.get('image_sha256'),
            'row_id': row.get('row_id'),
            'bbox': row.get('bbox'),
            'heading_text': row.get('heading_text'),
            'entry_text': row.get('entry_text'),
            'values': values,
            'ai_values': {field: str((row.get('ai') or {}).get(field) or '').strip()
                          for field in REVIEW_FIELDS},
            'corrections': _corrections(row),
            'customer': payload.get('customer'),
            'reviewer': str(payload.get('reviewer') or '').strip(),
            'confirmed_at': confirmed_at,
        }
        added += 1
    payload_out = {'schema_version': 1, 'revision': _revision('e'),
                   'examples': list(examples.values())}
    write_json(target, payload_out)
    return {'examples': len(payload_out['examples']), 'written': added,
            'revision': payload_out['revision']}


def save_confirmed(payload, *, annotations_dir, vocabulary_path, examples_path,
                   image_root=None, **kwargs):
    """核对通过后的总入口：落标注 + 学词典 + 存示例。"""
    errors = validate_review_payload(payload)
    if errors:
        raise LearningError('；'.join(errors[:8]))
    payload = dict(payload)
    payload['confirmed_at'] = payload.get('confirmed_at') or _now()
    annotation_path = save_annotation(payload, annotations_dir=annotations_dir,
                                      image_root=image_root, **kwargs)
    vocabulary = learn_vocabulary(payload, vocabulary_path=vocabulary_path)
    examples = learn_examples(payload, examples_path=examples_path)
    return {'annotation_path': annotation_path, 'vocabulary': vocabulary,
            'examples': examples, 'confirmed_at': payload['confirmed_at']}


# --------------------------------------------------------------- 确认后学习
def learn_from_confirmed_records(records, *, vocabulary_path, examples_path, customer=None):
    """把人工改过 / 确认过的行学进词典和示例库，**不区分识别通道**。

    决定因素是「人工确认状态」（`_user_edited` 或 `_user_confirmed`）与识别通道，
    不是图片是否真的手写：

      · 手写辅助通道的行带 `_evidence`，能比对出「AI 读错 → 人工改对」的别名；
      · 普通（非手写）识别结果没有 `_evidence`，但人工改过/确认过的值本身就是
        高准确率的参考数据，直接作为规范值收进词典（品类/编码/单位/备注）；
      · 数量不进词典（learn_vocabulary 里 LEARNABLE_FIELDS 已排除 weight）；
      · 任何异常都静默返回空结果，绝不因学习失败打断识别或打印。
    """
    try:
        rows = []
        image_sha256 = ''
        for record in records or []:
            if not isinstance(record, dict):
                continue
            if not (record.get('_user_edited') or record.get('_user_confirmed')):
                continue
            evidence = record.get('_evidence')
            evidence = evidence if isinstance(evidence, dict) else {}
            # final = 用户在清单里改对的值（当前值）。
            final = {field: str(record.get(field) or '') for field in REVIEW_FIELDS}
            # ai = AI 读到的原值：手写通道从候选里取「模型」来源；取不到就退回当前值，
            # 这样普通（非手写）行也能照常收录规范值，只是没有「读错别名」可学。
            ai = dict(final)
            candidates = evidence.get('candidates') or {}
            for field in REVIEW_FIELDS:
                for candidate in candidates.get(field) or []:
                    if candidate.get('source') == 'model' and candidate.get('value'):
                        ai[field] = str(candidate['value'])
                        break
            # 行标识：手写通道有 evidence.row_id；普通通道用 _id / 序号兜底，别撞成同一个。
            row_id = evidence.get('row_id') or ('id-%s' % (record.get('_id', len(rows) + 1)))
            rows.append({
                'row_id': str(row_id),
                'record_index': record.get('_record_index', len(rows)),
                'ai': ai,
                'final': final,
                'bbox': list(evidence.get('bbox')) if evidence.get('bbox') else None,
                'heading_text': evidence.get('heading_text'),
                'entry_text': evidence.get('entry_text'),
            })
            if not image_sha256:
                image_sha256 = str(record.get('_img_sha256') or record.get('_src')
                                   or record.get('_img') or '')
        if not rows:
            return {'learned': 0, 'changed': 0, 'pending': 0, 'examples': 0}
        payload = {
            'schema_version': 1,
            'image_sha256': image_sha256 or ('batch-%s' % _now()),
            'customer': customer,
            'reviewer': '人工核对',
            'rows': rows,
            'confirmed_at': _now(),
        }
        vocabulary = learn_vocabulary(payload, vocabulary_path=vocabulary_path)
        examples = learn_examples(payload, examples_path=examples_path)
        return {'learned': len(vocabulary.get('learned', [])),
                'changed': len(vocabulary.get('changed', [])),
                'pending': len(vocabulary.get('pending', [])),
                'examples': examples.get('written', 0)}
    except Exception:
        return {'learned': 0, 'changed': 0, 'pending': 0, 'examples': 0}
