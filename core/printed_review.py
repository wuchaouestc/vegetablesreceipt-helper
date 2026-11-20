"""按独立表格列定位印刷明细，逐字段核对并保留已确认值。"""
import re
from decimal import Decimal, InvalidOperation
from tempfile import TemporaryDirectory

from handwriting.geometry import normalize_blocks, v_overlap_ratio
from handwriting.review import crop_region


def _number(value):
    try:
        number = Decimal(str(value).strip())
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def review_columns(image_path, records, blocks, prompt, model, accept, key):
    cells = normalize_blocks(blocks)
    labels = {'category': ('商品名称', '物料名称', '品类'),
              'customer_code': ('客户编码',), 'unit': ('下单单位', '单位'),
              'weight': ('采购数量',)}
    headers = {}
    for field, names in labels.items():
        matches = [c for c in cells if c.text.strip() in names]
        if len(matches) != 1:
            return None
        headers[field] = matches[0]
    remarks = [c for c in cells if c.text.strip() in ('商品备注', '备注')]
    if len(remarks) == 1:
        headers['remark'] = remarks[0]
    if any(v_overlap_ratio(headers['customer_code'].bbox, h.bbox) < 0.5 for h in headers.values()):
        return None
    centers = {field: (h.bbox[0] + h.bbox[2]) / 2 for field, h in headers.items()}
    # Include non-target columns so totals and remarks cannot become field values.
    column_centers = sorted({(c.bbox[0] + c.bbox[2]) / 2 for c in cells
                             if v_overlap_ratio(c.bbox, headers['customer_code'].bbox) > 0.5})
    def column(cell, field):
        x = (cell.bbox[0] + cell.bbox[2]) / 2
        return abs(x - centers[field]) == min(abs(x - c) for c in column_centers)

    below = [c for c in cells if c.bbox[1] > max(h.bbox[3] for h in headers.values())]
    categories = sorted([c for c in below if column(c, 'category')
                         and re.search(r'[\u4e00-\u9fff]', c.text)], key=lambda c: c.bbox[1])
    details = []
    for code in sorted([c for c in below if column(c, 'customer_code')], key=lambda c: c.bbox[1]):
        row = {'customer_code': code}
        for field in ('weight', 'unit'):
            matches = [c for c in below if column(c, field) and v_overlap_ratio(c.bbox, code.bbox) >= 0.6]
            if len(matches) == 1:
                row[field] = matches[0]
        if len(row) == 3 and _number(row['weight'].text) is not None:
            if 'remark' in headers:
                matches = [c for c in below if column(c, 'remark')
                           and v_overlap_ratio(c.bbox, code.bbox) >= 0.6]
                if len(matches) == 1:
                    row['remark'] = matches[0]
            details.append(row)
    if not details:
        return []

    # A merged category can sit at its group's vertical center. Allocate only
    # when the printed group totals give one unique contiguous partition.
    totals = []
    for category in categories:
        matches = [c for c in below if centers['category'] < (c.bbox[0] + c.bbox[2]) / 2
                   < centers['customer_code'] and v_overlap_ratio(c.bbox, category.bbox) >= 0.5
                   and re.fullmatch(r'\d+(?:\.\d+)?\s*(?:斤|公斤|包|个|件)', c.text)]
        totals.append(_number(re.match(r'\d+(?:\.\d+)?', matches[0].text)[0]) if len(matches) == 1 else None)
    partitions = []
    def partition(group, start, ends):
        if len(partitions) > 1:
            return
        if group == len(categories):
            if start == len(details):
                partitions.append(ends)
            return
        total = Decimal(0)
        for end in range(start, len(details)):
            total += _number(details[end]['weight'].text)
            if total == totals[group]:
                partition(group + 1, end + 1, ends + [end + 1])
            if total > totals[group]:
                break
    if (categories and all(t is not None and t > 0 for t in totals)
            and all(_number(row['weight'].text) > 0 for row in details)):
        partition(0, 0, [])
    if len(partitions) == 1:
        start = 0
        for category, end in zip(categories, partitions[0]):
            if (details[start]['customer_code'].bbox[1] <= category.bbox[3]
                    and category.bbox[1] <= details[end - 1]['customer_code'].bbox[3]):
                for row in details[start:end]:
                    row['category'] = category
            start = end

    def equal(field, a, b):
        return _number(a) == _number(b) if field == 'weight' else key(a) == key(b)
    proposals = {}
    for index, record in enumerate(records):
        if not record.get('_uncertain') or record.get('_user_confirmed') or record.get('_user_edited'):
            continue
        ranked = []
        for row in details:
            agreements = sum(equal(f, record.get(f), c.text) for f, c in row.items())
            if agreements >= 3 and agreements >= len(row) - 1:
                ranked.append((agreements, row))
        if not ranked:
            continue
        best = max(score for score, _ in ranked)
        matches = [row for score, row in ranked if score == best]
        if len(matches) == 1:
            row = matches[0]
            # Other records may already own the same printed row.
            competitors = [r for r in records if sum(equal(f, r.get(f), c.text)
                           for f, c in row.items()) >= best]
            if len(competitors) == 1:
                proposals[index] = row

    notes = []
    owners = {}
    for index, row in proposals.items():
        owners.setdefault(row['customer_code'].index, []).append(index)
    with TemporaryDirectory(prefix='printed-columns-') as folder:
        for index, row in proposals.items():
            if len(owners[row['customer_code'].index]) != 1:
                continue
            record = records[index]
            pending = {f for f, c in row.items() if not equal(f, record.get(f), c.text)}
            for field in tuple(pending):
                if (row[field].confidence or 0) > 95:
                    record[field] = row[field].text.strip()
                    pending.remove(field)
            if pending:
                boxes = [c.bbox for f, c in row.items() if f != 'category']
                bbox = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                        max(b[2] for b in boxes), max(b[3] for b in boxes))
                crop = crop_region(image_path, bbox, folder, f'row-{index}')
                if crop:
                    try:
                        response = model(crop, None, 'off', prompt + '\n'
                            f'只读取本裁图的一行。返回一条 records，待核对字段为 {sorted(pending)}。'
                            '不要猜测，看不清留空。', timeout=30)
                        returned = response.get('records') or []
                        if len(returned) == 1 and isinstance(returned[0], dict):
                            for field in tuple(pending):
                                if returned[0].get(field) and equal(field, returned[0][field], row[field].text):
                                    record[field] = row[field].text.strip()
                                    pending.remove(field)
                    except Exception as exc:
                        notes.append(f'第{index + 1}条局部复核失败：{type(exc).__name__}')
            if not pending:
                accept(record, 'ocr')
                notes.append(f'第{index + 1}条已按印刷分列同行证据校正')
            else:
                record['_warn'] = '印刷明细待核对：' + '、'.join(sorted(pending))
    return notes
