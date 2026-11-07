# -*- coding: utf-8 -*-
"""打单助手 —— 主程序（GUI 主体）。

流程：拖入单据图片 → 腾讯云 OCR → DeepSeek 识别成结构化清单 → 核对 → 打印标签。

本文件是主程序的主体：窗口、三个页面（识单 / 历史 / 设置）、
后台线程（Worker）、打印调度。业务规则在 ttsb2 / customer_profiles 里，
标签排版在 label_bitmap 里，数据落盘在 打单记录 / 打印历史 / 标签余量 / 导入落地 里。

启动时会把 _界面补丁 下的 布局.py / 识别补丁.py / 结构补丁.py 挂进来
（见 加载.py），它们通过 monkey-patch 覆盖本类里的布局/识别/结构方法。
"""

from __future__ import annotations

import copy
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customer_profiles as CP
import label_bitmap as LB
import ttsb2 as T
import app_settings as 设置
import 标签余量 as LY
import 打单记录 as DR
import 打印历史 as PH
import 导入落地 as 落地

try:
    from PIL import Image, ImageTk
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False

import 路径
HERE = 路径.程序目录()

IMG_EXT = ('.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tif', '.tiff')

# 拖拽（可选）
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _HAS_DND = True
except Exception:
    _HAS_DND = False


# ---------------------------------------------------------------------------
# DPI 感知 & 缩放
# ---------------------------------------------------------------------------

界面缩放 = 1.0


def 声明DPI感知():
    """让 Windows 别拉伸我们的窗口。必须在**建任何窗口之前**调。"""
    global 界面缩放
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    try:
        import tkinter as tk
        r = tk.Tk()
        界面缩放 = r.winfo_fpixels('1i') / 96.0
        r.destroy()
    except Exception:
        界面缩放 = 1.0


def P(v):
    """把「设计尺寸」换算成当前屏幕上的真实像素。"""
    return int(v * 界面缩放)


def 走遍控件(widget):
    """把一个控件连同它下面所有的子孙都吐出来（深度优先）。"""
    yield widget
    for c in widget.winfo_children():
        yield from 走遍控件(c)


_能拿焦点的类 = frozenset()


# ---------------------------------------------------------------------------
# 主题
# ---------------------------------------------------------------------------

class 主题:
    底色 = '#f5f6f8'
    面 = '#ffffff'
    面淡 = '#fafbfc'
    边框 = '#e0e2e6'
    正文 = '#1f2329'
    次要 = '#6b7280'
    淡 = '#9aa0a8'
    主色 = '#2f6fed'
    主色淡 = '#e8f0fe'
    成功 = '#1a9e63'
    成功淡 = '#e6f7ef'
    警告 = '#d97706'
    警告淡 = '#fef3e2'
    危险 = '#dc2626'
    危险淡 = '#fee2e2'
    字体 = '微软雅黑'
    字号小 = 9
    字号 = 10


def 主按钮(parent, text, command, 色调='主'):
    """实心按钮 —— 用来标出「这一页该点哪个」的主要操作。"""
    色 = {'主': 主题.主色, '成功': 主题.成功, '危险': 主题.危险}.get(色调, 主题.主色)
    b = tk.Button(parent, text=text, command=command, bg=色, fg='#ffffff',
                  relief='flat', font=(主题.字体, 主题.字号, 'bold'), cursor='hand2',
                  padx=P(14), pady=P(5), activebackground=色, activeforeground='#ffffff')
    return b


def 次按钮(parent, text, command, 危险=False):
    """白底细边框按钮 —— 次要操作全用它，尺寸统一。"""
    fg = 主题.危险 if 危险 else 主题.正文
    b = tk.Button(parent, text=text, command=command, bg=主题.面, fg=fg,
                  relief='flat', font=(主题.字体, 主题.字号), cursor='hand2',
                  padx=P(12), pady=P(4), highlightthickness=1,
                  highlightbackground=主题.边框)
    return b


def 分区标题(parent, 标题, 说明=''):
    """一段内容的小标题 + 一行灰色说明。"""
    框 = tk.Frame(parent, bg=parent.cget('bg') if parent.cget('bg') else 主题.面)
    t = tk.Label(框, text=标题, bg=框.cget('bg'), fg=主题.正文,
                 font=(主题.字体, 主题.字号, 'bold'))
    t.pack(side='left')
    if 说明:
        s = tk.Label(框, text=说明, bg=框.cget('bg'), fg=主题.淡,
                     font=(主题.字体, 主题.字号小))
        s.pack(side='left', padx=(P(8), 0))
    return 框


def 说明字(parent, text):
    """一行灰色小字，用来解释「这项是干嘛的 / 改了会怎样」。"""
    return tk.Label(parent, text=text, bg=parent.cget('bg'), fg=主题.淡,
                    font=(主题.字体, 主题.字号小), anchor='w', justify='left')


def 气泡提示(widget, text):
    """给控件挂一个悬浮提示。"""
    def 进(e):
        try:
            widget._tip = tk.Toplevel(widget)
            widget._tip.wm_overrideredirect(True)
            widget._tip.wm_geometry('+%d+%d' % (e.x_root + 16, e.y_root + 12))
            l = tk.Label(widget._tip, text=text, bg='#333', fg='#fff',
                         font=(主题.字体, 主题.字号小), padx=8, pady=4)
            l.pack()
        except Exception:
            pass

    def 出(e):
        try:
            if getattr(widget, '_tip', None):
                widget._tip.destroy()
                widget._tip = None
        except Exception:
            pass
    widget.bind('<Enter>', 进)
    widget.bind('<Leave>', 出)


class 滚动区(tk.Frame):
    """带垂直滚动条的容器，真正内容放在 ``内容`` 中。"""

    def __init__(self, parent):
        super().__init__(parent, bg=主题.底色)
        self.canvas = tk.Canvas(self, bg=主题.底色, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.内容 = tk.Frame(self.canvas, bg=主题.底色)
        self._窗口 = self.canvas.create_window((0, 0), window=self.内容, anchor='nw')
        self.内容.bind('<Configure>', self._刷新滚动范围)
        self.canvas.bind('<Configure>', self._同步宽度)
        self.canvas.bind('<Enter>', lambda e: self.canvas.bind_all('<MouseWheel>', self._滚轮))
        self.canvas.bind('<Leave>', lambda e: self.canvas.unbind_all('<MouseWheel>'))

    def _刷新滚动范围(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def _同步宽度(self, event):
        self.canvas.itemconfigure(self._窗口, width=event.width)

    def _滚轮(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), 'units')
        return 'break'


class 设置卡(tk.Frame):
    """可折叠卡片；``内容`` 始终存在，折叠只改变显示状态。"""

    def __init__(self, parent, 标题, 说明='', 展开=True):
        super().__init__(parent, bg=主题.面, highlightthickness=1,
                         highlightbackground=主题.边框)
        self.标题 = 标题
        self.展开 = bool(展开)
        self.标题钮 = tk.Button(self, text='', command=self.切换, anchor='w',
                                bg=主题.面, fg=主题.正文, relief='flat', cursor='hand2',
                                font=(主题.字体, 主题.字号, 'bold'), padx=P(12), pady=P(9))
        self.标题钮.pack(fill='x')
        if 说明:
            说明字(self, 说明).pack(fill='x', padx=P(14), pady=(0, P(5)))
        self.内容 = tk.Frame(self, bg=主题.面)
        if self.展开:
            self.内容.pack(fill='both', expand=True, padx=P(12), pady=(0, P(12)))
        self._刷新标题()

    def _刷新标题(self):
        self.标题钮.configure(text=('%s  %s' % ('[-]' if self.展开 else '[+]', self.标题)))

    def 切换(self):
        self.展开 = not self.展开
        if self.展开:
            self.内容.pack(fill='both', expand=True, padx=P(12), pady=(0, P(12)))
        else:
            self.内容.pack_forget()
        self._刷新标题()


# ---------------------------------------------------------------------------
# 后台线程
# ---------------------------------------------------------------------------

class Worker:
    """把耗时任务放到后台线程，通过 queue 把进度/结果/错误传回主线程。"""

    def __init__(self, app):
        self.app = app
        self.q = queue.Queue()
        self._thread = None

    def _pump(self):
        try:
            while True:
                类型, 数据 = self.q.get_nowait()
                if 类型 == 'log':
                    self.on_log(数据)
                elif 类型 == 'progress':
                    self.on_progress(数据)
                elif 类型 == 'done':
                    self.on_done(数据)
                elif 类型 == 'error':
                    self.on_error(数据)
        except queue.Empty:
            pass
        self.app.root.after(80, self._pump)

    def run(self, fn, *args, **kw):
        def 目标():
            try:
                结果 = fn(*args, **kw)
                self.q.put(('done', 结果))
            except Exception as e:
                self.q.put(('error', e))
        self._thread = threading.Thread(target=目标, daemon=True)
        self._thread.start()

    def progress(self, v):
        self.q.put(('progress', v))

    def on_log(self, msg):
        self.app.log(msg)

    def on_progress(self, v):
        self.app._on_progress(v)

    def on_done(self, r):
        self.app._on_task_done(r)

    def on_error(self, e):
        self.app.on_task_error(e)


# ---------------------------------------------------------------------------
# 主应用
# ---------------------------------------------------------------------------

class App:
    def __init__(self, root):
        self.root = root
        self.files = []
        self.records = []
        self._原始记录 = None
        self._删除记录 = []
        self._待打 = None
        self._编辑框 = None
        self._编辑位置 = None
        self._文件指纹 = {}
        self._记录序号 = 0
        self._图片筛选 = None
        self._筛选 = '全部'
        self.busy = False
        self.preview_on = True
        self._上一批 = None
        self.var_customer = tk.StringVar()
        self.var_effort = tk.BooleanVar()
        self.可编辑列 = {1: '_state', 2: 'category', 3: 'customer_code', 4: 'weight', 5: 'unit', 6: 'remark'}
        self.列名 = {1: '状态', 2: '品类', 3: '客户编码', 4: '斤数', 5: '单位', 6: '备注'}
        self.worker = Worker(self)
        self._init_style()
        self._build_ui()
        self._注册拖拽()
        self.root.after(100, self.worker._pump)
        self.root.title('打单助手 v6.1')

    # ---------- 样式 ----------
    def _init_style(self):
        self.root.configure(bg=主题.底色)
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except Exception:
            pass

    # ---------- UI ----------
    def _build_ui(self):
        self._当前页 = None
        self._主区 = tk.Frame(self.root, bg=主题.底色)
        self._主区.pack(fill='both', expand=True)
        self._build_sidebar()
        self._build_topbar()
        self._页容器 = tk.Frame(self._主区, bg=主题.底色)
        self._页容器.pack(fill='both', expand=True)
        self.goto('scan')

    def _build_sidebar(self):
        self._侧边 = tk.Frame(self.root, bg=主题.面, width=P(160))
        self._侧边.pack(side='left', fill='y')
        self._侧边.pack_propagate(False)
        self._导航按钮 = {}
        for 键, 名 in (('scan', '识单'), ('history', '历史'), ('settings', '设置')):
            b = tk.Label(self._侧边, text=名, bg=主题.面, fg=主题.次要,
                         font=(主题.字体, 主题.字号 + 1), padx=P(16), pady=P(12),
                         anchor='w', cursor='hand2')
            b.pack(fill='x')
            b.bind('<Button-1>', lambda e, k=键: self.goto(k))
            self._导航按钮[键] = b
        self._导航刷新('scan')

    def _切换侧边(self):
        self._侧边收起()

    def _侧边收起(self):
        try:
            if self._侧边.winfo_manager():
                self._侧边.pack_forget()
            else:
                self._侧边.pack(side='left', fill='y', before=self._主区)
        except Exception:
            pass

    def _导航条(self):
        pass

    def _导航悬停(self, e):
        pass

    def _导航刷新(self, 当前):
        for 键, b in self._导航按钮.items():
            if 键 == 当前:
                b.configure(bg=主题.主色淡, fg=主题.主色)
            else:
                b.configure(bg=主题.面, fg=主题.次要)

    def _build_topbar(self):
        self._顶栏 = tk.Frame(self._主区, bg=主题.面, height=P(48))
        self._顶栏.pack(fill='x')
        self._顶栏.pack_propagate(False)
        self.lbl_title = tk.Label(self._顶栏, text='打单助手', bg=主题.面,
                                  fg=主题.正文, font=(主题.字体, 主题.字号 + 3, 'bold'))
        self.lbl_title.pack(side='left', padx=P(16))
        self._纸chip = tk.Label(self._顶栏, text='', bg=主题.面, fg=主题.次要,
                                cursor='hand2', font=(主题.字体, 主题.字号小))
        self._纸chip.pack(side='right', padx=P(16))
        self._纸chip.bind('<Button-1>', lambda e: self._点纸chip())
        self._刷纸chip()

    def _刷纸chip(self):
        状态 = LY.读()
        self._纸chip.configure(text='标签纸 ' + LY.徽章(状态))

    def _点纸chip(self):
        self._跳到标签纸卡()

    def _跳到标签纸卡(self):
        self.goto('settings')

    def set_status(self, text, 色=None):
        try:
            self.lbl_check_status.configure(text=text, fg=色 or 主题.次要)
        except Exception:
            pass

    def _on_progress(self, v):
        try:
            self.set_status('正在识别 %d / %d…' % (v[0], v[1]), 主题.主色)
        except Exception:
            pass

    # ---------- 设置页 ----------
    def _build_page_settings(self, parent):
        page = tk.Frame(parent, bg=主题.底色)
        self._设置页 = page
        self._设置卡 = {}
        滚动 = 滚动区(page)
        滚动.pack(fill='both', expand=True, padx=P(12), pady=P(10))
        self._设置滚动区 = 滚动
        self._搭_客户卡(滚动.内容)
        self._搭_标签纸卡(滚动.内容)
        self._搭_打印机卡(滚动.内容)
        self._搭_界面卡(滚动.内容)
        self._搭_数据卡(滚动.内容)
        self._搭_备份卡(滚动.内容)
        self._搭_自检卡(滚动.内容)
        self._搭_关于卡(滚动.内容)
        return page

    def _设置卡片(self, 键, 标题, 说明='', 展开=False):
        卡 = 设置卡(self._设置滚动区.内容, 标题, 说明, 展开=展开)
        卡.pack(fill='x', expand=True, pady=(0, P(8)))
        self._设置卡[键] = 卡
        return 卡.内容

    def _设置行(self, parent, 标题):
        行 = tk.Frame(parent, bg=主题.面)
        行.pack(fill='x', pady=P(3))
        tk.Label(行, text=标题, bg=主题.面, fg=主题.正文, width=12,
                 anchor='w', font=(主题.字体, 主题.字号)).pack(side='left')
        return 行

    def _搭_客户卡(self, parent):
        体 = self._设置卡片('客户', '客户管理', '客户规则保存在 customers.json；删除规则不会删除单据图片。', 展开=True)
        钮 = tk.Frame(体, bg=主题.面)
        钮.pack(fill='x', pady=(0, P(6)))
        for 文字, 命令 in (('新建', self._设置_新建客户), ('复制', self._设置_复制客户),
                           ('编辑规则', self._设置_编辑客户), ('改名', self._设置_改名),
                           ('删除', self._设置_删除客户)):
            次按钮(钮, 文字, 命令, 危险=(文字 == '删除')).pack(side='left', padx=(0, P(5)))
        self._设置客户tree = ttk.Treeview(体, columns=('客户', '文件夹', '类型', '编码', '排序'),
                                          show='headings', height=6, selectmode='browse')
        for 列, 宽 in (('客户', 120), ('文件夹', 130), ('类型', 100), ('编码', 80), ('排序', 80)):
            self._设置客户tree.heading(列, text=列)
            self._设置客户tree.column(列, width=P(宽), anchor='center' if 列 != '文件夹' else 'w')
        self._设置客户tree.pack(fill='both', expand=True)
        self._设置_刷新客户表()

    def _设置_刷新客户表(self):
        if not hasattr(self, '_设置客户tree') or not self._设置客户tree.winfo_exists():
            return
        self._设置客户tree.delete(*self._设置客户tree.get_children())
        for 名 in CP.list_customers():
            档 = CP.get_profile(名)
            self._设置客户tree.insert('', 'end', iid=名, values=(
                名, 档.get('folder', 名),
                '手写单' if CP.is_handwritten(档) else '电脑单',
                '显示' if 档.get('show_code', True) else '不显示',
                '开启' if 档.get('sort_categories') else '关闭'))

    def _同步客户控件(self, 首选=None):
        客户们 = CP.list_customers()
        当前 = 首选 if 首选 in 客户们 else self.var_customer.get()
        if 当前 not in 客户们:
            当前 = 客户们[0] if 客户们 else ''
        self.var_customer.set(当前)
        if hasattr(self, '_客户列表') and self._客户列表.winfo_exists():
            self._客户列表.configure(values=客户们)
        self._设置_刷新客户表()
        if hasattr(self, '_历史客户框') and self._历史客户框.winfo_exists():
            self._历史客户框.configure(values=['全部客户'] + 客户们)
        self._on_customer_change()

    def _同步设置控件(self):
        配置 = 设置.全部(重新读=True)
        打印 = 配置.get('打印机', {})
        界面 = 配置.get('界面', {})
        for 名, 值 in (('_打印通道变量', 打印.get('通道', 'usb')),
                       ('_打印名称变量', 打印.get('名称', '')),
                       ('_打印vid变量', 打印.get('vid', '')),
                       ('_打印pid变量', 打印.get('pid', '')),
                       ('_字号变量', 界面.get('字号档', '大')),
                       ('_显示预览变量', bool(界面.get('显示预览', False))),
                       ('_显示日志变量', bool(界面.get('显示日志', False))),
                       ('_侧边收起变量', bool(界面.get('侧边收起', False))),
                       ('_仔细默认变量', bool(配置.get('识别', {}).get('仔细模式默认', False)))):
            if hasattr(self, 名):
                getattr(self, 名).set(值)
        self.var_effort.set(bool(配置.get('识别', {}).get('仔细模式默认', False)))
        状态 = LY.读()
        for 名, 值 in (('_每卷变量', 状态.get('每卷张数', 800)),
                       ('_损耗变量', 状态.get('损耗预留', 50)),
                       ('_已打变量', 状态.get('已用张数', 0))):
            if hasattr(self, 名):
                getattr(self, 名).set(str(值))
        self._设置_刷标签纸()
        if hasattr(self, '_侧栏打印机'):
            self._侧栏打印机.configure(text='40×30mm · ' + str(打印.get('名称', '')))

    def _设置_选中的客户(self):
        if not hasattr(self, '_设置客户tree'):
            return None
        选中 = self._设置客户tree.selection()
        return 选中[0] if 选中 else None

    def _设置_新建客户(self):
        if CP.还能加几个() <= 0:
            messagebox.showwarning('客户已满', '客户数量已到上限，请先删除不再使用的客户。')
            return False
        模板 = self._设置_选中的客户() or (CP.list_customers() or [''])[0]
        值 = self._客户窗('新建客户', {'名称': '', '模板': 模板, '文件夹': ''})
        if not 值:
            return False
        名 = 值['名称'].strip()
        if not 名:
            messagebox.showwarning('内容不对', '客户名不能为空。')
            return False
        成功, 消息 = CP.新建档案(名, 值['模板'], 值['文件夹'].strip() or 名)
        if not 成功:
            messagebox.showwarning('新建失败', 消息)
            return False
        self._同步客户控件(名)
        return True

    def _设置_复制客户(self):
        原名 = self._设置_选中的客户()
        if not 原名:
            messagebox.showinfo('没选中', '先选择一个客户。')
            return False
        值 = self._客户窗('复制客户', {'名称': 原名 + '_副本', '模板': 原名, '文件夹': 原名 + '_副本'})
        if not 值:
            return False
        成功, 消息 = CP.复制档案(原名, 值['名称'].strip())
        if not 成功:
            messagebox.showwarning('复制失败', 消息)
            return False
        if 值['文件夹'].strip():
            CP.PROFILES[值['名称'].strip()]['folder'] = 值['文件夹'].strip()
            CP.保存档案()
        self._同步客户控件(值['名称'].strip())
        return True

    def _设置_编辑客户(self):
        名 = self._设置_选中的客户()
        if not 名:
            messagebox.showinfo('没选中', '先选择一个客户。')
            return False
        窗, 体 = self._模态窗('编辑客户规则：' + 名, '使用 JSON 编辑；保存前会检查格式。', 680, 560)
        文本 = tk.Text(体, wrap='none', font=('Consolas', 10), undo=True)
        文本.pack(fill='both', expand=True)
        文本.insert('1.0', json.dumps(CP.get_profile(名), ensure_ascii=False, indent=2))
        结果 = {'保存': False}

        def 保存():
            try:
                新档 = json.loads(文本.get('1.0', 'end'))
                if not isinstance(新档, dict):
                    raise ValueError('客户规则必须是一个 JSON 对象')
                if not str(新档.get('folder', '')).strip():
                    raise ValueError('folder 不能为空')
            except Exception as e:
                messagebox.showwarning('格式不对', str(e), parent=窗)
                return
            成功, 消息 = CP.更新档案(名, 新档)
            if not 成功:
                messagebox.showwarning('保存失败', 消息, parent=窗)
                return
            结果['保存'] = True
            窗.destroy()

        主按钮(体, '保存规则', 保存).pack(side='right', pady=(P(8), 0))
        次按钮(体, '取消', 窗.destroy).pack(side='right', padx=(0, P(6)), pady=(P(8), 0))
        窗.wait_window()
        self._设置_刷新客户表()
        return 结果['保存']

    def _设置_删除客户(self):
        名 = self._设置_选中的客户()
        if not 名:
            messagebox.showinfo('没选中', '先选择一个客户。')
            return False
        if not messagebox.askyesno('删除客户规则', '确定删除「%s」的规则吗？\n不会删除该客户的图片和打单记录。' % 名):
            return False
        成功, 消息 = CP.删除档案(名)
        if not 成功:
            messagebox.showwarning('删除失败', 消息)
            return False
        self._同步客户控件()
        return True

    def _客户窗(self, 标题, 初始):
        窗, 体 = self._模态窗(标题, '', 440, 260)
        名称 = tk.StringVar(value=初始.get('名称', ''))
        行 = self._设置行(体, '客户名')
        tk.Entry(行, textvariable=名称).pack(side='left', fill='x', expand=True)
        模板 = None
        文件夹 = None
        if not 初始.get('只改名'):
            模板 = tk.StringVar(value=初始.get('模板', ''))
            行 = self._设置行(体, '规则模板')
            ttk.Combobox(行, textvariable=模板, values=CP.list_customers(), state='readonly').pack(
                side='left', fill='x', expand=True)
            文件夹 = tk.StringVar(value=初始.get('文件夹', ''))
            行 = self._设置行(体, '单据文件夹')
            tk.Entry(行, textvariable=文件夹).pack(side='left', fill='x', expand=True)
        结果 = {}

        def 确定():
            if not 名称.get().strip():
                messagebox.showwarning('内容不对', '客户名不能为空。', parent=窗)
                return
            结果.update(名称=名称.get(), 模板=模板.get() if 模板 else '',
                        文件夹=文件夹.get() if 文件夹 else '')
            窗.destroy()

        主按钮(体, '确定', 确定).pack(side='right', pady=(P(12), 0))
        次按钮(体, '取消', 窗.destroy).pack(side='right', padx=(0, P(6)), pady=(P(12), 0))
        窗.wait_window()
        return 结果 or None

    def _设置_改名(self):
        原名 = self._设置_选中的客户()
        if not 原名:
            messagebox.showinfo('没选中', '先选择一个客户。')
            return False
        值 = self._客户窗('客户改名', {'名称': 原名, '只改名': True})
        if not 值:
            return False
        成功, 消息 = CP.改名档案(原名, 值['名称'].strip())
        if not 成功:
            messagebox.showwarning('改名失败', 消息)
            return False
        self._同步客户控件(值['名称'].strip())
        return True

    def _搭_标签纸卡(self, parent):
        体 = self._设置卡片('标签纸', '标签纸', '余量是软件按已发送作业数估算的，换纸后请重新校准。')
        状态 = LY.读()
        self._每卷变量 = tk.StringVar(value=str(状态.get('每卷张数', 800)))
        self._损耗变量 = tk.StringVar(value=str(状态.get('损耗预留', 50)))
        self._已打变量 = tk.StringVar(value=str(状态.get('已用张数', 0)))
        for 标题, 变量 in (('每卷张数', self._每卷变量), ('损耗预留', self._损耗变量), ('本卷已打', self._已打变量)):
            行 = self._设置行(体, 标题)
            tk.Entry(行, textvariable=变量, width=12).pack(side='left')
        钮 = tk.Frame(体, bg=主题.面)
        钮.pack(fill='x', pady=(P(7), 0))
        主按钮(钮, '保存每卷/预留', self._设置_存每卷).pack(side='left')
        次按钮(钮, '校准已打张数', self._设置_存已打).pack(side='left', padx=P(6))
        次按钮(钮, '我换了新卷', self._设置_换新卷).pack(side='left')
        self._标签状态文本 = 说明字(体, '')
        self._标签状态文本.pack(fill='x', pady=(P(6), 0))
        self._设置_刷标签纸()

    def _设置_刷标签纸(self):
        if not hasattr(self, '_标签状态文本') or not self._标签状态文本.winfo_exists():
            return
        self._标签状态文本.configure(text=LY.自检(LY.读()))

    def _设置_存每卷(self):
        try:
            每卷 = int(self._每卷变量.get())
            预留 = int(self._损耗变量.get())
            if 每卷 <= 0 or 预留 < 0:
                raise ValueError
        except (TypeError, ValueError):
            messagebox.showwarning('内容不对', '每卷张数要大于 0，损耗预留不能小于 0。')
            return False
        LY.设置(每卷, 预留)
        回读 = LY.读()
        if not LY.状态文件.is_file() or 回读.get('每卷张数') != 每卷 or 回读.get('损耗预留') != 预留:
            messagebox.showerror('保存失败', '标签余量文件无法写入。')
            return False
        self._设置_刷标签纸()
        self._刷纸chip()
        return True

    def _设置_存已打(self):
        try:
            已打 = int(self._已打变量.get())
            if 已打 < 0:
                raise ValueError
        except (TypeError, ValueError):
            messagebox.showwarning('内容不对', '已打张数必须是大于或等于 0 的整数。')
            return False
        状态 = LY.读()
        状态['已用张数'] = 已打
        状态['没纸标记'] = False
        状态['实测剩余'] = None
        if not LY.写(状态):
            messagebox.showerror('保存失败', '标签余量文件无法写入。')
            return False
        self._设置_刷标签纸()
        self._刷纸chip()
        return True

    def _设置_换新卷(self):
        if not messagebox.askyesno('换新卷', '确认已换上新标签卷？本卷计数将归零。'):
            return False
        try:
            每卷 = int(self._每卷变量.get())
            if 每卷 <= 0:
                raise ValueError
        except (TypeError, ValueError):
            messagebox.showwarning('内容不对', '先填写正确的每卷张数。')
            return False
        LY.换新卷(每卷)
        self._已打变量.set('0')
        self._设置_刷标签纸()
        self._刷纸chip()
        return True

    def _搭_打印机卡(self, parent):
        体 = self._设置卡片('打印机', '打印机', 'USB 直连使用 VID/PID；驱动打印使用打印机名称。')
        self._打印通道变量 = tk.StringVar(value=str(设置.取('打印机.通道', 'usb')))
        self._打印名称变量 = tk.StringVar(value=str(设置.取('打印机.名称', '') or ''))
        self._打印vid变量 = tk.StringVar(value=str(设置.取('打印机.vid', '') or ''))
        self._打印pid变量 = tk.StringVar(value=str(设置.取('打印机.pid', '') or ''))
        行 = self._设置行(体, '打印通道')
        ttk.Combobox(行, textvariable=self._打印通道变量, values=('usb', 'driver', 'auto'),
                     state='readonly', width=12).pack(side='left')
        for 标题, 变量 in (('驱动打印机名', self._打印名称变量), ('USB VID', self._打印vid变量),
                           ('USB PID', self._打印pid变量)):
            行 = self._设置行(体, 标题)
            tk.Entry(行, textvariable=变量).pack(side='left', fill='x', expand=True)
        行 = self._设置行(体, '发现的 USB')
        self._打印设备变量 = tk.StringVar()
        self._打印设备框 = ttk.Combobox(行, textvariable=self._打印设备变量, state='readonly')
        self._打印设备框.pack(side='left', fill='x', expand=True)
        self._打印设备框.bind('<<ComboboxSelected>>', self._设置_选打印设备)
        次按钮(行, '扫描', self._设置_扫打印机).pack(side='left', padx=(P(6), 0))
        钮 = tk.Frame(体, bg=主题.面)
        钮.pack(fill='x', pady=(P(7), 0))
        主按钮(钮, '保存打印机', self._设置_存打印机).pack(side='left')
        次按钮(钮, '打印测试标签', self._设置_测打印机).pack(side='left', padx=P(6))

    def _设置_选打印设备(self, _event=None):
        设备 = getattr(self, '_打印设备', {}).get(self._打印设备变量.get())
        if 设备:
            self._打印vid变量.set(设备.get('vid', ''))
            self._打印pid变量.set(设备.get('pid', ''))

    def _设置_扫打印机(self):
        try:
            设备们 = LB.list_usb_printers()
        except Exception as e:
            messagebox.showerror('扫描失败', str(e))
            return []
        self._打印设备 = {x.get('显示', ''): x for x in 设备们}
        if hasattr(self, '_打印设备框') and self._打印设备框.winfo_exists():
            self._打印设备框.configure(values=list(self._打印设备))
            if 设备们:
                self._打印设备变量.set(设备们[0].get('显示', ''))
                self._设置_选打印设备()
        return 设备们

    def _设置_测打印机(self):
        if not messagebox.askyesno('打印测试', '将向当前配置的打印机发送一张测试标签，继续吗？'):
            return False
        成功 = LB.print_label('打印测试', '', '1', '张', '打单助手V6')
        if 成功:
            messagebox.showinfo('测试完成', '测试标签已发送。')
        else:
            messagebox.showerror('测试失败', LB.最后打印错误 or '打印机未确认成功')
        return bool(成功)

    def _设置_存打印机(self):
        通道 = self._打印通道变量.get().strip().lower()
        if 通道 not in ('usb', 'driver', 'auto'):
            messagebox.showwarning('内容不对', '打印通道只能是 usb、driver 或 auto。')
            return False
        成功 = 设置.改多项({
            '打印机.通道': 通道,
            '打印机.名称': self._打印名称变量.get().strip(),
            '打印机.vid': self._打印vid变量.get().strip().lower(),
            '打印机.pid': self._打印pid变量.get().strip().lower(),
        })
        if not 成功:
            messagebox.showerror('保存失败', 'settings.json 无法写入。')
        elif hasattr(self, '_侧栏打印机'):
            self._侧栏打印机.configure(text='40×30mm · ' + self._打印名称变量.get().strip())
        return 成功

    def _搭_界面卡(self, parent):
        体 = self._设置卡片('界面', '界面与识别', '设置立即写入；字号和布局在重新打开页面或重启后完全生效。')
        self._字号变量 = tk.StringVar(value=str(设置.取('界面.字号档', '大')))
        self._显示预览变量 = tk.BooleanVar(value=bool(设置.取('界面.显示预览', False)))
        self._显示日志变量 = tk.BooleanVar(value=bool(设置.取('界面.显示日志', False)))
        self._侧边收起变量 = tk.BooleanVar(value=bool(设置.取('界面.侧边收起', False)))
        self._仔细默认变量 = tk.BooleanVar(value=bool(设置.取('识别.仔细模式默认', False)))
        行 = self._设置行(体, '字号档')
        ttk.Combobox(行, textvariable=self._字号变量, values=设置.字号档名单,
                     state='readonly', width=12).pack(side='left')
        次按钮(行, '保存字号', self._设置_存字号).pack(side='left', padx=(P(8), 0))
        for 文字, 变量 in (('显示标签预览', self._显示预览变量), ('显示运行日志', self._显示日志变量),
                           ('启动时收起侧栏', self._侧边收起变量), ('默认使用仔细模式', self._仔细默认变量)):
            ttk.Checkbutton(体, text=文字, variable=变量).pack(anchor='w', pady=P(3))
        主按钮(体, '保存界面与识别设置', self._设置_存界面).pack(anchor='e', pady=(P(6), 0))

    def _设置_存字号(self):
        return self._设置_存界面()

    def _设置_存界面(self):
        档 = self._字号变量.get()
        if 档 not in 设置.字号档表:
            messagebox.showwarning('内容不对', '请选择有效的字号档。')
            return False
        成功 = 设置.改多项({
            '界面.字号档': 档,
            '界面.显示预览': bool(self._显示预览变量.get()),
            '界面.显示日志': bool(self._显示日志变量.get()),
            '界面.侧边收起': bool(self._侧边收起变量.get()),
            '识别.仔细模式默认': bool(self._仔细默认变量.get()),
        })
        if 成功:
            self.var_effort.set(bool(self._仔细默认变量.get()))
        else:
            messagebox.showerror('保存失败', 'settings.json 无法写入。')
        return 成功

    def _搭_数据卡(self, parent):
        体 = self._设置卡片('数据', '数据与缓存', '清理 OCR 缓存不会删除客户规则、图片或打单记录。')
        self._缓存状态 = 说明字(体, '')
        self._缓存状态.pack(fill='x')
        钮 = tk.Frame(体, bg=主题.面)
        钮.pack(fill='x', pady=(P(7), 0))
        次按钮(钮, '刷新缓存信息', self._设置_刷新缓存).pack(side='left')
        次按钮(钮, '清理 OCR 缓存', self._设置_清缓存, 危险=True).pack(side='left', padx=P(6))
        次按钮(钮, '打开程序目录', self._设置_开文件夹).pack(side='left')
        self._设置_刷新缓存()

    def _设置_刷新缓存(self):
        文件 = []
        try:
            文件 = [p for p in T.ocr_cache.glob('*.json') if p.is_file()]
            大小 = sum(p.stat().st_size for p in 文件)
        except Exception:
            大小 = 0
        if hasattr(self, '_缓存状态') and self._缓存状态.winfo_exists():
            self._缓存状态.configure(text='OCR 缓存：%d 个文件，%.2f MB' % (len(文件), 大小 / 1024 / 1024))
        return len(文件), 大小

    def _设置_清缓存(self):
        数量, _大小 = self._设置_刷新缓存()
        if not 数量:
            return True
        if not messagebox.askyesno('清理缓存', '确定删除 %d 个 OCR 缓存文件吗？' % 数量):
            return False
        失败 = 0
        try:
            for p in T.ocr_cache.glob('*.json'):
                try:
                    p.unlink()
                except OSError:
                    失败 += 1
        except Exception:
            失败 += 1
        self._设置_刷新缓存()
        if 失败:
            messagebox.showwarning('部分未清理', '%d 个缓存文件正在使用或无法删除。' % 失败)
        return 失败 == 0

    def _设置_开文件夹(self):
        try:
            os.startfile(str(HERE))
            return True
        except Exception as e:
            messagebox.showerror('打开失败', str(e))
            return False

    def _搭_备份卡(self, parent):
        体 = self._设置卡片('备份', '备份与恢复', '备份包含 settings.json、customers.json、标签余量和打单记录，不包含密钥。')
        self._备份状态 = 说明字(体, '')
        self._备份状态.pack(fill='x')
        钮 = tk.Frame(体, bg=主题.面)
        钮.pack(fill='x', pady=(P(7), 0))
        主按钮(钮, '导出备份', self._设置_导出).pack(side='left')
        次按钮(钮, '导入备份', self._设置_导入).pack(side='left', padx=P(6))
        次按钮(钮, '刷新', self._设置_刷新备份).pack(side='left')
        次按钮(钮, '恢复默认设置', self._设置_恢复出厂, 危险=True).pack(side='left', padx=P(6))
        self._设置_刷新备份()

    def _设置_刷新备份(self):
        夹 = HERE / '备份'
        try:
            文件 = sorted(夹.glob('*.zip'), key=lambda p: p.stat().st_mtime, reverse=True)
        except Exception:
            文件 = []
        if hasattr(self, '_备份状态') and self._备份状态.winfo_exists():
            文字 = '备份目录：%s\n已有 %d 个备份' % (夹, len(文件))
            if 文件:
                文字 += '；最近：' + 文件[0].name
            self._备份状态.configure(text=文字)
        return 文件

    def _设置_导出(self):
        try:
            夹 = HERE / '备份'
            夹.mkdir(parents=True, exist_ok=True)
            默认名 = '打单助手备份_%s.zip' % datetime.now().strftime('%Y%m%d_%H%M%S')
            路 = filedialog.asksaveasfilename(title='导出备份', initialdir=str(夹), initialfile=默认名,
                                               defaultextension='.zip', filetypes=[('ZIP 备份', '*.zip')])
            if not 路:
                return None
            路 = Path(路)
            import zipfile
            with zipfile.ZipFile(路, 'w', compression=zipfile.ZIP_DEFLATED) as 包:
                for 名 in ('settings.json', 'customers.json', '标签余量.json'):
                    文件 = HERE / 名
                    if 文件.is_file():
                        包.write(文件, 名)
                记录夹 = DR.默认目录
                if 记录夹.is_dir():
                    for 文件 in 记录夹.rglob('*'):
                        if 文件.is_file():
                            包.write(文件, str(Path('打单记录') / 文件.relative_to(记录夹)).replace('\\', '/'))
            self._设置_刷新备份()
            return str(路)
        except Exception as e:
            messagebox.showerror('导出失败', str(e))
            return None

    def _设置_导入(self):
        路 = filedialog.askopenfilename(title='导入备份', filetypes=[('ZIP 备份', '*.zip')])
        if not 路:
            return False
        if not messagebox.askyesno('导入备份', '导入会替换同名配置和打单记录，确定继续吗？'):
            return False
        try:
            import zipfile
            允许文件 = {'settings.json', 'customers.json', '标签余量.json'}
            with zipfile.ZipFile(路, 'r') as 包:
                for 信息 in 包.infolist():
                    名 = 信息.filename.replace('\\', '/')
                    if 信息.is_dir() or (名 not in 允许文件 and not 名.startswith('打单记录/')):
                        continue
                    部分 = Path(名).parts
                    if not 部分 or 名.startswith('/') or '..' in 部分:
                        raise ValueError('备份中包含不安全路径：' + 名)
                    目标 = HERE.joinpath(*部分)
                    目标.parent.mkdir(parents=True, exist_ok=True)
                    with 包.open(信息, 'r') as 源, 目标.open('wb') as 输出:
                        shutil.copyfileobj(源, 输出)
            设置.全部(重新读=True)
            CP.重新载入()
            self._同步客户控件()
            self._同步设置控件()
            self._设置_刷新备份()
            self._刷纸chip()
            return True
        except Exception as e:
            messagebox.showerror('导入失败', str(e))
            return False

    def _设置_恢复出厂(self):
        if not messagebox.askyesno('恢复默认设置', '将恢复界面、打印机和标签纸默认设置；客户规则及打单记录不变。继续吗？'):
            return False
        if not 设置.写(copy.deepcopy(设置.默认设置)):
            messagebox.showerror('恢复失败', 'settings.json 无法写入。')
            return False
        设置.全部(重新读=True)
        LY.恢复出厂()
        self._同步设置控件()
        self._刷纸chip()
        self.goto('settings')
        return True

    def _搭_自检卡(self, parent):
        体 = self._设置卡片('自检', '离线自检', '只检查文件、模块和打印配置，不调用云 OCR，也不发送打印任务。')
        self._自检文本 = tk.Text(体, height=7, wrap='word', state='disabled',
                                  bg=主题.面淡, fg=主题.正文, relief='flat',
                                  font=(主题.字体, 主题.字号小))
        self._自检文本.pack(fill='both', expand=True)
        主按钮(体, '重新自检', self._设置_跑自检).pack(anchor='e', pady=(P(7), 0))
        self._设置_跑自检()

    def _设置_跑自检(self):
        配置 = 设置.读()
        打印 = 配置.get('打印机', {})
        结果 = [
            ('settings.json', 设置.设置文件.is_file(), str(设置.设置文件)),
            ('customers.json', CP.客户档.is_file(), '%d 家客户' % len(CP.list_customers())),
            ('标签余量', True, LY.自检(LY.读())),
            ('打印配置', bool(打印.get('vid') or 打印.get('名称')),
             '%s / %s / %s' % (打印.get('通道', 'usb'), 打印.get('vid', ''), 打印.get('名称', ''))),
            ('Pillow 图像模块', _HAS_PIL, '可用' if _HAS_PIL else '缺失'),
            ('拖放模块', _HAS_DND, '可用' if _HAS_DND else '未安装，可用选择图片按钮'),
            ('OCR 缓存目录', True, str(T.ocr_cache)),
        ]
        文本 = '\n'.join('[%s] %s：%s' % ('通过' if 成功 else '注意', 名, 说明) for 名, 成功, 说明 in 结果)
        if hasattr(self, '_自检文本') and self._自检文本.winfo_exists():
            self._自检文本.configure(state='normal')
            self._自检文本.delete('1.0', 'end')
            self._自检文本.insert('1.0', 文本)
            self._自检文本.configure(state='disabled')
        return 结果

    def _搭_关于卡(self, parent):
        体 = self._设置卡片('关于', '关于', '打单助手 V6.1')
        for 文字 in ('流程：图片导入 → OCR → 结构化识别 → 人工核对 → 标签打印',
                     '配置与业务数据保存在程序目录，迁移时请使用备份功能。',
                     '打印历史用于本次运行内补打；长期查账请打开打单记录。'):
            说明字(体, 文字).pack(fill='x', pady=P(3))

    def _模态窗(self, 标题, 内容, 宽=480, 高=320):
        窗 = tk.Toplevel(self.root)
        窗.title(标题)
        窗.configure(bg=主题.面)
        窗.geometry('%dx%d' % (P(宽), P(高)))
        窗.transient(self.root)
        体 = tk.Frame(窗, bg=主题.面)
        体.pack(fill='both', expand=True, padx=P(14), pady=P(14))
        if 内容:
            说明字(体, 内容).pack(fill='x', pady=(0, P(8)))
        try:
            窗.grab_set()
        except Exception:
            pass
        return 窗, 体

    # ---------- 历史页 ----------
    def _build_page_history(self, parent):
        page = tk.Frame(parent, bg=主题.底色)
        self._历史页 = page
        工具 = tk.Frame(page, bg=主题.面, highlightthickness=1, highlightbackground=主题.边框)
        工具.pack(fill='x', padx=P(16), pady=(P(12), 0))
        tk.Label(工具, text='客户筛选', bg=主题.面, fg=主题.正文,
                 font=(主题.字体, 主题.字号)).pack(side='left', padx=(P(10), P(6)), pady=P(8))
        self._历史筛选变量 = tk.StringVar(value='全部客户')
        self._历史客户框 = ttk.Combobox(工具, textvariable=self._历史筛选变量,
                                        values=['全部客户'] + CP.list_customers(), state='readonly', width=14)
        self._历史客户框.pack(side='left', pady=P(8))
        self._历史客户框.bind('<<ComboboxSelected>>', lambda e: self._刷新历史())
        self.btn_历史重打 = 主按钮(工具, '重打选中的', self._重打选中的)
        self.btn_历史重打.pack(side='right', padx=(0, P(10)), pady=P(8))
        self.btn_历史台账 = 次按钮(工具, '打开今天台账', self._打开今天记录)
        self.btn_历史台账.pack(side='right', padx=P(6), pady=P(8))
        self.btn_历史全选 = 次按钮(工具, '全选', self._历史全选)
        self.btn_历史全选.pack(side='right', padx=P(6), pady=P(8))
        self.btn_历史刷新 = 次按钮(工具, '刷新', self._刷新历史)
        self.btn_历史刷新.pack(side='right', padx=P(6), pady=P(8))
        表框 = tk.Frame(page, bg=主题.面)
        表框.pack(fill='both', expand=True, padx=P(16), pady=P(12))
        self._历史tree = ttk.Treeview(表框, columns=('no', '时间', '客户', '品类', '编码', '斤数'),
                                      show='headings', selectmode='extended')
        for cid, t, w in (('no', '#', 40), ('时间', '时间', 150), ('客户', '客户', 100),
                          ('品类', '品类', 150), ('编码', '编码', 100), ('斤数', '斤数', 80)):
            self._历史tree.heading(cid, text=t)
            self._历史tree.column(cid, width=P(w), anchor='center' if cid == 'no' else 'w')
        滚动 = ttk.Scrollbar(表框, orient='vertical', command=self._历史tree.yview)
        self._历史tree.configure(yscrollcommand=滚动.set)
        滚动.pack(side='right', fill='y')
        self._历史tree.pack(side='left', fill='both', expand=True)
        self._历史tree.bind('<<TreeviewSelect>>', lambda e: self._刷新重打按钮())
        self._历史状态 = 说明字(page, '')
        self._历史状态.pack(fill='x', padx=P(16), pady=(0, P(8)))
        self._刷新历史()
        return page

    def _历史条目们(self):
        筛选 = getattr(self, '_历史筛选变量', None)
        客户 = 筛选.get() if 筛选 else '全部客户'
        return PH.列表(None if 客户 == '全部客户' else 客户)

    def _刷新历史(self):
        if not hasattr(self, '_历史tree') or not self._历史tree.winfo_exists():
            return
        if hasattr(self, '_历史客户框'):
            旧 = self._历史筛选变量.get()
            客户们 = ['全部客户'] + CP.list_customers()
            self._历史客户框.configure(values=客户们)
            if 旧 not in 客户们:
                self._历史筛选变量.set('全部客户')
        self._历史tree.delete(*self._历史tree.get_children())
        条目们 = self._历史条目们()
        for t in 条目们:
            self._历史tree.insert('', 'end', iid=str(t['id']),
                                  values=(t['id'], t.get('时间', ''), t.get('客户', ''),
                                          t.get('品类', ''), t.get('编码', ''),
                                          '%s%s' % (t.get('斤数', ''), t.get('单位', ''))))
        if hasattr(self, '_历史状态'):
            self._历史状态.configure(text='当前显示 %d 条；历史记录保存在本次运行内存中。' % len(条目们))
        self._刷新重打按钮()

    def _刷新重打按钮(self):
        if not hasattr(self, 'btn_历史重打') or not self.btn_历史重打.winfo_exists():
            return
        数量 = len(self._历史tree.selection()) if hasattr(self, '_历史tree') else 0
        self.btn_历史重打.configure(text='重打选中的%s' % (' (%d)' % 数量 if 数量 else ''),
                                    state='normal' if 数量 and not self.busy else 'disabled')

    def _历史全选(self):
        行 = self._历史tree.get_children()
        self._历史tree.selection_set(行)
        self._刷新重打按钮()
        return len(行)

    def _打开今天记录(self):
        客户 = ''
        选中 = self._历史tree.selection() if hasattr(self, '_历史tree') else []
        if 选中:
            条目 = PH.取(int(选中[0]))
            客户 = 条目.get('客户', '') if 条目 else ''
        if not 客户 and hasattr(self, '_历史筛选变量') and self._历史筛选变量.get() != '全部客户':
            客户 = self._历史筛选变量.get()
        客户 = 客户 or self.var_customer.get()
        成功, 消息 = DR.打开今天(客户)
        if not 成功:
            messagebox.showinfo('打开台账', 消息)
        return 成功, 消息

    def _重打选中的(self):
        if self.busy:
            return None
        sel = self._历史tree.selection()
        if not sel:
            messagebox.showinfo('没选中', '先选一条要重打的记录')
            return None
        记录们 = []
        for 编号 in sel:
            条目 = PH.取(int(编号))
            if 条目:
                记录们.append(PH.转记录(条目))
        if not 记录们:
            return None
        self._task_kind = 'print'
        self.set_busy(True)
        self._刷新重打按钮()
        self.worker.run(self._print_task, 记录们, customer=None, 重打=True)
        return 记录们

    # ---------- 识单页 ----------
    def _build_page_scan(self, parent):
        page = tk.Frame(parent, bg=主题.底色)
        self._识单页 = page
        self._上 = tk.Frame(page, bg=主题.底色)
        self._上.pack(fill='x')
        左 = tk.Frame(self._上, bg=主题.面, highlightthickness=1, highlightbackground=主题.边框)
        左.pack(side='left', fill='y', padx=(P(16), P(0)), pady=(P(10), P(8)))
        self._build_customer_box(左)
        右 = tk.Frame(self._上, bg=主题.面, highlightthickness=1, highlightbackground=主题.边框)
        右.pack(side='left', fill='both', expand=True, padx=(P(12), P(16)), pady=(P(10), P(8)))
        self._build_files_box(右)
        self._build_checklist(page)
        return page

    def _自适应收单区(self):
        pass

    def _build_customer_box(self, parent):
        tk.Label(parent, text='客户', bg=主题.面, fg=主题.正文,
                 font=(主题.字体, 主题.字号, 'bold')).pack(anchor='w', padx=P(12), pady=(P(10), P(0)))
        self._refresh_customers()

    def _build_files_box(self, parent):
        pad = tk.Frame(parent, bg=主题.面)
        pad.pack(fill='both', expand=True, padx=P(14), pady=P(10))
        分区标题(pad, '单据图片', '拖进来 / 双击某张只看它').pack(fill='x')
        self.lbl_files = tk.Label(pad, text='还没有加图片', bg=主题.面, fg=主题.次要,
                                  font=(主题.字体, 主题.字号小), anchor='e')
        self.lbl_files.pack(fill='x')
        钮 = tk.Frame(pad, bg=主题.面)
        钮.pack(fill='x', pady=(P(8), P(8)))
        次按钮(钮, '选择图片…', self.add_files).pack(side='left')
        次按钮(钮, '读客户文件夹', self.load_from_folder).pack(side='left', padx=P(6))
        次按钮(钮, '清空', self.clear_files, 危险=True).pack(side='left', padx=P(6))
        # Windows 的 ttk 主题可能把选中标记画成 X；经典 Tk 复选框固定显示勾号。
        self.chk_effort = tk.Checkbutton(
            钮, text='仔细模式', variable=self.var_effort, indicatoron=True,
            bg=主题.面, fg=主题.正文, activebackground=主题.面,
            activeforeground=主题.正文, selectcolor=主题.面,
            font=(主题.字体, 主题.字号), cursor='hand2',
            highlightthickness=0, bd=0)
        self.chk_effort.pack(side='right', padx=(P(8), P(10)))
        self.btn_scan = 主按钮(钮, '开始识别', self.start_scan)
        self.btn_scan.pack(side='right')

    def _build_checklist(self, parent):
        外 = tk.Frame(parent, bg=主题.底色)
        外.pack(fill='both', expand=True, padx=P(16), pady=(P(0), P(10)))
        self._清单外 = 外
        盒 = tk.Frame(外, bg=主题.面, highlightthickness=1, highlightbackground=主题.边框)
        盒.pack(fill='both', expand=True)
        条 = tk.Frame(盒, bg=主题.面)
        条.pack(fill='x', padx=P(14), pady=(P(8), P(0)))
        分区标题(条, '核对清单', '双击单元格就能改').pack(side='left')
        self.btn_print = 主按钮(条, '确认打印', self.do_print, 色调='成功')
        self.btn_print.pack(side='right')
        self.btn_undo = 次按钮(条, '撤销修改', self.undo_edits)
        self.btn_undo.pack(side='right', padx=P(6))
        self.btn_del = 次按钮(条, '删除这一行', self.delete_selected, 危险=True)
        self.btn_del.pack(side='right', padx=(P(0), P(6)))
        第二 = tk.Frame(盒, bg=主题.面)
        第二.pack(fill='x', padx=P(14), pady=(P(6), P(0)))
        self.lbl_check_status = tk.Label(第二, text='', bg=主题.面, fg=主题.次要,
                                         font=(主题.字体, 主题.字号小), anchor='w')
        self.lbl_check_status.pack(side='left', fill='x', expand=True)
        cols = ('no', 'state', 'category', 'code', 'weight', 'unit', 'remark')
        self.tree = ttk.Treeview(盒, columns=cols, show='headings', height=12, selectmode='browse')
        for cid, t, w, anchor, stretch in (('no', '#', 40, 'center', False),
                                           ('state', '状态', 96, 'center', False),
                                           ('category', '品类', 190, 'w', True),
                                           ('code', '客户编码', 108, 'center', False),
                                           ('weight', '斤数', 84, 'center', False),
                                           ('unit', '单位', 56, 'center', False),
                                           ('remark', '备注', 150, 'w', True)):
            self.tree.heading(cid, text=t)
            self.tree.column(cid, width=P(w), anchor=anchor, stretch=stretch)
        self.tree.pack(fill='both', expand=True, padx=P(14), pady=(P(8), P(10)))
        self.tree.bind('<Double-1>', self._on_double_click)
        self.tree.bind('<Delete>', lambda e: self.delete_selected())
        self.tree.bind('<F2>', lambda e: self.open_editor_for_selection())
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.update_preview())

    def _build_log(self, parent):
        pass

    def toggle_log(self):
        pass

    def goto(self, 页):
        for 键 in self._导航按钮:
            if 键 == 页:
                continue
        self._导航刷新(页)
        for w in self._页容器.winfo_children():
            w.destroy()
        if 页 == 'scan':
            self._build_page_scan(self._页容器).pack(fill='both', expand=True)
            self.lbl_title.configure(text='打单助手 · 识单')
        elif 页 == 'history':
            self._build_page_history(self._页容器).pack(fill='both', expand=True)
            self.lbl_title.configure(text='打单助手 · 历史')
        else:
            self._build_page_settings(self._页容器).pack(fill='both', expand=True)
            self.lbl_title.configure(text='打单助手 · 设置')

    def _让页能拿焦点(self):
        pass

    def log(self, msg):
        try:
            log_dir = HERE / 'log'
            log_dir.mkdir(exist_ok=True)
            log_file = log_dir / ('程序_%s.log' % time.strftime('%Y%m%d'))
            with log_file.open('a', encoding='utf-8') as f:
                f.write('%s\t%s\n' % (time.strftime('%H:%M:%S'), msg))
        except Exception:
            pass

    def debug_log(self, msg):
        try:
            if 设置.取('系统.运行模式') == 'debug':
                self.log('[DEBUG] ' + msg)
        except Exception:
            pass

    def _刷打印按钮(self):
        try:
            有待打 = any(not r.get('_printed') for r in self.records)
            self.btn_print.configure(state='normal' if 有待打 and not self.busy else 'disabled')
            self.btn_re.configure(state='normal' if self.records and not self.busy else 'disabled')
        except Exception:
            pass

    def set_busy(self, b):
        self.busy = bool(b)
        for 名 in ('btn_scan', 'btn_re', 'btn_del_row', 'btn_print'):
            if hasattr(self, 名):
                try:
                    getattr(self, 名).configure(state='disabled' if self.busy else 'normal')
                except Exception:
                    pass
        if not self.busy:
            self._刷打印按钮()
        self._刷新重打按钮()

    def _refresh_customers(self):
        try:
            self._客户列表 = ttk.Combobox(self._上 if hasattr(self, '_上') else self.root,
                                          textvariable=self.var_customer,
                                          values=CP.list_customers(), state='readonly')
            self._客户列表.pack(fill='x', padx=P(12), pady=P(8))
            self._客户列表.bind('<<ComboboxSelected>>', self._on_customer_change)
            if CP.list_customers() and not self.var_customer.get():
                self.var_customer.set(CP.list_customers()[0])
        except Exception:
            pass

    def _on_customer_change(self, e=None):
        pass

    # ---------- 文件 ----------
    def _注册拖拽(self):
        if not _HAS_DND:
            return
        控件们 = [self.root]
        if hasattr(self, 'lst_files'):
            控件们.append(self.lst_files)
        for 控件 in 控件们:
            try:
                控件.drop_target_register(DND_FILES)
                控件.dnd_bind('<<Drop>>', self._on_drop)
            except Exception as e:
                self.log('拖拽注册失败：%s' % e)

    def add_files(self):
        self.debug_log('用户点击了[选择图片...]按钮')
        路径s = filedialog.askopenfilenames(filetypes=[('图片', '*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff')])
        if 路径s:
            self.debug_log('用户选择了 %d 个文件: %s' % (len(路径s), 路径s))
            self._add(路径s)

    def load_from_folder(self):
        客户 = self.var_customer.get()
        self.debug_log('用户点击了[读客户文件夹]按钮，当前客户: %s' % 客户)
        profile = CP.get_profile(客户)
        folder = 路径.数据目录() / (profile.get('folder') or 客户)
        if not folder.is_dir():
            self.debug_log('客户文件夹不存在: %s' % folder)
            messagebox.showinfo('没有这个文件夹', str(folder))
            return
        路径s = [str(p) for p in sorted(folder.glob('*')) if p.suffix.lower() in IMG_EXT]
        self.debug_log('从文件夹读取了 %d 个文件' % len(路径s))
        if 路径s:
            self._add(路径s)

    def _add(self, paths):
        if self.busy:
            messagebox.showinfo('正在处理', '请等当前识别或打印结束后再加图片。')
            return
        新增 = 0
        无效 = []
        for p in paths:
            源 = Path(str(p).strip().strip('"'))
            if not 源.is_file() or 源.suffix.lower() not in IMG_EXT:
                无效.append(源.name or str(p))
                continue
            指纹 = 落地.指纹(源)
            if 指纹 and 指纹 in self._文件指纹:
                continue
            落 = 落地.落地(源)
            键 = str(落)
            if 键 in self.files:
                continue
            self.files.append(键)
            if 指纹:
                self._文件指纹[指纹] = 键
            新增 += 1
        self._redraw_files()
        if 无效:
            self.set_status('已跳过 %d 个非图片或找不到的文件' % len(无效), 主题.警告)
            self.log('跳过导入：' + '、'.join(无效))
        elif 新增:
            self.set_status('已加入 %d 张图片，可以开始识别' % 新增, 主题.主色)

    def _remove_selected_files(self):
        sel = self.lst_files.selection() if hasattr(self, 'lst_files') else []
        self.debug_log('用户移除选中图片，选中项: %s' % list(sel))
        for i in sorted((int(x) for x in sel), reverse=True):
            if 0 <= i < len(self.files):
                旧 = self.files.pop(i)
                self.debug_log('移除图片: %s' % 旧)
                for 指纹, 路 in list(self._文件指纹.items()):
                    if 路 == 旧:
                        del self._文件指纹[指纹]
        self._redraw_files()

    def clear_files(self):
        self.debug_log('用户点击了[清空/返回]按钮，清空文件和核对清单')
        self._清空列表(清图片=True)

    def _清空列表(self, 清图片=True):
        if 清图片:
            self.files = []
            self._文件指纹 = {}
            self._图片筛选 = None
        self.records = []
        self._原始记录 = None
        self._删除记录 = []
        self._redraw_files()
        self._redraw_records()
        self._刷打印按钮()

    def _redraw_files(self):
        n = len(self.files)
        if hasattr(self, 'lbl_files'):
            self.lbl_files.configure(text=('已加 %d 张' % n if n else '还没有加图片'),
                                     fg=主题.正文 if n else 主题.次要)
        if hasattr(self, 'lst_files'):
            self.lst_files.delete(*self.lst_files.get_children())
            for i, p in enumerate(self.files):
                名 = Path(p).name
                条数 = sum(1 for r in self.records
                           if r.get('_img') == p or r.get('_src') == 名)
                self.lst_files.insert('', 'end', iid=str(i), values=(i + 1, 名, 条数 or ''))
            self._自适应收单区()
        if hasattr(self, '_刷新收起条'):
            self._刷新收起条()

    def _按图片筛选(self, e=None):
        if not hasattr(self, 'lst_files'):
            return
        选 = self.lst_files.selection()
        if not 选:
            return
        try:
            路 = self.files[int(选[0])]
        except (ValueError, IndexError):
            return
        self._图片筛选 = None if self._图片筛选 == 路 else 路
        self._redraw_records()

    def _show_file_preview(self, e=None):
        pass

    def _on_drop(self, e):
        try:
            路径们 = list(self.root.tk.splitlist(e.data))
        except Exception:
            路径们 = [e.data]
        self._add(路径们)
        return 'copy'

    # ---------- 识别 ----------
    def _scan_heartbeat(self, count=0):
        if not self.busy or getattr(self, '_task_kind', '') != 'scan':
            return
        msgs = [
            '正在调用 AI 大模型分析图片内容...',
            '正在交叉比对 OCR 底层数据...',
            '正在校对商品明细与数量...',
            '识别引擎运行中，请耐心等待...'
        ]
        self.log(msgs[count % len(msgs)])
        self.root.after(15000, self._scan_heartbeat, count + 1)

    def start_scan(self):
        if self.busy:
            return
        if not self.files:
            messagebox.showinfo('没有图片', '先把单据图片拖进来')
            return
        客户 = self.var_customer.get()
        if not 客户 or 客户 not in CP.list_customers():
            messagebox.showinfo('没选客户', '先在上面选一个有效客户')
            self._同步客户控件()
            return
        self._task_kind = 'scan'
        self._识别警告 = []
        self.set_busy(True)
        self.set_status('正在识别…', 主题.主色)
        self.log('开始识别，共 %d 张图片。' % len(self.files))
        self.root.after(15000, self._scan_heartbeat, 0)
        self.worker.run(self._scan_task, 客户)

    def _scan_task(self, 客户):
        self._识别警告 = []
        prompt = T.build_prompt(None)
        仔细 = bool(self.var_effort.get())
        effort = 'high' if 仔细 else 'off'
        records = []
        文件们 = list(self.files)
        for i, p in enumerate(文件们):
            self.worker.progress((i + 1, len(文件们)))
            名 = Path(p).name
            self.debug_log('开始识别文件: %s' % 名)
            try:
                recs, blocks, notes = T.recognize_parallel(p, effort, prompt, careful=仔细)
                self.debug_log('提取结果: %d 条记录，%d 个文本块，notes: %s' % (len(recs) if recs else 0, len(blocks) if blocks else 0, notes))
                for 提示 in notes:
                    self.log('%s：%s' % (名, 提示))
                    if '腾讯 OCR 未返回' in 提示:
                        self._识别警告.append('%s：%s' % (名, 提示))
                if not recs:
                    raise RuntimeError('AI 没有提取到有效明细，请检查客户是否选对，或换一张更清晰的图片')
                T.fix_category_boundaries(recs, blocks)
                覆盖提示 = T.check_coverage(blocks, recs)
                if 覆盖提示:
                    # 漏品类是整批提示，不能把现有每一行都误标成“待核对”。
                    self._识别警告.append('%s：%s' % (名, 覆盖提示.replace('\n', '；')))
                    self.log('%s：%s' % (名, 覆盖提示.replace('\n', '；')))
                交叉提示 = T.cross_check(recs, blocks)
                if 交叉提示:
                    self.log('%s：%s' % (名, 交叉提示.replace('\n', '；')))
                for 提示 in T.mark_uncertain(recs):
                    self.log('%s：%s' % (名, 提示))
                for r in recs:
                    self._记录序号 += 1
                    r['_id'] = self._记录序号
                    r['_img'] = p
                    r['_src'] = 名
                records.extend(recs)
                self.log('%s：识别 %d 条，完整保留；客户仅用于输出格式' % (名, len(recs)))
            except Exception as e:
                友好 = T.说人话的报错(e)
                raise RuntimeError('第 %d 张「%s」识别失败：%s' % (i + 1, 名, 友好)) from e
        return records

    def on_scan_done(self, result):
        self.debug_log('识别流程结束，合并结果共 %d 条' % len(result))
        self.records = list(result)
        for r in self.records:
            if '_id' not in r:
                self._记录序号 += 1
                r['_id'] = self._记录序号
        self._原始记录 = copy.deepcopy(self.records)
        self._图片筛选 = None
        self._筛选 = '全部'
        self.set_busy(False)
        self._redraw_files()
        self._redraw_records()
        self._刷打印按钮()
        if self.records:
            待核对 = sum(self._需要核对(r) for r in self.records)
            整单警告 = len(getattr(self, '_识别警告', []))
            文字 = '识别完成，共 %d 条' % len(self.records)
            if 待核对:
                文字 += '，%d 条需核对' % 待核对
            if 整单警告:
                文字 += '，%d 条整单警告' % 整单警告
            if not 待核对 and not 整单警告:
                文字 += '，未发现明显异常'
            self.set_status(文字, 主题.警告 if 待核对 or 整单警告 else 主题.成功)
        else:
            self.set_status('识别完成，但按客户规则过滤后没有可打印记录', 主题.警告)

    def on_scan_error(self, e):
        self.debug_log('识别流程报错结束: %s' % repr(e))
        self.set_busy(False)
        友好 = T.说人话的报错(e)
        self.set_status('识别失败：%s' % 友好, 主题.危险)
        self.log('识别失败：%s' % 友好)
        messagebox.showerror('识别失败', str(友好))

    # ---------- 记录显示 ----------
    def _rec_weight_text(self, r):
        w = str(r.get('weight', ''))
        u = str(r.get('unit', '斤'))
        if not w:
            return ''
        if r.get('_uncertain'):
            return w + u + ' ?'
        if r.get('_weight_edited'):
            return w + u + ' ✓'
        return w + u

    def _需要核对(self, r):
        """只有真实疑点才进入核对筛选，不把“尚未人工确认”等同于异常。"""
        return (not r.get('_printed') and not r.get('_user_edited')
                and not r.get('_user_confirmed')
                and bool(r.get('_uncertain') or r.get('_suspect')))

    def _rec_state(self, r):
        if r.get('_printed'):
            return ('已打印', '已打印', '正常')
        if r.get('_user_edited'):
            return ('已修改', '已修改', '正常')
        if r.get('_uncertain'):
            return ('? 拿不准', '斤数可疑', '可疑')
        if r.get('_suspect'):
            return ('! 可疑', '认字可疑', '可疑')
        if r.get('_user_confirmed'):
            return ('✓ 已确认', '', '正常')
        return ('已识别', '', '正常')

    def _rec_row_values(self, no, r):
        state, tag, _ = self._rec_state(r)
        r = CP.format_record(r, self.var_customer.get())
        return (no, state, r.get('category', ''), r.get('customer_code', ''),
                r.get('weight', ''), r.get('unit', ''), r.get('remark', ''))

    def _设筛选(self, k):
        self._筛选 = k
        self._redraw_records()

    def _这条要显示(self, r):
        if self._图片筛选 and r.get('_img') != self._图片筛选:
            return False
        筛选 = getattr(self, '_筛选', '全部')
        if 筛选 == '全部':
            return True
        if 筛选 == '要核对':
            return self._需要核对(r)
        if 筛选 == '已修改':
            return bool(r.get('_user_edited'))
        if 筛选 == '已打印':
            return bool(r.get('_printed'))
        return True

    def _刷新筛选按钮(self):
        pass

    def _取消图片筛选(self, e=None):
        self._图片筛选 = None
        self._redraw_records()

    def _redraw_records(self):
        self.tree.delete(*self.tree.get_children())
        显示记录 = CP.排序记录(copy.deepcopy(self.records), self.var_customer.get())
        for i, r in enumerate(显示记录):
            if not self._这条要显示(r):
                continue
            state, tag, _ = self._rec_state(r)
            iid = str(r.get('_id', i))
            self.tree.insert('', 'end', iid=iid,
                             values=self._rec_row_values(i + 1, r),
                             tags=(tag,) if tag else ())
        if hasattr(self, 'lbl_图片筛选'):
            if self._图片筛选:
                self.lbl_图片筛选.configure(text='只看：%s ×' % Path(self._图片筛选).name,
                                            bg=主题.警告淡, fg=主题.警告)
            else:
                self.lbl_图片筛选.configure(text='', bg=主题.面)
        self._refresh_checklist_status()

    def _row_to_idx(self, iid):
        for i, r in enumerate(self.records):
            if str(r.get('_id', i)) == str(iid):
                return i
        return -1

    def _refresh_checklist_status(self):
        n = len(self.records)
        可疑 = sum(self._需要核对(r) for r in self.records)
        改过 = sum(bool(r.get('_user_edited')) for r in self.records)
        文字 = '共 %d 条待打印' % n
        if 可疑:
            文字 += '  |  %d 条要重点核对' % 可疑
        if 改过:
            文字 += '  |  %d 条改过' % 改过
        整单警告 = len(getattr(self, '_识别警告', []))
        if 整单警告:
            文字 += '  |  %d 条整单警告（见日志）' % 整单警告
        if self._图片筛选:
            文字 += '  |  正在只看一张图'
        self.lbl_check_status.configure(text=文字)

    def _有修改(self):
        return any(r.get('_user_edited') for r in self.records)

    def _tree_yview(self, *a):
        if not self._commit_editor():
            return
        self.tree.yview(*a)

    def _tree_wheel(self, e):
        if not self._commit_editor():
            return 'break'
        self.tree.yview_scroll(int(-e.delta / 120), 'units')
        return 'break'

    def _跳格(self, e=None):
        if self._编辑位置 is None:
            return 'break'
        行号, 列号 = self._编辑位置
        r = self.records[行号]
        行标识 = str(r.get('_id', 行号))
        列序 = sorted(self.可编辑列)
        位置 = 列序.index(列号)
        if 位置 + 1 < len(列序):
            下一列 = 列序[位置 + 1]
        else:
            下一列 = 列序[0]
            可见行 = list(self.tree.get_children())
            if 行标识 in 可见行 and 可见行.index(行标识) + 1 < len(可见行):
                行标识 = 可见行[可见行.index(行标识) + 1]
        if not self._commit_editor(e):
            return 'break'
        self.root.after_idle(lambda: self._open_editor(行标识, '#%d' % (下一列 + 1)))
        return 'break'

    def _快捷键撤销(self, e=None):
        self.undo_edits()

    def _on_double_click(self, e):
        iid = self.tree.identify_row(e.y)
        col = self.tree.identify_column(e.x)
        if iid:
            self.tree.selection_set(iid)
            self._open_editor(iid, col)

    def open_editor_for_selection(self):
        sel = self.tree.selection()
        if not sel:
            return
        self._open_editor(sel[0], '#3')

    def _open_editor(self, iid, col):
        if self.busy or not str(col).startswith('#'):
            return
        try:
            列号 = int(str(col)[1:]) - 1
        except ValueError:
            return
        if 列号 not in self.可编辑列:
            return
        if self._编辑框 is not None and not self._commit_editor():
            return
        行号 = self._row_to_idx(iid)
        if 行号 < 0:
            return
        区域 = self.tree.bbox(iid, col)
        if not 区域:
            return
        字段 = self.可编辑列[列号]
        r = self.records[行号]
        x, y, w, h = 区域
        if 字段 == '_state':
            编辑 = ttk.Combobox(self.tree, font=(主题.字体, 主题.字号), state='readonly')
            编辑['values'] = ('✓ 已确认', '已识别', '? 拿不准', '! 可疑', '已修改', '已打印')
            编辑.set(self._rec_state(r)[0])
        else:
            编辑 = tk.Entry(self.tree, font=(主题.字体, 主题.字号),
                            bg='#fffbe8', fg=主题.正文, relief='solid', bd=1)
            编辑.insert(0, str(r.get(字段, '') or ''))
            编辑.selection_range(0, 'end')
        编辑.place(x=x, y=y, width=w, height=h)
        self._编辑框 = 编辑
        self._编辑位置 = (行号, 列号)
        self._编辑字段 = 字段
        编辑.focus_set()
        编辑.bind('<Return>', lambda e: self._commit_editor(e) and 'break')
        编辑.bind('<Escape>', lambda e: self._cancel_editor() or 'break')
        编辑.bind('<Tab>', self._跳格)
        if 字段 == '_state':
            编辑.bind('<<ComboboxSelected>>', lambda e: self._commit_editor(e))
        编辑.bind('<FocusOut>', self._on_editor_focus_out)

    def _on_editor_focus_out(self, e=None):
        self._commit_editor(e)

    def _commit_editor(self, event=None):
        if self._编辑框 is None or self._编辑位置 is None:
            return True
        行号, 列号 = self._编辑位置
        if 行号 < 0 or 行号 >= len(self.records):
            self._拆掉编辑框()
            return False
        字段 = self.可编辑列[列号]
        值 = self._编辑框.get().strip()
        r = self.records[行号]

        if 字段 == '_state':
            旧值 = self._rec_state(r)[0]
            if 值 != 旧值:
                if 值 == '✓ 已确认':
                    r['_user_confirmed'] = True
                    r['_uncertain'] = False
                    r['_suspect'] = False
                elif 值 == '? 拿不准':
                    r['_uncertain'] = True
                    r['_suspect'] = False
                    r['_user_confirmed'] = False
                elif 值 == '! 可疑':
                    r['_suspect'] = True
                    r['_uncertain'] = False
                    r['_user_confirmed'] = False
                elif 值 == '已识别':
                    r['_uncertain'] = False
                    r['_suspect'] = False
                    r['_user_confirmed'] = False
                elif 值 == '已修改':
                    r['_user_edited'] = True
                elif 值 == '已打印':
                    r['_printed'] = True
        else:
            错误 = self._校验(字段, 值)
            if 错误:
                messagebox.showwarning('内容不对', 错误)
                self._编辑框.focus_set()
                return False
            旧值 = str(r.get(字段, '') or '')
            r[字段] = 值
            if 字段 == 'code':
                r['customer_code'] = 值
            if 值 != 旧值:
                r['_user_edited'] = True
                r['_user_confirmed'] = True
                if 字段 == 'weight':
                    r['_weight_edited'] = True
                    r['_uncertain'] = False
                if 字段 in ('category', 'code'):
                    r['_suspect'] = False

        行标识 = str(r.get('_id', 行号))
        self._拆掉编辑框()
        self._redraw_records()
        if self.tree.exists(行标识):
            self.tree.selection_set(行标识)
            self.tree.see(行标识)
        self.update_preview(r)
        self._刷打印按钮()
        return True

    def _提交编辑器(self):
        return self._commit_editor()

    def _校验(self, 字段, 值):
        if 字段 == 'category' and not 值:
            return '品类不能为空。'
        if 字段 == 'weight':
            try:
                if float(值) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                return '斤数要是大于 0 的数字，例如 3 或 2.5。'
        if 字段 == 'code' and 值 and any(c in 值 for c in ':/\\'):
            return '客户编码不能包含路径符号。'
        return None

    def _拆掉编辑框(self):
        if self._编辑框 is not None:
            try:
                self._编辑框.destroy()
            except Exception:
                pass
        self._编辑框 = None
        self._编辑位置 = None
        if hasattr(self, '_编辑字段'):
            self._编辑字段 = None

    def _cancel_editor(self):
        self._拆掉编辑框()
        return True

    def delete_selected(self):
        if not self._commit_editor():
            return
        sel = self.tree.selection()
        if not sel:
            return
        行号们 = sorted((self._row_to_idx(iid) for iid in sel), reverse=True)
        for idx in 行号们:
            if 0 <= idx < len(self.records):
                self._删除记录.append(self.records.pop(idx))
        self._redraw_records()
        self._redraw_files()
        self._刷打印按钮()
        self.update_preview()

    def undo_edits(self):
        self._cancel_editor()
        if self._原始记录 is not None:
            self.records = copy.deepcopy(self._原始记录)
            self._删除记录 = []
            self._redraw_records()
            self._redraw_files()
            self._刷打印按钮()
            self.update_preview()
            self.set_status('已撤销全部修改')

    def _popup_menu(self, e):
        iid = self.tree.identify_row(e.y)
        if iid:
            self.tree.selection_set(iid)
        菜单 = tk.Menu(self.root, tearoff=False)
        菜单.add_command(label='修改这一行', command=self.open_editor_for_selection)
        菜单.add_command(label='删除这一行', command=self.delete_selected)
        菜单.add_separator()
        菜单.add_command(label='撤销全部修改', command=self.undo_edits)
        菜单.tk_popup(e.x_root, e.y_root)

    def toggle_preview(self):
        if self.preview_on:
            self.pane.forget(self.preview_panel)
            self.preview_on = False
            self.btn_preview.configure(text='显示预览')
        else:
            self.pane.add(self.preview_panel, minsize=P(180))
            self.preview_on = True
            self.btn_preview.configure(text='隐藏预览')
            self.update_preview()

    def update_preview(self, rec=None):
        if not hasattr(self, 'canvas') or not self.preview_on:
            return
        if rec is None:
            sel = self.tree.selection()
            if sel:
                idx = self._row_to_idx(sel[0])
                if 0 <= idx < len(self.records):
                    rec = self.records[idx]
        self.canvas.delete('all')
        if hasattr(self, 'canvas_orig'):
            self.canvas_orig.delete('all')
        if rec is None:
            self.canvas.create_text(self.canvas.winfo_width() / 2 or 100, 70,
                                    text='点一行看它的标签', fill=主题.淡,
                                    font=(主题.字体, 主题.字号))
            self.lbl_preview.configure(text='点一行看它的标签')
            return
        rec = CP.format_record(rec, self.var_customer.get())
        图, _预览警告 = LB.render_label(rec.get('category', ''), rec.get('customer_code', ''),
                                      rec.get('weight', ''), rec.get('unit', ''), rec.get('remark', ''))
        宽 = max(80, self.canvas.winfo_width())
        高 = max(60, self.canvas.winfo_height())
        # 1 位图的白色可能存为 1；直接交给 ImageTk 会显示成 RGB(1,1,1)。
        # 打印仍用原始黑白位图，界面明确转换 RGB 并等比例缩放。
        比例 = min(宽 / 图.width, 高 / 图.height)
        图 = 图.convert('RGB').resize(
            (max(1, round(图.width * 比例)), max(1, round(图.height * 比例))),
            Image.Resampling.NEAREST)
        self._预览图 = ImageTk.PhotoImage(图, master=self.canvas)
        self.canvas.create_image(宽 / 2, 高 / 2, image=self._预览图, anchor='center')
        有改 = '，改过' if rec.get('_user_edited') else ''
        self.lbl_preview.configure(text='%s  |  %s %s %s\n客户编码 %s%s' % (
            rec.get('_src', ''), rec.get('category', ''), rec.get('weight', ''),
            rec.get('unit', ''), rec.get('customer_code', ''), 有改))
        
        if hasattr(self, 'canvas_orig'):
            self.canvas_orig.delete('all')
            orig_path = rec.get('_img')
            if orig_path and Path(orig_path).is_file():
                try:
                    orig_img = Image.open(orig_path)
                    宽o = max(80, self.canvas_orig.winfo_width())
                    高o = max(60, self.canvas_orig.winfo_height())
                    比例o = min(宽o / orig_img.width, 高o / orig_img.height)
                    orig_img = orig_img.convert('RGB').resize(
                        (max(1, round(orig_img.width * 比例o)), max(1, round(orig_img.height * 比例o))),
                        Image.Resampling.LANCZOS)
                    self._orig_图 = ImageTk.PhotoImage(orig_img, master=self.canvas_orig)
                    self.canvas_orig.create_image(宽o / 2, 高o / 2, image=self._orig_图, anchor='center')
                except Exception:
                    pass

    # ---------- 打印 ----------
    def do_print(self):
        self.debug_log('用户点击了[确认打印]按钮')
        if self.busy or not self._commit_editor():
            return
        待打 = [r for r in self.records if not r.get('_printed')]
        if not 待打:
            return
        客户 = self.var_customer.get()
        状态 = LY.读()
        掂 = LY.掂量(len(待打), 状态)
        if not 掂['能打'] and not messagebox.askyesno('标签纸可能不够', 掂['文案'] + '\n\n还要打吗？'):
            return
        self._task_kind = 'print'
        self.set_busy(True)
        self.set_status('正在打印…', 主题.主色)
        self.worker.run(self._print_task, 待打, customer=客户, 重打=False)

    def _print_task(self, records, customer=None, 重打=False):
        self._打印重打 = bool(重打)
        客户 = customer or self.var_customer.get()
        图号们 = {}
        if not 重打:
            来源们 = list(dict.fromkeys(str(r.get('_src', '') or '') for r in records))
            for i, 来源 in enumerate(来源们):
                if 来源:
                    图号们[来源] = '%s_%d' % (datetime.now().strftime('%Y%m%d_%H%M%S'), i + 1)
            if not 客户:
                raise RuntimeError('没有选客户，不能打印分隔标签')
            if not LB.print_separator(客户):
                raise RuntimeError('客户分隔标签发送失败：%s' % (LB.最后打印错误 or '未知错误'))
            时间 = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            DR.记一张(客户, '名牌', '', '', '', '', '', '—', '已发出', 时间)
            PH.加(客户, '名牌', '', '', '', '', '', '—', '已发出', 时间)
            LY.记一张(1, LY.读())
        成功数 = 0
        for r in CP.排序记录(records, 客户):
            if r.get('_printed') and not 重打:
                continue
            # 历史重打使用已记录的输出字段；当前识别记录只在输出时套格式。
            输出 = r if 重打 and r.get('type') else CP.format_record(r, 客户)
            cat = 输出.get('category', '')
            code = 输出.get('customer_code', '') or 输出.get('code', '')
            w = 输出.get('weight', '')
            u = 输出.get('unit', '斤')
            rem = 输出.get('remark', '')
            if isinstance(rem, list):
                rem = ' '.join(rem)
            客 = r.get('_客户') or r.get('customer') or 客户 or ''
            是名牌 = 重打 and r.get('type') == '名牌'
            if 是名牌:
                if not 客:
                    raise RuntimeError('历史名牌缺少客户名，不能补打')
                发送成功 = LB.print_separator(客)
            else:
                发送成功 = LB.print_label(cat, code, w, u, rem)
            if not 发送成功:
                内容 = '客户名牌「%s」' % 客 if 是名牌 else '「%s %s%s」' % (cat, w, u)
                raise RuntimeError('第 %d 张%s发送失败：%s' % (
                    成功数 + 1, 内容, LB.最后打印错误 or '打印机未确认成功'))
            图 = 图号们.get(str(r.get('_src', '') or ''), '')
            时间 = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            结果 = '已发出（手改）' if r.get('_user_edited') else '已发出'
            类型 = '名牌' if 是名牌 else ('重打' if 重打 else '单据')
            DR.记一张(客, 类型, cat, code, w, u, rem, 图, 结果, 时间)
            PH.加(客, 类型, cat, code, w, u, rem, 图, 结果, 时间)
            LY.记一张(1, LY.读())
            r['_printed'] = True
            成功数 += 1
        return 成功数

    def on_print_done(self, 张数):
        self.debug_log('打印流程结束，共完成 %d 张' % 张数)
        self.set_busy(False)
        if getattr(self, '_打印重打', False):
            self._打印重打 = False
            self._redraw_records()
            self._刷打印按钮()
            self._刷纸chip()
            self.set_status('已重打 %d 张' % 张数, 主题.成功)
            return
        self._这批打完清空(张数)

    def _这批打完清空(self, 张数):
        self.records = []
        self._原始记录 = None
        self._删除记录 = []
        self._redraw_records()
        self._redraw_files()
        self._刷打印按钮()
        self._刷纸chip()
        self.set_status('已打印 %d 张' % 张数, 主题.成功)

    def _缺纸停了(self):
        LY.标没纸了()
        self._刷纸chip()

    def on_print_error(self, e):
        self.set_busy(False)
        self._redraw_records()
        self._redraw_files()
        self._刷打印按钮()
        self._刷纸chip()
        self.set_status('打印失败：%s' % e, 主题.危险)
        self.log('打印失败：%s' % e)
        messagebox.showerror('打印失败', str(e))

    def _on_task_done(self, r):
        if self._task_kind == 'print':
            self.on_print_done(r)
        else:
            self.on_scan_done(r)

    def on_task_error(self, e):
        if self._task_kind == 'print':
            self.on_print_error(e)
        else:
            self.on_scan_error(e)


def main():
    声明DPI感知()
    if _HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    app = App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
