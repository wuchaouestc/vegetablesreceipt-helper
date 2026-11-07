"""单据级单位一致性：先在**整张单据**范围内取单位共识，再对读不出单位的行回填。

为什么需要这一步
----------------
实测（`data/evaluations/paddle_fields/rec_评测结果.json`）给出的结论很硬：

  · 腾讯云 OCR 在单位区域上把「斤」读成「个」51 次、读对斤仅 6 次；
  · Paddle rec 更糟——76% 的单位被数字吞掉（`1365:1.5斤` → `1365:15`）；
  · **直接拿通道自己读出的单位取众数去回填，命中 0%**——因为众数被同一个
    错读方向带偏了（实测真值众数是「斤」，通道自众数却是「个」）；
  · 换成「本单真值众数」回填，命中 100%，总命中上限从 29% 升到 76%。

也就是说：回填能不能救命，全看**共识单位取对没有**。而共识不是靠某一路读数的
多数派说了算，得先把「字形混淆」这一层拆掉——这正是人工确认过的
「个 ← 斤」这条字典记录该发挥的作用。

本模块的做法
------------
1. **投票**：把一张单据里所有行的单位候选按来源加权统计。
   原图行内文字（`ocr_row`）与局部复核（`review`）是直接看到纸面的证据，权重高；
   大模型读数（`model`）与字段识别（`paddle_mobile_rec`）次之；
   词典候选（`vocabulary`）权重为 0——**词典只提示，不参与投票**（铁律 4）。

2. **混淆折叠**：词典里人工确认过的「字形混淆对」（`个 → 斤`），
   别名的票折算进规范值，别名本身不再单独参与竞争。
   这不是「斤≡个」的等价替换：它只影响**本单共识**，
   而共识只用来填「读不出单位」的行。

3. **回填**：只回填**单位为空**的行，绝不覆盖已经读出来的值。
   回填值必须同时标待核对（`_uncertain` + `_review_reasons`），这是铁律第 5 条。

4. **字形冲突只留线索**：读成别名（本单共识是「斤」而本行读成「个」）的行，
   按铁律第 1 条——单位字形不作裁决依据——**只记观察线索**，不改值、不加疑点。

护栏
----
  · 不覆盖任何已有单位值（铁律 1）；
  · 共识不足（支持行数不够）、或共识没有任何硬证据时**不回填**，退回现网兜底；
  · 折叠必须有本单内的直接读数佐证（规范值的直接读数行数 ≥ 别名的），
    否则「本单真的在数『个』」就会被硬掰成「斤」；
  · 没有人工确认过的字形混淆映射时**不回填**；
  · 每一次回填都留下中文原因，用户在打印前看得见。
"""
from __future__ import annotations

from .geometry import normalize_text

# 来源权重：直接看到纸面的证据权重高，大模型/字段识别次之。
# 词典候选权重为 0——词典只提示，不参与投票（铁律 4）。
SOURCE_WEIGHTS = {
    'ocr_row': 3.0,            # 原图行内文字
    'review': 3.0,             # 局部复核（裁图重读）
    'model': 1.0,              # 大模型读数
    'paddle_mobile_rec': 1.0,  # 字段识别（独立 rec 模型）
    'ocr_heading': 0.0,        # 标题里的单位不算明细单位
    'vocabulary': 0.0,         # 词典只提示，不投票
    # 人工整行确认过的单位：人已经对着原图核过了，权重最高。
    'user_confirmed': 10.0,
}
# 「直接看到纸面」的来源：有这类票才算本单共识有硬证据。
# `user_confirmed` 是人工核对的结果，比机器读数可靠一个量级。
TRUSTED_SOURCES = ('ocr_row', 'review', 'user_confirmed')
# 已知单位表（与 geometry.UNIT_PATTERN 对齐），用来筛掉人工标注里的脏值。
KNOWN_UNITS = frozenset({'公斤', '千克', 'kg', '斤', '个', '只', '袋', '包', '件', '箱', '条',
                         '把', '盒', '支', '瓶', '片', '棵', '枚', '扎', '提', '罐', '卷',
                         '对', '双', '打'})
# 人工标注里的「这行不存在」标记，统计先验时要剔除。
_ROW_GONE_MARKERS = ('没有这行', '删除')


def _norm(value):
    return normalize_text(value)


def collect_unit_votes(records):
    """统计一张单据里所有行的单位候选票。

    返回 {'votes': {单位: {'weight': 权重和, 'rows': 行号集合, 'sources': {来源: 次数},
                            'trusted_rows': 直接看到纸面的行号集合}}}。
    行号集合用于「按行计票」——同一行的多个来源不能当成多行。
    """
    votes: dict[str, dict] = {}
    for index, record in enumerate(records or []):
        if not isinstance(record, dict):
            continue
        evidence = record.get('_evidence')
        evidence = evidence if isinstance(evidence, dict) else {}
        candidates = (evidence.get('candidates') or {}).get('unit') or []
        # 人工整行确认过的行：直接用确认后的值当票，不再看机器候选——
        # 人已经对着原图核过了，这是最硬的证据。
        if record.get('_user_confirmed'):
            confirmed = _norm(record.get('unit'))
            if confirmed in KNOWN_UNITS:
                candidates = [{'value': confirmed, 'source': 'user_confirmed'}]
        seen_here = set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            value = _norm(candidate.get('value'))
            source = str(candidate.get('source') or '')
            if not value or source in seen_here:
                continue
            seen_here.add(source)
            weight = SOURCE_WEIGHTS.get(source, 0.0)
            if weight <= 0:
                continue
            bucket = votes.setdefault(value, {'weight': 0.0, 'rows': set(), 'sources': {},
                                            'trusted_rows': set()})
            bucket['weight'] += weight
            bucket['rows'].add(index)
            bucket['sources'][source] = bucket['sources'].get(source, 0) + 1
            if source in TRUSTED_SOURCES:
                bucket['trusted_rows'].add(index)
    return {'votes': votes}


def load_unit_prior(path, customer=None, min_rows=3):
    """从人工确认过的示例库统计「单位先验」。

    先验是**人工确认**的单位分布（本项目里是「斤」占绝大多数），它是字形混淆
    之外的第二条可靠锚点：文档里一个「斤」都没读到时，仍然知道这批单该用什么单位。

    只统计有效行：单位为空、单位不是已知单位、品类里带「这行不存在 / 删除」
    标记的行（人工标记删掉的行，其单位是噪声）一律剔除。
    客户维度的样本够多（≥ min_rows 行）时优先用客户维度的先验，并标记
    `scoped=True`——只有客户维度的先验才可以授权「把别名折叠成规范值」，
    全局先验只能当弱票，避免别的客户的「个」被本客户的历史带偏。

    返回 {'units': {单位: 次数}, 'scoped': 是否客户维度}。
    """
    result = {'units': {}, 'scoped': False}
    try:
        from .storage import read_json
        data = read_json(path)
    except Exception:  # noqa: BLE001 —— 先验只是加分项，读不到就当没有
        return result
    if not isinstance(data, dict):
        return result
    global_units: dict[str, int] = {}
    scoped_units: dict[str, int] = {}
    for item in data.get('examples') or []:
        if not isinstance(item, dict):
            continue
        values = item.get('values') or {}
        unit = _norm(values.get('unit'))
        if not unit or unit not in KNOWN_UNITS:
            continue
        category = str(values.get('category') or '')
        if any(marker in category for marker in _ROW_GONE_MARKERS):
            continue
        global_units[unit] = global_units.get(unit, 0) + 1
        if customer and item.get('customer') == customer:
            scoped_units[unit] = scoped_units.get(unit, 0) + 1
    if scoped_units and sum(scoped_units.values()) >= min_rows:
        result['units'] = scoped_units
        result['scoped'] = True
        return result
    result['units'] = global_units
    return result


def load_image_unit_prior(path, image_sha256=None, customer=None):
    """**本单**人工确认过的单位——最强的锚点。

    人工确认是「越用越智能」的终点：这张单上人已经核过几行了，剩下的行
    就该按这个单位走。按图片指纹匹配，所以不依赖客户档案有没有填对。

    返回 {'unit': 单位或 None, 'rows': 确认过的行数, 'scoped': True}。
    """
    result = {'unit': None, 'rows': 0, 'scoped': True}
    if not image_sha256:
        return result
    try:
        from .storage import read_json
        data = read_json(path)
    except Exception:  # noqa: BLE001 —— 锚点是加分项，读不到就当没有
        return result
    if not isinstance(data, dict):
        return result
    counts: dict[str, int] = {}
    for item in data.get('examples') or []:
        if not isinstance(item, dict):
            continue
        if item.get('image_sha256') != image_sha256:
            continue
        if customer and item.get('customer') and item.get('customer') != customer:
            continue
        values = item.get('values') or {}
        unit = _norm(values.get('unit'))
        category = str(values.get('category') or '')
        if not unit or unit not in KNOWN_UNITS:
            continue
        if any(marker in category for marker in _ROW_GONE_MARKERS):
            continue
        counts[unit] = counts.get(unit, 0) + 1
    if not counts:
        return result
    unit, count = max(sorted(counts.items()), key=lambda item: item[1])
    result.update({'unit': unit, 'rows': count})
    return result


def _fold(votes, confusion, prior):
    """把字形混淆对的别名票折算进规范值，别名本身不再单独参与共识竞争。

    `confusion` 是 {错读写法: 规范值}（由词典人工确认条目派生）。

    折叠必须站得住脚，三个条件同时成立才折：
      ① 本单里规范值有**直接看到纸面**的读数（`ocr_row`/`review`）——说明这张单
         确实出现过它；
      ② 规范值的**硬证据行数 ≥ 别名的硬证据行数**——否则「本单真的在数『个』」
         会被硬掰成「斤」；
      ③ 或者：客户维度的人工先验明确偏向规范值（≥ 别名先验的 2 倍且 ≥ 2 次）。

    ⚠️ 比较的是**硬证据行数**，不是加权票数。原因：实测里大模型读数是**系统性
    错读**的方向（腾讯 OCR 把斤读成个 51 次、读对斤只有 6 次），用总票数比大小
    等于「谁错得更多谁赢」。硬证据只有原图行内文字和局部复核，方向可靠。
    """
    folded: dict[str, dict] = {}
    for value, bucket in (votes or {}).items():
        folded[value] = {'weight': bucket['weight'], 'rows': set(bucket['rows']),
                         'sources': dict(bucket['sources']), 'folded_from': [],
                         'direct_rows': set(bucket['rows']),
                         'trusted_rows': set(bucket.get('trusted_rows') or set())}
    applied = []
    for alias, canonical in sorted((confusion or {}).items()):
        alias = _norm(alias)
        canonical = _norm(canonical)
        if not alias or not canonical or alias == canonical:
            continue
        source_bucket = folded.get(alias)
        if source_bucket is None:
            continue
        target = folded.setdefault(canonical, {'weight': 0.0, 'rows': set(), 'sources': {},
                                               'folded_from': [], 'direct_rows': set(),
                                               'trusted_rows': set()})
        prior_units = (prior or {}).get('units') or {}
        prior_canonical = prior_units.get(canonical, 0)
        prior_alias = prior_units.get(alias, 0)
        prior_supports = bool((prior or {}).get('scoped')) and prior_canonical >= 2 \
            and prior_canonical >= 2 * prior_alias
        hard_canonical = len(target['trusted_rows'])
        hard_alias = len(source_bucket['trusted_rows'])
        if not prior_supports and (hard_canonical == 0 or hard_canonical < hard_alias):
            # 没有直接看到纸面的证据、或证据还比别名少 → 不折叠，让别名自己参与竞争。
            continue
        target['weight'] += source_bucket['weight']
        target['rows'] |= source_bucket['rows']
        target['trusted_rows'] |= source_bucket['trusted_rows']
        for name, count in source_bucket['sources'].items():
            target['sources'][name] = target['sources'].get(name, 0) + count
        target['folded_from'].append(alias)
        applied.append((alias, canonical))
        folded.pop(alias, None)
    return folded, applied


def decide_consensus(votes, confusion=None, prior=None, *, min_rows=2):
    """取本单的单位共识。

    返回 {'unit': 共识单位或 None, 'detail': {...}}。拿不准就返回 None——
    **宁可不回填，也不要回填一个错的单位**。
    """
    prior = prior or {}
    prior_units = prior.get('units') or {}
    detail = {'votes': {}, 'folded': {}, 'min_rows': min_rows,
              'prior': dict(prior_units), 'prior_scoped': bool(prior.get('scoped'))}
    for value, bucket in (votes or {}).items():
        detail['votes'][value] = {'weight': round(bucket['weight'], 3),
                                  'rows': len(bucket['rows']),
                                  'sources': dict(bucket['sources'])}
    if not votes and not prior_units:
        detail['reason'] = '本单没有任何单位读数，也没有人工确认的先验'
        return {'unit': None, 'detail': detail}
    folded, applied = _fold(votes, confusion, prior)
    for value, bucket in folded.items():
        detail['folded'][value] = {'weight': round(bucket['weight'], 3),
                                   'rows': len(bucket['rows']),
                                   'direct_rows': len(bucket['direct_rows']),
                                   'sources': dict(bucket['sources']),
                                   'folded_from': list(bucket['folded_from'])}
    if applied:
        detail['folded_pairs'] = ['%s→%s' % pair for pair in applied]
    # 先验票：权重低，只在没有更好候选时起作用；且**不计入行数**
    #（先验是历史，不是本单的行）。
    for unit, count in prior_units.items():
        if unit in folded:
            folded[unit]['weight'] += 0.5 * min(int(count), 10)
            folded[unit]['prior_rows'] = int(count)
        else:
            folded[unit] = {'weight': 0.5 * min(int(count), 10), 'rows': set(),
                            'sources': {}, 'folded_from': [], 'direct_rows': set(),
                            'trusted_rows': set(), 'prior_rows': int(count)}
    if not folded:
        detail['reason'] = '本单单位读数都落在字形混淆的别名上，没有规范值可依'
        return {'unit': None, 'detail': detail}
    # 共识候选：得分最高者；平票时先看有没有「直接看到纸面」的票，再看行数。
    best = None
    for value, bucket in sorted(folded.items(), key=lambda item: (-item[1]['weight'], item[0])):
        trusted = sum(count for name, count in bucket['sources'].items()
                      if name in TRUSTED_SOURCES)
        score = (bucket['weight'], trusted, len(bucket['rows']))
        if best is None or score > best[0]:
            best = (score, value, bucket)
    _score, value, bucket = best
    # 「硬证据」= 直接看到纸面的读数行（原图行内文字 / 局部复核）。
    # 大模型读数、字段识别读数都只是**推断**，不能单独支撑一次回填。
    hard_rows = len(bucket.get('trusted_rows') or set())
    prior_rows = int(bucket.get('prior_rows') or 0)
    if len(bucket['rows']) < max(1, int(min_rows)) and prior_rows < max(1, int(min_rows)):
        detail['reason'] = '共识单位「%s」只有 %d 行支持，票数不够' % (value, len(bucket['rows']))
        return {'unit': None, 'detail': detail}
    if not hard_rows and not prior_rows:
        detail['reason'] = ('共识单位「%s」没有直接看到纸面的读数，也没有人工先验，证据不足'
                            % value)
        return {'unit': None, 'detail': detail}
    detail['reason'] = '本单共识单位「%s」（%d 行支持，其中 %d 行直接读到纸面，来源 %s）' % (
        value, len(bucket['rows']), hard_rows,
        '、'.join('%s×%d' % (name, count) for name, count in sorted(bucket['sources'].items())))
    if bucket['folded_from']:
        detail['folded_from'] = list(bucket['folded_from'])
    return {'unit': value, 'detail': detail}


def _own_hard_unit(record):
    """取这一行自己「直接看到纸面」的单位读数（`ocr_row`/`review`）。

    优先级高于单据级共识：这一行原图上就写着「斤」，那它比全单统计更贴近事实。
    """
    evidence = record.get('_evidence')
    evidence = evidence if isinstance(evidence, dict) else {}
    candidates = (evidence.get('candidates') or {}).get('unit') or []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        source = str(candidate.get('source') or '')
        if source not in TRUSTED_SOURCES:
            continue
        value = _norm(candidate.get('value'))
        if value in KNOWN_UNITS:
            return value
    return None


def apply_unit_consensus(records, *, confusion=None, prior=None, image_prior=None,
                         min_rows=2, flag_conflicts=False):
    """按本单共识回填「读不出单位」的行，并给字形冲突的行留对照线索。

    返回 (新记录列表, 说明列表)。原记录对象不被修改。
    """
    notes: list[str] = []
    items = [dict(record) if isinstance(record, dict) else record for record in (records or [])]
    if not items:
        return items, notes
    confusion = {_norm(alias): _norm(canonical) for alias, canonical in (confusion or {}).items()
                 if _norm(alias) and _norm(canonical) and _norm(alias) != _norm(canonical)}
    has_image_prior = bool((image_prior or {}).get('unit'))
    if not confusion and not (prior or {}).get('units') and not has_image_prior:
        # 没有人工确认过的字形混淆映射、也没有人工先验，就不知道这批单的
        # 「个」是不是斤，不该拿单据级共识去改空白行——退回现网兜底。
        notes.append('单据级单位一致性未生效：没有人工确认过的字形混淆映射，也没有人工确认的单位先验')
        return items, notes
    collected = collect_unit_votes(items)
    consensus = decide_consensus(collected['votes'], confusion, prior, min_rows=min_rows)
    unit = consensus['unit']
    detail = consensus['detail']
    if has_image_prior:
        # 人工确认过的本单单位优先级最高：人已经对着原图核过了。
        unit = image_prior['unit']
        detail = dict(detail)
        detail['reason'] = '本单已有 %d 行人工确认的单位「%s」，直接按它对齐' % (
            int(image_prior.get('rows') or 0), unit)
        detail['image_prior'] = dict(image_prior)
    if not unit:
        notes.append('单据级单位一致性未生效：%s' % detail.get('reason', '票数不足'))
        return items, notes
    filled, conflicts = 0, 0
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get('_user_edited') or item.get('_user_confirmed'):
            continue                       # 人工已经定过，不动
        current = _norm(item.get('unit'))
        if not current:
            # 行自身有原图硬证据就用自己的，否则才用单据级共识。
            own = _own_hard_unit(item)
            目标 = own or unit
            依据 = '本行原图读数' if own else '本单单位一致性'
            item['unit'] = 目标
            reasons = list(item.get('_review_reasons') or [])
            reasons.append('单位读不出，已按%s回填为「%s」，请核对' % (依据, 目标))
            item['_review_reasons'] = reasons
            item['_uncertain'] = True
            observations = list(item.get('_observations') or [])
            observations.append('单位回填依据：%s' % detail.get('reason', ''))
            item['_observations'] = observations
            item['_unit_consensus'] = {'unit': 目标, 'source': 'document_consensus',
                                       'basis': 依据, 'detail': detail.get('reason', '')}
            filled += 1
            continue
        # 只在「恰好是字形混淆对」时留线索：读成别名而共识是规范值。
        canonical = confusion.get(current)
        if canonical and _norm(canonical) == unit:
            observations = list(item.get('_observations') or [])
            observations.append('单位字形冲突：本单共识「%s」，本行读作「%s」，请核对原图'
                                % (unit, current))
            item['_observations'] = observations
            if flag_conflicts:
                reasons = list(item.get('_review_reasons') or [])
                reasons.append('本单多数行的单位是「%s」，本行读作「%s」（字形易混），请核对'
                               % (unit, current))
                item['_review_reasons'] = reasons
                item['_uncertain'] = True
            conflicts += 1
    if filled or conflicts:
        notes.append('单据级单位一致性：共识「%s」，回填 %d 行，字形冲突 %d 行（%s）'
                     % (unit, filled, conflicts, detail.get('reason', '')))
    else:
        notes.append('单据级单位一致性：共识「%s」，本单没有需要回填的行（%s）'
                     % (unit, detail.get('reason', '')))
    return items, notes
