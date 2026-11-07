# -*- coding: utf-8 -*-
"""在 Windows 上安装构建依赖并生成打单助手V6.exe。

构建先进入暂存目录，再合并到 dist，避免 PyInstaller 清理旧发布目录时
连同用户配置、打单记录等运行时数据一起删除。
"""

from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PYTHON = VENV / "Scripts" / "python.exe"
PRODUCT_NAME = "打单助手V6"
DIST_DIR = ROOT / "dist" / PRODUCT_NAME
STAGING_ROOT = ROOT / "build" / "release_staging"
RELEASE_FILES = ("settings.json", "customers.json", "启动V6.bat")
# 真实密钥只放在**本机发布目录**：不进仓库、不打包进 exe，缺失也不算构建失败
# （从 GitHub 下载源码的人没有这个文件，他们按 config_local.example.py 自己填）。
OPTIONAL_RELEASE_FILES = ("config_local.py",)

# 「越用越智能」的积累数据：发布时把初始词典/示例库带过去，
# 但**存在就不覆盖**——用户运行中积累的别名、示例不能被打包的新版冲掉。
# 这里按目录**递归**复制，`data/evaluations` 里有评测产物（报告 + 截图），
# 必须整棵带过去——否则发布出来的软件里「评测结果」是空的。
SEED_DATA_DIRS = ("data/dictionaries", "data/learning", "data/evaluations")
# 空目录也要在发布里建出来，让用户第一次核对时标注有地方落盘。
SEED_EMPTY_DIRS = ("data/annotations",)


def run(command):
    subprocess.run([str(part) for part in command], cwd=ROOT, check=True)


def sync_program_files(staging_dir, dist_dir=DIST_DIR):
    """用新构建更新程序文件，但保留发布目录里额外的用户数据。"""
    staging_dir = Path(staging_dir)
    dist_dir = Path(dist_dir)
    if not staging_dir.is_dir():
        raise FileNotFoundError("构建产物不存在：%s" % staging_dir)
    dist_dir.mkdir(parents=True, exist_ok=True)
    protected = {
        "settings.json", "customers.json", "config.py", "启动V6.bat", "标签余量.json",
        "打单记录", "导入", "备份", "ocr_cache", "程序.log", "结构服务启动.log",
        # 越用越智能的积累数据：人工核对标注、词典别名、已确认示例都在这下面，
        # 更新软件时**绝不能被新版覆盖**，否则用户核对的数据就白攒了。
        "data",
    }

    def ignore_user_data(current_dir, names):
        if Path(current_dir).resolve() == staging_dir.resolve():
            return protected.intersection(names) | {name for name in names if name.endswith('.log')}
        return []

    shutil.copytree(staging_dir, dist_dir, dirs_exist_ok=True, ignore=ignore_user_data)


def copy_release_files(source_dir=ROOT, dist_dir=DIST_DIR):
    """补齐发布所需外部文件；任何同名现有文件都不覆盖。"""
    source_dir = Path(source_dir)
    dist_dir = Path(dist_dir)
    dist_dir.mkdir(parents=True, exist_ok=True)
    result = {"copied": [], "kept": [], "missing": []}
    for name in RELEASE_FILES:
        source = source_dir / name
        target = dist_dir / name
        if not source.is_file():
            result["missing"].append(name)
        elif target.exists():
            result["kept"].append(name)
        else:
            shutil.copy2(source, target)
            result["copied"].append(name)
    # 可选文件（真实密钥）：有就补上，没有就跳过，不报警告。
    for name in OPTIONAL_RELEASE_FILES:
        source = source_dir / name
        target = dist_dir / name
        if not source.is_file():
            continue
        if target.exists():
            result["kept"].append(name)
        else:
            shutil.copy2(source, target)
            result["copied"].append(name)
    ensure_runtime_mode(dist_dir / "settings.json")
    return result


def ensure_runtime_mode(path):
    """只补齐运行模式，不覆盖用户设置；无效配置保留并警告。"""
    path = Path(path)
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        print("警告：配置无法解析，已原样保留：%s" % path)
        return
    if not isinstance(data, dict):
        print("警告：配置不是对象，已原样保留：%s" % path)
        return
    system = data.setdefault("系统", {})
    if not isinstance(system, dict):
        print("警告：系统设置不是对象，已原样保留：%s" % path)
        return
    if "运行模式" in system:
        return
    from app_settings import 默认设置
    system["运行模式"] = 默认设置["系统"]["运行模式"]
    shutil.copy2(path, path.with_name(path.name + ".before-runtime-mode.bak"))
    temporary = path.with_name(path.name + ".release.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def copy_seed_data(source_dir=ROOT, dist_dir=DIST_DIR):
    """把初始词典/示例库/评测产物带进发布目录；同名文件已存在则保留（不覆盖）。

    按目录**递归**复制：词典、示例库只有文件，评测产物 `data/evaluations`
    下有子目录（每次评测一个目录 + 截图），只复制顶层文件会把内容漏掉。
    """
    source_dir = Path(source_dir)
    dist_dir = Path(dist_dir)
    result = {"copied": [], "kept": [], "missing": []}
    for rel in SEED_DATA_DIRS:
        source = source_dir / rel
        target = dist_dir / rel
        if not source.is_dir():
            result["missing"].append(rel)
            continue
        target.mkdir(parents=True, exist_ok=True)
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            # 跳过写入过程的备份（.bak）与临时（.tmp）残留，这些不是数据本身。
            if path.suffix in ('.bak', '.tmp'):
                continue
            dest = target / path.relative_to(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                result["kept"].append(str(dest.relative_to(dist_dir)))
            else:
                shutil.copy2(path, dest)
                result["copied"].append(str(dest.relative_to(dist_dir)))
    for rel in SEED_EMPTY_DIRS:
        (dist_dir / rel).mkdir(parents=True, exist_ok=True)
    return result


def main():
    if not PYTHON.is_file():
        launcher = shutil.which("py")
        if launcher:
            try:
                run([launcher, "-3.11", "-m", "venv", VENV])
            except subprocess.CalledProcessError:
                run([sys.executable, "-m", "venv", VENV])
        else:
            run([sys.executable, "-m", "venv", VENV])
    run([PYTHON, "-m", "pip", "install", "-r", ROOT / "requirements.txt"])
    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix="run_", dir=STAGING_ROOT))
    run([
        PYTHON, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", staging_root, "打单助手V6.spec",
    ])
    sync_program_files(staging_root / PRODUCT_NAME, DIST_DIR)
    result = copy_release_files(ROOT, DIST_DIR)
    种子 = copy_seed_data(ROOT, DIST_DIR)
    print("构建完成：%s" % (DIST_DIR / "打单助手V6.exe"))
    print("新增发布文件：%s" % ("、".join(result["copied"]) or "无"))
    print("保留已有文件：%s" % ("、".join(result["kept"]) or "无"))
    print("词典/示例库：新增 %s，保留 %s" % (
        "、".join(种子["copied"]) or "无", "、".join(种子["kept"]) or "无"))
    if result["missing"]:
        print("警告，源目录缺少：%s" % "、".join(result["missing"]))
    if 种子["missing"]:
        print("提示，未随发布携带：%s" % "、".join(种子["missing"]))


if __name__ == "__main__":
    main()
