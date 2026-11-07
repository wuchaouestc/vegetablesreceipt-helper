# -*- coding: utf-8 -*-
"""应用设置 —— 全部收在 settings.json 里，启动时读、在设置页改完立刻写回。

为什么要有这个文件
------------------
v3 之前，打印机 VID、界面字号、仔细模式默认值这些东西全是**写死在代码里的常量**
（比如 label_bitmap.py 里的 GPRINTER_VID = "vid_0471"）。
父母想换台打印机，或者年纪大了觉得字太小看不清，就只能找人改代码 —— 这不合理。

三条设计原则
------------
① **读的时候永远给默认值。**
   settings.json 缺字段、被改坏、甚至整个删掉，程序都必须照常启动。
   宁可退回默认值，也不能因为一个配置文件打不开就让全家打不了标签。

② **写的时候先写临时文件再改名。**
   直接覆盖写，写到一半断电就会留下半个坏文件，下次启动就读不出来了。
   os.replace 在 Windows 上是原子的。

③ **这里只放设置，不放业务规则。**
   客户规则在 customers.json（见 customer_profiles.py 的说明）。
   分开的理由：设置坏了最多是界面难看，客户规则坏了是**打错标签**。
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import 路径

HERE = 路径.程序目录()
设置文件 = HERE / 'settings.json'
临时文件 = HERE / 'settings.json.tmp'

版本 = '4.0'

# 默认设置 —— 读不到 / 缺字段时的兜底
默认设置 = {
    '版本': 4,
    '打印机': {
        'vid': 'vid_0471',
        'pid': 'pid_0055',
        '名称': 'Gprinter GP-1326D',
    },
    '界面': {
        '字号档': '大',
        '显示预览': False,
        '显示日志': False,
        '侧边收起': False,
    },
    '识别': {
        '仔细模式默认': False,
        # 手写辅助通道（阶段 3/4 + 合入）。默认「自动」＝每张图先判是不是
        # 手写单据，是就走手写识别库，不是走原流程。判不准时退回原流程，
        # 两条通道都保留疑点、都标待核对，所以判错不会导致印错单。
        '手写辅助模式': '自动',        # 自动 / 关 / 全开 / 逐张
        '手写局部复核': True,
        # 词典文件路径：人工核对网页每确认一次就往里加别名，
        # 识别时据此提示候选（只提示不强改）。文件不存在时按空词典处理，不影响识别。
        '手写词典辅助': str(HERE / 'data' / 'dictionaries' / 'handwriting.json'),
        '手写预算请求数': 8,           # 每张图片允许的额外请求数
        '手写预算秒数': 120,           # 每张图片允许的总时限
        '手写标题置信阈值': 90,        # 标题置信度达到这个值才允许改品类
        '手写复核区域数': 2,           # 每张图片最多复核几个疑点区域
        # 单据级单位一致性：先在本单内取单位共识，再回填「读不出单位」的行。
        # 铁律：只回填空单位行，不改写已读出的值；回填值一定标待核对。
        '手写单位一致性': True,
        '手写单位一致最低行数': 2,     # 共识至少要几行支持才生效
        # 字段识别（独立 rec 模型，观察模式，默认关闭）
        '字段辅助': '关',
        '字段服务地址': 'http://127.0.0.1:18765/recognize_fields',
        '字段区域上限': 32,
        '字段批量大小': 16,
        '字段总时限': 10,
    },
    '系统': {
        '运行模式': 'release',
    },
    '客户': {
        '上限': 15,
    },
}

# 各档位的字号表（像素）。档名 → 各元素字号。
字号档表 = {
    '紧凑': {'页标题': 14, '分区': 10, '正文': 9, '小': 8, '清单': 26, '行高': 21},
    '标准': {'页标题': 11, '分区': 30, '正文': 18, '小': 13, '清单': 12, '行高': 38},
    '大':   {'页标题': 14, '分区': 34, '正文': 21, '小': 15, '清单': 14, '行高': 46},
    '特大': {'页标题': 16, '分区': 38, '正文': 24, '小': 17, '清单': 16, '行高': 52},
}
字号档名单 = list(字号档表.keys())


def 合并默认(读到的):
    """把读到的设置盖在默认设置上。

    只合并字典，列表和标量直接覆盖 —— 用「深合并」是为了让
    settings.json 里只写想改的那几项，其余自动跟默认值走。
    """
    def 盖(底, 上):
        for k, v in 上.items():
            if isinstance(v, dict) and isinstance(底.get(k), dict):
                盖(底[k], v)
            else:
                底[k] = v
        return 底

    return 盖(copy.deepcopy(默认设置), 读到的 or {})


def 读():
    """读设置。文件不存在、读不动、内容坏了 —— 一律退回默认值，不抛异常。"""
    try:
        with 设置文件.open('r', encoding='utf-8') as f:
            内容 = json.load(f)
        return 合并默认(内容)
    except Exception:
        return copy.deepcopy(默认设置)


def 写(设置):
    """把设置写回 settings.json。先写临时文件再原子改名。"""
    try:
        临时文件.write_text(
            json.dumps(设置, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(临时文件, 设置文件)
        return True
    except Exception:
        return False


_cache = None


def 全部(重新读=False):
    """进程内缓存的设置。程序里到处都在读，不要每次都开一次文件。"""
    global _cache
    if _cache is None or 重新读:
        _cache = 读()
    return _cache


def 取(路径, 默认=None):
    """按点分路径取值：取("打印机.vid") / 取("界面.字号档")。"""
    表 = 全部()
    for 段 in 路径.split('.'):
        if not isinstance(表, dict) or 段 not in 表:
            return 默认
        表 = 表[段]
    return 表


def 改(路径, 值):
    """按点分路径改值并立刻落盘。"""
    表 = 全部()
    段们 = 路径.split('.')
    目标 = 表
    for 段 in 段们[:-1]:
        目标 = 目标.setdefault(段, {})
    目标[段们[-1]] = 值
    return 写(表)


def 改多项(改动):
    """一次改多项再落盘：{"打印机.vid": "vid_0471", "界面.字号档": "大"}"""
    表 = 全部()
    for 路径, 值 in 改动.items():
        段们 = 路径.split('.')
        目标 = 表
        for 段 in 段们[:-1]:
            目标 = 目标.setdefault(段, {})
        目标[段们[-1]] = 值
    return 写(表)


def 字号():
    """当前档位的字号表。认不出的档名退回「标准」。"""
    档 = 取('界面.字号档', '大')
    if 档 not in 字号档表:
        档 = '标准'
    return 字号档表[档]


if __name__ == '__main__':
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    s = 全部()
    print('=' * 60)
    print('设置文件：', 设置文件)
    print('存在吗：', 设置文件.is_file())
    print(json.dumps(s, ensure_ascii=False, indent=2))
    print('当前字号档：', s.get('界面', {}).get('字号档', '大'))
