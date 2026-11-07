# -*- coding: utf-8 -*-
"""导入落地 —— 拖进来的图片先复制一份到程序目录，后面所有事都读副本。

为什么需要这个
--------------
父母是从**微信聊天框**把单据拖进窗口的。微信给程序的是一个临时文件：

    C:\\Users\\…\\AppData\\Local\\Temp\\…\\4ea90b1e743c363142bb44d03445399c.jpg

名字是一串内容 hash，位置在临时目录。微信一退出、清理软件扫一遍、或者系统
清临时目录，这张图就没了 —— 而缩略图、"只看这张图"、重新识别都还要读它。

所以：**读到文件就立刻复制**到 `导入\\<日期>\\<原名>`，列表里存副本路径，
之后全部操作都读副本。原件被清掉也不影响。

为什么保留原文件名
------------------
界面显示的文件名、`_src`、"双击某张图只看它出来的行" 全都按文件名走。
改成 `时间_原名` 会连累这些地方，收益却只有"好看一点"。所以不改名，
只换目录；真正防重复靠**内容指纹**（`指纹()`）。

三条兜底
--------
· 复制失败 → 用原路径，**绝不**因为复制不了就拒绝加图
· 同名不同图 → 加 `_2` / `_3`（同一张图会先被指纹去重挡住，走不到这里）
· 目录建不出来 → 同上，退回原路径
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime
from pathlib import Path

import 路径

HERE = 路径.程序目录()
默认目录 = 路径.数据目录() / '导入'
目录 = 默认目录
复制 = True


def 换目录(新目录):
    """换落地目录。只有测试会调它。"""
    global 默认目录, 目录
    默认目录 = Path(新目录)
    目录 = 默认目录


def 换复制(开):
    """开/关复制（测试用）。"""
    global 复制
    复制 = bool(开)


def 指纹(路径):
    """文件内容的 md5。读不出来返回空串（当作「不知道」，不参与去重）。"""
    try:
        h = hashlib.md5()
        with open(路径, 'rb') as f:
            for 块 in iter(lambda: f.read(65536), b''):
                h.update(块)
        return h.hexdigest()
    except Exception:
        return ''


def 落地(路径):
    """复制一份到导入目录，返回**该用哪个路径**。永远返回一个可以继续用的路径。"""
    源 = Path(路径)
    if not 复制:
        return 源
    try:
        今天 = datetime.now().strftime('%Y-%m-%d')
        夹 = 默认目录 / 今天
        夹.mkdir(parents=True, exist_ok=True)
        目标 = 夹 / 源.name
        # 同名不同图 → 加 _2/_3；同名同图会被指纹去重挡住，走不到这里
        n = 1
        while 目标.is_file():
            n += 1
            目标 = 夹 / ('%s_%d%s' % (源.stem, n, 源.suffix))
        shutil.copy2(源, 目标)
        return 目标
    except Exception:
        return 源
