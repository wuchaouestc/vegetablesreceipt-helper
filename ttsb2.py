# -*- coding: utf-8 -*-
r"""
采购单识别 + 标签打印（新版）

流程：图片 -> 腾讯云OCR与DeepSeek图片理解并行 -> 共识/合计核对 -> 位图排版 -> 打印

相比旧版 ttsb.py 的改进：
  1. 输出改成结构化 JSON（品类/编码/斤数/单位/备注 五个字段），不再靠切字符串
  2. 备注两种来源都抓：① 编码后的"备注:xxx" ② 品类里的加工属性（圆的/扁的）
  3. 明确告诉模型"数量列是总数，不要输出"
  4. 一个品类多个客户 -> 拆成多条记录
  5. 打印用新的位图排版（数字大、斤小、备注同号、长名不裁切）
  6. 打印前先出核对清单，确认后再打（可用 AUTO_PRINT 关闭）
  7. OCR 结果本地缓存，反复调提示词不重复花钱

用法：
    python ttsb2.py                 # 识别 -> 打印核对清单 -> 打印标签
    python ttsb2.py --dry           # 只识别和排版预览，不发打印机
    python ttsb2.py --only 电脑单据  # 只处理某个文件夹
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
import time

import 路径
import customer_profiles as CP

HERE = 路径.程序目录()

def _debug_log(msg):
    try:
        import _库.配置 as 设置
        if 设置.取('系统.运行模式') == 'debug':
            log_dir = HERE / 'log'
            log_dir.mkdir(exist_ok=True)
            log_file = log_dir / ('程序_%s.log' % time.strftime('%Y%m%d'))
            with log_file.open('a', encoding='utf-8') as f:
                f.write('%s\t[DEBUG] [TTSB2] %s\n' % (time.strftime('%H:%M:%S'), msg))
    except Exception:
        pass

# ---------------------------------------------------------------------------
# 密钥
# ---------------------------------------------------------------------------


def 取密钥(名):
    """读一个密钥。

    源码运行时就是 `from config import ...` 那两个值；
    **打包成 exe 以后以 exe 旁边那个 config.py 为准** —— 换密钥只要改那个文件、
    重开程序，不用重新打包（也不该为了换个密钥就让父母等我重打一次包）。

    注意顺序：外部 config.py 要先看。打包进 exe 的 config.py 只是空壳，
    `import config` 拿到的会是空串而**不会抛异常**，若先 import 就再也没机会
    回退到外部文件，打包版会一直报"没找到密钥"。
    """
    # 打包后先读 exe 旁边的 config.py
    外 = 路径.读配置()
    if 外 is not None:
        值 = getattr(外, 名, None)
        if 值:
            return 值
    try:
        import config
        值 = getattr(config, 名, None)
        if 值:
            return 值
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------

ocr_cache = HERE / 'ocr_cache'


def _img_hash(path):
    try:
        h = hashlib.md5()
        with open(path, 'rb') as f:
            for 块 in iter(lambda: f.read(65536), b''):
                h.update(块)
        return h.hexdigest()
    except Exception:
        return ''


def _记(话):
    """写 OCR 诊断日志（失败也不影响流程）。"""
    try:
        with (HERE / '识别诊断.log').open('a', encoding='utf-8') as f:
            f.write('%s\t%s\n' % (__import__('time').strftime('%m-%d %H:%M:%S'), 话))
    except Exception:
        pass


def tencent_ocr(image_path, 错误=None):
    """腾讯云 OCR，返回 [{text, x, y}]；带本地缓存。

    `错误` 是个**可选的垃圾桶**（list）：给一句话说明"为什么一条都没返回"。
    ⚠️ 以前「接口报错」和「一个字都没认出来」都只 `return []`，界面上完全
       分不出来 —— 前者是网络/密钥问题（换台电脑也一样），后者是这张图的问题。
       现在把话扔进这个桶里，界面才能跟用户说人话。
    """
    import time
    _debug_log(f"进入 tencent_ocr，图片路径: {image_path}")
    # 本地缓存
    key = _img_hash(image_path)
    cache_file = ocr_cache / (key + '.json')
    if key and cache_file.is_file():
        try:
            with cache_file.open('r', encoding='utf-8') as f:
                数据 = json.load(f)
            块们 = 数据.get('blocks', [])
            # 阶段 3：完整框必须是多边形包围盒。老缓存只有首点，要重新取一次。
            if 块们 and all(块.get('polygon') for 块 in 块们):
                return 块们
        except Exception:
            pass

    sid = 取密钥('TENCENT_SECRET_ID')
    skey = 取密钥('TENCENT_SECRET_KEY')
    if not sid or not skey:
        if 错误 is not None:
            错误.append('没找到 config.py，请先填写密钥')
        return []

    blocks = []
    try:
        # 腾讯云通用印刷体识别（带坐标）
        import hashlib as _h
        import hmac as _hmac
        import urllib.parse as _up
        import urllib.request as _ur
        import random

        with open(image_path, 'rb') as f:
            img_b64 = base64.b64encode(f.read()).decode()

        # 腾讯云 API 签名（TC3-HMAC-SHA256）
        host = 'ocr.tencentcloudapi.com'
        service = 'ocr'
        action = 'GeneralAccurateOCR'
        version = '2018-11-19'
        region = ''
        ts = int(time.time())
        date = time.strftime('%Y-%m-%d', time.gmtime(ts))
        payload = json.dumps({'ImageBase64': img_b64})

        def _sha256(s):
            if isinstance(s, str):
                s = s.encode('utf-8')
            return _h.sha256(s).hexdigest()

        def _hmac_s(k, msg):
            return _hmac.new(k.encode(), msg.encode(), _h.sha256).digest()

        canonical_request = '\n'.join(['POST', '/', '',
                                       'content-type:application/json; charset=utf-8\n'
                                       'host:%s\n' % host,
                                       'content-type;host',
                                       _sha256(payload)])
        credential_scope = '%s/%s/tc3_request' % (date, service)
        string_to_sign = '\n'.join(['TC3-HMAC-SHA256', str(ts), credential_scope,
                                    _sha256(canonical_request)])
        secret_date = _hmac_s('TC3' + skey, date)
        secret_service = _hmac.new(secret_date, service.encode(), _h.sha256).digest()
        secret_signing = _hmac.new(secret_service, b'tc3_request', _h.sha256).digest()
        signature = _hmac.new(secret_signing, string_to_sign.encode(), _h.sha256).hexdigest()
        authorization = ('TC3-HMAC-SHA256 Credential=%s/%s, SignedHeaders=content-type;host, '
                         'Signature=%s' % (sid, credential_scope, signature))

        req = _ur.Request('https://%s' % host, data=payload.encode(),
                          headers={'Authorization': authorization,
                                   'Content-Type': 'application/json; charset=utf-8',
                                   'Host': host, 'X-TC-Action': action,
                                   'X-TC-Version': version, 'X-TC-Timestamp': str(ts)})
        with _ur.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read().decode())
        响应 = resp.get('Response', {})
        if 响应.get('Error'):
            云错误 = 响应['Error']
            raise RuntimeError('%s：%s' % (云错误.get('Code', '腾讯云错误'),
                                           云错误.get('Message', '未知错误')))
        for 项 in 响应.get('TextDetections', []):
            box = 项.get('Polygon', [])
            x = box[0].get('X', 0) if box else 0
            y = box[0].get('Y', 0) if box else 0
            # 阶段 3：完整多边形 + 置信度。旧逻辑只留首点 x/y，
            # 手写辅助通道要靠完整框做行列关联和标题从属。
            多边形 = [{'X': float(点.get('X', 0)), 'Y': float(点.get('Y', 0))}
                      for 点 in box if isinstance(点, dict)]
            置信 = 项.get('Confidence')
            块 = {'text': 项.get('DetectedText', ''), 'x': x, 'y': y, 'polygon': 多边形}
            if isinstance(置信, (int, float)) and not isinstance(置信, bool):
                块['confidence'] = float(置信)
            blocks.append(块)
    except Exception as e:
        if 错误 is not None:
            错误.append('OCR 失败：%r' % e)

    if not blocks and 错误 is not None and not 错误:
        错误.append('一个字都没认出来（可能是图太糊或不是单据）')
        _debug_log("tencent_ocr 结果: 未识别出任何文字块")
    elif blocks:
        _debug_log(f"tencent_ocr 结果: 成功识别出 {len(blocks)} 个文字块")

    # 写缓存
    if key and blocks:
        try:
            ocr_cache.mkdir(exist_ok=True)
            cache_file.write_text(json.dumps({'blocks': blocks}, ensure_ascii=False),
                                  encoding='utf-8')
        except Exception:
            pass
    return blocks


def compress_image(image_path, max_size=1400):
    """把大图缩到最长边 max_size，减小 OCR 和模型的负担。"""
    try:
        from PIL import Image
        im = Image.open(image_path)
        w, h = im.size
        if max(w, h) > max_size:
            比 = max_size / max(w, h)
            im = im.resize((int(w * 比), int(h * 比)), Image.LANCZOS)
        buf = BytesIO()
        im.convert('RGB').save(buf, 'PNG')
        return buf.getvalue()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# DeepSeek
# ---------------------------------------------------------------------------


def deepseek_parse(image_path, blocks=None, effort='off', system_prompt=''):
    """调一次 DeepSeek，返回模型原始输出的 dict（含 records / notes）。

    ``blocks`` 可以省略：此时直接理解图片，因而能与腾讯 OCR 并行执行。
    只有需要 OCR 辅助重试时才传入坐标文本。
    """
    key = 取密钥('DEEPSEEK_API_KEY')
    if not key:
        raise RuntimeError('没找到 DEEPSEEK_API_KEY')

    import urllib.request as _ur

    # 图片转 base64
    with open(image_path, 'rb') as f:
        img_b64 = base64.b64encode(f.read()).decode()

    # OCR 文本（带坐标，按 y 聚行）
    行 = []
    for b in sorted(blocks or [], key=lambda b: (b.get('y', 0), b.get('x', 0))):
        行.append('[%.0f,%.0f]%s' % (b.get('x', 0), b.get('y', 0), b.get('text', '')))
    ocr文本 = '\n'.join(行)

    model = 'deepseek-chat'
    if effort == 'high':
        model = 'deepseek-reasoner'

    if ocr文本:
        用户 = ('下面是腾讯 OCR 独立识别出的文字块（[x,y]是左上角坐标）：\n%s\n\n'
                '请结合图片和 OCR 文本，严格按 system 的格式要求输出 JSON。' % ocr文本)
        _debug_log(f"deepseek_parse 开始，传入了 {len(blocks or [])} 个 OCR 文字块，模型: {model}")
    else:
        用户 = ('请直接读取这张单据图片，严格按 system 的格式要求输出 JSON。\n'
                '本次没有等待其他 OCR 结果；只输出你实际看清的明细，模糊或无法确定的写进 notes。')
        _debug_log(f"deepseek_parse 开始，直接进行图片理解，模型: {model}")

    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': [
                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + img_b64}},
                {'type': 'text', 'text': 用户},
            ]},
        ],
        'temperature': 0.1,
    }
    req = _ur.Request('https://api.deepseek.com/chat/completions',
                      data=json.dumps(payload).encode(),
                      headers={'Authorization': 'Bearer ' + key,
                               'Content-Type': 'application/json'})
    with _ur.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode())
    内容 = resp['choices'][0]['message']['content']

    # 从输出里抠 JSON
    m = re.search(r'\{[\s\S]*\}', 内容)
    if not m:
        _debug_log("deepseek_parse 结果: 未返回有效的 JSON 结构")
        raise RuntimeError('模型没返回 JSON')
    
    结果_json = json.loads(m.group())
    _debug_log(f"deepseek_parse 结果: 返回了 {len(结果_json.get('records', []))} 条记录")
    return 结果_json


# ---------------------------------------------------------------------------
# 错误翻译
# ---------------------------------------------------------------------------

_错误表 = (
    (('authentication', '401', 'invalid api key', 'api key: ****'), True,
     'API key 不对 —— 打开 config.py，检查 DEEPSEEK_API_KEY 有没有抄错、两头有没有多出空格或引号'),
    (('insufficient', 'balance', '402', 'quota', 'arrears'), True,
     'DeepSeek 账号余额不够了 —— 先去充值'),
    (('rate limit', '429', 'too many requests'), False,
     '请求太频繁了 —— 歇一两分钟再来'),
    (('connection', 'timeout', 'timed out', 'ssl', 'proxy', 'getaddrinfo',
      'connection reset', 'temporary failure'), True,
     '连不上 DeepSeek —— 检查网线/无线，或者是不是开了代理把它挡住了'),
    (('model not exist', 'model_not_found', 'does not exist'), True,
     '模型名不对 —— DeepSeek 那边可能把模型改名了'),
)


def 说人话的报错(e):
    """把异常翻成父母看得懂的一句话（认不出来就原样返回）。"""
    s = str(e).lower()
    for 词们, _整批, 话 in _错误表:
        if any(词 in s for 词 in 词们):
            return 话
    return str(e)


def 是整批跑不了的(e):
    """这个错是不是「换一张图也一样会炸」？

    认证失败、余额不足、连不上 —— 后面每一张都会一模一样地再炸一次。
    没必要把 50 张挨个试过去：每张都要先过一遍 OCR（要钱）再等一次网络超时。
    """
    s = str(e).lower()
    for 词们, 整批, _话 in _错误表:
        if 整批 and any(词 in s for 词 in 词们):
            return True
    return False


# ---------------------------------------------------------------------------
# 品类词汇表 & 辅助
# ---------------------------------------------------------------------------

MENU_VOCAB = (
    '去皮小洋葱头', '小洋葱头', '本地去皮干红葱头', '去皮干红葱头', '本地干红葱头',
    '干红葱头', '红葱头肉', '红葱头', '去皮生姜', '大肉姜', '生姜', '老姜肉',
    '老姜', '沙姜', '去皮沙姜', '子姜', '仔姜', '小黄姜', '黄姜', '南姜', '姜肉',
    '手工现剥蒜肉', '手工蒜米', '蒜米', '蒜头', '独蒜', '铁棍淮山', '鲜淮山',
    '去皮淮山', '淮山', '马蹄肉', '去皮沙葛', '沙葛', '红芽芋头仔', '竹蔗', '香茅',
    '鲜茅根', '茅根', '折耳根', '蒜肉', '小米蒜',
)

加工属性 = ('圆的', '扁的', '长的', '短的', '圆', '扁', '长', '短', '手工', '去皮',
            '带冰', '切片', '大块', '小的', '大的')

表头词 = ('序号', '物料名称', '数量', '客户明细', '商品名称', '下单量', '客户编码',
          '下单单位', '采购数量', '商品备注', '备注', '合计', '小计', '分组', '供应商',
          '采购订单', '采购汇总', '默认库房', '未发单', '已发单', '刷新', '显示订单',
          '汇总', '单位', '单价', '金额', '规格', '计划', '详情', '实采', '总价',
          '行', '分类', '鲜肉类', '时间')


def category_candidates_by_position(blocks):
    """按**版面位置**猜哪些字是「品类名」。

    不用词汇表 —— 新品类也能抓到。
    依据：单据里品类列总在序号列右边、数量列左边。
    实测（金勺子真单）品类落在 x≈153~210，序号 x≈107，数量 x≈380。
    粒粒香的品类列也落在 x≈120~320 之间，所以沿用同一区间。
    """
    候选 = []
    for b in blocks or []:
        x = float(b.get('x', 0) or 0)
        t = str(b.get('text', '')).strip()
        if not t:
            continue
        if 120 <= x <= 320 and len(t) <= 12 and not re.search(r'[\d]', t):
            候选.append(t)
    return 候选


def check_coverage(blocks, records):
    """查 AI 有没有把**整段品类**漏掉，返回提示文本。

    两道查法，取并集：
      ① **词汇表法**：拿 MENU_VOCAB 里的品类名去搜 OCR 原文。
      ② **位置法**：按版面位置（品类列）抠出候选品类名。
         —— 这一道不依赖词汇表，**没见过的新品类也能抓到**。

    只在整单条数够多时才提示（>=5 条），避免小单/误认字造成噪音。
    """
    if not records or len(records) < 5:
        return ''
    ocr文本 = ''.join(str(b.get('text', '')) for b in (blocks or []))
    已提取 = {str(r.get('category', '')) for r in records}
    漏 = set()
    # ① 词汇表法
    for 词 in MENU_VOCAB:
        if 词 in ocr文本 and 词 not in 已提取 and not any(词 in c for c in 已提取):
            漏.add(词)
    # ② 位置法
    for 候选 in category_candidates_by_position(blocks):
        if 候选 not in 已提取 and not any(候选 in c for c in 已提取):
            # 别把「备注/分组」这类词误判成品类
            if 候选 not in 表头词 and not re.search(r'[：:＝=]', 候选):
                漏.add(候选)
    if 漏:
        return '⚠️ 下面这些品类可能被漏读了，请核对：%s' % '、'.join(sorted(漏))
    return ''


def _lead(s):
    """字符串开头的连续数字（用于数量匹配）。"""
    m = re.match(r'^\s*(\d+(?:\.\d+)?)', s)
    return m.group(1) if m else ''


def _core(s):
    """去空白、去单位后缀。"""
    return re.sub(r'[\s]', '', str(s))


def raw_weight_values(blocks):
    """从 OCR 原文里抠出所有出现过的斤数/数量数字。

    用途：AI 有时候会把 2.5 写成 25、把 2.5 写成 4 之类的。
    凡是 AI 给的 weight 在原文里找不到的，都标出来让人工看一眼。
    """
    值 = set()
    for b in blocks or []:
        t = str(b.get('text', '')).strip()
        # 允许 4 位小数（金勺子的 434.1.1）和「斤/公斤/kg」后缀
        for m in re.finditer(r'\d{1,3}(?:\.\d{1,4})?', t):
            值.add(m.group())
    return 值


def split_trailing_paren(category):
    """把品类末尾的括号说明拆出来 → (主名, 括号内容)。

    ⚠️ **当前主流程没有调用它**（2026-10-14）。
    它是在做「括号保留 vs 挪进备注」对比时写的工具函数，
    结论是保留括号更好，但这个函数对排障还有用，所以留着。

    `去皮马蹄(荸荠)` → (`去皮马蹄`, `荸荠`)
    `去皮马蹄（荸荠）` → (`去皮马蹄`, `荸荠`)
    `马蹄(去皮)`    → (`马蹄`, `去皮`)
    没有括号说明时原样返回、第二项为空串。

    ⚠️ 只在「括号在**最末尾**」且「括号前还有字」时才拆：
      · `姜肉`、`蒜米（蒜米先生）` 这类拆开后主名不为空，照拆；
      · `(C19欢聚时代每日优选)` 这种**整串就是括号**的（粒粒香详情列）不拆。
    """
    s = str(category).strip()
    m = re.search(r'[（(]([^（）()]*)[）)]\s*$', s)
    if m and s[:m.start()].strip():
        return s[:m.start()].strip(), m.group(1)
    return s, ''


def clean_record(rec, 手写单):
    """规范化一条记录

    手写单：这份单据是不是手写的（由 build_prompt 挑的提示词决定）。
        True  → 保留老行为：认不出来的单位一律按「斤」兜底。
                手写体上的「斤」被认成一个怪字很常见，不兜底就会印错。
        False → 电脑单：认不出来的单位**照原样保留**。
                印刷体上「包/件/箱」就是真写的，改成斤反而错。
    """
    rec = dict(rec)
    rec['category'] = str(rec.get('category', '')).strip()
    rec['customer_code'] = str(rec.get('customer_code', '')).strip()
    rec['weight'] = str(rec.get('weight', '')).strip()
    rec['unit'] = str(rec.get('unit', '')).strip()
    rec['remark'] = str(rec.get('remark', '')).strip()
    # 单位兜底
    if not rec['unit']:
        rec['unit'] = '斤' if 手写单 else ''
    return rec


def is_valid(rec):
    """一条记录是否有效（有品类且有斤数或编码）。"""
    return bool(rec.get('category')) and bool(rec.get('weight') or rec.get('customer_code'))


# ---------------------------------------------------------------------------
# 打印
# ---------------------------------------------------------------------------


def do_print(records, dry=False, customer=None, sender=None):
    """打印标签。

    走「直写 USB + TSPL」通路 —— 这是当前这台打印机唯一可用的通路。
    （经 Windows 驱动提交的任务会进队列、状态显示完成，但机器不动，
      怀疑与重装驱动有关，所以不再使用。）

    传入 customer 时，**先打一张客户名分隔标签**再打单据 —— 这是用户定的规矩：
    每家客户的单据之间夹一张名牌，分单时一眼就知道分界在哪。
    sender 可注入假发送器，测试时不碰真打印机。
    """
    import label_bitmap as LB

    def 发(job_bytes):
        if sender is not None:
            return sender(job_bytes)
        return LB.print_via_usb(job_bytes, None)

    if customer and not dry:
        sep = LB.build_separator_tspl(customer)
        发(sep)
    for r in records:
        job, _img, _info = LB.build_tspl(
            r.get('category', ''), r.get('customer_code', ''),
            r.get('weight', ''), r.get('unit', '斤'), r.get('remark', ''))
        if not dry:
            发(job)
    return len(records)


# ---------------------------------------------------------------------------
# 核对
# ---------------------------------------------------------------------------


def find_totals(blocks, records):
    """从 OCR 里找出像『汇总数量』的数字块（用于核对有没有漏行）

    单据里通常有『数量』列，写着该品类所有客户斤数的合计。
    把所有解析出的斤数加起来，跟这些数字对得上 -> 基本没漏。

    ⚠️ 修过一次（2026-10-14）：原来正则要求**整块都是纯数字**
    （`re.fullmatch(r"\\d{1,3}(\\.\\d{1,2})?", t)`），于是
    『55.50斤』这种最常见的写法**根本匹配不上** —— 南波旺那张单的
    55.5 斤汇总一直看不见，数学核对形同虚设。
    现在允许 4 位小数（金勺子的 434.1.1）和「斤/公斤/kg」后缀，
    并用「这个数字是否当过客户编码」来排掉编码误判。
    """
    编码们 = {str(r.get('customer_code', '')) for r in records}
    总数 = []
    for b in blocks or []:
        t = str(b.get('text', '')).strip()
        m = re.search(r'\d{1,3}(?:\.\d{1,4})?', t)
        if not m:
            continue
        num = m.group()
        if num in 编码们:
            continue
        # 带单位后缀的更像汇总
        if re.search(r'(斤|公斤|kg)', t):
            总数.append(num)
    return 总数


def _文本键(value):
    """用于字段证据匹配的规范化文本，不改变最终打印内容。"""
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or ''))).lower()


def _数值(value):
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def cross_check(records, blocks):
    """按品类核对“客户明细斤数合计 vs 单据数量列”。

    只给对不上账的品类标记，不再把同一条全局警告贴到每一行。合计相符还能
    裁决两遍识别的冲突；没有可靠数量列时，不凭猜测制造“待核对”。
    """
    totals = category_totals_by_category(records, blocks)
    _debug_log(f"cross_check 开始，提取到 {len(totals)} 个品类汇总数量: {totals}")
    if not totals:
        _debug_log("cross_check 结束，没有提取到品类汇总数量")
        return ''
    数量 = {_文本键(k): _数值(v) for k, v in totals.items()}
    分组 = {}
    for r in records:
        分组.setdefault(_文本键(r.get('category')), []).append(r)

    告警 = []
    for 品类键, 行们 in 分组.items():
        if 品类键 not in 数量 or 数量[品类键] is None:
            continue
        目标 = 数量[品类键]
        # 包/件/箱等不同单位不能直接相加为“斤数”。
        单位 = {_文本键(r.get('unit') or '斤') for r in 行们}
        if len(单位) != 1 or next(iter(单位)) not in ('斤', '公斤', 'kg'):
            continue
        数值们 = [_记录斤数(r) for r in 行们]
        if any(v is None for v in 数值们):
            for r, v in zip(行们, 数值们):
                if v is None:
                    r['_uncertain'] = True
                    r['_warn'] = '品类「%s」有无法辨认的斤数' % r.get('category', '')
            告警.append('品类「%s」有无法辨认的斤数，无法完成合计核对' % 行们[0].get('category', ''))
            continue
        合计 = sum(数值们, Decimal(0))
        if 合计 == 目标:
            _debug_log(f"cross_check 校验通过: 品类 [{品类键}] 合计 {合计} == 目标 {目标}")
            for r in 行们:
                r['_total_verified'] = True
                if not r.get('_hedged') and (r.get('_conflict') or r.get('_ocr_missing')):
                    r.pop('_uncertain', None)
                    r.pop('_conflict', None)
                    r.pop('_ocr_missing', None)
                    r['_warn'] = ''
            continue
        话 = '品类「%s」明细合计 %s，与单据数量 %s 不一致' % (
            行们[0].get('category', ''), 合计, 目标)
        _debug_log(f"cross_check 校验失败: {话}")
        告警.append(话)
        for r in 行们:
            r['_uncertain'] = True
            r['_warn'] = 话
    return '\n'.join(告警)


# ---------------------------------------------------------------------------
# 提示词
# ---------------------------------------------------------------------------

PROMPT_PRINTED = '''【输出格式】必须严格返回如下 JSON（不要任何解释文字）：
{
  "records": [
    {"category": "品类", "customer_code": "客户编码", "weight": "斤数",
     "unit": "斤", "remark": "备注"}
  ],
  "notes": ["需要人工核对的内容"]
}

【字段说明】
- category：物料/品类名称。若表格是合并单元格（一个品类跨多行），必须把该品类套用到它覆盖的每一行。
  ⚠️【最容易错的地方】合并单元格的品类名常常是**上下居中**画的 —— 也就是画在它自己那几行明细的**正中间**，
     于是它**第一行明细会出现在这个名字的上方**。这时**绝不能**按「离谁近就归谁」判断，
     否则会把它的第一行错算给上一个品类（真事：生姜本来只有 1 条 0.1，却被塞进了 5 条共 15.5）。
     唯一可靠的判据是**用「数量」列核对**：一个品类所有明细的斤数加起来，必须**正好等于**它的「数量」。
     所以遇到拿不准的行，先看合计能不能凑出某个品类的「数量」，能凑上就归给它 —— 哪怕它画在名字上方。
- customer_code：客户编码，形如 022.2 / 1089B.8 / 305 / C44 / A23.2。
- weight：该客户对应的**明细斤数**，只写数字，例如 0.01 / 8 / 1.5。
- unit：**单位照单据上写的原样填，一个字都不要改**。最常见的是"斤"，
  但电脑单上常有"包/件/箱/瓶/袋/盒/条/只/公斤"等 —— 单据写「6包」就填"包"，
  不要因为"这家通常按斤卖"就换成"斤"。拿不准就填单据上那个字。
- remark：特殊要求，见下方【备注规则】。没有就填空字符串。

【极其重要：不要读错数字】
1. "数量"这一列是**这家供应商的汇总总数**，**绝对不要输出**！
   例：铁棍淮山 数量=24.01，但客户明细是 0.01/8/12/4 —— 要输出的是 0.01、8、12、4，不是 24.01。
2. 必须读取"客户明细"里的每一条，一条客户明细 = 一条记录。
3. 一个品类下有 N 个客户，就必须输出 N 条记录，绝对不能合并、不能跳行。

【备注规则 —— 两种来源都要抓】
来源① 客户编码后面的备注：如 "0.01/斤 (022.2) 备注:7条" → remark = "7条"
      （只取"备注:"后面的内容，不要把"备注"二字输出）
来源② 品类名称里的加工属性：如 "本地去皮干红葱头 扁的" → category = "本地去皮干红葱头"，remark = "扁的"
      常见加工属性：圆的、扁的、长的、短的、手工、去皮、带冰、切片
- 如果备注超过 3 个字（例如"包装切成片的"），照原样填进 remark，程序会自动截断显示。
- 不要把客户名称、分组名称、供应商名称当作备注。
- 品类名称本身包含的括号（如"大肉姜(生姜)"）不算备注。

【其他】
- 严格按从左到右、从上到下的顺序输出。
- 忽略划掉或涂黑的内容。
- 无法确定归属的，放进 notes 说明，不要硬塞进 records。'''

PROMPT_HANDWRITTEN = '''这是一张**手写**采购单，字迹可能潦草。请结合图片和OCR坐标仔细辨认。

【输出格式】必须严格返回如下 JSON（不要任何解释文字）：
{
  "records": [
    {"category": "品类", "customer_code": "客户编码", "weight": "斤数",
     "unit": "斤", "remark": "备注"}
  ],
  "notes": ["需要人工核对的内容"]
}

【手写单据额外注意】
1. 品类是每一列最上方的标题文字，通常不含数字和量词。
2. 每条数据必须归属于它**正上方最近的**品类（看水平位置 x 坐标）。
3. 形近字易错，请特别留意：
   亢皮干芯失 -> 去皮干葱头 ; 药头 -> 蒜头 ; 香矛 -> 香茅
   竹磨 -> 竹蔗 ; 小茴美 -> 小黄姜 ; 马蹄肉 不要认成 乌生净肉
4. 沙姜、独蒜、小黄姜 后面的括号一般是"（加工）"。
5. 去皮干葱头 后面的括号只有"长"或"圆"。
6. 数据水平位置明显偏离所有品类时，放进 notes，不要硬归属。

【字段说明】
- category：品类名；customer_code：客户编码；weight：斤数（只写数字）；
  unit：单位（通常是"斤"，个别蒜米是"包"）；remark：备注，没有就空。
- 每个「编码:斤数」都算一条独立记录，同一个编码出现两次就是两条。
- 小数点照抄：『15:2.5斤』的 weight 是 2.5，不要写成 25。'''


def is_handwritten(profile):
    """供结构补丁使用的统一手写单判断接口。"""
    return CP.is_handwritten(profile)


def prompt_is_handwritten(prompt):
    """这份提示词是不是「手写单」那一版。

    build_prompt 只会拼出两种：PROMPT_PRINTED / PROMPT_HANDWRITTEN
    （后者可能再追加一段客户专属规则，所以用 startswith）。
    认不出来时按「电脑单」算 —— 那个方向更保守：单位照原样留着，
    总比把单据上写的「包」悄悄改成「斤」强。
    """
    return bool(prompt and prompt.startswith(PROMPT_HANDWRITTEN))


# ---------------------------------------------------------------------------
# 提取
# ---------------------------------------------------------------------------


def _clean_response(resp, prompt):
    """清洗一次模型响应，并保留模型主动报告的模糊项。"""
    手写 = prompt_is_handwritten(prompt)
    records = [clean_record(r, 手写) for r in (resp.get('records', []) or [])
               if isinstance(r, dict)]
    notes = [str(n) for n in (resp.get('notes', []) or [])]
    _flag_hedged(records, notes)
    return records, notes


def extract_once(img_path, blocks, effort, prompt):
    """调一次 DeepSeek，返回清洗后的记录列表。"""
    resp = deepseek_parse(img_path, blocks, effort, prompt)
    return _clean_response(resp, prompt)[0]


def _flag_hedged(records, notes):
    """把 AI 自己「拿不准」的那些行标进记录里，供清单显示。

    为什么不能只把 note 打在控制台：家长根本不看控制台，
    而 AI 说的「1365:14 可能为 1 斤或 14 斤」恰恰是**唯一一处**
    它自己承认有风险的地方 —— 丢掉它就等于把风险丢给了分货的人。
    """
    for n in (notes or []):
        s = str(n)
        # 从 notes 里找「编码:斤数」样式的提及
        for r in records:
            code = str(r.get('customer_code', ''))
            if code and code in s:
                r['_hedged'] = True


def _coord(b, name):
    """取 OCR 块的 x/y。dict 和对象两种形态都认，取不到返回 None。"""
    try:
        if isinstance(b, dict):
            return b.get(name)
        return getattr(b, name, None)
    except Exception:
        return None


def _block_text(b):
    try:
        if isinstance(b, dict):
            return str(b.get('text', ''))
        return str(getattr(b, 'text', ''))
    except Exception:
        return ''


def _斤系数(unit):
    单位 = _文本键(unit or '斤')
    if 单位 == '斤':
        return Decimal(1)
    if 单位 in ('公斤', 'kg'):
        return Decimal(2)
    return None


def _记录斤数(r):
    值 = _数值(r.get('weight'))
    系数 = _斤系数(r.get('unit'))
    if 值 is None or 系数 is None:
        return None
    return 值 * 系数


def _纯数量单位(text):
    s = unicodedata.normalize('NFKC', str(text)).strip()
    m = re.fullmatch(r'(\d{1,3}(?:\.\d{1,4})?)\s*(斤|公斤|kg)?', s, re.I)
    return (m.group(1), m.group(2) or '斤') if m else (None, None)


def _pure_number(text):
    """读取整块数量并统一换算成斤，避免把 6 公斤误当成 6 斤。"""
    数字, 单位 = _纯数量单位(text)
    if 数字 is None:
        return None
    值 = _数值(数字) * _斤系数(单位)
    return format(值.normalize(), 'f')


def category_totals_by_category(records, blocks):
    """给每个品类找出单据「数量」列里属于它的那个汇总数。

    版面依据：「数量」永远在品类名**右边**、明细列**左边**，
    而且和品类名在**同一条水平线**上（y 相同）。实测：
        生姜  在 x=129.0 y=141.0  →  它的数量『0.1』在 x=294.5 y=140.5
        去皮生姜 在 x=140.5 y=197.5  →  它的数量『48.5』在 x=295.5 y=197.5
    """
    def norm(s):
        return re.sub(r'\s', '', str(s))

    结果 = {}
    品类 = {}
    for b in blocks or []:
        t = _block_text(b)
        if not t or _pure_number(t):
            continue
        x = _coord(b, 'x')
        y = _coord(b, 'y')
        if x is None or y is None:
            continue
        # 品类名：在品类列（x 120~320），不含数字
        if 120 <= x <= 320 and not re.search(r'\d', t) and len(t) <= 12:
            品类[(round(x), round(y))] = t

    for b in blocks or []:
        t = _pure_number(_block_text(b))
        if t is None:
            continue
        x = _coord(b, 'x')
        y = _coord(b, 'y')
        if x is None or y is None:
            continue
        # 不能仅凭“品类右边有数字”认定它是汇总栏。
        # 必须有位于其右侧的独立客户明细（编码 + 数量/单位），或对应的明确汇总表头。
        汇总表头 = any(
            re.fullmatch(r'(?:汇总数量|合计数量|总数量|数量合计|合计)', _block_text(h).strip())
            and _coord(h, 'x') is not None and abs(_coord(h, 'x') - x) <= 40
            and _coord(h, 'y') is not None and _coord(h, 'y') < y
            for h in blocks or [])
        右侧明细 = any(
            _coord(d, 'x') is not None and _coord(d, 'x') > x + 10
            and re.search(r'\d\s*(?:/\s*)?(?:斤|公斤|kg)', _block_text(d), re.I)
            and (re.search(r'[A-Za-z]\d', _block_text(d))
                 or re.search(r'[（(]\s*[\w.\-]+\s*[）)]', _block_text(d)))
            for d in blocks or [])
        if not (汇总表头 or 右侧明细):
            continue
        # 数量列：在品类名右边（x > 280）
        if x > 280:
            # 找同 y 的品类名
            for (cx, cy), 名 in 品类.items():
                if cx < x and abs(cy - y) <= 8:
                    # 数量列在客户明细列左边：只取最靠左的候选，不能让同行
                    # 后面的客户斤数覆盖真正的品类合计。
                    旧 = 结果.get(名)
                    if 旧 is None or x < 旧[1]:
                        结果[名] = (t, x)
                    break
    _debug_log(f"category_totals_by_category 开始，提取 {len(结果)} 个品类的汇总数量")
    return {名: 值[0] for 名, 值 in 结果.items()}


def fix_category_boundaries(records, blocks, log=None):
    """纠正「品类归属串行」—— 合并单元格的品类名可能**垂直居中**，
    它自己第一行明细就画到了名字**上方**，AI 于是把它算给了上一个品类。
    返回被改归属的记录条数。

    判据（纯算术，三条全中才动手，算不通一个字都不改）：
      ① 这个品类的明细合计 ≠ 它的数量；
      ② 但它的**某个前缀**刚好等于它的数量 —— 断点就在这儿；
      ③ 把余下那些改归下一个品类后，下一个品类的合计**也要刚好**等于它的数量。
    """
    def dec(v):
        try:
            return Decimal(str(v).strip())
        except (InvalidOperation, ValueError, TypeError):
            return None

    def 分组(records):
        组 = []
        for i, r in enumerate(records):
            cat = str(r.get('category') or '').strip()
            if 组 and 组[-1][0] == cat:
                组[-1][2] = i + 1
            else:
                组.append([cat, i, i + 1])
        return 组

    def 合计(a, b):
        """[a,b) 的斤数合计；有一条读不成数字就返回 None（宁可不判）"""
        total = Decimal(0)
        for r in records[a:b]:
            v = _记录斤数(r)
            if v is None:
                return None
            total += v
        return total

    totals = category_totals_by_category(records, blocks)
    if not totals:
        return 0
    changed = 0
    组们 = 分组(records)
    for i in range(len(组们) - 1):
        cat, start, end = 组们[i]
        target = dec(totals.get(cat))
        current = 合计(start, end)
        if target is None or current is None or current == target:
            continue
        # 找前缀断点
        total = Decimal(0)
        split = None
        for j in range(start, end):
            v = _记录斤数(records[j])
            if v is None:
                break
            total += v
            if total == target:
                split = j + 1
                break
            if total > target:
                break
        if split is None or split >= end:
            continue
        next_cat = 组们[i + 1][0]
        # 接收方也必须精确对上
        if 合计(split, end) != dec(totals.get(next_cat)):
            continue
        _debug_log(f"fix_category_boundaries: 修正品类 [{cat}] 的串行行，移交给 [{next_cat}]")
        for r in records[split:end]:
            r['category'] = next_cat
        changed += (end - split)
    return changed


def extract_with_retry(img_path, blocks, effort, prompt):
    """提取一次；**一条都没出来就升级重试**。

    策略：升级到思考模式重跑，谁提取到的条数多用谁。
          只在「0 条但 OCR 明明读到了字」时触发，正常单不会多花一次钱。
    """
    records = extract_once(img_path, blocks, effort, prompt)
    if records or not blocks:
        return records
    # 0 条但 OCR 有字 → 升级思考模式重试
    try:
        再 = extract_once(img_path, blocks, 'high', prompt)
        if len(再) > len(records):
            return 再
    except Exception:
        pass
    return records


def process_image(img_path, effort, prompt, skip_cross_check=False, profile=None):
    """单张图完整流水线：双路并行 → 共识合并 → 品类合计核对。"""
    records, blocks, _notes = recognize_parallel(
        img_path, effort, prompt, careful=(effort == 'high'))
    if not records:
        return []
    fix_category_boundaries(records, blocks)
    cross_check(records, blocks)
    return records


def mark_uncertain(records, profile=None):
    """给可疑记录打上 `_uncertain` 标记，返回给用户看的说明列表。

    对所有客户使用相同的图像疑点标记；客户只控制输出格式。
    """
    说明 = []
    for r in records:
        if r.get('_hedged') or r.get('_suspect'):
            r['_uncertain'] = True
            说明.append('「%s %s」拿不准，请核对' %
                       (r.get('category', ''), r.get('customer_code', '')))
    return 说明


def _rec_key(r):
    return (_文本键(r.get('category')), _文本键(r.get('customer_code')))


def _rec_sig(r):
    return (_rec_key(r), _num_key(r.get('weight')), _文本键(r.get('unit')))


def _num_key(w):
    """把斤数变成可以比对的字符串：`0.50` 和 `0.5` 是一回事。"""
    值 = _数值(w)
    if 值 is None:
        return _文本键(w)
    return format(值.normalize(), 'f')


def _rec_same(a, b):
    """关键字段完全一致才算共识；备注措辞不同不制造误报。"""
    return _rec_sig(a) == _rec_sig(b)


def _字段模糊(r):
    话 = ' '.join(str(r.get(k, '')) for k in ('category', 'customer_code', 'weight'))
    return bool(re.search(r'[?？]|看不清|不确定|拿不准|待核对|unknown', 话, re.I))


def _record_ocr_support(r, blocks):
    """返回品类、编码、斤数是否分别得到独立 OCR 证据。"""
    原文 = _文本键(' '.join(_block_text(b) for b in (blocks or [])))
    数字 = [_数值(v) for v in raw_weight_values(blocks)]
    重量 = _数值(r.get('weight'))
    return {
        'category': bool(_文本键(r.get('category'))) and _文本键(r.get('category')) in 原文,
        'customer_code': not _文本键(r.get('customer_code')) or _文本键(r.get('customer_code')) in 原文,
        'weight': 重量 is not None and any(v == 重量 for v in 数字 if v is not None),
    }


def _单位证据(r, blocks):
    单位 = _文本键(r.get('unit'))
    if not 单位:
        return False
    原文 = _文本键(' '.join(_block_text(b) for b in (blocks or [])))
    return 单位 in 原文


def _接受(r, 方法):
    # 模型主动说“看不清”时，相同答案出现两次仍可能只是重复猜错；
    # 只有独立 OCR 字段证据可以消除这种自报疑点。
    if (r.get('_hedged') or _字段模糊(r)) and 方法 != 'ocr':
        r['_confidence'] = 'medium'
        r['_evidence'] = 方法
        r['_uncertain'] = True
        return r
    r['_confidence'] = 'high'
    r['_evidence'] = 方法
    for 键 in ('_uncertain', '_conflict', '_ocr_missing', '_suspect', '_hedged'):
        r.pop(键, None)
    r['_warn'] = ''
    return r


def _单路证据(r, blocks):
    """单路模型结果只有获得独立 OCR 支持，才直接视为高置信。"""
    支持 = _record_ocr_support(r, blocks)
    if all(支持.values()) and not _字段模糊(r):
        return _接受(r, 'ocr')
    r['_ocr_missing'] = True
    r['_uncertain'] = True
    名 = {'category': '品类', 'customer_code': '客户编码', 'weight': '斤数'}
    缺 = [名[k] for k, v in 支持.items() if not v]
    r['_warn'] = '、'.join(缺) + ' 未得到独立 OCR 确认'
    return r


def merge_consensus(runs, blocks):
    """合并两遍模型结果：共识 → OCR 证据 → 品类合计 → 人工核对。

    使用“已匹配索引”而不是字典覆盖，同一品类同一客户出现两次也不会丢行。
    """
    次 = [list(run or []) for run in runs if run]
    _debug_log(f"merge_consensus 开始合并 {len(次)} 遍模型结果")
    if not 次:
        return [], []
    if len(次) == 1:
        _debug_log("只有单路模型结果，跳过交叉共识对比，直接进入 OCR 证据判定")
        return [_单路证据(dict(r), blocks) for r in 次[0]], []

    第一, 第二 = 次[0], 次[1]
    _debug_log(f"开始两路共识对比，第一路 {len(第一)} 条，第二路 {len(第二)} 条")
    已用 = set()
    结果 = []
    待裁决 = []

    def 找(当前, 精确):
        for j, 候选 in enumerate(第二):
            if j in 已用:
                continue
            if (精确 and _rec_same(当前, 候选)) or (not 精确 and _rec_key(当前) == _rec_key(候选)):
                已用.add(j)
                return 候选
        return None

    def 找同行冲突(当前):
        """品类或编码一个字段不同，仍按同一明细行裁决，避免生成重复标签。"""
        for j, 候选 in enumerate(第二):
            if j in 已用:
                continue
            同编码 = _文本键(当前.get('customer_code')) == _文本键(候选.get('customer_code'))
            同品类 = _文本键(当前.get('category')) == _文本键(候选.get('category'))
            if 同编码 or 同品类:
                已用.add(j)
                return 候选
        return None

    for 行号, 原 in enumerate(第一):
        r = dict(原)
        一致 = 找(r, True)
        if 一致 is not None:
            if 一致.get('_hedged') or _字段模糊(一致):
                r['_hedged'] = True
            结果.append(_接受(r, 'consensus'))
            continue
        冲突 = 找(r, False)
        if 冲突 is None:
            冲突 = 找同行冲突(r)
        if (冲突 is None and len(第一) == len(第二) and 行号 < len(第二)
                and 行号 not in 已用):
            # 两遍条数一致时，最后按原顺序配对其余行；多字段同时读错也只能
            # 形成一条冲突记录，不能各自保留而打印成双份。
            冲突 = 第二[行号]
            已用.add(行号)
        if 冲突 is None:
            结果.append(_单路证据(r, blocks))
            continue
        单位冲突 = _文本键(r.get('unit')) != _文本键(冲突.get('unit'))
        a = (all(_record_ocr_support(r, blocks).values())
             and (not 单位冲突 or _单位证据(r, blocks)))
        b = (all(_record_ocr_support(冲突, blocks).values())
             and (not 单位冲突 or _单位证据(冲突, blocks)))
        if b and not a:
            r = dict(冲突)
            _debug_log(f"两路冲突 [{_rec_key(r)}]，根据 OCR 证据接受第二路")
            结果.append(_接受(r, 'ocr'))
        elif a and not b:
            _debug_log(f"两路冲突 [{_rec_key(r)}]，根据 OCR 证据接受第一路")
            结果.append(_接受(r, 'ocr'))
        else:
            r['_conflict'] = True
            r['_uncertain'] = True
            _debug_log(f"两路冲突 [{_rec_key(r)}]，OCR 无法裁决，加入待裁决列表")
            待裁决.append((len(结果), dict(冲突)))
            结果.append(r)

    # 第二遍多出的行不能静默丢掉；有 OCR 支持可接收，否则保留并提示。
    for j, 原 in enumerate(第二):
        if j not in 已用:
            结果.append(_单路证据(dict(原), blocks))

    # 用“该品类数量列”裁决仍然冲突的斤数。
    totals = {_文本键(k): _数值(v) for k, v in category_totals_by_category(结果, blocks).items()}
    for 索引, 候选 in 待裁决:
        当前 = 结果[索引]
        if (_rec_key(当前) != _rec_key(候选)
                or _文本键(当前.get('unit')) != _文本键(候选.get('unit'))):
            continue
        目标 = totals.get(_文本键(当前.get('category')))
        旧值, 新值 = _记录斤数(当前), _记录斤数(候选)
        if 目标 is None or 旧值 is None or 新值 is None:
            continue
        同组 = [r for r in 结果 if _文本键(r.get('category')) == _文本键(当前.get('category'))]
        组值 = [_记录斤数(r) for r in 同组]
        if any(v is None for v in 组值):
            continue
        合计 = sum(组值, Decimal(0))
        if 合计 == 目标:
            _debug_log(f"品类 [{当前.get('category')}] 数量合计校验通过 (合计: {合计} == 目标: {目标})，接受第一路结果")
            _接受(当前, 'category_total')
        elif 合计 - 旧值 + 新值 == 目标:
            _debug_log(f"品类 [{当前.get('category')}] 数量合计校验通过 (换用新值后: {合计 - 旧值 + 新值} == 目标: {目标})，接受第二路结果")
            结果[索引] = _接受(候选, 'category_total')

    notes = []
    for 索引, 候选 in 待裁决:
        当前 = 结果[索引]
        if 当前.get('_conflict'):
            话 = '「%s %s」两遍结果不一致（%s vs %s），OCR/合计仍无法裁决' % (
                当前.get('category', ''), 当前.get('customer_code', ''),
                当前.get('weight', ''), 候选.get('weight', ''))
            当前['_warn'] = 话
            notes.append(话)
    return 结果, notes


def recognize_parallel(img_path, effort, prompt, careful=False):
    """腾讯 OCR 与 DeepSeek 图片理解并行，返回 ``(records, blocks, notes)``。

    仔细模式同时执行两遍 DeepSeek；只有图片理解完全失败时才用 OCR 文本补提，
    因而正常图片的耗时约等于最慢的一路，而不是两路耗时相加。
    """
    轮数 = 2 if careful else 1
    ocr错误 = []
    模型错误 = []
    次 = []
    notes = []
    with ThreadPoolExecutor(max_workers=轮数 + 1) as 池:
        ocr任务 = 池.submit(tencent_ocr, img_path, ocr错误)
        模型任务 = [池.submit(deepseek_parse, img_path, None, effort, prompt)
                    for _ in range(轮数)]
        blocks = ocr任务.result()
        for 任务 in 模型任务:
            try:
                records, 提示 = _clean_response(任务.result(), prompt)
                notes.extend(提示)
                if records:
                    次.append(records)
            except Exception as e:
                模型错误.append(e)

    if not 次 and blocks:
        # 独立图片理解失败时的保底通道，不影响正常并行快速路径。
        records = extract_with_retry(img_path, blocks, 'high' if careful else effort, prompt)
        if records:
            次.append(records)
            notes.append('图片直接理解失败，已使用 OCR 辅助识别')
    if not 次:
        if 模型错误:
            raise 模型错误[0]
        raise RuntimeError('；'.join(ocr错误) or '两种识别方法都没有提取到有效明细')

    records, 合并提示 = merge_consensus(次, blocks)
    notes.extend(合并提示)
    if not blocks:
        notes.append('腾讯 OCR 未返回文字；本次仅依据模型共识，未做数量列交叉核对')
    
    _debug_log(f"recognize_parallel 完成，最终提取明细: {len(records)} 条")
    return records, blocks, notes


def extract_consensus(img_path, blocks, effort, prompt, rounds=2):
    """兼容旧入口：两遍 OCR 辅助提取并行，单轮失败保留另一轮结果。"""
    次 = []
    错误 = []
    with ThreadPoolExecutor(max_workers=max(1, rounds)) as 池:
        任务 = [池.submit(extract_once, img_path, blocks, effort, prompt) for _ in range(rounds)]
        for 项 in 任务:
            try:
                records = 项.result()
                if records:
                    次.append(records)
            except Exception as e:
                错误.append(e)
    if not 次 and 错误:
        raise 错误[0]
    return merge_consensus(次, blocks)


def build_prompt(profile=None):
    """统一按图片提取；保留参数仅为兼容，客户档案不能改变识别。"""
    return '''请仅根据图片识别采购单据，自行判断印刷表格、逐行明细或手写分栏布局。
返回 JSON：{"records":[{"category":"品类","customer_code":"编码","weight":"数量",
"unit":"单位","remark":"备注"}],"notes":["需要人工核对的内容"]}。
完整提取每一条明细，按图片阅读顺序输出；不得按客户、品类或编码过滤。
相同品类、相同编码出现多次也分别保留，禁止合并或去重。
字段按原图填写，保留小数点和单位（斤、公斤、包、件等），不能根据客户习惯改写。
逐行明细表：每行数量就是该条记录的数量，不能把其中某一行当作整个品类的汇总数。
只有图片明确存在独立的汇总数量列和客户明细列时，才把汇总数量用于核对；
输出客户明细数量，不能把汇总数当成另一条明细。没有汇总栏时不得猜测合计。
合并单元格按真实边框及跨行范围归属，不按品类文字的垂直距离猜测。
手写分栏按各栏标题及编码、数量的位置归属；“1136:2.5斤”拆成编码1136、数量2.5、单位斤。
编码与名称按原图完整保留，备注单独提取；品类自带括号不是备注，重复行也不可遗漏。
忽略明确划掉的内容。不能辨认或归属不确定时保留可辨认字段，并在 notes 指明具体行和疑点。
只根据图片中的证据报告不确定，不使用任何客户预设。'''


def collect_records(only=None):
    """遍历各客户文件夹，识别并套用该客户规则。

    返回 (保留记录, 跳过记录)
    """
    import customer_profiles as CP
    from pathlib import Path
    保留 = []
    跳过 = []
    客户们 = CP.list_customers() if only is None else [only]
    for 客户 in 客户们:
        profile = CP.get_profile(客户)
        folder = 路径.数据目录() / (profile.get('folder') or 客户)
        if not folder.is_dir():
            continue
        prompt = build_prompt(profile)
        effort = 'off'
        for 图 in sorted(folder.glob('*')):
            if 图.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tif', '.tiff'):
                continue
            records = process_image(str(图), effort, prompt,
                                    profile=None)
            # 应用客户规则
            保, 跳 = CP.apply_rules(records, 客户)
            保留.extend(保)
            跳过.extend(跳)
    return 保留, 跳过


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--dry', action='store_true')
    p.add_argument('--only', default=None)
    args = p.parse_args()
    保留, 跳过 = collect_records(args.only)
    print('保留 %d 条，跳过 %d 条' % (len(保留), len(跳过)))
    for r in 保留:
        print('  %s\t%s\t%s%s\t%s' % (r.get('category'), r.get('customer_code'),
                                      r.get('weight'), r.get('unit'), r.get('remark')))
    if not args.dry and 保留:
        do_print(保留, dry=args.dry)
    return 0


if __name__ == '__main__':
    sys.exit(main())
