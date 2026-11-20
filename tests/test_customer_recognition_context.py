import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from app import customer_profiles as CP
from core import ttsb2


class CustomerContextTests(unittest.TestCase):
    def test_handwritten_profile_supplies_layout_candidates_and_raw_note(self):
        profile = dict(CP.get_profile('农和'))
        profile['extra_note'] = '编码在括号里。\n数量在左侧。'
        prompt = ttsb2.build_prompt(profile)
        self.assertIn('不得跨栏借用标题', prompt)
        self.assertIn('姜肉', prompt)
        self.assertTrue(prompt.endswith(profile['extra_note']))
        self.assertNotEqual(prompt, ttsb2.build_prompt(None))

    def test_blank_note_preserves_prompt(self):
        profile = dict(CP.get_profile('中惠'))
        profile.pop('extra_note', None)
        expected = ttsb2.build_prompt(profile)
        for note in ('', ' \n ', None):
            with self.subTest(note=note):
                profile['extra_note'] = note
                self.assertEqual(ttsb2.build_prompt(profile), expected)

    def test_printed_customer_note_is_sent_with_image(self):
        profile = dict(CP.get_profile('中惠'), extra_note='括号内是客户编码。\n先看客户明细。')
        prompt = ttsb2.build_prompt(profile)
        response = {'choices': [{'message': {'content': '{"records": [], "notes": []}'}}]}
        with TemporaryDirectory() as directory:
            image = Path(directory) / 'sample.png'
            image.write_bytes(b'synthetic image bytes')
            with patch.object(ttsb2, '取密钥', return_value='test-key'), \
                 patch.object(ttsb2, '_debug_log'), \
                 patch('urllib.request.urlopen') as opener:
                opener.return_value.__enter__.return_value.read.return_value = json.dumps(response).encode()
                ttsb2.deepseek_parse(image, system_prompt=prompt)
        payload = json.loads(opener.call_args.args[0].data)
        self.assertTrue(payload['messages'][0]['content'].endswith(profile['extra_note']))
        self.assertEqual(payload['messages'][1]['content'][0]['type'], 'image_url')

    def test_printing_exclusions_cannot_delete_recognized_rows(self):
        prompt = ttsb2.build_prompt(CP.get_profile('南波旺'))
        self.assertIn('识别阶段不要据此删行', prompt)
        self.assertIn('识别阶段仍完整提取图上编码', prompt)

    def test_hints_match_reference_meaning(self):
        self.assertIn('手写单', CP.customer_hint('农和', CP.get_profile('农和')))
        hint = CP.customer_hint('南波旺', CP.get_profile('南波旺'))
        self.assertIn('不打客户编码', hint)
        self.assertIn('整条跳过', hint)
