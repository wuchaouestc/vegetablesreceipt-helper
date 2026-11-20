# -*- coding: utf-8 -*-
"""把可构建的源码树打包成发布用压缩包（源码 + 配置 + data 数据）。

为什么要脚本而不是右键压缩
--------------------------
1. **排除规则要可复现**：虚拟环境、构建产物、运行期数据、密钥文件一个都不能进包，
   靠人工勾选每次都会漏。
2. **中文文件名要立即可用**：用 zipfile 写入并显式设置 UTF-8 标志位，
   Windows 资源管理器与 7-Zip 解压都不会乱码。
3. **自包含**：解压后直接 `python build.py` 就能出 exe，不依赖本机残留。

用法
----
    .venv/Scripts/python.exe tools/打包源码.py            # 默认 V6.2
    .venv/Scripts/python.exe tools/打包源码.py V6.3       # 指定版本号

产物：项目根目录下 `打单助手<版本>_源码.zip`

包内结构（顶层是一个同名文件夹，避免解压时文件散落一地）：

    打单助手V6.2_源码/
    ├── 启动器.py / 打单助手.py / 路径.py / config.py / ...
    ├── app/ core/ handwriting/ patches/ field_service/（不含 .venv）
    ├── data/          词典、示例库、标注、评测产物
    ├── tests/ tools/ docs/
    └── config_local.example.py   ← 密钥模板（真实 config_local.py 不入包）

刻意排除
--------
- `config_local.py`：含腾讯云与 DeepSeek 真实密钥，绝不外发；用 example 模板代替。
- `.venv/`、`field_service/.venv/`：本机环境，体积 1.2 GB 且与机器绑定。
- `build/`、`dist/`、`__pycache__/`：构建产物与字节码，可由源码重新生成。
- `导入/`、`log/`、`ocr_cache/`、`output/`：用户业务数据与缓存，不属于源码。
- `docs/软件著作权材料/源程序页/`：由源码二次生成的派生物，重复且体积大。
- `*.log`、`*.bak`、`*.tmp`：运行与编辑残留。
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

根 = Path(__file__).resolve().parents[1]
版本 = sys.argv[1] if len(sys.argv) > 1 else 'V6.2'
包名 = '打单助手%s_源码' % 版本
输出 = 根 / ('%s.zip' % 包名)

# 目录名命中即整棵跳过
排除目录 = {
    '.git', '.workbuddy', '.setup', '.venv', '__pycache__',
    'build', 'dist', '导入', 'log', 'ocr_cache', 'output', '清理归档',
}
# 后缀命中即跳过
排除后缀 = {'.pyc', '.pyo', '.log', '.bak', '.tmp', '.zip'}
# 相对根目录的路径命中即跳过（文件或目录）
排除路径 = {
    'config_local.py',                       # 真实密钥
    'field_service/cache',                   # 结构服务模型缓存（体积大且可重建）
    'docs/软件著作权材料/源程序页',           # 由源码二次生成的派生物
}


def 该跳过(路径: Path, 相对: str) -> bool:
    规范 = 相对.replace('\\', '/')
    部件 = 规范.split('/')
    # 目录层级里任一段命中即整棵跳过（.venv/Scripts/x.exe 要按 .venv 判断，不能只看文件名）
    if any(部分 in 排除目录 for 部分 in 部件[:-1]):
        return True
    if 部件[-1] in 排除目录:
        return True
    if any(规范 == p or 规范.startswith(p + '/') for p in 排除路径):
        return True
    return 路径.suffix.lower() in 排除后缀


def 收集():
    条目 = []
    for 路径 in sorted(根.rglob('*')):
        if not 路径.is_file():
            continue
        相对 = 路径.relative_to(根).as_posix()
        if 该跳过(路径, 相对):
            continue
        条目.append((路径, 相对))
    return 条目


def main() -> int:
    条目 = 收集()
    if 输出.exists():
        输出.unlink()

    with zipfile.ZipFile(输出, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as 包:
        for 路径, 相对 in 条目:
            包.write(路径, '%s/%s' % (包名, 相对))

    # 统计：按顶层目录汇总
    汇总: dict[str, list[int]] = {}
    for 路径, 相对 in 条目:
        键 = 相对.split('/')[0] if '/' in 相对 else '(根目录文件)'
        项 = 汇总.setdefault(键, [0, 0])
        项[0] += 1
        项[1] += 路径.stat().st_size

    print('打包版本：%s' % 版本)
    print('输出文件：%s' % 输出.name)
    print('压缩包大小：%.1f MB' % (输出.stat().st_size / 1024 / 1024))
    print('收录文件：%d 个，原始大小 %.1f MB'
          % (len(条目), sum(v[1] for v in 汇总.values()) / 1024 / 1024))
    print()
    print('%-24s %8s %12s' % ('目录', '文件数', '原始大小'))
    print('-' * 48)
    for 键 in sorted(汇总, key=lambda k: -汇总[k][1]):
        数, 大小 = 汇总[键]
        print('%-24s %8d %10.1f MB' % (键, 数, 大小 / 1024 / 1024))
    return 0


if __name__ == '__main__':
    sys.exit(main())
