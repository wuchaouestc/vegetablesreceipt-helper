# -*- coding: utf-8 -*-
"""识别补丁 —— 在「原版」模块的命名空间里执行（由 加载.py 在 布局.py 之后加载）。

做三件事，每一件都是「包一层」，业务逻辑一行没改：

  ① 坐标聚行：包 `T.tencent_ocr`，把 {text,x,y} 先按 y 聚成行、行内按 x 排序，
     再交给 extract_once。原来是一堆无序块，模型只能靠猜行列；现在是行优先。
  ② 识别诊断：把每张图的块数/行数/重排与否、以及 extract_once 返回的字段名，
     追加写到 识别诊断.log —— 用来确认补丁真的生效了，也用来摸清记录结构。
  ③ 改动率：包 `App._这批打完清空`，在收清单前把「这一批改过几行」写进
     改单统计.jsonl。这是唯一能量化识别质量的指标。

开关都在 settings.json 的「识别」段里，改完重启即生效：
    "识别": { "坐标聚行": true, "聚行容差": 0 }
    聚行容差 = 0 表示自动（取相邻 y 间距中位数的一半）。

所有入口都套了 try/except：补丁出错一律退回原行为，绝不让打单流程停住。
"""

import json as _json
import time as _time
from pathlib import Path as _Path


# ---------- 路径 ----------
def _找根():
    for p in (globals().get('HERE'), globals().get('路径')):
        if p:
            try:
                return _Path(str(p)).parent if _Path(str(p)).name == '_界面补丁' else _Path(str(p))
            except Exception:
                pass
    try:
        return _Path(globals().get('__file__', '.')).resolve().parent.parent
    except Exception:
        return _Path('.')


_根 = _找根()
_诊断 = _根 / '识别诊断.log'
_改单 = _根 / '改单统计.jsonl'


def _记(内容):
    """追加一行诊断。写文件失败不影响任何流程。"""
    try:
        with _诊断.open('a', encoding='utf-8') as f:
            f.write('%s\t%s\n' % (_time.strftime('%m-%d %H:%M:%S'), 内容))
    except Exception:
        pass


def _配置():
    """读 settings.json 的「识别」段。读不到就用默认值。"""
    开 = True
    容差 = 0.0
    try:
        d = _json.loads((_根 / 'settings.json').read_text(encoding='utf-8'))
        r = d.get('识别') or {}
        if '坐标聚行' in r:
            开 = bool(r.get('坐标聚行'))
        try:
            容差 = float(r.get('聚行容差') or 0)
        except (TypeError, ValueError):
            容差 = 0.0
    except Exception:
        pass
    return 开, 容差


def _聚行(blocks, 容差=0.0):
    """把 [{text,x,y}] 按行聚类、行内按 x 排序，返回行优先的扁平列表。

    返回 (新列表, 行数)。块数少于 2 时原样返回。

    容差自动取值：相邻 y 间距（>1px 的）中位数 × 0.5 —— 因为多数行只含一块，
    这个中位数就约等于行距。同一行内不同字号的基线差异通常远小于行距，
    所以不会被拆成两行。
    """
    项 = []
    丢 = 0
    for b in blocks or []:
        try:
            t = str(b.get('text', '')).strip()
            if not t:
                continue                                   # 空块本来就没内容，丢掉无妨
            项.append({'text': t, 'x': float(b.get('x', 0) or 0), 'y': float(b.get('y', 0) or 0), 'raw': b})
        except Exception:
            丢 += 1
    if 丢 or len(项) < 2:
        # 有块读不懂就整体放弃，绝不悄悄丢内容 —— 少一块可能就少一行货
        return list(blocks or []), len(项), 0.0

    项.sort(key=lambda d: d['y'])

    if 容差 <= 0:
        间距 = sorted(项[i]['y'] - 项[i - 1]['y'] for i in range(1, len(项))
                      if 项[i]['y'] - 项[i - 1]['y'] > 1.0)
        中位 = 间距[len(间距) // 2] if 间距 else 20.0
        容差 = max(6.0, 中位 * 0.5)

    行们 = []
    for it in 项:
        if 行们 and abs(it['y'] - 行们[-1]['中心']) <= 容差:
            行们[-1]['块'].append(it)
            行们[-1]['中心'] = sum(x['y'] for x in 行们[-1]['块']) / len(行们[-1]['块'])
        else:
            行们.append({'中心': it['y'], '块': [it]})

    结果 = []
    for 行号, 行 in enumerate(行们):
        行['块'].sort(key=lambda d: d['x'])
        for it in 行['块']:
            b = dict(it['raw'])
            b['_行'] = 行号
            结果.append(b)
    return 结果, len(行们), 容差


# ---------- ① 坐标聚行 ----------
_识别原_tencent_ocr = T.tencent_ocr
_首图 = [True]


def tencent_ocr(*a, **kw):
    blocks = _识别原_tencent_ocr(*a, **kw)
    开, 容差 = _配置()
    if not 开:
        _记('OCR 块 %d｜坐标聚行=关，原样传给模型' % len(blocks or []))
        return blocks
    try:
        新, 行数, 用容差 = _聚行(blocks, 容差)
        if 用容差 <= 0:
            _记('OCR 块 %d｜没做聚行（块太少或有块读不懂），原样传给模型' % len(blocks or []))
            return blocks
        原序 = [str(b.get('text', ''))[:14] for b in (blocks or [])[:3]]
        新序 = [str(b.get('text', ''))[:14] for b in 新[:3]]
        _记('OCR 块 %d → %d 行｜重排=%s｜容差=%.1f｜原序: %s｜新序: %s' % (
            len(blocks or []), 行数, '是' if 原序 != 新序 else '否(本来就按行给的)',
            用容差, ' / '.join(原序), ' / '.join(新序),
        ))
        if _首图[0] and 新:
            _首图[0] = False
            前两行 = []
            号 = object()
            for b in 新:
                if b.get('_行') != 号:
                    号 = b.get('_行')
                    前两行.append(' ▸ ' + str(b.get('text', ''))[:30])
                elif 前两行:
                    前两行[-1] += ' | ' + str(b.get('text', ''))[:30]
            _记('聚行效果样例: ' + ''.join(前两行[:6]))
        return 新
    except Exception as e:                                   # noqa: BLE001
        _记('⚠ 聚行失败，退回原顺序：%r' % (e,))
        return blocks


T.tencent_ocr = tencent_ocr


# ---------- 连续品类边界：原版只能修单处，连续错位会互相卡住 ----------
_识别原_fix_category_boundaries = T.fix_category_boundaries


def _边界分组(records):
    组 = []
    for i, r in enumerate(records):
        cat = str(r.get('category') or '').strip()
        if 组 and 组[-1][0] == cat:
            组[-1][2] = i + 1
        else:
            组.append([cat, i, i + 1])
    return 组


def _边界数值(value):
    from decimal import Decimal, InvalidOperation
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def _边界合计(records, start, end):
    from decimal import Decimal
    total = Decimal(0)
    for r in records[start:end]:
        value = _边界数值(r.get('weight'))
        if value is None:
            return None
        total += value
    return total


def _级联品类边界(records, blocks):
    from decimal import Decimal

    totals = T.category_totals_by_category(records, blocks)
    if not totals:
        return 0
    candidate = [dict(r) for r in records]
    affected = set()
    i = 0
    while i < len(_边界分组(candidate)) - 1:
        groups = _边界分组(candidate)
        cat, start, end = groups[i]
        target = _边界数值(totals.get(cat))
        current = _边界合计(candidate, start, end)
        if target is None or current is None or current == target:
            i += 1
            continue

        total = Decimal(0)
        split = None
        for j in range(start, end):
            value = _边界数值(candidate[j].get('weight'))
            if value is None:
                break
            total += value
            if total == target:
                split = j + 1
                break
            if total > target:
                break
        if split is None or split >= end:
            i += 1
            continue

        next_cat = groups[i + 1][0]
        for r in candidate[split:end]:
            r['category'] = next_cat
        affected.update((cat, next_cat))
        i += 1

    if not affected:
        return 0

    # 接收组可能也错位；涉及品类全部对账后才允许改动真实记录。
    groups = _边界分组(candidate)
    for cat in affected:
        matches = [g for g in groups if g[0] == cat]
        target = _边界数值(totals.get(cat))
        if len(matches) != 1 or target is None:
            return 0
        _, start, end = matches[0]
        if _边界合计(candidate, start, end) != target:
            return 0

    changed = 0
    for original, revised in zip(records, candidate):
        if original.get('category') != revised.get('category'):
            original['category'] = revised['category']
            changed += 1
    return changed


def fix_category_boundaries(records, blocks, log=None):
    changed = _识别原_fix_category_boundaries(records, blocks, log)
    try:
        cascaded = _级联品类边界(records, blocks)
        if cascaded and log:
            log('      🔧 连续品类边界纠错：%d 条' % cascaded)
        return changed + cascaded
    except Exception as e:                                   # noqa: BLE001
        _记('⚠ 连续品类边界纠错已回滚：%r' % (e,))
        return changed


T.fix_category_boundaries = fix_category_boundaries


# ---------- ② 诊断：摸清 extract_once 的返回结构 ----------
_识别原_extract_once = T.extract_once
_首条 = [True]


def extract_once(*a, **kw):
    r = _识别原_extract_once(*a, **kw)
    try:
        if _首条[0]:
            _首条[0] = False
            条数 = len(r) if hasattr(r, '__len__') else -1
            样例 = (r[0] if isinstance(r, (list, tuple)) and r else r)
            if isinstance(样例, dict):
                _记('extract_once 返回 %s 条，字段=%s' % (条数, sorted(样例.keys())))
                _记('记录样例：%s' % _json.dumps(样例, ensure_ascii=False)[:600])
            else:
                _记('extract_once 返回 %s 条，元素类型=%s，值=%r' % (条数, type(样例).__name__, 样例))
    except Exception:                                        # noqa: BLE001
        pass
    return r


T.extract_once = extract_once


# ---------- 同值确认：打开过可疑斤数并原样提交，等于人工确认无误 ----------
_识别原_rec_state = App._rec_state


def _rec_state(self, r):
    state = _识别原_rec_state(r)
    if state[0] == '' and isinstance(r, dict) and r.get('_user_confirmed'):
        return ('✓ 已确认', '', '正常')
    return state


App._rec_state = _rec_state


# ---------- ③ 改动率：收清单前记一笔 ----------
_识别原_这批打完清空 = App._这批打完清空


def _这批打完清空(self, 张数):
    快照 = list(getattr(self, 'records', None) or [])
    try:
        客户 = ''
        v = getattr(self, 'var_customer', None)
        if v is not None and hasattr(v, 'get'):
            客户 = str(v.get() or '')
        改过 = [i + 1 for i, r in enumerate(快照)
                if isinstance(r, dict) and r.get('_user_edited')]
        可疑 = [i + 1 for i, r in enumerate(快照)
                if isinstance(r, dict) and r.get('_suspect')]
        行 = {
            '时间': _time.strftime('%Y-%m-%d %H:%M:%S'),
            '客户': 客户,
            '张数': len(快照),
            '改过': len(改过),
            '可疑': len(可疑),
            '改了哪几行': 改过,
            '可疑哪几行': 可疑,
        }
        with _改单.open('a', encoding='utf-8') as f:
            f.write(_json.dumps(行, ensure_ascii=False) + '\n')
        _记('这批 %d 张：改过 %d 行，可疑 %d 行' % (len(快照), len(改过), len(可疑)))
    except Exception as e:                                   # noqa: BLE001
        _记('⚠ 改动率没记上：%r' % (e,))
    return _识别原_这批打完清空(self, 张数)


App._这批打完清空 = _这批打完清空


# ---------- ④ 别名采集：人工改品类时静默记一笔 ----------
# 为什么不在打单记录里自动反推别名：实测过，不行。
# 152 种写法里绝大多数是两字词（生姜/沙姜/南姜/子姜/老姜/蒜头/蒜米），
# 只差一个字却是完全不同的商品，程序分不清，自动合并会直接把品名改错。
# 所以改成「人改字的那一刻顺手记下来」，积累两天后再由人确认，零打扰零风险。
_识别原_commit_editor = App._commit_editor
_别名档 = _根 / '别名候选.jsonl'


def _commit_editor(self, event=None):
    框 = getattr(self, '_编辑框', None)
    位 = getattr(self, '_编辑位置', None)
    旧 = 新 = 字段 = None
    idx = None
    if 框 is not None and 位:
        try:
            新 = str(框.get()).strip()
            idx, col = 位[0], 位[1]
            字段 = self.可编辑列.get(col)
            if 字段 and 0 <= idx < len(getattr(self, 'records', [])):
                旧 = str(self.records[idx].get(字段, '')).strip()
        except Exception:                                    # noqa: BLE001
            旧 = 新 = 字段 = None
    ok = _识别原_commit_editor(self, event)
    try:
        if ok and 字段 == 'weight' and idx is not None and 新 and 新 == 旧:
            r = self.records[idx]
            r['_weight_edited'] = True
            r['_user_confirmed'] = True
            iid = str(r.get('_id', ''))
            tree = getattr(self, 'tree', None)
            if tree is not None and tree.exists(iid):
                state = self._rec_state(r)
                tree.item(iid, values=self._rec_row_values(idx + 1, r),
                          tags=(state[1],) if state[1] else ())
                tree.selection_set(iid)
            self._refresh_checklist_status()
            self.update_preview()
    except Exception as e:                                   # noqa: BLE001
        _记('⚠ 同值确认后刷新失败：%r' % (e,))
    try:
        if ok and 字段 == 'category' and 旧 and 新 and 旧 != 新:
            客户 = ''
            v = getattr(self, 'var_customer', None)
            if v is not None and hasattr(v, 'get'):
                客户 = str(v.get() or '')
            with _别名档.open('a', encoding='utf-8') as f:
                f.write(_json.dumps({'时间': _time.strftime('%Y-%m-%d %H:%M:%S'),
                                     '客户': 客户, '原写法': 旧, '改成': 新},
                                    ensure_ascii=False) + '\n')
    except Exception as e:                                   # noqa: BLE001
        _记('⚠ 别名采集失败：%r' % (e,))
    return ok


App._commit_editor = _commit_editor


# ---------- 自检：一眼看出补丁有没有挂上 ----------
try:
    开, 容差 = _配置()
    _记('✅ 识别补丁已加载｜坐标聚行=%s｜容差=%s｜改动统计=开｜根目录=%s' % (
        '开' if 开 else '关', '自动' if 容差 <= 0 else ('%.1f' % 容差), _根))
except Exception:                                            # noqa: BLE001
    pass
