# -*- coding: utf-8 -*-
"""方案 v3「本轮核验结论与优化路线」的落地测试。

覆盖：
  · geometry：无冒号 / 缺单位条目解析、同行字段装配；
  · evidence：备注候选接入、单位缺失观察；
  · vocabulary：危险跨语义单位别名审核；
  · learning：单位跨语义 / 编码错读不进词典别名（转待审核）；
  · pipeline：模型调用前把已审核词典候选接进提示词、客户上下文与警告。

全部离线，不联网、不读真实单据。
"""
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from handwriting.evidence import build_evidence
from handwriting.geometry import build_layout, normalize_blocks, parse_entries
from handwriting.learning import build_review_payload, learn_vocabulary, load_pending
from handwriting.pipeline import RecognitionRequest, assisted_recognize
from handwriting.vocabulary import Vocabulary, flag_dangerous_aliases, load_vocabulary, save_vocabulary


def block(text, x0, y0, x1, y1, confidence=95.0):
    polygon = [{'X': x0, 'Y': y0}, {'X': x1, 'Y': y0}, {'X': x1, 'Y': y1}, {'X': x0, 'Y': y1}]
    return {'text': text, 'polygon': polygon, 'confidence': confidence}


class GeometryExtensionTests(unittest.TestCase):
    def test_entry_without_colon_is_parsed(self):
        entries = parse_entries(normalize_blocks([block('1136 2.5斤', 10, 100, 180, 150)])[0])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].code, '1136')
        self.assertEqual(entries[0].quantity, '2.5')
        self.assertEqual(entries[0].unit, '斤')
        self.assertEqual(entries[0].ambiguous, 'no_colon')

    def test_entry_without_unit_is_parsed_and_flagged(self):
        entries = parse_entries(normalize_blocks([block('1136:2.5', 10, 100, 180, 150)])[0])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].code, '1136')
        self.assertEqual(entries[0].quantity, '2.5')
        self.assertIsNone(entries[0].unit)
        self.assertEqual(entries[0].ambiguous, 'no_unit')

    def test_split_code_and_quantity_blocks_are_assembled_into_one_row(self):
        blocks = normalize_blocks([
            block('1136', 10, 100, 150, 150),
            block('2.5斤', 80, 100, 220, 150),
        ])
        layout = build_layout(blocks)
        entries = [e for row in layout.rows for e in row['entries']]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].code, '1136')
        self.assertEqual(entries[0].quantity, '2.5')
        self.assertEqual(entries[0].unit, '斤')
        self.assertEqual(entries[0].ambiguous, 'assembled')


def _unit_vocabulary():
    return {'schema_version': 1, 'revision': 't', 'entries': [
        {'id': 'uni-jin', 'field': 'unit', 'canonical': '斤', 'aliases': ['个'],
         'scope': {'customers': []}, 'source': 'human_correction',
         'confirmed_at': '2026-11-07', 'enabled': True}]}


class VocabularyAuditTests(unittest.TestCase):
    def test_cross_semantic_unit_alias_is_flagged(self):
        vocabulary = Vocabulary.from_dict(_unit_vocabulary())
        dangerous = flag_dangerous_aliases(vocabulary)
        self.assertEqual(len(dangerous), 1)
        self.assertEqual(dangerous[0]['canonical'], '斤')
        self.assertEqual(dangerous[0]['alias'], '个')

    def test_same_unit_alias_is_not_flagged(self):
        data = _unit_vocabulary()
        data['entries'][0]['aliases'] = ['斤']
        vocabulary = Vocabulary.from_dict(data)
        self.assertEqual(flag_dangerous_aliases(vocabulary), [])


class EvidenceExtensionTests(unittest.TestCase):
    def _layout(self):
        return build_layout(normalize_blocks([
            block('马蹄肉', 56, 254, 256, 314, confidence=98),
            block('931:12斤', 50, 307, 203, 364, confidence=95),
        ]))

    def test_remark_gets_model_and_vocabulary_candidates(self):
        vocabulary = Vocabulary.from_dict({'schema_version': 1, 'revision': 't', 'entries': [
            {'id': 'rem-long', 'field': 'remark', 'canonical': '长', 'aliases': [],
             'scope': {'customers': []}, 'source': 'human_correction',
             'confirmed_at': '2026-11-07', 'enabled': True}]})
        records = [{'category': '马蹄肉', 'customer_code': '931', 'weight': '12', 'unit': '斤',
                    'remark': '长'}]
        page = build_evidence(records, self._layout(), vocabulary=vocabulary)
        sources = {c.source for c in page.rows[0].candidates.get('remark', [])}
        self.assertEqual(sources, {'model', 'vocabulary'})

    def test_missing_model_unit_is_observed(self):
        records = [{'category': '马蹄肉', 'customer_code': '931', 'weight': '12', 'unit': ''}]
        page = build_evidence(records, self._layout())
        self.assertTrue(any('没读出单位' in o for o in page.rows[0].observations))

    def test_both_units_missing_is_observed(self):
        layout = build_layout(normalize_blocks([
            block('马蹄肉', 56, 254, 256, 314, confidence=98),
            block('931:12', 50, 307, 203, 364, confidence=95),
        ]))
        records = [{'category': '马蹄肉', 'customer_code': '931', 'weight': '12', 'unit': ''}]
        page = build_evidence(records, layout)
        self.assertTrue(any('两路都没读出' in o for o in page.rows[0].observations))


def _record(category='马步鱼', code='935', weight='3', unit='斤'):
    return {'category': category, 'customer_code': code, 'weight': weight, 'unit': unit,
            'remark': '', '_review_reasons': [],
            '_evidence': {'row_id': 'row-0001', 'bbox': [1, 2, 3, 4], 'heading_text': '马蹄肉',
                          'entry_text': '935:3斤', 'candidates': {}, 'decisions': []}}


class LearningGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.vocabulary = self.root / 'dict' / 'handwriting.json'

    def test_cross_semantic_unit_correction_goes_pending_not_alias(self):
        payload = build_review_payload([_record()], image_path='x.jpg', image_sha256='abc')
        payload['reviewer'] = '张三'
        payload['rows'][0]['ai']['unit'] = '个'
        payload['rows'][0]['final']['unit'] = '斤'
        result = learn_vocabulary(payload, vocabulary_path=self.vocabulary)
        self.assertNotIn('unit', [item['field'] for item in result['changed']])
        self.assertTrue(any(p['field'] == 'unit' and p['from'] == '个' and p['to'] == '斤'
                            for p in result['pending']))
        pending = load_pending(self.vocabulary.with_suffix('.pending.json'))
        self.assertEqual(pending[0]['reason'], 'cross_semantic_unit')

    def test_code_misread_goes_pending_not_alias(self):
        payload = build_review_payload([_record(code='136')], image_path='x.jpg', image_sha256='abc')
        payload['reviewer'] = '张三'
        payload['rows'][0]['ai']['customer_code'] = '136'
        payload['rows'][0]['final']['customer_code'] = '1136'
        result = learn_vocabulary(payload, vocabulary_path=self.vocabulary)
        self.assertNotIn('customer_code', [item['field'] for item in result['changed']])
        self.assertTrue(any(p['field'] == 'customer_code' and p['reason'] == 'code_misread'
                            for p in result['pending']))

    def test_category_correction_still_becomes_enabled_alias(self):
        payload = build_review_payload([_record()], image_path='x.jpg', image_sha256='abc')
        payload['reviewer'] = '张三'
        payload['rows'][0]['ai']['category'] = '马步鱼'
        payload['rows'][0]['final']['category'] = '马蹄肉'
        result = learn_vocabulary(payload, vocabulary_path=self.vocabulary)
        self.assertEqual(len(result['changed']), 1)
        self.assertEqual(result['pending'], [])
        entry = next(e for e in load_vocabulary(self.vocabulary).entries
                     if e.canonical == '马蹄肉')
        self.assertIn('马步鱼', entry.aliases)


class PipelinePromptContextTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.image = self.root / 'image.png'
        Image.new('RGB', (400, 600), 'white').save(self.image)
        self.vocabulary = self.root / 'handwriting.json'
        save_vocabulary(self.vocabulary, {'schema_version': 1, 'revision': 't', 'entries': [
            {'id': 'cat-mati', 'field': 'category', 'canonical': '马蹄肉', 'aliases': ['马步鱼'],
             'scope': {'customers': []}, 'source': 'human_correction',
             'confirmed_at': '2026-11-07', 'enabled': True}]})

    def test_model_prompt_includes_reviewed_candidates(self):
        captured = {}

        def ocr(path, **kwargs):
            return {'status': 'ok', 'blocks': [
                block('马步鱼', 56, 254, 256, 314, confidence=98),
                block('931:12斤', 50, 307, 203, 364, confidence=95)]}

        def model(path, **kwargs):
            captured['prompt'] = kwargs.get('system_prompt', '')
            return {'status': 'ok', 'records': [
                {'category': '马蹄肉', 'customer_code': '931', 'weight': '12', 'unit': '斤'}],
                'notes': []}

        request = RecognitionRequest(str(self.image), model='deepseek-chat', prompt='读这张单',
                                     vocabulary_path=str(self.vocabulary))
        assisted_recognize(request, output_dir=self.root / 'run', ocr_provider=ocr,
                           model_provider=model)
        self.assertIn('马蹄肉', captured['prompt'])
        self.assertIn('允许新值', captured['prompt'])

    def test_vocabulary_warning_reaches_notes(self):
        def ocr(path, **kwargs):
            return {'status': 'ok', 'blocks': [block('931:12斤', 50, 307, 203, 364, confidence=95)]}

        def model(path, **kwargs):
            return {'status': 'ok', 'records': [
                {'category': '马蹄肉', 'customer_code': '931', 'weight': '12', 'unit': '斤'}],
                'notes': []}

        self.vocabulary.write_text('{broken', encoding='utf-8')
        request = RecognitionRequest(str(self.image), model='deepseek-chat',
                                     vocabulary_path=str(self.vocabulary))
        result = assisted_recognize(request, output_dir=self.root / 'run', ocr_provider=ocr,
                                    model_provider=model)
        self.assertTrue(any('词典提示' in n for n in result.notes))


if __name__ == '__main__':
    unittest.main()
