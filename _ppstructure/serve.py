# -*- coding: utf-8 -*-
"""PP-Structure 结构服务（sidecar）。

跑在独立进程 / 独立 venv 里，只监听 127.0.0.1：
    GET  /health      健康检查
    POST /structure   图片 + OCR 文本块 → 结构卡（JSON）
    GET  /overlay     把网格画到原图上，便于肉眼检查

设计原则：
  · 只输出「结构」和「文字归属」，不替业务做判断
  · PP-Structure 可用时优先用它（拿到单元格/行列跨度），否则用几何引擎
  · 任何异常都不 500 到调用方无法处理：能降级就降级，降不了返回 ok=false
  · 结果按「图片 md5 + 文本块指纹」缓存，重复识别零成本
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import grid as gridmod
import layout

# PP 模型源：国内走 ModelScope，避免 HuggingFace 卡住；没有这个变量时 PaddleX 会做联网探测
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "modelscope")
# Paddle 3.3.1 + oneDNN 在本机跑 PP-Structure 会抛
# "ConvertPirAttribute2RuntimeAttribute not support"，关掉 MKLDNN 走普通 CPU 推理。
os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "0")

BASE = Path(__file__).resolve().parent
CACHE = BASE / "cache"
LOG = BASE / "服务.log"
LOCK = threading.Lock()
START = time.time()

_state = {
    "backend": "grid",       # grid / pp
    "pp_ready": False,
    "pp_error": "",
    "requests": 0,
    "errors": 0,
    # 字段识别（独立 rec 模型），与 PP-Structure 完全分离
    "fields_ready": False,
    "fields_model_id": "",
    "fields_error": "",
    "fields_requests": 0,
    "fields_regions": 0,
    "fields_errors": 0,
    "fields_last_success": 0.0,
}

# 字段识别安全上限（任务书建议值，可调）
FIELDS_MAX_REGIONS = 32
FIELDS_MAX_BATCH = 16
FIELDS_MAX_PIXELS = 50_000_000      # 原图像素量上限，防超大图
FIELDS_LOCK = threading.Lock()
FIELDS_INFER_LOCK = threading.Lock()   # 串行化推理，避免并发 predict 同一模型
_field_backend = None


def log(msg):
    try:
        if LOG.exists() and LOG.stat().st_size > 5 * 1024 * 1024:
            LOG.replace(LOG.with_suffix(".log.1"))
        with LOG.open("a", encoding="utf-8") as f:
            f.write("%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:  # noqa: BLE001
        pass


def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def blocks_fp(blocks):
    h = hashlib.md5()
    for b in blocks or []:
        h.update(str(b.get("text", "")).encode("utf-8", "ignore"))
        h.update(b"|")
        h.update(str(round(float(b.get("x", 0) or 0), 1)).encode())
        h.update(b",")
        h.update(str(round(float(b.get("y", 0) or 0), 1)).encode())
        h.update(b";")
    return h.hexdigest()


def _init_pp(backend):
    if backend == "grid":
        return
    try:
        from pp_backend import PPBackend

        def warm():
            try:
                t0 = time.time()
                _state["pp"] = PPBackend()
                _state["pp_ready"] = True
                _state["backend"] = "pp"
                log("PP-Structure 就绪，加载耗时 %.1fs" % (time.time() - t0))
            except Exception as e:  # noqa: BLE001
                _state["pp_error"] = repr(e)
                log("PP-Structure 初始化失败，继续用几何引擎：%r" % (e,))
                log(traceback.format_exc())

        threading.Thread(target=warm, name="pp-warm", daemon=True).start()
        log("PP-Structure 后台加载中…")
    except Exception as e:  # noqa: BLE001
        _state["pp_error"] = repr(e)
        log("PP-Structure 导入失败，继续用几何引擎：%r" % (e,))


def _needs_pp(g):
    """几何搞不定的图才值得让 PP 跑（PP 在 CPU 上单图 20 秒左右）。

    注意：完全没线框的图（confidence=none）也不调 PP —— 实测 PP 同样找不到表，
    调了只是白等 20 秒。
    """
    if not g or not g.get("tables"):
        return False
    if g.get("confidence") == "high" and len(g.get("v_lines") or []) >= 3:
        return False
    return True


def _pp_grid(path):
    try:
        g = _state["pp"].grid(path)
        if g and g.get("tables"):
            return g
    except Exception as e:  # noqa: BLE001
        log("PP-Structure 解析失败，改走几何引擎：%r" % (e,))
    return None


# ---------------------------------------------------------------- 字段识别
def _ensure_fields():
    """惰性加载独立 rec 模型（只在字段请求到来时才 import paddleocr）。"""
    global _field_backend
    if _field_backend is not None:
        return _field_backend
    with FIELDS_LOCK:
        if _field_backend is not None:
            return _field_backend
        t0 = time.time()
        try:
            from field_backend import FieldBackend
            _field_backend = FieldBackend()
            _state["fields_ready"] = True
            _state["fields_model_id"] = _field_backend.model_id
            _state["fields_error"] = ""
            log("字段 rec 模型就绪 %s，加载耗时 %.1fs" % (
                _state["fields_model_id"], time.time() - t0))
        except Exception as e:  # noqa: BLE001
            _state["fields_error"] = repr(e)
            _state["fields_ready"] = False
            log("字段 rec 模型初始化失败：%r" % (e,))
            log(traceback.format_exc())
        return _field_backend


def _recognize_fields(path, regions, deadline):
    """按区域裁剪原图并识别。返回 (results, errors)。"""
    from PIL import Image

    t0 = time.time()
    backend = _ensure_fields()
    if backend is None:
        return [], "字段模型不可用：%s" % _state["fields_error"]

    with Image.open(path) as image:
        w, h = image.size
        if w * h > FIELDS_MAX_PIXELS:
            return [], "图片像素量超过上限 %d" % FIELDS_MAX_PIXELS
        results = []
        for region in regions:
            if time.time() - t0 > deadline:
                return results, "字段阶段超时，剩余区域未处理"
            rid = region.get("region_id")
            bbox = region.get("bbox")
            try:
                x0, y0, x1, y1 = (int(round(float(v))) for v in bbox)
            except (TypeError, ValueError):
                results.append({"region_id": rid, "status": "error",
                                "error_code": "bad_bbox", "raw_text": "", "rec_score": 0.0})
                continue
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(w, x1), min(h, y1)
            if x1 <= x0 or y1 <= y0:
                results.append({"region_id": rid, "status": "error",
                                "error_code": "crop_empty", "raw_text": "", "rec_score": 0.0})
                continue
            try:
                crop = image.crop((x0, y0, x1, y1))
                import numpy as np
                rec = backend.recognize(np.asarray(crop))
                results.append({"region_id": rid, "status": "ok",
                                "raw_text": rec["rec_text"], "rec_score": rec["rec_score"]})
            except Exception as e:  # noqa: BLE001
                results.append({"region_id": rid, "status": "error",
                                "error_code": type(e).__name__, "raw_text": "", "rec_score": 0.0})
    return results, ""


def structure(path, blocks, pp_mode="auto"):
    """pp_mode: auto（几何优先，搞不定才 PP）/ always / never"""
    t0 = time.time()
    ready = bool(_state.get("pp_ready") and _state.get("pp"))
    g = None
    source = "lines"

    if pp_mode == "always" and ready:
        g = _pp_grid(path)
        if g is not None:
            source = "pp"
    if g is None:
        g = gridmod.detect(path, blocks)
        source = g.get("source", "lines")
    if pp_mode == "auto" and ready and _needs_pp(g):
        gp = _pp_grid(path)
        if gp is not None:
            g, source = gp, "pp"

    card, meta = layout.build_card(blocks, g)
    elapsed = int((time.time() - t0) * 1000)
    return {
        "ok": True,
        "card": card,
        "meta": meta,
        "source": source,
        "confidence": g.get("confidence"),
        "grid": g,
        "elapsed_ms": elapsed,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "PPStructSidecar/6.0"

    def log_message(self, fmt, *args):  # noqa: A003
        pass

    # ---------------------------------------------------------- 基础
    def _send(self, code, obj=None, raw=None, ctype="application/json; charset=utf-8"):
        body = raw if raw is not None else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 32 * 1024 * 1024:
            raise ValueError("请求体为空或过大")
        return json.loads(self.rfile.read(n).decode("utf-8"))

    # ---------------------------------------------------------- POST
    def _recognize_fields_endpoint(self):
        """独立 rec 字段识别：裁剪区域批量识别，只回 raw_text / rec_score。"""
        _state["fields_requests"] += 1
        try:
            body = self._json_body()
            path = str(body.get("image_path") or body.get("path") or "")
            if not path or not Path(path).is_file():
                self._send(400, {"ok": False, "error": "图片不存在：%s" % path})
                return
            regions = body.get("regions") or []
            if not isinstance(regions, list) or not regions:
                self._send(400, {"ok": False, "error": "regions 为空"})
                return
            if len(regions) > FIELDS_MAX_REGIONS:
                regions = regions[:FIELDS_MAX_REGIONS]
            deadline = float(body.get("deadline") or 10.0)
            # 单批最多 16 区域：分批处理，避免一次请求做太久
            results = []
            error = ""
            for start in range(0, len(regions), FIELDS_MAX_BATCH):
                chunk = regions[start:start + FIELDS_MAX_BATCH]
                with FIELDS_INFER_LOCK:
                    chunk_results, chunk_error = _recognize_fields(path, chunk, deadline)
                results.extend(chunk_results)
                if chunk_error:
                    error = chunk_error
                    break
            ok_count = sum(1 for r in results if r.get("status") == "ok")
            _state["fields_regions"] += len(results)
            if ok_count:
                _state["fields_last_success"] = time.time()
                _state["fields_errors"] = 0
            else:
                _state["fields_errors"] += 1
            log("字段 %s｜区域 %d｜成功 %d｜%s｜%s" % (
                Path(path).name, len(results), ok_count, _state["fields_model_id"], error or "ok"))
            self._send(200, {
                "ok": True,
                "model_id": _state["fields_model_id"],
                "results": results,
                "error": error,
                "elapsed_ms": 0,
            })
        except Exception as e:  # noqa: BLE001
            _state["errors"] += 1
            log("字段请求失败：%r" % (e,))
            log(traceback.format_exc())
            self._send(500, {"ok": False, "error": repr(e)})

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/health":
            self._send(200, {
                "ok": True,
                "service": "ppstructure-sidecar",
                "version": "6.0",
                "backend": _state["backend"],
                "pp_ready": _state["pp_ready"],
                "pp_error": _state["pp_error"],
                "uptime_s": int(time.time() - START),
                "requests": _state["requests"],
                "errors": _state["errors"],
                "fields_ready": _state["fields_ready"],
                "fields_model_id": _state["fields_model_id"],
                "fields_requests": _state["fields_requests"],
                "fields_regions": _state["fields_regions"],
                "fields_errors": _state["fields_errors"],
                "fields_last_success": _state["fields_last_success"],
            })
            return
        if u.path == "/overlay":
            qs = dict(p.split("=", 1) for p in u.query.split("&") if "=" in p)
            path = qs.get("path", "")
            if not path or not Path(path).is_file():
                self._send(400, {"ok": False, "error": "path 无效"})
                return
            try:
                g = gridmod.detect(path)
                out = CACHE / ("overlay_%s.png" % md5_file(path)[:12])
                CACHE.mkdir(exist_ok=True)
                gridmod.overlay(path, g, str(out))
                self._send(200, raw=out.read_bytes(), ctype="image/png")
            except Exception as e:  # noqa: BLE001
                self._send(500, {"ok": False, "error": repr(e)})
            return
        self._send(404, {"ok": False, "error": "not found"})

    # ---------------------------------------------------------- POST
    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/recognize_fields":
            self._recognize_fields_endpoint()
            return
        if u.path != "/structure":
            self._send(404, {"ok": False, "error": "not found"})
            return
        _state["requests"] += 1
        try:
            body = self._json_body()
            path = str(body.get("path") or "")
            if not path or not Path(path).is_file():
                self._send(400, {"ok": False, "error": "图片不存在：%s" % path})
                return
            blocks = body.get("blocks") or []
            pp_mode = str(body.get("pp_mode") or ("auto" if body.get("use_pp", True) else "never"))
            if pp_mode not in ("auto", "always", "never"):
                pp_mode = "auto"
            img_md5 = str(body.get("md5") or md5_file(path))
            key = "%s_%s_%s_%s" % (img_md5[:16], blocks_fp(blocks)[:8], layout.CARD_VER, pp_mode)
            cache_file = CACHE / (key + ".json")
            if cache_file.is_file() and not body.get("force"):
                try:
                    data = json.loads(cache_file.read_text(encoding="utf-8"))
                    data["cached"] = True
                    self._send(200, data)
                    return
                except Exception:  # noqa: BLE001
                    pass

            with LOCK:
                data = structure(path, blocks, pp_mode=pp_mode)
            data["md5"] = img_md5
            data["key"] = key
            data["cached"] = False
            try:
                CACHE.mkdir(exist_ok=True)
                cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
            meta = data.get("meta") or {}
            log("结构 %s｜来源=%s｜行=%s 物料=%s｜%.0fms｜%s" % (
                Path(path).name, data.get("source"), meta.get("rows"),
                meta.get("items"), data.get("elapsed_ms"), "PP" if data.get("source") == "pp" else "几何"))
            self._send(200, data)
        except Exception as e:  # noqa: BLE001
            _state["errors"] += 1
            log("请求失败：%r" % (e,))
            log(traceback.format_exc())
            self._send(500, {"ok": False, "error": repr(e)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18765)
    ap.add_argument("--backend", default=os.environ.get("PP_BACKEND", "auto"),
                    choices=("auto", "pp", "grid"))
    ap.add_argument("--selftest", default="", help="给一张图，打印结构卡后退出")
    args = ap.parse_args()

    CACHE.mkdir(exist_ok=True)
    if args.selftest:
        p = args.selftest
        t0 = time.time()
        g = gridmod.detect(p)
        card, meta = layout.build_card([], g)
        print(card or "（没有文本块，只检测到网格）")
        print("meta:", json.dumps(meta, ensure_ascii=False), "%.0fms" % ((time.time() - t0) * 1000))
        return

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((args.host, args.port))
        s.close()
    except OSError:
        print("端口 %d 已被占用，视为已启动" % args.port)
        log("端口 %d 已被占用，退出" % args.port)
        return

    _init_pp(args.backend)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    log("服务启动 http://%s:%d backend=%s" % (args.host, args.port, args.backend))
    print("PP-Structure sidecar: http://%s:%d  backend=%s" % (args.host, args.port, args.backend))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
