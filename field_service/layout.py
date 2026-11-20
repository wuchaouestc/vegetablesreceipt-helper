# -*- coding: utf-8 -*-
"""版面结构卡：把 OCR 文本块整理成「按行、按列、带续行」的结构化提示词。

为什么不是严格的单元格矩阵：
  供应商截图里的「客户明细」往往是一个大单元格，里面横着排了 7~8 条
  "N/斤(编码)"，还会换行。硬套 行×列 的格子会把它们糊成一团。
  这里只做三件事，都是模型现在靠猜的：
    1. 哪几个文本块属于同一行（视觉行）
    2. 每一行里，每块落在哪一列（列边界来自表格竖线；没有竖线就不猜列）
    3. 没有序号/物料名的行是「续行」，紧挨着上一条物料

只给结构和文字归属，不替模型做业务判断（品类归一、跳过、单位、备注）。
"""

from __future__ import annotations

import re
import statistics

_HEADER_WORDS = {
    "序号", "行", "物料", "物料名称", "名称", "品类", "商品名称", "商品",
    "数量", "客户", "客户明细", "明细", "编码", "客户编码", "斤数", "重量",
    "单位", "备注", "规格", "单价", "总价", "金额", "计划", "实采", "详情",
    "预订", "预订数", "配送单", "分类", "鲜肉类", "合计", "小计",
}

_NUM_RE = re.compile(r"^[0-9]{1,3}$")
_SEQ_WORDS = ("序号", "行")
_NAME_WORDS = ("物料", "名称", "品类", "商品")

# 结构卡算法版本：改了 layout.py 就 +1，服务端缓存会自动失效
CARD_VER = "4"


def _is_header_text(t):
    t = (t or "").strip()
    if not t:
        return False
    if t in _HEADER_WORDS:
        return True
    return any(w in t for w in ("序号", "物料", "客户明细", "数量", "单价", "总价", "预订"))


def _rows_from_blocks(blocks):
    """按 y 聚成视觉行。返回 [{'y':.., 'items':[{'x','text'}]}]。"""
    items = []
    for b in blocks or []:
        t = str(b.get("text", "")).strip()
        if not t:
            continue
        try:
            x, y = float(b.get("x", 0) or 0), float(b.get("y", 0) or 0)
        except (TypeError, ValueError):
            continue
        items.append({"x": x, "y": y, "text": t})
    if not items:
        return []

    items.sort(key=lambda d: (d["y"], d["x"]))
    gaps = sorted(b["y"] - a["y"] for a, b in zip(items, items[1:]) if b["y"] - a["y"] > 1.0)
    tol = max(6.0, min(16.0, (statistics.median(gaps) * 0.5) if gaps else 10.0))

    rows = []
    for it in items:
        if rows and abs(it["y"] - rows[-1]["y"]) <= tol:
            rows[-1]["items"].append(it)
            rows[-1]["y"] = (rows[-1]["y"] * (len(rows[-1]["items"]) - 1) + it["y"]) / len(rows[-1]["items"])
        else:
            rows.append({"y": it["y"], "items": [it]})
    for r in rows:
        r["items"].sort(key=lambda d: d["x"])
    return rows


def _find_header(rows):
    """找最像表头的一行。返回 (索引, 命中词数) 或 (-1, 0)。"""
    best, best_hit = -1, 0
    for i, r in enumerate(rows[:8]):
        hit = sum(1 for it in r["items"] if _is_header_text(it["text"]))
        if hit > best_hit:
            best, best_hit = i, hit
    return (best, best_hit) if best_hit >= 2 else (-1, 0)


def _dedup(vals, gap=12):
    out = []
    for v in sorted(vals):
        if not out or v - out[-1] >= gap:
            out.append(v)
    return out


def _bounds_from_grid(grid):
    """列边界：优先表格竖线，其次单元格 x 边界。"""
    if not grid:
        return []
    lines = grid.get("v_lines") or []
    if len(lines) >= 3:
        return [float(v) for v in lines]
    xs = set()
    for t in grid.get("tables", []):
        for c in t["cells"]:
            xs.add(round(float(c["bbox"][0]), 1))
            xs.add(round(float(c["bbox"][2]), 1))
    xs = _dedup(xs)
    return xs if len(xs) >= 3 else []


def _column_labels(bounds, header_row):
    labels = []
    for i in range(len(bounds) - 1):
        label = ""
        if header_row is not None:
            for it in header_row["items"]:
                if bounds[i] <= it["x"] <= bounds[i + 1]:
                    label = it["text"]
                    break
        labels.append(label or "")
    return labels


def _col_of(x, bounds):
    if not bounds or len(bounds) < 2:
        return 0
    for i in range(len(bounds) - 1):
        if bounds[i] <= x <= bounds[i + 1]:
            return i
    return 0 if x < bounds[0] else len(bounds) - 2


def _role(labels, words):
    for i, lab in enumerate(labels):
        if lab and any(w in lab for w in words):
            return i
    return -1


def build_card(blocks, grid=None, max_rows=60, max_chars=3800):
    """生成结构卡。返回 (card_text, meta)。"""
    rows = _rows_from_blocks(blocks)
    if not rows:
        return "", {"reason": "no_blocks"}

    header_idx, header_hit = _find_header(rows)
    header = rows[header_idx] if header_idx >= 0 else None
    bounds = _bounds_from_grid(grid)
    single = len(bounds) < 3
    if single:
        allx = [it["x"] for r in rows for it in r["items"]]
        bounds = [min(allx) - 20, max(allx) + 40] if allx else []
        labels = ["内容"]
    else:
        # 表头命中不足 4 个词时不标列名：截图上表头常被裁掉，硬标会标错
        labels = _column_labels(bounds, header) if header_hit >= 4 else [""] * (len(bounds) - 1)

    body = rows[header_idx + 1:] if header_idx >= 0 else rows
    if not body:
        return "", {"reason": "no_body"}

    ncol = max(1, len(bounds) - 1)
    seq_i = -1 if single else _role(labels, _SEQ_WORDS)
    name_i = -1 if single else _role(labels, _NAME_WORDS)

    lines = []
    item_count = 0
    for r in body:
        # 表头重复行（页脚再印一遍列名）不当数据
        if r["items"] and all(_is_header_text(it["text"]) for it in r["items"]):
            continue
        cells = [[] for _ in range(ncol)]
        for it in r["items"]:
            cells[_col_of(it["x"], bounds)].append(it["text"])
        if single:
            is_item = bool(_NUM_RE.match(cells[0][0])) if cells[0] else False
        elif seq_i >= 0:
            is_item = bool(cells[seq_i] and _NUM_RE.match(cells[seq_i][0]))
        elif name_i >= 0:
            is_item = bool(cells[name_i])
        else:
            # 没识别出列名：前三个有内容的列里，任一列是 1~3 位数字就算物料行
            is_item = any(cells[i] and _NUM_RE.match(cells[i][0])
                          for i in range(min(3, ncol)))
        if is_item:
            item_count += 1
        lines.append({"y": r["y"], "cells": cells, "item": is_item})

    used = [i for i in range(ncol) if any(ln["cells"][i] for ln in lines)]
    if not used:
        return "", {"reason": "empty"}

    out = ["【版面结构（机器解析，只提供行列归属；文字可能认错，最终以图片为准）】"]
    if single:
        out.append("列：未检测到表格竖线，按视觉行分组")
    else:
        if any(labels[ci] for ci in used):
            out.append("列：%s" % " | ".join("C%d %s" % (j + 1, labels[ci] or "（空）")
                                            for j, ci in enumerate(used)))
        else:
            out.append("列：%s（表头未识别，按列序号引用）" % " / ".join(
                "C%d" % (j + 1) for j, _ in enumerate(used)))
        if header is not None:
            out.append("表头（y=%.0f）：%s" % (header["y"], " | ".join(it["text"] for it in header["items"])))
    out.append("说明：同一行里并列的多段文字属于同一个物料；紧跟其后的「续」行也属于上一条物料。")

    for i, ln in enumerate(lines[:max_rows]):
        parts = []
        for ci in used:
            texts = ln["cells"][ci]
            if not texts:
                continue
            txt = " ｜ ".join(t[:30] for t in texts)
            name = labels[ci] if not single and labels[ci] else ("内容" if single else "C%d" % (ci + 1))
            parts.append("%s=%s" % (name, txt))
        if not parts:
            continue
        tag = "R%d" % (i + 1) + ("" if ln["item"] else "（续）")
        out.append("%s %s" % (tag, " | ".join(parts)))

    if len(lines) > max_rows:
        out.append("……（还有 %d 行未列出）" % (len(lines) - max_rows))
    out.append("合计：物料行 %d 条，视觉行 %d 条。" % (item_count, len(lines)))

    card = "\n".join(out)
    if len(card) > max_chars:
        card = card[:max_chars] + "\n……（结构卡过长，已截断）"
    return card, {
        "rows": len(lines), "items": item_count, "cols": len(used),
        "bounds_source": "single" if single else "grid", "header_hit": header_hit,
    }


if __name__ == "__main__":
    import json
    import sys

    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "blocks" in data:
        data = data["blocks"]
    g = None
    if len(sys.argv) > 2:
        import grid
        g = grid.detect(sys.argv[2], data)
    card, meta = build_card(data, g)
    print(card)
    print("\n--- meta ---")
    print(json.dumps(meta, ensure_ascii=False))
