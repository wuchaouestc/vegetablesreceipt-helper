from dataclasses import asdict
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from handwriting.pipeline import RecognitionRequest, recognize
from handwriting.storage import write_json
from handwriting.vocabulary import Vocabulary, save_vocabulary


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.image = self.root / 'image.png'
        Image.new('RGB', (300, 300), 'white').save(self.image)
        self.records = [{'category': '新品', 'customer_code': '001A', 'weight': '0.5', 'unit': '袋',
                         'remark': '未知备注', '_id': 1},
                        {'category': '新品', 'customer_code': '001A', 'weight': '0.5', 'unit': '袋',
                         'remark': '未知备注', '_id': 2}]

    def tearDown(self): self.temp.cleanup()

    def run_pipeline(self, ocr_status='ok', model_status='ok', dictionary=None, model='fake-model'):
        def ocr(path, **kwargs):
            return {'status': ocr_status, 'error_code': 'test_error' if ocr_status == 'error' else '',
                    'blocks': [{'text': '001A:0.5袋'}] if ocr_status == 'ok' else []}
        def provider(path, **kwargs):
            return {'status': model_status, 'error_code': 'test_error' if model_status == 'error' else '',
                    'records': self.records if model_status == 'ok' else [], 'notes': []}
        return recognize(RecognitionRequest(str(self.image), model=model, vocabulary_path=str(dictionary) if dictionary else None),
                         output_dir=self.root / 'run', ocr_provider=ocr, model_provider=provider)

    def test_no_filter_or_mutation_unknown_duplicate_leading_zero(self):
        original = copy.deepcopy(self.records)
        result = self.run_pipeline()
        self.assertEqual(result.status, 'needs_review')
        self.assertEqual(result.records, original)
        result.records[0]['weight'] = '9'
        self.assertEqual(self.records, original)
        legacy = result.as_legacy_tuple()
        legacy[0][0]['weight'] = '10'
        self.assertEqual(result.records[0]['weight'], '9')

    def test_empty_dictionary_and_corrupt_dictionary_degrade(self):
        dictionary = self.root / 'vocabulary.json'
        save_vocabulary(dictionary, Vocabulary.empty())
        result = self.run_pipeline(dictionary=dictionary)
        self.assertEqual(result.records, self.records)
        self.assertEqual(result.suggestions, [])
        dictionary.write_text('{broken', encoding='utf-8')
        result = self.run_pipeline(dictionary=dictionary)
        self.assertEqual(result.records, self.records)
        self.assertTrue(any('禁用' in n for n in result.notes))

    def test_ocr_failure_keeps_model_as_reviewable_not_legacy_success(self):
        result = self.run_pipeline(ocr_status='error')
        self.assertEqual(result.status, 'needs_review')
        self.assertEqual(result.records, self.records)
        self.assertTrue(any('OCR 失败' in n for n in result.notes))

    def test_model_failure_does_not_claim_structured_success(self):
        result = self.run_pipeline(model_status='error')
        self.assertEqual(result.status, 'error')
        self.assertFalse(result.records)
        self.assertTrue(result.blocks)

    def test_ocr_only_is_explicit(self):
        result = self.run_pipeline(model=None)
        self.assertEqual(result.status, 'ocr_only')
        self.assertEqual(result.records, [])

    def test_cli_candidate_preview_uses_real_english_keys_and_escapes_html(self):
        from handwriting.__main__ import main
        dictionary, records, output = self.root / 'v.json', self.root / 'r.json', self.root / 'preview.json'
        save_vocabulary(dictionary, {'schema_version': 1, 'revision': 'synthetic-test-only', 'entries': [
            {'id': 'test', 'field': 'category', 'canonical': '测试标准名', 'aliases': ['新品'],
             'scope': {'customers': []}, 'source': 'synthetic test', 'confirmed_at': '2026-11-06', 'enabled': True}]})
        original = copy.deepcopy(self.records)
        original[0]['remark'] = '<script>unsafe()</script>'
        write_json(records, original)
        with patch('sys.argv', ['handwriting', 'preview', '--dictionary', str(dictionary),
                               '--records', str(records), '--output', str(output)]):
            main()
        import json
        result = json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(result['records'], original)
        self.assertEqual(len(result['suggestions']), 2)
        page = output.with_suffix('.html').read_text(encoding='utf-8')
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)


if __name__ == '__main__': unittest.main()
