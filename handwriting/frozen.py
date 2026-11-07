"""冻结验收报告：关键字段错误下降、未提示错误、核对负担、成本。

口径固定（《方案》第十节 10.2 / 10.3）：
  · 明细按**稳定 row_id / 原图位置**一对一关联，不因数量相同跨行匹配；
  · 关键字段准确率按品类、编码、数量、单位分别统计；
  · 整行完全正确率 = 四个关键字段全对的明细数 / 标注明细数；
  · 未提示错误 = 实际有错但没标成待核对的明细数；
  · 核对负担 = 待核对比例、错误被提示比例、正确行误提示比例；
  · 性能成本 = 请求数、耗时、费用、失败与降级比例。
"""
from __future__ import annotations

from .evaluation import FIELDS, normalized


def _flagged(record):
    return bool(record.get('_uncertain') or record.get('_review_reasons'))


def score_image(annotation, predictions):
    """按人工标注的 prediction_index 一对一核对一张图片。"""
    truth_rows = annotation.get('records', [])
    field_correct = dict.fromkeys(FIELDS, 0)
    field_eligible = dict.fromkeys(FIELDS, 0)
    row_correct = 0
    unflagged_errors = 0
    flagged_rows = 0
    false_alarms = 0
    details = []
    for row in truth_rows:
        index = row.get('prediction_index')
        predicted = predictions[index] if isinstance(index, int) and 0 <= index < len(predictions) else {}
        wrong = []
        for field in FIELDS:
            if row.get(field) is None:
                continue
            field_eligible[field] += 1
            same = normalized(predicted.get(field), field) == normalized(row.get(field), field)
            field_correct[field] += bool(same)
            if not same:
                wrong.append(field)
        row_ok = not wrong
        row_correct += row_ok
        flagged = _flagged(predicted)
        flagged_rows += flagged
        if wrong and not flagged:
            unflagged_errors += 1
        if not wrong and flagged:
            false_alarms += 1
        details.append({'row_id': row.get('row_id'), 'prediction_index': index,
                        'wrong_fields': wrong, 'flagged': flagged,
                        'expected': {f: row.get(f) for f in FIELDS},
                        'actual': {f: predicted.get(f) for f in FIELDS},
                        'reasons': list(predicted.get('_review_reasons') or [])})
    total = len(truth_rows)
    return {'image_path': annotation.get('image_path'),
            'image_sha256': annotation.get('image_sha256'),
            'rows': total,
            'field_correct': field_correct, 'field_eligible': field_eligible,
            'field_accuracy': {f: (field_correct[f] / field_eligible[f] if field_eligible[f] else None)
                               for f in FIELDS},
            'field_errors': {f: field_eligible[f] - field_correct[f] for f in FIELDS},
            'row_correct': row_correct,
            'row_accuracy': (row_correct / total) if total else None,
            'flagged_rows': flagged_rows,
            'flag_rate': (flagged_rows / total) if total else None,
            'unflagged_errors': unflagged_errors,
            'false_alarms': false_alarms,
            'error_recall': ((total - row_correct) and
                             ((total - row_correct) - unflagged_errors) / (total - row_correct)
                             or None),
            'details': details}


def _sum(images, key):
    return sum(image.get(key, 0) for image in images)


def _row_outcomes(baseline_images, assisted_images):
    """按 row_id 对齐两次运行，算出「修好 / 改坏 / 误提示 / 错误被提示」。"""
    base = {}
    for image in baseline_images:
        for row in image.get('details', []):
            base[row['row_id']] = row
    fixed, broken, false_alarms = [], [], []
    flagged_errors, unflagged_errors = [], []
    for image in assisted_images:
        for row in image.get('details', []):
            before = base.get(row['row_id'])
            if before is None:
                continue
            was_ok = not before['wrong_fields']
            now_ok = not row['wrong_fields']
            if was_ok and not now_ok:
                broken.append(row['row_id'])
            elif not was_ok and now_ok:
                fixed.append(row['row_id'])
            if row['flagged']:
                if was_ok and now_ok:
                    false_alarms.append(row['row_id'])
                elif not now_ok:
                    flagged_errors.append(row['row_id'])
            elif not now_ok:
                unflagged_errors.append(row['row_id'])
    return {'fixed_rows': fixed, 'broken_rows': broken, 'false_alarm_rows': false_alarms,
            'flagged_error_rows': flagged_errors, 'unflagged_error_rows': unflagged_errors}


def compare(baseline_images, assisted_images, *, cost=None, meta=None):
    """对比两次运行（基线 A 组 vs 手写辅助 E 组）的验收指标。"""
    rows = _sum(assisted_images, 'rows') or 1
    base_rows = _sum(baseline_images, 'rows') or 1
    report = {'schema_version': 1, 'meta': dict(meta or {})}
    for name, images, total in (('baseline', baseline_images, base_rows),
                                ('assisted', assisted_images, rows)):
        correct = {f: 0 for f in FIELDS}
        eligible = {f: 0 for f in FIELDS}
        for image in images:
            for field in FIELDS:
                correct[field] += image['field_correct'][field]
                eligible[field] += image['field_eligible'][field]
        errors = {f: eligible[f] - correct[f] for f in FIELDS}
        total_errors = sum(errors.values())
        row_correct = _sum(images, 'row_correct')
        flagged = _sum(images, 'flagged_rows')
        unflagged = _sum(images, 'unflagged_errors')
        false_alarms = _sum(images, 'false_alarms')
        report[name] = {
            'rows': total,
            'field_accuracy': {f: (correct[f] / eligible[f] if eligible[f] else None) for f in FIELDS},
            'field_errors': errors,
            'field_error_total': total_errors,
            'row_accuracy': row_correct / total,
            'row_error_total': total - row_correct,
            'flagged_rows': flagged,
            'flag_rate': flagged / total,
            'unflagged_errors': unflagged,
            'unflagged_error_rate': unflagged / total,
            'false_alarms': false_alarms,
            'false_alarm_rate': false_alarms / total,
            'error_recall': ((total - row_correct - unflagged) / (total - row_correct)
                             if total - row_correct else None),
            'images': images,
        }
    base_errors = report['baseline']['field_error_total']
    new_errors = report['assisted']['field_error_total']
    reduction = ((base_errors - new_errors) / base_errors) if base_errors else None
    outcomes = _row_outcomes(baseline_images, assisted_images)
    report['outcomes'] = outcomes
    report['gates'] = {
        'key_error_reduction': reduction,
        'key_error_reduction_target': 0.20,
        'key_error_reduction_pass': bool(reduction is not None and reduction >= 0.20),
        'no_new_unflagged_errors':
            report['assisted']['unflagged_errors'] <= report['baseline']['unflagged_errors'],
        'recall_not_lower':
            report['assisted']['row_accuracy'] >= report['baseline']['row_accuracy'],
        'no_broken_rows': not outcomes['broken_rows'],
        'baseline_field_error_total': base_errors,
        'assisted_field_error_total': new_errors,
    }
    report['cost'] = dict(cost or {})
    report['scope'] = ('仅覆盖已人工确认的冻结样本；不代表全部单据的普遍准确率；'
                        '手写辅助通道的改动一律同时标记待核对，不做静默改写。')
    return report


def render_markdown(report):
    """把验收报告渲染成可直接贴进文档的 Markdown。"""
    base, assisted = report['baseline'], report['assisted']
    lines = ['# 手写辅助（阶段 3/4）冻结验收报告', '']
    meta = report.get('meta') or {}
    if meta:
        lines += ['- 冻结集：%s' % meta.get('dataset', '（未标注）'),
                  '- 样本：%d 张图 / %d 行明细' % (meta.get('images', 0), base['rows']),
                  '- 生成时间：%s' % meta.get('generated_at', ''), '']
    lines += ['## 一、关键字段准确率', '',
              '| 字段 | 基线正确率 | 手写辅助正确率 | 基线错误数 | 手写辅助错误数 | 错误下降 |',
              '| --- | --- | --- | --- | --- | --- |']
    for field in FIELDS:
        b = base['field_accuracy'][field]
        a = assisted['field_accuracy'][field]
        be = base['field_errors'][field]
        ae = assisted['field_errors'][field]
        drop = ('%.0f%%' % ((be - ae) / be * 100)) if be else '—'
        lines.append('| %s | %s | %s | %d | %d | %s |' % (
            field, _pct(b), _pct(a), be, ae, drop))
    lines += ['', '**关键字段错误总数：%d → %d（相对下降 %s）**' % (
        base['field_error_total'], assisted['field_error_total'],
        _pct(report['gates']['key_error_reduction'])), '']
    lines += ['## 二、整行与核对负担', '',
              '| 指标 | 基线 | 手写辅助 |', '| --- | --- | --- |',
              '| 整行完全正确率 | %s | %s |' % (_pct(base['row_accuracy']), _pct(assisted['row_accuracy'])),
              '| 待核对比例 | %s | %s |' % (_pct(base['flag_rate']), _pct(assisted['flag_rate'])),
              '| 未提示错误 | %d | %d |' % (base['unflagged_errors'], assisted['unflagged_errors']),
              '| 错误被提示比例 | %s | %s |' % (_pct(base['error_recall']), _pct(assisted['error_recall'])),
              '']
    outcomes = report.get('outcomes') or {}
    lines += ['对照基线逐行结果：', '',
              '- 修好的整行：%d 条%s' % (len(outcomes.get('fixed_rows', [])),
                                       ('（%s）' % '、'.join(outcomes['fixed_rows'])) if outcomes.get('fixed_rows') else ''),
              '- 改坏的整行：%d 条%s' % (len(outcomes.get('broken_rows', [])),
                                       ('（%s）' % '、'.join(outcomes['broken_rows'])) if outcomes.get('broken_rows') else ''),
              '- 本来就对、却仍标待核对（误提示）：%d 条%s' % (
                  len(outcomes.get('false_alarm_rows', [])),
                  ('（%s）' % '、'.join(outcomes['false_alarm_rows'])) if outcomes.get('false_alarm_rows') else ''),
              '- 仍有错但已标待核对：%d 条' % len(outcomes.get('flagged_error_rows', [])),
              '- 仍有错且没提示：%d 条' % len(outcomes.get('unflagged_error_rows', [])), '']
    cost = report.get('cost') or {}
    if cost:
        lines += ['## 三、性能成本', '']
        for key, value in cost.items():
            lines.append('- %s：%s' % (key, value))
        lines.append('')
    lines += ['## 四、发布门槛判定', '']
    gates = report['gates']
    lines += ['| 门槛 | 判定 |', '| --- | --- |',
              '| 关键字段错误相对下降 ≥ 20%% | %s（实测 %s） |' % (
                  '通过' if gates['key_error_reduction_pass'] else '未通过',
                  _pct(gates['key_error_reduction'])),
              '| 不新增未提示错误 | %s |' % ('通过' if gates['no_new_unflagged_errors'] else '未通过'),
              '| 明细召回/精确率不下降 | %s |' % ('通过' if gates['recall_not_lower'] else '未通过'),
              '', '> %s' % report.get('scope', '')]
    return '\n'.join(lines)


def _pct(value):
    return '—' if value is None else '%.1f%%' % (value * 100)
