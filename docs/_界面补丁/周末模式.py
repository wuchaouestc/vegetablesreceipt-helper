# -*- coding: utf-8 -*-
"""周末模式（2026-10-31）

周六有些客户只出报价表、不打热敏标签，但报价助手需要当天的打单记录 TXT。
这里在左侧「功能」里加一个「周末模式」页：开启后，核对清单的【确认打印】
只把这一批追加写进当天打单记录 TXT —— 不发打印机、不占标签纸、不写打印历史。

· 只多加一条「写记录」链路：记批次 / 名牌 / 单据 的调用顺序和参数与
  原版 _print_task 完全一致，报价助手的导入规则不用改。
· 模式只在开启当天有效（记在设置里），第二天自动恢复打印，避免周一忘关。
· 不改原版任何业务代码：全部是包一层 / 新增方法。
"""
import sys

_原版 = sys.modules['打单助手_原版']
_g = _原版.__dict__
tk = _g['tk']
P = _g['P']
主题 = _g['主题']
设置 = _g['设置']
DR = _g['DR']
messagebox = _g['messagebox']
分区标题 = _g['分区标题']
说明字 = _g['说明字']
主按钮 = _g['主按钮']

_开启键 = '周末模式.开启日期'
_导航项 = ('周末', '周末模式', '末')


def _今天():
    from datetime import date
    return date.today().isoformat()


def 周末模式开():
    try:
        return str(设置.取(_开启键, '') or '').strip() == _今天()
    except Exception:                                    # noqa: BLE001
        return False


def _存开关(开):
    try:
        设置.改(_开启键, _今天() if 开 else '')
        return True
    except Exception as e:                               # noqa: BLE001
        try:
            messagebox.showerror('设置没存上', f'周末模式开关没写进设置：{e}')
        except Exception:                                # noqa: BLE001
            pass
        return False


# ---------- 侧边栏加一项 ----------
_导航 = list(App.导航)
if not any(item[0] == '周末' for item in _导航):
    _位 = next((i for i, item in enumerate(_导航) if item[0] == '设置'), len(_导航))
    _导航.insert(_位, _导航项)
    App.导航 = _导航


def _build_page_weekend(self, parent):
    page = tk.Frame(parent, bg=主题.底色)
    self._周末页 = page

    头 = tk.Frame(page, bg=主题.底色)
    头.pack(fill='x', padx=P(24), pady=(P(16), P(6)))
    tk.Label(头, text='周末模式', bg=主题.底色, fg=主题.正文,
             font=(主题.字体, 主题.字号页标题, 'bold')).pack(side='left')
    说明字(头, '不开热敏打印机，只写当天打单记录 TXT；报价助手照常导入出价格表').pack(
        side='left', padx=P(14), pady=(P(8), 0))

    卡 = tk.Frame(page, bg=主题.面, highlightthickness=1, highlightbackground=主题.边框)
    卡.pack(fill='x', padx=P(24), pady=(P(8), P(10)))
    分区标题(卡, '开关', '只在开启当天有效，第二天自动恢复打印标签').pack(
        fill='x', padx=P(16), pady=(P(12), P(2)))
    行 = tk.Frame(卡, bg=主题.面)
    行.pack(fill='x', padx=P(16), pady=(P(6), P(4)))
    self.lbl_周末状态 = tk.Label(行, text='', bg=主题.面, fg=主题.次要,
                                 font=(主题.字体, 主题.字号正文, 'bold'), anchor='w')
    self.lbl_周末状态.pack(side='left')
    self.btn_周末 = 主按钮(行, '开启周末模式', self._周末切换, 色调='成功')
    self.btn_周末.pack(side='right')
    说明字(
        卡,
        '开启后：核对清单的【确认打印】变成【只写记录】，只追加当天打单记录，不出标签纸。\n'
        '关闭后：恢复原样 —— 确认打印 = 打标签 + 写记录。\n'
        '周末模式不占标签纸、不写打印历史；需要重打标签时请先关掉它。',
    ).pack(fill='x', padx=P(16), pady=(P(4), P(14)))

    卡2 = tk.Frame(page, bg=主题.面, highlightthickness=1, highlightbackground=主题.边框)
    卡2.pack(fill='x', padx=P(24), pady=(P(0), P(12)))
    分区标题(卡2, '今天会写到哪里', '路径就是打单记录的默认目录').pack(
        fill='x', padx=P(16), pady=(P(12), P(2)))
    self.lbl_周末路径 = tk.Label(卡2, text='', bg=主题.面, fg=主题.正文,
                                 font=(主题.字体, 主题.字号小), anchor='w',
                                 justify='left', wraplength=P(760))
    self.lbl_周末路径.pack(fill='x', padx=P(16), pady=(P(4), P(14)))
    return page


def _周末刷新(self):
    开 = 周末模式开()
    try:
        self.lbl_周末状态.configure(
            text='当前：已开启 —— 确认打印只写记录，不打标签' if 开
            else '当前：未开启 —— 正常打印标签',
            fg=主题.危险 if 开 else 主题.次要)
        self.btn_周末.configure(
            text='关闭周末模式（恢复打印）' if 开 else '开启周末模式（只写记录）')
    except Exception:                                    # noqa: BLE001
        pass
    try:
        客户 = str(self.var_customer.get() or '').strip()
        路径 = DR.今日路径(客户) if 客户 else '（还没选客户）'
        self.lbl_周末路径.configure(
            text=f'客户：{客户 or "—"}\n文件：{路径}\n只追加今天的文件，不改、不删历史 TXT。')
    except Exception:                                    # noqa: BLE001
        pass
    _周末刷新chip(self)


def _周末刷新chip(self):
    """核对清单按钮旁常驻一个红字提示，防止开着周末模式忘了关。"""
    try:
        if getattr(self, '_周末chip', None) is None and hasattr(self, 'btn_print'):
            self._周末chip = tk.Label(
                self.btn_print.master, text='周末模式 · 只写记录，不打标签',
                bg=主题.面, fg=主题.危险, font=(主题.字体, 主题.字号小, 'bold'))
        chip = getattr(self, '_周末chip', None)
        if chip is None:
            return
        if 周末模式开():
            if not chip.winfo_manager():
                chip.pack(side='right', padx=P(10))
        else:
            chip.pack_forget()
    except Exception:                                    # noqa: BLE001
        pass


def _周末切换(self, 开=None):
    开 = (not 周末模式开()) if 开 is None else bool(开)
    if 开:
        if not messagebox.askyesno(
                '开启周末模式',
                '开启后，点【确认打印】不会打热敏标签，只把这一批写进当天打单记录 TXT。\n\n'
                '只在今天有效，明天自动恢复打印标签。\n\n确定开启吗？',
                default='no'):
            return
        if not _存开关(True):
            return
        self.log('🕊 周末模式已开启（今天有效）：【确认打印】只写打单记录，不打标签')
        self.set_status('周末模式已开启：只写打单记录，不打标签', 主题.成功)
    else:
        if not _存开关(False):
            return
        self.log('🖨 周末模式已关闭：恢复打印热敏标签')
        self.set_status('周末模式已关闭：恢复正常打印', 主题.主色)
    _周末刷新(self)
    try:
        self._刷打印按钮()
    except Exception:                                    # noqa: BLE001
        pass


App._build_page_weekend = _build_page_weekend
App._周末刷新 = _周末刷新
App._周末刷新chip = _周末刷新chip
App._周末切换 = _周末切换


# ---------- 页面挂进 self.页，标题和导航按「周末模式」显示 ----------
_原_build_ui = App._build_ui


def _build_ui(self):
    _原_build_ui(self)
    try:
        self.页['周末'] = self._build_page_weekend(self.内容)
    except Exception as e:                               # noqa: BLE001
        self.log(f'⚠ 周末模式页构建失败：{e}')


App._build_ui = _build_ui

_原_goto = App.goto


def goto(self, 页名):
    _原_goto(self, 页名)
    try:
        if 页名 == '周末':
            self.lbl_页标题.configure(text='周末模式')
            _周末刷新(self)
        else:
            _周末刷新chip(self)
    except Exception:                                    # noqa: BLE001
        pass


App.goto = goto

_原_刷打印按钮 = App._刷打印按钮


def _刷打印按钮(self):
    _原_刷打印按钮(self)
    try:
        if 周末模式开():
            n = len(getattr(self, 'records', None) or [])
            self.btn_print.configure(text=f'只写记录（{n} 条）' if n else '只写记录')
        _周末刷新chip(self)
    except Exception:                                    # noqa: BLE001
        pass


App._刷打印按钮 = _刷打印按钮


# ---------- 只写记录：和原版 _print_task 的 TXT 写法逐项对齐 ----------
def _周末写记录(self, recs, customer):
    """把这一批追问写进当天打单记录，返回 (条数, 路径)。

    顺序和原版一致：记批次 → 客户名牌 → 每条单据。
    图列用 ①②③… 对应这一批图片，报价助手按 10 列导入不受影响。
    """
    默认客户 = str(customer or self.var_customer.get() or '').strip()
    图们 = []
    图号 = {}
    for r in recs:
        名 = str(r.get('_src') or '')
        if 名 and 名 not in 图们:
            图们.append(名)
    序号字 = '①②③④⑤⑥⑦⑧⑨⑩'
    for i, 名 in enumerate(图们):
        图号[名] = 序号字[i] if i < 10 else f'#{i + 1}'

    if 图们:
        DR.记批次(默认客户, 图们)
    if 默认客户:
        DR.记一张(默认客户, '名牌', 结果='已发出')

    条数 = 0
    for r in recs:
        客 = str(r.get('_客户') or 默认客户)
        号 = 图号.get(str(r.get('_src') or ''), '')
        结果 = '已发出（手改）' if r.get('_user_edited') else '已发出'
        DR.记一张(客, '单据', r.get('category', ''), r.get('customer_code', ''),
                  self._rec_weight_text(r), r.get('unit', '斤'), r.get('remark', ''),
                  号, 结果)
        条数 += 1
    路径 = DR.今日路径(默认客户) if 默认客户 else ''
    return 条数, 路径


def _周末收尾(self, recs):
    """这一批写完后收清单：移走用过的图片，清空清单，但不说「已发出」。"""
    用过的 = set()
    for r in recs:
        名 = str(r.get('_src') or '')
        if 名:
            用过的.add(名)
    try:
        if 用过的:
            from pathlib import Path as _P
            self.files = [p for p in self.files if _P(p).name not in 用过的]
            self._文件指纹 = {k: v for k, v in self._文件指纹.items() if k in self.files}
        self._待打 = None
        self._清空列表(清图片=False)
        self._刷打印按钮()
        self._刷新历史()
    except Exception as e:                               # noqa: BLE001
        self.log(f'⚠ 周末模式收清单时出岔子（记录已写入，不影响报价表）：{e}')


App._周末写记录 = _周末写记录
App._周末收尾 = _周末收尾


def _周末_do_print(self):
    if getattr(self, 'busy', False):
        return
    if getattr(self, '_编辑框', None) is not None and not self._commit_editor():
        return
    recs = list(getattr(self, 'records', None) or [])
    if not recs:
        return
    customer = str(self.var_customer.get() or '').strip()
    if not customer:
        messagebox.showinfo('先选客户', '周末模式的记录要写进客户文件夹，请先选客户。')
        return
    prompt = (
        '周末模式：只写打单记录，不打印热敏标签。\n\n'
        f'客户：{customer}\n共 {len(recs)} 条\n\n'
        f'要追加到今天的打单记录里：\n{DR.今日路径(customer)}\n\n确认写入吗？'
    )
    if not messagebox.askyesno('周末模式 · 只写记录', prompt, default='yes'):
        return
    self.set_status('正在写打单记录…', 主题.主色)
    try:
        条数, 路径 = self._周末写记录(recs, customer)
    except Exception as e:                               # noqa: BLE001
        self.set_status('写打单记录失败', 主题.危险)
        self.log(f'❌ 周末模式写记录失败：{e}')
        messagebox.showerror('写打单记录失败', f'{e}')
        return
    self._周末收尾(recs)
    self.log(f'📝 周末模式：已写入 {条数} 条到 {路径}（没有打印标签）')
    self.set_status(f'✅ 周末模式：已写入打单记录 {条数} 条，没有打印标签', 主题.成功)
    messagebox.showinfo(
        '已写入打单记录',
        f'已写入 {条数} 条：\n{路径}\n\n没有打印任何标签。\n'
        '去报价助手导入当天记录，就能出价格表了。')


App._周末_do_print = _周末_do_print

_原_do_print = App.do_print


def do_print(self):
    if 周末模式开():
        return _周末_do_print(self)
    return _原_do_print(self)


App.do_print = do_print


# ---------- 启动时刷新开关状态 ----------
_原_init = App.__init__


def _App_init(self, root):
    _原_init(self, root)
    try:
        _周末刷新(self)
        self._刷打印按钮()
        self.var_customer.trace_add('write', lambda *_: _周末刷新(self))
        if 周末模式开():
            self.log('🕊 周末模式已开启（今天有效）：【确认打印】只写打单记录，不打标签')
    except Exception:                                    # noqa: BLE001
        pass


App.__init__ = _App_init


def 自检():
    """不建窗口的自检：补丁链是否挂好。"""
    断言 = [
        ('导航项', any(item[0] == '周末' for item in App.导航)),
        ('页面构建', App._build_page_weekend is _build_page_weekend),
        ('打印分流', App.do_print is do_print),
    ]
    失败 = [名 for 名, 通过 in 断言 if not 通过]
    if 失败:
        raise RuntimeError('周末模式自检失败：' + '、'.join(失败))
    return True
