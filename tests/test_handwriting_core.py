import copy
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from handwriting.dataset import prepare, inventory, annotation_audit
from handwriting.evaluation import score_records, score_regions, region_text
from handwriting.experiments import Budget, BudgetExceeded, ExperimentRunner
from handwriting.preprocess import original_image
from handwriting.storage import ResponseCache, digest, write_json, read_json


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.image = self.root / "a.png"
        Image.new("RGB", (300, 400), "white").save(self.image)

    def tearDown(self):
        self.temp.cleanup()

    def test_inventory_deduplicates_pixels_and_does_not_overwrite_annotation(self):
        Image.open(self.image).save(self.root / "b.bmp")
        first = prepare(self.root, self.root / "manifest.json", self.root / "annotations")
        self.assertEqual(first["file_count"], 2)
        self.assertEqual(first["unique_pixels"], 1)
        self.assertEqual(len({e["source_group"] for e in first["entries"]}), 1)
        annotation = next((self.root / "annotations").glob("*.json"))
        annotation.write_text('{"manual":"keep"}', encoding="utf-8")
        prepare(self.root, self.root / "manifest.json", self.root / "annotations")
        self.assertEqual(read_json(annotation), {"manual": "keep"})

    def test_original_unchanged_and_enhancement_not_silently_enabled(self):
        before = digest(self.image)
        result = original_image(self.image)
        self.assertEqual(result.sha256, before)
        self.assertEqual(result.mime, "image/png")
        with self.assertRaises(ValueError):
            original_image(self.image, enhancement=True)
        self.assertEqual(digest(self.image), before)

    def test_budget_cost_unknown_and_time_and_requests(self):
        clock = [10]
        budget = Budget(1, 5, clock=lambda: clock[0])
        budget.reserve()
        with self.assertRaises(BudgetExceeded): budget.reserve()
        clock[0] = 20
        self.assertEqual(budget.remaining_seconds, 0)
        with self.assertRaises(BudgetExceeded): Budget(max_cost=1).reserve()
        budget = Budget(max_cost=1)
        budget.reserve(.8)
        with self.assertRaises(BudgetExceeded): budget.reserve(.3)

    def test_failed_calls_consume_budget_but_are_not_cached(self):
        calls = []
        def provider(path, timeout):
            calls.append(timeout)
            return {"status": "error", "error_code": "timeout"}
        runner = ExperimentRunner(self.root / "run", Budget(2, 100))
        runner.call(self.image, provider, {"action": "A"})
        runner.call(self.image, provider, {"action": "A"})
        self.assertEqual(len(calls), 2)
        with self.assertRaises(BudgetExceeded): runner.call(self.image, provider, {"action": "A"})

    def test_cache_separates_interfaces_parameters_and_preserves_cold_provenance(self):
        calls = []
        def provider(path, timeout):
            calls.append(1)
            return {"status": "ok", "blocks": [{"text": "001:2.5斤"}]}
        runner = ExperimentRunner(self.root / "run")
        first = runner.call(self.image, provider, {"action": "A", "version": 1})
        second = runner.call(self.image, provider, {"action": "A", "version": 1})
        runner.call(self.image, provider, {"action": "B", "version": 1})
        runner.call(self.image, provider, {"action": "A", "version": 2})
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(list((self.root / 'run/responses').glob('*.replay.json'))), 1)

    def test_draft_not_accuracy_and_duplicate_rows_are_not_collapsed(self):
        rows = [{"row_id": str(i), "category": "新品", "customer_code": "001", "weight": "2.5", "unit": "袋"} for i in range(2)]
        annotation = {"records": rows, "complete": True, "review_status": "draft_visual"}
        self.assertEqual(score_records(annotation, rows)["status"], "not_evaluable")
        annotation['review_status'] = 'confirmed'
        prediction = [copy.deepcopy(rows[0]), copy.deepcopy(rows[0])]
        scored = score_records(annotation, prediction)
        self.assertEqual(scored['recall'], .5)
        self.assertEqual(scored['precision'], .5)
        self.assertEqual(scored['unflagged_errors'], 1)
        prediction[1] = copy.deepcopy(rows[1])
        prediction[1]['customer_code'] = '1'
        self.assertEqual(score_records(annotation, prediction)['field_correct']['customer_code'], 1)

    def test_region_does_not_borrow_numbers_from_other_rows(self):
        blocks = [{'text': '001:2.5斤', 'polygon': [[0, 0], [50, 0], [50, 20], [0, 20]]},
                  {'text': '002:9斤', 'polygon': [[0, 50], [50, 50], [50, 70], [0, 70]]}]
        self.assertEqual(region_text(blocks, [0, 0, 60, 30]), '001:2.5斤')
        ann = {'review_status': 'draft_visual', 'regions': [{'region_id': 'r1', 'bbox': [0, 0, 60, 30], 'text': '001:2.5斤'}]}
        self.assertEqual(score_regions(ann, blocks)['status'], 'not_evaluable')
        self.assertEqual(score_regions(ann, blocks, True)['cer'], 0)

    def test_annotation_freeze_rejects_drafts_and_cross_split_source(self):
        write_json(self.root / 'a.json', {'image_sha256': 'a', 'source_group': 'same', 'split': 'development',
                   'review_status': 'confirmed', 'complete': True})
        write_json(self.root / 'b.json', {'image_sha256': 'b', 'source_group': 'same', 'split': 'acceptance',
                   'review_status': 'draft_visual', 'complete': False})
        report = annotation_audit(self.root)
        self.assertFalse(report['acceptance_ready'])
        self.assertEqual(len(report['issues']), 2)


if __name__ == '__main__': unittest.main()
