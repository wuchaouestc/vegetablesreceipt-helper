# -*- coding: utf-8 -*-
"""生成软件著作权登记用的源程序页（前 30 页 + 后 30 页）。

为什么需要这个脚本
------------------
软著登记要求提交源程序**前 30 页与后 30 页**，每页不少于 50 行，
页眉注明软件名称与版本号。手工复制既容易漏文件，也会因换行差异导致页数不准。

本脚本按固定顺序合并全部生产源码，统一分页，输出可直接打印为 PDF 的文本：

    docs/软件著作权材料/源程序页/源程序_全量.txt     全量（带页码与分页符）
    docs/软件著作权材料/源程序页/源程序_前30页.txt
    docs/软件著作权材料/源程序页/源程序_后30页.txt

用法：
    .venv/Scripts/python.exe tools/生成软著源码页.py

约定：
    · 页眉固定为「打单助手软件 V<版本>」+ 第 N 页，每页 50 行代码。
    · 文件之间插入 3 行分隔注释（含文件路径），便于审查时定位。
    · 收集顺序固定：契约层 → 基础设施层 → 业务领域层 → 子系统 → 服务 → 测试，
      保证多次运行结果一致（可复现是登记材料的基本要求）。
"""
from __future__ import annotations

import sys
from pathlib import Path

每页行数 = 50
前后页数 = 30

根 = Path(__file__).resolve().parents[1]
if str(根) not in sys.path:
    sys.path.insert(0, str(根))
from 版本 import 软件版本  # noqa: E402  版本号唯一来源

软件名称 = "打单助手软件 V%s" % 软件版本
输出目录 = 根 / "docs" / "软件著作权材料" / "源程序页"

# 固定收集顺序：先契约层（入口与路径），再核心能力，最后子系统与测试。
收集顺序 = [
    "启动器.py",
    "路径.py",
    "config.py",
    "打单助手.py",
    "core/ttsb2.py",
    "core/label_bitmap.py",
    "app/app_settings.py",
    "app/customer_profiles.py",
    "app/打单记录.py",
    "app/打印历史.py",
    "app/标签余量.py",
    "app/导入落地.py",
    "handwriting/",
    "patches/",
    "field_service/serve.py",
    "field_service/grid.py",
    "field_service/layout.py",
    "field_service/pp_backend.py",
    "field_service/field_backend.py",
    "build.py",
    "tools/",
    "tests/",
]

排除片段 = {"__pycache__", ".venv", ".git", "site-packages"}


def _展开(条目: str) -> list[Path]:
    """把收集顺序里的一个条目展开成具体 .py 文件列表。"""
    路径 = 根 / 条目
    if 路径.is_file():
        return [路径]
    if not 路径.is_dir():
        return []
    文件们 = []
    for 子 in sorted(路径.rglob("*.py")):
        if any(片段 in 子.parts for 片段 in 排除片段):
            continue
        if 子.name == "__init__.py":
            continue
        文件们.append(子)
    return 文件们


def 收集() -> list[Path]:
    结果: list[Path] = []
    已记录: set[Path] = set()
    for 条目 in 收集顺序:
        for 文件 in _展开(条目):
            if 文件 in 已记录:
                continue
            已记录.add(文件)
            结果.append(文件)
    return 结果


def 拼装(文件们: list[Path]) -> list[str]:
    """产出全部源码行，文件之间插入分隔注释。"""
    行们: list[str] = []
    for 文件 in 文件们:
        相对 = 文件.relative_to(根).as_posix()
        行们.append("# " + "=" * 76)
        行们.append("# 文件：" + 相对)
        行们.append("# " + "=" * 76)
        try:
            内容 = 文件.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            内容 = 文件.read_text(encoding="utf-8", errors="replace")
        行们.extend(内容.splitlines())
        行们.append("")
    return 行们


def 分页(行们: list[str]) -> list[str]:
    """按固定行数分页，页眉带软件名称与页码，页尾加分页符。"""
    页们: list[str] = []
    总页数 = max(1, (len(行们) + 每页行数 - 1) // 每页行数)
    for 序号 in range(总页数):
        起点 = 序号 * 每页行数
        本页 = 行们[起点:起点 + 每页行数]
        页们.append("%s                                   第 %d 页 / 共 %d 页"
                    % (软件名称, 序号 + 1, 总页数))
        页们.append("")
        页们.extend(本页)
        页们.append("\f")
    return 页们


def main() -> int:
    文件们 = 收集()
    行们 = 拼装(文件们)
    页行们 = 分页(行们)
    总页数 = 页行们.count("\f")

    输出目录.mkdir(parents=True, exist_ok=True)
    (输出目录 / "源程序_全量.txt").write_text("\n".join(页行们), encoding="utf-8")

    if 总页数 < 前后页数 * 2:
        前 = 页行们
        后: list[str] = []
    else:
        前 = 页行们[:前后页数 * (每页行数 + 3)]
        后 = 页行们[-前后页数 * (每页行数 + 3):]
    (输出目录 / "源程序_前30页.txt").write_text("\n".join(前), encoding="utf-8")
    (输出目录 / "源程序_后30页.txt").write_text("\n".join(后), encoding="utf-8")

    print("收集源码文件：%d 个" % len(文件们))
    print("源码总行数：%d 行" % len(行们))
    print("分页结果：共 %d 页（每页 %d 行）" % (总页数, 每页行数))
    print("是否满足 60 页要求：%s" % ("是" if 总页数 >= 前后页数 * 2 else "否，源码不足 60 页，提交全部"))
    print("输出目录：%s" % 输出目录.relative_to(根))
    return 0


if __name__ == "__main__":
    sys.exit(main())
