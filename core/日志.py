# -*- coding: utf-8 -*-
"""统一日志模块 —— 全软件所有日志的唯一出口。

为什么需要这一层
----------------
改造前每个写日志的地方各自拼路径：

    _根 = Path(globals().get("HERE"))      # 补丁拿到的「程序根目录」
    _诊断 = _根 / "识别诊断.log"            # 直接落在根目录

补丁机制注入的 ``HERE`` 是**程序根目录**，各补丁拿到后直接拼文件名，
中间没有任何约定层。结果是 11 个诊断日志全部堆在根目录；其中
``patches/原图缩放.py`` 甚至用了相对路径 ``open('结构诊断.log')``，
落在**当前工作目录**而非程序目录。

本模块提供这一层：所有日志统一落到 ``程序目录/log/``，调用方只需要给文件名。

设计约束
--------
1. **日志绝不能影响业务**：任何异常都在本模块内吞掉（返回 None），
   调用方不需要 try/except，也不会因为磁盘满、权限不足而中断识别或打印。
2. **目录自动创建**：一条日志写入时若 ``log/`` 不存在，自动建立，
   避免"目录没了 → 日志静默消失"。
3. **不依赖界面层**：只 import 标准库与 ``路径``，可被 ``core`` / ``app`` /
   ``handwriting`` / 补丁 / 独立服务共用。
4. **``路径`` 不可用时能兜底**：启动极早期（例如 ``启动器.py`` 写
   ``启动失败.log``）``路径`` 可能尚未就绪，此时退回
   ``sys.executable`` / ``__file__`` 推导程序目录，保证日志仍有落点。
5. **大小轮转**：单个文件超过 ``默认上限``（5 MB）后改名为 ``xxx.log.1``
   再重新开始，防止诊断日志无限增长。轮转失败只是不轮转，不影响写入。

约定
----
- 文件名只取 basename：``写日志("识别诊断.log", ...)``，不接受子目录，
  避免拼错路径导致日志跑到别处。
- 每行自动加 ``YYYY-MM-DD HH:MM:SS<TAB>`` 前缀，调用方只传内容。
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

# 单文件上限：超过后轮转为 <文件名>.1（覆盖旧的 .1）
默认上限 = 5 * 1024 * 1024

# 行首时间戳格式（全软件统一，便于跨日志对齐排查）
时间格式 = '%Y-%m-%d %H:%M:%S'

# 同一进程内多个线程写同一个文件时串行化，避免行被截断交错。
# 跨进程（主程序与结构服务）写的是不同文件，不需要跨进程锁。
_写锁 = threading.Lock()


def _程序目录() -> Path:
    """程序目录。优先用 ``路径`` 的契约，失败时按打包形态兜底。"""
    try:
        import 路径

        return Path(路径.程序目录())
    except Exception:
        pass
    try:
        if getattr(sys, 'frozen', False):
            return Path(sys.executable).resolve().parent
    except Exception:
        pass
    # 源码形态：本文件位于 <根>/core/日志.py，上两级即程序根目录
    try:
        return Path(__file__).resolve().parents[1]
    except Exception:
        return Path('.').resolve()


def 日志目录(根=None) -> Path:
    """返回日志目录 ``<根>/log`` 并确保存在。``根`` 仅供测试注入。"""
    目录 = (Path(根) if 根 else _程序目录()) / 'log'
    目录.mkdir(parents=True, exist_ok=True)
    return 目录


def 日志文件(名称: str, 根=None) -> Path:
    """返回某个日志的完整路径（不创建文件）。

    用于需要把**路径**交给别人的场景，例如把结构服务的 stdout 重定向到
    ``结构服务启动.log``。
    """
    名字 = Path(str(名称)).name or '日志.log'
    return 日志目录(根) / 名字


def 写日志(名称: str, 内容, 根=None, 上限=默认上限) -> None:
    """追加一行日志。**永不抛异常**。

    参数
    ----
    名称 : 日志文件名，例如 ``'识别诊断.log'``
    内容 : 任意可转字符串的内容；多行内容会原样写入，时间戳只加在第一行
    根   : 程序根目录覆盖值，仅测试使用
    上限 : 单文件字节上限；传 ``0`` 或 ``None`` 表示不轮转
    """
    try:
        文件 = 日志文件(名称, 根)
        with _写锁:
            if 上限:
                try:
                    if 文件.is_file() and 文件.stat().st_size > 上限:
                        文件.replace(文件.with_name(文件.name + '.1'))
                except OSError:
                    # 被占用（Windows 常见）或权限不足：放弃轮转，继续追加
                    pass
            with 文件.open('a', encoding='utf-8') as 流:
                流.write('%s\t%s\n' % (time.strftime(时间格式), 内容))
    except Exception:
        # 日志写失败一律吞掉：宁可少一条日志，也不能影响打单
        pass
