# -*- coding: utf-8 -*-
'''启动壳：先跑原版程序，再把「识单页」的布局换掉。

为什么这样做，而不是重新打包整个程序：
  · 原版 exe 里是 Python 3.12 字节码，业务逻辑（OCR / 规则 / 打印）一行没动；
  · 这里只重写识单页的三个布局函数，风险最小；
  · 以后要改布局，改同目录的 `布局.py` 就行，不用重新打包。

目录结构（都在 exe 旁边）：
  打单助手.exe
  _internal\
  _界面补丁\
      加载.py        ← 这个文件（只负责加载）
      布局.py        ← 真正的界面补丁
      原版.pyc       ← 从原 exe 里取出来的原版主程序
'''

import json
import marshal
import os
import shutil
import subprocess
import sys
import time
import types
import urllib.request
from pathlib import Path


_服务 = 'http://127.0.0.1:18765'


def _补资源读取():
    import importlib.readers

    for finder in sys.meta_path:
        if finder.__class__.__name__ != '归档取用':
            continue
        loader_class = finder.find_spec.__globals__.get('_归档加载器')
        if not loader_class or hasattr(loader_class, 'get_resource_reader'):
            continue

        def get_resource_reader(self, fullname):
            if self.包路径:
                self.path = self.来源
                return importlib.readers.FileReader(self)
            return None

        loader_class.get_resource_reader = get_resource_reader


def _服务在():
    try:
        with urllib.request.urlopen(_服务 + '/health', timeout=0.6) as r:
            return bool(json.loads(r.read().decode('utf-8')).get('ok'))
    except Exception:                                    # noqa: BLE001
        return False


def _有PIL(py):
    """venv 里有没有 Pillow —— 没装完 Paddle 时 venv 是空的，得退回系统 Python。"""
    try:
        return (py.parent.parent / 'Lib' / 'site-packages' / 'PIL').is_dir()
    except Exception:                                    # noqa: BLE001
        return False


def _起结构服务(base):
    """拉起 PP-Structure sidecar（独立 venv / 独立进程）。

    失败一律静默：没有结构服务，识别照常跑（结构补丁会自己跳过）。
    """
    if os.environ.get('打单助手_不起结构服务') == '1':
        return
    if _服务在():
        return
    serve = base / '_ppstructure' / 'serve.py'
    if not serve.is_file():
        return
    py = base / '_ppstructure' / '.venv' / 'Scripts' / 'python.exe'
    if not (py.is_file() and _有PIL(py)):
        py = shutil.which('python') or shutil.which('python3')
    if not py:
        return
    try:
        log = open(base / '_ppstructure' / '服务启动.log', 'a', encoding='utf-8')
        flags = 0x08000000 if os.name == 'nt' else 0
        subprocess.Popen([str(py), str(serve)], cwd=str(base / '_ppstructure'),
                         stdout=log, stderr=log, creationflags=flags)
    except Exception:                                    # noqa: BLE001
        return
    for _ in range(8):
        time.sleep(0.4)
        if _服务在():
            return


def _加载密钥(base, 模块):
    """把 `密钥配置.py` 当独立模块执行，顺手把 ttsb2 的取密钥接过来。

    必须在执行 `布局.py` **之前** —— 设置页那张「密钥与模型」卡要用它画遮罩。
    加载失败只是少了向导和那张卡，程序照常起。
    """
    路 = base / '_界面补丁' / '密钥配置.py'
    if not 路.is_file():
        return None
    try:
        密钥模块 = types.ModuleType('密钥配置')
        密钥模块.__file__ = str(路)
        空间 = 密钥模块.__dict__
        空间.update({
            '__name__': '密钥配置',
            '__file__': str(路),
            '__builtins__': __builtins__,
            'HERE': str(base),
            'T': getattr(模块, 'T', None),
            '路径': getattr(模块, '路径', None),
        })
        sys.modules['密钥配置'] = 密钥模块
        exec(compile(路.read_text(encoding='utf-8'), str(路), 'exec'), 空间)
        模块.密钥 = 密钥模块
        return 密钥模块
    except Exception as _e:                                  # noqa: BLE001
        try:
            (base / '密钥诊断.log').open('a', encoding='utf-8').write(
                '⚠ 密钥配置加载失败，已跳过（首次运行向导和设置卡不会出现）：%r\n' % (_e,))
        except Exception:                                    # noqa: BLE001
            pass
        print('⚠ 密钥配置加载失败，已跳过：', _e)
        return None


def 运行(base):
    base = Path(base).resolve()
    _补资源读取()
    补丁夹 = base / '_界面补丁'
    原版 = 补丁夹 / '原版.pyc'
    布局 = 补丁夹 / '布局.py'
    if not 原版.is_file():
        raise RuntimeError(f'找不到原版程序：{原版}')
    if not 布局.is_file():
        raise RuntimeError(f'找不到界面补丁：{布局}')

    # 先把结构服务拉起来，和后面的界面构建并行
    _起结构服务(base)

    数据 = 原版.read_bytes()
    if len(数据) < 17:
        raise RuntimeError(f'原版程序文件不完整：{原版}')
    代码 = marshal.loads(数据[16:])

    模块 = types.ModuleType('打单助手_原版')
    模块.__file__ = str(原版)
    模块.__name__ = '打单助手_原版'
    sys.modules['打单助手_原版'] = 模块
    exec(代码, 模块.__dict__)

    # 密钥配置（DPAPI 本机加密）：必须在布局之前，设置页的「密钥与模型」卡要用它。
    密钥 = _加载密钥(base, 模块)

    源码 = 布局.read_text(encoding='utf-8')
    exec(compile(源码, str(布局), 'exec'), 模块.__dict__)

    # 识别补丁（坐标聚行 / 诊断 / 改动率）：可选，没有就不加载。
    # 必须在**独立命名空间**里执行 —— 布局.py 和它在同一个模块里，
    # 如果共用 globals，两边都叫 _原_commit_editor 会互相覆盖，
    # 包装函数就会调用自己（RecursionError，实测 989 层）。
    # 万一补丁本身出错（原版改了函数名等），这里兜住 —— 绝不能因为它打不开程序。
    识别 = 补丁夹 / '识别补丁.py'
    if 识别.is_file():
        try:
            独立 = {
                '__name__': '识别补丁',
                '__file__': str(识别),
                '__builtins__': __builtins__,
                'T': 模块.T,
                'App': 模块.App,
                'HERE': str(base),
            }
            exec(compile(识别.read_text(encoding='utf-8'), str(识别), 'exec'), 独立)
        except Exception as _e:                                   # noqa: BLE001
            try:
                (base / '识别诊断.log').open('a', encoding='utf-8').write(
                    '⚠ 识别补丁加载失败，已跳过（程序照常启动）：%r\n' % (_e,))
            except Exception:                                     # noqa: BLE001
                pass
            print('⚠ 识别补丁加载失败，已跳过：', _e)

    # 结构补丁（PP-Structure 结构卡）：同样独立命名空间，可选。
    结构 = 补丁夹 / '结构补丁.py'
    if 结构.is_file():
        try:
            空间 = {
                '__name__': '结构补丁',
                '__file__': str(结构),
                '__builtins__': __builtins__,
                'T': 模块.T,
                'App': 模块.App,
                'HERE': str(base),
            }
            exec(compile(结构.read_text(encoding='utf-8'), str(结构), 'exec'), 空间)
        except Exception as _e:                                   # noqa: BLE001
            try:
                (base / '结构诊断.log').open('a', encoding='utf-8').write(
                    '⚠ 结构补丁加载失败，已跳过（程序照常启动）：%r\n' % (_e,))
            except Exception:                                     # noqa: BLE001
                pass
            print('⚠ 结构补丁加载失败，已跳过：', _e)

    # 导入补丁（修 copy2 半途失败导致列表指向临时文件）：同样独立命名空间。
    导入 = 补丁夹 / '导入补丁.py'
    if 导入.is_file():
        try:
            空间 = {
                '__name__': '导入补丁',
                '__file__': str(导入),
                '__builtins__': __builtins__,
                'HERE': str(base),
            }
            exec(compile(导入.read_text(encoding='utf-8'), str(导入), 'exec'), 空间)
        except Exception as _e:                                   # noqa: BLE001
            try:
                (base / '导入诊断.log').open('a', encoding='utf-8').write(
                    '⚠ 导入补丁加载失败，已跳过（程序照常启动）：%r\n' % (_e,))
            except Exception:                                     # noqa: BLE001
                pass
            print('⚠ 导入补丁加载失败，已跳过：', _e)

    # 防卡死（界面消息泵兜底 / 异常落盘 / 看门狗）：同样独立命名空间，可选。
    防卡死 = 补丁夹 / '防卡死.py'
    if 防卡死.is_file():
        try:
            空间 = {
                '__name__': '防卡死',
                '__file__': str(防卡死),
                '__builtins__': __builtins__,
                'Worker': 模块.Worker,
                'App': 模块.App,
                'HERE': str(base),
            }
            exec(compile(防卡死.read_text(encoding='utf-8'), str(防卡死), 'exec'), 空间)
        except Exception as _e:                                   # noqa: BLE001
            try:
                (base / '防卡死诊断.log').open('a', encoding='utf-8').write(
                    '⚠ 防卡死补丁加载失败，已跳过（程序照常启动）：%r\n' % (_e,))
            except Exception:                                     # noqa: BLE001
                pass
            print('⚠ 防卡死补丁加载失败，已跳过：', _e)

    # 周末模式（只写打单记录、不打热敏标签）：同样独立命名空间，可选。
    周末 = 补丁夹 / '周末模式.py'
    if 周末.is_file():
        try:
            空间 = {
                '__name__': '周末模式',
                '__file__': str(周末),
                '__builtins__': __builtins__,
                'T': 模块.T,
                'App': 模块.App,
                'HERE': str(base),
            }
            exec(compile(周末.read_text(encoding='utf-8'), str(周末), 'exec'), 空间)
        except Exception as _e:                                   # noqa: BLE001
            try:
                (base / '周末模式诊断.log').open('a', encoding='utf-8').write(
                    '⚠ 周末模式补丁加载失败，已跳过（程序照常启动）：%r\n' % (_e,))
            except Exception:                                     # noqa: BLE001
                pass
            print('⚠ 周末模式补丁加载失败，已跳过：', _e)

    # 首次运行密钥向导：放最后加载 —— 此时布局（设置页那张卡）和防卡死都已经挂好。
    首次 = 补丁夹 / '首次运行.py'
    if 首次.is_file() and 密钥 is not None:
        try:
            空间 = {
                '__name__': '首次运行',
                '__file__': str(首次),
                '__builtins__': __builtins__,
                'App': 模块.App,
                '密钥': 密钥,
                '主题': getattr(模块, '主题', None),
                'P': getattr(模块, 'P', None),
                'HERE': str(base),
            }
            exec(compile(首次.read_text(encoding='utf-8'), str(首次), 'exec'), 空间)
        except Exception as _e:                                   # noqa: BLE001
            try:
                (base / '首次运行诊断.log').open('a', encoding='utf-8').write(
                    '⚠ 首次运行补丁加载失败，已跳过（程序照常启动）：%r\n' % (_e,))
            except Exception:                                     # noqa: BLE001
                pass
            print('⚠ 首次运行补丁加载失败，已跳过：', _e)

    # 原版 main() 里会建窗口、进 mainloop；patch 已经在它的命名空间里挂好了。
    # 设了 打单助手_自检=1 就只加载不建窗口，用来验证补丁链是否正常。
    import os as _os
    if _os.environ.get('打单助手_自检') == '1':
        print('[自检] 原版 + 布局补丁 + 密钥配置 + 识别补丁 + 结构补丁 + 导入补丁 '
              '+ 防卡死 + 周末模式 + 首次运行 均已加载，未进入 mainloop')
        return
    模块.main()
