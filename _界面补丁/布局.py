# -*- coding: utf-8 -*-
# 在「打单助手」原模块的命名空间里执行：只重写识单页的三块布局。
# 目的：14 寸笔记本（1920x1080，150% 缩放）上，让核对清单一屏能看到 8 行左右，
#       而不是原来的 2~3 行。业务逻辑一行不动。

# ---------- 整体框架：深色左栏 + 白色顶栏 + 页面内容 ----------
主题.字号页标题 = 16
主题.字号正文 = 主题.字号
主题.字号分区 = 主题.字号
主题.侧栏 = '#1b2533'
主题.侧栏选中 = '#2b3b4f'
主题.侧栏字 = '#d9e0e8'
主题.侧栏淡 = '#8f9cab'

App.导航 = [
    ('scan', '单据识别', '▦'),
    ('history', '打印历史', '◷'),
    ('周末', '周末模式', '末'),
    ('settings', '设置', '⚙'),
]


def _build_ui(self):
    self._当前页 = None
    self.root.minsize(960, 640)
    self.root.geometry('2160x1426')
    self._build_sidebar()
    self._主区 = tk.Frame(self.root, bg=主题.底色)
    self._主区.pack(side='left', fill='both', expand=True)
    self._build_topbar()
    
    # 在内容区之前，将其 pack 在底部，使得所有页面共享运行日志
    self._build_log(self._主区)
    
    self.内容 = tk.Frame(self._主区, bg=主题.底色)
    self.内容.pack(fill='both', expand=True)
    self._页容器 = self.内容
    self.页 = {
        'scan': self._build_page_scan(self.内容),
        'history': self._build_page_history(self.内容),
        'settings': self._build_page_settings(self.内容),
    }
    self.goto('scan')


def _build_sidebar(self):
    self._侧边 = tk.Frame(self.root, bg=主题.侧栏, width=P(144))
    self._侧边.pack(side='left', fill='y')
    self._侧边.pack_propagate(False)

    品牌 = tk.Frame(self._侧边, bg=主题.侧栏)
    品牌.pack(fill='x')
    tk.Label(品牌, text='打单助手', bg=主题.侧栏, fg='#ffffff',
             font=(主题.字体, 15, 'bold'), anchor='w').pack(
                 fill='x', padx=(P(18), P(38)), pady=(P(20), P(2)))
    tk.Label(品牌, text='单据识别 · 标签打印', bg=主题.侧栏, fg=主题.侧栏淡,
             font=(主题.字体, 主题.字号小), anchor='w').pack(
                 fill='x', padx=P(18), pady=(0, P(22)))
    self._侧栏开关 = tk.Label(品牌, text='☰', bg=主题.侧栏, fg=主题.侧栏淡,
                             font=(主题.字体, 15), cursor='hand2')
    self._侧栏开关.place(relx=1.0, x=P(-12), y=P(22), anchor='ne')
    self._侧栏开关.bind('<Button-1>', lambda e: self._切换侧边())

    tk.Label(self._侧边, text='功能', bg=主题.侧栏, fg=主题.侧栏淡,
             font=(主题.字体, 主题.字号小), anchor='w').pack(
                 fill='x', padx=P(18), pady=(0, P(8)))

    self._导航按钮 = {}
    for 键, 名, 图标 in App.导航:
        b = tk.Label(self._侧边, text=f'{图标}   {名}', bg=主题.侧栏,
                     fg=主题.侧栏字, font=(主题.字体, 主题.字号, 'bold'),
                     padx=P(18), pady=P(8), anchor='w', cursor='hand2')
        b.pack(fill='x')
        b.bind('<Button-1>', lambda e, k=键: self.goto(k))
        b.bind('<Enter>', self._导航悬停)
        b.bind('<Leave>', self._导航悬停)
        self._导航按钮[键] = b

    底 = tk.Frame(self._侧边, bg=主题.侧栏)
    底.pack(side='bottom', fill='x', pady=(0, P(14)))
    打印机 = str(设置.取('打印机.名称', '佳博 GP-1326D'))
    self._侧栏打印机 = tk.Label(底, text='40×30mm · ' + 打印机,
                                bg=主题.侧栏, fg=主题.侧栏淡,
                                font=(主题.字体, 8), anchor='w')
    self._侧栏打印机.pack(fill='x', padx=P(18), pady=(0, P(22)))
    tk.Label(底, text='当前客户', bg=主题.侧栏, fg=主题.侧栏淡,
             font=(主题.字体, 主题.字号小), anchor='w').pack(
                 fill='x', padx=P(18))
    self._侧栏客户 = tk.Label(底, text='未选择', bg=主题.侧栏, fg='#ffffff',
                             font=(主题.字体, 主题.字号 + 1, 'bold'), anchor='w')
    self._侧栏客户.pack(fill='x', padx=P(18), pady=(P(3), 0))


def _导航悬停(self, e):
    键 = next((k for k, b in self._导航按钮.items() if b is e.widget), None)
    if not 键 or 键 == self._当前页:
        return
    e.widget.configure(bg=主题.侧栏选中 if e.type == '7' else 主题.侧栏)


def _导航刷新(self, 当前):
    for 键, b in self._导航按钮.items():
        选中 = 键 == 当前
        b.configure(bg=主题.侧栏选中 if 选中 else 主题.侧栏,
                    fg='#ffffff' if 选中 else 主题.侧栏字)


def _侧边收起(self):
    if self._侧边.winfo_manager():
        self._侧边.pack_forget()
    else:
        self._侧边.pack(side='left', fill='y', before=self._主区)


def _build_topbar(self):
    self._顶栏 = tk.Frame(self._主区, bg=主题.面, height=P(56))
    self._顶栏.pack(fill='x')
    self._顶栏.pack_propagate(False)
    self.lbl_页标题 = tk.Label(self._顶栏, text='单据识别', bg=主题.面,
                              fg=主题.正文, font=(主题.字体, 主题.字号页标题, 'bold'))
    self.lbl_页标题.pack(side='left', padx=(P(16), P(22)), pady=P(12))
    self.lbl_title = self.lbl_页标题

    self._纸chip = tk.Label(self._顶栏, text='', bg=主题.成功淡, fg=主题.成功,
                            cursor='hand2', font=(主题.字体, 主题.字号小),
                            padx=P(10), pady=P(5))
    self._纸chip.pack(side='left', padx=(0, P(10)))
    self._纸chip.bind('<Button-1>', lambda e: self._点纸chip())
    self._客户chip = tk.Label(self._顶栏, text='当前客户  未选择', bg=主题.主色淡,
                              fg=主题.主色, font=(主题.字体, 主题.字号小),
                              padx=P(10), pady=P(5))
    self._客户chip.pack(side='left', padx=(0, P(12)))
    
    # 右侧容器
    self._顶栏右 = tk.Frame(self._顶栏, bg=主题.面)
    self._顶栏右.pack(side='right', fill='y')
    
    self.btn_返回 = tk.Button(self._顶栏右, text='返回', font=(主题.字体, 主题.字号),
                            bg='#ffffff', fg=主题.正文, relief='solid', bd=1, cursor='hand2',
                            command=lambda: self.clear_files(), padx=P(16))
    # btn_返回 初始不 pack，识别完成后 pack

    self._顶栏状态 = tk.Label(self._顶栏, text='', bg=主题.面, fg=主题.次要,
                              font=(主题.字体, 主题.字号小), anchor='w')
    self._顶栏状态.pack(side='left', fill='x', expand=True, padx=(0, P(14)))
    self._刷纸chip()


def goto(self, 页名):
    if 页名 not in self.页:
        return
    self._当前页 = 页名
    for 页 in self.页.values():
        页.pack_forget()
    self.页[页名].pack(fill='both', expand=True)
    self._导航刷新(页名)
    标题 = {键: 名 for 键, 名, _图标 in App.导航}
    self.lbl_页标题.configure(text=标题.get(页名, '打单助手'))
    self._顶栏状态.configure(text='' if 页名 != 'scan' else self._顶栏状态.cget('text'))


def _build_customer_box(self, parent):
    pad = tk.Frame(parent, bg=主题.面)
    pad.pack(fill='both', expand=True, padx=P(12), pady=(P(4), P(4)))
    分区标题(pad, '客户', '选错客户会套错规则').pack(fill='x')
    self._客户卡 = pad
    self._refresh_customers()
    self._客户提示框 = tk.Frame(pad, bg=主题.面淡, highlightthickness=1,
                               highlightbackground=主题.边框)
    self._客户提示框.pack(fill='both', expand=True, pady=(P(4), 0))
    self.lbl_customer_hint = tk.Label(self._客户提示框, text='', bg=主题.面淡,
                                      fg=主题.次要, font=(主题.字体, 主题.字号小),
                                      justify='left', anchor='nw', wraplength=P(190),
                                      padx=P(8), pady=P(2))
    self.lbl_customer_hint.pack(fill='both', expand=True)
    self._on_customer_change()


def _refresh_customers(self):
    客户们 = CP.list_customers()
    self._客户列表 = ttk.Combobox(self._客户卡, textvariable=self.var_customer,
                                  values=客户们, state='readonly')
    self._客户列表.pack(fill='x', pady=(P(6), P(12)))
    self._客户列表.bind('<<ComboboxSelected>>', self._on_customer_change)
    if 客户们 and not self.var_customer.get():
        self.var_customer.set(客户们[0])


def _on_customer_change(self, e=None):
    if getattr(self, '_编辑框', None) is not None and not self._commit_editor():
        self.var_customer.set(getattr(self, '_格式客户', self.var_customer.get()))
        return
    客户 = str(self.var_customer.get() or '').strip()
    self._格式客户 = 客户
    档案 = CP.get_profile(客户)
    if hasattr(self, '_客户chip'):
        self._客户chip.configure(text='当前客户  ' + (客户 or '未选择'))
    if hasattr(self, '_侧栏客户'):
        self._侧栏客户.configure(text=客户 or '未选择')
    if hasattr(self, 'lbl_customer_hint'):
        编码 = '显示客户编码' if 档案.get('show_code', True) else '隐藏客户编码'
        self.lbl_customer_hint.configure(text=f'【{客户 or "未选择"}】\n{编码}；仅影响输出格式，识别只看图片。')
    if hasattr(self, 'tree'):
        选中 = self.tree.selection()
        self._redraw_records()
        for 行 in 选中:
            if self.tree.exists(行):
                self.tree.selection_add(行)
        self.update_preview()


def _build_log(self, parent):
    self._日志外 = tk.Frame(parent, bg=主题.底色)
    self._日志外.pack(side='bottom', fill='x', padx=P(16), pady=(0, P(6)))
    self._日志状态_图标 = '▾'
    self._日志状态_文字 = ''
    self._日志头 = tk.Label(self._日志外, text='运行日志（看识别过程 / 报错）  ▾',
                            bg=主题.底色, fg=主题.次要, cursor='hand2',
                            font=(主题.字体, 主题.字号小), anchor='w')
    self._日志头.pack(fill='x')
    self._日志头.bind('<Button-1>', lambda e: self.toggle_log())
    self._日志文本 = tk.Text(self._日志外, height=6, bg=主题.面, fg=主题.次要,
                            relief='flat', highlightthickness=1,
                            highlightbackground=主题.边框,
                            font=(主题.字体, 主题.字号小), state='disabled')


def _update_log_header(self):
    状态 = f'  |  {self._日志状态_文字}' if self._日志状态_文字 else ''
    self._日志头.configure(text=f'运行日志（看识别过程 / 报错）  {self._日志状态_图标}{状态}')

def toggle_log(self):
    if self._日志文本.winfo_manager():
        self._日志文本.pack_forget()
        self._日志状态_图标 = '▾'
    else:
        self._日志文本.pack(fill='x', pady=(P(4), 0))
        self._日志状态_图标 = '▴'
    self._update_log_header()


_原_set_status = App.set_status

def set_status(self, text, 色=None):
    _原_set_status(self, text, 色)
    try:
        self._顶栏状态.configure(text=text, fg=色 or 主题.次要)
        self._日志状态_文字 = text
        self._update_log_header()
    except Exception:
        pass


_原_log = App.log

def log(self, msg):
    _原_log(self, msg)
    def 写入():
        try:
            self._日志文本.configure(state='normal')
            import time
            时间 = time.strftime('%H:%M:%S')
            self._日志文本.insert('end', f'[{时间}] {msg}\n')
            self._日志文本.see('end')
            self._日志文本.configure(state='disabled')
        except Exception:
            pass
    try:
        self.root.after(0, 写入)
    except Exception:
        pass


_原_debug_log = getattr(App, 'debug_log', None)
def debug_log(self, msg):
    if _原_debug_log:
        _原_debug_log(self, msg)
    if self._日志文本.winfo_manager():
        # 如果面板展开了，并且在debug模式，就把debug信息也写到界面面板里
        import app_settings as 设置
        if 设置.取('系统.运行模式') == 'debug':
            def 写入():
                try:
                    self._日志文本.configure(state='normal')
                    import time
                    时间 = time.strftime('%H:%M:%S')
                    self._日志文本.insert('end', f'[{时间}] [DEBUG] {msg}\n')
                    self._日志文本.see('end')
                    self._日志文本.configure(state='disabled')
                except Exception:
                    pass
            try:
                self.root.after(0, 写入)
            except Exception:
                pass


App._build_ui = _build_ui
App._build_sidebar = _build_sidebar
App._build_topbar = _build_topbar
App._build_customer_box = _build_customer_box
App._refresh_customers = _refresh_customers
App._on_customer_change = _on_customer_change
App._build_log = _build_log
App.toggle_log = toggle_log
App._导航刷新 = _导航刷新
App._导航悬停 = _导航悬停
App._侧边收起 = _侧边收起
App.goto = goto
App.set_status = set_status
App.log = log
App.debug_log = debug_log
App._update_log_header = _update_log_header


# ---------- 识单页：上方收单区 + 下方清单 ----------
def _build_page_scan(self, parent):
    page = tk.Frame(parent, bg=主题.底色)
    self._识单页 = page
    self._上 = 上 = tk.Frame(page, bg=主题.底色)
    self._收单区 = 上
    上.pack(fill='x')

    左 = tk.Frame(上, bg=主题.面, width=P(190), highlightthickness=1, highlightbackground=主题.边框)
    左.pack(side='left', fill='y', padx=(P(16), P(0)), pady=(P(10), P(8)))
    左.pack_propagate(False)
    self._build_customer_box(左)

    右 = tk.Frame(上, bg=主题.面, highlightthickness=1, highlightbackground=主题.边框)
    右.pack(side='left', fill='both', expand=True, padx=(P(12), P(16)), pady=(P(10), P(8)))
    self._build_files_box(右)

    self._build_checklist(page)

    # 收起后露出来的一条窄条：不占地方，但一眼能看见「图还在、点这里展开」。
    self._上收起条 = tk.Frame(page, bg=主题.面, height=P(32), highlightthickness=1, highlightbackground=主题.边框)
    self._上收起条.pack_propagate(False)
    self.lbl_收起 = tk.Label(self._上收起条, text='图片区已收起', bg=主题.面, fg=主题.次要, font=(主题.字体, 主题.字号小))
    self.lbl_收起.pack(side='left', padx=P(12))
    self.btn_展开 = tk.Label(self._上收起条, text='展开图片区 ▾', bg=主题.面, fg=主题.主色, font=(主题.字体, 主题.字号小, 'bold'), cursor='hand2')
    self.btn_展开.pack(side='right', padx=P(12))
    self.btn_展开.bind('<Button-1>', lambda e: self._收图片区(False))

    self._上收起 = False
    self._自适应收单区()
    self._刷新收起条()
    return page


def _收图片区(self, 收=True):
    '''把上面的「客户 + 单据图片」整块收起来 / 放下来。

    收起时清单能多吃约 260px（150% 下差不多 4~5 行），核对大批量时用。
    '''
    if not hasattr(self, '_上'):
        return
    收 = bool(收)
    if 收 == getattr(self, '_上收起', False):
        return
    try:
        if 收:
            self._上.pack_forget()
            self._上收起条.pack(fill='x', before=self._清单外, padx=(P(16), P(16)), pady=(P(8), P(0)))
        else:
            self._上收起条.pack_forget()
            self._上.pack(fill='x', before=self._清单外)
        self._上收起 = 收
        self._刷新收起条()
        self.root.update_idletasks()
    except Exception:
        pass


def _刷新收起条(self):
    try:
        n = len(getattr(self, 'files', []))
        if hasattr(self, 'lbl_收起'):
            self.lbl_收起.configure(text=f'图片区已收起（{n} 张图）' if n else '图片区已收起')
        if hasattr(self, 'btn_放大'):
            self.btn_放大.configure(text='还原布局 ▾' if self._上收起 else '放大清单 ⤢')
    except Exception:
        pass


# ---------- 单据图片卡：压缩到 1~2 行，按钮和「开始识别」并在一条 ----------
def _build_files_box(self, parent):
    pad = tk.Frame(parent, bg=主题.面)
    pad.pack(fill='both', expand=True, padx=P(12), pady=P(4))

    头 = tk.Frame(pad, bg=主题.面)
    头.pack(fill='x')
    self.lbl_files = tk.Label(头, text='还没有加图片', bg=主题.面, fg=主题.次要,
                              font=(主题.字体, 主题.字号小), anchor='e')
    self.lbl_files.pack(side='right')
    self.lbl_dnd = tk.Label(头, text='png / jpg / bmp / webp / tif', bg=主题.面,
                            fg=主题.淡, font=(主题.字体, 主题.字号小))
    self.lbl_dnd.pack(side='right', padx=P(8))
    分区标题(头, '单据图片', '拖进来 / 双击某张只看它').pack(side='left')

    钮 = tk.Frame(pad, bg=主题.面)
    钮.pack(fill='x', pady=(P(1), P(1)))
    self.btn_scan = 主按钮(钮, '开始识别', self.start_scan)
    self.btn_scan.configure(pady=P(1), font=(主题.字体, 主题.字号, 'bold'))
    self.btn_scan.pack(side='right')
    self.var_effort = tk.BooleanVar(value=bool(设置.取('识别.仔细模式默认', False)))
    # Windows 的 ttk 主题可能把选中标记画成 X；经典 Tk 复选框固定显示勾号。
    self.chk_effort = tk.Checkbutton(
        钮, text='仔细模式', variable=self.var_effort, indicatoron=True,
        bg=主题.面, fg=主题.正文, activebackground=主题.面,
        activeforeground=主题.正文, selectcolor=主题.面,
        font=(主题.字体, 主题.字号), cursor='hand2',
        highlightthickness=0, bd=0)
    self.chk_effort.pack(side='right', padx=(P(6), P(8)))
    气泡提示(self.chk_effort, '手写单请勾上：同一张图识别两遍对比，慢一点但更准')
    for 文字, 命令, 危险 in (
            ('选择图片…', self.add_files, False),
            ('读客户文件夹', self.load_from_folder, False),
            ('移除选中', self._remove_selected_files, False),
            ('清空', self.clear_files, True)):
        b = 次按钮(钮, 文字, 命令, 危险=危险)
        b.configure(padx=P(5), pady=P(2), font=(主题.字体, 主题.字号小))
        b.pack(side='left', padx=(0, P(4)))

    中 = tk.Frame(pad, bg=主题.面)
    中.pack(fill='both', expand=True)
    列表框 = tk.Frame(中, bg=主题.面)
    列表框.pack(side='left', fill='both', expand=True)
    self.lst_files = ttk.Treeview(列表框, columns=('no', 'name', 'out'),
                                  show='headings', height=1, selectmode='extended')
    self.lst_files.heading('no', text='#')
    self.lst_files.heading('name', text='图片文件')
    self.lst_files.heading('out', text='出了几条')
    self.lst_files.column('no', width=P(32), anchor='center', stretch=False)
    self.lst_files.column('name', width=P(240), anchor='w')
    self.lst_files.column('out', width=P(64), anchor='center', stretch=False)
    vs = ttk.Scrollbar(列表框, orient='vertical', command=self.lst_files.yview)
    self.lst_files.configure(yscrollcommand=vs.set)
    self.lst_files.pack(side='left', fill='both', expand=True)
    vs.pack(side='left', fill='y')
    self.lst_files.bind('<<TreeviewSelect>>', self._show_file_preview)
    self.lst_files.bind('<Double-1>', self._按图片筛选)

    缩 = tk.Frame(中, bg=主题.面淡, highlightthickness=1,
                  highlightbackground=主题.边框, width=P(90), height=P(60))
    缩.pack(side='left', fill='y', padx=(P(8), P(0)))
    缩.pack_propagate(False)
    self.canvas_file = tk.Canvas(缩, width=P(88), height=P(58), bg=主题.面淡,
                                 highlightthickness=0)
    self.canvas_file.pack(fill='both', expand=True)
    self.canvas_file.create_text(P(44), P(29), text='选中看缩略图', fill=主题.淡,
                                 font=(主题.字体, 主题.字号小))


def _自适应收单区(self):
    '''图片列表最多 2 行：多出来的在列表里滚，不往上顶清单。'''
    框 = getattr(self, 'lst_files', None)
    if 框 is None:
        return
    try:
        框.configure(height=max(1, min(len(self.files), 2)))
        return
    except Exception:
        return


_原_redraw_files = App._redraw_files
def _redraw_files(self):
    _原_redraw_files(self)
    try:
        n = len(self.files)
        self.lbl_files.configure(text=(f'已加 {n} 张' if n else '还没有加图片'), fg=主题.正文 if n else 主题.次要)
        self._刷新收起条()
    except Exception:
        pass
App._redraw_files = _redraw_files


# ---------- 核对清单：去掉一行长说明，状态和筛选各压一行 ----------
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
    self.btn_print.configure(pady=P(1), font=(主题.字体, 主题.字号, 'bold'))
    self.btn_print.pack(side='right')
    self.btn_undo = 次按钮(条, '撤销修改', self.undo_edits)
    self.btn_undo.pack(side='right', padx=P(6))
    self.btn_del = 次按钮(条, '删除这一行', self.delete_selected, 危险=True)
    self.btn_del.pack(side='right', padx=(P(0), P(6)))
    self.btn_re = 次按钮(条, '重新识别', self.start_scan)
    self.btn_re.pack(side='right', padx=(P(0), P(6)))
    self.btn_preview = 次按钮(条, '隐藏预览', self.toggle_preview)
    self.btn_preview.pack(side='right', padx=(P(0), P(6)))

    第二 = tk.Frame(盒, bg=主题.面)
    第二.pack(fill='x', padx=P(14), pady=(P(2), P(0)))
    self.lbl_check_status = tk.Label(第二, text='', bg=主题.面, fg=主题.次要, font=(主题.字体, 主题.字号小), anchor='w')
    self.lbl_check_status.pack(side='left', fill='x', expand=True)
    self.btn_放大 = tk.Label(第二, text='放大清单 ⤢', bg=主题.面, fg=主题.主色, font=(主题.字体, 主题.字号小, 'bold'), cursor='hand2')
    self.btn_放大.pack(side='right')
    self.btn_放大.bind('<Button-1>', lambda e: self._收图片区(not self._上收起))

    # 补丁 II 的两个入口：默认不挂出来，由 `_刷新召回条` 按状态显隐。
    #   · 「重打这一张」—— 选中某一行时才出现（用清单里现在的值，单打一张）
    #   · 「召回上一批」—— 清单空、但刚打完一批时才出现（改完可以只重打其中一张）
    self.lbl_重打 = tk.Label(第二, text='重打这一张 ⏎', bg=主题.面, fg=主题.成功,
                             font=(主题.字体, 主题.字号小, 'bold'), cursor='hand2')
    self.lbl_重打.bind('<Button-1>', lambda e: self._重打这一行())
    self.lbl_召回 = tk.Label(第二, text='召回上一批', bg=主题.面, fg=主题.危险,
                             font=(主题.字体, 主题.字号小, 'bold'), cursor='hand2')
    self.lbl_召回.bind('<Button-1>', lambda e: (self._召回上一批(), self._刷新召回条()))

    筛 = tk.Frame(盒, bg=主题.面)
    筛.pack(fill='x', padx=P(14), pady=(P(2), P(0)))
    tk.Label(筛, text='显示：', bg=主题.面, fg=主题.次要, font=(主题.字体, 主题.字号小)).pack(side='left')
    self._筛按钮 = {}
    for 键 in ('全部', '要核对', '已修改', '已打印'):
        b = tk.Label(筛, text='', bg=主题.面淡, fg=主题.次要, cursor='hand2', font=(主题.字体, 主题.字号小), padx=P(10), pady=P(1))
        b.pack(side='left', padx=(P(0), P(6)))
        b.bind('<Button-1>', (lambda e, k=键: self._设筛选(k)))
        self._筛按钮[键] = b
    self.lbl_图片筛选 = tk.Label(筛, text='', cursor='hand2', font=(主题.字体, 主题.字号小), padx=P(10), pady=P(1))
    self.lbl_图片筛选.bind('<Button-1>', self._取消图片筛选)

    self.pane = tk.PanedWindow(盒, orient='horizontal', bg=主题.边框, sashwidth=4, bd=P(0), relief='flat')
    self.pane.pack(fill='both', expand=True, padx=P(14), pady=(P(6), P(10)))
    左 = tk.Frame(self.pane, bg=主题.面)
    self.pane.add(左, minsize=P(360))
    cols = ('no', 'state', 'category', 'code', 'weight', 'unit', 'remark')
    self.tree = ttk.Treeview(左, columns=cols, show='headings', height=12, selectmode='browse')
    for cid, title, w, anchor, stretch in (('no', '#', P(28), 'center', False), ('state', '状态', P(60), 'center', False), ('category', '品类', P(120), 'w', True), ('code', '客户编码', P(76), 'center', False), ('weight', '斤数', P(52), 'center', False), ('unit', '单位', P(44), 'center', False), ('remark', '备注', P(80), 'w', True)):
        self.tree.heading(cid, text=title)
        self.tree.column(cid, width=w, anchor=anchor, stretch=stretch)
    self.tree.tag_configure('斤数可疑', background=主题.警告淡)
    self.tree.tag_configure('认字可疑', background=主题.警告淡)
    self.tree.tag_configure('已修改', background=主题.主色淡)
    self.tree.tag_configure('已打印', background=主题.成功淡)
    vs = ttk.Scrollbar(左, orient='vertical', command=self._tree_yview)
    hs = ttk.Scrollbar(左, orient='horizontal', command=self.tree.xview)
    self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
    self.tree.grid(row=0, column=0, sticky='nsew')
    vs.grid(row=0, column=1, sticky='ns')
    hs.grid(row=1, column=0, sticky='ew')
    左.rowconfigure(0, weight=1)
    左.columnconfigure(0, weight=1)
    self.tree.bind('<MouseWheel>', self._tree_wheel)
    self.tree.bind('<Double-1>', self._on_double_click)
    self.tree.bind('<Delete>', (lambda e: self.delete_selected()))
    self.tree.bind('<Return>', (lambda e: self.open_editor_for_selection()))
    self.tree.bind('<F2>', (lambda e: self.open_editor_for_selection()))
    self.tree.bind('<<TreeviewSelect>>', (lambda e: self.update_preview()))
    # 补丁 II：选中哪一行，决定「重打这一张」这个入口要不要露出来
    self.tree.bind('<<TreeviewSelect>>', (lambda e: self._刷新召回条()), add='+')
    self.tree.bind('<Button-3>', self._popup_menu)
    self.root.bind('<Control-z>', self._快捷键撤销)
    self.root.bind('<Control-Z>', self._快捷键撤销)
    self.root.bind('<Control-b>', (lambda e: self._切换侧边()))
    self.root.bind('<Control-B>', (lambda e: self._切换侧边()))
    self.tree.bind('<Configure>', (lambda e: self._commit_editor()))

    self.右键菜单 = tk.Menu(self.tree, tearoff=0, font=(主题.字体, 主题.字号小))
    self.右键菜单.add_command(label='改这一格（F2）', command=self.open_editor_for_selection)
    self.右键菜单.add_command(label='看这张标签的预览', command=self.update_preview)
    # 补丁 II：用**清单里现在的值**重打这一张（不经过打印历史里的旧值）
    self.右键菜单.add_command(label='重打这一张（用现在的值）', command=self._重打这一行)
    self.右键菜单.add_separator()
    self.右键菜单.add_command(label='删除这一行', command=self.delete_selected)
    self.右键菜单.add_command(label='撤销全部修改', command=self.undo_edits)

    self.preview_panel = tk.Frame(self.pane, bg=主题.面)
    tk.Label(self.preview_panel, text='标签预览（实际纸张 40×30mm）', bg=主题.面,
             fg=主题.次要, font=(主题.字体, 主题.字号小)).pack(anchor='w', pady=(P(2), P(6)))
    self.canvas = tk.Canvas(self.preview_panel, width=P(176), height=P(132), bg='#ffffff',
                            highlightthickness=1, highlightbackground=主题.边框)
    self.canvas.pack(fill='x')
    self.canvas.bind('<Configure>', lambda event: self.update_preview())
    # 说明文字固定单行：wraplength=0 表示不换行。
    # 原来写死 wraplength=P(176)，面板早就被拉宽到 1000+，文字还是按 176 折行，
    # 一句话折成三行、四行，白吃掉 4~5 行高度。这里改成不折行，
    # 内容太长时由 _压成一行() 按实际可用宽度截断，不再靠折行。
    self.lbl_preview = tk.Label(self.preview_panel, text='点一行看它的标签', bg=主题.面,
                                fg=主题.次要, font=(主题.字体, 主题.字号小), justify='left',
                                wraplength=0, anchor='w')
    self.lbl_preview.pack(fill='x', pady=(P(4), P(0)))

    # 增加原始图片缩略图显示
    self.lbl_orig_title = tk.Label(self.preview_panel, bg=主题.面, fg=主题.次要,
                                   font=(主题.字体, 主题.字号小), justify='left',
                                   wraplength=0, anchor='w')
    self.lbl_orig_title.configure(text='原图缩略图（滚轮缩放 · 左键拖动 · 双击复位）')
    self.lbl_orig_title.pack(anchor='w', pady=(P(6), P(2)))
    # 原图画布：给出请求高度 + expand，父面板多出来的高度全部给它。
    self.canvas_orig = tk.Canvas(self.preview_panel, bg='#ffffff', height=P(260),
                                 highlightthickness=1, highlightbackground=主题.边框)
    self.canvas_orig.pack(fill='both', expand=True)
    self.canvas_orig.bind('<Configure>', lambda event: self.update_preview())

    if self.preview_on:
        self.pane.add(self.preview_panel, minsize=P(280))
        self.btn_preview.configure(text='隐藏预览')
    else:
        self.btn_preview.configure(text='显示预览')
    self._刷新筛选按钮()


def _刷新筛选按钮(self):
    if not hasattr(self, '_筛按钮'):
        return
    记录 = list(getattr(self, 'records', []) or [])
    数量 = {
        '全部': len(记录),
        '要核对': sum(self._需要核对(r) for r in 记录),
        '已修改': sum(bool(r.get('_user_edited')) for r in 记录),
        '已打印': sum(bool(r.get('_printed')) for r in 记录),
    }
    名称 = {'全部': '全部', '要核对': '只看要核对的',
            '已修改': '只看改过的', '已打印': '只看打过的'}
    当前 = getattr(self, '_筛选', '全部')
    for 键, b in self._筛按钮.items():
        选中 = 键 == 当前
        b.configure(text=f'{名称[键]}（{数量[键]}）',
                    bg=主题.主色 if 选中 else 主题.面淡,
                    fg='#ffffff' if 选中 else 主题.次要)


_原_redraw_records = App._redraw_records

def _redraw_records(self):
    _原_redraw_records(self)
    self._刷新筛选按钮()


# 把新方法挂上去（只有这几处）
App._build_page_scan = _build_page_scan
App._build_files_box = _build_files_box
App._自适应收单区 = _自适应收单区
App._build_checklist = _build_checklist
App._收图片区 = _收图片区
App._刷新收起条 = _刷新收起条
App._刷新筛选按钮 = _刷新筛选按钮
App._redraw_records = _redraw_records


# ---------- 窗口标题：和 6.1 保持一致 ----------
_原_App_init = App.__init__

def _App_init(self, root):
    _原_App_init(self, root)
    try:
        self.root.title('打单助手 v6.1')
    except Exception:
        pass
App.__init__ = _App_init


# ---------- 自动收起 / 自动放回 ----------
# 识别成功后，图片区自动收起：这时候清单才是要盯的地方。
# 清空（打完了 / 手动清空）或又拖进新图时，自动放回来，不耽误下一批。
_原_on_scan_done = App.on_scan_done

def on_scan_done(self, result):
    _原_on_scan_done(self, result)
    try:
        if self.records:
            self._收图片区(True)
            if hasattr(self, 'btn_返回'):
                self.btn_返回.pack(side='right', padx=P(16), pady=P(12))
    except Exception:
        pass
App.on_scan_done = on_scan_done


_原_清空列表 = App._清空列表

def _清空列表(self, 清图片=True):
    _原_清空列表(self, 清图片)
    try:
        if not self.records:
            self._收图片区(False)
            if hasattr(self, 'btn_返回'):
                self.btn_返回.pack_forget()
    except Exception:
        pass
App._清空列表 = _清空列表


_原_add = App._add

def _add(self, paths):
    _原_add(self, paths)
    try:
        if self.files:
            self._收图片区(False)
    except Exception:
        pass
App._add = _add


# ---------- 缩略图按卡片实际大小缩放（100% / 150% 都不会被裁掉） ----------
def _show_file_preview(self, event=None):
    sel = self.lst_files.selection()
    self.canvas_file.delete('all')
    self._文件缩略图 = None
    w = self.canvas_file.winfo_width()
    h = self.canvas_file.winfo_height()
    if w <= 1:
        w = P(108)
    if h <= 1:
        h = P(74)
    if not sel or not _HAS_PIL:
        self.canvas_file.create_text(w // 2, h // 2, text='选中看缩略图', fill=主题.淡, font=(主题.字体, 主题.字号小))
        return
    try:
        p = self.files[int(sel[0])]
    except (ValueError, IndexError):
        return
    try:
        im = Image.open(p)
        im.thumbnail((max(20, w - 6), max(20, h - 6)), Image.LANCZOS)
        self._文件缩略图 = ImageTk.PhotoImage(im.convert('RGB'))
        self.canvas_file.create_image(w // 2, h // 2, image=self._文件缩略图)
    except Exception as e:
        self.canvas_file.create_text(w // 2, h // 2, text='读不出这张图', fill=主题.危险, font=(主题.字体, 主题.字号小))
        self.log(f'⚠ 缩略图读不出来：{e}')
App._show_file_preview = _show_file_preview


# ---------- 状态行：去掉和标题重复的「双击单元格就能改」 ----------
_原_refresh_checklist_status = App._refresh_checklist_status

def _refresh_checklist_status(self):
    _原_refresh_checklist_status(self)
    try:
        文字 = str(self.lbl_check_status.cget('text'))
        文字 = 文字.replace('　|　双击单元格就能改', '').replace('双击单元格就能改', '').strip('　| ')
        self.lbl_check_status.configure(text=文字)
    except Exception:
        pass
App._refresh_checklist_status = _refresh_checklist_status


# ===========================================================================
# 补丁 II（2026-10-19）——「我改过的值，一定有路变成纸」
#
# 为什么加这一节
# ------------
# 2026-10-18 用户报：清单里有一行品类被打成了「去皮干葱头」（周围全是蒜米），
# 他在程序里改了，打出来**还是**「去皮干葱头」。
#
# 查下来（离线复现 + 打单记录反查）不是「编辑写不进记录」——`_提交编辑器`
# 写完、`_print_task` 现读现渲染，这条链路是通的。问题出在**两头都没有护栏**：
#   ① `_提交编辑器` 在 `新值 == 旧值` 时是**完全静默**收掉的（打单助手.py:3675）。
#      中文输入法没把字送进输入框、双击落到了相邻行 —— 用户完全看不出来，
#      以为改上了，纸上当然还是旧值。
#   ② 打完一批清单会被自动收掉（v5 的防重打设计）。等发现某张打错了，
#      想重印只剩【打印历史 → 重打选中的】这一条，而它用的是 `PH.转记录()`：
#      **重打的永远是「上次打出去的那张」，永远不会跟着清单里的修改走。**
#      于是"改完再重打"必然打回旧值。
#
# 这一节只做三件事，全部是**包一层/新增方法**，一行业务逻辑都没有复制：
#   ① 改了一格但字没变 → 状态栏明说一句（不再静默）
#   ② 清单里加「重打这一张」→ 用**清单现在的值**单打一张，不走打印历史
#   ③ 一批打完自动收清单之前，留一份「上一批」→ 可召回、可改、可单张重打
# ===========================================================================


# ---------- ① 改了一格但字没变，要说出来 ----------
# 不改原函数的逻辑，只在外层观察「提交完之后这一格到底变没变」。
_原_commit_editor = App._commit_editor


def _commit_editor(self, event=None):
    """包一层 `_commit_editor`：提交后发现这一格一个字都没变，就明说一句。

    ⚠️ 判据是「填进去的字 == 提交**之前**那一格的值」——不能拿提交之后的比：
       提交成功之后这两个值当然相等，那就变成每次改对了也报「没变」。
    ⚠️ 只在**按回车 / 点别处**这类带 event 的提交上提示；
       `Tab` 跳格（`_跳格` 里不带 event 地调）不提示 —— 逐格扫一遍不该刷屏。
    """
    框 = getattr(self, '_编辑框', None)
    位 = getattr(self, '_编辑位置', None)
    填的 = None
    if 框 is not None:
        try:
            填的 = str(框.get()).strip()
        except Exception:                      # noqa: BLE001
            填的 = None

    旧值 = None
    列 = ''
    if 位:
        try:
            idx0, col0 = 位[0], 位[1]
            field0 = self.可编辑列.get(col0)
            列 = self.列名.get(col0, col0)
            if field0 and 0 <= idx0 < len(self.records):
                旧值 = str(self.records[idx0].get(field0, ''))
        except Exception:                      # noqa: BLE001
            旧值 = None

    ok = _原_commit_editor(self, event)

    try:
        if ok and event is not None and 填的 is not None \
                and 旧值 is not None and 填的 == 旧值:
            idx, _col = 位[0], 位[1]
            self.set_status(
                f'第 {idx + 1} 行的{列}没变，还是「{旧值}」—— 这一格没有改动',
                主题.警告)
            self.log(f'ℹ 第 {idx + 1} 行「{列}」提交的内容和原来一模一样（「{旧值}」）：'
                     f'输入法/双击位置可能没落上，纸上的值不会变')
    except Exception:                          # noqa: BLE001
        pass
    return ok


App._commit_editor = _commit_editor


# ---------- ② 用清单现在的值，单打这一张 ----------
def _重打这一行(self):
    """把清单里选中的那一行**单独再打一张**，用的是它现在的值。

    为什么不用【打印历史 → 重打】：那边存的是「打出去那一刻的值」，
    改完再去重打，打回来的还是旧标签（这正是 2026-10-18 那次"改了没用"的样子）。
    """
    if getattr(self, 'busy', False):
        return
    if self._编辑框 is not None and not self._commit_editor():
        return
    选 = self.tree.selection()
    if not 选:
        messagebox.showinfo('没有选中', '先在清单里点一下要重打的那一行，再点这里。')
        return
    idx = self._row_to_idx(选[0])
    if not (0 <= idx < len(self.records)):
        return
    r = self.records[idx]
    概要 = (f"{r.get('category', '')}　{r.get('customer_code', '')}　"
            f"{self._rec_weight_text(r)}{r.get('unit', '斤')} {r.get('remark', '')}").strip()
    改过 = '（你改过的）' if r.get('_user_edited') else ''
    if not messagebox.askyesno(
            '重打这一张',
            f"要重打第 {idx + 1} 行{改过}：\n\n  {概要}\n\n"
            "用的是**清单里现在的值**，不是上次打出去的那张。\n"
            "这会再占 1 张标签纸。\n\n确定吗？",
            default='no'):
        return
    self._task_kind = 'print'
    self.set_busy(True)
    self.set_status('正在重打这一张…', 主题.主色)
    # 重打=True → 不打客户名牌、不写批次头、打完也不会收清单
    self.worker.run(self._print_task, [r], customer=None, 重打=True)


App._重打这一行 = _重打这一行


# ---------- ③ 一批打完留一份「上一批」，可以召回 ----------
_原_这批打完清空 = App._这批打完清空


def _这批打完清空(self, 张数):
    """包一层：自动收清单**之前**先把这一批留住。

    原版收掉清单是为了防「下一次识别把打过的单子再打一遍」——那是对的，
    但收掉之后，一旦发现某张打错了，用户就再也没地方改、也没地方单张重打了。
    所以留一份在 `self._上一批` 里（同一批 dict，`_img`/`_printed` 都在）。
    """
    快照 = list(self.records)
    客户 = str(self.var_customer.get() or '')
    _原_这批打完清空(self, 张数)
    try:
        if 快照:
            self._上一批 = {'记录': 快照, '客户': 客户}
            self.log(f'↩ 这一批 {len(快照)} 张留在「召回上一批」里：'
                     f'发现哪张打错了，召回 → 选中那一行 →【重打这一张】')
    except Exception:                          # noqa: BLE001
        pass
    self._刷新召回条()


App._这批打完清空 = _这批打完清空


def _召回上一批(self):
    """把刚打完的那一批放回清单（用来改一行、再单张重打）。"""
    if getattr(self, 'busy', False):
        return
    批 = getattr(self, '_上一批', None)
    if not 批 or not 批.get('记录'):
        messagebox.showinfo('没有上一批',
                            '这一批还没打完，或者程序重开过（这个只在本次开机内有效）。')
        return
    if self.records:
        messagebox.showinfo('清单不是空的',
                            '清单里还有别的行。先【清空】，再召回上一批，'
                            '免得两批混在一起分不清哪张是哪张。')
        return
    条们 = list(批['记录'])
    if not messagebox.askyesno(
            '召回上一批',
            f"把上一批 {len(条们)} 张放回清单？\n\n"
            f"客户：{批.get('客户') or '—'}\n"
            f"这些标签**已经打出去了**，放回来是为了让你改完只重打其中一张。\n\n"
            "放回来以后：选中有问题的那一行 →【重打这一张】。\n"
            "⚠️ 别点【确认打印】——那会把这一批整批再打一遍。",
            default='no'):
        return
    self.records = 条们
    self._原始记录 = None          # 已经不是"识别出来的那批"了，撤销没意义
    self._删除记录 = []
    self._待打 = None
    self._redraw_records()
    self.set_status(f'已召回上一批 {len(条们)} 张 —— 选中要改的那一行，改完点【重打这一张】',
                    主题.警告)
    self.log(f'↩ 召回了上一批 {len(条们)} 张（客户：{批.get("客户") or "—"}）')


App._召回上一批 = _召回上一批


def _刷新召回条(self):
    """管两个入口的显隐 —— 平时一个都不出现，占地方。"""
    try:
        有条 = bool(self.tree.selection())
    except Exception:                          # noqa: BLE001
        有条 = False
    try:
        if 有条:
            if not self.lbl_重打.winfo_manager():
                self.lbl_重打.pack(side='right', padx=(P(0), P(10)))
        else:
            self.lbl_重打.pack_forget()
    except Exception:                          # noqa: BLE001
        pass

    批 = getattr(self, '_上一批', None)
    try:
        if 批 and not self.records:
            self.lbl_召回.configure(text=f'召回上一批（{len(批["记录"])} 张）')
            if not self.lbl_召回.winfo_manager():
                self.lbl_召回.pack(side='right', padx=(P(0), P(10)))
            # 清单空着的时候，状态行原本说的是「还没有识别结果」——那会让人以为
            # 刚才那批没了。换成一句能指路的话（只是文案，不影响任何逻辑）。
            if '还没有识别结果' in str(self.lbl_check_status.cget('text')):
                self.lbl_check_status.configure(
                    text=(f'这一批 {len(批["记录"])} 张已经打完收起来了 —— '
                          f'哪张打错了，点右边【召回上一批】把它放回清单'),
                    fg=主题.警告)
        else:
            self.lbl_召回.pack_forget()
    except Exception:                          # noqa: BLE001
        pass


App._刷新召回条 = _刷新召回条



# ---------- 预览说明：不管原文有几行，一律压成一行 ----------
# 背景：主程序把「文件名 | 品类 斤数 单位」和「客户编码」用 \n 拼成两行，
# 再叠加原来写死的 wraplength=P(176)，一句说明能折成三行，把原图区域挤没了。
# 这里不再改主程序文案，只在预览刷新之后把它压成一行；面板窄到放不下时，
# 只缩短开头的图片文件名（保留尾部，尾部才是区分度最高的部分），绝不折行。
def _压成一行(self, 文):
    try:
        行们 = [段.strip().replace('  |  ', ' ｜ ') for 段 in str(文).splitlines()]
        # 客户档案设了「隐藏客户编码」时，主程序仍会拼出光秃秃的「客户编码」四个字，
        # 单行里留着很碍眼，直接丢掉这一段（有值时是「客户编码 C1-10」，保留）。
        行们 = [段 for 段 in 行们 if 段 and 段 != '客户编码']
        文 = ' ｜ '.join(行们) if 行们 else ''
        if not 文:
            return 文
        宽 = 0
        for 控件 in (getattr(self, 'lbl_preview', None),
                     getattr(self, 'preview_panel', None)):
            try:
                宽 = 控件.winfo_width()
            except Exception:
                宽 = 0
            if 宽 > P(120):
                break
        可用 = max(P(120), 宽 - P(4))
        字体 = getattr(self, '_说明字体', None)
        if 字体 is None:
            from tkinter import font as _tk字体
            字体 = _tk字体.Font(font=self.lbl_preview.cget('font'))
            self._说明字体 = 字体
        if 字体.measure(文) <= 可用:
            return 文
        段们 = 文.split(' ｜ ')
        if len(段们) < 2 or '.' not in 段们[0]:
            return 文
        原名 = 段们[0]
        for 保留 in (24, 18, 14, 10, 6):
            if len(原名) <= 保留:
                break
            段们[0] = '…' + 原名[-保留:]
            if 字体.measure(' ｜ '.join(段们)) <= 可用:
                return ' ｜ '.join(段们)
        return ' ｜ '.join(段们)
    except Exception:                          # noqa: BLE001
        return str(文).replace('\n', ' ｜ ')


_原_update_preview_说明 = App.update_preview


def update_preview(self, rec=None):
    """包一层：原流程照跑，之后把说明文字压成一行（失败就保持原样）。"""
    _原_update_preview_说明(self, rec)
    try:
        if hasattr(self, 'lbl_preview'):
            self.lbl_preview.configure(text=_压成一行(self, self.lbl_preview.cget('text')))
    except Exception:                          # noqa: BLE001
        pass


App._压成一行 = _压成一行
App.update_preview = update_preview
