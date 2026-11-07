"""Opt-in experiment adapters. No retries, caches, logging or production changes.

Credentials are loaded lazily through the application's existing resolver.
Transport exceptions and response bodies are never copied into diagnostics.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import time
import urllib.error
import urllib.request
from pathlib import Path


def _key(name):
    from ttsb2 import 取密钥
    return 取密钥(name)


def _result(provider, start, **values):
    result = dict(provider=provider, status='error', blocks=[], records=[], notes=[],
                  error_code='', request_id='', usage={}, elapsed_seconds=round(time.monotonic()-start, 4))
    result.update(values)
    return result


def _identifier(value):
    # Do not accept free-form service messages as diagnostic identifiers.
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', value) else ''


def _request(request, timeout, opener):
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        return None, 'invalid_timeout', ''
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            body = json.loads(response.read().decode('utf-8'))
        return body, '', ''
    except urllib.error.HTTPError as exc:
        # HTTP status alone is safe; body may echo input/credentials.
        return None, 'http_%s' % exc.code, ''
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, 'network_error', ''
    except (ValueError, UnicodeError, TypeError):
        return None, 'invalid_response', ''
    except Exception:
        return None, 'transport_error', ''


def _image(path, max_bytes=32 * 1024 * 1024):
    # Bounded read also handles files that grow between stat and open.
    with Path(path).open('rb') as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError('image_too_large')
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        mime = 'image/png'
    elif data.startswith(b'\xff\xd8\xff'):
        mime = 'image/jpeg'
    elif data.startswith((b'GIF87a', b'GIF89a')):
        mime = 'image/gif'
    elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        mime = 'image/webp'
    else:
        raise ValueError('unsupported_image_format')
    return base64.b64encode(data).decode('ascii'), mime


def tencent_ocr(image_path, action='GeneralAccurateOCR', *, timeout=30,
                key_reader=None, opener=None):
    """Return complete OCR polygons/confidence; empty success differs from errors."""
    start = time.monotonic()
    def done(**values):
        return _result('tencent', start, action=action, **values)
    if action not in ('GeneralAccurateOCR', 'GeneralHandwritingOCR'):
        return done(error_code='unsupported_action')
    try:
        read_key = key_reader or _key
        sid, secret = read_key('TENCENT_SECRET_ID'), read_key('TENCENT_SECRET_KEY')
        if not sid or not secret:
            return done(error_code='missing_credentials')
        encoded, mime = _image(image_path, max_bytes=(10 * 1024 * 1024 // 4) * 3)
        if mime not in ('image/png', 'image/jpeg'):
            return done(error_code='unsupported_image_format')
    except OSError:
        return done(error_code='image_read_error')
    except ValueError as exc:
        return done(error_code='image_too_large' if str(exc) == 'image_too_large' else 'unsupported_image_format')
    except Exception:
        return done(error_code='credential_error')
    payload = json.dumps({'ImageBase64': encoded}).encode('utf-8')
    host = 'ocr.tencentcloudapi.com'
    stamp = int(time.time())
    date = time.strftime('%Y-%m-%d', time.gmtime(stamp))
    scope = date + '/ocr/tc3_request'
    canonical = '\n'.join(['POST', '/', '', 'content-type:application/json; charset=utf-8\nhost:' + host + '\n',
                           'content-type;host', hashlib.sha256(payload).hexdigest()])
    sign_text = '\n'.join(['TC3-HMAC-SHA256', str(stamp), scope, hashlib.sha256(canonical.encode()).hexdigest()])
    def digest(key, value):
        return hmac.new(key, value.encode(), hashlib.sha256).digest()
    signing_key = digest(digest(digest(('TC3' + secret).encode(), date), 'ocr'), 'tc3_request')
    signature = hmac.new(signing_key, sign_text.encode(), hashlib.sha256).hexdigest()
    headers = {'Authorization': 'TC3-HMAC-SHA256 Credential=%s/%s, SignedHeaders=content-type;host, Signature=%s' % (sid, scope, signature),
               'Content-Type': 'application/json; charset=utf-8', 'Host': host,
               'X-TC-Action': action, 'X-TC-Version': '2018-11-19', 'X-TC-Timestamp': str(stamp)}
    response, error, _ = _request(urllib.request.Request('https://' + host, data=payload, headers=headers), timeout, opener)
    if error:
        return done(error_code=error)
    request_id = ''
    try:
        body = response['Response']
        request_id = _identifier(body.get('RequestId', ''))
        if body.get('Error'):
            code = _identifier(body['Error'].get('Code')) or 'unknown'
            return done(error_code='business:' + code, request_id=request_id)
        detections = body['TextDetections']
        if not isinstance(detections, list):
            raise ValueError()
        blocks = []
        for item in detections:
            polygon = [{'X': float(p['X']), 'Y': float(p['Y'])} for p in item.get('Polygon', [])]
            if any(not math.isfinite(value) for point in polygon for value in point.values()):
                raise ValueError()
            text = item['DetectedText']
            if not isinstance(text, str):
                raise ValueError()
            confidence = item.get('Confidence')
            if confidence is not None:
                confidence = float(confidence)
                if not math.isfinite(confidence):
                    raise ValueError()
            blocks.append(dict(text=text, x=polygon[0]['X'] if polygon else 0,
                               y=polygon[0]['Y'] if polygon else 0, polygon=polygon,
                               confidence=confidence))
        return done(status='ok' if blocks else 'empty', blocks=blocks, request_id=request_id)
    except (KeyError, TypeError, ValueError, AttributeError):
        return done(error_code='invalid_response', request_id=request_id)


def deepseek_parse(image_path, *, model, system_prompt='', blocks=None,
                   timeout=120, max_tokens=4096, key_reader=None, opener=None):
    """One explicit model request. Caller owns model choice, budget and cache."""
    start = time.monotonic()
    def done(**values):
        return _result('deepseek', start, model=model, **values)
    if not isinstance(model, str) or not model.strip():
        return done(error_code='missing_model')
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 128 <= max_tokens <= 16384:
        return done(error_code='invalid_max_tokens')
    try:
        key = (key_reader or _key)('DEEPSEEK_API_KEY')
        if not key:
            return done(error_code='missing_credentials')
        encoded, mime = _image(image_path)
    except OSError:
        return done(error_code='image_read_error')
    except ValueError as exc:
        return done(error_code='image_too_large' if str(exc) == 'image_too_large' else 'unsupported_image_format')
    except Exception:
        return done(error_code='credential_error')
    instruction = '读取图片实际可见内容。返回 JSON 对象，含 records 数组和 notes 数组；无法确定的内容写入 notes，不猜测。'
    if blocks:
        instruction += '\n独立 OCR 证据（可能有错）：' + json.dumps(blocks, ensure_ascii=False)
    payload = {'model': model, 'messages': [
        {'role': 'system', 'content': system_prompt or '只返回 JSON，包含 records 与 notes。'},
        {'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': 'data:' + mime + ';base64,' + encoded}},
            {'type': 'text', 'text': instruction}]}], 'temperature': 0.1, 'max_tokens': max_tokens}
    payload_bytes = json.dumps(payload).encode()
    if len(payload_bytes) > 48 * 1024 * 1024:
        return done(error_code='request_too_large')
    request = urllib.request.Request('https://api.deepseek.com/chat/completions',
        data=payload_bytes, headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    response, error, _ = _request(request, timeout, opener)
    if error:
        return done(error_code=error)
    request_id, usage = '', {}
    try:
        request_id = _identifier(response.get('id', ''))
        raw_usage = response.get('usage', {})
        usage = {k: v for k, v in raw_usage.items() if k in ('prompt_tokens', 'completion_tokens', 'total_tokens',
                  'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens') and isinstance(v, (int, float)) and v >= 0}
        if response.get('error'):
            return done(error_code='business:' + (_identifier(response['error'].get('code')) or 'unknown'), request_id=request_id, usage=usage)
        choice = response['choices'][0]
        if choice.get('finish_reason') not in (None, 'stop'):
            return done(error_code='incomplete_response', request_id=request_id, usage=usage)
        content = choice['message']['content'].strip()
        if content.startswith('```'):
            content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content)
        parsed = json.loads(content)
        records, notes = parsed['records'], parsed.get('notes', [])
        if not isinstance(records, list) or any(not isinstance(r, dict) for r in records) or not isinstance(notes, (list, str)):
            raise ValueError()
        # 保留原始文本（截断）：模型可能返回按标题分组的结构，证据链要能核对原貌。
        return done(status='ok' if records else 'empty', records=records,
                    notes=[notes] if isinstance(notes, str) else notes, request_id=request_id,
                    usage=usage, raw_text=content[:4000])
    except (KeyError, IndexError, ValueError, TypeError, AttributeError):
        return done(error_code='invalid_response', request_id=request_id, usage=usage)


def probe_deepseek(image_path, *, model, max_tokens=256, **kwargs):
    """Probe using a caller-supplied labelled image; success alone proves no accuracy.

    Use a synthetic image with known text and compare returned text manually.
    This is a real, potentially billable request, never an automatic startup probe.
    """
    return deepseek_parse(image_path, model=model, max_tokens=max_tokens,
        system_prompt='读取图中文字，返回 {"records":[{"text":"实际看到的文字"}],"notes":[]}。看不清则 records 留空，并在 notes 说明。', **kwargs)
