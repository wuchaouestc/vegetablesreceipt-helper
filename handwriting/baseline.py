"""Offline source baseline and explicit limitations. Never calls an API."""
import copy
import hashlib
import platform
from pathlib import Path
from .storage import digest, read_json, write_json, fingerprint


def collect(root, manifest):
    root = Path(root)
    files = ['ttsb2.py', '打单助手.py', 'app_settings.py', 'customer_profiles.py',
             'build.py', 'requirements.txt', '_界面补丁/布局.py', '_界面补丁/识别补丁.py',
             '_界面补丁/结构补丁.py']
    cached = []
    for entry in manifest['entries']:
        source = Path(manifest['root']) / entry['path']
        key = hashlib.md5(source.read_bytes()).hexdigest() + '.json'
        sources = []
        for directory in (root / 'ocr_cache', root / 'dist/打单助手V6/ocr_cache'):
            path = directory / key
            if path.exists():
                payload = read_json(path)
                sources.append({'path': str(path.relative_to(root)), 'sha256': digest(path),
                                'blocks': len(payload.get('blocks', [])),
                                'provenance': 'legacy_cache_no_interface_version_or_request_id'})
        cached.append({'image_sha256': entry['sha256'], 'sources': sources})
    # Reproduce only local decision risks with synthetic records, not a claim about real samples.
    import ttsb2
    record = {'category': '生姜', 'customer_code': '001', 'weight': '2.5', 'unit': '斤', 'remark': ''}
    unrelated = [{'text': '生姜', 'x': 0, 'y': 0}, {'text': '001', 'x': 900, 'y': 300},
                 {'text': '2.5斤', 'x': 900, 'y': 900}]
    support = ttsb2._record_ocr_support(record, unrelated)
    consensus, _ = ttsb2.merge_consensus([[copy.deepcopy(record)], [copy.deepcopy(record)]], [])
    return {'schema_version': 1, 'python': platform.python_version(),
            'code_sha256': {name: digest(root / name) for name in files},
            'settings_snapshot': read_json(root / 'settings.json'),
            'customers_sha256': digest(root / 'customers.json'),
            'manifest_id': manifest['manifest_id'], 'legacy_cache_inventory': cached,
            'offline_risks': {'whole_page_fields_report_support': support,
                              'same_model_consensus_without_ocr': {'confidence': consensus[0].get('_confidence'),
                                                                   'uncertain': consensus[0].get('_uncertain', False)}},
            'network_calls': 0, 'accuracy': None,
            'limitations': ['没有真实模型请求复现，不能据此确定真实样本的主要错误来源。',
                            '旧缓存无接口版本证据，只能辅助排查，不能用作新旧 OCR 公平对照。',
                            '风险用例为合成数据，证明本地判据边界，不证明某张业务单据实际错在此处。']}
