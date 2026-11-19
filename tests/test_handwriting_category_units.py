import unittest
from handwriting.category_units import build_dictionary, apply_category_units


class CategoryUnitsTests(unittest.TestCase):
    def examples(self, units=('斤', '斤', '斤')):
        return [dict(reviewer='human', confirmed_at='2026-11-07', customer='c',
                     image_sha256='image', row_id=str(i),
                     values={'category': '姜肉', 'unit': unit}) for i, unit in enumerate(units)]

    def test_confirmed_printed_examples_correct_glyph_confusion(self):
        dictionary = build_dictionary(self.examples())
        original = [{'category': '姜肉', 'unit': '个', 'weight': '6'}]
        result, _ = apply_category_units(original, dictionary, 'c')
        self.assertEqual(result[0]['unit'], '斤')
        self.assertEqual(result[0]['weight'], '6')
        self.assertEqual(original[0]['unit'], '个')
        self.assertTrue(result[0]['_uncertain'])

    def test_mixed_units_no_forced_mapping(self):
        result, _ = apply_category_units([{'category': '姜肉', 'unit': '个'}],
                                        build_dictionary(self.examples(('斤', '斤', '包'))), 'c')
        self.assertEqual(result[0]['unit'], '个')

    def test_packaging_customer_manual_and_duplicate_protection(self):
        dictionary = build_dictionary(self.examples() * 2)
        self.assertEqual(dictionary['entries'][0]['units']['斤'], 3)
        for row, customer in [({'category': '姜肉', 'unit': '包'}, 'c'),
                              ({'category': '姜肉', 'unit': '个'}, 'other'),
                              ({'category': '姜肉', 'unit': '个', '_user_confirmed': True}, 'c')]:
            result, _ = apply_category_units([row], dictionary, customer)
            self.assertEqual(result[0]['unit'], row['unit'])

    def test_unconfirmed_examples_are_excluded(self):
        examples = self.examples()
        for row in examples:
            row.pop('reviewer')
        self.assertFalse(build_dictionary(examples)['entries'])

    def test_review_unit_disagreement_with_confirmed_history_and_distinct_rows(self):
        reason = '单位两路读数不同，待核对'
        rows = [{'category': '姜肉', 'unit': '斤', '_uncertain': True,
                 '_review_reasons': [reason],
                 '_evidence': {'matched': True, 'bbox': [0, y, 100, y + 10],
                               'candidates': {'unit': [{'source': 'model', 'value': '斤'},
                                                       {'source': 'ocr_row', 'value': '个'}]}}}
                for y in (10, 30)]
        rows[1]['_review_reasons'].append('数量待核对')
        dictionary = build_dictionary(self.examples())
        result, _ = apply_category_units(rows, dictionary, 'c')
        self.assertFalse(result[0].get('_uncertain'))
        self.assertEqual(result[1]['_review_reasons'], ['数量待核对'])
        self.assertTrue(result[1]['_uncertain'])
        for data, customer in [([rows[0]] * 2, 'c'), (rows, 'other')]:
            result, _ = apply_category_units(data, dictionary, customer)
            self.assertIn(reason, result[0]['_review_reasons'])
        rows[0]['_evidence']['candidates']['unit'].append({'source': 'review', 'value': '个'})
        result, _ = apply_category_units(rows, dictionary, 'c')
        self.assertIn(reason, result[0]['_review_reasons'])
