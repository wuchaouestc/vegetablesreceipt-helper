import unittest
from types import SimpleNamespace
from handwriting.evidence import _decide_weight, match_records, Candidate, RowEvidence
from handwriting.review import _re_decide


class ConflictRegressionTests(unittest.TestCase):
    def test_digit_like_unit_cannot_replace_correct_quantity(self):
        result = _decide_weight('2', SimpleNamespace(quantity='24', ambiguous=''))
        self.assertEqual(result[:2], ('keep', '2'))

    def test_equal_matches_in_different_columns_are_rejected(self):
        entries = [SimpleNamespace(code='1366', quantity='3', unit='个', heading=None)
                   for _ in range(2)]
        layout = SimpleNamespace(rows=[{'column': i, 'entries': [entry], 'heading': None}
                                       for i, entry in enumerate(entries)])
        assignment, _, used = match_records(
            [{'category': '姜肉', 'customer_code': '1366', 'weight': '3', 'unit': '斤'}], layout)
        self.assertEqual(assignment, {})
        self.assertFalse(used)

    def test_review_agreeing_with_wrong_ocr_cannot_replace_quantity_or_unit(self):
        for field, original, wrong in [('weight', '2', '24'), ('unit', '斤', '个')]:
            row = RowEvidence(row_id='r', record_index=0)
            for source, value in [('model', original), ('ocr_row', wrong), ('review', wrong)]:
                row.add(Candidate(field=field, value=value, source=source))
            _re_decide(row, [field])
            self.assertEqual(row.decisions[-1]['action'], 'keep')
            self.assertEqual(row.decisions[-1]['value'], original)
