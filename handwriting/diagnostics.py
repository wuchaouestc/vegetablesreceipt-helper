"""Best-effort stage summaries and local field evidence artifacts; no credentials."""
import json
import time
from collections import Counter


def trace_input(request, config, image_version):
    try:
        from pathlib import Path
        from .storage import data_root, digest, fingerprint
        root = data_root()
        trace(request, 'start', image_sha256=image_version, data_root=str(root),
              image_name=Path(request.image_path).name, model=request.model,
              pipeline_sha256=(digest(Path(__file__).with_name('pipeline.py'))
                               if Path(__file__).with_name('pipeline.py').is_file() else 'bundled'),
              prompt_hash=fingerprint(request.prompt), config=config.as_dict(),
              annotation_files=sum(1 for _ in (root / 'annotations').glob('*.json')),
              matching_annotation_exists=(root / 'annotations' / (image_version + '.json')).is_file(),
              annotation_replay=False)
    except Exception:
        pass


def trace(request, stage, records=None, **metadata):
    """Diagnostics must never interrupt recognition, including malformed responses."""
    try:
        actual_model = metadata.pop('model', getattr(request, 'model', None))
        if records is not None:
            items = records if isinstance(records, list) else []
            rows = [row for row in items if isinstance(row, dict)]
            metadata.update(
                container_type=type(records).__name__, rows=len(items),
                field_present={key: sum(row.get(key) not in (None, '') for row in rows)
                               for key in ('category', 'customer_code', 'weight', 'unit', 'remark')},
                key_shapes=dict(Counter(','.join(sorted(str(k) for k in row)) for row in rows)),
                uncertain=sum(bool(row.get('_uncertain')) for row in rows),
                unmatched=sum(bool(row.get('_evidence')) and
                              not row['_evidence'].get('matched') for row in rows),
            )
            # Record only the actions and affected positions, never field values.
            metadata['decisions'] = [
                {'index': index, 'field': decision.get('field'), 'action': decision.get('action')}
                for index, row in enumerate(rows)
                for decision in (row.get('_evidence') or {}).get('decisions', [])
            ]
            metadata['unit_sources'] = [
                {'index': index, 'unit': row.get('unit') if row.get('unit') in ('斤', '个', '包', '公斤', '千克') else 'other',
                 'candidates': [{'source': c.get('source'),
                                 'unit': c.get('value') if c.get('value') in ('斤', '个', '包', '公斤', '千克') else 'other'}
                                for c in (row.get('_evidence') or {}).get('candidates', {}).get('unit', [])]}
                for index, row in enumerate(rows)]
            metadata['review_reason_counts'] = dict(Counter(
                str(reason).split('：', 1)[0]
                for row in rows for reason in row.get('_review_reasons', [])))
            metadata['model_differences'] = {
                field: [index for index, row in enumerate(rows)
                        if any(candidate.get('source') == 'model' and
                               str(candidate.get('value')) != str(row.get(field))
                               for candidate in (row.get('_evidence') or {}).get('candidates', {}).get(field, []))]
                for field in ('weight', 'unit')
            }
        from .storage import data_root
        directory = data_root().parent / 'log'
        directory.mkdir(parents=True, exist_ok=True)
        if records is not None:
            from .storage import write_json
            # Local diagnostic artifact; never store HTTP headers, credentials or images.
            safe_fields = ('category', 'customer_code', 'weight', 'unit', 'remark',
                           'name', 'entries', 'items', 'rows', 'details', 'text',
                           '_id', '_uncertain', '_review_reasons', '_evidence',
                           '_category_unit_correction')
            safe_rows = [{k: row[k] for k in safe_fields if k in row} for row in rows]
            suffix = ('-%s' % metadata['region_index']) if 'region_index' in metadata else ''
            detail = directory / '手写明细' / request.task_id / (stage + suffix + '.json')
            write_json(detail, {'stage': stage, 'model': actual_model, 'rows': safe_rows})
            metadata['detail_file'] = str(detail)
        payload = json.dumps(dict(task_id=request.task_id, stage=stage,
                                  model=actual_model, **metadata),
                             ensure_ascii=False, default=str)
        with (directory / time.strftime('程序_%Y%m%d.log')).open('a', encoding='utf-8') as stream:
            stream.write(time.strftime('%H:%M:%S') + '\t[手写阶段] ' + payload + '\n')
    except Exception:
        pass


def trace_display(records, stage, displayed=None):
    """Trace only real handwritten jobs, retaining task/row association into Tk."""
    try:
        from types import SimpleNamespace
        tasks = {r.get('_recognition_task') for r in records if r.get('_recognition_task')}
        for task in tasks:
            rows = [r for r in records if r.get('_recognition_task') == task]
            trace(SimpleNamespace(task_id=task, model=rows[0].get('_recognition_model')),
                  stage, records=rows, displayed=displayed)
    except Exception:
        pass


def trace_rendered(records, tree):
    try:
        trace_display(records, 'ui_rendered', displayed=[
            {'iid': str(iid), 'values': list(tree.item(iid, 'values'))}
            for iid in tree.get_children()])
    except Exception:
        pass
