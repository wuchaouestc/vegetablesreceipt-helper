"""Explicit bounded live experiments. No production code, settings or cache writes.

Run .venv/Scripts/python.exe 设计和测试/手写阶段012实测.py --live
Existing artifacts are reused by default; use a different --output for a new run.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from handwriting.storage import digest, fingerprint, write_json, read_json
from handwriting.dataset import prepare
from handwriting.experiments import Budget, ExperimentRunner, summarize
from handwriting.providers import tencent_ocr, deepseek_parse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/evaluations/stage012_20261106')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = prepare(ROOT / 'data/手写素材', output / 'manifest.json', ROOT / 'data/annotations')
    code_files = ['ttsb2.py', 'app_settings.py', 'customer_profiles.py', '打单助手.py',
                  '_界面补丁/识别补丁.py', '_界面补丁/结构补丁.py', 'handwriting/providers.py']
    environment = {'python': sys.version, 'platform': sys.platform,
        'code_sha256': {p: digest(ROOT / p) for p in code_files},
        'settings': read_json(ROOT / 'settings.json'),
        'customer_profiles_sha256': digest(ROOT / 'customers.json'),
        'source_images_manifest': manifest['manifest_id'],
        'enhancement': 'deferred_by_user_plan', 'production_modified': False,
        'budget': {'max_requests': 30, 'max_seconds': 900, 'automatic_retry': False,
                   'model_max_tokens': 4096, 'price_status': 'unknown_account_tariff',
                   'currency_ceiling': None, 'note': '固定调用数量上限，不声称精确人民币费用上限'}}
    # Freeze context once. Do not replace the baseline on resume.
    context_file = output / 'context.json'
    if context_file.exists():
        saved = read_json(context_file)
        if saved['code_sha256'] != environment['code_sha256'] or saved['source_images_manifest'] != environment['source_images_manifest'] or saved['settings'] != environment['settings']:
            raise RuntimeError('基线代码/配置/输入变化，请使用新的 --output')
    else:
        write_json(context_file, environment)
    if not args.live:
        print('Prepared offline inventory; --live required for external calls.')
        return
    import ttsb2
    runner = ExperimentRunner(output, Budget(30, 900))
    results_file = output / 'results.json'
    completed = read_json(results_file) if results_file.exists() else []
    existing = {(r['request']['image_sha256'], fingerprint(r['request']['provider'])): r for r in completed}
    ledger_file = output / 'request_ledger.json'
    ledger = read_json(ledger_file) if ledger_file.exists() else []

    def call(image, identity, fn):
        key = digest(image), fingerprint(identity)
        if key in existing:
            return existing[key]
        # 30 includes saved attempts on resume, even failed requests.
        if len(ledger) >= 30:
            raise RuntimeError('累计调用达到 30 次上限')
        attempt = {'number': len(ledger) + 1, 'image_sha256': key[0], 'provider': identity,
                   'status': 'reserved', 'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')}
        ledger.append(attempt)
        write_json(ledger_file, ledger)
        result = runner.call(image, fn, identity, use_cache=False)
        attempt['status'] = result['response']['status']
        write_json(ledger_file, ledger)
        completed.append(result)
        existing[key] = result
        write_json(results_file, completed)
        write_json(output / 'summary.json', summarize(completed))
        print(identity.get('action', identity.get('model')), result['response']['status'],
              result['response'].get('error_code', ''), result['response'].get('elapsed_seconds'), flush=True)
        return result

    items = [e for e in manifest['entries'] if e['status'] == 'valid']
    # OCR A/D groups use the identical original bytes; no oracle or enhancements.
    disabled_actions = set()
    for item in items:
        image = ROOT / 'data/手写素材' / item['path']
        for action in ('GeneralAccurateOCR', 'GeneralHandwritingOCR'):
            if action in disabled_actions:
                continue
            identity = {'kind': 'tencent', 'action': action, 'adapter_version': 1, 'parameters': {}}
            result = call(image, identity, lambda p, timeout, a=action: tencent_ocr(p, action=a, timeout=timeout))
            if result['response']['error_code'].startswith(('business:Unauthorized', 'business:AuthFailure', 'business:ResourceUnavailable', 'http_401', 'http_403')):
                disabled_actions.add(action)
    # Current-model image capability probe on the same real document, separate from OCR comparison.
    image = ROOT / 'data/手写素材' / items[0]['path']
    prompt = ttsb2.build_prompt(None)
    for model in ('deepseek-chat', 'deepseek-reasoner'):
        identity = {'kind': 'model', 'model': model, 'prompt_hash': fingerprint(prompt),
                    'adapter_version': 1, 'max_tokens': 4096, 'purpose': 'current_model_capability'}
        call(image, identity, lambda p, timeout, m=model: deepseek_parse(p, model=m, system_prompt=prompt, timeout=timeout))
    # A second draft-labelled receipt provides a comparable review sample, within the same 30-call cap.
    review = read_json(ROOT / 'data/annotations/visual_review.json')
    for annotated in review['images']:
        if annotated.get('records') and annotated['sha256'] != digest(image):
            second = ROOT / 'data/手写素材' / annotated['filename']
            identity = {'kind': 'model', 'model': 'deepseek-chat', 'prompt_hash': fingerprint(prompt),
                        'adapter_version': 1, 'max_tokens': 4096, 'purpose': 'current_model_capability'}
            call(second, identity, lambda p, timeout: deepseek_parse(p, model='deepseek-chat', system_prompt=prompt, timeout=timeout))
            break
    write_json(output / 'summary.json', summarize(completed))
    print('Finished', len(completed), 'recorded calls; confirmed accuracy is not inferred.', flush=True)


if __name__ == '__main__': main()
