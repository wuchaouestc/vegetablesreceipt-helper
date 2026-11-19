import unittest
from types import SimpleNamespace
from handwriting.category_source import apply_ocr_categories
from handwriting.evidence import apply_evidence, build_evidence
from handwriting.geometry import build_layout, normalize_blocks
from handwriting.review import ReviewRegion, ReviewToken, apply_review_results, row_version


class CategorySourceTests(unittest.TestCase):
    def test_ocr_only_fallback_preserves_other_fields(self):
        rows = [{'category': '姜均', 'customer_code': '001', 'weight': '2', 'unit': '斤', 'remark': '小'}]
        page = SimpleNamespace(rows=[SimpleNamespace(record_index=0, matched=True,
                               heading=SimpleNamespace(text='姜肉', confidence=75))])
        result = apply_ocr_categories(rows, page)
        self.assertEqual(result[0]['category'], '姜肉')
        self.assertTrue(result[0]['_uncertain'])
        for field in ('customer_code', 'weight', 'unit', 'remark'):
            self.assertEqual(rows[0][field], result[0][field])

    def test_missing_position_cannot_restore_model_guess(self):
        page = SimpleNamespace(rows=[])
        result = apply_ocr_categories([{'category': '老姜'}], page)
        self.assertEqual(result[0]['category'], '')
        self.assertTrue(result[0]['_uncertain'])

    def test_threshold_group_majority_and_local_review(self):
        def block(text, x, y, confidence=99):
            return {'text': text, 'confidence': confidence,
                    'polygon': [{'X': x, 'Y': y}, {'X': x+80, 'Y': y},
                                {'X': x+80, 'Y': y+20}, {'X': x, 'Y': y+20}]}

        layout = build_layout(normalize_blocks([
            block('善肉', 10, 10, 99), block('蒜米', 200, 10, 96),
            block('11:2斤', 10, 60), block('12:2斤', 10, 90),
            block('13:2斤', 10, 120), block('14:2斤', 200, 60),
            block('15:2斤', 200, 90),
        ]))
        model = [{'category': name, 'customer_code': code, 'weight': '2', 'unit': '斤'}
                 for code, name in [('11', '姜肉'), ('12', '姜肉'), ('13', '姜均'),
                                    ('14', '蒜米'), ('15', '蒜米')]]
        page = build_evidence(model, layout, grouped_categories=True)
        result = apply_ocr_categories(model, page, model_records=model, layout=layout)
        self.assertEqual([row['category'] for row in result], ['姜肉'] * 3 + ['蒜米'] * 2)
        self.assertTrue(result[0]['_uncertain'])
        self.assertFalse(result[3].get('_uncertain'))
        reviewed_titles = []

        def review(heading, names):
            reviewed_titles.append(heading.text)
            self.assertCountEqual(names, ['姜肉', '姜肉', '姜均'])
            return '净姜肉'

        result = apply_ocr_categories(model, page, model_records=model, layout=layout,
                                      review_title=review)
        self.assertEqual(reviewed_titles, ['善肉'])
        self.assertEqual([row['category'] for row in result], ['净姜肉'] * 3 + ['蒜米'] * 2)

    def test_bracketed_row_keeps_model_quantity_and_clears_category_doubt(self):
        def block(text, x, y):
            return {'text': text, 'confidence': 96, 'x': x, 'y': y,
                    'polygon': [{'X': x, 'Y': y}, {'X': x+80, 'Y': y},
                                {'X': x+80, 'Y': y+20}, {'X': x, 'Y': y+20}]}

        layout = build_layout(normalize_blocks([
            block('生姜', 10, 10), block('蒜米', 200, 10),
            block('11:1个', 10, 60), block('1188:24', 10, 90),
            block('1188:5斤', 10, 120), block('1188:8斤', 200, 90),
        ]))
        model = [{'category': name, 'customer_code': code, 'weight': weight, 'unit': '斤'}
                 for name, code, weight in [('生姜', '11', '1'), ('生姜', '1188', '2'),
                                             ('生姜', '1188', '5'), ('蒜米', '1188', '8')]]
        page = build_evidence(model, layout, grouped_categories=True)
        result = apply_ocr_categories(apply_evidence(model, page), page,
                                      model_records=model, layout=layout)
        self.assertEqual(result[0]['_review_reasons'], ['单位两路读数不同，待核对'])
        self.assertEqual((result[1]['category'], result[1]['weight']), ('生姜', '2'))
        self.assertEqual(result[1]['_evidence']['entry_text'], '1188:24')
        self.assertFalse(result[2].get('_uncertain'))

    def test_unqualified_ocr_quantity_cannot_override_model_or_group(self):
        def block(text, x, y):
            return {'text': text, 'confidence': 93,
                    'polygon': [{'X': x, 'Y': y}, {'X': x + 80, 'Y': y},
                                {'X': x + 80, 'Y': y + 20}, {'X': x, 'Y': y + 20}]}

        layout = build_layout(normalize_blocks([
            block('姜肉', 10, 10), block('蒜米', 200, 10),
            block('1381:2个', 10, 60), block('11:1斤', 200, 60),
            block('1381:24', 200, 90), block('12:5斤', 200, 120),
        ]))
        model = [{'category': '蒜米', 'customer_code': code, 'weight': weight, 'unit': '斤'}
                 for code, weight in [('11', '1'), ('1381', '2'), ('12', '5')]]
        page = build_evidence(model, layout, grouped_categories=True)
        row = page.rows[1]
        self.assertEqual(row.entry.text, '1381:24')
        self.assertEqual(row.candidates['weight'][-1].source, 'ocr_invalid_format')
        region = ReviewRegion(row.bbox, row_ids=[row.row_id], ordered_gap=True)
        token = ReviewToken('task', 'image', row_version(row))
        apply_review_results(page, [{'region': region, 'tokens': {row.row_id: token},
                                     'values': {row.row_id: {'weight': '24'}}}],
                             confirm_agreement=True, pending_only=True)
        result = apply_ocr_categories(apply_evidence(model, page), page,
                                      model_records=model, layout=layout)[1]
        self.assertEqual((result['category'], result['weight']), ('蒜米', '2'))
        self.assertTrue(result['_uncertain'])
        self.assertTrue(all('品类' not in reason for reason in result['_review_reasons']))
