"""Rebuild offline artifacts without any remote requests or production writes."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from handwriting.storage import write_json, read_json, digest
from handwriting.dataset import prepare, import_visual_drafts, annotation_audit
from handwriting.baseline import collect
from handwriting.reports import annotation_preview, vocabulary_preview
from handwriting.vocabulary import load_vocabulary, annotate_records, Vocabulary


def main():
    output = ROOT / 'data/evaluations/stage012_20261106'
    manifest = prepare(ROOT / 'data/手写素材', output / 'manifest.json', ROOT / 'data/annotations')
    review_path = ROOT / 'data/annotations/visual_review.json'
    if review_path.exists():
        import_visual_drafts(review_path, ROOT / 'data/annotations', ROOT / 'data/手写素材')
        review = read_json(review_path)
        annotation_preview(review, ROOT / 'data/手写素材', output / '标注草稿复核.html')
    baseline_path = output / 'offline_baseline.json'
    if not baseline_path.exists():
        write_json(baseline_path, collect(ROOT, manifest))
    audit = annotation_audit(ROOT / 'data/annotations')
    write_json(output / 'annotation_audit.json', audit)
    example = [{'category': '示例新品类', 'customer_code': '001A', 'weight': '0.5', 'unit': '袋',
                'remark': '示例备注，不是真实业务规则', '_id': i} for i in (1, 2)]
    vocabulary = load_vocabulary(ROOT / 'data/dictionaries/handwriting.json')
    preview = annotate_records(vocabulary, example,
        field_map={key: key for key in ('category', 'customer_code', 'unit', 'remark')})
    write_json(output / '词典空库预览.json', preview)
    vocabulary_preview(preview['records'], preview['suggestions'], preview['warnings'], output / '词典空库预览.html')
    # Explicit synthetic demonstration, never written into the production vocabulary.
    demo = Vocabulary.from_dict({'schema_version': 1, 'revision': 'synthetic-demo-only', 'entries': [
        {'id': 'demo-only', 'field': 'category', 'canonical': '示例标准品类', 'aliases': ['示例新品类'],
         'scope': {'customers': []}, 'source': '仅功能演示，不是真实业务规则',
         'confirmed_at': '2026-11-06', 'enabled': True}]})
    demo_result = annotate_records(demo, example, field_map={key: key for key in ('category', 'customer_code', 'unit', 'remark')})
    vocabulary_preview(demo_result['records'], demo_result['suggestions'], ['仅合成示例，不是真实词典，未接入生产。'], output / '词典候选功能演示.html')
    write_json(output / 'stage_status.json', {
        'stage0': {'inventory': 'done', 'files': manifest['file_count'], 'unique_pixels': manifest['unique_pixels'],
                   'visual_review': '13 images; 40 draft rows in 2 images',
                   'confirmed_annotations': audit['counts'].get('confirmed', 0),
                   'offline_baseline': 'done', 'live_capability': 'pending_explicit_data_transfer_approval',
                   'acceptance': 'pending_confirmed_reference_and_live_test'},
        'stage1': {'library': 'implemented', 'dictionary_entries': len(vocabulary.entries),
                   'policy': 'suggest_only_preserve_records', 'production_enabled': False},
        'stage2': {'enhancement': 'deferred_by_user_plan_section6', 'ocr_experiment_runner': 'implemented',
                   'real_results': 'not_run_pending_approval', 'promote': False},
        'budget': {'max_live_requests': 30, 'max_run_seconds': 900, 'request_timeout_seconds': 45,
                   'retry': 0, 'model_output_tokens_per_call': 4096,
                   'account_prices': 'not_verified', 'currency_budget_enforced': False,
                   'cost_formula': 'N_accurate * P_accurate + N_handwriting * P_handwriting + model tokens tariff',
                   'business_latency_budget': 'not_yet_confirmed'},
        'real_api_calls_performed': 0,
        'production_code_unchanged': all(digest(ROOT / name) == digest(ROOT / '.setup/手写库阶段012基线_20261106' / name)
             for name in ['ttsb2.py', '打单助手.py', 'app_settings.py', 'build.py', 'customer_profiles.py']
             if (ROOT / '.setup/手写库阶段012基线_20261106' / name).exists())})
    # Rebuilding offline previews must not erase a completed live run's status.
    live_file = output / 'results.json'
    if live_file.exists():
        live = read_json(live_file)
        status = read_json(output / 'stage_status.json')
        status['real_api_calls_performed'] = sum(r.get('network_calls', 0) for r in live)
        status['stage0']['live_capability'] = 'executed_see_live_findings'
        status['stage0']['acceptance'] = 'pending_business_human_confirmation'
        status['stage2']['real_results'] = 'executed_no_accuracy_claim'
        write_json(output / 'stage_status.json', status)
    print('Offline artifacts:', output)
    print('Annotation status:', audit)


if __name__ == '__main__': main()
