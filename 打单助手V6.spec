# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all
from pathlib import Path


dnd_datas, dnd_binaries, dnd_hiddenimports = collect_all("tkinterdnd2")

a = Analysis(
    ["启动器.py"],
    pathex=[],
    binaries=dnd_binaries,
    datas=[
        ("_界面补丁", "_界面补丁"),
        ("handwriting/review_page.html", "handwriting"),
    ] + [(str(p), "_ppstructure") for p in Path("_ppstructure").iterdir()
         if p.is_file() and p.suffix in (".py", ".bat", ".txt")] + dnd_datas,
    hiddenimports=[
        "PIL.Image",
        "PIL.ImageDraw",
        "PIL.ImageFont",
        "PIL.ImageTk",
        "win32print",
        "win32ui",
        "win32api",
        "win32con",
        # 阶段 3/4 的独立手写识别库（界面补丁按需导入，要显式收进包里）
        "handwriting",
        "handwriting.budget",
        "handwriting.compare",
        "handwriting.detect",
        "handwriting.evidence",
        "handwriting.frozen",
        "handwriting.gate",
        "handwriting.geometry",
        "handwriting.learning",
        "handwriting.model_records",
        "handwriting.modes",
        "handwriting.pipeline",
        "handwriting.preprocess",
        "handwriting.providers",
        "handwriting.review",
        "handwriting.review_web",
        "handwriting.storage",
        "handwriting.vocabulary",
    ] + dnd_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["paddle", "paddleocr", "paddlex"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="打单助手V6",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="图标.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="打单助手V6",
)
