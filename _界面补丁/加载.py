# -*- coding: utf-8 -*-
"""纯源码补丁加载器：不读取旧 EXE、PYC 或外部归档。"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


_服务 = "http://127.0.0.1:18765"


def _服务在():
    try:
        with urllib.request.urlopen(_服务 + "/health", timeout=0.6) as 响应:
            return bool(json.loads(响应.read().decode("utf-8")).get("ok"))
    except Exception:
        return False


def _有PIL(解释器):
    try:
        return (解释器.parent.parent / "Lib" / "site-packages" / "PIL").is_dir()
    except Exception:
        return False


def _起结构服务(根, 资源根):
    if os.environ.get("打单助手_不起结构服务") == "1" or _服务在():
        return
    服务文件 = 资源根 / "_ppstructure" / "serve.py"
    if not 服务文件.is_file():
        return
    候选 = [资源根 / "_ppstructure" / ".venv" / "Scripts" / "python.exe"]
    # 源码目录下的 dist 发布版共用本机 Paddle 环境，不把数 GB 依赖打进 EXE。
    if 根.parent.name == "dist":
        候选.append(根.parent.parent / "_ppstructure" / ".venv" / "Scripts" / "python.exe")
    解释器 = next((p for p in 候选 if p.is_file() and _有PIL(p)), 候选[0])
    if not (解释器.is_file() and _有PIL(解释器)):
        解释器 = shutil.which("python") or shutil.which("python3")
    if not 解释器:
        return
    try:
        日志 = open(根 / "结构服务启动.log", "a", encoding="utf-8")
        标志 = 0x08000000 if os.name == "nt" else 0
        subprocess.Popen(
            [str(解释器), str(服务文件)],
            cwd=str(资源根 / "_ppstructure"),
            stdout=日志,
            stderr=日志,
            creationflags=标志,
        )
    except Exception:
        return
    for _ in range(8):
        time.sleep(0.4)
        if _服务在():
            return


def _执行补丁(路径, 空间):
    空间.update({
        "__file__": str(路径),
        "__builtins__": __builtins__,
    })
    exec(compile(路径.read_text(encoding="utf-8"), str(路径), "exec"), 空间)


def _可选补丁(根, 资源根, 文件名, 空间):
    路径 = 资源根 / "_界面补丁" / 文件名
    if not 路径.is_file():
        return
    try:
        _执行补丁(路径, 空间)
    except Exception as 错误:
        try:
            with (根 / "补丁加载诊断.log").open("a", encoding="utf-8") as 文件:
                文件.write("%s: %r\n" % (文件名, 错误))
        except Exception:
            pass


def 运行(base, 模块=None, 资源根=None):
    """加载主模块和生产补丁，再进入主循环。"""
    根 = Path(base).resolve()
    资源根 = Path(资源根).resolve() if 资源根 else 根
    if 模块 is None:
        import 打单助手 as 模块
    sys.modules["打单助手_原版"] = 模块

    _起结构服务(根, 资源根)

    布局 = 资源根 / "_界面补丁" / "布局.py"
    if not 布局.is_file():
        raise RuntimeError("找不到界面补丁：%s" % 布局)
    _执行补丁(布局, 模块.__dict__)

    公共 = {"T": 模块.T, "App": 模块.App, "HERE": str(根)}
    _可选补丁(根, 资源根, "识别补丁.py", dict(公共, __name__="识别补丁"))
    _可选补丁(根, 资源根, "结构补丁.py", dict(公共, __name__="结构补丁"))
    _可选补丁(根, 资源根, "手写补丁.py", dict(公共, __name__="手写补丁", 主模块=模块))
    _可选补丁(根, 资源根, "原图缩放.py", dict(公共, __name__="原图缩放"))
    _可选补丁(根, 资源根, "导入补丁.py", {"__name__": "导入补丁", "HERE": str(根)})
    _可选补丁(根, 资源根, "防卡死.py", {
        "__name__": "防卡死",
        "Worker": 模块.Worker,
        "App": 模块.App,
        "HERE": str(根),
    })
    _可选补丁(根, 资源根, "周末模式.py", dict(公共, __name__="周末模式"))

    if os.environ.get("打单助手_自检") == "1":
        print("[自检] 纯源码主程序与生产补丁已加载，未进入 mainloop")
        return
    模块.main()
