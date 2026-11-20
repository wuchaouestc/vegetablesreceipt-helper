# -*- coding: utf-8 -*-
"""结构补丁 —— 在「原版」模块的命名空间里执行（由 加载.py 在 识别补丁.py 之后加载）。

作用：把 PP-Structure / 几何引擎解析出的「版面结构卡」拼到提示词后面，让模型
从「猜行列」变成「读行列」。业务逻辑（OCR / 规则 / 打印）一行没改。

接入点（两个，都是包一层）：
  ① T.build_prompt(profile)  —— 每次扫描开始时调用一次，在这里判断：
        当前客户是不是印刷单、结构增强开关、客户白名单
  ② T.extract_once(img, blocks, effort, prompt)
        —— 印刷/手写两条路径、重试/共识的每一次抽取都经过这里

为什么包 extract_once 而不是 tencent_ocr：
  · 它有 blocks，能直接落格；
  · process_image 之后的 cross_check / check_coverage 用的是原始 blocks，
    改这里不会影响它们。

所有入口都套 try/except：补丁出错一律退回原行为，绝不让打单流程停住。

开关（settings.json → 识别）：
    "结构增强": "自动"      关 / 自动（印刷开、手写关） / 开
    "结构服务": "http://127.0.0.1:18765"
    "结构超时毫秒": 25000
    "结构客户": []          空 = 不限制；填了则只对名单里的客户开
    "结构引擎": "自动"      自动（几何优先，几何搞不定才用 PP）/ PP（每张都用）/ 几何（从不用）
"""

import json as _json
import threading as _threading
import time as _time
import urllib.error as _urlerr
import urllib.request as _urlreq
from pathlib import Path as _Path

# ---------------------------------------------------------------- 路径/日志
_根 = _Path(str(globals().get("HERE") or "."))
_诊断 = _根 / "结构诊断.log"
_卡片夹 = _根 / "结构卡"
_线程 = _threading.local()
_缓存 = {}
_缓存锁 = _threading.Lock()
_状态 = {"连续失败": 0, "暂停到": 0.0, "注入": 0, "跳过": 0}


def _记(内容):
    try:
        if _诊断.exists() and _诊断.stat().st_size > 3 * 1024 * 1024:
            _诊断.replace(_诊断.with_suffix(".log.1"))
        with _诊断.open("a", encoding="utf-8") as f:
            f.write("%s\t%s\n" % (_time.strftime("%m-%d %H:%M:%S"), 内容))
    except Exception:  # noqa: BLE001
        pass


def _配置():
    默认 = {"模式": "自动", "服务": "http://127.0.0.1:18765", "超时": 25000,
            "客户": [], "引擎": "自动"}
    try:
        d = _json.loads((_根 / "settings.json").read_text(encoding="utf-8"))
        r = d.get("识别") or {}
        return {
            "模式": str(r.get("结构增强", 默认["模式"]) or 默认["模式"]),
            "服务": str(r.get("结构服务", 默认["服务"]) or 默认["服务"]).rstrip("/"),
            "超时": int(r.get("结构超时毫秒", 默认["超时"]) or 默认["超时"]),
            "客户": [str(x) for x in (r.get("结构客户") or [])],
            "引擎": str(r.get("结构引擎", 默认["引擎"]) or 默认["引擎"]),
        }
    except Exception:  # noqa: BLE001
        return 默认


def _允许(profile):
    c = _配置()
    if c["模式"] == "关":
        return False, "开关=关"
    cust = ""
    if isinstance(profile, dict):
        cust = str(profile.get("folder") or profile.get("customer") or "")
    try:
        hand = bool(T.is_handwritten(profile))
    except Exception:  # noqa: BLE001
        hand = False
    if c["模式"] == "自动" and hand:
        return False, "手写单不注入"
    if c["客户"] and cust not in c["客户"]:
        return False, "客户不在白名单（%s）" % cust
    return True, cust


# ---------------------------------------------------------------- 调服务
def _health(服务, 超时=1.0):
    try:
        req = _urlreq.Request(服务 + "/health", method="GET")
        with _urlreq.urlopen(req, timeout=超时) as r:
            d = _json.loads(r.read().decode("utf-8"))
            return bool(d.get("ok"))
    except Exception:  # noqa: BLE001
        return False


def _取卡(img_path, blocks, 客户=""):
    c = _配置()
    if _状态["暂停到"] > _time.time():
        return None
    try:
        p = _Path(str(img_path))
        st = p.stat()
        key = "%s|%d|%d" % (p.name, st.st_size, int(st.st_mtime))
    except Exception:  # noqa: BLE001
        key = str(img_path)

    with _缓存锁:
        hit = _缓存.get(key)
    if hit and _time.time() - hit[1] < 600:
        return hit[0]

    if not _health(c["服务"], 1.0):
        _状态["暂停到"] = _time.time() + 30
        _状态["跳过"] += 1
        _记("结构服务不可用（%s），30 秒内不再尝试" % c["服务"])
        return None

    payload = {
        "path": str(img_path),
        "blocks": [{"text": b.get("text", ""), "x": b.get("x", 0), "y": b.get("y", 0)}
                   for b in (blocks or [])],
        "pp_mode": {"自动": "auto", "PP": "always", "pp": "always", "几何": "never"}.get(
            c["引擎"], "auto"),
    }
    data = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = _urlreq.Request(c["服务"] + "/structure", data=data,
                          headers={"Content-Type": "application/json; charset=utf-8"},
                          method="POST")
    try:
        t0 = _time.time()
        with _urlreq.urlopen(req, timeout=max(2.0, c["超时"] / 1000.0)) as r:
            d = _json.loads(r.read().decode("utf-8"))
        card = (d.get("card") or "").strip()
        if not d.get("ok") or not card:
            raise RuntimeError(d.get("error") or "服务没有返回结构卡")
        meta = d.get("meta") or {}
        _状态["连续失败"] = 0
        _状态["注入"] += 1
        with _缓存锁:
            _缓存[key] = (card, _time.time())
        _记("结构卡 OK｜%s｜来源=%s｜行=%s 物料=%s｜%.0fms｜%d 字" % (
            _Path(str(img_path)).name, d.get("source"), meta.get("rows"),
            meta.get("items"), (_time.time() - t0) * 1000, len(card)))
        try:
            _卡片夹.mkdir(exist_ok=True)
            if len(list(_卡片夹.glob("*.txt"))) < 400:
                (_卡片夹 / ("%s.%s.txt" % (_Path(str(img_path)).stem[:40], key[-8:].replace("|", "_")))).write_text(
                    "# %s\n# 客户=%s 来源=%s\n\n%s\n" % (img_path, 客户, d.get("source"), card),
                    encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        return card
    except Exception as e:  # noqa: BLE001
        _状态["连续失败"] += 1
        _状态["跳过"] += 1
        _记("结构卡失败（第 %d 次）：%r" % (_状态["连续失败"], e))
        if _状态["连续失败"] >= 3:
            _状态["暂停到"] = _time.time() + 120
            _记("连续失败 3 次，暂停 120 秒")
        return None


# ---------------------------------------------------------------- 包装
_原_build_prompt = T.build_prompt


def build_prompt(profile):
    p = _原_build_prompt(profile)
    try:
        # 客户只负责输出格式；结构增强由全局开关控制，不受客户类型/白名单影响。
        ok, why = _配置()["模式"] != "关", ""
        _线程.允许 = ok
        _线程.客户 = why if ok else ""
        _线程.原因 = "" if ok else why
        if not ok:
            _记("跳过客户=%s：%s" % (str(profile.get("folder") if isinstance(profile, dict) else ""), why))
    except Exception as e:  # noqa: BLE001
        _线程.允许 = False
        _记("⚠ 判断开关失败：%r" % (e,))
    return p


T.build_prompt = build_prompt


_原_extract_once = T.extract_once


def extract_once(img_path=None, blocks=None, effort=None, prompt=None, *a, **kw):
    try:
        if getattr(_线程, "允许", False) and blocks:
            card = _取卡(img_path, blocks, getattr(_线程, "客户", ""))
            if card:
                prompt = (prompt or "") + "\n\n" + card
    except Exception as e:  # noqa: BLE001
        _记("⚠ 结构卡注入失败，按原样识别：%r" % (e,))
    return _原_extract_once(img_path, blocks, effort, prompt, *a, **kw)


T.extract_once = extract_once


# ---------------------------------------------------------------- 自检
try:
    _c = _配置()
    _记("✅ 结构补丁已加载｜模式=%s｜引擎=%s｜服务=%s｜白名单=%s" % (
        _c["模式"], _c["引擎"], _c["服务"], ",".join(_c["客户"]) or "（不限）"))
except Exception:  # noqa: BLE001
    pass
