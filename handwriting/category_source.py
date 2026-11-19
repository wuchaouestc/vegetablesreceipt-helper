"""Resolve handwritten categories using OCR geometry and model title candidates."""
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path

from .evidence import _same_number
from .geometry import normalize_text


CATEGORY_CONFIDENCE = 95


def load_title_annotations(root, image_sha256, customer):
    from .storage import read_json

    try:
        annotation = read_json(Path(root) / 'annotations' / (image_sha256 + '.json'))
    except (OSError, ValueError):
        return []
    if (not isinstance(annotation, dict) or annotation.get('image_sha256') != image_sha256
            or annotation.get('review_status') != 'confirmed'
            or annotation.get('customer') not in (None, '', customer)):
        return []
    return [r for r in (annotation.get('regions') or []) if isinstance(r, dict)
            and r.get('field') == 'category' and r.get('source') == 'human_correction'
            and r.get('visibility') == 'visible' and not r.get('uncertain')
            and isinstance(r.get('normalized_value'), str) and r['normalized_value'].strip()]


def _annotated_title(heading, annotations):
    x0, y0, x1, y1 = heading.bbox
    area = (x1 - x0) * (y1 - y0)
    names = set()
    for region in annotations:
        try:
            a, b, c, d = map(float, region['bbox'])
        except (KeyError, TypeError, ValueError):
            continue
        overlap = max(0, min(x1, c) - max(x0, a)) * max(0, min(y1, d) - max(y0, b))
        if area > 0 and overlap >= 0.7 * max(area, (c - a) * (d - b)):
            names.add(region['normalized_value'].strip())
    return next(iter(names)) if len(names) == 1 else None


def _majority(values):
    counts = Counter(value for value in values if value)
    if counts:
        value, count = counts.most_common(1)[0]
        if count * 2 > sum(counts.values()):
            return value
    return None


def _groups(records, page, layout):
    """Use exact code/quantity anchors to align model groups with OCR titles."""
    titles = {heading.index: heading for heading in layout.headings}
    by_column = defaultdict(list)
    for heading in titles.values():
        by_column[layout.columns[heading.index]].append(heading)
    entries = defaultdict(list)
    for row in layout.rows:
        for entry in row['entries']:
            heading = entry.heading or row.get('heading')
            if heading is None:
                # Use the unique nearest title above a row already placed in a column.
                above = [h for h in by_column[row['column']] if h.bbox[3] <= entry.bbox[1]]
                if above:
                    nearest = max(h.bbox[3] for h in above)
                    nearest_titles = [h for h in above if h.bbox[3] == nearest]
                    if len(nearest_titles) == 1:
                        heading = nearest_titles[0]
            if entry.unit:
                entries[normalize_text(entry.code)].append((entry.quantity, heading))

    candidates, anchors = [], defaultdict(list)
    for record in records:
        name = normalize_text(record.get('category'))
        hits = [heading.index if heading is not None else None
                for quantity, heading in entries[normalize_text(record.get('customer_code'))]
                if _same_number(record.get('weight'), quantity)]
        options = set(hits)
        candidates.append(options)
        if name and len(hits) == 1 and hits[0] is not None:
            anchors[name].append(hits[0])
    aligned = {}
    for name, votes in anchors.items():
        majority = Counter(votes).most_common(1)[0]
        if majority[1] >= 2 and majority[1] * 2 > len(votes):
            aligned[name] = majority[0]

    evidence = {item.record_index: item for item in page.rows}
    groups = defaultdict(list)
    for index, (record, options) in enumerate(zip(records, candidates)):
        preferred = aligned.get(normalize_text(record.get('category')))
        item = evidence.get(index)
        heading = item.heading if item is not None and item.matched else None
        if item is not None and item.group_heading is not None:
            group = item.group_heading.index
        elif heading is not None and item.recovered_gap is not None:
            group = heading.index
        elif heading is not None and preferred == heading.index:
            group = heading.index
        elif (item is not None and item.matched and preferred is not None
              and layout.columns[preferred] == item.column
              and len(by_column[item.column]) == 1
              and titles[preferred].bbox[3] <= item.bbox[1]
              and normalize_text(record.get('customer_code')) == normalize_text(item.entry.code)):
            # A displaced row can miss the narrow title's horizontal box.
            group = preferred
        elif preferred is not None and preferred in options:
            group = preferred
        elif preferred is None and len(options) == 1:
            group = next(iter(options))
        elif (not options and heading is not None and preferred in (None, heading.index)
              and normalize_text(record.get('customer_code')) == normalize_text(item.entry.code)):
            group = heading.index
        else:
            continue
        if group is not None:
            groups[group].append(index)
    return titles, groups


def apply_ocr_categories(records, page, threshold=CATEGORY_CONFIDENCE, *,
                         model_records=None, layout=None, review_title=None, annotations=()):
    rows = deepcopy(records)
    evidence = {r.record_index: r for r in page.rows}
    resolved = {}
    if model_records is not None and layout is not None:
        titles, groups = _groups(model_records, page, layout)
        def has_conflict(group):
            heading = titles[group]
            return any(name and name != normalize_text(heading.text)
                       for name in (normalize_text(model_records[i].get('category'))
                                    for i in groups[group]))

        for group in sorted(groups, key=lambda group: (not has_conflict(group), group)):
            indices = groups[group]
            heading = titles[group]
            names = [normalize_text(model_records[index].get('category')) for index in indices]
            annotated = _annotated_title(heading, annotations)
            if annotated:
                name, source, needs_review = annotated, 'human_heading_annotation', False
            elif (heading.confidence is not None and heading.confidence > threshold
                  and not has_conflict(group) and any(names)):
                name, source, needs_review = heading.text, 'tencent_ocr_heading', False
            else:
                reviewed = review_title(heading, names) if review_title else None
                if reviewed:
                    name, source, needs_review = reviewed, 'deepseek_title_review', False
                else:
                    name = _majority(names) or heading.text
                    source, needs_review = 'unconfirmed_group_heading', True
            for index in indices:
                resolved[index] = (name, source, needs_review)

    for index, row in enumerate(rows):
        if row.get('_user_edited') or row.get('_user_confirmed'):
            continue
        if model_records is not None and layout is not None:
            name, source, uncertain = resolved.get(index, ('', 'unresolved', True))
        else:
            item = evidence.get(index)
            heading = item.heading if item is not None and item.matched else None
            name = heading.text if heading is not None else ''
            source = 'tencent_ocr_heading' if heading is not None else 'unresolved'
            uncertain = heading is None or heading.confidence is None or heading.confidence <= threshold
        row['category'], row['_category_source'] = name, source
        if uncertain:
            row['_uncertain'] = True
            reason = '明细的品类归属不确定，请核对'
            if name:
                reason = '组标题待确认，请核对'
            row.setdefault('_review_reasons', []).append(reason)
    return rows
