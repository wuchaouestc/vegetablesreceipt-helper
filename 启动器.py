# -*- coding: utf-8 -*-
"""打单助手 V6 纯源码启动入口。"""

import os
import sys
import traceback
from pathlib import Path


def 程序目录():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def 运行():
    根 = 程序目录()
    if str(根) not in sys.path:
        sys.path.insert(0, str(根))

    import 打单助手 as 主程序

    资源根 = 根 / "_internal" if (根 / "_internal" / "patches").is_dir() else 根
    加载文件 = 资源根 / "patches" / "加载.py"
    if not 加载文件.is_file():
        raise RuntimeError("找不到界面补丁加载器：%s" % 加载文件)
    空间 = {
        "__name__": "界面补丁加载器",
        "__file__": str(加载文件),
        "__package__": None,
    }
    exec(compile(加载文件.read_text(encoding="utf-8"), str(加载文件), "exec"), 空间)
    空间["运行"](根, 主程序, 资源根)


def 报告(错误):
    文本 = "".join(traceback.format_exception(*错误))
    try:
        # 这里刻意不依赖 路径 / core.日志：本函数在"程序起不来"时执行，
        # 那些模块恰恰可能没导入成功。只用启动器自身的 程序目录() 兜底。
        日志目录 = 程序目录() / "log"
        日志目录.mkdir(parents=True, exist_ok=True)
        (日志目录 / "启动失败.log").write_text(文本, encoding="utf-8")
    except Exception:
        pass
    try:
        import tkinter.messagebox as 消息框
        消息框.showerror("打单助手启动失败", 文本[-1600:])
    except Exception:
        pass


if __name__ == "__main__":
    if "--自检" in sys.argv:
        os.environ["打单助手_自检"] = "1"
    try:
        运行()
    except Exception:
        报告(sys.exc_info())
        sys.exit(1)
