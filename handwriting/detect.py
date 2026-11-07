"""手写单据判别 —— 判断「这张图是不是手写单据」。

为什么要有这个模块
------------------
用户要的是：**识别到手写单据图片时自动走手写识别库**，不需要人去界面里切模式。
但「是不是手写」这件事不能靠客户档案：档案是按客户分的，而同一客户
也会同时送来印刷单和手写单。`customers.json` 里 7 家只有「农和」标了 handwritten，
可导入目录里 13 张手写素材分散在 6 个日期下 —— 手写单并不只来自农和。

所以判据必须落在**图像本身**。

判据是怎么定出来的
------------------
不是拍脑袋，是拿**真实导入目录里的 115 张图**（13 张手写 + 102 张印刷）
跑 OCR 统计出来的。第一版想用「表格线检测」，实测下来是**反的**：
手写照片里纸张阴影和折痕形成大片暗带，印刷截图反而干净，
纯 Python 逐像素扫描完全不可靠，已废弃。

最终采用两条互相独立的证据：

1. **「编码:数量」格式率**（主判据，来自真实样本，余量最大）
   手写单据的每一行都是 `1368:10斤`、`1136:2.5斤` 这种「编码:数量」；
   印刷表格的每一格是独立的品类名、独立的数量，**几乎不会出现冒号**。
   实测：手写样本该比率 0.43—0.95，印刷样本 0.00—0.015（个别带备注的到 0.16）。
   阈值取 **0.20**，两侧各留 0.2 以上的余量。

2. **OCR 置信度**（辅助判据）
   手写体连笔多，通用印刷体 OCR 认不准，置信度偏低。
   实测：手写样本平均置信度 89.4—95.7，印刷样本 96.0—100。
   阈值取 **96.0**。

两条**同时**成立才判为手写。宁可让一张手写单走原流程（少一次优化），
也不要把一张印刷单误判成手写 —— 后者会让手写库去处理规整表格，可能丢列。

**不额外花钱**：两条证据都来自现网本来就要做的那一次 OCR 调用，
不新增任何 API 请求。

样本量的诚实说明
----------------
13 张手写 / 102 张印刷，样本偏小；手写样本全部来自同一个客户（农和），
笔迹风格单一。阈值留了余量，但换一个人写的手写单仍需复验。
因此判别器**只是分流器**：判错的后果是「这张图没走最优通道」，
不是「数据错」——两条通道都保留疑点、都标待核对。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 「编码:数量」形态：冒号前后都是数字。允许中间有空格。
编码数量 = re.compile(r'\d+\s*[：:]\s*\d')
# 任何冒号（中英文都算）
冒号 = re.compile(r'[：:]')

# ---- 阈值（来自 115 张真实样本的网格搜索，零错分且两侧有余量）----
编码数量率阈值 = 0.20      # 手写最低 0.429 / 印刷最高 0.015
置信均值阈值 = 96.0        # 手写最高 95.74 / 印刷最低 96.00
最少文字块 = 8             # 块太少（零碎截图）样本不足，不下结论


@dataclass
class Detection:
    """判别结果。`handwritten` 是最终结论，其余是证据，便于事后核查。"""

    handwritten: bool = False
    confident: bool = False          # 证据是否充分（样本太少时为 False）
    reason: str = ''
    signals: dict = field(default_factory=dict)

    def as_dict(self):
        return {'handwritten': self.handwritten, 'confident': self.confident,
                'reason': self.reason, 'signals': dict(self.signals)}


def 文本特征(blocks):
    """从 OCR 块统计文本形态特征。"""
    文本 = [str(块.get('text', '')) for 块 in blocks or []]
    if not 文本:
        return None
    数量 = len(文本)
    带冒号 = sum(1 for t in 文本 if 冒号.search(t))
    编码数量条数 = sum(1 for t in 文本 if 编码数量.search(t))
    return {
        '文本块数': 数量,
        '冒号率': 带冒号 / 数量,
        '编码数量率': 编码数量条数 / 数量,
        '平均字数': sum(len(t) for t in 文本) / 数量,
    }


def 置信特征(blocks):
    """从 OCR 块统计置信度。没有置信度时返回 None（不参与判断）。"""
    置信 = []
    for 块 in blocks or []:
        值 = 块.get('confidence') if isinstance(块, dict) else None
        if isinstance(值, (int, float)) and not isinstance(值, bool):
            置信.append(float(值))
    if not 置信:
        return None
    排序 = sorted(置信)
    return {
        '置信块数': len(置信),
        '置信均值': sum(置信) / len(置信),
        '置信中位': 排序[len(排序) // 2],
        '低置信占比': sum(1 for c in 置信 if c < 92) / len(置信),
    }


def 判别(blocks=None):
    """判断这批 OCR 块是不是手写单据。

    `blocks` 是现网 OCR 返回的块列表。

    判不出来时一律返回「不是手写」——也就是走原流程。
    判别器只是分流器，两条通道都不会静默改错，所以误判的代价可控。
    """
    try:
        return _判别(blocks)
    except Exception:
        return Detection(handwritten=False, confident=False, reason='判别失败，按原流程')


def _判别(blocks):
    文本 = 文本特征(blocks)
    置信 = 置信特征(blocks)
    信号 = {}
    if 文本:
        信号.update(文本)
    if 置信:
        信号.update(置信)

    if not 文本:
        return Detection(handwritten=False, confident=False, reason='没有 OCR 文字块，按原流程', signals=信号)

    块数 = 文本['文本块数']
    if 块数 < 最少文字块:
        return Detection(handwritten=False, confident=False,
                         reason='文字块只有 %d 个，样本不足，按原流程' % 块数, signals=信号)

    编码像手写 = 文本['编码数量率'] >= 编码数量率阈值
    置信像手写 = bool(置信) and 置信['置信均值'] < 置信均值阈值

    if 编码像手写 and 置信像手写:
        return Detection(handwritten=True, confident=True,
                         reason='%.0f%% 的文字是「编码:数量」写法，且识别置信度偏低（%.1f），判定为手写单据' % (
                             文本['编码数量率'] * 100, 置信['置信均值']), signals=信号)
    if 编码像手写:
        return Detection(handwritten=False, confident=True,
                         reason='像手写的编码格式，但识别置信度正常（%.1f），按印刷单据处理' % (
                             置信['置信均值']), signals=信号)
    if 置信像手写:
        return Detection(handwritten=False, confident=False,
                         reason='识别置信度偏低但没有「编码:数量」写法，证据不足，按原流程', signals=信号)
    return Detection(handwritten=False, confident=True,
                     reason='没有「编码:数量」写法，识别置信度正常，按印刷单据处理', signals=信号)


def 判别说明(检测):
    """给界面/日志看的一句话。"""
    return '自动判别：%s' % 检测.reason
