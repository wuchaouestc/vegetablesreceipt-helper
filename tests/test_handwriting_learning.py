# -*- coding: utf-8 -*-
"""模型明细归一化与人工核对学习闭环的测试。"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from handwriting.learning import (LearningError, build_review_payload, find_similar_examples,
                                  learn_examples, learn_vocabulary, load_examples,
                                  save_annotation, save_confirmed, validate_review_payload)
from handwriting.model_records import normalize_records, strip_internal
from handwriting.vocabulary import load_vocabulary


def _record(category='马步鱼', code='935', weight='3', unit='斤', row_id='row-0001',
            reasons=None):
    return {'category': category, 'customer_code': code, 'weight': weight, 'unit': unit,
            'remark': '', '_review_reasons': reasons or [],
            '_evidence': {'row_id': row_id, 'bbox': [1, 2, 3, 4], 'heading_text': '马蹄肉',
                          'entry_text': '935:3斤', 'candidates': {}, 'decisions': []}}


class NormalizeTests(unittest.TestCase):
    """模型按标题分组返回时必须能展开，否则整条证据链会失配。"""

    def test_grouped_records_are_flattened(self):
        raw = [{'name': '沙姜', 'entries': ['1366: 1个', '935: 1.5个']}]
        flat, notes = normalize_records(raw)
        self.assertEqual(2, len(flat))
        self.assertEqual('沙姜', flat[0]['category'])
        self.assertEqual('1366', flat[0]['customer_code'])
        self.assertEqual('1', flat[0]['weight'])
        self.assertEqual('个', flat[0]['unit'])
        self.assertEqual('935', flat[1]['customer_code'])
        self.assertEqual('1.5', flat[1]['weight'])
        self.assertTrue(any('展开' in note for note in notes))

    def test_flat_records_pass_through(self):
        raw = [{'category': '沙姜', 'customer_code': '1366', 'weight': '1', 'unit': '个'}]
        flat, _notes = normalize_records(raw)
        self.assertEqual(1, len(flat))
        self.assertEqual('沙姜', flat[0]['category'])
        self.assertEqual('1366', flat[0]['customer_code'])

    def test_group_alias_keys_are_supported(self):
        raw = [{'category': '沙姜', 'items': [{'text': '1366: 1个'}]}]
        flat, _notes = normalize_records(raw)
        self.assertEqual('沙姜', flat[0]['category'])
        self.assertEqual('1366', flat[0]['customer_code'])

    def test_remark_in_parentheses_is_kept(self):
        flat, _notes = normalize_records([{'name': '古城千禧', 'entries': ['933: 2个(大)']}])
        self.assertEqual('大', flat[0]['remark'])
        self.assertEqual('2', flat[0]['weight'])

    def test_range_quantity_is_kept_verbatim(self):
        flat, _notes = normalize_records([{'name': '沙姜', 'entries': ['1366: 1-2个']}])
        self.assertEqual('1-2', flat[0]['weight'])
        self.assertEqual('range', flat[0].get('_ambiguous'))

    def test_unreadable_entry_keeps_code_empty(self):
        flat, notes = normalize_records([{'name': '沙姜', 'entries': ['看不清']}])
        self.assertEqual('', flat[0]['customer_code'])
        self.assertTrue(any('读不出编码' in note for note in notes))

    def test_strip_internal_removes_private_keys(self):
        flat, _notes = normalize_records([{'name': '沙姜', 'entries': ['1366: 1个']}])
        cleaned = strip_internal(flat[0])
        self.assertNotIn('_record_index', cleaned)
        self.assertNotIn('_model_heading', cleaned)
        self.assertEqual('沙姜', cleaned['category'])

    def test_row_id_is_preserved(self):
        flat, _notes = normalize_records([{'row_id': 'r-9', 'category': '沙姜',
                                            'customer_code': '1366', 'weight': '1', 'unit': '个'}])
        self.assertEqual('r-9', flat[0]['row_id'])


class ReviewPayloadTests(unittest.TestCase):

    def test_payload_keeps_ai_and_final_values(self):
        payload = build_review_payload([_record(reasons=['品类两路不一致'])],
                                       image_path='x.jpg', image_sha256='abc')
        row = payload['rows'][0]
        self.assertEqual('马步鱼', row['ai']['category'])
        self.assertEqual('马步鱼', row['final']['category'])
        self.assertEqual('doubt', row['status'])

    def test_ok_row_has_no_reasons(self):
        payload = build_review_payload([_record(reasons=[])], image_path='x.jpg', image_sha256='abc')
        self.assertEqual('ok', payload['rows'][0]['status'])

    def test_validation_requires_reviewer_and_fields(self):
        payload = build_review_payload([_record()], image_path='x.jpg', image_sha256='abc')
        errors = validate_review_payload(payload)
        self.assertTrue(any('复核人' in error for error in errors))
        payload['reviewer'] = '张三'
        self.assertEqual([], validate_review_payload(payload))

    def test_removed_row_is_not_required_to_be_filled(self):
        payload = build_review_payload([_record()], image_path='x.jpg', image_sha256='abc')
        payload['reviewer'] = '张三'
        payload['rows'][0]['status'] = 'removed'
        payload['rows'][0]['final']['category'] = ''
        self.assertEqual([], validate_review_payload(payload))

    def test_duplicate_row_id_is_rejected(self):
        payload = build_review_payload([_record(), _record(row_id='row-0001')],
                                       image_path='x.jpg', image_sha256='abc')
        payload['reviewer'] = '张三'
        errors = validate_review_payload(payload)
        self.assertTrue(any('重复' in error for error in errors))


class LearningTests(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.annotations = self.root / 'annotations'
        self.vocabulary = self.root / 'dict' / 'handwriting.json'
        self.examples = self.root / 'learning' / 'examples.json'

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _payload(self, reviewer='张三'):
        payload = build_review_payload([_record()], image_path='x.jpg', image_sha256='abc')
        payload['reviewer'] = reviewer
        payload['rows'][0]['final']['category'] = '马蹄肉'
        return payload

    def test_correction_becomes_dictionary_alias(self):
        result = learn_vocabulary(self._payload(), vocabulary_path=self.vocabulary)
        self.assertEqual(1, len(result['changed']))
        self.assertEqual('马步鱼', result['changed'][0]['alias'])
        self.assertEqual('马蹄肉', result['changed'][0]['canonical'])
        vocabulary = load_vocabulary(self.vocabulary)
        entry = next(e for e in vocabulary.entries if e.canonical == '马蹄肉')
        self.assertIn('马步鱼', entry.aliases)
        self.assertEqual('human_correction', entry.source)

    def test_quantity_never_enters_the_dictionary(self):
        result = learn_vocabulary(self._payload(), vocabulary_path=self.vocabulary)
        self.assertNotIn('weight', [item['field'] for item in result['changed']]
                         + [item['field'] for item in result['learned']])

    def test_correct_value_becomes_alias_of_nothing(self):
        payload = build_review_payload([_record(category='沙姜')], image_path='x.jpg',
                                       image_sha256='abc')
        payload['reviewer'] = '张三'
        result = learn_vocabulary(payload, vocabulary_path=self.vocabulary)
        self.assertEqual([], result['changed'])
        vocabulary = load_vocabulary(self.vocabulary)
        self.assertTrue(any(e.canonical == '沙姜' for e in vocabulary.entries))

    def test_alias_survives_a_second_round(self):
        learn_vocabulary(self._payload(), vocabulary_path=self.vocabulary)
        payload = self._payload(reviewer='李四')
        learn_vocabulary(payload, vocabulary_path=self.vocabulary)
        vocabulary = load_vocabulary(self.vocabulary)
        entry = next(e for e in vocabulary.entries if e.canonical == '马蹄肉')
        self.assertEqual(1, len(entry.aliases))

    def test_examples_are_written_with_corrections(self):
        payload = self._payload()
        payload['confirmed_at'] = '2026-11-06T10:00:00'
        result = learn_examples(payload, examples_path=self.examples)
        self.assertEqual(1, result['examples'])
        stored = load_examples(self.examples)[0]
        self.assertEqual('马蹄肉', stored['values']['category'])
        self.assertEqual('马步鱼', stored['ai_values']['category'])
        self.assertEqual(1, len(stored['corrections']))
        self.assertEqual('category', stored['corrections'][0]['field'])

    def test_annotation_is_written_with_confirmed_status(self):
        path = save_annotation(self._payload(), annotations_dir=self.annotations,
                               source_group='g1')
        saved = json.loads(Path(path).read_text(encoding='utf-8'))
        self.assertEqual('confirmed', saved['review_status'])
        self.assertTrue(saved['complete'])
        self.assertEqual('张三', saved['reviewer'])
        self.assertEqual(1, len(saved['records']))
        self.assertEqual('马蹄肉', saved['records'][0]['category'])

    def test_annotation_refuses_incomplete_payload(self):
        payload = self._payload()
        payload['rows'][0]['final']['weight'] = ''
        with self.assertRaises(LearningError):
            save_annotation(payload, annotations_dir=self.annotations)

    def test_save_confirmed_writes_all_three(self):
        result = save_confirmed(self._payload(), annotations_dir=self.annotations,
                                 vocabulary_path=self.vocabulary, examples_path=self.examples,
                                 source_group='g1')
        self.assertTrue(Path(result['annotation_path']).exists())
        self.assertTrue(self.vocabulary.exists())
        self.assertTrue(self.examples.exists())
        self.assertEqual(1, len(result['vocabulary']['changed']))

    def test_find_similar_examples_matches_by_code(self):
        examples = [{'id': 'a', 'values': {'customer_code': '935', 'category': '马蹄肉'}}]
        found = find_similar_examples({'customer_code': '935'}, examples)
        self.assertEqual(1, len(found))
        self.assertEqual('马蹄肉', found[0]['values']['category'])

    def test_find_similar_examples_returns_empty_without_match(self):
        examples = [{'id': 'a', 'values': {'customer_code': '111', 'category': '别的'}}]
        self.assertEqual([], find_similar_examples({'customer_code': '935'}, examples))

    def test_load_examples_survives_missing_file(self):
        self.assertEqual([], load_examples(self.root / 'nope.json'))


if __name__ == '__main__':
    unittest.main()
