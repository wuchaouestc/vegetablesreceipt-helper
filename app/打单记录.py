# -*- coding: utf-8 -*-
"""打单记录 —— 每打一张标签，就往「当天 + 该客户」那个文本文件里追加一行。

为什么是 .txt 制表符，不是 .csv / .xlsx
--------------------------------------
用户的对账场景是「今天给中惠打了什么、几点、几张」。这份文件第一要务是
**一个字都不许变**：Excel 双击打开 csv 会把客编码 `022.2` 改成 `22.2`、
`03.0008` 改成 `3.0008` —— 一份对账用的记录被静默改值，比不方便更糟。
所以写 .txt；要用 Excel 分析时走「数据 → 从文本导入 → 分隔符=制表符 →
列格式选文本」，一样能透视，而且保真。

文件长什么样
------------
    A:\\代码测试\\优化\\打单记录\\中惠\\打单记录_2026-10-17_中惠.txt

    # 打单助手 打单记录    2026-10-17    客户：中惠
    # 只追加、不改旧行。……
    # 时间\t客户\t类型\t品类\t编码\t斤数\t单位\t备注\t图\t结果
    【批次】09:14:22  图片 2 张：①4ea90b1e….jpg ②9f3a2c8d….jpg
    2026-10-17 09:14:22\t中惠\t名牌\t—\t—\t—\t—\t—\t—\t已发出
    2026-10-17 09:14:23\t中惠\t单据\t生姜\t1078.4\t8\t斤\t\t①\t已发出
    2026-10-17 09:14:24\t中惠\t单据\t大蒜\t1078.9\t2.5\t斤\t圆的\t①\t已发出（手改）

三条铁律
--------
① **只追加，不改旧行。** 程序崩了，最坏的后果是最后一行不完整，前面都在。
② **每行 flush。** 否则「指令发出去了、纸出来了、但记录还压在缓冲区里」，
   断电就丢 —— 而断电恰恰是最需要这份记录的时候。
③ **永不抛异常。** 记录写不进去（磁盘满、文件被 Excel 占用、只读），
   标签照样要打得出来。失败只记在 `最后错误` 里，绝不打断打印。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import 路径

HERE = 路径.程序目录()
默认目录 = 路径.数据目录() / '打单记录'

_非法 = re.compile(r'[\\/:*?"<>|\r\n\t]')
最后错误 = ''


def 换目录(新目录):
    """换记录目录。**只有核验/测试会调它**，生产代码不调。"""
    global 默认目录
    默认目录 = Path(新目录)


def _洗干净(客户):
    """把客户名里的非法字符清掉，用作文件夹名。"""
    return _非法.sub('', str(客户))


def 客户夹(客户):
    """某客户的记录文件夹路径。"""
    return 默认目录 / _洗干净(客户)


def 今日路径(客户, 哪天=None):
    """今天该写哪个文件。`哪天` 只给测试用。"""
    if 哪天 is None:
        哪天 = datetime.now()
    return 客户夹(客户) / ('打单记录_%s_%s.txt' % (
        哪天.strftime('%Y-%m-%d'), _洗干净(客户)))


# 表头（每次新建文件时写入）
_表头 = ('# 打单助手 打单记录    {天}    客户：{客户}\n'
         '# 只追加、不改旧行。要用 Excel 分析：数据 → 从文本导入 → 分隔符选制表符 → '
         '列格式选「文本」，否则 022.2 会变成 22.2。\n'
         '# 时间\t客户\t类型\t品类\t编码\t斤数\t单位\t备注\t图\t结果\n')


def _追加(客户, 行, 哪天=None):
    """往今天的文件里追加一行。任何异常都吃掉，返回 False。"""
    global 最后错误
    try:
        夹 = 客户夹(客户)
        夹.mkdir(parents=True, exist_ok=True)
        路 = 今日路径(客户, 哪天)
        if not 路.is_file():
            路.write_text(_表头.format(天=哪天.strftime('%Y-%m-%d'), 客户=客户),
                          encoding='utf-8')
        with 路.open('a', encoding='utf-8') as f:
            f.write(行 + '\n')
            f.flush()
        return True
    except Exception as e:
        最后错误 = str(e)
        return False


def _格(值):
    """一格内容。空的写「—」；换行/制表符换成空格，别把列冲散。"""
    if 值 is None or str(值).strip() == '':
        return '—'
    return str(值).replace('\n', ' ').replace('\t', ' ')


def 记批次(客户, 图片名列表, 时间, 标题=None):
    """写一行批次头，把长文件名收进来，后面的行只用 ①② 指代。

    这样每行短、能对齐，又仍然能追溯到具体哪张单。
    """
    圈 = '①②③④⑤⑥⑦⑧⑨⑩'
    名 = []
    for i, f in enumerate(图片名列表):
        号 = 圈[i] if i < len(圈) else str(i + 1)
        名.append('%s%s' % (号, f))
    行 = '【批次】%s  图片 %d 张：%s' % (时间, len(图片名列表), ' '.join(名))
    if 标题:
        行 = '【批次】%s  %s' % (时间, 标题)
    return _追加(客户, 行)


def 记一张(客户, 类型, 品类, 编码, 斤数, 单位, 备注, 图, 结果, 时间):
    """记一张标签。类型：单据 / 名牌 / 重打。"""
    行 = '\t'.join([
        _格(时间), _格(客户), _格(类型), _格(品类), _格(编码),
        _格(斤数), _格(单位), _格(备注), _格(图), _格(结果),
    ])
    return _追加(客户, 行)


def 今天行数(客户, 哪天=None):
    """今天这个文件里已经记了几行「标签行」（不含注释和批次头）。"""
    try:
        路 = 今日路径(客户, 哪天)
        if not 路.is_file():
            return 0
        n = 0
        for 行 in 路.read_text(encoding='utf-8').splitlines():
            行 = 行.strip()
            if 行 and not 行.startswith('#') and not 行.startswith('【批次】'):
                n += 1
        return n
    except Exception:
        return 0


def 打开今天(客户):
    """用系统默认程序打开今天的文件。返回 (成功吗, 一句话)。"""
    try:
        路 = 今日路径(客户)
        if not 路.is_file():
            return False, '今天还没有记录'
        import os
        os.startfile(str(路))
        return True, str(路)
    except Exception as e:
        return False, str(e)
