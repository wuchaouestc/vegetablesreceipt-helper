"""One-time import of the user's reviewed draft, explicitly affirmed in this chat.

This does NOT change the generic scorer to accept arbitrary AI drafts.
"""
from pathlib import Path
from datetime import datetime
from copy import deepcopy
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from handwriting.storage import read_json, write_json, digest, fingerprint
from handwriting.acceptance import acceptance_report
from handwriting.evaluation import normalized, FIELDS

source = ROOT / 'data/annotations/手写人工复核草稿.json'
out = ROOT / 'data/evaluations/stage012_20261106'
draft = read_json(source)
results = read_json(out / 'results.json')
source_sha = digest(source)
if draft.get('kind') != 'handwriting-review-draft' or not draft.get('reviewer', '').strip():
    raise ValueError('缺少复核人或文件类型不符')
if draft.get('results_fingerprint') != fingerprint(results):
    raise ValueError('不是本次实测结果')
if len(draft.get('images', [])) != 2 or not all(i.get('complete') is True for i in draft['images']):
    raise ValueError('本次两张图片未全部确认完整')
stamp = datetime.now().astimezone().isoformat()
review = {'schema_version': 1, 'human_attestation': True, 'reviewer': draft['reviewer'],
    'confirmed_at': stamp, 'confirmation_time_source': '导入时间；原草稿没有记录实际点击确认的时间',
    'confirmation_basis': '用户在本聊天明确表示“人工复核页面我已经复核”，随后提交此文件并要求计算；文件两图complete均为true。',
    'source_filename': source.name, 'source_sha256': source_sha,
    'results_fingerprint': draft['results_fingerprint'], 'annotations': []}
for image in draft['images']:
    review['annotations'].append({'image_sha256': image['image_sha256'], 'image_path': image['image_path'],
        'review_status': 'confirmed', 'complete': True,
        'records': [{**deepcopy(r), 'review_status': 'confirmed'} for r in image['records']]})
report = acceptance_report(review, results, ROOT / 'data/手写素材')
totals = {'reference_rows': 0, 'output_rows': 0, 'matched_rows': 0, 'correct_rows': 0,
          'field_correct': dict.fromkeys(FIELDS, 0), 'field_eligible': dict.fromkeys(FIELDS, 0)}
index = {r['request']['image_sha256']: r['response']['records'] for r in results
         if r['request']['provider'].get('model') == 'deepseek-chat' and r['response']['status'] == 'ok'}
remarks = {'correct': 0, 'eligible': 0}
correct_with_remark = 0
# Independent direct comparison verifies the scorer's totals and counts optional remarks separately.
for annotation, image_report in zip(review['annotations'], report['images']):
    stats = image_report['scores']
    for k in ('reference_rows', 'output_rows', 'matched_rows'):
        totals[k] += stats[k]
    truth = annotation['records']
    predictions = index[annotation['image_sha256']]
    correct = 0
    for row in truth:
        p = predictions[row['prediction_index']] if row.get('prediction_index') is not None else {}
        is_correct = all(normalized(row[k], k) == normalized(p.get(k), k) for k in FIELDS)
        correct += is_correct
        if isinstance(row.get('remark'), str):
            remarks['eligible'] += 1
            remark_correct = normalized(row['remark']) == normalized(p.get('remark', ''))
            remarks['correct'] += remark_correct
            correct_with_remark += is_correct and remark_correct
    assert correct / len(truth) == stats['complete_row_accuracy']
    image_report['correct_rows'] = correct
    totals['correct_rows'] += correct
    for field in FIELDS:
        totals['field_correct'][field] += stats['field_correct'][field]
        totals['field_eligible'][field] += stats['field_eligible'][field]
        independent = sum(normalized(r[field], field) == normalized(
            predictions[r['prediction_index']].get(field), field) for r in truth if r.get('prediction_index') is not None)
        assert independent == stats['field_correct'][field]
totals['field_accuracy'] = {k: totals['field_correct'][k]/totals['field_eligible'][k] for k in FIELDS}
totals['complete_row_accuracy'] = totals['correct_rows']/totals['reference_rows']
totals['all_key_fields_accuracy'] = sum(totals['field_correct'].values()) / sum(totals['field_eligible'].values())
totals['remarks'] = remarks
totals['all_five_fields_correct_rows'] = correct_with_remark
report['aggregate'] = totals
report['confirmation_provenance'] = {k: review[k] for k in ('source_filename','source_sha256','confirmation_basis','confirmation_time_source')}
write_json(out / '人工确认准确率.human_reference.json', review)
write_json(out / '人工确认准确率.json', report)

labels = {'category':'品类', 'customer_code':'编码', 'weight':'数量', 'unit':'单位'}
lines = ['# 业务人工复核后的准确率', '', '范围：本批2张单据、40行人工复核结果，对照已保存的 deepseek-chat 原始输出。未重新调用接口，未修改生产配置。', '',
         '## 确认依据', '', '原文件虽然名为草稿，但已填写复核人、两图complete=true、40行关键字段和位置映射齐全。结合用户明确表示已完成复核并要求计算，导入为本次人工参考。原文件不变；导入时间不冒充实际点击确认时间。', '',
         '## 汇总', '', '| 指标 | 正确数/总数 | 准确率 |', '| --- | ---: | ---: |']
for k in FIELDS:
    lines.append(f"| {labels[k]} | {totals['field_correct'][k]}/{totals['field_eligible'][k]} | {totals['field_accuracy'][k]:.2%} |")
lines += [f"| 四个关键字段合计 | {sum(totals['field_correct'].values())}/{sum(totals['field_eligible'].values())} | {totals['all_key_fields_accuracy']:.2%} |",
          f"| 整行四个关键字段全部正确 | {totals['correct_rows']}/{totals['reference_rows']} | {totals['complete_row_accuracy']:.2%} |"]
if remarks['eligible']:
    lines.append(f"| 备注（补充统计，精确文本一致） | {remarks['correct']}/{remarks['eligible']} | {remarks['correct']/remarks['eligible']:.2%} |")
    lines.append(f"| 包括备注的五字段全部正确 | {correct_with_remark}/{totals['reference_rows']} | {correct_with_remark/totals['reference_rows']:.2%} |")
lines += ['', '数量按十进制数值比较（2.50与2.5相等）；编码保留前导零；品类/单位不套用词典别名。备注为精确文本比较，可能把同义写法计为不一致，需结合明细解释。', '',
          '## 分图', '', '| 图片 | 行数 | 四字段全对行数 | 整行准确率 |','| --- | ---: | ---: | ---: |']
for image in report['images']:
    s=image['scores']
    lines.append(f"| {image['image_path']} | {s['reference_rows']} | {image['correct_rows']} | {s['complete_row_accuracy']:.2%} |")
lines += ['', '## 关键字段错误明细', '', '| 图片/行 | 字段 | 人工确认值 | 模型输出 |', '| --- | --- | --- | --- |']
def cell(v):
    return str(v).replace('|','\\|').replace('\n',' ')
for image in report['images']:
    for difference in image['differences']:
        for k in difference['fields']:
            lines.append(f"| {difference['row_id']} | {labels[k]} | {cell(difference['expected'][k])} | {cell(difference['actual'][k])} |")
lines += ['', '## 适用边界', '',
    f"人工映射的明细为{totals['matched_rows']}条，标准明细{totals['reference_rows']}条，模型输出{totals['output_rows']}条。位置映射完整不等于字段正确。",
    '这些数字仅描述已人工复核的2张样本；不代表全部13张素材、未来单据、腾讯OCR原始结果或软件最终融合/过滤后的准确率。没有把同一模型的两次一致当作人工正确答案。',
    '确认文件、完整数值及逐行差异分别保存为 人工确认准确率.human_reference.json 与 人工确认准确率.json。']
(out / '人工确认准确率报告.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
assert digest(source) == source_sha
status = read_json(out / 'stage_status.json')
status['stage0']['confirmed_annotations'] = len(review['annotations'])
status['stage0']['acceptance'] = 'two_human_confirmed_samples_scored_not_full_dataset'
status['human_accuracy_report'] = '人工确认准确率.json'
write_json(out / 'stage_status.json', status)
print(totals)
