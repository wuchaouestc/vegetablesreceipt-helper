"""本地核对网页：让业务人员在浏览器里核对 AI 读出来的手写单据。

只监听 127.0.0.1，不对外暴露。核对提交后做三件事（见 `learning`）：
落标注文件、学词典别名、存已确认示例——**软件因此越用越智能**。

路由：
    GET  /                 核对页
    GET  /api/payload      核对数据
    GET  /api/image        原图
    GET  /api/crop?row=    某行的原图裁剪（按原图坐标裁，保留上下文）
    POST /api/confirm      提交核对结果 → 落盘并学习
"""
from __future__ import annotations

import json
import threading
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .learning import save_confirmed
from .review import crop_region
from .storage import digest, write_json

def _page_path():
    """定位核对页 HTML。打包成 EXE 后资源在临时解包目录里。"""
    import sys
    candidates = [Path(__file__).resolve().parent / 'review_page.html']
    base = getattr(sys, '_MEIPASS', None)
    if base:
        candidates.append(Path(base) / 'handwriting' / 'review_page.html')
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


PAGE = _page_path()
MAX_BODY = 4 * 1024 * 1024          # 核对结果不会超过 4 MB


@dataclass
class ReviewSession:
    """一次核对会话要带的全部东西。"""
    image_path: Path
    image_sha256: str
    payload: dict
    annotations_dir: Path
    vocabulary_path: Path
    examples_path: Path
    image_root: Path | None = None
    crop_dir: Path | None = None
    task_id: str = ''
    difficulties: list = field(default_factory=list)
    doc_type: str = 'handwritten'
    source_group: str | None = None
    split: str = 'unassigned'
    lock: threading.Lock = field(default_factory=threading.Lock)

    def crops(self):
        """给每一行和每条未匹配原文预裁一块图，原图坐标可回溯。"""
        if self.crop_dir is None:
            self.crop_dir = Path(self.image_path).parent.parent / 'evaluations' / (self.task_id or '核对') / '核对裁剪'
        self.crop_dir.mkdir(parents=True, exist_ok=True)
        wanted = []
        for row in self.payload.get('rows') or []:
            if row.get('bbox'):
                wanted.append((row.get('row_id'), row['bbox']))
        for item in self.payload.get('unmatched') or []:
            if item.get('bbox'):
                wanted.append((item.get('id'), item['bbox']))
        made = {}
        for name, bbox in wanted:
            if not name:
                continue
            path = self.crop_dir / ('%s.png' % str(name).replace('/', '_'))
            if not path.exists():
                result = crop_region(self.image_path, bbox, self.crop_dir, str(name).replace('/', '_'))
                if not result:
                    continue
            made[str(name)] = path
        return made


def _make_handler(session):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'HandwritingReview/1.0'

        # ---------------------------------------------------------- 工具
        def _send(self, code, body=b'', content_type='application/json; charset=utf-8'):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _json(self, code, value):
            self._send(code, json.dumps(value, ensure_ascii=False).encode('utf-8'))

        def log_message(self, fmt, *args):
            return                      # 安静：不往控制台刷请求日志

        # ---------------------------------------------------------- 路由
        def do_GET(self):
            parsed = urlparse(self.path)
            route = parsed.path
            if route in ('/', '/index.html'):
                try:
                    self._send(200, PAGE.read_bytes(), 'text/html; charset=utf-8')
                except OSError:
                    self._json(500, {'error': '核对页文件缺失'})
                return
            if route == '/api/payload':
                with session.lock:
                    payload = json.loads(json.dumps(session.payload, ensure_ascii=False))
                payload['crop_ready'] = True
                self._json(200, payload)
                return
            if route == '/api/image':
                try:
                    self._send(200, Path(session.image_path).read_bytes(), 'image/jpeg')
                except OSError:
                    self._json(404, {'error': '原图读不到'})
                return
            if route == '/api/crop':
                name = (parse_qs(parsed.query).get('row') or [''])[0]
                crops = session.crops()
                path = crops.get(name)
                if not path or not Path(path).exists():
                    self._json(404, {'error': '这一行没有原图区域'})
                    return
                self._send(200, Path(path).read_bytes(), 'image/png')
                return
            self._json(404, {'error': '未知路径'})

        def do_POST(self):
            if urlparse(self.path).path != '/api/confirm':
                self._json(404, {'error': '未知路径'})
                return
            try:
                length = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_BODY:
                self._json(400, {'error': '提交内容为空或过大'})
                return
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode('utf-8'))
            except (UnicodeDecodeError, ValueError):
                self._json(400, {'error': '提交内容不是合法 JSON'})
                return
            with session.lock:
                try:
                    result = save_confirmed(body,
                                           annotations_dir=session.annotations_dir,
                                           vocabulary_path=session.vocabulary_path,
                                           examples_path=session.examples_path,
                                           image_root=session.image_root,
                                           difficulties=session.difficulties,
                                           doc_type=session.doc_type,
                                           source_group=session.source_group,
                                           split=session.split)
                except Exception as exc:
                    self._json(400, {'error': str(exc)})
                    return
                # 留一份提交原文，方便回溯这次核对到底改了什么
                if session.task_id:
                    try:
                        archive = Path(session.annotations_dir).parent / 'evaluations' / session.task_id
                        archive.mkdir(parents=True, exist_ok=True)
                        write_json(archive / '人工核对提交.json', body)
                    except OSError:
                        pass
            self._json(200, {'ok': True, 'result': result})

    return Handler


def serve(session, *, host='127.0.0.1', port=8765, open_browser=False, background=False):
    """启动核对服务。返回 (server, url)。"""
    if host not in ('127.0.0.1', 'localhost'):
        raise ValueError('核对服务只允许监听本机')
    session.crops()
    server = ThreadingHTTPServer((host, port), _make_handler(session))
    url = 'http://%s:%d/' % (host, server.server_address[1])
    if background:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        if open_browser:
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        return server, url
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    server.serve_forever()
    return server, url


def session_from_result(result, *, annotations_dir, vocabulary_path, examples_path,
                        image_root=None, task_id='', difficulties=None, doc_type='handwritten',
                        source_group=None, split='unassigned'):
    """把手写辅助通道的 `RecognitionResult` 变成一个核对会话。"""
    from .learning import build_review_payload
    image_path = Path(result.image_path) if hasattr(result, 'image_path') else None
    payload = getattr(result, 'review_payload', None)
    if payload is None:
        payload = build_review_payload(result.records, image_path=str(image_path or ''),
                                       image_sha256='', task_id=task_id,
                                       notes=list(getattr(result, 'notes', []) or []))
    return ReviewSession(image_path=Path(image_path), image_sha256=payload.get('image_sha256', ''),
                         payload=payload, annotations_dir=Path(annotations_dir),
                         vocabulary_path=Path(vocabulary_path), examples_path=Path(examples_path),
                         image_root=Path(image_root) if image_root else None,
                         task_id=task_id, difficulties=list(difficulties or []),
                         doc_type=doc_type, source_group=source_group, split=split)


def build_session(image_path, records, *, image_sha256=None, annotations_dir,
                  vocabulary_path, examples_path, image_root=None, task_id='',
                  reviewer='', customer=None, notes=None, unmatched=None,
                  difficulties=None, doc_type='handwritten', source_group=None, split='unassigned'):
    """从「原图 + 识别明细」直接建一个核对会话。"""
    from .learning import build_review_payload, load_examples
    image_path = Path(image_path)
    payload = build_review_payload(records, image_path=str(image_path),
                                   image_sha256=image_sha256 or digest(image_path),
                                   task_id=task_id, reviewer=reviewer, customer=customer,
                                   notes=notes, unmatched=unmatched, image_root=image_root,
                                   examples=load_examples(examples_path))
    return ReviewSession(image_path=image_path, image_sha256=payload['image_sha256'],
                         payload=payload, annotations_dir=Path(annotations_dir),
                         vocabulary_path=Path(vocabulary_path), examples_path=Path(examples_path),
                         image_root=Path(image_root) if image_root else None,
                         task_id=task_id, difficulties=list(difficulties or []),
                         doc_type=doc_type, source_group=source_group, split=split)
