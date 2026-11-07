"""候选追踪与保守裁决。

一条明细的每个关键字段都可能有多个来源的候选值：模型读数、OCR 行内文字、
OCR 标题文字、词典候选、局部复核结果。本模块把这些候选**并列保存**，
再按《方案》第八节的裁决规则给出结论：

  · 能裁决的才改值（且一定同时标成待核对）；
  · 不能裁决的**保留疑点**，不静默改错；
  · 词典只提示不强改；
  · 单位字形（斤/个）在 OCR 上极易混淆，**不作为裁决依据**，只留作对照线索。

本模块不联网、不读图片、不依赖 Tk。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .geometry import Entry, PageLayout, TextBlock, normalize_text

FIELDS = ('category', 'customer_code', 'weight', 'unit')

# 字段 → 候选来源的中文说明，用于界面提示。
FIELD_LABELS = {'category': '品类', 'customer_code': '编码', 'weight': '数量', 'unit': '单位'}


def _numeric(value):
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() else None


def _digits(value):
    return ''.join(ch for ch in str(value or '') if ch.isdigit())


def _same_number(left, right):
    a, b = _numeric(left), _numeric(right)
    return a is not None and b is not None and a == b


@dataclass
class Candidate:
    """一个字段的一个候选值。"""
    field: str
    value: str
    source: str                      # model / ocr_row / ocr_heading / vocabulary / review
    confidence: float | None = None
    evidence: str = ''               # 原文片段
    bbox: tuple | None = None
    detail: dict = field(default_factory=dict)

    def as_dict(self):
        return {'field': self.field, 'value': self.value, 'source': self.source,
                'confidence': self.confidence, 'evidence': self.evidence,
                'bbox': list(self.bbox) if self.bbox else None, 'detail': dict(self.detail)}


@dataclass
class RowEvidence:
    """一条明细的完整证据链。"""
    row_id: str
    record_index: int
    matched: bool = False
    entry: Entry | None = None
    heading: TextBlock | None = None
    heading_score: float = 0.0
    bbox: tuple | None = None
    column: int | None = None
    candidates: dict = field(default_factory=dict)     # 字段 → [Candidate]
    conflicts: list = field(default_factory=list)      # 字段级冲突
    decisions: list = field(default_factory=list)      # 裁决动作
    reasons: list = field(default_factory=list)        # 中文核对原因
    observations: list = field(default_factory=list)   # 只提示、不标待核对

    def add(self, candidate):
        self.candidates.setdefault(candidate.field, []).append(candidate)

    def as_dict(self):
        return {'row_id': self.row_id, 'record_index': self.record_index, 'matched': self.matched,
                'bbox': list(self.bbox) if self.bbox else None, 'column': self.column,
                'entry': self.entry.as_dict() if self.entry else None,
                'heading': self.heading.as_dict() if self.heading else None,
                'heading_score': round(self.heading_score, 4),
                'candidates': {k: [c.as_dict() for c in v] for k, v in self.candidates.items()},
                'conflicts': list(self.conflicts), 'decisions': list(self.decisions),
                'reasons': list(self.reasons), 'observations': list(self.observations)}


@dataclass
class PageEvidence:
    """一页（一张图片）的证据汇总。"""
    rows: list = field(default_factory=list)
    unmatched_entries: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def as_dict(self):
        return {'rows': [r.as_dict() for r in self.rows],
                'unmatched_entries': [e.as_dict() for e in self.unmatched_entries],
                'notes': list(self.notes)}


# --------------------------------------------------------------------- 配对
def _pair_score(record, entry, heading_text):
    code = normalize_text(record.get('customer_code'))
    ecode = normalize_text(entry.code)
    score = 0.0
    if code and ecode:
        if code == ecode:
            score += 6
        elif code in ecode or ecode in code:
            score += 3                       # 截断/多位：模型常见丢首位或末位
        else:
            score -= 5
    if _same_number(record.get('weight'), entry.quantity):
        score += 4
    elif entry.quantity is not None:
        score -= 1
    eunit = normalize_text(entry.unit)
    if eunit and normalize_text(record.get('unit')) == eunit:
        score += 1
    cat = normalize_text(record.get('category'))
    if cat and heading_text:
        score += 5 if heading_text == cat else -2
    return score


def match_records(records, layout, min_score=3.0):
    """模型明细与 OCR 条目一对一配对。

    只用**内容**（编码/数量/单位/标题）配对，不按金额相同跨行匹配；
    配对结果按分数从高到低贪心占用，同一条目只配给一条明细。
    """
    entries = [(row_index, entry) for row_index, row in enumerate(layout.rows)
               for entry in row.get('entries', [])]
    pairs = []
    for record_index, record in enumerate(records):
        for entry_index, (row_index, entry) in enumerate(entries):
            heading = entry.heading if entry.heading is not None else layout.rows[row_index].get('heading')
            heading_text = normalize_text(heading.text) if heading is not None else ''
            score = _pair_score(record, entry, heading_text)
            if score >= min_score:
                pairs.append((score, record_index, entry_index))
    pairs.sort(key=lambda item: (-item[0], item[1], item[2]))
    used_records, used_entries, assignment = set(), set(), {}
    for score, record_index, entry_index in pairs:
        if record_index in used_records or entry_index in used_entries:
            continue
        used_records.add(record_index)
        used_entries.add(entry_index)
        assignment[record_index] = (entry_index, score)
    return assignment, entries, used_entries


# --------------------------------------------------------------------- 裁决
def _decide_code(model_value, entry):
    """编码：截断型不一致可以裁决（行内文字是同一位置的直接证据）。"""
    left, right = normalize_text(model_value), normalize_text(entry.code)
    if not left or not right or left == right:
        return None
    if left in right or right in left:
        return ('replace', right, '编码两路不一致，已按原图行内文字修正')
    return ('keep', left, '编码两路不一致，两路都读不准，需人工核对')


def _decide_weight(model_value, entry):
    """数量：数字本身不同才算行内文字更可信；只是小数点位置不同则不裁决。"""
    if entry.quantity is None:
        if entry.ambiguous == 'range':
            return ('keep', model_value, '数量疑似区间写法，无法确定具体数值')
        return None
    if _same_number(model_value, entry.quantity):
        return None
    if _digits(model_value) != _digits(entry.quantity):
        return ('replace', entry.quantity, '数量两路不一致，已按原图行内文字修正')
    return ('keep', model_value, '数量小数点位置两路不一致，需人工核对')


def _decide_category(model_value, heading, min_heading_confidence):
    """品类：标题文字与模型读数不一致时，只有高置信标题才足以改值。"""
    if heading is None:
        return None
    heading_text = normalize_text(heading.text)
    model_text = normalize_text(model_value)
    if not heading_text or heading_text == model_text:
        return None
    confidence = heading.confidence
    if confidence is not None and confidence >= min_heading_confidence:
        return ('replace', heading.text, '品类两路不一致，已按原图标题文字修正')
    return ('keep', model_value, '品类两路不一致（两路都读不准），需人工核对标题')


def build_evidence(records, layout, *, vocabulary=None, customer=None,
                   min_heading_confidence=90.0, row_id_key='row_id'):
    """给每条明细建立候选追踪与裁决结果。

    返回 PageEvidence；调用方决定要不要把裁决结果写回明细。
    """
    page = PageEvidence()
    assignment, entries, used_entries = match_records(records, layout)
    for record_index, record in enumerate(records):
        row_id = str(record.get(row_id_key) or ('row-%04d' % (record_index + 1)))
        evidence = RowEvidence(row_id=row_id, record_index=record_index)
        matched = assignment.get(record_index)
        for field_name in FIELDS:
            value = record.get(field_name)
            if value not in (None, ''):
                evidence.add(Candidate(field=field_name, value=str(value), source='model',
                                       evidence=str(value)))
        # 备注（开放文本）：只保留来源与原文，不进裁决、不判对错。
        remark_value = record.get('remark')
        if remark_value not in (None, ''):
            evidence.add(Candidate(field='remark', value=str(remark_value), source='model',
                                   evidence=str(remark_value)))
        if matched is None:
            evidence.reasons.append('缺少同行证据：原图这一行没找到可对应的文字')
            page.rows.append(evidence)
            continue
        entry_index, _score = matched
        row_index, entry = entries[entry_index]
        row = layout.rows[row_index]
        evidence.matched = True
        evidence.entry = entry
        evidence.bbox = entry.bbox
        evidence.column = row.get('column')
        evidence.heading = entry.heading if entry.heading is not None else row.get('heading')
        evidence.heading_score = entry.heading_score or row.get('heading_score', 0.0)
        evidence.add(Candidate(field='customer_code', value=entry.code, source='ocr_row',
                               confidence=entry.confidence, evidence=entry.text, bbox=entry.bbox))
        if entry.quantity is not None:
            evidence.add(Candidate(field='weight', value=entry.quantity, source='ocr_row',
                                   confidence=entry.confidence, evidence=entry.text, bbox=entry.bbox))
        if entry.unit:
            evidence.add(Candidate(field='unit', value=entry.unit, source='ocr_row',
                                   confidence=entry.confidence, evidence=entry.text, bbox=entry.bbox))
        if evidence.heading is not None:
            evidence.add(Candidate(field='category', value=evidence.heading.text, source='ocr_heading',
                                   confidence=evidence.heading.confidence, evidence=evidence.heading.text,
                                   bbox=evidence.heading.bbox))
        if vocabulary is not None:
            from .vocabulary import preview_candidates
            for field_name in ('category', 'customer_code', 'unit', 'remark'):
                observed = record.get(field_name)
                if not observed:
                    continue
                for item in preview_candidates(vocabulary, field_name, str(observed), customer):
                    evidence.add(Candidate(field=field_name, value=item['canonical'], source='vocabulary',
                                           evidence=item.get('reason', ''),
                                           detail={'id': item['id'], 'revision': vocabulary.revision}))
        decisions = []
        for field_name, decider in (('customer_code', lambda: _decide_code(record.get('customer_code'), entry)),
                                    ('weight', lambda: _decide_weight(record.get('weight'), entry)),
                                    ('category', lambda: _decide_category(record.get('category'),
                                                                          evidence.heading,
                                                                          min_heading_confidence))):
            outcome = decider()
            if outcome is None:
                continue
            action, value, reason = outcome
            decisions.append({'field': field_name, 'action': action, 'value': value, 'reason': reason})
            evidence.reasons.append(reason)
            evidence.conflicts.append({'field': field_name, 'reason': reason})
        # 单位：OCR 字形易混（斤/个），只留线索，不裁决、不单独标疑点。
        ocr_unit = normalize_text(entry.unit)
        model_unit = normalize_text(record.get('unit'))
        if ocr_unit and model_unit and ocr_unit != model_unit:
            evidence.observations.append('单位字形两路不一致（斤/个等易混字），不作裁决依据')
        elif ocr_unit and not model_unit:
            evidence.observations.append('模型没读出单位，原图行内文字是「%s」，请核对' % entry.unit)
        elif not ocr_unit and not model_unit:
            evidence.observations.append('单位两路都没读出，将按现网规则兜底，请核对')
        # 词典外值只提示核对，不直接判错。
        # 词典还没建立（一条都没有）时不提「词典外」——否则每行都会被打上疑点，
        # 那是噪声不是提示。词典有内容之后，「词典外」才真正代表「这个值没人确认过」。
        if vocabulary is not None and vocabulary.entries:
            from .vocabulary import preview_candidates
            for field_name in ('category', 'customer_code'):
                observed = record.get(field_name)
                if not observed:
                    continue
                if not preview_candidates(vocabulary, field_name, str(observed), customer):
                    evidence.reasons.append('词典外%s：%s' % (FIELD_LABELS.get(field_name, field_name), observed))
        evidence.decisions = decisions
        page.rows.append(evidence)
    page.unmatched_entries = [entry for index, (_row, entry) in enumerate(entries)
                               if index not in used_entries]
    if page.unmatched_entries:
        page.notes.append('原图有 %d 处文字没对上任何明细，可能是漏行或不是明细，请核对原图'
                          % len(page.unmatched_entries))
    return page


def apply_evidence(records, page, *, row_id_key='row_id'):
    """把裁决结果写回明细（返回新列表，不改原对象）。

    规则：
      · 裁决动作是 replace 的字段才改值；
      · 只要有冲突或缺证据，就标 _uncertain 并写明中文原因；
      · 原记录已有人工确认/手动修改标记时**不覆盖**。
    """
    updated = []
    for record in records:
        item = dict(record)
        evidence = next((r for r in page.rows if r.record_index == len(updated)), None)
        if evidence is not None:
            if not (item.get('_user_confirmed') or item.get('_user_edited')):
                for decision in evidence.decisions:
                    if decision['action'] == 'replace':
                        item[decision['field']] = decision['value']
            reasons = list(evidence.reasons)
            if reasons:
                item['_uncertain'] = True
                item['_review_reasons'] = reasons
            if evidence.observations:
                item['_observations'] = list(evidence.observations)
            item['_evidence'] = {
                'row_id': evidence.row_id, 'matched': evidence.matched,
                'bbox': list(evidence.bbox) if evidence.bbox else None,
                'heading_bbox': list(evidence.heading.bbox) if evidence.heading else None,
                'heading_text': evidence.heading.text if evidence.heading else None,
                'entry_text': evidence.entry.text if evidence.entry else None,
                'candidates': {k: [c.as_dict() for c in v] for k, v in evidence.candidates.items()},
                'decisions': list(evidence.decisions),
            }
        updated.append(item)
    return updated
