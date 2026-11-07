# -*- coding: utf-8 -*-
"""程序目录 —— 打包成 exe 之后，数据文件该放哪儿。

为什么必须有这个模块
--------------------
所有模块原来都是 `HERE = Path(__file__).resolve().parent`。源码运行时这是对的
（= 打单v4\\）。但用 PyInstaller 打包以后：

  · `--onefile`：`__file__` 指着 `%TEMP%\\_MEIxxxx\\打单助手.pyc` —— 临时解包夹，
    程序一关就被删。settings.json / customers.json / 打单记录 全写进去，
    **下次启动就全没了**。
  · `--onedir`：`__file__` 指着 `打单助手\\_internal\\…` —— 数据会藏在
    `_internal` 里，用户（和我）都找不到。

所以统一走这里：**打包后 = exe 所在的文件夹；源码运行 = 这个 .py 所在的文件夹。**
换句话说，不管怎么跑，"配置文件、客户档案、打单记录、导入副本"永远和程序挨着。

⚠️ 任何新代码都不要再自己写 `Path(__file__).parent`。
"""

from __future__ import annotations

import sys
from pathlib import Path


# 是否以「打包后的 exe」形态运行。启动器.py 会手动把它改成 True，
# 让数据落在程序目录里而不是上一级（见启动器.py 的 校正数据路径）。
是否打包 = bool(getattr(sys, 'frozen', False))


def 程序目录() -> Path:
    """exe（或源码）所在的那个文件夹。"""
    if 是否打包:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def 数据目录():
    """用户数据（打单记录 / 导入副本）放哪儿。

    · 打包后：就在程序目录里（D:\\打单助手\\打单记录\\、D:\\打单助手\\导入\\），
      整个文件夹搬走也不散。
    · 源码运行：放在程序目录的**上一级**（A:\\代码测试\\优化\\打单记录\\）——
      这是原来就定好的位置，用户已经在那儿建好七家客户的文件夹了。
    """
    if 是否打包:
        return 程序目录()
    return 程序目录().parent


# 兼容旧代码的别名：目录 = 程序目录，数据根 = 数据目录
目录 = 程序目录()
数据根 = 数据目录()

_外部配置 = None
_读过外部配置 = False


def 读配置():
    """打包后读 **exe 旁边那个 config.py**（换密钥不用重新打包）。

    源码运行时返回 None —— 那时候 `from config import ...` 直接就能用。
    读不出来也返回 None，绝不抛异常。
    """
    global _外部配置, _读过外部配置
    if _读过外部配置:
        return _外部配置
    _读过外部配置 = True
    if not 是否打包:
        return None
    路 = 程序目录() / 'config.py'
    if not 路.is_file():
        return None
    try:
        import importlib.util
        import sys
        旧 = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec = importlib.util.spec_from_file_location('config_外部', str(路))
            模块 = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(模块)
            _外部配置 = 模块
        finally:
            sys.dont_write_bytecode = 旧
    except Exception:
        _外部配置 = None
    return _外部配置
