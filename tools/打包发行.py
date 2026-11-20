# -*- coding: utf-8 -*-
"""把已构建的发布目录打成「绿色版」压缩包（解压即用，无需安装）。

与 tools/打包源码.py 的区别
---------------------------
- 源码包：给开发者，含全部源码与 data，**不含** exe 与密钥。
- 绿色版：给使用者，含 exe、运行时依赖、密钥与初始数据，**不含**源码。

为什么用白名单而不是黑名单
--------------------------
发布目录里混着大量运行期产物（60 MB 诊断日志、42 MB 客户单据副本、旧版回滚 exe）。
用黑名单容易漏，一旦漏进去就是把客户业务数据发出去；白名单只允许列出的条目进包，
新增的东西默认不进。

用法
----
    .venv/Scripts/python.exe tools/打包发行.py              # 版本取 版本.py
    .venv/Scripts/python.exe tools/打包发行.py V6.4         # 临时指定版本号

产物：项目根目录 `打单助手<版本>_绿色版.zip`
"""

from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

根 = Path(__file__).resolve().parents[1]
if str(根) not in sys.path:
    sys.path.insert(0, str(根))
from 版本 import 软件版本 as _软件版本  # noqa: E402  版本号唯一来源

版本 = sys.argv[1] if len(sys.argv) > 1 else 'V' + _软件版本
源 = 根 / 'dist' / '打单助手V6'
包名 = '打单助手%s' % 版本
输出 = 根 / ('%s_绿色版.zip' % 包名)

# —— 白名单：只有列在这里的才进包 ——
允许文件 = (
    '打单助手V6.exe',
    'config_local.py',      # 含真实密钥，发行包必须带上，否则识别调不通
    'settings.json',
    'customers.json',
    '启动V6.bat',
)
允许目录 = (
    '_internal',            # PyInstaller 运行时与内置资源（patches / field_service / handwriting）
    'data',                 # 词典、示例库、评测产物
)
空目录 = ('导入', '打单记录', 'ocr_cache')

# 即使在白名单目录里，这些运行残留也不能进包：
# data 下的 .bak 是词典写入时留的备份，_internal/field_service/log 是服务日志。
排除后缀 = {'.log', '.bak', '.tmp', '.pyc', '.pyo'}
排除目录段 = {'__pycache__', 'log'}


def 该跳过(路径: Path) -> bool:
    if 路径.suffix.lower() in 排除后缀:
        return True
    return any(段 in 排除目录段 for 段 in 路径.parts)

使用说明 = """打单助手 %s
========================================

一、启动
    双击「启动V6.bat」或「打单助手V6.exe」。
    首次使用请在「设置」页选择标签打印机。

二、目录说明
    打单助手V6.exe     主程序
    _internal\\         运行时依赖与内置资源，不可删除
    config_local.py   密钥配置（腾讯云 OCR / DeepSeek），不要外传
    settings.json     应用设置
    customers.json    客户档案
    data\\             词典、示例库、评测产物，会越用越准
    导入\\             单据图片副本
    打单记录\\         打单台账（追加式，不要删）
    ocr_cache\\        OCR 结果缓存，可在设置页清理

三、数据与备份
    全部数据都在本文件夹内，整个文件夹复制走即可迁移。
    升级新版本时，保留 config_local.py、settings.json、customers.json、
    data\\、导入\\、打单记录\\，只替换 exe 与 _internal\\。

四、注意
    1. 本包内含真实密钥，请勿上传网盘或公开渠道。
    2. 需要联网才能识别单据（腾讯云 OCR 与 DeepSeek）。
    3. 首次运行若提示缺少「结构服务」，属正常：该增强需单独安装 Paddle 环境。
""" % 版本


def 收集() -> list[tuple[Path, str]]:
    条目: list[tuple[Path, str]] = []
    for 名 in 允许文件:
        文件 = 源 / 名
        if 文件.is_file():
            条目.append((文件, 名))
    for 目录 in 允许目录:
        基 = 源 / 目录
        if not 基.is_dir():
            continue
        for 路径 in sorted(基.rglob('*')):
            if 路径.is_file() and not 该跳过(路径):
                条目.append((路径, '%s/%s' % (目录, 路径.relative_to(基).as_posix())))
    return 条目


def 发行设置(目标: Path):
    """包内 settings.json 只改一处：运行模式 debug → release。

    出厂包不应带调试级日志（每次识别都写诊断文件，客户目录很快变脏）。
    只改副本，不动 dist 里的原文件。
    """
    原文 = 源 / 'settings.json'
    数据 = json.loads(原文.read_text(encoding='utf-8'))
    系统 = 数据.get('系统')
    if isinstance(系统, dict) and 系统.get('运行模式') == 'debug':
        系统['运行模式'] = 'release'
    目标.write_text(json.dumps(数据, ensure_ascii=False, indent=2), encoding='utf-8')


def main() -> int:
    if not (源 / '打单助手V6.exe').is_file():
        print('错误：发布目录不存在或未构建：%s' % 源)
        return 1

    条目 = 收集()
    缺失 = [n for n in ('打单助手V6.exe', 'config_local.py', 'settings.json') if n not in
            [a for _, a in 条目]]
    if 缺失:
        print('警告：发布目录缺少 %s' % '、'.join(缺失))

    if 输出.exists():
        输出.unlink()

    with tempfile.TemporaryDirectory() as 临时:
        临时说明 = Path(临时) / '使用说明.txt'
        临时说明.write_text(使用说明, encoding='utf-8')
        临时设置 = Path(临时) / 'settings.json'
        发行设置(临时设置)

        with zipfile.ZipFile(输出, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as 包:
            for 路径, 相对 in 条目:
                if 相对 == 'settings.json':
                    continue
                包.write(路径, '%s/%s' % (包名, 相对))
            # 覆盖为发行版设置
            包.write(临时设置, '%s/settings.json' % 包名)
            包.write(临时说明, '%s/使用说明.txt' % 包名)
            # 空目录也要建出来，否则首次写入时路径不存在
            for 目录 in 空目录:
                包.writestr('%s/%s/' % (包名, 目录), '')

    大小 = 输出.stat().st_size / 1024 / 1024
    print('版本：%s' % 版本)
    print('输出：%s' % 输出.name)
    print('大小：%.1f MB' % 大小)
    print('文件：%d 个（不含空目录）' % (len(条目) + 1))
    for 名 in 允许文件:
        print('  含 %s' % 名)
    for 名 in 允许目录:
        print('  含 %s/' % 名)
    return 0


if __name__ == '__main__':
    sys.exit(main())
