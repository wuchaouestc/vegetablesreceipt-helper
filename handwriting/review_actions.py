"""Explicit review learning, preserving per-image partial annotation provenance."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import re
import unicodedata
from .storage import digest, write_json, read_json
from .learning import learn_from_confirmed_records, REVIEW_FIELDS

STATES = ('✓ 已确认', '已识别', '? 拿不准', '! 可疑')


def _key(value):
    return ''.join(unicodedata.normalize('NFKC', str(value or '')).split()).lower()


def _code(value):
    match = re.match(r'[a-z]*\d+(?:-\d+)*', _key(value))
    return match.group() if match else _key(value)


def attach_review_locations(records, blocks):
    """Use a unique detail cell and its in-cell occurrence as image-stable identity."""
    from .geometry import normalize_blocks
    cells = normalize_blocks(blocks)
    pattern = re.compile(r'[（(]([a-zA-Z]*\d+(?:-\d+)*)[^()（）]*[）)]\s*(\d+(?:\.\d+)?)\s*(斤|公斤|千克|kg|包|个|件|袋)', re.I)
    for row in records:
        hits = []
        for cell in cells:
            for number, match in enumerate(pattern.finditer(cell.text)):
                if (_code(row.get('customer_code')) == _code(match[1])
                        and _key(row.get('weight')) == _key(match[2])
                        and _key(row.get('unit')) == _key(match[3])):
                    hits.append({'bbox': list(cell.bbox), 'detail_index': number})
        if len(hits) == 1:
            row['_review_location'] = hits[0]


def apply_partial_review(records, image_path, customer, root):
    """Restore only uniquely matched same-image, same-customer reviewed rows."""
    output = deepcopy(records)
    try:
        data = read_json(Path(root) / 'annotations/reviewed_rows' / (digest(image_path) + '.json'))
    except (OSError, ValueError):
        return output, {'reused': 0, 'unresolved': 0}
    if data.get('customer') != customer or data.get('image_sha256') != digest(image_path):
        return output, {'reused': 0, 'unresolved': 0}
    proposals = {}
    annotations = data.get('records') or []
    for saved in annotations:
        final = saved.get('values') or {}
        original = dict(final)
        for change in reversed(saved.get('changes') or []):
            original[change['field']] = change.get('before', '')
        choices = []
        for i, row in enumerate(output):
            location = saved.get('location')
            if location and row.get('_review_location'):
                matched = location == row['_review_location']
            else:
                matched = (_code(row.get('customer_code')) in
                           {_code(original.get('customer_code')), _code(final.get('customer_code'))}
                           and _key(row.get('weight')) == _key(original.get('weight'))
                           and _key(row.get('unit')) == _key(original.get('unit'))
                           and _key(row.get('category')) in
                           {_key(original.get('category')), _key(final.get('category'))})
            if matched:
                choices.append(i)
        if len(choices) == 1:
            proposals.setdefault(choices[0], []).append(saved)
    reused = 0
    for index, candidates in proposals.items():
        if len(candidates) != 1:
            continue
        saved = candidates[0]
        row = output[index]
        fields = REVIEW_FIELDS if saved.get('confirmed') else {
            change['field'] for change in saved.get('changes') or [] if change.get('field') in REVIEW_FIELDS}
        if not fields:
            continue
        for field in fields:
            row[field] = saved['values'].get(field, '')
        row['_partial_review_fields'] = sorted(fields)
        row['_partial_review_replay'] = True
        row['_user_changes'] = deepcopy(saved.get('changes') or [])
        if not saved.get('confirmed'):
            row['_user_edited'] = True
        if saved.get('confirmed'):
            row['_user_confirmed'] = True
            row['_uncertain'] = False
            row['_suspect'] = False
            row.pop('_hedged', None)
            row.pop('_review_reasons', None)
            row['_warn'] = ''
        reused += 1
    return output, {'reused': reused, 'unresolved': len(annotations) - reused}


def mark_all(records, state):
    if state not in STATES:
        raise ValueError('unsupported review state')
    for row in records:
        row['_bulk_review_state'] = state
        row['_user_confirmed'] = state == '✓ 已确认'
        row['_uncertain'] = state == '? 拿不准'
        row['_suspect'] = state == '! 可疑'
        row['_review_excluded'] = state != '✓ 已确认'
        row['_review_state_changed_at'] = datetime.now().astimezone().isoformat()


def learn_reviewed(records, customer, root):
    groups = {}
    skipped = 0
    for row in records:
        if row.get('_review_excluded') or not (row.get('_user_edited') or row.get('_user_confirmed')):
            skipped += 1
            continue
        path = Path(row.get('_img') or '')
        if not path.is_file():
            raise ValueError('已核对记录缺少可用原图，未执行学习')
        sha = digest(path)
        item = deepcopy(row)
        item['_img_sha256'] = sha
        groups.setdefault(sha, []).append(item)
    result = {'rows': 0, 'images': len(groups), 'skipped': skipped}
    for sha, rows in groups.items():
        all_image_rows = [r for r in records if r.get('_img') == rows[0]['_img']]
        annotation = {'schema_version': 1, 'kind': 'partial_review', 'image_sha256': sha,
                      'image_path': rows[0]['_img'], 'customer': customer,
                      'review_status': 'confirmed_rows_only', 'complete': False,
                      'reviewed_at': datetime.now().astimezone().isoformat(),
                      'records': []}
        for i, row in enumerate(rows):
            evidence = row.get('_evidence')
            # The ordinary OCR flow stores a source name, while handwriting stores row details.
            row_id = evidence.get('row_id') if isinstance(evidence, dict) else None
            location = row.get('_review_location')
            if location:
                from .storage import fingerprint
                row_id = 'region-' + fingerprint(location)[:24]
            annotation['records'].append({
                'row_id': row_id or str(row.get('_id', i)),
                'values': {field: row.get(field, '') for field in REVIEW_FIELDS},
                'changes': row.get('_user_changes', []),
                'confirmed': bool(row.get('_user_confirmed')),
                'location': row.get('_review_location'),
            })
        target = root / 'annotations' / 'reviewed_rows' / (sha + '.json')
        previous = read_json(target) if target.exists() else {}
        saved = {str(r['row_id']): r for r in previous.get('records', [])}
        for current in annotation['records']:
            if current.get('location'):
                saved = {key: old for key, old in saved.items()
                         if old.get('location') != current['location']}
        saved.update({str(r['row_id']): r for r in annotation['records']})
        annotation['records'] = list(saved.values())
        write_json(target, annotation)
        stats = learn_from_confirmed_records(rows, vocabulary_path=root / 'dictionaries/handwriting.json',
                                            examples_path=root / 'learning/confirmed_examples.json', customer=customer)
        if not stats.get('examples'):
            raise ValueError('学习未成功保存示例，请检查文件权限或数据格式')
        if len(rows) == len(all_image_rows):
            snapshot = {'schema_version': 1, 'image_sha256': sha, 'customer': customer,
                        'complete': True, 'rows': [{f: r.get(f, '') for f in REVIEW_FIELDS} for r in rows]}
            write_json(root / 'reviewed_results' / str_customer(customer) / (sha + '.json'), snapshot)
            write_json(root / 'reviewed_results' / 'shared' / (sha + '.json'), snapshot)
        else:
            # A new partial review cannot leave an older complete snapshot authoritative.
            target_snapshot = root / 'reviewed_results' / str_customer(customer) / (sha + '.json')
            if target_snapshot.exists():
                prior_snapshot = read_json(target_snapshot)
                prior_snapshot['complete'] = False
                write_json(target_snapshot, prior_snapshot)
            shared = root / 'reviewed_results' / 'shared' / (sha + '.json')
            if shared.exists():
                shared_data = read_json(shared)
                shared_data['complete'] = False
                write_json(shared, shared_data)
        result['rows'] += len(rows)
    return result


def str_customer(customer):
    import hashlib
    return hashlib.sha256(str(customer or '').encode('utf-8')).hexdigest()[:24]


def load_reviewed_result(root, image_sha256, customer):
    try:
        shared = root / 'reviewed_results' / 'shared' / (image_sha256 + '.json')
        if shared.exists():
            data = read_json(shared)
        else:
            # Migrate old customer-scoped snapshots only when their answers agree.
            candidates = [read_json(p) for p in (root / 'reviewed_results').glob('*/' + image_sha256 + '.json')]
            if not candidates or any(d.get('complete') is not True for d in candidates):
                return None
            if any(d.get('rows') != candidates[0].get('rows') for d in candidates):
                return None
            data = candidates[0]
        if (data.get('schema_version') != 1 or data.get('complete') is not True
                or data.get('image_sha256') != image_sha256
                or not isinstance(data.get('rows'), list) or not data['rows']):
            return None
        if any(not isinstance(row, dict) or any(not isinstance(row.get(f), str) for f in REVIEW_FIELDS)
               for row in data['rows']):
            return None
        return [dict(row, _learned_replay=True) for row in data['rows']]
    except (OSError, ValueError, TypeError):
        return None
