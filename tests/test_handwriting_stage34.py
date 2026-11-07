"""阶段 4 测试：统一预算、局部复核、模式切换、原图对照、冻结验收报告。"""
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from handwriting.budget import UnifiedBudget
from handwriting.compare import build_comparison, comparison_rows
from handwriting.evidence import apply_evidence, build_evidence
from handwriting.frozen import compare, render_markdown, score_image
from handwriting.geometry import build_layout, normalize_blocks
from handwriting.modes import (MODE_ASSISTED, MODE_AUTO, MODE_LEGACY, MODE_PER_IMAGE,
                               mode_for_image, resolve_mode)
from handwriting.review import (ReviewToken, apply_review_results, crop_region, plan_regions,
                                row_version, validate_late, write_region_manifest)


def block(text, x0, y0, x1, y1, confidence=95.0):
    polygon = [{'X': x0, 'Y': y0}, {'X': x1, 'Y': y0}, {'X': x1, 'Y': y1}, {'X': x0, 'Y': y1}]
    return {'text': text, 'polygon': polygon, 'confidence': confidence}


class BudgetTests(unittest.TestCase):
    def test_request_limit_stops_further_calls(self):
        budget = UnifiedBudget(max_requests=2, max_seconds=60)
        self.assertTrue(budget.reserve('ocr'))
        self.assertTrue(budget.reserve('model'))
        self.assertFalse(budget.reserve('review'))
        self.assertIn('请求数', budget.stopped_reason)

    def test_time_limit_stops_further_calls(self):
        clock = [0.0]
        budget = UnifiedBudget(max_requests=10, max_seconds=5, clock=lambda: clock[0])
        clock[0] = 6.0
        self.assertFalse(budget.reserve('model'))
        self.assertIn('时限', budget.stopped_reason)

    def test_cost_limit_requires_upper_bound(self):
        budget = UnifiedBudget(max_requests=10, max_seconds=60, max_cost=1.0)
        self.assertFalse(budget.reserve('model'))
        self.assertIn('费用', budget.stopped_reason)

    def test_cost_limit_blocks_when_upper_bound_exceeds(self):
        budget = UnifiedBudget(max_requests=10, max_seconds=60, max_cost=1.0)
        self.assertTrue(budget.reserve('model', upper_cost=0.5))
        self.assertFalse(budget.reserve('model', upper_cost=0.8))

    def test_quota_limits_review_regions(self):
        budget = UnifiedBudget(max_requests=10, max_seconds=60)
        self.assertTrue(budget.reserve('review', quota=('review_regions', 2)))
        self.assertTrue(budget.reserve('review', quota=('review_regions', 2)))
        self.assertFalse(budget.reserve('review', quota=('review_regions', 2)))

    def test_usage_report_is_serialisable(self):
        budget = UnifiedBudget(max_requests=3, max_seconds=30)
        budget.reserve('ocr')
        usage = budget.usage()
        self.assertEqual(usage['requests'], 1)
        self.assertEqual(usage['by_kind'], {'ocr': 1})


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.image = self.root / '原图.png'
        Image.new('RGB', (400, 600), 'white').save(self.image)

    def tearDown(self):
        self.temp.cleanup()

    def _page(self):
        blocks = normalize_blocks([
            block('马蹄肉', 56, 254, 256, 314, confidence=98),
            block('931:12斤', 50, 307, 203, 364, confidence=95),
            block('21:20个', 40, 360, 184, 410, confidence=99),
        ])
        layout = build_layout(blocks)
        records = [{'category': '马齿苋', 'customer_code': '931', 'weight': '12', 'unit': '斤'},
                   {'category': '马齿苋', 'customer_code': '21', 'weight': '20', 'unit': '斤'}]
        page = build_evidence(records, layout, min_heading_confidence=90)
        return layout, records, page

    def test_plan_regions_groups_neighbouring_rows_and_keeps_heading(self):
        layout, records, page = self._page()
        regions = plan_regions(page, layout, max_regions=2)
        self.assertTrue(regions)
        region = regions[0]
        self.assertTrue(region.row_ids)
        self.assertIsNotNone(region.heading_bbox)

    def test_crop_region_writes_image_and_manifest(self):
        layout, records, page = self._page()
        regions = plan_regions(page, layout, max_regions=1)
        crop = crop_region(self.image, regions[0].bbox, self.root / '复核', 'region-01')
        self.assertTrue(crop and Path(crop).is_file())
        manifest = write_region_manifest(self.root / 'regions.json', regions, self.image, 'task-1')
        self.assertTrue(Path(manifest).is_file())

    def test_late_response_with_changed_row_version_is_discarded(self):
        layout, records, page = self._page()
        row = page.rows[0]
        token = ReviewToken('task-1', 'img-1', row_version(row))
        self.assertTrue(validate_late(token, row_version(row))[0])
        self.assertFalse(validate_late(token, 'other-version')[0])
        self.assertFalse(validate_late(ReviewToken('task-2', 'img-1', row_version(row)),
                                        row_version(row), task_id='task-1')[0])
        self.assertFalse(validate_late(ReviewToken('task-1', 'img-2', row_version(row)),
                                        row_version(row), image_version='img-1')[0])

    def test_review_agreeing_with_row_text_is_adopted(self):
        layout, records, page = self._page()
        row = page.rows[0]
        token = ReviewToken('task-1', 'img-1', row_version(row))
        results = [{'region': plan_regions(page, layout, max_regions=1)[0],
                    'tokens': {row.row_id: token},
                    'values': {row.row_id: {'category': '马蹄肉'}},
                    'raw_text': '马蹄肉 931:12斤'}]
        apply_review_results(page, results, records=records)
        updated = apply_evidence(records, page)
        self.assertEqual(updated[0]['category'], '马蹄肉')

    def test_review_conflicting_with_both_is_kept_as_doubt(self):
        layout, records, page = self._page()
        row = page.rows[0]
        token = ReviewToken('task-1', 'img-1', row_version(row))
        results = [{'region': plan_regions(page, layout, max_regions=1)[0],
                    'tokens': {row.row_id: token},
                    'values': {row.row_id: {'category': '第三种写法'}},
                    'raw_text': ''}]
        apply_review_results(page, results, records=records)
        updated = apply_evidence(records, page)
        self.assertTrue(updated[0]['_uncertain'])
        self.assertTrue(any('局部复核' in reason for reason in updated[0]['_review_reasons']))

    def test_review_does_not_overwrite_confirmed_row(self):
        layout, records, page = self._page()
        row = page.rows[0]
        records[0]['_user_confirmed'] = True
        token = ReviewToken('task-1', 'img-1', row_version(row))
        results = [{'region': plan_regions(page, layout, max_regions=1)[0],
                    'tokens': {row.row_id: token},
                    'values': {row.row_id: {'category': '别覆盖我'}},
                    'raw_text': ''}]
        apply_review_results(page, results, records=records)
        self.assertTrue(any('已确认' in note for note in page.notes))


class ModeTests(unittest.TestCase):
    def test_default_is_auto_judgement(self):
        """合入后默认是「自动判别」：每张图先判是不是手写单据。

        阶段 4 时默认是「原流程」（首次升级行为不变），合入手写识别库后
        改成自动分流 —— 手写单据自动走手写库，印刷单据自动走原流程。
        判不准时退回原流程，两条通道都保留疑点，不会印错。
        """
        config = resolve_mode({})
        self.assertEqual(config.mode, MODE_AUTO)
        self.assertFalse(config.assisted)

    def test_explicit_legacy_overrides_auto_default(self):
        """想强制走原流程时，界面上的选择仍然管用。"""
        config = resolve_mode({}, explicit=MODE_LEGACY)
        self.assertEqual(config.mode, MODE_LEGACY)

    def test_explicit_choice_overrides_settings(self):
        config = resolve_mode({'手写辅助模式': '关'}, explicit=MODE_ASSISTED)
        self.assertEqual(config.mode, MODE_ASSISTED)

    def test_per_image_mode_defaults_to_legacy_without_choice(self):
        config = resolve_mode({'手写辅助模式': '逐张'})
        self.assertEqual(config.mode, MODE_PER_IMAGE)
        self.assertEqual(mode_for_image(config, 'a.jpg'), MODE_LEGACY)
        self.assertEqual(mode_for_image(config, 'a.jpg', {'a.jpg': MODE_ASSISTED}), MODE_ASSISTED)

    def test_explicit_per_image_choice_overrides_settings(self):
        config = resolve_mode({}, explicit=MODE_PER_IMAGE)
        self.assertEqual(config.mode, MODE_PER_IMAGE)

    def test_budget_and_threshold_come_from_settings(self):
        config = resolve_mode({'手写预算请求数': '4', '手写预算秒数': '30', '手写标题置信阈值': '75'})
        self.assertEqual(config.budget_requests, 4)
        self.assertEqual(config.budget_seconds, 30)
        self.assertEqual(config.heading_confidence, 75.0)

    def test_invalid_values_fall_back_to_defaults(self):
        config = resolve_mode({'手写预算请求数': 'abc', '手写标题置信阈值': '999'})
        self.assertEqual(config.budget_requests, 8)
        self.assertEqual(config.heading_confidence, 90.0)


class CompareTests(unittest.TestCase):
    def test_comparison_shows_region_text_candidates_and_suggestion(self):
        blocks = normalize_blocks([
            block('马蹄肉', 56, 254, 256, 314, confidence=98),
            block('931:12斤', 50, 307, 203, 364, confidence=95),
        ])
        layout = build_layout(blocks)
        records = [{'category': '马齿苋', 'customer_code': '931', 'weight': '12', 'unit': '斤'}]
        page = build_evidence(records, layout, min_heading_confidence=90)
        updated = apply_evidence(records, page)
        view = build_comparison(updated[0], image_path=None)
        self.assertEqual(view['heading_text'], '马蹄肉')
        self.assertEqual(view['entry_text'], '931:12斤')
        self.assertTrue(view['candidates'])
        self.assertIn('建议', view['suggestion'])
        self.assertEqual(len(comparison_rows(updated)), 1)


class FrozenTests(unittest.TestCase):
    def _annotation(self):
        return {'image_path': 'a.jpg', 'image_sha256': 'sha', 'review_status': 'confirmed',
                'complete': True,
                'records': [{'row_id': 'r1', 'category': '马蹄肉', 'customer_code': '931',
                             'weight': '12', 'unit': '斤', 'prediction_index': 0},
                            {'row_id': 'r2', 'category': '鱼腥草', 'customer_code': '185',
                             'weight': '0.5', 'unit': '斤', 'prediction_index': 1}]}

    def test_score_image_counts_field_and_row_accuracy(self):
        annotation = self._annotation()
        predictions = [{'category': '马蹄肉', 'customer_code': '931', 'weight': '12', 'unit': '斤'},
                       {'category': '金针菇', 'customer_code': '185', 'weight': '2.5', 'unit': '斤'}]
        score = score_image(annotation, predictions)
        self.assertEqual(score['field_errors']['category'], 1)
        self.assertEqual(score['field_errors']['weight'], 1)
        self.assertEqual(score['row_accuracy'], 0.5)
        self.assertEqual(score['unflagged_errors'], 1)

    def test_flagged_error_is_not_counted_as_unflagged(self):
        annotation = self._annotation()
        predictions = [{'category': '马蹄肉', 'customer_code': '931', 'weight': '12', 'unit': '斤'},
                       {'category': '金针菇', 'customer_code': '185', 'weight': '2.5', 'unit': '斤',
                        '_uncertain': True, '_review_reasons': ['品类两路不一致']}]
        score = score_image(annotation, predictions)
        self.assertEqual(score['unflagged_errors'], 0)

    def test_compare_reports_reduction_and_gates(self):
        annotation = self._annotation()
        baseline = score_image(annotation, [{'category': '马步鱼', 'customer_code': '931',
                                             'weight': '12', 'unit': '斤'},
                                            {'category': '金针菇', 'customer_code': '185',
                                             'weight': '2.5', 'unit': '斤'}])
        assisted = score_image(annotation, [{'category': '马蹄肉', 'customer_code': '931',
                                             'weight': '12', 'unit': '斤',
                                             '_uncertain': True, '_review_reasons': ['品类两路不一致']},
                                            {'category': '鱼腥草', 'customer_code': '185',
                                             'weight': '0.5', 'unit': '斤',
                                             '_uncertain': True, '_review_reasons': ['数量两路不一致']}])
        report = compare([baseline], [assisted])
        self.assertEqual(report['baseline']['field_error_total'], 3)
        self.assertEqual(report['assisted']['field_error_total'], 0)
        self.assertTrue(report['gates']['key_error_reduction_pass'])
        self.assertTrue(report['gates']['no_new_unflagged_errors'])
        self.assertEqual(len(report['outcomes']['fixed_rows']), 2)
        self.assertEqual(report['outcomes']['broken_rows'], [])
        self.assertIn('冻结验收报告', render_markdown(report))


if __name__ == '__main__':
    unittest.main()
