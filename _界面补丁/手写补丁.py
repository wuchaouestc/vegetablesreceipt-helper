# -*- coding: utf-8 -*-
"""手写补丁 —— 阶段 4：模式切换 + 原图对照 + 自动分流（由 加载.py 在其它补丁之后加载）。

做五件事，每一件都是「包一层」，业务逻辑一行没改：

  ① 模式切换：在图片区加一个下拉框（自动判别 / 原流程 / 手写辅助 / 逐张选择）。
     默认「自动判别」——每张图先判是不是手写单据，是就走手写识别库。
  ② 识别分流：`App._scan_task` 按模式决定走原流程还是手写辅助通道。
     单张图出错只退回**这一张**的原流程，不影响其它图片。
  ③ 原图对照：清单右键加「原图对照」，点开能看到原图区域、原文、
     各个来源的候选值和建议。
  ④ 快照纪律：模式、仔细模式这些 Tk 状态一律在**主线程**读完再冻结，
     后台线程只拿不可变快照用。
  ⑤ 学习闭环：核对网页学到的词典自动接进手写通道，越用越准。

开关在 settings.json 的「识别」段：
    "手写辅助模式": "自动"        自动 / 关 / 全开 / 逐张
    "手写局部复核": true        有疑点且预算允许时裁原图复核
    "手写词典辅助": ""          词典文件路径，空 = 不启用
    "手写预算请求数": 8         每张图片允许的额外请求数
    "手写预算秒数": 120         每张图片允许的总时限
    "手写标题置信阈值": 90      标题置信度到这个值才允许改品类
    "手写复核区域数": 2         每张图片最多复核几个疑点区域
    "手写单位一致性": true      单据级单位一致性：先在本单内取单位共识，
                                再回填「读不出单位」的行；回填值一定标待核对
    "手写单位一致最低行数": 2   共识至少要几行支持才生效

所有入口都套了 try/except：补丁出错一律退回原行为，绝不让打单流程停住。
"""

import json as _json
import sys as _sys
import time as _time
from pathlib import Path as _Path

_主模块 = globals().get('主模块')
_根 = _Path(str(globals().get("HERE") or "."))
_诊断 = _根 / "手写诊断.log"

_tk = getattr(_主模块, 'tk', None)
_ttk = getattr(_主模块, 'ttk', None)
_主题 = getattr(_主模块, '主题', None)
_消息框 = getattr(_主模块, 'messagebox', None)
_P = getattr(_主模块, 'P', None) or (lambda v: v)
_气泡提示 = getattr(_主模块, '气泡提示', None)


def _记(内容):
    """追加一行诊断。写文件失败不影响任何流程。"""
    try:
        with _诊断.open('a', encoding='utf-8') as f:
            f.write('%s\t%s\n' % (_time.strftime('%m-%d %H:%M:%S'), 内容))
    except Exception:
        pass


def _识别设置():
    """读 settings.json 的「识别」段。读不到就用空字典（等于关闭）。"""
    try:
        d = _json.loads((_根 / "settings.json").read_text(encoding="utf-8"))
        return dict(d.get("识别") or {})
    except Exception:
        return {}


def _库():
    """导入手写识别库；失败返回 (None, None)，调用方退回原流程。"""
    try:
        import handwriting.modes as 模式
        import handwriting.pipeline as 流水线
        return 模式, 流水线
    except Exception as e:  # noqa: BLE001
        _记("手写识别库不可用，按原流程：%r" % (e,))
        return None, None


def _判别库():
    """导入判别器；失败返回 None（调用方退回原流程）。"""
    try:
        import handwriting.detect as 判别
        return 判别
    except Exception as e:  # noqa: BLE001
        _记("手写判别器不可用，按原流程：%r" % (e,))
        return None


# ---------------------------------------------------------------- 快照
def _快照(self):
    """主线程读 Tk 状态，冻结成不可变快照给后台线程用。"""
    模式, _ = _库()
    if 模式 is None:
        return None
    显式 = None
    try:
        映射 = {'原流程': 模式.MODE_LEGACY, '手写辅助': 模式.MODE_ASSISTED,
                '逐张选择': 模式.MODE_PER_IMAGE, '自动判别': 模式.MODE_AUTO}
        显式 = 映射.get(self.var_手写模式.get())
    except Exception:
        显式 = None
    try:
        仔细 = bool(self.var_effort.get())
    except Exception:
        仔细 = False
    配置 = 模式.resolve_mode(_识别设置(), explicit=显式, careful=仔细)
    逐张 = dict(getattr(self, '_手写逐张', {}) or {})
    return {'配置': 配置, '逐张': 逐张, '模式': 模式, '仔细': 仔细}


def _这张走哪条(self, 图片, 快照):
    """决定这张图走哪条通道。返回 ('legacy'|'assisted', 判别说明)。"""
    模式 = 快照['模式']
    走哪条 = _按模式(模式, 快照, 图片)
    if 走哪条 != 模式.MODE_AUTO:
        return 走哪条, ''
    # 自动判别：拿 OCR 块判是不是手写单据。
    判别 = _判别库()
    if 判别 is None:
        return 模式.MODE_LEGACY, '判别器不可用，按原流程'
    try:
        错误 = []
        块们 = T.tencent_ocr(图片, 错误)
        if not 块们:
            return 模式.MODE_LEGACY, '没取到 OCR 文字，按原流程'
        结果 = 判别.判别(块们)
        return (模式.MODE_ASSISTED if 结果.handwritten else 模式.MODE_LEGACY,
                判别.判别说明(结果))
    except Exception as e:  # noqa: BLE001
        _记("自动判别失败，按原流程：%r" % (e,))
        return 模式.MODE_LEGACY, '判别失败，按原流程'


def _按模式(模式, 快照, 图片):
    """按配置与逐张选择取这条图的通道。自动模式原样返回，交给判别器处理。"""
    if 快照['配置'].mode != 模式.MODE_PER_IMAGE:
        return 快照['配置'].mode
    选择 = 快照['逐张'].get(str(图片))
    if 选择 in (模式.MODE_LEGACY, 模式.MODE_ASSISTED):
        return 选择
    return 模式.MODE_LEGACY


# ---------------------------------------------------------------- 提供器
def _OCR提供(path, action='GeneralAccurateOCR', timeout=30):
    """复用现网 OCR（带完整多边形的版本），沿用同一份本地缓存。"""
    错误 = []
    try:
        blocks = T.tencent_ocr(path, 错误)
    except Exception as e:  # noqa: BLE001
        return {'status': 'error', 'error_code': type(e).__name__, 'blocks': [], 'records': [], 'notes': []}
    if not blocks:
        return {'status': 'error', 'error_code': (错误[0] if 错误 else 'empty'),
                'blocks': [], 'records': [], 'notes': []}
    return {'status': 'ok', 'blocks': blocks, 'records': [], 'notes': []}


def _模型提供(effort):
    def 提供(path, model=None, system_prompt='', timeout=120):
        档 = 'high' if (effort == 'high' or (model or '').endswith('reasoner')) else 'off'
        try:
            结果 = T.deepseek_parse(path, None, 档, system_prompt)
        except Exception as e:  # noqa: BLE001
            return {'status': 'error', 'error_code': type(e).__name__,
                    'records': [], 'notes': []}
        return {'status': 'ok' if 结果.get('records') else 'empty',
                'records': 结果.get('records', []), 'notes': 结果.get('notes', [])}
    return 提供


_复核提示 = ('只读取这张裁剪出来的局部图，按「编码:数量单位」读出每一行明细，'
              '返回 JSON：{"records":[{"customer_code":"编码","weight":"数量",'
              '"unit":"单位","category":"品类"}],"raw_text":"看到的原文"}。'
              '看不清就留空，不要猜；不要输出整单。')


def _复核提供(path, timeout=90):
    try:
        结果 = T.deepseek_parse(path, None, 'off', _复核提示)
    except Exception as e:  # noqa: BLE001
        return {'status': 'error', 'error_code': type(e).__name__, 'records': [], 'raw_text': ''}
    return {'status': 'ok', 'records': 结果.get('records', []),
            'raw_text': str(结果.get('raw_text', '')), 'notes': 结果.get('notes', [])}


def _手写识别(self, 图片, prompt, effort, 快照):
    """跑一张图的手写辅助通道。"""
    模式, 流水线 = _库()
    配置 = 快照['配置']
    from handwriting.pipeline import RecognitionRequest
    # 客户上下文：词典里按客户范围（scope）的词条要靠它命中；拿不到就传 None。
    客户 = None
    try:
        客户 = self.var_customer.get() or None
    except Exception:  # noqa: BLE001
        客户 = None
    请求 = RecognitionRequest(
        image_path=str(图片),
        doc_type='handwritten',
        action='GeneralAccurateOCR',
        model='deepseek-reasoner' if effort == 'high' else 'deepseek-chat',
        prompt=prompt,
        vocabulary_path=配置.vocabulary_path,
        customer=客户,
    )
    结果 = 流水线.assisted_recognize(
        请求, config=配置,
        ocr_provider=_OCR提供,
        model_provider=_模型提供(effort),
        review_provider=_复核提供 if 配置.review_enabled else None,
        legacy_fix=T.fix_category_boundaries,
        legacy_cross=T.cross_check,
    )
    return 结果


# ---------------------------------------------------------------- 识别分流
_原_scan_task = App._scan_task


def _scan_task(self, 客户):
    """按模式分流。原流程一行没改；手写辅助失败只退回这一张。"""
    快照 = getattr(self, '_手写快照', None)
    if not 快照:
        return _原_scan_task(self, 客户)
    self._识别警告 = []
    prompt = T.build_prompt(None)
    effort = 'high' if 快照.get('仔细') else 'off'
    records = []
    文件们 = list(self.files)
    for i, p in enumerate(文件们):
        self.worker.progress((i + 1, len(文件们)))
        名 = _Path(p).name
        self.debug_log('开始识别文件: %s' % 名)
        走哪条, 判别话 = _这张走哪条(self, p, 快照)
        走手写 = (走哪条 == 快照['模式'].MODE_ASSISTED)
        if 判别话:
            self.log('%s：%s' % (名, 判别话))
            _记('%s %s' % (名, 判别话))
        try:
            if 走手写:
                结果 = _手写识别(self, p, prompt, effort, 快照)
                recs, blocks, notes = 结果.records, 结果.blocks, 结果.notes
                self.log('%s：手写辅助｜疑点 %d 条｜预算 %s' % (
                    名, sum(1 for r in recs if r.get('_uncertain')), 结果.budget.get('requests', 0)))
            else:
                recs, blocks, notes = T.recognize_parallel(p, effort, prompt, careful=effort == 'high')
            self.debug_log('提取结果: %d 条记录，%d 个文本块' % (
                len(recs) if recs else 0, len(blocks) if blocks else 0))
            for 提示 in notes:
                self.log('%s：%s' % (名, 提示))
                if '腾讯 OCR 未返回' in 提示:
                    self._识别警告.append('%s：%s' % (名, 提示))
            if not recs:
                raise RuntimeError('AI 没有提取到有效明细，请检查客户是否选对，或换一张更清晰的图片')
            if not 走手写:
                # 原流程的老裁决照旧；手写辅助通道已经在库里做过门控。
                T.fix_category_boundaries(recs, blocks)
            覆盖提示 = T.check_coverage(blocks, recs)
            if 覆盖提示:
                self._识别警告.append('%s：%s' % (名, 覆盖提示.replace('\n', '；')))
                self.log('%s：%s' % (名, 覆盖提示.replace('\n', '；')))
            if not 走手写:
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
                if 走手写:
                    # 记下这张图走的哪条通道，界面上能看出来。
                    r['_通道'] = '手写辅助'
            records.extend(recs)
            self.log('%s：识别 %d 条，完整保留；客户仅用于输出格式' % (名, len(recs)))
        except Exception as e:
            友好 = T.说人话的报错(e)
            if 走手写:
                # 单张降级：这一张退回原流程，其余图片不受影响。
                _记('%s 手写辅助失败，这张退回原流程：%r' % (名, e))
                try:
                    recs, blocks, notes = T.recognize_parallel(p, effort, prompt, careful=effort == 'high')
                    for 提示 in notes:
                        self.log('%s：%s' % (名, 提示))
                    if recs:
                        T.fix_category_boundaries(recs, blocks)
                        T.cross_check(recs, blocks)
                        for 提示 in T.mark_uncertain(recs):
                            self.log('%s：%s' % (名, 提示))
                        for r in recs:
                            self._记录序号 += 1
                            r['_id'] = self._记录序号
                            r['_img'] = p
                            r['_src'] = 名
                        records.extend(recs)
                        self.log('%s：已退回原流程，识别 %d 条' % (名, len(recs)))
                        continue
                except Exception as e2:  # noqa: BLE001
                    _记('%s 退回原流程也失败：%r' % (名, e2))
            raise RuntimeError('第 %d 张「%s」识别失败：%s' % (i + 1, 名, 友好)) from e
    return records


App._scan_task = _scan_task


# ---------------------------------------------------------------- 模式控件
_原_build_files_box = App._build_files_box
_原_start_scan = App.start_scan


def _build_files_box(self, parent):
    _原_build_files_box(self, parent)
    try:
        行 = getattr(self, 'btn_scan', None).master
        if 行 is None:
            return
        框 = _tk.Frame(行, bg=_主题.面 if _主题 else None)
        框.pack(side='right', padx=_P(4))
        self.var_手写模式 = _tk.StringVar(value='自动判别')
        下拉 = _ttk.Combobox(框, textvariable=self.var_手写模式, state='readonly',
                             values=('自动判别', '原流程', '手写辅助', '逐张选择'), width=_P(9))
        下拉.pack(side='left')
        if _气泡提示:
            _气泡提示(下拉, '自动判别＝每张图先看是不是手写单据，是就用完整框+行列关联+保守裁决；'
                             '原流程＝现在的行为；手写辅助＝全部用手写通道；'
                             '逐张选择＝下面两个按钮逐张指定')
        按钮1 = _tk.Button(框, text='这张用辅助', command=lambda: self._手写逐张设置('手写辅助'),
                            font=(_主题.字体, _主题.字号小) if _主题 else None)
        按钮2 = _tk.Button(框, text='这张用原流程', command=lambda: self._手写逐张设置('原流程'),
                            font=(_主题.字体, _主题.字号小) if _主题 else None)
        按钮1.pack(side='left', padx=(_P(2), 0))
        按钮2.pack(side='left')
        self._手写逐张 = {}
    except Exception as e:  # noqa: BLE001
        _记('模式控件加载失败，继续用原流程：%r' % (e,))


def _手写逐张设置(self, 选择):
    """给清单里选中的图片逐张指定通道。"""
    try:
        选中 = list(self.lst_files.selection())
        if not 选中:
            if _消息框:
                _消息框.showinfo('先选图片', '先在上面的列表里选中要指定的图片')
            return
        if not hasattr(self, '_手写逐张'):
            self._手写逐张 = {}
        for iid in 选中:
            路径 = self.lst_files.item(iid, 'values')
            名 = 路径[1] if len(路径) > 1 else ''
            匹配 = [p for p in self.files if _Path(p).name == 名]
            if not 匹配:
                continue
            if 选择 == '手写辅助':
                self._手写逐张[str(匹配[0])] = 'assisted'
            else:
                self._手写逐张.pop(str(匹配[0]), None)
        self.log('逐张设置：%s %d 张' % (选择, len(选中)))
    except Exception as e:  # noqa: BLE001
        _记('逐张设置失败：%r' % (e,))


App._build_files_box = _build_files_box
App._手写逐张设置 = _手写逐张设置


def start_scan(self):
    """主线程先把 Tk 状态冻成快照，再交给后台线程。"""
    try:
        self._手写快照 = _快照(self)
    except Exception as e:  # noqa: BLE001
        _记('模式快照失败，按原流程：%r' % (e,))
        self._手写快照 = None
    return _原_start_scan(self)


App.start_scan = start_scan


# ---------------------------------------------------------------- 原图对照
def _原图对照(self):
    """清单右键 → 看这一行的原图区域、原文、候选和建议。"""
    try:
        选中 = self.tree.selection()
        if not 选中:
            return
        idx = self._row_to_idx(选中[0])
        if idx < 0:
            return
        r = self.records[idx]
        证据 = r.get('_evidence') or {}
        if not 证据:
            if _消息框:
                _消息框.showinfo('没有对照信息',
                                 '这一行是原流程识别的，没有原图对照信息。\n'
                                 '切到「手写辅助」再识别一次才会有。')
            return
        from handwriting.compare import build_comparison
        目录 = _根 / 'data' / '复核对照'
        视图 = build_comparison(r, image_path=r.get('_img'), crop_dir=目录,
                                name='row-%s' % r.get('_id'))
        _开对照窗(self, 视图)
    except Exception as e:  # noqa: BLE001
        _记('原图对照失败：%r' % (e,))
        if _消息框:
            _消息框.showerror('原图对照', '打开对照窗口失败：%r' % (e,))


def _开对照窗(self, 视图):
    窗 = _tk.Toplevel(self.root)
    窗.title('原图对照')
    窗.transient(self.root)
    面 = _主题.面 if _主题 else None
    左 = _tk.Frame(窗, bg=面)
    左.pack(side='left', fill='both', expand=True, padx=_P(8), pady=_P(8))
    右 = _tk.Frame(窗, bg=面)
    右.pack(side='left', fill='both', expand=True, padx=_P(4), pady=_P(8))
    文本 = _tk.Text(右, width=_P(56), height=_P(22), wrap='word', font=(_主题.字体, _主题.字号小) if _主题 else None)
    文本.pack(fill='both', expand=True)
    行 = []
    行.append('原图：%s' % (视图.get('image_path') or '（无）'))
    行.append('区域：%s' % (视图.get('bbox') or '（无）'))
    行.append('标题原文：%s' % (视图.get('heading_text') or '（无）'))
    行.append('行内原文：%s' % (视图.get('entry_text') or '（无）'))
    行.append('')
    行.append('当前值：%s' % '、'.join('%s=%s' % (k, v) for k, v in (视图.get('values') or {}).items()))
    行.append('')
    行.append('候选：')
    for c in 视图.get('candidates') or []:
        行.append('  · %s：%s（来源 %s%s）' % (
            c.get('label'), c.get('value'), c.get('source'),
            '，置信 %s' % c['confidence'] if c.get('confidence') is not None else ''))
    行.append('')
    行.append('建议：%s' % 视图.get('suggestion'))
    if 视图.get('reasons'):
        行.append('核对原因：%s' % '；'.join(视图['reasons']))
    if 视图.get('observations'):
        行.append('补充线索：%s' % '；'.join(视图['observations']))
    文本.insert('1.0', '\n'.join(行))
    文本.configure(state='disabled')
    裁剪 = 视图.get('crop_path')
    if 裁剪 and _Path(裁剪).is_file():
        try:
            from PIL import Image, ImageTk
            图 = Image.open(裁剪)
            宽 = _P(300)
            比例 = 宽 / max(1, 图.width)
            图 = 图.resize((宽, max(1, int(图.height * 比例))))
            照片 = ImageTk.PhotoImage(图)
            画 = _tk.Label(左, image=照片, bg=面)
            画.image = 照片
            画.pack()
            _tk.Label(左, text='上面是原图里这一行的位置', bg=面,
                      fg=_主题.次要 if _主题 else None).pack()
        except Exception as e:  # noqa: BLE001
            _记('对照窗口显示裁剪图失败：%r' % (e,))
    else:
        _tk.Label(左, text='（没有裁剪图）', bg=面).pack()


App._原图对照 = _原图对照


def _装右键菜单(self):
    """给清单的右键菜单加一项「原图对照」。"""
    try:
        菜单 = getattr(self, '右键菜单', None)
        if 菜单 is None:
            return
        菜单.add_separator()
        菜单.add_command(label='原图对照（看原图/原文/候选）', command=self._原图对照)
    except Exception as e:  # noqa: BLE001
        _记('右键菜单加项失败：%r' % (e,))


_原_build_checklist = App._build_checklist


def _build_checklist(self, parent):
    _原_build_checklist(self, parent)
    try:
        _装右键菜单(self)
    except Exception as e:  # noqa: BLE001
        _记('右键菜单装载失败：%r' % (e,))


App._build_checklist = _build_checklist


# ---------------------------------------------------------------- 人工确认后学习
# 用户在核对清单里改过 / 点过确认的行，学进词典和示例库。
# 这样正式流程里每一次人工核对都在让软件变聪明，不需要额外开网页。
#
# 决定因素是「人工确认状态 + 实际识别通道」，不是图片是否手写，也不是是否打印：
#   · 手写辅助通道的行带 _evidence，能比对出「读错 → 改对」的别名；
#   · 普通（非手写）识别结果没有 _evidence，但人工改过/确认过的值本身就是
#     高准确率的参考数据，同样收进词典（learn_from_confirmed_records 内部兜底）。
# 触发点有两个：编辑确认（_commit_editor）即时学习；打印完成（on_print_done）兜底。
# 任何学习失败都静默，绝不打断识别或打印。
_原_do_print = App.do_print
_原_on_print_done = App.on_print_done
_原_commit_editor = App._commit_editor


def _学习已确认(self):
    """把当前清单里人工改过/确认过的行学进词典。失败静默。"""
    try:
        from handwriting.learning import learn_from_confirmed_records
        路径根 = _根
        词典 = str((路径根 / 'data' / 'dictionaries' / 'handwriting.json'))
        示例 = str((路径根 / 'data' / 'learning' / 'confirmed_examples.json'))
        客户 = None
        try:
            客户 = self.var_customer.get()
        except Exception:
            客户 = None
        记录 = list(getattr(self, 'records', []) or [])
        if not 记录:
            return
        结果 = learn_from_confirmed_records(记录, vocabulary_path=词典,
                                            examples_path=示例, customer=客户)
        if 结果.get('learned') or 结果.get('changed') or 结果.get('pending'):
            _记('人工确认学习：词典别名 %d 条，规范值 %d 条，待审核 %d 条，示例 %d 条' % (
                结果.get('changed', 0), 结果.get('learned', 0),
                len(结果.get('pending', [])), 结果.get('examples', 0)))
    except Exception as e:  # noqa: BLE001
        _记('人工确认学习失败（不影响识别/打印）：%r' % (e,))


def do_print(self):
    """只转发原打印流程。学习移到 on_print_done 里做。"""
    return _原_do_print(self)


def on_print_done(self, 张数):
    """打印真正完成（张数>0）后学习兜底；失败静默，不影响打印收尾。"""
    try:
        if 张数 and 张数 > 0:
            _学习已确认(self)
    except Exception as e:  # noqa: BLE001
        _记('打印后学习异常：%r' % (e,))
    return _原_on_print_done(self, 张数)


def _commit_editor(self, event=None):
    """编辑确认后即时学习：不论是否点击打印，改对/确认的值都进词典。"""
    try:
        位置 = getattr(self, '_编辑位置', None)
        行号 = 位置[0] if 位置 else None
    except Exception:  # noqa: BLE001
        行号 = None
    结果 = _原_commit_editor(self, event)
    if 结果 and 行号 is not None:
        try:
            if 0 <= 行号 < len(self.records):
                r = self.records[行号]
                if r.get('_user_edited') or r.get('_user_confirmed'):
                    _学习已确认(self)
        except Exception as e:  # noqa: BLE001
            _记('编辑后学习异常：%r' % (e,))
    return 结果


App.do_print = do_print
App.on_print_done = on_print_done
App._commit_editor = _commit_editor


# ---------------------------------------------------------------- 自检
try:
    _记('✅ 手写补丁已加载｜模式=%s｜逐张=%s' % (
        _识别设置().get('手写辅助模式', '关'), '开' if _识别设置().get('手写辅助模式') == '逐张' else '关'))
except Exception:  # noqa: BLE001
    pass
