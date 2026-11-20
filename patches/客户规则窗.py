# -*- coding: utf-8 -*-
"""客户规则窗 —— 「新建」和「编辑规则」共用同一个三标签窗口。

为什么要做成补丁
----------------
主程序原来的 _设置_编辑客户 是「把整份 customers.json 摊在一个文本框里
让你手改 JSON」：改一个勾要懂 JSON 结构，改错一个引号就存不进去。
这个补丁只替换两个入口（App._设置_新建客户 / App._设置_编辑客户），
窗体本身完全独立，其余功能照主程序原样跑。
补丁任何一处出错都退回原实现（见文件末尾），不影响启动。

界面结构严格照 设计和测试/客户规则界面*.png：
    客户名 / 图片文件夹
    基本规则 · 客户说明 · 品类归一（三个标签）
    底部：改名字… · 保存 · 取消

落盘
----
保存走 customer_profiles，最终写项目根目录的 customers.json
（CP.客户档 = 程序目录()/customers.json，先写 .tmp 再原子改名）。
"""

import json
import tkinter as tk
from tkinter import messagebox, ttk

from app import customer_profiles as CP

主模块 = globals().get('主模块')

# ---------------------------------------------------------------------------
# 颜色 / 字号（取自参考截图的采样值）
# ---------------------------------------------------------------------------

_底 = '#f1f5f9'        # 内容底色 slate-100
_面 = '#ffffff'
_正文 = '#0f172a'      # slate-900
_次要 = '#64748b'      # slate-500
_淡 = '#94a3b8'
_边 = '#cbd5e1'        # 输入框细边框
_提示底 = '#e7edf5'    # 「手写单会自动更仔细」那种灰底小提示
_提示边 = '#d3dce8'
_蓝 = '#2563eb'        # 保存按钮
_蓝深 = '#1d4ed8'
_页灰 = '#dcdad5'      # 未选中的标签页（截图里的 Windows 灰）
_页边 = '#b4b0a8'
_隔线 = '#e2e8f0'


def _px(v):
    return 主模块.P(v)


def _工作区():
    """Windows 上「去掉任务栏之后」的桌面区域 (左, 上, 宽, 高)；拿不到就返回 None。

    Tk 的 winfo_screenheight 含任务栏，照着它算高度会把保存按钮压到任务栏底下。
    """
    try:
        import ctypes

        class _RECT(ctypes.Structure):
            _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                        ('right', ctypes.c_long), ('bottom', ctypes.c_long)]

        矩 = _RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(矩), 0):
            宽 = 矩.right - 矩.left
            高 = 矩.bottom - 矩.top
            if 宽 > 200 and 高 > 200:
                return 矩.left, 矩.top, 宽, 高
    except Exception:
        pass
    return None


def _字(号=10, 粗=False):
    return (主模块.主题.字体, 号, 'bold' if 粗 else 'normal')


# 编码怎么打 / 备注怎么截 —— 值就是 customers.json 里的原值，不另造一套
_编码选项 = (
    ('原样打', 'full'),
    ('砍掉编码里的中文', 'nochinese'),
    ('只留前面的编号', 'leadcode'),
)
_备注选项 = (
    ('整条截前 3 字', 'one'),
    ('按顿号拆开，各截前 3 字', 'split'),
)


# ---------------------------------------------------------------------------
# 品类归一文本 ←→ category_alias 字典
#   customers.json 里是 {标准名: {写法: 补字}}
#   界面上写的是  生姜: 生姜, 姜肉, 大生姜=大
# 两种表示一一对应，不丢信息；没有冒号的行直接跳过（宁可少归一一条，不吞整张表）。
# ---------------------------------------------------------------------------

def 归一文本(表):
    行 = []
    for 标准名, 写法们 in (表 or {}).items():
        段 = []
        for 写法, 补 in (写法们 or {}).items():
            段.append(str(写法) + ('=' + str(补) if 补 else ''))
        if 段:
            行.append('%s: %s' % (标准名, ', '.join(段)))
    return '\n'.join(行)


def 解析归一(文本):
    表 = {}
    for 行 in (文本 or '').splitlines():
        行 = 行.strip()
        if not 行 or 行.startswith('#'):
            continue
        行 = 行.replace('：', ':')
        if ':' not in 行:
            continue
        标准名, 右 = 行.split(':', 1)
        标准名 = 标准名.strip()
        if not 标准名:
            continue
        写法们 = {}
        for 项 in 右.replace('，', ',').replace('、', ',').split(','):
            项 = 项.strip()
            if not 项:
                continue
            if '=' in 项:
                写法, 补 = 项.split('=', 1)
                写法, 补 = 写法.strip(), 补.strip()
            else:
                写法, 补 = 项, ''
            if 写法:
                写法们[写法] = 补
        表[标准名] = 写法们
    return 表


def 解析跳过(文本):
    项 = []
    for 行 in (文本 or '').splitlines():
        行 = 行.strip()
        if 行 and 行 not in 项:
            项.append(行)
    return 项


# ---------------------------------------------------------------------------
# 小控件工厂
# ---------------------------------------------------------------------------

def _勾(父, 文字, 变量, 命令=None):
    return tk.Checkbutton(
        父, text=文字, variable=变量, command=命令,
        bg=父.cget('bg'), fg=_正文, activebackground=父.cget('bg'),
        activeforeground=_正文, selectcolor=_面, anchor='w', justify='left',
        bd=0, highlightthickness=0, cursor='hand2', font=_字(10))


def _单选(父, 文字, 变量, 值):
    return tk.Radiobutton(
        父, text=文字, variable=变量, value=值,
        bg=父.cget('bg'), fg=_正文, activebackground=父.cget('bg'),
        activeforeground=_正文, selectcolor=_面, anchor='w', justify='left',
        bd=0, highlightthickness=0, cursor='hand2', font=_字(10))


def _多行框(父, 行数):
    return tk.Text(
        父, height=行数, wrap='char', undo=True, bg=_面, fg=_正文,
        relief='flat', bd=0, highlightthickness=1,
        highlightbackground=_边, highlightcolor=_蓝, insertbackground=_正文,
        font=_字(10), padx=_px(6), pady=_px(4))


def _说明(父, 文字):
    return tk.Label(父, text=文字, bg=父.cget('bg'), fg=_次要, font=_字(9),
                    anchor='w', justify='left', wraplength=_px(690))


def _填(框, 文本):
    框.delete('1.0', 'end')
    框.insert('1.0', 文本)


def _选值(框, 选项, 值):
    for 序, (标签, 实际) in enumerate(选项):
        if 实际 == 值:
            框.current(序)
            return
    框.current(0)


def _取值(框, 选项, 缺省):
    序 = 框.current()
    if 0 <= 序 < len(选项):
        return 选项[序][1]
    return 缺省


class _滚(tk.Frame):
    """标签页里的滚动容器，真正内容放在 .内容。"""

    def __init__(self, 父):
        super().__init__(父, bg=_底)
        self.canvas = tk.Canvas(self, bg=_底, highlightthickness=0, bd=0)
        self.bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.bar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.内容 = tk.Frame(self.canvas, bg=_底)
        self._窗 = self.canvas.create_window((0, 0), window=self.内容, anchor='nw')
        self.内容.bind('<Configure>', self._范围)
        self.canvas.bind('<Configure>', self._宽)
        self.canvas.bind('<Enter>', lambda e: self.canvas.bind_all('<MouseWheel>', self._轮))
        self.canvas.bind('<Leave>', lambda e: self.canvas.unbind_all('<MouseWheel>'))

    def _范围(self, _e=None):
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        except Exception:
            pass

    def _宽(self, e):
        try:
            self.canvas.itemconfigure(self._窗, width=e.width)
        except Exception:
            pass

    def _轮(self, e):
        try:
            self.canvas.yview_scroll(int(-e.delta / 120), 'units')
        except Exception:
            pass
        return 'break'


# ---------------------------------------------------------------------------
# 窗口
# ---------------------------------------------------------------------------

class 规则窗:
    """新建 / 编辑规则 —— 同一个窗口，只差「客户名能不能填」和「有没有改名字」。"""

    def __init__(self, app, 模式, 客户名='', 源客户=''):
        self.app = app
        self.模式 = 'edit' if 模式 == 'edit' else 'new'
        self.名 = str(客户名 or '').strip()
        self.源 = str(源客户 or '').strip()
        self.保存了 = False
        self._窗 = None
        底 = CP.get_profile(self.名) if self.模式 == 'edit' else CP.get_profile(源客户)
        self.基档 = json.loads(json.dumps(底, ensure_ascii=False)) if isinstance(底, dict) else {}
        self.搭()

    # ---------- 骨架 ----------
    def 搭(self):
        窗 = tk.Toplevel(self.app.root)
        self._窗 = 窗
        窗.title(('编辑规则 —— ' + self.名) if self.模式 == 'edit' else '新建客户规则')
        窗.configure(bg=_底)
        窗.minsize(_px(660), _px(520))
        try:
            窗.transient(self.app.root)
            窗.grab_set()
        except Exception:
            pass
        窗.geometry(self._几何())

        self._搭头(窗)
        self._搭底(窗)               # 先占位在底部，中间那块才能吃掉剩余高度
        书 = self._搭标签(窗)
        self._搭基本规则(书)
        self._搭客户说明(书)
        self._搭品类归一(书)
        self._回显(self.基档)        # 控件建完统一回填一次，避免建的时候漏一项

        try:
            窗.protocol('WM_DELETE_WINDOW', self.关闭)
            窗.bind('<Escape>', lambda e: self.关闭())
        except Exception:
            pass

    def _几何(self):
        宽, 高 = _px(795), _px(862)
        try:
            区 = _工作区()
            if 区:
                左区, 上区, 区宽, 区高 = 区
            else:
                左区, 上区 = 0, 0
                区宽, 区高 = self._窗.winfo_screenwidth(), self._窗.winfo_screenheight()
            # 高分屏上 795×862 换算成物理像素可能高过可用桌面，底部按钮会被任务栏盖住，
            # 所以先收到「可用桌面」以内；收下来以后内容靠标签页里的滚动条看。
            宽 = max(_px(620), min(宽, 区宽 - _px(20)))
            高 = max(_px(480), min(高, 区高 - _px(20)))
            x = 左区 + max(0, (区宽 - 宽) // 2)
            y = 上区 + max(0, (区高 - 高) // 2)
            return '%dx%d+%d+%d' % (宽, 高, x, y)
        except Exception:
            return '%dx%d' % (宽, 高)

    def _搭头(self, 窗):
        头 = tk.Frame(窗, bg=_底)
        头.pack(side='top', fill='x', padx=_px(22), pady=(_px(14), _px(8)))

        行1 = tk.Frame(头, bg=_底)
        行1.pack(fill='x')
        tk.Label(行1, text='客户名：', bg=_底, fg=_正文,
                 font=_字(11, True)).pack(side='left')
        if self.模式 == 'edit':
            self._名标签 = tk.Label(行1, text=self.名, bg=_底, fg=_正文, font=_字(11, True))
            self._名标签.pack(side='left')
        else:
            self._名输入 = tk.Entry(行1, font=_字(11), width=18, bg=_面, fg=_正文,
                                    relief='flat', bd=0, highlightthickness=1,
                                    highlightbackground=_边, highlightcolor=_蓝,
                                    insertbackground=_正文)
            self._名输入.pack(side='left', ipady=_px(2))
            self._名输入.focus_set()

        行2 = tk.Frame(头, bg=_底)
        行2.pack(fill='x', pady=(_px(3), 0))
        tk.Label(行2, text='图片文件夹：', bg=_底, fg=_次要,
                 font=_字(10)).pack(side='left')
        if self.模式 == 'edit':
            tk.Label(行2, text=str(self.基档.get('folder') or self.名), bg=_底,
                     fg=_次要, font=_字(10)).pack(side='left')
        else:
            self._夹输入 = tk.Entry(行2, font=_字(10), width=18, bg=_面, fg=_次要,
                                    relief='flat', bd=0, highlightthickness=1,
                                    highlightbackground=_边, highlightcolor=_蓝,
                                    insertbackground=_正文)
            self._夹输入.pack(side='left', ipady=_px(1))

        行3 = tk.Frame(头, bg=_底)
        if self.模式 == 'new':
            行3.pack(fill='x', pady=(_px(6), 0))
            tk.Label(行3, text='从哪个客户复制：', bg=_底, fg=_次要,
                     font=_字(9)).pack(side='left')
            客户们 = CP.list_customers()
            self._模板框 = ttk.Combobox(行3, values=客户们, state='readonly',
                                        width=16, font=_字(9))
            self._模板框.pack(side='left')
            self._模板框.bind('<<ComboboxSelected>>', self._换模板)
            if 客户们:
                首选 = self.源 if self.源 in 客户们 else 客户们[0]
                self._模板框.current(客户们.index(首选))
            tk.Label(行3, text='（图片文件夹留空就跟客户名一样）', bg=_底, fg=_淡,
                     font=_字(9)).pack(side='left', padx=(_px(8), 0))

    def _搭标签(self, 窗):
        style = ttk.Style(窗)
        style.configure('客户规则.TNotebook', background=_底, borderwidth=0,
                        tabmargins=(0, 0, 0, 0))
        style.configure('客户规则.TNotebook.Tab', background=_页灰, foreground=_正文,
                        font=_字(10), padding=(_px(14), _px(5)))
        style.map('客户规则.TNotebook.Tab',
                  background=[('selected', _面)],
                  lightcolor=[('selected', _面)],
                  bordercolor=[('selected', _页边)])
        书 = ttk.Notebook(窗, style='客户规则.TNotebook')
        书.pack(fill='both', expand=True, padx=_px(18), pady=(0, _px(10)))
        self._书 = 书
        return 书

    def _搭底(self, 窗):
        底 = tk.Frame(窗, bg=_面)
        底.pack(side='bottom', fill='x')
        tk.Frame(底, bg=_隔线, height=1).pack(fill='x')
        内 = tk.Frame(底, bg=_面)
        内.pack(fill='x', padx=_px(22), pady=_px(9))

        if self.模式 == 'edit':
            改 = tk.Label(内, text='改名字...', bg=_面, fg=_正文,
                          font=_字(10), cursor='hand2')
            改.pack(side='left')
            改.bind('<Button-1>', lambda e: self._改名字())
        else:
            tk.Label(内, text='保存后会写进 customers.json', bg=_面, fg=_淡,
                     font=_字(9)).pack(side='left')

        取消 = tk.Button(内, text='取消', command=self.关闭, bg=_面, fg=_正文,
                         relief='flat', bd=0, activebackground=_面,
                         activeforeground=_正文, cursor='hand2',
                         font=_字(10), padx=_px(12), pady=_px(6))
        取消.pack(side='right', padx=(_px(6), 0))
        self._保存钮 = tk.Button(内, text='保存', command=self._保存, bg=_蓝,
                                 fg='#ffffff', relief='flat', bd=0,
                                 activebackground=_蓝深, activeforeground='#ffffff',
                                 cursor='hand2', font=_字(10),
                                 padx=_px(26), pady=_px(7))
        self._保存钮.pack(side='right')
        return 底

    def _节(self, 父, 文字, 上=(_px(10), _px(2))):
        tk.Label(父, text=文字, bg=_底, fg=_正文, font=_字(11, True),
                 anchor='w').pack(fill='x', padx=_px(16), pady=上)

    # ---------- 基本规则 ----------
    def _搭基本规则(self, 书):
        页 = _滚(书)
        书.add(页, text='基本规则')
        体 = 页.内容

        self._节(体, '单据类型', 上=(_px(10), _px(4)))
        行 = tk.Frame(体, bg=_底)
        行.pack(fill='x', padx=_px(16))
        self.var_手写 = tk.BooleanVar(value=CP.is_handwritten(self.基档))
        _单选(行, '电脑打印的截图/单子', self.var_手写, False).pack(side='left')
        _单选(行, '手写单（照片）', self.var_手写, True).pack(side='left', padx=(_px(12), 0))
        tk.Label(行, text='手写单会自动更仔细，也会多跑一遍对照', bg=_提示底,
                 fg=_次要, font=_字(9), padx=_px(7), pady=_px(3),
                 highlightthickness=1, highlightbackground=_提示边
                 ).pack(side='left', padx=(_px(12), 0))

        self._节(体, '标签上打印什么')
        self.var_打码 = tk.BooleanVar(value=bool(self.基档.get('show_code', True)))
        _勾(体, '打印客户编码', self.var_打码).pack(fill='x', padx=_px(16))

        self.编码框 = ttk.Combobox(体, values=[x[0] for x in _编码选项],
                                   state='readonly', font=_字(10))
        self.编码框.pack(fill='x', padx=_px(16), pady=(_px(2), _px(2)))
        _选值(self.编码框, _编码选项, self.基档.get('code_keep', 'full'))

        self.备注框 = ttk.Combobox(体, values=[x[0] for x in _备注选项],
                                   state='readonly', font=_字(10))
        self.备注框.pack(fill='x', padx=_px(16), pady=(0, _px(2)))
        _选值(self.备注框, _备注选项, self.基档.get('remark_groups', 'one'))

        self._节(体, '识别')
        self.var_仔细 = tk.BooleanVar(value=str(self.基档.get('effort', 'off')) == 'high')
        _勾(体, '仔细模式（让 AI 多想一会儿，慢但准）', self.var_仔细).pack(
            fill='x', padx=_px(16))
        行 = tk.Frame(体, bg=_底)
        行.pack(fill='x', padx=_px(16))
        self.var_单位 = tk.BooleanVar(value=bool(self.基档.get('unit_fix')))
        _勾(行, '单位纠错（这家只可能是斤/包）', self.var_单位).pack(side='left')
        self.var_大写 = tk.BooleanVar(value=bool(self.基档.get('upper_code')))
        _勾(行, '编码首字母自动大写', self.var_大写).pack(side='left', padx=(_px(18), 0))

        self._节(体, '整条跳过的品类（一行一个，空着就是都不跳）')
        self.跳过框 = _多行框(体, 6)
        self.跳过框.pack(fill='x', padx=_px(16), pady=(0, _px(10)))
        self.跳过框.insert('1.0', '\n'.join(self.基档.get('skip_items') or []))

    # ---------- 客户说明 ----------
    def _搭客户说明(self, 书):
        页 = _滚(书)
        书.add(页, text='客户说明')
        体 = 页.内容

        self._节(体, '客户专属说明（写给 AI 看 —— 这段最影响识别准不准）', 上=(_px(10), _px(3)))
        _说明(体, '这家单据长什么样、哪里容易认错，都写在这儿。改这段最见效。').pack(
            fill='x', padx=_px(16))
        self.说明框 = _多行框(体, 22)
        self.说明框.pack(fill='x', padx=_px(16), pady=(_px(6), _px(10)))
        self.说明框.insert('1.0', str(self.基档.get('extra_note') or ''))

    # ---------- 品类归一 ----------
    def _搭品类归一(self, 书):
        页 = _滚(书)
        书.add(页, text='品类归一')
        体 = 页.内容

        self._节(体, '品类归一（同一个货的不同叫法，并成一个名字）', 上=(_px(10), _px(3)))
        _说明(体, '一行一个标准名。格式：标准名: 写法1, 写法2, 写法3=要补的备注\n'
                  '例：生姜: 生姜, 姜肉, 老姜肉, 大生姜=大\n'
                  '「=大」的意思是：并成「生姜」之后，标签备注里再加个「大」\n'
                  '⚠ 写法要写全 —— 只有一字不差的写法才会被并掉，不写就不动它').pack(
            fill='x', padx=_px(16))
        self.归一框 = _多行框(体, 12)
        self.归一框.pack(fill='x', padx=_px(16), pady=(_px(6), _px(4)))
        self.归一框.insert('1.0', 归一文本(self.基档.get('category_alias') or {}))

        self.var_同类 = tk.BooleanVar(value=bool(self.基档.get('sort_categories')))
        _勾(体, '同品类的记录排到一起（单据乱序时，分货不用来回换货）',
            self.var_同类).pack(fill='x', padx=_px(16))
        _说明(体, '品类之间谁先谁后不固定（按第一次出现的顺序），只要同一类聚在一起就行。'
                  '同一类里面，原来的先后顺序不变。').pack(
            fill='x', padx=_px(16), pady=(0, _px(10)))

    def _回显(self, 档):
        """把一份档案填回界面（新建时换模板就靠它）。"""
        self.var_手写.set(CP.is_handwritten(档))
        self.var_打码.set(bool(档.get('show_code', True)))
        self.var_仔细.set(str(档.get('effort', 'off')) == 'high')
        self.var_单位.set(bool(档.get('unit_fix')))
        self.var_大写.set(bool(档.get('upper_code')))
        self.var_同类.set(bool(档.get('sort_categories')))
        _选值(self.编码框, _编码选项, 档.get('code_keep', 'full'))
        _选值(self.备注框, _备注选项, 档.get('remark_groups', 'one'))
        _填(self.跳过框, '\n'.join(档.get('skip_items') or []))
        _填(self.说明框, str(档.get('extra_note') or ''))
        _填(self.归一框, 归一文本(档.get('category_alias') or {}))

    def _换模板(self, _e=None):
        self.基档 = json.loads(json.dumps(CP.get_profile(self._模板框.get()),
                                          ensure_ascii=False))
        self._回显(self.基档)

    # ---------- 取值 / 保存 ----------
    def _收集(self, 底档):
        档 = json.loads(json.dumps(底档 or {}, ensure_ascii=False))
        档['doc_type'] = 'handwritten' if self.var_手写.get() else 'printed'
        档.pop('handwritten', None)      # 历史短写法会盖过 doc_type，一律清掉
        档['effort'] = 'high' if self.var_仔细.get() else 'off'
        档['show_code'] = bool(self.var_打码.get())
        档['code_keep'] = _取值(self.编码框, _编码选项, 'full')
        档['remark_groups'] = _取值(self.备注框, _备注选项, 'one')
        档['keep_note'] = bool(档.get('keep_note', True))
        档['skip_items'] = 解析跳过(self.跳过框.get('1.0', 'end'))
        档['skip_contains'] = list(档.get('skip_contains') or [])
        档['extra_note'] = self.说明框.get('1.0', 'end').strip()
        档['category_alias'] = 解析归一(self.归一框.get('1.0', 'end'))
        档['category_order'] = list(档.get('category_order') or [])
        档['sort_categories'] = bool(self.var_同类.get())
        档['unit_fix'] = bool(self.var_单位.get())
        档['upper_code'] = bool(self.var_大写.get())
        return 档

    def _保存(self):
        名 = self.名 if self.模式 == 'edit' else self._名输入.get().strip()
        if not 名:
            messagebox.showwarning('填一下客户名', '客户名不能为空。', parent=self._窗)
            self._名输入.focus_set()
            return

        原文 = self.归一框.get('1.0', 'end')
        别名表 = 解析归一(原文)
        if 原文.strip() and not 别名表:
            messagebox.showwarning(
                '这段没看懂',
                '一行都没认出「标准名: 写法1, 写法2」的格式，请检查冒号。\n'
                '（这段整个空着才表示不做归一，不会把原来存的归一冲掉）',
                parent=self._窗)
            return
        冲突 = CP.归一冲突(别名表)
        if 冲突:
            写法, 标准们 = 冲突[0]
            messagebox.showwarning(
                '归一写重了',
                '「%s」同时被这些名字抢：%s。\n同一个写法只能归到一个名字，请先改掉再保存。'
                % (写法, '、'.join(标准们)), parent=self._窗)
            return

        if self.模式 == 'new':
            if CP.还能加几个() <= 0:
                messagebox.showwarning('客户已满', '客户数量已到上限，请先删除不再使用的客户。',
                                       parent=self._窗)
                return
            if 名 in CP.list_customers():
                messagebox.showwarning('重名了', '客户「%s」已经存在，换个名字。' % 名,
                                       parent=self._窗)
                return
            文件夹 = self._夹输入.get().strip()
            if not self._模板框.get():
                messagebox.showwarning('缺模板', '请选择从哪个客户复制规则。', parent=self._窗)
                return
            成功, 消息 = CP.新建档案(名, self._模板框.get(), 文件夹 or 名)
            if not 成功:
                messagebox.showwarning('新建失败', 消息, parent=self._窗)
                return
            成功, 消息 = CP.更新档案(名, self._收集(CP.get_profile(名)))
        else:
            成功, 消息 = CP.更新档案(self.名, self._收集(self.基档))
        if not 成功:
            messagebox.showwarning('保存失败', 消息, parent=self._窗)
            return

        self.保存了 = True
        self.关闭()
        self._保存后(名)

    def _保存后(self, 名):
        """刷新设置页的客户表 / 右侧客户提示；任何一步失败都不影响已经存好的规则。"""
        try:
            self.app._设置_刷新客户表()
        except Exception:
            pass
        try:
            if self.模式 == 'new':
                self.app._同步客户控件(名)
            elif str(self.app.var_customer.get()) == 名:
                self.app._on_customer_change()
        except Exception:
            pass
        try:
            self.app.set_status('已保存「%s」的规则 → customers.json' % 名)
        except Exception:
            pass

    # ---------- 改名字 ----------
    def _改名字(self):
        值 = self.app._客户窗('客户改名', {'名称': self.名, '只改名': True})
        if not 值:
            return
        新名 = 值['名称'].strip()
        if not 新名 or 新名 == self.名:
            return
        成功, 消息 = CP.改名档案(self.名, 新名)
        if not 成功:
            messagebox.showwarning('改名失败', 消息, parent=self._窗)
            return
        self.名 = 新名
        self.基档 = CP.get_profile(新名)
        try:
            self._名标签.configure(text=新名)
            self._窗.title('编辑规则 —— ' + 新名)
        except Exception:
            pass
        try:
            self.app._设置_刷新客户表()
        except Exception:
            pass

    # ---------- 收尾 ----------
    def 关闭(self):
        try:
            self._窗.grab_release()
        except Exception:
            pass
        try:
            self._窗.destroy()
        except Exception:
            pass

    def 等待(self):
        try:
            self._窗.wait_window()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 接进主程序：只换两个入口，其余原样
# ---------------------------------------------------------------------------

def _编辑规则(self):
    名 = self._设置_选中的客户()
    if not 名:
        messagebox.showinfo('没选中', '先选择一个客户。')
        return False
    窗 = 规则窗(self, 'edit', 客户名=名)
    窗.等待()
    return 窗.保存了


def _新建客户(self):
    if CP.还能加几个() <= 0:
        messagebox.showwarning('客户已满', '客户数量已到上限，请先删除不再使用的客户。')
        return False
    源 = (self._设置_选中的客户()
          or str(getattr(self, 'var_customer', None).get() if getattr(self, 'var_customer', None) else '')
          or (CP.list_customers() or [''])[0])
    窗 = 规则窗(self, 'new', 源客户=源)
    窗.等待()
    return 窗.保存了


def _装():
    App = 主模块.App
    App._设置_新建客户 = _新建客户
    App._设置_编辑客户 = _编辑规则


try:
    if 主模块 is None:
        raise RuntimeError('缺少主模块注入')
    _应用类 = 主模块.App
    原_新建 = getattr(_应用类, '_设置_新建客户')
    原_编辑 = getattr(_应用类, '_设置_编辑客户')
    _装()
except Exception as 错误:
    # 补丁没装上就退回主程序原来的 JSON 编辑框，绝不因为补丁挂掉
    try:
        类 = globals().get('_应用类')
        if 类 is not None:
            if callable(globals().get('原_新建')):
                类._设置_新建客户 = 原_新建
            if callable(globals().get('原_编辑')):
                类._设置_编辑客户 = 原_编辑
    except Exception:
        pass
    try:
        import 路径 as _路径
        with (_路径.程序目录() / '补丁加载诊断.log').open('a', encoding='utf-8') as _f:
            _f.write('客户规则窗.py: %r\n' % (错误,))
    except Exception:
        pass
