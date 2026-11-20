import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from handwriting.diagnostics import trace


class DiagnosticsTests(unittest.TestCase):
    def test_stage_summary_does_not_mutate_or_log_business_values(self):
        records = [{'customer_code': 'PRIVATE_CODE', 'weight': '12345',
                    '_uncertain': True}]
        before = copy.deepcopy(records)
        with tempfile.TemporaryDirectory() as folder:
            日志目录 = Path(folder) / 'log'
            日志目录.mkdir(parents=True, exist_ok=True)
            with patch('core.日志.日志目录', return_value=日志目录):
                trace(SimpleNamespace(task_id='task'), 'model_raw', records=records)
            text = next((Path(folder) / 'log').glob('*.log')).read_text(encoding='utf-8')
            payload = json.loads(text.split('[手写阶段] ', 1)[1])
            self.assertEqual(payload['field_present']['customer_code'], 1)
            self.assertNotIn('PRIVATE_CODE', text)
            self.assertNotIn('12345', text)
        self.assertEqual(records, before)

    def test_io_failure_cannot_break_recognition(self):
        with patch('core.日志.日志目录', side_effect=OSError('unavailable')):
            trace(SimpleNamespace(task_id='task'), 'start', records=[None])

    def test_actual_model_and_review_regions_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            日志目录 = Path(folder) / 'log'
            日志目录.mkdir(parents=True, exist_ok=True)
            with patch('core.日志.日志目录', return_value=日志目录):
                for index in (0, 1):
                    trace(SimpleNamespace(task_id='task', model='deepseek-reasoner'), 'review_raw',
                          records=[{'category': '老姜', 'unit': '斤'}],
                          region_index=index, model='deepseek-chat')
            details = sorted((Path(folder) / 'log/手写明细/task').glob('*.json'))
            self.assertEqual(len(details), 2)
            data = json.loads(details[0].read_text(encoding='utf-8'))
            self.assertEqual(data['model'], 'deepseek-chat')
            self.assertEqual(data['rows'][0]['category'], '老姜')
