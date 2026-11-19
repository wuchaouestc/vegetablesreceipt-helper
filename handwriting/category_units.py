"""Customer-scoped category/unit dictionary derived only from confirmed examples."""
from collections import Counter
from copy import deepcopy
from .storage import read_json, write_json
from .geometry import normalize_text
from .unit_consensus import KNOWN_UNITS

UNIT_DISAGREEMENT = '单位两路读数不同，待核对'


def build_dictionary(examples):
    groups = {}
    seen = set()
    for item in examples:
        if not isinstance(item, dict) or not item.get('reviewer') or not item.get('confirmed_at'):
            continue
        values = item.get('values') or {}
        category = normalize_text(values.get('category'))
        unit = normalize_text(values.get('unit'))
        customer = str(item.get('customer') or '')
        source = str(item.get('image_sha256') or '')
        row = str(item.get('row_id') or '')
        if not category or unit not in KNOWN_UNITS or not source or not row:
            continue
        if any(word in category for word in ('删除', '不存在')):
            continue
        identity = (customer, source, row)
        if identity in seen:
            continue
        seen.add(identity)
        groups.setdefault((customer, category), Counter())[unit] += 1
    return {'schema_version': 1, 'source': 'human_confirmed_examples',
            'entries': [{'customer': c, 'category': cat, 'units': dict(counts)}
                        for (c, cat), counts in sorted(groups.items())]}


def refresh_dictionary(examples_path, dictionary_path):
    data = read_json(examples_path)
    if data.get('schema_version') != 1 or not isinstance(data.get('examples'), list):
        raise ValueError('invalid confirmed examples')
    dictionary = build_dictionary(data['examples'])
    write_json(dictionary_path, dictionary)
    return dictionary


def apply_category_units(records, dictionary, customer=None):
    out = deepcopy(records)
    changed = 0
    resolved = 0
    document_units = {}
    for row in out:
        evidence = row.get('_evidence') or {}
        if not evidence.get('matched') or not evidence.get('bbox'):
            continue
        category = normalize_text(row.get('category'))
        for candidate in (evidence.get('candidates') or {}).get('unit', []):
            if candidate.get('source') == 'model':
                unit = normalize_text(candidate.get('value'))
                document_units.setdefault(category, {}).setdefault(unit, set()).add(tuple(evidence['bbox']))
    for row in out:
        if row.get('_user_edited') or row.get('_user_confirmed'):
            continue
        category = normalize_text(row.get('category'))
        entries = [e for e in dictionary.get('entries', []) if e.get('category') == category
                   and e.get('customer') == str(customer or '')]
        if len(entries) != 1:
            continue
        units = entries[0].get('units') or {}
        # Any historical alternative prevents automatic replacement.
        if len(units) != 1:
            continue
        target, count = next(iter(units.items()))
        current = normalize_text(row.get('unit'))
        if target not in KNOWN_UNITS or type(count) is not int or count < 3:
            continue
        reasons = row.get('_review_reasons') or []
        if UNIT_DISAGREEMENT in reasons:
            evidence = row.get('_evidence') or {}
            candidates = (evidence.get('candidates') or {}).get('unit', [])
            reads = {source: {normalize_text(c.get('value')) for c in candidates
                              if c.get('source') == source}
                     for source in ('model', 'ocr_row', 'review')}
            peers = document_units.get(category, {})
            # Resolve a known glyph conflict only when this row and other
            # same-category readings support the customer-specific annotations.
            if (customer and category and target == current == '斤' and reads['model'] == {'斤'}
                    and reads['ocr_row'] == {'个'} and not reads['review'] - {'斤'}
                    and len(peers.get('斤', ())) >= 2 and not peers.get('个')
                    and not any(reason != UNIT_DISAGREEMENT and '单位' in reason for reason in reasons)):
                remaining = [reason for reason in reasons if reason != UNIT_DISAGREEMENT]
                row['_observations'] = [*(row.get('_observations') or []),
                                       '单位已确认：本单同品类模型读数与本客户人工标注支持「斤」']
                if remaining:
                    row['_review_reasons'] = remaining
                else:
                    row.pop('_review_reasons', None)
                    if not (row.get('_hedged') or row.get('_suspect')):
                        row.pop('_uncertain', None)
                resolved += 1
            continue
        if current == target:
            continue
        # Preserve distinct packaging units; only the documented glyph confusion
        # and missing units can be automatically corrected in this first policy.
        if current and {current, target} != {'个', '斤'}:
            continue
        row['_category_unit_correction'] = {'original': row.get('unit'), 'unit': target,
                                           'support_rows': count, 'source': 'category_unit_dictionary'}
        row['unit'] = target
        row['_uncertain'] = True
        row.setdefault('_review_reasons', []).append('已按人工确认的品类单位关联校正，请核对')
        changed += 1
    return out, ['品类单位词典：校正 %d 行，确认单位分歧 %d 行（保留包装单位）' % (changed, resolved)]
