# -*- coding: utf-8 -*-
r"""导入补丁 —— 修「复制其实成功了，但列表还指向微信临时文件」的坑。

原版 `导入落地.落地()` 用的是 `shutil.copy2`：
    copy2 = 复制文件数据 + 复制时间戳/权限
微信拖出来的临时文件名，常常在「数据已经复制完、时间戳还没复制」的瞬间被清理。
这时 copy2 会抛异常，原函数的 except 直接 `return 原路径`——
但 `导入\<日期>\` 里的副本其实**已经写好了**。
列表于是指向一个马上会消失的临时文件，点识别就报「这张图不在了」。

这个补丁在落地返回后做一次兜底：
  · 返回的路径存在 → 原样返回
  · 返回的路径不存在 → 去 `导入\<今天>\` 找同名（或 `同名_2`）且指纹一致的副本，
    找到就返回副本路径
  · 找不到 → 保持原路径（那是真的没复制成功）

只包一层，出错一律退回原行为。
"""

import datetime as _dt
import time as _time
from pathlib import Path as _Path

import 导入落地 as _D

_根 = _Path(str(globals().get("HERE") or "."))
_日志 = _根 / "导入诊断.log"


def _记(msg):
    try:
        if _日志.exists() and _日志.stat().st_size > 2 * 1024 * 1024:
            _日志.replace(_日志.with_suffix(".log.1"))
        with _日志.open("a", encoding="utf-8") as f:
            f.write("%s\t%s\n" % (_time.strftime("%m-%d %H:%M:%S"), msg))
    except Exception:  # noqa: BLE001
        pass


_原落地 = _D.落地


def 落地(路径):
    源 = _Path(str(路径))
    out = _原落地(路径)
    try:
        if out and _Path(str(out)).is_file():
            if str(out) != str(源):
                _记("落地 OK｜源=%s｜副本=%s" % (源, out))
            return out

        # 原函数退回了原路径：看今天目录里有没有已经写好的副本
        夹 = _Path(str(_D.目录)) / _dt.datetime.now().strftime("%Y-%m-%d")
        候选 = []
        if 夹.is_dir():
            指 = _D.指纹(源) if 源.is_file() else ""
            for f in 夹.iterdir():
                try:
                    if not f.is_file() or f.suffix.lower() != 源.suffix.lower():
                        continue
                    if f.stem == 源.stem or f.stem.startswith(源.stem + "_"):
                        候选.append(f)
                except Exception:  # noqa: BLE001
                    continue
            if 指:
                for f in 候选:
                    if _D.指纹(f) == 指:
                        _记("落地兜底（指纹）｜源=%s｜副本=%s" % (源, f))
                        return str(f)
            if 候选:
                候选.sort(key=lambda f: f.stat().st_mtime, reverse=True)
                _记("落地兜底（同名）｜源=%s｜副本=%s" % (源, 候选[0]))
                return str(候选[0])
        _记("落地失败｜源=%s｜返回=%s｜源还在=%s" % (源, out, 源.is_file()))
        return out
    except Exception as e:  # noqa: BLE001
        _记("落地兜底异常｜源=%s｜%r" % (源, e))
        return out


_D.落地 = 落地
_记("✅ 导入补丁已加载｜导入目录=%s" % getattr(_D, "目录", "?"))
