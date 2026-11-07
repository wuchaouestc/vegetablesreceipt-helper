import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from handwriting.providers import tencent_ocr, deepseek_parse, probe_deepseek


class Response:
    def __init__(self, body):
        self.body = body
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self):
        return json.dumps(self.body).encode()


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.image = Path(self.temp.name) / 'misleading.png'
        self.image.write_bytes(b'\xff\xd8\xfftest-image')
        self.calls = []
    def opener(self, body):
        def call(req, timeout):
            self.calls.append((req, timeout))
            return Response(body)
        return call
    def ocr(self, body, **kwargs):
        return tencent_ocr(self.image, key_reader=lambda _: 'test-key', opener=self.opener(body), **kwargs)
    def model(self, body, **kwargs):
        return deepseek_parse(self.image, model='explicit-test-model', key_reader=lambda _: 'test-key', opener=self.opener(body), **kwargs)
    def test_complete_ocr_evidence_and_explicit_action(self):
        polygon = [{'X': 1, 'Y': 2}, {'X': 8, 'Y': 2}, {'X': 8, 'Y': 7}, {'X': 1, 'Y': 7}]
        result = self.ocr({'Response': {'TextDetections': [{'DetectedText': '0.5', 'Polygon': polygon, 'Confidence': 92}], 'RequestId': 'req-123'}}, action='GeneralHandwritingOCR')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['blocks'][0]['polygon'], polygon)
        self.assertEqual(result['blocks'][0]['confidence'], 92)
        self.assertEqual(result['request_id'], 'req-123')
        self.assertEqual(self.calls[0][0].get_header('X-tc-action'), 'GeneralHandwritingOCR')
    def test_empty_and_business_failure_are_distinct(self):
        self.assertEqual(self.ocr({'Response': {'TextDetections': []}})['status'], 'empty')
        result = self.ocr({'Response': {'Error': {'Code': 'AuthFailure', 'Message': 'secret private message'}}})
        self.assertEqual(result['error_code'], 'business:AuthFailure')
        self.assertNotIn('secret', json.dumps(result))
    def test_malformed_response_not_empty(self):
        for body in ({}, {'Response': {}}, {'Response': {'TextDetections': None}}):
            self.assertEqual(self.ocr(body)['error_code'], 'invalid_response')
    def test_network_http_failure_no_retry_no_secret(self):
        for error, code in [(TimeoutError('secret'), 'network_error'),
                            (urllib.error.HTTPError('url', 401, 'secret', {}, None), 'http_401')]:
            calls = []
            def fail(*args, **kwargs):
                calls.append(1)
                raise error
            result = tencent_ocr(self.image, key_reader=lambda _: 'test-key', opener=fail)
            self.assertEqual(result['error_code'], code)
            self.assertEqual(len(calls), 1)
            self.assertNotIn('secret', json.dumps(result))
    def test_model_content_mime_and_usage(self):
        result = self.model({'id': 'req-1', 'usage': {'total_tokens': 50, 'unexpected': 'secret'}, 'choices': [{'finish_reason': 'stop', 'message': {'content': '{"records":[{"编码":"001"}],"notes":["待核对"]}'}}]}, timeout=17)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['records'][0]['编码'], '001')
        self.assertEqual(result['usage'], {'total_tokens': 50})
        request, timeout = self.calls[0]
        payload = json.loads(request.data)
        self.assertEqual(timeout, 17)
        self.assertEqual(payload['model'], 'explicit-test-model')
        self.assertEqual(payload['max_tokens'], 4096)
        self.assertTrue(payload['messages'][1]['content'][0]['image_url']['url'].startswith('data:image/jpeg;base64,'))
    def test_truncation_and_malformed_json_not_success(self):
        for choice, code in [({'finish_reason': 'length', 'message': {'content': '{"records":[]}'}}, 'incomplete_response'),
                             ({'message': {'content': 'not json'}}, 'invalid_response'),
                             ({'message': {'content': '{"records":{}}'}}, 'invalid_response')]:
            result = self.model({'choices': [choice]})
            self.assertEqual(result['error_code'], code)
    def test_invalid_input_and_missing_credentials_never_send(self):
        def fail(*args, **kwargs):
            self.fail('must not send')
        self.assertEqual(tencent_ocr(self.image, key_reader=lambda _: None, opener=fail)['error_code'], 'missing_credentials')
        self.assertEqual(tencent_ocr(self.image, action='unknown', opener=fail)['error_code'], 'unsupported_action')
        self.assertEqual(deepseek_parse(self.image, model='', opener=fail)['error_code'], 'missing_model')
    def test_explicit_probe_can_return_empty_without_claiming_accuracy(self):
        result = probe_deepseek(self.image, model='test', key_reader=lambda _: 'key', opener=self.opener({'choices': [{'message': {'content': '{"records":[],"notes":["看不清"]}'}}]}))
        self.assertEqual(result['status'], 'empty')
        self.assertEqual(result['notes'], ['看不清'])
        self.assertEqual(json.loads(self.calls[0][0].data)['max_tokens'], 256)

    def test_invalid_timeouts_do_not_send(self):
        for value in (True, False, float('nan'), float('inf'), -1, 0, '30'):
            with self.subTest(value=value):
                result = self.ocr({'Response': {'TextDetections': []}}, timeout=value)
                self.assertEqual(result['error_code'], 'invalid_timeout')
        self.assertEqual(self.calls, [])

    def test_token_limits_are_explicit_and_bounded(self):
        for value in (True, 127, 16385, 256.0, float('nan')):
            self.assertEqual(self.model({}, max_tokens=value)['error_code'], 'invalid_max_tokens')
        self.assertEqual(self.calls, [])
        self.model({'choices': [{'message': {'content': '{"records":[]}'}}]}, max_tokens=128)
        self.assertEqual(json.loads(self.calls[0][0].data)['max_tokens'], 128)

    def test_nonfinite_geometry_and_confidence_rejected(self):
        for value in (float('nan'), float('inf'), float('-inf')):
            for item in ({'DetectedText': '1', 'Polygon': [{'X': value, 'Y': 0}]},
                         {'DetectedText': '1', 'Confidence': value}):
                self.assertEqual(self.ocr({'Response': {'TextDetections': [item]}})['error_code'], 'invalid_response')

    def test_image_size_limits_prevent_network(self):
        with self.image.open('wb') as stream:
            stream.write(b'\xff\xd8\xff')
            stream.truncate((10 * 1024 * 1024 // 4) * 3 + 1)
        self.assertEqual(self.ocr({})['error_code'], 'image_too_large')
        with self.image.open('r+b') as stream:
            stream.truncate(32 * 1024 * 1024 + 1)
        self.assertEqual(self.model({})['error_code'], 'image_too_large')
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
