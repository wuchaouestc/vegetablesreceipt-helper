# -*- coding: utf-8 -*-
"""表格网格检测（几何引擎）。

给「结构卡」提供行列与合并关系，**不提供文字**——文字始终来自腾讯云 OCR。

两条路线：
  1. 有线框表格：二值化后找长横线/长竖线 → 单元格矩阵 → 用「某段线是否缺失」
     判断横向/纵向合并（rowspan / colspan）。
  2. 无线框（或有线框但检测失败）：退回用 OCR 文本块的坐标聚类，confidence=low。

依赖：Pillow（sidecar 的 venv 里装；主程序里本来就有）。
"""

from __future__ import annotations

import math
import statistics


# ---------------------------------------------------------------- 基础工具
def _pil():
    from PIL import Image, ImageOps
    return Image, ImageOps


def _max_ones_run(v: int) -> int:
    """整数二进制里最长的一段连续 1。"""
    if not v:
        return 0
    return max(len(p) for p in bin(v)[2:].split("0"))


def _group(vals, gap=3):
    """把相邻坐标合成一条线（线可能有 2~3 像素粗）。"""
    vals = sorted(vals)
    if not vals:
        return []
    out, cur = [], [vals[0]]
    for v in vals[1:]:
        if v - cur[-1] <= gap:
            cur.append(v)
        else:
            out.append(cur)
            cur = [v]
    out.append(cur)
    return [int(round(sum(g) / len(g))) for g in out]


def _cluster(vals, gap):
    """一维聚类，返回簇中心。"""
    vals = sorted(vals)
    if not vals:
        return []
    groups, cur = [], [vals[0]]
    for v in vals[1:]:
        if v - cur[-1] <= gap:
            cur.append(v)
        else:
            groups.append(cur)
            cur = [v]
    groups.append(cur)
    return [sum(g) / len(g) for g in groups]


def _to_bw(img, max_side=1600):
    """转灰度 → 缩放到 max_side 以内 → 二值化。返回 (1 位图, 缩放比, 工作尺寸)。"""
    Image, ImageOps = _pil()
    im = ImageOps.exif_transpose(img).convert("L")
    w0, h0 = im.size
    scale = 1.0
    if max(w0, h0) > max_side:
        scale = max_side / float(max(w0, h0))
        im = im.resize((max(1, int(w0 * scale)), max(1, int(h0 * scale))), Image.LANCZOS)
    # 阈值 235：能吃到 #ccc 这类浅灰线；文字比这黑得多，但行内不会形成长游程
    bw = im.point(lambda p: 0 if p < 235 else 255, "L").convert("1", dither=Image.Dither.NONE)
    return bw, scale, im.size


def _line_positions(bw, horizontal=True):
    """找长线。返回工作坐标系下的线中心列表。"""
    Image = _pil()[0]
    work = bw if horizontal else bw.transpose(Image.Transpose.TRANSPOSE)
    W, H = work.size
    stride = (W + 7) // 8
    data = work.tobytes()
    mask = (1 << W) - 1
    cands = []
    for y in range(H):
        v = int.from_bytes(data[y * stride:(y + 1) * stride], "big")
        dark = W - v.bit_count()
        if dark < 0.18 * W:          # 先粗筛，省掉逐行求最长游程
            continue
        run = _max_ones_run((~v) & mask)
        if run >= 0.45 * W:          # 长游程才算线，文字行过不了这一关
            cands.append((y, run))
    if not cands:
        return []
    # 同一条粗线会有多行，按 y 合并；保留其中最粗的一行做中心
    lines = _group([y for y, _ in cands], gap=3)
    return lines


def _dark_ratio(bw, box):
    """区域内黑像素占比。box=(x1,y1,x2,y2)。"""
    W, H = bw.size
    x1, y1, x2, y2 = box
    x1 = max(0, min(W - 1, int(x1)))
    x2 = max(x1 + 1, min(W, int(x2)))
    y1 = max(0, min(H - 1, int(y1)))
    y2 = max(y1 + 1, min(H, int(y2)))
    crop = bw.crop((x1, y1, x2, y2))
    hist = crop.histogram()
    total = crop.size[0] * crop.size[1]
    if not total:
        return 0.0
    return hist[0] / float(total)          # mode '1' 的直方图：0 = 黑


def _h_present(bw, y, x1, x2):
    return _dark_ratio(bw, (x1, y - 1, x2, y + 2)) >= 0.35


def _v_present(bw, x, y1, y2):
    return _dark_ratio(bw, (x - 1, y1, x + 2, y2)) >= 0.35


# ---------------------------------------------------------------- 有线框
def _cells_from_lines(bw, hs, vs, max_cells=400):
    nr, nc = len(hs) - 1, len(vs) - 1
    if nr < 1 or nc < 1:
        return []
    covered = set()
    cells = []
    for r in range(nr):
        for c in range(nc):
            if (r, c) in covered:
                continue
            rs = 1
            while r + rs < nr and not _h_present(bw, hs[r + rs], vs[c], vs[c + 1]):
                rs += 1
            cs = 1
            while c + cs < nc and not _v_present(bw, vs[c + cs], hs[r], hs[r + rs]):
                cs += 1
            for rr in range(r, r + rs):
                for cc in range(c, c + cs):
                    covered.add((rr, cc))
            cells.append({
                "r": r, "c": c, "rs": rs, "cs": cs,
                "bbox": [vs[c], hs[r], vs[c + cs], hs[r + rs]],
            })
            if len(cells) > max_cells:
                return cells
    return cells


# ---------------------------------------------------------------- 无线框
def _bounds_from_centers(centers, lo, hi, pad=30):
    """把列/行中心变成边界。"""
    out = [max(lo, centers[0] - pad)]
    for a, b in zip(centers, centers[1:]):
        out.append((a + b) / 2.0)
    out.append(min(hi, centers[-1] + pad))
    return out


def _cells_from_blocks(blocks, w, h):
    """没有线框时，用文本块坐标聚类出网格（置信度低）。"""
    pts = []
    for b in blocks or []:
        try:
            x, y = float(b.get("x", 0)), float(b.get("y", 0))
        except (TypeError, ValueError):
            continue
        if x <= 0 and y <= 0:
            continue
        pts.append((x, y))
    if len(pts) < 8:
        return [], 0, 0
    ys = [p[1] for p in pts]
    xs = [p[0] for p in pts]
    dys = sorted(b - a for a, b in zip(ys, ys[1:]) if b - a > 1)
    dxs = sorted(b - a for a, b in zip(xs, xs[1:]) if b - a > 1)
    tol_y = max(6.0, (statistics.median(dys) * 0.6) if dys else 20.0)
    tol_x = max(20.0, (statistics.median(dxs) * 1.6) if dxs else 60.0)
    row_c = _cluster(ys, tol_y)
    col_c = _cluster(xs, tol_x)
    if len(row_c) < 2 or len(col_c) < 2 or len(col_c) > 12:
        return [], 0, 0
    hs = _bounds_from_centers(row_c, 0, h, pad=max(8.0, tol_y))
    vs = _bounds_from_centers(col_c, 0, w, pad=max(12.0, tol_x / 2))
    cells = []
    for r in range(len(hs) - 1):
        for c in range(len(vs) - 1):
            cells.append({"r": r, "c": c, "rs": 1, "cs": 1,
                          "bbox": [vs[c], hs[r], vs[c + 1], hs[r + 1]]})
    return cells, len(hs) - 1, len(vs) - 1


# ---------------------------------------------------------------- 对外
def detect(path, blocks=None, max_side=1600):
    """检测一张图里的表格网格。

    返回：
      {"schema":1, "w":.., "h":.., "confidence":"high|low", "source":"lines|blocks|none",
       "tables":[{"bbox":[..], "n_rows":n, "n_cols":m, "header_row":0|-1,
                  "cells":[{"r","c","rs","cs","bbox"}]}]}
    """
    Image = _pil()[0]
    with Image.open(path) as im:
        w0, h0 = im.size
        bw, scale, (W, H) = _to_bw(im, max_side)

    hs = _line_positions(bw, True)
    vs = _line_positions(bw, False)

    nr, nc = len(hs) - 1, len(vs) - 1
    if nr >= 1 and nc >= 1 and nr <= 80 and nc <= 20:
        cells = _cells_from_lines(bw, hs, vs)
        # 线框检测的合理性检查：格子不能太少，也不能把整图包进来
        if cells and len(cells) >= 4:
            bbox = [min(vs), min(hs), max(vs), max(hs)]
            area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            if 0.03 * W * H <= area <= 0.99 * W * H:
                inv = 1.0 / scale if scale else 1.0
                fixed = []
                for c in cells:
                    b = [round(v * inv, 1) for v in c["bbox"]]
                    fixed.append({"r": c["r"], "c": c["c"], "rs": c["rs"], "cs": c["cs"], "bbox": b})
                header = 0 if _looks_like_header(fixed, nr, nc) else -1
                return {
                    "schema": 1, "w": w0, "h": h0,
                    "confidence": "high", "source": "lines",
                    "h_lines": [round(v * inv, 1) for v in hs],
                    "v_lines": [round(v * inv, 1) for v in vs],
                    "tables": [{
                        "bbox": [round(v * inv, 1) for v in bbox],
                        "n_rows": nr, "n_cols": nc, "header_row": header,
                        "cells": fixed,
                    }],
                }

    # 线框失败 → 文本块聚类（低置信度）
    cells, nr2, nc2 = _cells_from_blocks(blocks, w0, h0)
    if cells:
        return {
            "schema": 1, "w": w0, "h": h0,
            "confidence": "low", "source": "blocks",
            "tables": [{
                "bbox": [min(c["bbox"][0] for c in cells), min(c["bbox"][1] for c in cells),
                         max(c["bbox"][2] for c in cells), max(c["bbox"][3] for c in cells)],
                "n_rows": nr2, "n_cols": nc2, "header_row": 0, "cells": cells,
            }],
        }

    return {"schema": 1, "w": w0, "h": h0, "confidence": "none", "source": "none", "tables": []}


def _looks_like_header(cells, nr, nc):
    """第一行有 3 个以上格子、且文字偏短 → 认为有表头。"""
    if nr < 3 or nc < 3:
        return False
    first = [c for c in cells if c["r"] == 0 and c["cs"] == 1]
    return len(first) >= 3


def overlay(path, grid, out_path):
    """把网格画到原图上，用于肉眼检查。"""
    Image = _pil()[0]
    from PIL import ImageDraw
    im = Image.open(path).convert("RGB")
    d = ImageDraw.Draw(im)
    for t in grid.get("tables", []):
        for c in t["cells"]:
            x1, y1, x2, y2 = c["bbox"]
            d.rectangle([x1, y1, x2, y2], outline=(255, 0, 0), width=2)
        b = t["bbox"]
        d.rectangle(b, outline=(0, 120, 255), width=3)
    im.save(out_path)
    return out_path


if __name__ == "__main__":
    import json
    import sys
    import time

    p = sys.argv[1] if len(sys.argv) > 1 else ""
    t0 = time.time()
    g = detect(p)
    print(json.dumps({k: v for k, v in g.items() if k != "tables"}, ensure_ascii=False))
    for t in g["tables"]:
        print("表: %d 行 × %d 列, 表头行=%s, 格子=%d, bbox=%s" % (
            t["n_rows"], t["n_cols"], t["header_row"], len(t["cells"]), t["bbox"]))
    print("耗时 %.2fs" % (time.time() - t0))
