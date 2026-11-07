"""Read-only image inventory and draft annotation preparation."""
import hashlib
from pathlib import Path
from PIL import Image, ImageOps
from .storage import digest, fingerprint, write_json, read_json


def inventory(root):
    root = Path(root).resolve()
    entries, pixels = [], {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
            continue
        item = {"path": path.relative_to(root).as_posix(), "sha256": digest(path), "bytes": path.stat().st_size}
        try:
            with Image.open(path) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
                item.update(width=image.width, height=image.height, format=opened.format,
                            exif_orientation=opened.getexif().get(274, 1))
                pixel_hash = hashlib.sha256(str(image.size).encode() + image.tobytes()).hexdigest()
                # dHash only produces review candidates; never silently merges similar receipts.
                small = image.convert("L").resize((9, 8))
                values = list(small.get_flattened_data()) if hasattr(small, "get_flattened_data") else list(small.getdata())
                dhash = sum((values[y * 9 + x] > values[y * 9 + x + 1]) << (y * 8 + x)
                            for y in range(8) for x in range(8))
                item.update(pixel_sha256=pixel_hash, dhash=f"{dhash:016x}",
                            source_group=pixels.setdefault(pixel_hash, item["sha256"][:16]),
                            sample_kind="fragment" if min(image.size) < 200 else "document",
                            status="valid", split="unassigned")
        except (OSError, ValueError) as exc:
            item.update(status="invalid", error=type(exc).__name__, split="excluded")
        entries.append(item)
    similar = []
    valid = [item for item in entries if item["status"] == "valid"]
    for i, left in enumerate(valid):
        for right in valid[i + 1:]:
            distance = (int(left["dhash"], 16) ^ int(right["dhash"], 16)).bit_count()
            if left["pixel_sha256"] != right["pixel_sha256"] and distance <= 10:
                similar.append({"left": left["path"], "right": right["path"],
                                "distance": distance, "status": "needs_visual_review"})
    return {"schema_version": 1, "root": str(root), "entries": entries,
            "file_count": len(entries), "unique_bytes": len({e["sha256"] for e in entries}),
            "unique_pixels": len(pixels), "similar_candidates": similar,
            "manifest_id": fingerprint(entries)}


def prepare(root, output, annotations):
    manifest = inventory(root)
    write_json(output, manifest)
    for item in manifest["entries"]:
        if item["status"] != "valid":
            continue
        path = Path(annotations) / (item["sha256"] + ".json")
        if not path.exists():
            write_json(path, {"schema_version": 1, "image_sha256": item["sha256"],
                "image_path": item["path"], "source_group": item["source_group"],
                "split": "unassigned", "review_status": "pending", "reviewer": None,
                "doc_type": "unknown", "difficulties": [], "records": [], "regions": [],
                "complete": False, "notes": "待逐图校对，空列表不表示图片没有明细。"})
    return manifest


def import_visual_drafts(review_path, annotations, image_root):
    """Populate pending templates only. A model draft cannot confirm a reference."""
    review = read_json(review_path)
    imported = 0
    for image in review['images']:
        path = (Path(image_root) / image['filename']).resolve()
        if not path.is_relative_to(Path(image_root).resolve()) or digest(path) != image['sha256']:
            raise ValueError('Draft image/hash mismatch')
        target = Path(annotations) / (image['sha256'] + '.json')
        existing = read_json(target) if target.exists() else {}
        if existing.get('review_status') not in (None, 'pending'):
            continue
        existing.update(schema_version=1, image_sha256=image['sha256'], image_path=image['filename'],
            source_group=image['source_group'], split='unassigned', review_status='draft_visual',
            reviewer=review['reviewer'], doc_type=image['doc_type'], difficulties=image['difficulties'],
            records=image.get('records', []), complete=False, notes=image.get('notes', ''), regions=[])
        write_json(target, existing)
        imported += 1
    return imported


def annotation_audit(annotations):
    """Validate frozen splits cannot leak source groups or accept AI drafts."""
    groups, issues, counts = {}, [], {'confirmed': 0, 'draft_visual': 0, 'pending': 0}
    for path in sorted(Path(annotations).glob('*.json')):
        entry = read_json(path)
        if 'image_sha256' not in entry:
            continue
        status, split = entry.get('review_status'), entry.get('split', 'unassigned')
        counts[status] = counts.get(status, 0) + 1
        if split in ('development', 'acceptance'):
            group = entry.get('source_group')
            if not group:
                issues.append({'file': path.name, 'reason': 'missing_source_group'})
            elif group in groups and groups[group] != split:
                issues.append({'file': path.name, 'reason': 'source_group_leaks_across_splits'})
            groups[group] = split
        if split == 'acceptance' and (status != 'confirmed' or not entry.get('complete')):
            issues.append({'file': path.name, 'reason': 'acceptance_requires_confirmed_complete_reference'})
    return {'counts': counts, 'issues': issues, 'acceptance_ready': bool(groups) and not issues
            and 'acceptance' in groups.values()}
