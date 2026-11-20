# -*- coding: utf-8 -*-
"""重构后静态导入校验：解析所有源码的 AST，验证项目内部导入都能解析。

覆盖「自检未执行到的函数内导入」——补丁里的 import 都在函数体里，
只跑 --自检 无法证明它们都有效。
"""
import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

项目顶层 = {"app", "core", "handwriting", "路径", "config", "打单助手", "field_service"}
扫描目录 = ["app", "core", "handwriting", "patches", "tests", "field_service", "tools"]
根下文件 = ["打单助手.py", "启动器.py", "路径.py", "config.py", "build.py"]

缓存 = {}


def 能解析(模块名: str) -> bool:
    if 模块名 in 缓存:
        return 缓存[模块名]
    try:
        结果 = importlib.util.find_spec(模块名) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        结果 = False
    缓存[模块名] = 结果
    return 结果


def 收集(py: Path):
    树 = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
    for node in ast.walk(树):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对导入由包机制保证，跳过
                continue
            if node.module:
                yield node.lineno, node.module


目标 = [ROOT / n for n in 根下文件 if (ROOT / n).is_file()]
for 目录 in 扫描目录:
    目标.extend(sorted((ROOT / 目录).rglob("*.py")))
目标 = [p for p in 目标 if ".venv" not in p.parts and "__pycache__" not in p.parts]

失败 = []
检查数 = 0
for 文件 in 目标:
    try:
        条目 = list(收集(文件))
    except SyntaxError as 错:
        失败.append((文件, 0, "语法错误: %s" % 错))
        continue
    for 行号, 模块名 in 条目:
        顶层 = 模块名.split(".")[0]
        if 顶层 not in 项目顶层:
            continue
        检查数 += 1
        if not 能解析(模块名):
            失败.append((文件, 行号, 模块名))

print("扫描文件数：%d，项目内部导入检查点：%d" % (len(目标), 检查数))
if 失败:
    print("解析失败 %d 处：" % len(失败))
    for 文件, 行号, 模块名 in 失败:
        print("  %s:%s  %s" % (文件.relative_to(ROOT), 行号, 模块名))
    sys.exit(1)
print("全部项目内部导入均可解析 ✓")
