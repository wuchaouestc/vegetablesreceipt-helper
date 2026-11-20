import unittest
from unittest.mock import patch

from core.ttsb2 import review_printed_rows


class PrintedColumnsTests(unittest.TestCase):
    def blocks(self, confidence=100):
        def cell(text, x, y, conf=100):
            return {'text': text, 'bbox': [x, y, x + 40, y + 16], 'confidence': conf}
        return [
            cell('商品名称', 10, 50), cell('下单量(采购单位)', 100, 50),
            cell('客户编码', 200, 50), cell('下单单位', 300, 50), cell('采购数量', 400, 50),
            cell('去皮红葱头', 10, 104), cell('6.00斤', 100, 104),
            cell('815', 200, 90), cell('斤', 300, 90), cell('4', 400, 90),
            cell('广铁', 200, 118, confidence), cell('斤', 300, 118), cell('2', 400, 118),
        ]

    def rows(self):
        return [
            dict(category='去皮红葱头', customer_code='815', weight='4', unit='斤', remark=''),
            dict(category='去皮红葱头', customer_code='广扶', weight='2', unit='斤', remark='',
                 _uncertain=True),
        ]

    def test_chinese_code_and_merged_category(self):
        rows = self.rows()
        with patch('core.ttsb2.deepseek_parse') as api:
            review_printed_rows('', rows, self.blocks(), '')
            api.assert_not_called()
        self.assertEqual(rows[1]['customer_code'], '广铁')
        self.assertFalse(rows[1].get('_uncertain'))
        self.assertEqual((rows[1]['weight'], rows[1]['unit']), ('2', '斤'))

    def test_review_updates_only_pending_field(self):
        rows = self.rows()
        with patch('core.printed_review.crop_region', return_value='crop'), patch(
                'core.ttsb2.deepseek_parse', return_value={
                    'records': [{'customer_code': '广铁', 'weight': '999', 'unit': '包'}]}) as api:
            review_printed_rows('', rows, self.blocks(80), '')
            api.assert_called_once()
        self.assertEqual((rows[1]['customer_code'], rows[1]['weight'], rows[1]['unit']),
                         ('广铁', '2', '斤'))

    def test_duplicate_candidates_and_manual_confirmation_are_protected(self):
        rows = self.rows()
        rows.append(dict(rows[1]))
        review_printed_rows('', rows, self.blocks(), '')
        self.assertEqual(rows[1]['customer_code'], '广扶')
        rows = self.rows()
        rows[1]['_user_confirmed'] = True
        review_printed_rows('', rows, self.blocks(), '')
        self.assertEqual(rows[1]['customer_code'], '广扶')
