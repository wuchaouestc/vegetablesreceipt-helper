"""阶段 3 测试：完整框、分栏、行列关联、候选追踪、保守裁决、旧裁决门控。

全部离线，不联网、不读真实单据。
"""
import unittest

from handwriting.evidence import apply_evidence, build_evidence, match_records
from handwriting.gate import guard_category_boundaries, guard_cross_check, snapshot
from handwriting.geometry import (build_layout, heading_for, iou, is_heading, normalize_blocks,
                                  normalize_text, ov_ratio, parse_entries)


def block(text, x0, y0, x1, y1, confidence=95.0):
    """造一个带完整多边形的 OCR 块。"""
    polygon = [{'X': x0, 'Y': y0}, {'X': x1, 'Y': y0}, {'X': x1, 'Y': y1}, {'X': x0, 'Y': y1}]
    return {'text': text, 'polygon': polygon, 'confidence': confidence}


class GeometryTests(unittest.TestCase):
    def test_polygon_becomes_full_bounding_box(self):
        blocks = normalize_blocks([block('1137:0.5斤', 40, 240, 206, 314)])
        self.assertEqual(blocks[0].bbox, (40.0, 240.0, 206.0, 314.0))
        self.assertEqual(blocks[0].confidence, 95.0)
        self.assertEqual(len(blocks[0].polygon), 4)

    def test_legacy_first_point_only_block_degrades_gracefully(self):
        blocks = normalize_blocks([{'text': '1137:0.5斤', 'x': 40, 'y': 240}])
        self.assertEqual(blocks[0].bbox, (40.0, 240.0, 40.0, 240.0))

    def test_entry_parsing_splits_code_quantity_unit(self):
        blocks = normalize_blocks([block('1136:0.5斤', 250, 870, 390, 924)])
        entries = parse_entries(blocks[0])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].code, '1136')
        self.assertEqual(entries[0].quantity, '0.5')
        self.assertEqual(entries[0].unit, '斤')

    def test_entry_parsing_keeps_range_ambiguous(self):
        blocks = normalize_blocks([block('12:1-5个', 57, 863, 204, 928)])
        entries = parse_entries(blocks[0])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].ambiguous, 'range')
        self.assertIsNone(entries[0].quantity)

    def test_block_spanning_two_columns_splits_into_two_entries(self):
        blocks = normalize_blocks([block('1136:1个14:3个', 274, 1363, 628, 1467)])
        entries = parse_entries(blocks[0])
        self.assertEqual([e.code for e in entries], ['1136', '14'])
        self.assertLessEqual(entries[0].bbox[2], entries[1].bbox[0])

    def test_heading_detection_requires_text_without_digits(self):
        blocks = normalize_blocks([block('马蹄肉', 56, 254, 256, 314), block('931:12斤', 50, 307, 203, 364)])
        self.assertTrue(is_heading(blocks[0]))
        self.assertFalse(is_heading(blocks[1]))

    def test_iou_prefers_real_heading_over_narrow_inner_heading(self):
        from handwriting.geometry import TextBlock
        narrow = TextBlock('南姜', (88, 760, 166, 816))
        wide = TextBlock('九层塔', (57, 1207, 210, 1295))
        row = (39, 1282, 208, 1360)
        self.assertLess(iou(narrow.bbox, row), iou(wide.bbox, row))

    def test_heading_belongs_to_nearest_heading_above(self):
        blocks = normalize_blocks([
            block('马里市肉', 78, 133, 229, 191),
            block('九层塔', 57, 1207, 210, 1295),
            block('14:0.5个', 39, 1282, 208, 1360),
        ])
        layout = build_layout(blocks)
        entries = [entry for row in layout.rows for entry in row['entries']]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].heading.text, '九层塔')

    def test_columns_do_not_merge_same_y_rows(self):
        blocks = normalize_blocks([
            block('933:3斤', 67, 183, 203, 250),
            block('粮931:0.5斤()', 240, 164, 557, 253),
        ])
        layout = build_layout(blocks)
        columns = {row['column'] for row in layout.rows if row['entries']}
        self.assertEqual(len(columns), 2)


class EvidenceTests(unittest.TestCase):
    def _layout(self):
        blocks = normalize_blocks([
            block('马蹄肉', 56, 254, 256, 314, confidence=98),
            block('931:12斤', 50, 307, 203, 364, confidence=95),
            block('21:20个', 40, 360, 184, 410, confidence=99),
            block('1368:5个', 81, 899, 238, 972, confidence=94),
            block('子善', 109, 843, 194, 909, confidence=81),
        ])
        return build_layout(blocks)

    def test_match_records_pairs_by_content_not_by_amount(self):
        layout = self._layout()
        records = [{'category': '马蹄肉', 'customer_code': '931', 'weight': '12', 'unit': '斤'},
                   {'category': '马蹄肉', 'customer_code': '21', 'weight': '20', 'unit': '斤'},
                   {'category': '子姜', 'customer_code': '1368', 'weight': '5', 'unit': '斤'}]
        assignment, entries, _used = match_records(records, layout)
        self.assertEqual(len(assignment), 3)
        codes = sorted(entries[index][1].code for index, _ in assignment.values())
        self.assertEqual(codes, ['1368', '21', '931'])

    def test_high_confidence_heading_corrects_category_and_flags(self):
        layout = self._layout()
        records = [{'category': '马齿苋', 'customer_code': '931', 'weight': '12', 'unit': '斤'}]
        page = build_evidence(records, layout, min_heading_confidence=90)
        updated = apply_evidence(records, page)
        self.assertEqual(updated[0]['category'], '马蹄肉')
        self.assertTrue(updated[0]['_uncertain'])
        self.assertTrue(any('品类' in reason for reason in updated[0]['_review_reasons']))

    def test_low_confidence_heading_keeps_model_value_and_keeps_doubt(self):
        blocks = normalize_blocks([
            block('子善', 109, 843, 194, 909, confidence=81),
            block('1368:5个', 81, 899, 238, 972, confidence=94),
        ])
        layout = build_layout(blocks)
        records = [{'category': '王美', 'customer_code': '1368', 'weight': '5', 'unit': '斤'}]
        page = build_evidence(records, layout, min_heading_confidence=90)
        updated = apply_evidence(records, page)
        self.assertEqual(updated[0]['category'], '王美')
        self.assertTrue(updated[0]['_uncertain'])

    def test_truncated_code_is_corrected_from_row_text(self):
        blocks = normalize_blocks([
            block('香矛', 284, 1308, 399, 1389, confidence=93),
            block('1136:1个', 266, 1377, 429, 1461, confidence=97),
        ])
        layout = build_layout(blocks)
        records = [{'category': '香芹', 'customer_code': '136', 'weight': '1', 'unit': '斤'}]
        page = build_evidence(records, layout, min_heading_confidence=90)
        updated = apply_evidence(records, page)
        self.assertEqual(updated[0]['customer_code'], '1136')

    def test_decimal_point_disagreement_is_not_auto_corrected(self):
        blocks = normalize_blocks([block('1369:45斤', 18, 370, 194, 444, confidence=96)])
        layout = build_layout(blocks)
        records = [{'category': '马蹄肉', 'customer_code': '1369', 'weight': '4.5', 'unit': '斤'}]
        page = build_evidence(records, layout, min_heading_confidence=90)
        updated = apply_evidence(records, page)
        self.assertEqual(updated[0]['weight'], '4.5')
        self.assertTrue(any('小数点' in reason for reason in updated[0]['_review_reasons']))

    def test_different_digits_in_quantity_is_corrected(self):
        blocks = normalize_blocks([block('185:0.5斤', 244, 1282, 408, 1370, confidence=97)])
        layout = build_layout(blocks)
        records = [{'category': '鱼腥草', 'customer_code': '185', 'weight': '2.5', 'unit': '斤'}]
        page = build_evidence(records, layout, min_heading_confidence=90)
        updated = apply_evidence(records, page)
        self.assertEqual(updated[0]['weight'], '0.5')

    def test_unit_confusion_never_overwrites_model_value(self):
        blocks = normalize_blocks([block('29:2个', 0, 306, 174, 377, confidence=99)])
        layout = build_layout(blocks)
        records = [{'category': '马蹄肉', 'customer_code': '29', 'weight': '2', 'unit': '斤'}]
        page = build_evidence(records, layout, min_heading_confidence=90)
        updated = apply_evidence(records, page)
        self.assertEqual(updated[0]['unit'], '斤')
        self.assertNotIn('unit', [d['field'] for d in updated[0]['_evidence']['decisions']])

    def test_missing_row_evidence_is_flagged_not_guessed(self):
        blocks = normalize_blocks([block('马蹄肉', 56, 254, 256, 314, confidence=98)])
        layout = build_layout(blocks)
        records = [{'category': '马蹄肉', 'customer_code': '999', 'weight': '3', 'unit': '斤'}]
        page = build_evidence(records, layout, min_heading_confidence=90)
        updated = apply_evidence(records, page)
        self.assertTrue(updated[0]['_uncertain'])
        self.assertTrue(any('缺少同行证据' in reason for reason in updated[0]['_review_reasons']))
        self.assertEqual(updated[0]['customer_code'], '999')

    def test_candidates_keep_every_source_side_by_side(self):
        layout = self._layout()
        records = [{'category': '马齿苋', 'customer_code': '931', 'weight': '12', 'unit': '斤'}]
        page = build_evidence(records, layout, min_heading_confidence=90)
        row = page.rows[0]
        sources = {c.source for c in row.candidates['category']}
        self.assertEqual(sources, {'model', 'ocr_heading'})
        self.assertEqual({c.source for c in row.candidates['customer_code']}, {'model', 'ocr_row'})

    def test_confirmed_row_is_not_overwritten(self):
        layout = self._layout()
        records = [{'category': '马齿苋', 'customer_code': '931', 'weight': '12', 'unit': '斤',
                    '_user_confirmed': True}]
        page = build_evidence(records, layout, min_heading_confidence=90)
        updated = apply_evidence(records, page)
        self.assertEqual(updated[0]['category'], '马齿苋')

    def test_unmatched_entries_are_reported_as_page_note(self):
        layout = self._layout()
        records = [{'category': '马蹄肉', 'customer_code': '931', 'weight': '12', 'unit': '斤'}]
        page = build_evidence(records, layout, min_heading_confidence=90)
        self.assertTrue(page.unmatched_entries)
        self.assertTrue(any('没对上' in note for note in page.notes))


class GateTests(unittest.TestCase):
    def test_cross_check_cannot_clear_evidence_chain_doubt(self):
        records = [{'category': '马蹄肉', 'customer_code': '931', 'weight': '12', 'unit': '斤',
                    '_uncertain': True, '_review_reasons': ['品类两路不一致']}]

        def cross_check(items, blocks):
            items[0].pop('_uncertain', None)
            items[0].pop('_review_reasons', None)
            return ''

        text, notes = guard_cross_check(records, cross_check, [])
        self.assertTrue(records[0]['_uncertain'])
        self.assertTrue(records[0]['_review_reasons'])
        self.assertTrue(notes)

    def test_category_boundary_change_is_blocked_when_row_has_evidence(self):
        records = [{'category': '甲', 'customer_code': '1', 'weight': '2', 'unit': '斤',
                    '_evidence': {'entry_text': '1:2斤'}}]

        def fixer(items, blocks):
            items[0]['category'] = '乙'
            return 1

        changed, notes = guard_category_boundaries(records, [], fixer)
        self.assertEqual(records[0]['category'], '甲')
        self.assertEqual(changed, 1)
        self.assertTrue(notes)

    def test_pre_decision_snapshot_is_saved(self):
        records = [{'category': '甲', 'customer_code': '1', 'weight': '2', 'unit': '斤'}]

        def fixer(items, blocks):
            items[0]['weight'] = '9'
            return 1

        guard_category_boundaries(records, [], fixer)
        self.assertEqual(records[0]['_pre_decision']['weight'], '2')


if __name__ == '__main__':
    unittest.main()
