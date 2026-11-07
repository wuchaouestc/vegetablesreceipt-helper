"""python -m handwriting: offline by default; paid calls require --live."""
import argparse
import json
from dataclasses import asdict
from pathlib import Path
from .storage import data_root, write_json, read_json


def main():
    parser = argparse.ArgumentParser(description="独立手写库：原图、词典候选、可复查的 OCR 对照")
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory", help="去重清单和非覆盖标注模板")
    inventory.add_argument("--images", type=Path, default=data_root() / "手写素材")
    inventory.add_argument("--output", type=Path, default=data_root() / "evaluations" / "manifest.json")
    inventory.add_argument("--annotations", type=Path, default=data_root() / "annotations")
    preview = commands.add_parser("preview", help="离线词典候选，不发请求")
    preview.add_argument("--dictionary", type=Path, required=True)
    preview.add_argument("--records", type=Path, required=True)
    preview.add_argument("--customer")
    preview.add_argument("--output", type=Path, required=True)
    recognize = commands.add_parser("recognize", help="显式调用独立实验通道")
    recognize.add_argument("image", type=Path)
    recognize.add_argument("--live", action="store_true")
    recognize.add_argument("--action", choices=["GeneralAccurateOCR", "GeneralHandwritingOCR"], default="GeneralAccurateOCR")
    recognize.add_argument("--model")
    recognize.add_argument("--output", type=Path, required=True)
    review = commands.add_parser("review", help="打开本地核对网页（人工核对 → 标注落盘 + 学习）")
    review.add_argument("image", type=Path)
    review.add_argument("--records", type=Path, help="已保存的识别结果 JSON；不给就现场跑一次")
    review.add_argument("--live", action="store_true", help="现场调接口重新识别（可能产生费用）")
    review.add_argument("--port", type=int, default=8765)
    review.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.command == "inventory":
        from .dataset import prepare
        result = prepare(args.images, args.output, args.annotations)
        print(json.dumps({k: result[k] for k in ("file_count", "unique_bytes", "unique_pixels", "manifest_id")}, ensure_ascii=False))
    elif args.command == "preview":
        from .vocabulary import load_vocabulary, annotate_records
        result = annotate_records(load_vocabulary(args.dictionary), read_json(args.records), customer=args.customer,
                                  field_map={key: key for key in ('category', 'customer_code', 'unit', 'remark')})
        write_json(args.output, result)
        from .reports import vocabulary_preview
        vocabulary_preview(result['records'], result['suggestions'], result['warnings'], args.output.with_suffix('.html'))
        print(str(args.output))
    elif args.command == "recognize":
        if not args.live:
            parser.error("实际外部识别必须显式传 --live；这可能产生接口费用")
        from .pipeline import RecognitionRequest, recognize as execute
        import ttsb2
        request = RecognitionRequest(str(args.image), action=args.action, model=args.model, prompt=ttsb2.build_prompt(None))
        result = execute(request, output_dir=args.output.parent / (args.output.stem + "_artifacts"))
        write_json(args.output, asdict(result))
        print(result.status)
    elif args.command == "review":
        from .learning import build_review_payload, load_examples
        from .review_web import build_session, serve
        from .storage import digest
        image = Path(args.image)
        records, notes, unmatched = [], [], []
        if args.records:
            saved = read_json(args.records)
            records = saved.get('records') or []
            notes = saved.get('notes') or []
            page = (saved.get('evidence') or {}).get('page') or {}
            for index, item in enumerate(page.get('unmatched_entries') or []):
                unmatched.append({'id': 'unmatched-%d' % (index + 1),
                                  'text': item.get('text'), 'bbox': item.get('bbox')})
        elif args.live:
            parser.error("现场识别请用 .setup/核对网页_目标图.py --live；这里只读已保存的结果")
        else:
            parser.error("要给 --records（已保存的识别结果），或用 --live 现场识别")
        session = build_session(image, records,
                                image_sha256=digest(image),
                                annotations_dir=data_root() / 'annotations',
                                vocabulary_path=data_root() / 'dictionaries/handwriting.json',
                                examples_path=data_root() / 'learning/confirmed_examples.json',
                                image_root=image.parent, task_id=image.stem,
                                notes=notes, unmatched=unmatched)
        server, url = serve(session, port=args.port, background=False,
                            open_browser=not args.no_browser)
        print('核对网页：%s' % url)


if __name__ == "__main__":
    main()
