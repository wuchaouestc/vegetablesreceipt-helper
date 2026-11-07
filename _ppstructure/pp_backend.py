# -*- coding: utf-8 -*-
"""PP-StructureV3 适配层（可选）。

只在 sidecar 的 venv 里装了 paddleocr 时才会被导入。
目标不是拿它的文字，而是拿它的**表格结构**：单元格 bbox + 行列跨度。

PaddleOCR 3.x 的返回结构在不同小版本间有出入，这里做了防御式解析：
  · 先取 result.json（或 dict(result)）
  · 找 table_res_list / table_html / cell_box_list
  · 用 HTML 里的 rowspan/colspan 还原行列，再用 cell_box_list 补 bbox
任何一步对不上就抛异常，由 serve.py 退回几何引擎。
"""

from __future__ import annotations

from html.parser import HTMLParser


class _TableHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self._row = None
        self._cell = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            a = {k.lower(): v for k, v in attrs}
            self._cell = {
                "rs": _int(a.get("rowspan"), 1),
                "cs": _int(a.get("colspan"), 1),
                "text": "",
            }
            self._text = []

    def handle_data(self, data):
        if self._cell is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("td", "th") and self._cell is not None:
            self._cell["text"] = " ".join("".join(self._text).split())
            self._row.append(self._cell)
            self._cell = None
            self._text = []
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _int(v, default):
    try:
        return max(1, int(str(v).strip()))
    except (TypeError, ValueError):
        return default


def _bbox_of(box):
    """cell_box_list 里的框可能是 4 点多边形，也可能是 [x1,y1,x2,y2]。"""
    try:
        pts = list(box)
        if len(pts) == 4 and all(isinstance(p, (list, tuple)) and len(p) == 2 for p in pts):
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
            return [min(xs), min(ys), max(xs), max(ys)]
        flat = [float(v) for v in pts]
        if len(flat) >= 8:
            xs, ys = flat[0::2], flat[1::2]
            return [min(xs), min(ys), max(xs), max(ys)]
        if len(flat) == 4:
            return [flat[0], flat[1], flat[2], flat[3]]
    except (TypeError, ValueError):
        pass
    return None


def _as_dict(res):
    d = getattr(res, "json", None)
    if callable(d):
        d = d()
    if isinstance(d, dict):
        # PaddleOCR 3.7 的 LayoutParsingResultV2 会包一层 {"res": {...}}
        inner = d.get("res")
        if isinstance(inner, dict) and any(
            k in inner for k in ("table_res_list", "parsing_res_list", "layout_det_res")
        ):
            return inner
        return d
    for name in ("to_dict", "as_dict"):
        fn = getattr(res, name, None)
        if callable(fn):
            try:
                d = fn()
                if isinstance(d, dict):
                    return d
            except Exception:  # noqa: BLE001
                pass
    try:
        return dict(res)
    except Exception:  # noqa: BLE001
        return {}


def _find_tables(d):
    for key in ("table_res_list", "tables", "table_results"):
        v = d.get(key)
        if isinstance(v, dict):
            return [v]
        if isinstance(v, list) and v:
            return v
    return []


def _table_html(t):
    if isinstance(t, str):
        return t
    for key in ("pred_html", "html", "table_html", "pred", "structure"):
        v = t.get(key)
        if isinstance(v, str) and "<" in v:
            return v
    return ""


def _cell_boxes(t):
    if not isinstance(t, dict):
        return []
    for key in ("cell_box_list", "cell_bbox_list", "cells"):
        v = t.get(key)
        if isinstance(v, list) and v:
            return v
    return []


class PPBackend:
    def __init__(self, **kwargs):
        from paddleocr import PPStructureV3  # noqa: F401

        # 只保留「版面 + 表格 + 文字」三件事：印章/公式/图表与业务无关，
        # 关掉它们能把单图耗时从 60 秒压到 24 秒左右（CPU）。
        # 版面必须用 M 及以上：S 版在本机测不出这类宽表。
        opts = dict(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            use_seal_recognition=False,
            use_formula_recognition=False,
            use_chart_recognition=False,
            layout_detection_model_name="PP-DocLayout-M",
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_recognition_model_name="PP-OCRv5_mobile_rec",
        )
        opts.update(kwargs or {})
        self.pipe = PPStructureV3(**opts)
        self.name = "ppstructurev3"

    def grid(self, path):
        out = self.pipe.predict(input=str(path))
        best = None
        w = h = None
        for res in out:
            d = _as_dict(res)
            w = d.get("width") or w
            h = d.get("height") or h
            for t in _find_tables(d):
                html = _table_html(t)
                if not html:
                    continue
                g = _grid_from_table(t, html)
                if g and (best is None or len(g["cells"]) > len(best["cells"])):
                    best = g
        if not best:
            raise RuntimeError("PP-Structure 没有返回可用的表格结构")
        return {
            "schema": 1,
            "w": w, "h": h,
            "confidence": "high",
            "source": "pp",
            "h_lines": best["h_lines"],
            "v_lines": best["v_lines"],
            "tables": [best],
        }


def _grid_from_table(t, html):
    parser = _TableHTML()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001
        return None
    if len(parser.rows) < 2:
        return None

    boxes = [_bbox_of(b) for b in _cell_boxes(t)]
    boxes = [b for b in boxes if b]
    flat = []
    occupied = set()
    n_rows = n_cols = 0
    for r, row in enumerate(parser.rows):
        c = 0
        for cell in row:
            while (r, c) in occupied:
                c += 1
            rs, cs = cell["rs"], cell["cs"]
            for rr in range(r, r + rs):
                for cc in range(c, c + cs):
                    occupied.add((rr, cc))
            n_rows = max(n_rows, r + rs)
            n_cols = max(n_cols, c + cs)
            flat.append({"r": r, "c": c, "rs": rs, "cs": cs, "text": cell["text"], "bbox": None})
            c += cs

    if n_rows < 2 or n_cols < 2:
        return None
    if len(boxes) == len(flat):
        for cell, box in zip(flat, boxes):
            cell["bbox"] = box
    elif not boxes:
        return None

    vx, hy = set(), set()
    for cell in flat:
        if cell["bbox"]:
            x1, y1, x2, y2 = cell["bbox"]
            vx.add(round(x1, 1)); vx.add(round(x2, 1))
            hy.add(round(y1, 1)); hy.add(round(y2, 1))
    v_lines = sorted(vx)
    h_lines = sorted(hy)
    if len(v_lines) < 3 or len(h_lines) < 3:
        return None
    cells = [{k: cell[k] for k in ("r", "c", "rs", "cs", "bbox")} for cell in flat if cell["bbox"]]
    if not cells:
        return None
    return {
        "bbox": [min(v_lines), min(h_lines), max(v_lines), max(h_lines)],
        "n_rows": n_rows, "n_cols": n_cols, "header_row": 0,
        "cells": cells,
        "h_lines": h_lines, "v_lines": v_lines,
    }
