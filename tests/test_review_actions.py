from pathlib import Path
import tempfile
import unittest
from handwriting.review_actions import mark_all, learn_reviewed, load_reviewed_result
from handwriting.storage import digest
from handwriting.storage import read_json


class ReviewActionTests(unittest.TestCase):
    def test_bulk_unconfirm_excludes_previously_edited_rows(self):
        rows = [{'_user_edited': True}, {}]
        mark_all(rows, '? 拿不准')
        self.assertTrue(all(r['_review_excluded'] for r in rows))
        mark_all(rows, '✓ 已确认')
        self.assertTrue(all(r['_user_confirmed'] and not r['_review_excluded'] for r in rows))

    def test_only_reviewed_rows_saved_and_prior_annotations_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / 'source.png'
            image.write_bytes(b'fixed image identity')
            rows = [{'_img': str(image), '_id': i, 'category': '生姜', 'customer_code': str(i),
                     'weight': '2', 'unit': '斤', '_user_confirmed': i == 0} for i in range(2)]
            result = learn_reviewed(rows, '客户', root / 'data')
            self.assertEqual((result['rows'], result['skipped']), (1, 1))
            rows[0]['_user_confirmed'] = False
            rows[1]['_user_confirmed'] = True
            learn_reviewed(rows, '客户', root / 'data')
            annotation = read_json(next((root / 'data/annotations/reviewed_rows').glob('*.json')))
            self.assertEqual(len(annotation['records']), 2)
            self.assertFalse(annotation['complete'])

    def test_full_review_replay_is_exact_across_customers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / 'image.png'
            image.write_bytes(b'same image')
            rows = [{'_img': str(image), '_id': i, '_user_confirmed': True,
                     'category': '姜肉', 'customer_code': '001', 'weight': str(i),
                     'unit': '斤', 'remark': ''} for i in (1, 2)]
            learn_reviewed(rows, '客户', root / 'data')
            replay = load_reviewed_result(root / 'data', digest(image), '客户')
            self.assertEqual([r['weight'] for r in replay], ['1', '2'])
            self.assertTrue(all(r['_learned_replay'] for r in replay))
            other = load_reviewed_result(root / 'data', digest(image), '其他客户')
            self.assertEqual(other, replay)
            self.assertIsNone(load_reviewed_result(root / 'data', 'different-image', '客户'))
            rows[1]['_user_confirmed'] = False
            learn_reviewed(rows, '客户', root / 'data')
            self.assertIsNone(load_reviewed_result(root / 'data', digest(image), '客户'))

    def test_learning_accepts_source_names_and_structured_row_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / 'source.png'
            image.write_bytes(b'fixed image identity')
            rows = [{'_img': str(image), '_id': i, '_user_confirmed': True,
                     'category': '姜肉', 'customer_code': 'k222', 'weight': str(i),
                     'unit': '斤', '_evidence': evidence}
                    for i, evidence in enumerate(('ocr', 'consensus', {'row_id': 'row-003'}), 1)]
            result = learn_reviewed(rows, '客户', root / 'data')
            self.assertEqual(result['rows'], 3)
            annotation = read_json(root / 'data/annotations/reviewed_rows' / (digest(image) + '.json'))
            self.assertEqual([r['row_id'] for r in annotation['records']], ['1', '2', 'row-003'])
            replay = load_reviewed_result(root / 'data', digest(image), '客户')
            self.assertEqual([r['weight'] for r in replay], ['1', '2', '3'])
