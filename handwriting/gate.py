"""手写辅助通道的裁决门控。

《方案》第八节末段：「现有合计裁决和品类边界修正与上述规则存在差异。
实施时对手写辅助通道增加门控，保存裁决前候选，避免旧逻辑先消除疑点；
原流程保持可回退。」

现网两个老逻辑都会**先消除疑点**：
  · `cross_check`：品类合计对上就把 `_uncertain`/`_conflict` 清掉 ——
    但合计对上不代表这一行读对了；
  · `fix_category_boundaries`：按合计把整段行改归到下一个品类。

门控做两件事：
  1. 动手前保存**裁决前候选**（`_pre_decision`），随时可回看；
  2. 证据链已经给出同行证据的地方，不让老逻辑改写（改回原值并记说明）。

原流程（关闭手写辅助）完全不经过这里，行为不变。
"""
from __future__ import annotations

FIELDS = ('category', 'customer_code', 'weight', 'unit', 'remark')


def snapshot(records):
    """保存裁决前的关键字段。"""
    return [{field: record.get(field) for field in FIELDS} for record in records]


def attach_snapshot(records, before):
    for record, old in zip(records, before):
        record['_pre_decision'] = dict(old)
    return records


def has_row_evidence(record):
    """这一行有没有拿到「同行证据」（OCR 行内条目）。"""
    evidence = record.get('_evidence') or {}
    return bool(evidence.get('entry_text'))


def guard_cross_check(records, cross_check, blocks, *, enabled=True):
    """包 `cross_check`：合计对上也不许清掉证据链标出的疑点。

    返回 (提示文本, 门控说明列表)。
    """
    if not enabled:
        return cross_check(records, blocks), []
    protected = {}
    for index, record in enumerate(records):
        if record.get('_uncertain') or record.get('_review_reasons'):
            protected[index] = {'_uncertain': record.get('_uncertain'),
                                '_review_reasons': list(record.get('_review_reasons') or []),
                                '_warn': record.get('_warn', '')}
    text = cross_check(records, blocks)
    notes = []
    for index, keep in protected.items():
        record = records[index]
        restored = False
        for key in ('_uncertain', '_review_reasons'):
            if keep[key] and not record.get(key):
                record[key] = keep[key]
                restored = True
        if restored:
            notes.append('第 %d 行的疑点由原图证据链标出，合计核对不能替它消除' % (index + 1))
    return text, notes


def guard_category_boundaries(records, blocks, fixer, *, enabled=True):
    """包 `fix_category_boundaries`：有同行证据的行不许被合计逻辑改归品类。

    返回 (被改条数, 门控说明列表)。
    """
    before = snapshot(records)
    if not enabled:
        changed = fixer(records, blocks)
        attach_snapshot(records, before)
        return changed, []
    changed = fixer(records, blocks)
    notes = []
    for index, record in enumerate(records):
        old = before[index]
        if record.get('category') == old['category']:
            continue
        if has_row_evidence(record):
            record['category'] = old['category']
            notes.append('第 %d 行有原图同行证据，品类归属不被合计推算改写（保留「%s」）'
                         % (index + 1, old['category']))
    attach_snapshot(records, before)
    return changed, notes


def guard_merge_consensus(runs, blocks, merger, *, enabled=True):
    """包 `merge_consensus`：多轮模型结果合并时保留每轮原始候选。

    返回 (合并结果, 说明列表)。合并只在**没有**原图证据时才按老规矩投票，
    有原图证据的字段由证据链裁决。
    """
    before = [snapshot(run) for run in runs]
    result, notes = merger(runs, blocks)
    if enabled:
        for index, run in enumerate(runs):
            for field in FIELDS:
                if run.get(field) != before[index][field]:
                    notes.append('第 %d 轮合并改动了 %s，裁决前候选已保存' % (index + 1, field))
                    break
    return result, notes


class DecisionGate:
    """门控的集合入口。手写辅助通道用它，原流程不用。"""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.notes = []
        self.before = []

    def cross_check(self, records, cross_check, blocks):
        text, notes = guard_cross_check(records, cross_check, blocks, enabled=self.enabled)
        self.notes.extend(notes)
        return text

    def category_boundaries(self, records, blocks, fixer):
        changed, notes = guard_category_boundaries(records, blocks, fixer, enabled=self.enabled)
        self.notes.extend(notes)
        return changed

    def snapshot(self, records):
        self.before = snapshot(records)
        attach_snapshot(records, self.before)
        return self.before
