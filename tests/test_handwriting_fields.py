# -*- coding: utf-8 -*-
"""字段识别模块测试：区域规划、候选挂接（观察模式）、单位解析、数据审计与导出。

全部离线；真实模型走单独冒烟脚本（不在 unittest 里联网）。
"""
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from handwriting.evidence import apply_evidence, build_evidence
from handwriting.field_dataset import (audit_annotations, build_region_annotation_draft,
                                      export_training_manifest)
from handwriting.field_regions import plan_field_regions
from handwriting.geometry import build_layout, normalize_blocks
from handwriting.modes import resolve_mode
from handwriting.paddle_fields import (attach_field_candidates, parse_field_results,
                                       parse_unit_from_line, recognize_fields_client)


def block(text, x0, y0, x1, y1, confidence=95.0):
    polygon = [{'X': x0, 'Y': y0}, {'X': x1, 'Y': y0}, {'X': x1, 'Y': y1}, {'X': x0, 'Y': y1}]
    return {'text': text, 'polygon': polygon, 'confidence': confidence}


def _layout():
    return build_layout(normalize_blocks([
        block('马蹄肉', 56, 254, 256, 314, confidence=98),
        block('931:12斤', 50, 307, 203, 364, confidence=95),
        block('21:20个', 40, 360, 184, 410, confidence=99),
    ]))


class RegionPlanningTests(unittest.TestCase):
    def test_one_heading_one_region_dedup(self):
        regions = plan_field_regions(_layout())
        categories = [r for r in regions if r.field == 'category']
        self.assertEqual(len(categories), 1)
        self.assertEqual(categories[0].match_kind, 'heading')

    def test_unit_region_uses_entry_bbox(self):
        regions = plan_field_regions(_layout())
        units = [r for r in regions if r.field == 'unit']
        self.assertEqual(len(units), 2)
        self.assertTrue(any(u.expected_unit == '斤' for u in units))
        self.assertTrue(any(u.expected_unit == '个' for u in units))

    def test_region_bbox_padded(self):
        regions = plan_field_regions(_layout())
        cat = next(r for r in regions if r.field == 'category')
        sx0, sy0, sx1, sy1 = cat.source_bbox
        bx0, by0, bx1, by1 = cat.bbox
        self.assertLessEqual(bx0, sx0)
        self.assertGreaterEqual(bx1, sx1)


class UnitParseTests(unittest.TestCase):
    def test_parse_trailing_unit(self):
        self.assertEqual(parse_unit_from_line('1136:2.5斤'), '斤')
        self.assertEqual(parse_unit_from_line('14:3个'), '个')
        self.assertEqual(parse_unit_from_line('9:5公斤'), '公斤')
        self.assertEqual(parse_unit_from_line('看不清'), None)
        self.assertEqual(parse_unit_from_line(''), None)

    def test_parse_field_results_maps_by_region_id(self):
        from handwriting.field_regions import FieldRegion
        regions = [FieldRegion('unit-0', 'unit', (0, 0, 10, 10), (0, 0, 10, 10), 'entry'),
                   FieldRegion('cat-0', 'category', (0, 0, 10, 10), (0, 0, 10, 10), 'heading')]
        raw = [{'region_id': 'unit-0', 'status': 'ok', 'raw_text': '12:3斤', 'rec_score': 0.9},
               {'region_id': 'cat-0', 'status': 'ok', 'raw_text': '马蹄肉', 'rec_score': 0.95},
               {'region_id': 'unit-1', 'status': 'error', 'raw_text': '', 'rec_score': 0.0}]
        parsed = parse_field_results(regions, raw)
        by_field = {p['field']: p for p in parsed}
        self.assertEqual(by_field['unit']['value'], '斤')
        self.assertEqual(by_field['category']['value'], '马蹄肉')


class AttachObserveTests(unittest.TestCase):
    def test_attach_adds_candidate_but_does_not_replace(self):
        layout = _layout()
        records = [{'category': '马齿苋', 'customer_code': '931', 'weight': '12', 'unit': '斤'},
                   {'category': '马齿苋', 'customer_code': '21', 'weight': '20', 'unit': '斤'}]
        page = build_evidence(records, layout, min_heading_confidence=90)
        # Paddle 读成第三种写法，与模型「马齿苋」和标题「马蹄肉」都不一致
        field_values = [{'field': 'category', 'value': '九层塔', 'source_bbox': (56.0, 254.0, 256.0, 314.0),
                         'match_kind': 'heading', 'raw_text': '九层塔', 'rec_score': 0.7}]
        attach_field_candidates(page, field_values)
        updated = apply_evidence(records, page)
        row = page.rows[0]
        sources = {c.source for c in row.candidates.get('category', [])}
        self.assertIn('paddle_mobile_rec', sources)
        # 观察模式：候选进了证据链，当前值不改（仍由原有裁决决定），冲突记观察
        self.assertTrue(any('Paddle 读品类' in o for o in row.observations))
        self.assertNotIn('九层塔', updated[0]['category'])


class ClientDegradeTests(unittest.TestCase):
    def test_no_service_returns_empty_and_note(self):
        results, notes = recognize_fields_client(
            'x.png', [], sidecar_url='http://127.0.0.1:1/recognize_fields', timeout=1)
        self.assertEqual(results, [])
        self.assertTrue(notes)


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_audit_reports_drafts_and_missing_regions(self):
        annotations = self.root / 'annotations'
        annotations.mkdir()
        (annotations / 'draft.json').write_text(json.dumps({
            'image_sha256': 'a' * 64, 'image_path': 'x.png', 'review_status': 'draft_visual',
            'records': [{'category': '马蹄肉'}], 'regions': []}), encoding='utf-8')
        result = audit_annotations(annotations, image_root=self.root)
        self.assertEqual(result['drafts'], 1)
        self.assertTrue(any('regions 为空' in i for i in result['issues']))

    def test_draft_and_export(self):
        layout = _layout()
        draft = build_region_annotation_draft(layout)
        self.assertTrue(any(d['field'] == 'unit' and d['transcript'] for d in draft))
        image = self.root / 'x.png'
        Image.new('RGB', (400, 500), 'white').save(image)
        out = export_training_manifest(draft, image, self.root / 'export')
        self.assertGreaterEqual(out['crops'], 1)
        self.assertTrue(Path(out['manifest']).is_file())


class ModeConfigTests(unittest.TestCase):
    def test_field_assist_default_off(self):
        self.assertFalse(resolve_mode({}).field_assist)

    def test_field_assist_on(self):
        config = resolve_mode({'字段辅助': '开'})
        self.assertTrue(config.field_assist)
        self.assertEqual(config.field_region_limit, 32)


if __name__ == '__main__':
    unittest.main()
