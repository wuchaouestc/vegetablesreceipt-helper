import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from handwriting.vocabulary import (Vocabulary, VocabularyError, annotate_records,
                                    build_prompt_context, load_vocabulary,
                                    preview_candidates, save_vocabulary)


def sample():
    return {'schema_version': 1, 'revision': 'test-1', 'entries': [
        {'id': 'test-category', 'field': 'category', 'canonical': '测试品类',
         'aliases': ['测试别名'], 'scope': {'customers': ['测试客户']},
         'source': 'synthetic test fixture', 'confirmed_at': '2026-11-06', 'enabled': True},
        {'id': 'test-code', 'field': 'customer_code', 'canonical': '001A',
         'aliases': [], 'scope': {'customers': []}, 'source': 'synthetic test fixture',
         'confirmed_at': '2026-11-06', 'enabled': True}]}


class VocabularyTests(unittest.TestCase):
    def test_roundtrip_and_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'dictionary.json'
            self.assertIsNone(save_vocabulary(path, sample()))
            before = path.read_bytes()
            updated = sample()
            updated['revision'] = 'test-2'
            backup = save_vocabulary(path, updated)
            self.assertEqual(backup.read_bytes(), before)
            self.assertEqual(load_vocabulary(path).as_dict(), updated)

    def test_missing_corrupt_and_unsupported_fail_open(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'dictionary.json'
            for content in (None, b'{broken', b'\xff', b'{"schema_version":2}',
                            b'{"revision":"a","revision":"b"}'):
                if content is not None:
                    path.write_bytes(content)
                result = load_vocabulary(path)
                self.assertEqual(result.entries, ())
                self.assertTrue(result.warnings)
                records = [{'编码': '0001', '品类': '新商品', '单位': '新单位'}]
                self.assertEqual(annotate_records(result, records)['records'], records)

    def test_strict_schema(self):
        mutations = [lambda x: x.update(schema_version=True),
                     lambda x: x.update(revision=1),
                     lambda x: x['entries'][0].update(enabled='false'),
                     lambda x: x['entries'][0].update(aliases='alias'),
                     lambda x: x['entries'][0].update(canonical=123),
                     lambda x: x['entries'][0].update(scope={'customers': [123]}),
                     lambda x: x['entries'][0].update(confirmed_at='yesterday'),
                     lambda x: x['entries'][0].update(field='quantity'),
                     lambda x: x['entries'].append(copy.deepcopy(x['entries'][0]))]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                data = sample()
                mutation(data)
                with self.assertRaises(VocabularyError):
                    Vocabulary.from_dict(data)

    def test_invalid_save_never_touches_existing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'dictionary.json'
            path.write_bytes(b'original malformed data')
            with self.assertRaises(VocabularyError):
                save_vocabulary(path, {'bad': 'data'})
            self.assertEqual(path.read_bytes(), b'original malformed data')
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_replace_failure_preserves_old_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'dictionary.json'
            path.write_bytes(b'old bytes')
            with patch('handwriting.vocabulary.os.replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    save_vocabulary(path, sample())
            self.assertEqual(path.read_bytes(), b'old bytes')
            self.assertFalse(list(Path(directory).glob('*.tmp')))

    def test_scope_no_leak_and_code_no_fuzzy(self):
        vocabulary = Vocabulary.from_dict(sample())
        for customer in (None, '无关客户'):
            self.assertEqual(preview_candidates(vocabulary, 'category', '测试别名', customer), [])
            self.assertEqual(build_prompt_context(vocabulary, {'category': ['测试别名']}, customer), '')
        self.assertEqual(len(preview_candidates(vocabulary, 'category', '测试别名', '测试客户')), 1)
        self.assertEqual(preview_candidates(vocabulary, 'customer_code', '01A'), [])
        self.assertEqual(preview_candidates(vocabulary, 'customer_code', '001A')[0]['canonical'], '001A')

    def test_records_unknown_duplicates_and_metadata_are_untouched(self):
        vocabulary = Vocabulary.from_dict(sample())
        records = [{'品类': '测试别名', '编码': '001A', '斤数': '0.05', '单位': '新单位',
                    '备注': '未知开放备注', '_id': 'one', '_img': 'image', '_src': {'raw': [1]}}] * 2
        original = copy.deepcopy(records)
        result = annotate_records(vocabulary, records, '测试客户')
        self.assertEqual(result['records'], original)
        self.assertEqual(records, original)
        self.assertEqual({s['record_index'] for s in result['suggestions']}, {0, 1})
        result['records'][0]['_src']['raw'].append(2)
        self.assertEqual(records, original)

    def test_ambiguity_remains_only_suggestions(self):
        data = sample()
        other = copy.deepcopy(data['entries'][0])
        other.update(id='ambiguous', canonical='另一测试品类')
        data['entries'].append(other)
        vocabulary = Vocabulary.from_dict(data)
        result = annotate_records(vocabulary, [{'品类': '测试别名'}], '测试客户')
        self.assertEqual(len(result['suggestions'][0]['candidates']), 2)
        self.assertEqual(result['records'], [{'品类': '测试别名'}])

    def test_bounded_context_disabled_empty_and_field_map(self):
        vocabulary = Vocabulary.from_dict(sample())
        self.assertEqual(preview_candidates(vocabulary, 'category', '', '测试客户'), [])
        self.assertEqual(build_prompt_context(vocabulary, {'customer_code': ['001A']}, limit=0), '')
        context = build_prompt_context(vocabulary, {'category': ['测试别名'], 'customer_code': ['001A']}, '测试客户', limit=1)
        self.assertIn('允许新值', context)
        self.assertEqual(len(json.loads(context.split('\n')[1])), 1)
        data = sample()
        data['entries'][0]['enabled'] = False
        self.assertEqual(preview_candidates(Vocabulary.from_dict(data), 'category', '测试别名', '测试客户'), [])
        result = annotate_records(vocabulary, [{'category': '测试别名'}], '测试客户', {'category': 'category'})
        self.assertEqual(len(result['suggestions']), 1)


if __name__ == '__main__':
    unittest.main()
