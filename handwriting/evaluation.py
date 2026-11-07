"""Conservative evaluation. Draft references never qualify as acceptance evidence."""
from decimal import Decimal, InvalidOperation
import unicodedata

FIELDS = ("category", "customer_code", "weight", "unit")


def normalized(value, field=None):
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if field == "weight":
        try:
            number = Decimal(text)
            if number.is_finite():
                return str(number.normalize())
        except InvalidOperation:
            pass
    return text


def score_records(annotation, predicted):
    """Match stable annotation row_id only, never match rows by equal amounts."""
    if annotation.get("review_status") != "confirmed" or not annotation.get("complete"):
        return {"status": "not_evaluable", "reason": "reference_not_confirmed_complete"}
    truth = annotation.get("records", [])
    ids = [row.get("row_id") for row in truth]
    if any(not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("Reference row IDs must be nonempty and unique")
    mapped = {row["row_id"]: row for row in truth}
    used, matched, correct_rows, unflagged = set(), 0, 0, 0
    correct, eligible = dict.fromkeys(FIELDS, 0), dict.fromkeys(FIELDS, 0)
    for row in predicted:
        row_id = row.get("row_id")
        reference = mapped.get(row_id) if row_id not in used else None
        flagged = bool(row.get("_uncertain") or row.get("review_reasons"))
        if reference is None:
            unflagged += not flagged
            continue
        used.add(row_id)
        matched += 1
        comparisons = []
        for field in FIELDS:
            if reference.get(field) is None:
                continue
            eligible[field] += 1
            same = normalized(row.get(field), field) == normalized(reference[field], field)
            correct[field] += same
            comparisons.append(same)
        correct_rows += len(comparisons) == len(FIELDS) and all(comparisons)
        unflagged += (not all(comparisons)) and not flagged
    return {"status": "scored", "reference_rows": len(truth), "output_rows": len(predicted),
            "matched_rows": matched, "recall": matched / len(truth) if truth else None,
            "precision": matched / len(predicted) if predicted else None,
            "complete_row_accuracy": correct_rows / len(truth) if truth else None,
            "field_correct": correct, "field_eligible": eligible, "unflagged_errors": unflagged}


def region_text(blocks, box):
    """OCR diagnostics restricted to the annotated region, not whole-page evidence."""
    x0, y0, x1, y1 = box
    selected = []
    for block in blocks:
        polygon = block.get("polygon") or []
        if not polygon:
            continue
        points = [(p.get("x", p.get("X")), p.get("y", p.get("Y"))) if isinstance(p, dict) else p
                  for p in polygon]
        cx, cy = sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            selected.append((cy, cx, block.get("text", "")))
    return " ".join(text for _, _, text in sorted(selected))


def edit_distance(left, right):
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def score_regions(annotation, blocks, allow_draft=False):
    if annotation.get("review_status") != "confirmed" and not allow_draft:
        return {"status": "not_evaluable", "reason": "reference_not_confirmed"}
    rows = []
    for region in annotation.get("regions", []):
        if not region.get("text") or region.get("uncertain"):
            continue
        expected = "".join(normalized(region["text"]).split())
        actual = "".join(normalized(region_text(blocks, region["bbox"])).split())
        rows.append({"region_id": region["region_id"], "expected": expected, "actual": actual,
                     "edit_distance": edit_distance(expected, actual), "characters": len(expected)})
    chars = sum(r["characters"] for r in rows)
    return {"status": "draft_diagnostic" if annotation.get("review_status") != "confirmed" else "scored",
            "regions": rows, "characters": chars,
            "cer": sum(r["edit_distance"] for r in rows) / chars if chars else None,
            "limitation": "区域文本诊断不是整单明细正确率；框内附加字也计入差异。"}
