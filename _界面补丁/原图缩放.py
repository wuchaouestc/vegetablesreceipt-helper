# -*- coding: utf-8 -*-
"""原图缩略图缩放补丁 —— 滚轮缩放 + 左键拖动。

在「标签预览」下方的「原图缩略图」区域里：
  · 鼠标滚轮向上（正滚）＝放大原图，向下（负滚）＝缩小原图；
  · 左键点住图片拖动，图片可以上下左右移动，可以移出显示区域；
  · 缩放围绕**鼠标所在位置**进行，指哪放哪，不会跳来跳去。

为什么不能直接改主程序 `update_preview`：
主程序里原图是「按画布尺寸等比例缩小后居中」一次性画死的，没有状态。
要支持缩放/拖动必须引入「缩放倍数 + 平移偏移」这两个状态，再按状态重绘。
所以这里包一层 `update_preview`，把原图绘制换成带状态的重绘，标签预览部分原样不动。

数据模型（都挂在 self 上，随选中行重设）：
  self._orig_原图   : PIL Image（RGB 原始图，未缩放）
  self._orig_缩放   : float，相对「适配画布」的倍数（1.0＝刚好放进画布）
  self._orig_偏移   : (dx, dy)，图片中心相对画布中心的偏移（像素）
  self._orig_拖动   : 左键拖动时的起点快照

所有入口都 try/except 兜底：补丁出错就退回原行为，绝不让界面崩。
"""
from __future__ import annotations


_最小缩放 = 0.2
_最大缩放 = 8.0
_缩放步进 = 1.1        # 每格滚轮缩放 10%


def _取原图(self, rec):
    """拿到选中行对应的原图路径，读成 RGB PIL 图。失败返回 None。"""
    try:
        路径 = rec.get('_img') if rec else None
        if not 路径:
            return None
        from pathlib import Path as _Path
        if not _Path(路径).is_file():
            return None
        from PIL import Image
        return Image.open(路径).convert('RGB')
    except Exception:
        return None


def _画布尺寸(self):
    """原图画布当前宽高；太小则用兜底尺寸。"""
    try:
        宽 = self.canvas_orig.winfo_width()
        高 = self.canvas_orig.winfo_height()
    except Exception:
        宽 = 高 = 0
    return max(60, 宽), max(60, 高)


def _重绘原图(self):
    """按当前缩放与偏移把原图画到 canvas_orig 上。"""
    try:
        画布 = self.canvas_orig
    except Exception:
        return
    画布.delete('all')
    原图 = getattr(self, '_orig_原图', None)
    if 原图 is None:
        return
    缩放 = getattr(self, '_orig_缩放', 1.0)
    偏移 = getattr(self, '_orig_偏移', (0, 0))
    宽, 高 = _画布尺寸(self)
    # 基础比例：让原图刚好放进画布
    基础 = min(宽 / 原图.width, 高 / 原图.height)
    显示宽 = max(1, round(原图.width * 基础 * 缩放))
    显示高 = max(1, round(原图.height * 基础 * 缩放))
    from PIL import Image
    from PIL import ImageTk
    图 = 原图.resize((显示宽, 显示高), Image.Resampling.LANCZOS)
    self._orig_图 = ImageTk.PhotoImage(图, master=画布)
    # 图片中心 = 画布中心 + 偏移；偏移可以很大，图片就移出显示区域。
    中心x = 宽 / 2 + 偏移[0]
    中心y = 高 / 2 + 偏移[1]
    画布.create_image(中心x, 中心y, image=self._orig_图, anchor='center')


def _原图滚轮(self, event):
    """鼠标滚轮：向上放大、向下缩小，围绕鼠标位置缩放。"""
    try:
        原图 = getattr(self, '_orig_原图', None)
        if 原图 is None:
            return
        画布 = self.canvas_orig
        宽, 高 = _画布尺寸(self)
        旧缩放 = getattr(self, '_orig_缩放', 1.0)
        # Windows：正 delta = 向上滚 = 放大；Linux 用 Button-4/5 另外绑定。
        if getattr(event, 'delta', 0) > 0 or getattr(event, 'num', 0) == 4:
            新缩放 = min(_最大缩放, 旧缩放 * _缩放步进)
        elif getattr(event, 'delta', 0) < 0 or getattr(event, 'num', 0) == 5:
            新缩放 = max(_最小缩放, 旧缩放 / _缩放步进)
        else:
            return
        if abs(新缩放 - 旧缩放) < 1e-9:
            return
        旧偏移 = getattr(self, '_orig_偏移', (0, 0))
        # 鼠标在画布里的位置（相对画布中心）
        mx = event.x - 宽 / 2
        my = event.y - 高 / 2
        # 缩放前后，让鼠标指向的图内那一点保持不动：
        # 该点相对图片中心 = (mx - 旧偏移)，缩放后应 = 该点 × 新/旧。
        比例 = 新缩放 / 旧缩放
        新偏移x = mx - (mx - 旧偏移[0]) * 比例
        新偏移y = my - (my - 旧偏移[1]) * 比例
        self._orig_缩放 = 新缩放
        self._orig_偏移 = (新偏移x, 新偏移y)
        _重绘原图(self)
    except Exception:
        pass


def _原图按下(self, event):
    """左键按下：记录拖动起点。"""
    try:
        if getattr(self, '_orig_原图', None) is None:
            return
        self._orig_拖动 = (event.x, event.y, getattr(self, '_orig_偏移', (0, 0)))
    except Exception:
        pass


def _原图移动(self, event):
    """左键按住拖动：按位移更新偏移并重绘。"""
    try:
        起点 = getattr(self, '_orig_拖动', None)
        if 起点 is None:
            return
        sx, sy, (ox, oy) = 起点
        self._orig_偏移 = (ox + (event.x - sx), oy + (event.y - sy))
        _重绘原图(self)
    except Exception:
        pass


def _原图松开(self, event):
    try:
        self._orig_拖动 = None
    except Exception:
        pass


def _原图双击复位(self, event):
    """双击复位：回到刚适配画布、居中的状态（顺手给的便利，不改需求）。"""
    try:
        self._orig_缩放 = 1.0
        self._orig_偏移 = (0, 0)
        _重绘原图(self)
    except Exception:
        pass


_原_update_preview = App.update_preview


def update_preview(self, rec=None):
    """包一层：先跑原来的（画标签预览），再用带缩放/偏移的状态重绘原图。"""
    _原_update_preview(self, rec)
    try:
        if not hasattr(self, 'canvas_orig'):
            return
        # 重新解析当前选中行（和主程序同一套逻辑），拿到原图路径。
        目标 = rec
        if 目标 is None:
            sel = self.tree.selection()
            if sel:
                idx = self._row_to_idx(sel[0])
                if 0 <= idx < len(self.records):
                    目标 = self.records[idx]
        if 目标 is None:
            # 没有选中行：清掉原图状态，只留「点一行看原图」占位。
            self._orig_原图 = None
            self._orig_缩放 = 1.0
            self._orig_偏移 = (0, 0)
            self.canvas_orig.delete('all')
            return
        原图 = _取原图(self, 目标)
        # 只有换了行才重置缩放/偏移；同一行反复重绘保持用户当前视图。
        if getattr(self, '_orig_行号', None) != id(目标):
            self._orig_行号 = id(目标)
            self._orig_缩放 = 1.0
            self._orig_偏移 = (0, 0)
        self._orig_原图 = 原图
        _重绘原图(self)
    except Exception:
        # 兜底：原流程已经把原图按固定比例画好了，出错就保留原样。
        pass


App.update_preview = update_preview


def _装缩放手势(self):
    """给 canvas_orig 绑滚轮与拖动事件（在界面搭好后调用一次）。"""
    try:
        画布 = getattr(self, 'canvas_orig', None)
        if 画布 is None:
            return
        画布.bind('<MouseWheel>', self._原图滚轮)
        画布.bind('<Button-4>', self._原图滚轮)     # Linux 上滚
        画布.bind('<Button-5>', self._原图滚轮)     # Linux 下滚
        画布.bind('<ButtonPress-1>', self._原图按下)
        画布.bind('<B1-Motion>', self._原图移动)
        画布.bind('<ButtonRelease-1>', self._原图松开)
        画布.bind('<Double-Button-1>', self._原图双击复位)
        self._orig_缩放 = 1.0
        self._orig_偏移 = (0, 0)
        self._orig_拖动 = None
        self._orig_原图 = None
        self._orig_行号 = None
    except Exception:
        pass


App._原图滚轮 = _原图滚轮
App._原图按下 = _原图按下
App._原图移动 = _原图移动
App._原图松开 = _原图松开
App._原图双击复位 = _原图双击复位
App._重绘原图 = _重绘原图


# 界面搭好之后绑手势。找一个可靠的挂载点：包一层 _build_checklist（布局补丁里创建 canvas_orig 的地方）。
_原_build_checklist = App._build_checklist


def _build_checklist(self, parent):
    _原_build_checklist(self, parent)
    try:
        _装缩放手势(self)
    except Exception:
        pass


App._build_checklist = _build_checklist


# 自检日志
try:
    with open('结构诊断.log', 'a', encoding='utf-8') as f:
        f.write('原图缩略图缩放补丁已加载\n')
except Exception:
    pass
