# -*- coding: utf-8 -*-
"""核对网页本地服务的测试：路由、写盘边界、只听本机。"""
import json
import shutil
import tempfile
import unittest
import urllib.request
from pathlib import Path

from handwriting.review_web import build_session, serve


def _record(row_id='row-0001'):
    return {'category': '马步鱼', 'customer_code': '935', 'weight': '3', 'unit': '斤',
            'remark': '', '_review_reasons': ['品类两路不一致'],
            '_evidence': {'row_id': row_id, 'bbox': [10, 20, 60, 60],
                          'heading_text': '马蹄肉', 'entry_text': '935:3斤',
                          'candidates': {}, 'decisions': []}}


def _make_image(path):
    try:
        from PIL import Image
    except ImportError:
        return False
    Image.new('RGB', (200, 200), 'white').save(path)
    return True


class ReviewWebTests(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.image = self.root / 'sample.jpg'
        if not _make_image(self.image):
            self.skipTest('缺少 Pillow')
        self.annotations = self.root / 'annotations'
        self.vocabulary = self.root / 'dict' / 'handwriting.json'
        self.examples = self.root / 'learning' / 'examples.json'
        self.session = build_session(
            self.image, [_record()], image_sha256='abc',
            annotations_dir=self.annotations, vocabulary_path=self.vocabulary,
            examples_path=self.examples, image_root=self.root, task_id='t1')
        self.server, self.url = serve(self.session, port=0, background=True)

    def tearDown(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass
        shutil.rmtree(self.root, ignore_errors=True)

    def _get(self, path):
        try:
            with urllib.request.urlopen(self.url.rstrip('/') + path, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def _post(self, path, payload):
        request = urllib.request.Request(
            self.url.rstrip('/') + path,
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode('utf-8'))

    def test_page_is_served(self):
        status, body = self._get('/')
        self.assertEqual(200, status)
        self.assertIn('手写单据核对', body.decode('utf-8'))

    def test_payload_endpoint_returns_rows(self):
        status, body = self._get('/api/payload')
        self.assertEqual(200, status)
        data = json.loads(body.decode('utf-8'))
        self.assertEqual(1, len(data['rows']))
        self.assertEqual('row-0001', data['rows'][0]['row_id'])

    def test_image_endpoint_serves_the_original(self):
        status, body = self._get('/api/image')
        self.assertEqual(200, status)
        self.assertTrue(body.startswith(b'\xff\xd8') or body.startswith(b'\x89PNG'))

    def test_crop_endpoint_serves_the_row_region(self):
        status, body = self._get('/api/crop?row=row-0001')
        self.assertEqual(200, status)
        self.assertTrue(body.startswith(b'\x89PNG'))

    def test_unknown_crop_row_is_404(self):
        status, body = self._get('/api/crop?row=nope')
        self.assertEqual(404, status)

    def test_unknown_path_is_404(self):
        status, _body = self._get('/api/whatever')
        self.assertEqual(404, status)

    def test_confirm_writes_annotation_and_learns(self):
        payload = json.loads(self._get('/api/payload')[1].decode('utf-8'))
        payload['reviewer'] = '张三'
        payload['rows'][0]['final']['category'] = '马蹄肉'
        status, result = self._post('/api/confirm', payload)
        self.assertEqual(200, status)
        self.assertTrue(result['ok'])
        self.assertTrue(Path(result['result']['annotation_path']).exists())
        self.assertTrue(self.vocabulary.exists())
        self.assertTrue(self.examples.exists())
        self.assertEqual(1, len(result['result']['vocabulary']['changed']))

    def test_confirm_rejects_missing_reviewer(self):
        payload = json.loads(self._get('/api/payload')[1].decode('utf-8'))
        status, result = self._post('/api/confirm', payload)
        self.assertEqual(400, status)
        self.assertIn('复核人', result['error'])
        self.assertFalse((self.annotations / 'abc.json').exists())

    def test_confirm_rejects_blank_required_field(self):
        payload = json.loads(self._get('/api/payload')[1].decode('utf-8'))
        payload['reviewer'] = '张三'
        payload['rows'][0]['final']['weight'] = '  '
        status, result = self._post('/api/confirm', payload)
        self.assertEqual(400, status)
        self.assertIn('数量', result['error'])

    def test_server_refuses_non_local_host(self):
        from handwriting.review_web import serve as serve_fn
        with self.assertRaises(ValueError):
            serve_fn(self.session, host='0.0.0.0', port=0, background=True)


if __name__ == '__main__':
    unittest.main()
