"""阶段 0—4 的实验/辅助入口。

两条通道，互不干扰：
  · `recognize()`：阶段 0—2 的实验通道，返回原始结果，不做自动纠正。
  · `assisted_recognize()`：阶段 3—4 的**手写辅助**通道，
    完整框 → 行列关联 → 候选追踪 → 保守裁决 →（可选）局部复核。

原流程（现网 `ttsb2.recognize_parallel`）不经过本模块，行为完全不变。
"""
from dataclasses import dataclass, field
import copy
from pathlib import Path
import uuid

from .budget import UnifiedBudget
from .evidence import apply_evidence, build_evidence
from .geometry import build_layout, normalize_blocks
from .preprocess import original_image
from .experiments import ExperimentRunner, Budget
from .storage import data_root, digest, fingerprint
from .diagnostics import trace, trace_input


@dataclass(frozen=True)
class RecognitionRequest:
    image_path: str
    doc_type: str = "handwritten"
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    customer: str | None = None
    action: str = "GeneralAccurateOCR"
    model: str | None = None
    enhancement: bool = False
    vocabulary_path: str | None = None
    prompt: str = ""


@dataclass
class RecognitionResult:
    task_id: str
    status: str
    records: list = field(default_factory=list)
    blocks: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    suggestions: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    mode: str = "legacy"
    budget: dict = field(default_factory=dict)
    regions: list = field(default_factory=list)

    def as_legacy_tuple(self):
        return copy.deepcopy((self.records, self.blocks, self.notes))


def recognize(request, *, budget=None, output_dir=None, ocr_provider=None, model_provider=None):
    """Raw experimental result. No automatic correction, consensus or print filtering.

    model=None means OCR diagnostics only, not a completed structured recognition.
    Production UI never calls this function in stages 0-2.
    """
    from .providers import tencent_ocr, deepseek_parse
    from .vocabulary import load_vocabulary, annotate_records
    image = original_image(request.image_path, enhancement=request.enhancement)
    if request.doc_type not in ("handwritten", "mixed"):
        raise ValueError("实验通道只接受明确选择的手写/混合单据")
    runner = ExperimentRunner(output_dir or data_root() / "evaluations" / request.task_id,
                              budget or Budget(max_requests=2, max_seconds=120))
    ocr = runner.call(image.path,
        lambda path, timeout: (ocr_provider or tencent_ocr)(path, action=request.action, timeout=timeout),
        {"kind": "tencent", "action": request.action, "adapter_version": 1})
    response = ocr["response"]
    result = RecognitionResult(request.task_id, "ocr_only" if response["status"] != "error" else "error",
                               blocks=copy.deepcopy(response.get("blocks", [])), evidence={"ocr": ocr})
    if response["status"] == "error":
        result.notes.append("OCR 失败：" + response.get("error_code", "unknown"))
    if request.model:
        # Independent image input: no oracle annotations or OCR content injected implicitly.
        model = runner.call(image.path,
            lambda path, timeout: (model_provider or deepseek_parse)(path, model=request.model,
                                        system_prompt=request.prompt, timeout=timeout),
            {"kind": "model", "model": request.model, "prompt_hash": fingerprint(request.prompt),
             "adapter_version": 1})
        result.evidence["model"] = model
        result.records = copy.deepcopy(model["response"].get("records", []))
        result.notes.extend(model["response"].get("notes", []))
        # 只做结构归一化，不改数值；原始结构留在 evidence 里可回溯。
        from .model_records import normalize_records
        result.records, structure_notes = normalize_records(result.records)
        result.notes.extend(structure_notes)
        result.status = "needs_review" if result.records else "error"
        if model["response"]["status"] == "error":
            result.notes.append("模型失败：" + model["response"].get("error_code", "unknown"))
    if request.vocabulary_path:
        vocabulary = load_vocabulary(request.vocabulary_path)
        # API result integration is finalized against the vocabulary module's typed interface.
        annotated = annotate_records(vocabulary, result.records, customer=request.customer,
                                     field_map={key: key for key in ('category', 'customer_code', 'unit', 'remark')})
        result.suggestions = annotated['suggestions']
        result.notes.extend(annotated['warnings'])
    result.notes.append("阶段 0—2 实验结果：未做区域复核，未自动接入打印。")
    return result


# ---------------------------------------------------------------- 阶段 3—4
def _legacy_blocks(blocks):
    """转回现网的 {text,x,y} 约定，兼容下游打印流程。"""
    out = []
    for block in blocks:
        out.append({'text': block.text, 'x': block.bbox[0], 'y': block.bbox[1],
                    'bbox': list(block.bbox),
                    'confidence': block.confidence})
    return out


def _observed_from_layout(layout, limit=20):
    """从版面结构提取有限的观测值，作为词典候选提示词的输入。

    只取「图片上真的出现了」的编码 / 单位 / 品类（图片优先、允许新值），
    不是把整本词典灌给模型。
    """
    observed = {'customer_code': [], 'unit': [], 'category': []}
    for entry in layout.entries():
        if entry.code:
            observed['customer_code'].append(entry.code)
        if entry.unit:
            observed['unit'].append(entry.unit)
    for heading in layout.headings:
        if heading.text:
            observed['category'].append(heading.text)
    result = {}
    for field, values in observed.items():
        unique = list(dict.fromkeys(values))[:limit]
        if unique:
            result[field] = unique
    return result


def 规整记录(records, *, unit_fallback='斤'):
    """把明细整理成与现网 `ttsb2.clean_record` **完全一致**的形态。

    为什么必须对齐：手写通道的输出要直接进打印与「待核对」筛选，
    字段名、单位兜底规则、类型都得跟原流程一模一样，否则会出现
    「手写通道打出来的标签少个单位」这类只在一条路径上出现的问题。

    规则（照抄现网 `clean_record` 对手写单的处理）：
      · 五个字段一律转成去掉首尾空白的字符串；
      · 单位读不出来时兜底成「斤」——手写体上的「斤」被认成怪字很常见，
        不兜底就会印错；这里是对齐现网既有行为，不是新增策略。

    `unit_fallback` 允许传入单据级一致性算出的共识单位（决策 4）：
    一致性回填已经写好的单位不受影响（这里只在**仍然为空**时才兜底）。
    """
    out = []
    for record in records or []:
        item = dict(record)
        for field in ('category', 'customer_code', 'weight', 'unit', 'remark'):
            值 = item.get(field)
            # None 要转成空串，不能变成字符串 'None'。
            item[field] = '' if 值 is None else str(值).strip()
        if not item['unit']:
            item['unit'] = unit_fallback
            # 兜底是「按现网规则」的既有行为，但兜底本身就意味着单位没读出来，
            # 必须让用户看得见、手动核对，不能静默假设是「斤」。
            item.setdefault('_observations', []).append(
                '单位读不出，已按现网规则兜底为「%s」，请核对' % unit_fallback)
        out.append(item)
    return out


def assisted_recognize(request, *, config=None, budget=None, output_dir=None,
                       ocr_provider=None, model_provider=None, review_provider=None,
                       vocabulary=None, legacy_fix=None, legacy_cross=None, quantity_review_provider=None):
    """手写辅助通道：一张图一份证据链一份裁决结果。

    返回 RecognitionResult：records 里带 `_uncertain` / `_review_reasons` / `_evidence`，
    供界面显示具体原因（「数量两路不一致」而不是笼统的低置信度）。
    """
    from .providers import tencent_ocr, deepseek_parse
    from .modes import ModeConfig
    from .review import apply_review_results, plan_regions, plan_quantity_regions, row_version, ReviewRegion, ReviewToken
    from .vocabulary import load_vocabulary

    config = config or ModeConfig()
    if request.doc_type not in ("handwritten", "mixed"):
        raise ValueError("手写辅助通道只接受明确选择的手写/混合单据")
    budget = budget or UnifiedBudget(max_requests=config.budget_requests,
                                     max_seconds=config.budget_seconds)
    output_dir = Path(output_dir or data_root() / "evaluations" / request.task_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    notes = []
    image = original_image(request.image_path, enhancement=config.enhancement)
    image_version = digest(image.path)
    trace_input(request, config, image_version)
    from .review_actions import load_reviewed_result
    reviewed = load_reviewed_result(data_root(), image_version, request.customer)
    if reviewed is not None:
        trace(request, 'human_review_replay', records=reviewed)
        return RecognitionResult(request.task_id, 'ok', records=reviewed, mode='assisted',
                                 notes=['已复用同图的完整人工核对结果，未重新调用识别模型。'],
                                 budget=budget.usage())

    # ① OCR：必须拿到完整多边形，现网只留首点，阶段 3 补齐。
    if not budget.reserve('ocr', quota=('enhancement_variants', None)):
        return RecognitionResult(request.task_id, 'budget_exhausted', notes=[budget.stopped_reason],
                                 mode='assisted', budget=budget.usage())
    ocr_response = (ocr_provider or tencent_ocr)(image.path, action=request.action,
                                                  timeout=min(30, max(5, budget.remaining_seconds)))
    if ocr_response.get('status') == 'error':
        notes.append('OCR 失败：' + str(ocr_response.get('error_code', 'unknown')))
        return RecognitionResult(request.task_id, 'error', notes=notes, mode='assisted',
                                 budget=budget.usage())
    blocks = normalize_blocks(ocr_response.get('blocks', []))
    trace(request, 'ocr', blocks=len(blocks), status=ocr_response.get('status'))
    notes.append('OCR 取到 %d 个文字块（含完整坐标框）' % len(blocks))

    # ② 版面结构提前建好：既给模型候选当输入，也复用给后面的证据链，不多算一遍。
    layout = build_layout(blocks)

    # ③ 词典：加载、警告、危险别名审核、客户范围提示。只提示，不改变任何开关。
    vocabulary_data = vocabulary
    if vocabulary_data is None:
        vocab_path = config.vocabulary_path or request.vocabulary_path
        if vocab_path:
            try:
                vocabulary_data = load_vocabulary(vocab_path)
            except Exception as exc:
                notes.append('词典读取失败，按无词典继续：%r' % (exc,))
    if vocabulary_data is not None:
        from .vocabulary import flag_dangerous_aliases
        for warning in vocabulary_data.warnings:
            notes.append('词典提示：%s' % warning)
        for item in flag_dangerous_aliases(vocabulary_data):
            # 决策 1：这条别名不停用——「个」正是这批单最常见的错读，
            # 客户手写的「斤」很像「个」。它被重新定位成**字形混淆映射**：
            # 供单据级一致性归一使用（见 unit_consensus），不作逐行裁决依据。
            notes.append('词典含跨语义单位别名「%s→%s」，已按字形混淆处理'
                         '（用于单据级单位一致性归一，不改单行读数），请人工审核'
                         % (item['alias'], item['canonical']))
        if request.customer is None and any(e.customers for e in vocabulary_data.entries):
            notes.append('词典有按客户范围的词条，但本次未提供客户上下文，这些词条可能无法命中')

    # ④ 模型：只调用一次，原始明细作为候选之一。调用前把「有限的已审核词典候选」
    #    接进提示词（图片优先、允许新值）；候选随提示词一起进入下游缓存键。
    records = []
    prompt_context = ''
    if request.model:
        if vocabulary_data is not None and vocabulary_data.entries:
            from .vocabulary import build_prompt_context
            prompt_context = build_prompt_context(
                vocabulary_data, _observed_from_layout(layout), customer=request.customer)
            if prompt_context:
                notes.append('已向模型注入 %d 字词典候选上下文' % len(prompt_context))
        model_prompt = request.prompt or ''
        if prompt_context:
            model_prompt = (model_prompt + '\n' if model_prompt else '') + prompt_context
        if not budget.reserve('model'):
            notes.append('预算用尽，停止追加识别：' + budget.stopped_reason)
        else:
            model_response = (model_provider or deepseek_parse)(
                image.path, model=request.model, system_prompt=model_prompt,
                timeout=min(120, max(10, budget.remaining_seconds)))
            if model_response.get('status') == 'error':
                notes.append('模型失败：' + str(model_response.get('error_code', 'unknown')))
            else:
                trace(request, 'model_raw', records=model_response.get('records', []),
                      prompt_hash=fingerprint(model_prompt), vocabulary_revision=getattr(vocabulary_data, 'revision', None))
                records = copy.deepcopy(model_response.get('records', []))
                notes.extend('[%s 模型返回说明] %s' % (request.model, note)
                             for note in model_response.get('notes', []))
                # 模型对手写单据常按标题分组返回；不展开的话证据链会整条失配。
                from .model_records import normalize_records
                records, structure_notes = normalize_records(records)
                trace(request, 'normalized', records=records)
                notes.extend(structure_notes)
    if not records:
        return RecognitionResult(request.task_id, 'needs_review' if blocks else 'error',
                                 records=规整记录(records),
                                 blocks=_legacy_blocks(blocks), notes=notes, mode='assisted',
                                 budget=budget.usage())

    from .category_source import apply_ocr_categories, load_title_annotations
    model_records = records
    # ⑤ 证据链：候选追踪 → 保守裁决（版面结构在②已建好，这里直接复用）。
    page = build_evidence(records, layout, vocabulary=vocabulary_data, customer=request.customer,
                          grouped_categories=True)

    # ⑤½ 字段识别（观察模式，默认关闭）：独立 rec 模型读品类/单位，只加候选与冲突观察。
    if config.field_assist:
        from .paddle_fields import attach_field_candidates, recognize_fields
        try:
            field = recognize_fields(image.path, layout, sidecar_url=config.field_service,
                                     image_sha256=image_version, deadline=config.field_timeout)
            notes.extend(field.get('notes', []))
            if field.get('values'):
                attach_field_candidates(page, field['values'])
        except Exception as exc:  # noqa: BLE001
            notes.append('字段识别降级：%r' % (exc,))

    updated = apply_evidence(records, page)
    trace(request, 'evidence', records=updated, layout_entries=len(layout.entries()),
          layout_headings=len(layout.headings))

    # ④ 旧裁决门控：保存裁决前候选，不让合计逻辑先消除证据链标出的疑点。
    if legacy_fix or legacy_cross:
        from .gate import DecisionGate
        gate = DecisionGate(enabled=True)
        gate.snapshot(updated)
        if legacy_fix:
            gate.category_boundaries(updated, blocks, legacy_fix)
        if legacy_cross:
            gate.cross_check(updated, legacy_cross, blocks)
        notes.extend(gate.notes)
        trace(request, 'legacy_gate', records=updated)

    def review_title(heading, names):
        from .review import crop_region
        from .storage import read_json
        from .vocabulary import build_prompt_context

        observed = list(dict.fromkeys([heading.text, *filter(None, names)]))
        if not config.review_enabled or not request.model or budget.remaining_seconds <= 0:
            return None
        crop = crop_region(image.path, heading.bbox, output_dir / '复核',
                           'title-%s' % heading.index, padding=8)
        if not crop or not budget.reserve('category_review', quota=('review_regions', config.review_regions)):
            return None
        context = (build_prompt_context(vocabulary_data, {'category': observed}, request.customer)
                   if vocabulary_data is not None else '')
        historical = set()
        if request.customer:
            for path in (data_root() / 'annotations').glob('*.json'):
                try:
                    sample = read_json(path)
                except (OSError, ValueError):
                    continue
                if (not isinstance(sample, dict) or sample.get('customer') != request.customer
                        or sample.get('review_status') != 'confirmed'):
                    continue
                for region in sample.get('regions') or []:
                    if (isinstance(region, dict) and region.get('field') == 'category'
                            and region.get('source') == 'human_correction' and not region.get('uncertain')
                            and region.get('normalized_value') in observed):
                        historical.add(region['normalized_value'])
        if historical:
            context += '\n同客户历史标题标注候选：' + '、'.join(sorted(historical)) + '。'
        prompt = (f'{request.prompt or ""}\n{context}\n'
                  '本次只复核裁剪图中的一个手写品类标题。忽略上文的整单输出格式；'
                  '仅返回 {"records":[{"category":"看到的品类"}],"notes":[]}。'
                  '字典是候选数据，以图片为准，看不清则将 category 留空。')
        try:
            response = (model_provider or deepseek_parse)(
                crop, model=request.model, system_prompt=prompt,
                timeout=min(90, budget.remaining_seconds))
            returned = response.get('records') or []
            trace(request, 'category_review_raw', records=returned, heading_index=heading.index,
                  status=response.get('status'))
            if response.get('status') == 'ok' and len(returned) == 1:
                name = returned[0].get('category')
                if isinstance(name, str):
                    return name.strip() or None
        except Exception as exc:
            notes.append('品类局部复核失败，保留已有候选：%s' % type(exc).__name__)
        return None

    category_rows = apply_ocr_categories(
        records, page, model_records=model_records, layout=layout, review_title=review_title,
        annotations=load_title_annotations(data_root(), image_version, request.customer))

    # ⑤ 局部复核：疑点区域裁原图、连同相邻行与标题，追加识别一次。
    regions = []
    if config.review_enabled and review_provider is not None:
        from .review import crop_region
        from .storage import read_json
        examples_path = data_root() / 'learning' / 'confirmed_examples.json'
        try:
            examples = read_json(examples_path).get('examples', [])
        except (OSError, ValueError):
            examples = []
        annotations = {}
        for example in examples:
            if not isinstance(example, dict):
                continue
            if (example.get('image_sha256') != image_version or example.get('customer') != request.customer
                    or not example.get('reviewer') or not example.get('confirmed_at') or not example.get('bbox')):
                continue
            values = example.get('values') or {}
            key = (tuple(example['bbox']), str(values.get('customer_code') or '').strip())
            annotations.setdefault(key, set()).add((str(values.get('weight') or '').strip(),
                                                      str(values.get('unit') or '').strip()))
        for row in page.rows:
            if row.entry is None or not any(c['field'] == 'weight' for c in row.conflicts):
                continue
            known = annotations.get((tuple(row.bbox), str(row.entry.code).strip()), set())
            if len(known) == 1:
                weight, unit = next(iter(known))
                values = {field: value for field, value in (('weight', weight), ('unit', unit))
                          if value}
                token = ReviewToken(request.task_id, image_version, row_version(row))
                apply_review_results(page, [{'tokens': {row.row_id: token},
                                             'values': {row.row_id: values}, 'annotation': True}],
                                     records=updated, confirm_agreement=True, pending_only=True)
        boundary_regions = []
        if quantity_review_provider is not None:
            for row in page.rows:
                if (row.matched and row.bbox and row.entry is not None
                        and any(c['field'] == 'weight' for c in row.conflicts)
                        and any(c.source == 'ocr_invalid_format' and str(c.value).endswith(('4', '2'))
                                for c in row.candidates.get('weight', []))):
                    boundary_regions.append(ReviewRegion(
                        bbox=row.bbox, row_ids=[row.row_id],
                        reasons=['数字与单位边界复核'], ordered_gap=True))
        quantity_regions = plan_quantity_regions(page, layout)
        covered = {rid for region in quantity_regions for rid in region.row_ids}
        covered.update(rid for region in boundary_regions for rid in region.row_ids)
        if any(row.row_id not in covered and any(c['field'] == 'weight' for c in row.conflicts)
               for row in page.rows):
            notes.append('部分数量疑点未能安全定位到局部区域，请人工核对。')
        regions = boundary_regions + quantity_regions + [region for region in plan_regions(
            page, layout, max_regions=config.review_regions)
            if not covered.intersection(region.row_ids)]
        for number, region in enumerate(regions):
            direct_boundary = region in boundary_regions
            if region.ordered_gap and not direct_boundary and not any(
                    row.row_id in region.row_ids and any(c['field'] == 'weight' for c in row.conflicts)
                    for row in page.rows):
                continue
            crop = region.crop_path
            if not crop:
                crop = crop_region(image.path, region.bbox, output_dir / '复核', 'region-%02d' % (number + 1))
                region.crop_path = crop
            if not crop:
                continue
            quota = None if region in quantity_regions else ('review_regions', config.review_regions)
            if not direct_boundary and not budget.reserve('review', quota=quota):
                notes.append('预算用尽，剩余疑点转人工：' + budget.stopped_reason)
                break
            timeout = min(90, max(1, budget.remaining_seconds))
            if region.ordered_gap and quantity_review_provider is not None:
                targets = [row for row in page.rows if row.row_id in region.row_ids]
                nearby = [row for row in page.rows if row.entry is not None
                          and row.column == targets[0].column and row.heading == targets[0].heading
                          and row.bbox[1] <= region.bbox[3] + 140 and row.bbox[3] >= region.bbox[1] - 140
                          and not row.reasons]
                context = {'目标编码顺序': [row.entry.code for row in sorted(targets, key=lambda r: r.bbox[1])],
                           '各行待核对字段': [sorted({c['field'] for c in row.conflicts})
                                            for row in sorted(targets, key=lambda r: r.bbox[1])],
                           '相邻已确认读数': [
                               {field: updated[row.record_index].get(field)
                                for field in ('customer_code', 'weight', 'unit')}
                               for row in sorted(nearby, key=lambda r: r.bbox[1])]}
                categories = {category_rows[row.record_index]['category'] for row in targets
                              if category_rows[row.record_index]['category']}
                history = [
                    example for example in reversed(examples) if isinstance(example, dict)
                    and request.customer and example.get('customer') == request.customer
                    and example.get('reviewer') and example.get('confirmed_at')
                    and example.get('bbox') and example.get('entry_text')
                    and (example.get('values') or {}).get('category') in categories
                ][:8]
                context['同客户历史局部标注'] = [
                    {'原读数': example.get('entry_text'),
                     '确认数量': example['values'].get('weight'),
                     '确认单位': example['values'].get('unit')} for example in history]
                references = []
                for example, label in zip(history, context['同客户历史局部标注']):
                    source = example.get('image_path')
                    if not source:
                        image_hash = str(example.get('image_sha256') or '')
                        if len(image_hash) == 64 and all(c in '0123456789abcdef' for c in image_hash):
                            try:
                                annotation = read_json(data_root() / 'annotations' / 'reviewed_rows'
                                                       / (image_hash + '.json'))
                                source = annotation.get('image_path') if isinstance(annotation, dict) else None
                            except (OSError, ValueError):
                                pass
                    if not source:
                        continue
                    source = Path(source)
                    if source.is_file() and digest(source) == example['image_sha256']:
                        reference = crop_region(source, example['bbox'], output_dir / '复核',
                                                'reference-%d-%d' % (number, len(references)))
                        if reference:
                            references.append(reference)
                            label['参考图序号'] = len(references)
                    if len(references) == 2:
                        break
                if vocabulary_data is not None:
                    from .vocabulary import build_prompt_context
                    context['词典'] = build_prompt_context(vocabulary_data, {
                        'category': sorted(categories),
                        'unit': [c.value for row in targets for c in row.candidates.get('unit', [])],
                    }, request.customer)
                response = None if direct_boundary else quantity_review_provider(crop, timeout=timeout, context=context,
                                                    reference_images=references)
            else:
                response = review_provider(crop, timeout=timeout)
            if response is None:
                response = {'status': 'scheduled_boundary', 'records': []}
            else:
                trace(request, 'review_raw', records=response.get('records', []), region_index=number,
                  status=response.get('status'), model='deepseek-chat')
            values = _review_values(response, region, page, pending_only=True)
            pending_weights = {row.row_id for row in page.rows if row.row_id in region.row_ids
                               and any(c['field'] == 'weight' for c in row.conflicts)}
            if region.ordered_gap and not direct_boundary and any('weight' not in values.get(rid, {}) for rid in pending_weights):
                notes.append('局部数量复核返回的明细或读数不完整，未确认的数量保留待核对。')
            tokens = {rid: ReviewToken(task_id=request.task_id, image_version=image_version,
                                        row_version=row_version(next(
                                            r for r in page.rows if r.row_id == rid)))
                      for rid in region.row_ids}
            apply_review_results(page, [{'region': region, 'tokens': tokens, 'values': values,
                                         'raw_text': str(response.get('raw_text', ''))[:400]}],
                                 records=updated, confirm_agreement=True, pending_only=True)
            if region.ordered_gap and quantity_review_provider is not None:
                from .evidence import _same_number
                for row in targets:
                    weights = row.candidates.get('weight', [])
                    model_weight = model_records[row.record_index].get('weight')
                    reviewed_weight = values.get(row.row_id, {}).get('weight')
                    if ((not direct_boundary and
                         (reviewed_weight is None or _same_number(reviewed_weight, model_weight)))
                            or not any(c.source == 'ocr_invalid_format' for c in weights)
                            or not any(c['field'] == 'weight' for c in row.conflicts)):
                        continue
                    target_crop = crop_region(image.path, row.bbox, output_dir / '复核',
                                              f'boundary-{row.record_index}')
                    if not target_crop:
                        continue
                    if not budget.reserve('quantity_boundary'):
                        notes.append('数字与单位边界复核未执行：' + budget.stopped_reason)
                        break
                    reference_images = []
                    seen = {tuple(row.bbox)}
                    for entry in layout.entries():
                        if entry.code != row.entry.code or tuple(entry.bbox) in seen:
                            continue
                        seen.add(tuple(entry.bbox))
                        ref = crop_region(image.path, entry.bbox, output_dir / '复核',
                                          f'boundary-{row.record_index}-peer-{len(reference_images)}')
                        if ref:
                            reference_images.append(ref)
                    boundary_context = {
                        '数字单位边界复核': True,
                        '目标编码': row.entry.code,
                        '目标原图坐标': row.bbox,
                        '同图同编码参考图数量': len(reference_images),
                        '历史标注参考图起始序号': len(reference_images) + 1,
                        '同客户历史局部标注': [
                            {**label, **({'参考图序号': label['参考图序号'] + len(reference_images)}
                                         if '参考图序号' in label else {})}
                            for label in context['同客户历史局部标注']],
                        '词典': context.get('词典', ''),
                    }
                    boundary = quantity_review_provider(
                        target_crop, timeout=min(90, max(1, budget.remaining_seconds)),
                        context=boundary_context, reference_images=reference_images + references)
                    returned = boundary.get('records') or []
                    trace(request, 'quantity_boundary_raw', records=returned,
                          row_id=row.row_id, status=boundary.get('status'))
                    if (boundary.get('status') != 'ok' or len(returned) != 1
                            or not isinstance(returned[0], dict)
                            or returned[0].get('boundary_clear') is not True
                            or not str(boundary.get('raw_text') or '').strip()
                            or str(returned[0].get('customer_code') or '').strip() != str(row.entry.code).strip()
                            or not _same_number(returned[0].get('weight'), model_weight)
                            or str(returned[0].get('unit') or '').strip() != '斤'):
                        continue
                    token = ReviewToken(request.task_id, image_version, row_version(row))
                    apply_review_results(page, [{
                        'region': region, 'tokens': {row.row_id: token},
                        'values': {row.row_id: {'weight': model_weight}},
                        'raw_text': str(boundary['raw_text'])[:400],
                    }], records=updated, confirm_agreement=True, pending_only=True)
        updated = apply_evidence(records, page)
        trace(request, 'review_applied', records=updated)
        notes.extend(page.notes)

    # ⑤¾ 单据级单位一致性（决策 4）：先在本单内取单位共识，再回填读不出单位的行。
    #     铁律：只回填**空单位**的行，绝不改写已读出的值；回填值必须标待核对。
    unit_fallback = '斤'
    if config.unit_consensus:
        from .unit_consensus import (apply_unit_consensus, load_image_unit_prior,
                                     load_unit_prior)
        from .vocabulary import unit_confusion_pairs
        confusion = {}
        if vocabulary_data is not None:
            try:
                confusion = unit_confusion_pairs(vocabulary_data, customer=request.customer)
            except Exception as exc:  # noqa: BLE001
                notes.append('字形混淆映射读取失败，按无映射继续：%r' % (exc,))
        prior = {}
        image_prior = {}
        examples_path = data_root() / 'learning' / 'confirmed_examples.json'
        try:
            prior = load_unit_prior(examples_path, customer=request.customer)
        except Exception as exc:  # noqa: BLE001
            notes.append('单位先验读取失败，按无先验继续：%r' % (exc,))
        try:
            image_prior = load_image_unit_prior(examples_path,
                                                image_sha256=digest(request.image_path),
                                                customer=request.customer)
        except Exception as exc:  # noqa: BLE001
            notes.append('本单人工确认单位读取失败，按无此锚点继续：%r' % (exc,))
        updated, unit_notes = apply_unit_consensus(
            updated, confusion=confusion, prior=prior, image_prior=image_prior,
            min_rows=config.unit_consensus_min_rows)
        trace(request, 'unit_consensus', records=updated,
              image_prior_rows=image_prior.get('rows', 0),
              prior_scoped=prior.get('scoped', False))
        notes.extend(unit_notes)
        if updated:
            filled = [r for r in updated if r.get('_unit_consensus')]
            if filled:
                unit_fallback = filled[0]['_unit_consensus']['unit']

    for row, category_row in zip(updated, category_rows):
        if row.get('_user_edited') or row.get('_user_confirmed'):
            continue
        row['category'] = category_row['category']
        row['_category_source'] = category_row['_category_source']
        if category_row.get('_uncertain'):
            row['_uncertain'] = True
            reasons = row.setdefault('_review_reasons', [])
            reasons.extend(reason for reason in category_row.get('_review_reasons', []) if reason not in reasons)
    trace(request, 'ocr_category_final', records=updated)
    try:
        from .category_units import refresh_dictionary, apply_category_units
        # Rebuild from the authoritative examples, avoiding stale inferred rules.
        category_dictionary = refresh_dictionary(
            data_root() / 'learning' / 'confirmed_examples.json',
            data_root() / 'dictionaries' / 'category_units.json')
        updated, category_notes = apply_category_units(updated, category_dictionary, request.customer)
        notes.extend(category_notes)
        trace(request, 'category_units', records=updated)
    except Exception as exc:
        notes.append('品类单位关联不可用，保留原结果：%s' % type(exc).__name__)
    status = 'needs_review' if any(r.get('_uncertain') for r in updated) else 'ok'
    trace(request, 'before_output_normalization', records=updated, status=status,
          budget=budget.usage())
    trace(request, 'final', records=规整记录(updated, unit_fallback=unit_fallback))
    return RecognitionResult(request.task_id, status, records=规整记录(updated, unit_fallback=unit_fallback),
                             blocks=_legacy_blocks(blocks),
                             notes=notes, evidence={'page': page.as_dict(),
                                                    'layout': {'rows': len(layout.rows),
                                                               'headings': len(layout.headings),
                                                               'entries': len(layout.entries())},
                                                    'prompt_context_used': bool(prompt_context)},
                             mode='assisted', budget=budget.usage(),
                             regions=[r.as_dict() for r in regions])


def _review_values(response, region, page=None, *, pending_only=False):
    """把复核返回的明细按编码对回区域内的行；对不上就不猜。"""
    rows = {row.row_id: row for row in (page.rows if page is not None else [])}
    records = response.get('records') or []
    if region.ordered_gap:
        from decimal import Decimal, InvalidOperation
        expected = [rows.get(rid) for rid in region.row_ids]
        if (response.get('status') != 'ok' or len(records) != len(expected)
                or any(row is None or row.entry is None or not isinstance(record, dict)
                       or str(record.get('customer_code') or '').strip() != str(row.entry.code).strip()
                       for row, record in zip(expected, records))):
            return {}
        values = {}
        for rid, record in zip(region.row_ids, records):
            pending = {c['field'] for c in rows[rid].conflicts} if pending_only else {'weight'}
            row_values = {}
            if 'unit' in pending and isinstance(record.get('unit'), str) and record['unit'].strip():
                row_values['unit'] = record['unit'].strip()
            try:
                weight = Decimal(str(record.get('weight')))
            except InvalidOperation:
                weight = None
            if 'weight' in pending and weight is not None and weight.is_finite() and weight > 0:
                row_values['weight'] = str(weight)
            if row_values:
                values[rid] = row_values
        return values
    used = set()
    values = {}
    for row_id in region.row_ids:
        row = rows.get(row_id)
        if row is None or row.entry is None:
            continue
        code = str(row.entry.code or '').strip()
        if (sum(1 for rid in region.row_ids if rows.get(rid) is not None
                and rows[rid].entry is not None and str(rows[rid].entry.code or '').strip() == code) > 1
                or sum(1 for record in records if str(record.get('customer_code') or '').strip() == code) > 1):
            continue  # Duplicate codes cannot be associated by response order.
        for index, record in enumerate(records):
            if index in used:
                continue
            if str(record.get('customer_code') or '').strip() == code:
                used.add(index)
                values[row_id] = {field: record.get(field)
                                  for field in ('customer_code', 'weight', 'unit', 'remark')
                                  if record.get(field) not in (None, '')}
                break
    return values
