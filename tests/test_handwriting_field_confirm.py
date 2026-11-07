# -*- coding: utf-8 -*-
"""人工核对结论落 confirmed 的测试。

守的是三条底线：
  ① 人工逐条结论 > 通则规则 > 草稿原值；
  ② 通则只动「单位」，不动编码与数量；
  ③ 人工没表态的区域不许冒充人工确认（必须标 draft_unreviewed）。
"""
import json
import tempfile
import unittest
from pathlib import Path

from handwriting.field_confirm import (apply_confirmations, confirmed_payload,
                                      load_corrections, save_confirmed)


def _草稿():
    return {'fields': [
        {'region_id': 'cat-0', 'field': 'category', 'bbox': [0, 0, 10, 10],
         'transcript': '马路肉', 'normalized_value': '马路肉', 'source': 'auto_draft'},
        {'region_id': 'cat-1', 'field': 'category', 'bbox': [0, 12, 10, 22],
         'transcript': '各皮', 'normalized_value': '各皮', 'source': 'auto_draft'},
        {'region_id': 'cat-2', 'field': 'category', 'bbox': [0, 24, 10, 34],
         'transcript': '工', 'normalized_value': '工', 'source': 'auto_draft'},
        {'region_id': 'unit-1', 'field': 'unit', 'bbox': [0, 36, 10, 46],
         'transcript': '931:0.5个', 'normalized_value': '个', 'source': 'auto_draft'},
        {'region_id': 'unit-2', 'field': 'unit', 'bbox': [0, 48, 10, 58],
         'transcript': '1381:24', 'normalized_value': '', 'source': 'auto_draft'},
        {'region_id': 'unit-3', 'field': 'unit', 'bbox': [0, 60, 10, 70],
         'transcript': '1137:1包', 'normalized_value': '包', 'source': 'auto_draft'},
        {'region_id': 'unit-4', 'field': 'unit', 'bbox': [0, 72, 10, 82],
         'transcript': '1368:10斤', 'normalized_value': '斤', 'source': 'auto_draft'},
    ]}


def _按区域(result, rid):
    for item in result['fields']:
        if item['region_id'] == rid:
            return item
    return None


class 人工优先Tests(unittest.TestCase):
    def test_显式纠正压过通则(self):
        result = apply_confirmations(_草稿(), {
            'unit_rule': {'to': '斤'},
            'regions': {'unit-1': {'transcript': '931:0.5斤', 'normalized_value': '斤'}},
        })
        row = _按区域(result, 'unit-1')
        self.assertEqual(row['transcript'], '931:0.5斤')
        self.assertEqual(row['source'], 'human_confirmed')

    def test_显式纠正只影响该字段(self):
        result = apply_confirmations(_草稿(), {
            'unit_rule': {'to': '斤'},
            'regions': {'cat-0': {'transcript': '马蹄肉', 'normalized_value': '马蹄肉'}},
        })
        cat = _按区域(result, 'cat-0')
        self.assertEqual(cat['transcript'], '马蹄肉')
        # 同一张单里没表态的单位行仍走通则
        self.assertEqual(_按区域(result, 'unit-1')['normalized_value'], '斤')

    def test_交叉证据来源单独标记(self):
        result = apply_confirmations(_草稿(), {
            'regions': {'unit-2': {'transcript': '931:0.5斤', 'normalized_value': '斤',
                                   'source': 'cross_evidence'}},
        })
        row = _按区域(result, 'unit-2')
        self.assertEqual(row['source'], 'cross_evidence')
        self.assertNotEqual(row['source'], 'human_confirmed')


class 通则Tests(unittest.TestCase):
    def test_通则只改单位不动编码数量(self):
        result = apply_confirmations(_草稿(), {'unit_rule': {'to': '斤'}})
        row = _按区域(result, 'unit-1')
        self.assertEqual(row['transcript'], '931:0.5斤')
        self.assertEqual(row['normalized_value'], '斤')
        self.assertTrue(row['transcript'].startswith('931:0.5'))

    def test_通则不影响品类(self):
        result = apply_confirmations(_草稿(), {'unit_rule': {'to': '斤'}})
        self.assertEqual(_按区域(result, 'cat-0')['transcript'], '马路肉')

    def test_通则keep例外(self):
        result = apply_confirmations(_草稿(), {'unit_rule': {'to': '斤', 'keep': ['包']}})
        row = _按区域(result, 'unit-3')
        self.assertEqual(row['transcript'], '1137:1包')
        self.assertEqual(row['normalized_value'], '包')

    def test_已经是目标单位不动(self):
        result = apply_confirmations(_草稿(), {'unit_rule': {'to': '斤'}})
        row = _按区域(result, 'unit-4')
        self.assertEqual(row['transcript'], '1368:10斤')

    def test_digit_suffix替换被吞掉的末位(self):
        result = apply_confirmations(_草稿(),
                                     {'unit_rule': {'to': '斤', 'digit_suffix': True}})
        row = _按区域(result, 'unit-2')
        self.assertEqual(row['transcript'], '1381:2斤')
        self.assertEqual(row['normalized_value'], '斤')
        self.assertEqual(row['source'], 'rule_derived')

    def test_没开digit_suffix不动缺单位的行(self):
        result = apply_confirmations(_草稿(), {'unit_rule': {'to': '斤'}})
        row = _按区域(result, 'unit-2')
        self.assertEqual(row['transcript'], '1381:24')


class 未表态Tests(unittest.TestCase):
    def test_未表态区域保持原值并标注(self):
        result = apply_confirmations(_草稿(), {
            'regions': {'cat-0': {'transcript': '马蹄肉', 'normalized_value': '马蹄肉'}},
        })
        row = _按区域(result, 'cat-1')
        self.assertEqual(row['transcript'], '各皮')
        self.assertEqual(row['source'], 'draft_unreviewed')
        self.assertIn('draft_unchanged', row['confirm_sources'])

    def test_规则推导不等于人工确认(self):
        result = apply_confirmations(_草稿(), {'unit_rule': {'to': '斤'}})
        row = _按区域(result, 'unit-1')
        self.assertEqual(row['source'], 'rule_derived')
        self.assertNotEqual(row['source'], 'human_confirmed')


class 删除留痕Tests(unittest.TestCase):
    def test_删除区域进removed且不进fields(self):
        result = apply_confirmations(_草稿(), {
            'regions': {'cat-2': {'remove': True, 'reason': '没有这个区域'}},
        })
        self.assertIsNone(_按区域(result, 'cat-2'))
        self.assertEqual(len(result['removed_regions']), 1)
        self.assertEqual(result['removed_regions'][0]['region_id'], 'cat-2')
        self.assertEqual(result['removed_regions'][0]['reason'], '没有这个区域')


class 读写Tests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_损坏文件返回空(self):
        path = self.root / 'bad.json'
        path.write_text('{不是 json', encoding='utf-8')
        self.assertEqual(load_corrections(path), {})
        self.assertEqual(load_corrections(self.root / 'missing.json'), {})

    def test_正常读取(self):
        path = self.root / 'c.json'
        path.write_text(json.dumps({'a': {'regions': {}}}), encoding='utf-8')
        self.assertIn('a', load_corrections(path))

    def test_save_confirmed落盘且可读回(self):
        path = self.root / 'out' / 'x.json'
        save_confirmed(path, {'a': 1})
        self.assertTrue(path.is_file())
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['a'], 1)

    def test_confirmed_payload结构(self):
        draft = _草稿()
        result = apply_confirmations(draft, {})
        payload = confirmed_payload(draft, result, image_sha256='s' * 64,
                                    image_path='x.png', reviewer='人工核对',
                                    confirmed_at='2026-11-07')
        self.assertEqual(payload['review_status'], 'confirmed')
        self.assertEqual(payload['reviewer'], '人工核对')
        self.assertEqual(payload['image_sha256'], 's' * 64)
        self.assertEqual(len(payload['fields']), len(draft['fields']))
        self.assertTrue(payload['complete'])


if __name__ == '__main__':
    unittest.main()
