# -*- coding: utf-8 -*-
"""字段识别后端（独立 rec 模型）。

只加载 PP-OCRv5_mobile_rec 的 TextRecognition，**不加载** PP-Structure。
用途：对原图里裁剪出来的「品类标题 / 单位文字」单行区域做本地文字识别，
作为腾讯 OCR、DeepSeek、词典之外的本地候选来源。

设计约束（对应任务书）：
  · 惰性加载：只有接到字段请求才 import paddleocr / 加载权重；
  · 缺失可降级：模型权重不存在或加载失败时抛 FieldUnavailable，由 serve.py 返回 ok=false；
  · 只返回 rec_text / rec_score，不做业务判断（是不是品类、是不是单位由主进程判）；
  · 提供模型指纹，供缓存键与审计使用。
"""

from __future__ import annotations

import hashlib
from pathlib import Path


class FieldUnavailable(RuntimeError):
    """字段模型不可用；调用方应降级，不应把它当识别错误反复重试。"""


def _as_dict(res):
    """兼容 paddleocr 不同小版本：结果可能是 dict / 有 .json() / 包一层 res。"""
    d = getattr(res, "json", None)
    if callable(d):
        d = d()
    if isinstance(d, dict):
        inner = d.get("res")
        if isinstance(inner, dict):
            return inner
        return d
    return {}


def _model_fingerprint(model_name, model_dir):
    """轻量模型指纹：模型名 + 配置文件哈希 + 权重文件大小，避免读整个 16MB。"""
    try:
        directory = Path(model_dir) if model_dir else None
        if directory is None:
            # 官方缓存目录
            directory = Path.home() / ".paddlex" / "official_models" / model_name
        digest = hashlib.sha256()
        config = directory / "config.json"
        if config.is_file():
            digest.update(config.read_bytes()[:1 << 20])
        params = directory / "inference.pdiparams"
        size = params.stat().st_size if params.is_file() else -1
        return "%s@%s-%d" % (model_name, digest.hexdigest()[:12], size)
    except Exception:  # noqa: BLE001
        return model_name


class FieldBackend:
    """独立 rec 模型的极简适配层。"""

    def __init__(self, model_name="PP-OCRv5_mobile_rec", model_dir=None):
        try:
            from paddleocr import TextRecognition  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            raise FieldUnavailable("paddleocr 不可用：%r" % (exc,)) from exc
        try:
            self.pipe = TextRecognition(model_name=model_name, model_dir=model_dir)
        except Exception as exc:  # noqa: BLE001
            raise FieldUnavailable("rec 模型加载失败：%r" % (exc,)) from exc
        self.name = "textrecognition"
        self.model_name = model_name
        self.model_id = _model_fingerprint(model_name, model_dir)

    def recognize(self, image):
        """识别一张单行文字区域图（路径或 numpy 数组）。

        返回 {'rec_text': str, 'rec_score': float}；识别不出文字时 rec_text 为空串。
        """
        out = self.pipe.predict(input=image)
        if not out:
            return {"rec_text": "", "rec_score": 0.0}
        d = _as_dict(out[0])
        return {
            "rec_text": str(d.get("rec_text") or ""),
            "rec_score": float(d.get("rec_score") or 0.0),
        }
