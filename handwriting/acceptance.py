"""Human-reviewed accuracy only; API responses are immutable test evidence."""
import copy
import json
from pathlib import Path
from .evaluation import score_records, FIELDS
from .storage import digest, fingerprint, read_json, write_json


def acceptance_report(review, results, image_root):
    if review.get('schema_version') != 1 or review.get('human_attestation') is not True:
        raise ValueError('需要业务人员实际逐图核对确认，不能使用AI草稿')
    if not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip():
        raise ValueError('需要填写业务复核人')
    if not review.get('confirmed_at'):
        raise ValueError('缺少确认时间')
    if review.get('results_fingerprint') != fingerprint(results):
        raise ValueError('实测结果已变化，请对当前结果重新确认')
    indexed = {}
    for result in results:
        if result['request']['provider'].get('model') == 'deepseek-chat' and result['response']['status'] == 'ok':
            sha = result['request']['image_sha256']
            if sha in indexed:
                raise ValueError('同图存在多个模型结果，需先确定唯一评测批次')
            indexed[sha] = result
    reports, seen = [], set()
    for annotation in review.get('annotations', []):
        sha = annotation['image_sha256']
        if sha in seen:
            raise ValueError('重复图片标注')
        seen.add(sha)
        image = (Path(image_root) / annotation['image_path']).resolve()
        if not image.is_relative_to(Path(image_root).resolve()) or digest(image) != sha:
            raise ValueError('标注原图不匹配')
        if annotation.get('review_status') != 'confirmed' or annotation.get('complete') is not True:
            raise ValueError('需要确认已覆盖整张可见单据，不得用未完成草稿验收')
        if sha not in indexed:
            raise ValueError('缺少该图成功的模型结果')
        truth = annotation.get('records')
        if not isinstance(truth, list) or not truth:
            raise ValueError('本批手写整单参考明细不得为空')
        ids, indices = set(), set()
        predictions = copy.deepcopy(indexed[sha]['response']['records'])
        # Only the reviewer's explicit position mapping assigns IDs; never auto-match by values.
        for prediction in predictions:
            prediction.pop('row_id', None)
        for row in truth:
            if not isinstance(row.get('row_id'), str) or not row['row_id'] or row['row_id'] in ids:
                raise ValueError('行ID必须非空且唯一')
            ids.add(row['row_id'])
            if any(not isinstance(row.get(k), str) or not row[k].strip() for k in FIELDS):
                raise ValueError('关键字段未确认，暂不能计算整单准确率')
            index = row.get('prediction_index')
            if index is not None:
                if type(index) is not int or not 0 <= index < len(predictions) or index in indices:
                    raise ValueError('模型行映射越界或重复')
                indices.add(index)
                predictions[index]['row_id'] = row['row_id']
        scores = score_records(annotation, predictions)
        # Raw API notes have no reliable per-row alert mapping; don't misreport silent errors.
        scores.pop('unflagged_errors', None)
        scores['alert_metric'] = 'not_evaluated_raw_model_notes_not_row_aligned'
        differences = []
        for row in truth:
            index = row.get('prediction_index')
            predicted = predictions[index] if index is not None else {}
            from .evaluation import normalized
            fields = [key for key in FIELDS if normalized(row[key], key) != normalized(predicted.get(key), key)]
            if fields:
                differences.append({'row_id': row['row_id'], 'prediction_index': index, 'fields': fields,
                                    'expected': {k: row[k] for k in fields},
                                    'actual': {k: predicted.get(k) for k in fields}})
        reports.append({'image_sha256': sha, 'image_path': annotation['image_path'],
                        'scores': scores, 'differences': differences})
    if not reports:
        raise ValueError('没有已经人工确认的样本')
    return {'schema_version': 1, 'status': 'human_confirmed_sample_scored',
            'reviewer': review['reviewer'], 'confirmed_at': review['confirmed_at'],
            'images': reports, 'results_fingerprint': review['results_fingerprint'],
            'scope': 'deepseek-chat原始明细；不含生产合并/客户过滤，不代表全部13图或泛化准确率',
            'production_modified': False}


def import_and_score(review_file, results_file, image_root, output):
    review, results = read_json(review_file), read_json(results_file)
    result = acceptance_report(review, results, image_root)
    # Store the attestation separately; never silently overwrite original draft annotations.
    write_json(Path(output).with_suffix('.human_reference.json'), review)
    write_json(output, result)
    return result
