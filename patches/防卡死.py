# -*- coding: utf-8 -*-
"""防卡死补丁 —— 让界面消息泵永远不会因为一条坏消息停摆。

原版 `Worker._pump` 只保护了「取队列」，四个界面回调在 try 外面；
任何一个回调抛异常，`root.after(60, self._pump)` 就不会再执行，
队列里的完成/错误消息永远没人取，界面永久停在「识别中」。

这里做三件事：
  ① 替换 `_pump`：每条消息单独兜底，坏消息写 `界面错误.log` 并继续；循环结束一定重排泵。
  ② 替换 `Worker.run`：保存线程对象，供看门狗判断后台是否已结束。
  ③ App 初始化后挂 Tk / 线程异常落盘钩子，并每秒跑一次看门狗：
     busy 且线程已结束且队列为空连续 3 次 → 复位 busy 并提示看日志。
"""

import queue as _queue
import threading as _threading
import time as _time
import traceback as _traceback
from pathlib import Path as _Path

_根 = _Path(str(globals().get("HERE") or "."))
_错误档 = _根 / "界面错误.log"


def _记错误(来源, 文本):
    try:
        with _错误档.open("a", encoding="utf-8") as f:
            f.write("%s\t[%s]\n%s\n\n" % (_time.strftime("%m-%d %H:%M:%S"), 来源, 文本))
    except Exception:  # noqa: BLE001
        pass


def _线程钩子(args):
    _记错误("线程 %s" % getattr(args.thread, "name", "?"),
            "".join(_traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))


_threading.excepthook = _线程钩子


def _pump(self):
    while True:
        try:
            kind, payload = self.q.get_nowait()
        except _queue.Empty:
            break
        try:
            if kind == "log":
                self.on_log(payload)
            elif kind == "progress":
                self.on_progress(payload)
            elif kind == "done":
                self.on_done(payload)
            elif kind == "error":
                self.on_error(payload)
        except Exception:  # noqa: BLE001
            _记错误("界面回调 %s" % kind, _traceback.format_exc())
            try:
                self.on_log("⚠ 界面更新出错（%s），详情已写进 界面错误.log" % kind)
            except Exception:  # noqa: BLE001
                pass
    try:
        self.app.root.after(60, self._pump)
    except Exception:  # noqa: BLE001
        pass


Worker._pump = _pump


def run(self, fn, *args, **kwargs):
    def target():
        try:
            result = fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            self.q.put(("error", e))
            return
        self.q.put(("done", result))

    thread = _threading.Thread(target=target, daemon=True)
    self._thread = thread
    thread.start()


Worker.run = run


def _看门狗(self):
    try:
        worker = getattr(self, "worker", None)
        thread = getattr(worker, "_thread", None)
        if (getattr(self, "busy", False) and thread is not None
                and not thread.is_alive() and worker.q.empty()):
            self._卡死计数 = getattr(self, "_卡死计数", 0) + 1
            if self._卡死计数 >= 3:
                self._卡死计数 = 0
                _记错误("看门狗", "识别线程已结束但界面未收到结果，已复位 busy")
                try:
                    self.set_busy(False)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self.set_status("识别线程已结束但界面没收到结果，详情见 界面错误.log")
                except Exception:  # noqa: BLE001
                    pass
        else:
            self._卡死计数 = 0
    except Exception:  # noqa: BLE001
        _记错误("看门狗", _traceback.format_exc())
    try:
        self.root.after(1000, self._防卡死看门狗)
    except Exception:  # noqa: BLE001
        pass


App._防卡死看门狗 = _看门狗

_原_init = App.__init__


def __init__(self, root):
    _原_init(self, root)
    try:
        def _报告(et, ev, tb):
            _记错误("Tk 回调", "".join(_traceback.format_exception(et, ev, tb)))
        self.root.report_callback_exception = _报告
    except Exception:  # noqa: BLE001
        pass
    try:
        self.root.after(1000, self._防卡死看门狗)
    except Exception:  # noqa: BLE001
        pass


App.__init__ = __init__
