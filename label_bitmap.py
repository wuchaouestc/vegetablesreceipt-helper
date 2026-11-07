# -*- coding: utf-8 -*-
"""
标签版式模块 —— 位图渲染版（40x30mm @203dpi）

为什么用位图：
    TSPL 内置中文字体只有 24点/48点 两档，且整行必须统一字号，
    做不到"数字比斤大"、也做不到"长品类刚好撑满不裁切"。
    所以先在本地把标签渲染成 320x240 黑白位图，再按 BITMAP 指令发给打印机。
"""

from __future__ import annotations

import ctypes
import struct
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import app_settings as 设置
import 路径

最后打印错误 = ''

HERE = 路径.程序目录()

# 40×30mm @203dpi → 像素
DPI = 203
宽MM = 40
高MM = 30
MM = 25.4
标签宽 = int(宽MM / MM * DPI)   # ≈ 320
标签高 = int(高MM / MM * DPI)   # ≈ 240

字体候选 = ('msyhbd.ttc', 'msyh.ttc', 'simhei.ttf', 'simsun.ttc', 'Deng.ttf')

_MAX_前缀 = 6          # 品类截断保留前 N 字
_MAX_备注 = 3           # 备注截断保留前 N 字


def load_font(size):
    """按字号加载中文字体（优先微软雅黑）。"""
    import os
    win = Path(os.environ.get('WINDIR', 'C:/Windows'))
    目录们 = [win / 'Fonts']
    for 目录 in 目录们:
        for 名 in 字体候选:
            p = 目录 / 名
            if p.is_file():
                try:
                    return ImageFont.truetype(str(p), size)
                except Exception:
                    continue
    return ImageFont.load_default()


def measure(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def fit_text(draw, text, target_h, max_w, force_h=None, ellipsis=False):
    """给一段文字挑字号。

    规则：
      1. 能放下 -> 正常完整显示
      2. 放不下 -> 截断（ellipsis=True 时保留前 MAX_备注 字加"…"，否则前 MAX_前缀 字）
      3. 截断后仍放不下 -> 继续缩字号
    force_h 给定时，字号固定为该值。
    返回 (font, text_to_draw, w, h, box, truncated)
    """
    if force_h is not None:
        font = load_font(force_h)
        w, h = measure(draw, text, font)
        return font, text, w, h, (0, 0, w, h), False

    字号 = target_h
    while 字号 >= 8:
        font = load_font(字号)
        w, h = measure(draw, text, font)
        if w <= max_w and h <= target_h:
            return font, text, w, h, (0, 0, w, h), False
        字号 -= 1

    # 截断
    保留 = _MAX_备注 if ellipsis else _MAX_前缀
    t = text[:保留] + '…'
    字号 = target_h
    while 字号 >= 8:
        font = load_font(字号)
        w, h = measure(draw, t, font)
        if w <= max_w:
            return font, t, w, h, (0, 0, w, h), True
        字号 -= 1
    font = load_font(8)
    w, h = measure(draw, t, font)
    return font, t, w, h, (0, 0, w, h), True


def fit_font(draw, text, target_h, max_w):
    """挑一个字号：高度不超过 target_h，宽度不超过 max_w，尽量大"""
    for 字号 in range(target_h, 7, -1):
        font = load_font(字号)
        w, h = measure(draw, text, font)
        if w <= max_w and h <= target_h:
            return font, w, h
    font = load_font(8)
    w, h = measure(draw, text, font)
    return font, w, h


def fit_bottom_line(draw, weight, unit, remark, avail_w):
    """排版底部行：数字最大，"斤"稍小，备注与"斤"同高 —— 整行绝不超出。

    优先级：数字(主角) > 斤 = 备注
    remark 可以是：
      · 普通字符串 —— 当一整条处理
      · 字符串列表   —— 每一组各自截前3字（金勺子「扁的」「包装切成片的」两组）
    返回 (元素列表, 总宽, 备注是否被截断)
    """
    def try_fit(h_unit, h_rem):
        """给定「斤」和备注的字号，试排版，看总宽是否够。"""
        # 数字尽量大
        数_font = load_font(标签高 // 2)
        # 数字字号下调到能放下
        数_font, 数_w, 数_h = fit_font(draw, str(weight), 标签高 // 2, avail_w)
        斤_font = load_font(h_unit)
        斤_w, 斤_h = measure(draw, unit, 斤_font)
        元素 = [('数', str(weight), 数_font, 数_w, 数_h)]
        元素.append(('斤', unit, 斤_font, 斤_w, 斤_h))
        总 = 数_w + 斤_w
        截断 = False
        # 备注
        若列表 = remark if isinstance(remark, list) else [remark]
        for 组 in 若列表:
            s = str(组 or '').strip()
            if not s:
                continue
            备注_font = load_font(h_rem)
            备注_w, 备注_h = measure(draw, s, 备注_font)
            if 总 + 备注_w > avail_w:
                s = s[:3] + '…'
                备注_w, 备注_h = measure(draw, s, 备注_font)
                截断 = True
            if 总 + 备注_w > avail_w:
                break
            元素.append(('备注', s, 备注_font, 备注_w, 备注_h))
            总 += 备注_w
        return 元素, 总, 截断

    # 分级下调「斤」和备注字号
    for h_unit, h_rem in ((46, 46), (40, 40), (34, 34), (28, 28), (24, 24)):
        元素, 总, 截断 = try_fit(h_unit, h_rem)
        if 总 <= avail_w:
            return 元素, 总, 截断
    return try_fit(20, 20)


def strip_remark_prefix(remark):
    """去掉备注开头的加工属性词（若它其实是品类的一部分）。"""
    s = str(remark or '').strip()
    for 词 in ('圆的', '扁的', '长的', '短的'):
        if s.startswith(词):
            return s[len(词):].strip()
    return s


def split_category_remark(category):
    """把品类里混着的加工属性拆出来：'本地去皮干红葱头 扁的' -> ('本地去皮干红葱头','扁的')"""
    s = str(category or '')
    词们 = ('圆的', '扁的', '长的', '短的', '手工', '去皮', '带冰', '切片')
    for 词 in 词们:
        if s.endswith(词):
            return s[:-len(词)].strip(), 词
    return s, ''


def render_separator(name):
    """渲染「客户分隔标签」：整张只写客户名，居中、尽量大。

    返回 (img, info)，和 render_label 同一形状，调用方不用分情况处理。
    """
    img = Image.new('1', (标签宽, 标签高), 1)
    draw = ImageDraw.Draw(img)
    text = str(name or '')
    font, w, h = fit_font(draw, text, 标签高 - 16, 标签宽 - 16)
    draw.text(((标签宽 - w) // 2, (标签高 - h) // 2), text, font=font, fill=0)
    return img, {'类型': '分隔', '文本': text}


def render_label(category, code, weight, unit, remark):
    """渲染成 320x240 黑白位图，返回 (img, 调试信息 dict)"""
    img = Image.new('1', (标签宽, 标签高), 1)
    draw = ImageDraw.Draw(img)

    # 品类行（最上方，尽量大，长名自动缩字号不裁切）
    品类 = str(category or '')
    cat_font, cat_w, cat_h = fit_font(draw, 品类, 64, 标签宽 - 8)
    draw.text(((标签宽 - cat_w) // 2, 2), 品类, font=cat_font, fill=0)

    # 客户编码（品类下方）
    码 = str(code or '')
    if 码:
        code_font, code_w, code_h = fit_font(draw, 码, 36, 标签宽 - 8)
        draw.text(((标签宽 - code_w) // 2, cat_h + 6), 码, font=code_font, fill=0)
    else:
        code_h = 0

    # 底部行：数字 + 斤 + 备注
    元素, 总宽, 截断 = fit_bottom_line(draw, weight, unit or '斤', remark, 标签宽 - 8)
    x = (标签宽 - 总宽) // 2
    for 类型, 文本, font, w, h in 元素:
        # Pillow 的文字坐标包含字体上留白；按实际墨迹底部对齐，避免大号重量被裁切。
        left, top, right, bottom = draw.textbbox((0, 0), 文本, font=font)
        draw.text((x - left, 标签高 - 4 - bottom), 文本, font=font, fill=0)
        x += w

    return img, {'品类': 品类, '编码': 码, '斤数': weight, '单位': unit,
                 '备注': remark, '截断': 截断}


def to_escpos_raster(img, offset_x=0):
    """位图 -> ESC/POS GS v 0 光栅指令（带横向偏移补偿）"""
    w, h = img.size
    row_bytes = (w + 7) // 8
    数据 = bytearray()
    for y in range(h):
        for xb in range(row_bytes):
            b = 0
            for bit in range(8):
                x = xb * 8 + bit
                if x < w and img.getpixel((x, y)) == 0:
                    b |= 1 << (7 - bit)
            数据.append(b)
    # GS v 0
    out = b'\x1d\x76\x30\x00' + struct.pack('<HH', row_bytes, h) + bytes(数据)
    return out


def to_tspl_job(img, width_mm=40, height_mm=30, gap_mm=2, direction=0,
                density=8, speed=4, bit_black=0):
    """位图 -> TSPL 指令（用 BITMAP 指令发像素数据）

    格式：1 位/像素、MSB 优先、每行字节数 = 宽/8

    位值含义（bit_black）—— 由实机全对比测试定案（2026-10-13）：
      发「整张全黑像素」-> 打出【全白】
      发「整张全白像素」-> 打出【全黑】
      => 位 0 是墨点、位 1 是空白。所以文字像素要置 0。

      bit_black=0 : 黑像素置 0、白像素置 1 -> 白底黑字 ✅ 本机正确
      bit_black=1 : 反过来 -> 黑底白字（曾经误用）
    """
    w, h = img.size
    row_bytes = (w + 7) // 8
    数据 = bytearray()
    for y in range(h):
        for xb in range(row_bytes):
            b = 0
            for bit in range(8):
                x = xb * 8 + bit
                if x < w:
                    pix = img.getpixel((x, y))
                    if bit_black == 0:
                        空白 = (pix != 0)   # TSPL 本机位 0 是墨，位 1 是空白
                    else:
                        空白 = (pix == 0)
                    if 空白:
                        b |= 1 << (7 - bit)
            数据.append(b)

    行 = []
    行.append('SIZE %d mm,%d mm' % (width_mm, height_mm))
    行.append('GAP %d mm,0 mm' % gap_mm)
    行.append('DIRECTION %d' % direction)
    行.append('DENSITY %d' % density)
    行.append('SPEED %d' % speed)
    行.append('CLS')
    行.append('BITMAP 0,0,%d,%d,0,' % (row_bytes, h))
    行.append(bytes(数据).hex().upper())
    行.append('PRINT 1,1')
    return ('\r\n'.join(行) + '\r\n').encode('latin-1')


def build_job(category, code, weight, unit, remark):
    """返回 (ESC/POS 字节流, 预览图, 调试信息)"""
    img, info = render_label(category, code, weight, unit, remark)
    return to_escpos_raster(img), img, info


def _枚举_usb打印接口():
    """列出当前存在的 GUID_DEVINTERFACE_USBPRINT 设备接口路径。"""
    class GUID(ctypes.Structure):
        _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD),
                    ('Data3', wintypes.WORD), ('Data4', ctypes.c_ubyte * 8)]

    class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('InterfaceClassGuid', GUID),
                    ('Flags', wintypes.DWORD), ('Reserved', ctypes.c_void_p)]

    class SP_DEVICE_INTERFACE_DETAIL_DATA_W(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('DevicePath', wintypes.WCHAR * 1)]

    结果 = []
    句柄 = None
    无效句柄 = ctypes.c_void_p(-1).value
    try:
        setupapi = ctypes.WinDLL('setupapi', use_last_error=True)
        setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                                  wintypes.LPCWSTR, wintypes.DWORD]
        setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
        setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
        setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
        setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
        setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
        setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

        guid = GUID(0x28d78fad, 0x5a12, 0x11d1,
                    (ctypes.c_ubyte * 8)(0xae, 0x5b, 0x00, 0x00, 0xf8, 0x03, 0xa8, 0xc2))
        句柄 = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, 0x12)
        if not 句柄 or 句柄 == 无效句柄:
            句柄 = None
            return 结果
        序号 = 0
        while True:
            data = SP_DEVICE_INTERFACE_DATA()
            data.cbSize = ctypes.sizeof(data)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                    句柄, None, ctypes.byref(guid), 序号, ctypes.byref(data)):
                break
            需要 = wintypes.DWORD()
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                句柄, ctypes.byref(data), None, 0, ctypes.byref(需要), None)
            if 需要.value:
                缓冲 = ctypes.create_unicode_buffer(max(需要.value, 8))
                detail = ctypes.cast(缓冲, ctypes.POINTER(SP_DEVICE_INTERFACE_DETAIL_DATA_W))
                detail.contents.cbSize = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
                if setupapi.SetupDiGetDeviceInterfaceDetailW(
                        句柄, ctypes.byref(data), detail, 需要, None, None):
                    结果.append(str(detail.contents.DevicePath))
            序号 += 1
    except Exception:
        return []
    finally:
        if 句柄:
            setupapi.SetupDiDestroyDeviceInfoList(句柄)
    return list(dict.fromkeys(结果))


def _find_usb_printer_path(vid=None, pid=None):
    """按 VID/PID 找接口；都为空时选择第一台 USB 打印机。"""
    目标vid = str(vid or '').lower()
    目标pid = str(pid or '').lower()
    for path in _枚举_usb打印接口():
        小写 = path.lower()
        if 目标vid and 目标vid not in 小写:
            continue
        if 目标pid and 目标pid not in 小写:
            continue
        return path
    return None


def list_usb_printers():
    """列出 USB 打印机的 VID、PID、显示名和设备接口路径。"""
    import re
    结果 = []
    for path in _枚举_usb打印接口():
        match = re.search(r'vid_([0-9a-f]{4})&pid_([0-9a-f]{4})', path, re.I)
        vid = ('vid_' + match.group(1).lower()) if match else ''
        pid = ('pid_' + match.group(2).lower()) if match else ''
        显示 = ('USB 打印机 %s %s' % (vid, pid)).strip() if match else Path(path).name
        结果.append({'vid': vid, 'pid': pid, '显示': 显示, 'path': path})
    return 结果


def build_tspl(category, code, weight, unit, remark):
    """返回 (TSPL 字节流, 预览图, 调试信息) —— 直写 USB 用这个"""
    img, info = render_label(category, code, weight, unit, remark)
    return to_tspl_job(img), img, info


def build_separator_tspl(name):
    """客户分隔标签的字节流（整张只写客户名）

    ⚠️ 返回值和 `build_tspl` 保持一致 —— **只返回字节流**。
    """
    img, _info = render_separator(name)
    return to_tspl_job(img)


def _打印配置():
    return {
        '通道': str(设置.取('打印机.通道', 'usb') or 'usb').lower(),
        'vid': 设置.取('打印机.vid'),
        'pid': 设置.取('打印机.pid'),
        '名称': 设置.取('打印机.名称'),
    }


def print_label(category, code, weight, unit, remark, via=None):
    """打印一张标签；via 可为 usb、driver 或 auto（USB 失败后走驱动）。"""
    配置 = _打印配置()
    通道 = str(via or 配置['通道']).lower()
    if 通道 == 'driver':
        return print_via_driver(category, code, weight, unit, remark, 配置['名称'])
    job, _img, _info = build_tspl(category, code, weight, unit, remark)
    if 通道 == 'auto':
        if print_via_usb(job, 配置['vid'], 配置['pid']):
            return True
        USB错误 = 最后打印错误
        if print_via_driver(category, code, weight, unit, remark, 配置['名称']):
            return True
        return _打印失败('USB：%s；驱动：%s' % (USB错误, 最后打印错误))
    return print_via_usb(job, 配置['vid'], 配置['pid'])


def print_separator(name, via=None):
    """打印客户分隔标签，使用与明细标签相同的通道配置。"""
    配置 = _打印配置()
    通道 = str(via or 配置['通道']).lower()
    if 通道 == 'driver':
        return print_via_driver(name, '', '', '', '', 配置['名称'])
    job = build_separator_tspl(name)
    if 通道 == 'auto':
        if print_via_usb(job, 配置['vid'], 配置['pid']):
            return True
        USB错误 = 最后打印错误
        if print_via_driver(name, '', '', '', '', 配置['名称']):
            return True
        return _打印失败('USB：%s；驱动：%s' % (USB错误, 最后打印错误))
    return print_via_usb(job, 配置['vid'], 配置['pid'])


def _真打印闸(通路):
    """是否允许真的发出打印（测试时可关）。"""
    return True


def _打印失败(消息):
    global 最后打印错误
    最后打印错误 = str(消息)
    return False


def print_via_usb(job_bytes, printer_vid=None, printer_pid=None):
    """把 TSPL 作业通过 CreateFileW/WriteFile 写入 USBPRINT 接口。"""
    global 最后打印错误
    最后打印错误 = ''
    if not _真打印闸('usb'):
        return True
    句柄 = None
    try:
        path = _find_usb_printer_path(printer_vid, printer_pid)
        if not path:
            条件 = ' / '.join(str(x) for x in (printer_vid, printer_pid) if x) or '任意 USB 打印机'
            return _打印失败('找不到 USB 打印机（%s），请检查接线和设置页通道' % 条件)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                         ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                         ctypes.c_void_p]
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        kernel32.WriteFile.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = wintypes.BOOL
        句柄 = kernel32.CreateFileW(path, 0x40000000, 3, None, 3, 0x80, None)
        if not 句柄 or 句柄 == ctypes.c_void_p(-1).value:
            句柄 = None
            return _打印失败('打不开 USB 打印机，Windows 错误码 %s' % ctypes.get_last_error())
        数据 = bytes(job_bytes)
        缓冲 = ctypes.create_string_buffer(数据)
        已写 = wintypes.DWORD()
        if not kernel32.WriteFile(句柄, 缓冲, len(数据), ctypes.byref(已写), None):
            return _打印失败('USB 打印发送失败，Windows 错误码 %s' % ctypes.get_last_error())
        if 已写.value != len(数据):
            return _打印失败('USB 打印只发送了 %d/%d 字节' % (已写.value, len(数据)))
        return True
    except Exception as e:
        return _打印失败('USB 打印异常：%s' % e)
    finally:
        if 句柄:
            kernel32.CloseHandle(句柄)


def _driver_dib_bytes(img):
    """构造 Windows StretchDIBits 使用的 1 位、自下而上 DIB。"""
    if img.mode != '1':
        img = img.convert('1')
    w, h = img.size
    行字节 = (w + 7) // 8
    对齐字节 = ((w + 31) // 32) * 4
    raw = img.tobytes()
    行们 = []
    for y in range(h - 1, -1, -1):
        行 = raw[y * 行字节:(y + 1) * 行字节]
        行们.append(行 + b'\0' * (对齐字节 - len(行)))
    像素 = b''.join(行们)
    头 = struct.pack('<IiiHHIIiiII', 40, w, h, 1, 1, 0, len(像素), 0, 0, 2, 2)
    调色板 = bytes((0, 0, 0, 0, 255, 255, 255, 0))
    return 头 + 调色板, 像素


def print_via_driver(category, code, weight, unit, remark, printer_name=None):
    """通过 Windows 打印机驱动，以 40×30mm 物理尺寸输出 1 位位图。"""
    global 最后打印错误
    最后打印错误 = ''
    if not _真打印闸('driver'):
        return True
    dc = None
    文档开始 = False
    页开始 = False
    try:
        import win32print
        import win32ui
        名称 = printer_name or win32print.GetDefaultPrinter()
        if not 名称:
            return _打印失败('没有配置 Windows 打印机，也找不到系统默认打印机')
        打印句柄 = win32print.OpenPrinter(名称)
        win32print.ClosePrinter(打印句柄)
        img, _info = render_label(category, code, weight, unit, remark)
        info, pixels = _driver_dib_bytes(img)
        dc = win32ui.CreateDC()
        dc.CreatePrinterDC(名称)
        hdc = dc.GetSafeHdc()
        gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
        gdi32.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
        gdi32.GetDeviceCaps.restype = ctypes.c_int
        gdi32.StretchDIBits.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 8 + [
            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.DWORD]
        gdi32.StretchDIBits.restype = ctypes.c_int
        dpi_x = gdi32.GetDeviceCaps(hdc, 88)
        dpi_y = gdi32.GetDeviceCaps(hdc, 90)
        宽 = max(1, round(40 / 25.4 * dpi_x))
        高 = max(1, round(30 / 25.4 * dpi_y))
        dc.StartDoc('打单助手V6 40x30mm')
        文档开始 = True
        dc.StartPage()
        页开始 = True
        info缓冲 = ctypes.create_string_buffer(info)
        像素缓冲 = ctypes.create_string_buffer(pixels)
        结果 = gdi32.StretchDIBits(hdc, 0, 0, 宽, 高, 0, 0, img.size[0], img.size[1],
                                   像素缓冲, info缓冲, 0, 0x00CC0020)
        if 结果 <= 0:
            return _打印失败('Windows GDI 位图输出失败，错误码 %s' % ctypes.get_last_error())
        dc.EndPage()
        页开始 = False
        dc.EndDoc()
        文档开始 = False
        return True
    except Exception as e:
        return _打印失败('Windows 驱动打印失败：%s' % e)
    finally:
        if dc is not None:
            try:
                if 页开始:
                    dc.EndPage()
                if 文档开始:
                    dc.EndDoc()
            except Exception:
                pass
            try:
                dc.DeleteDC()
            except Exception:
                pass
