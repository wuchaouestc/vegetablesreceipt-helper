# -*- coding: utf-8 -*-
"""
客户档案（每家客户一套规则）

为什么要这个文件：
  七家客户的单据格式和打印要求各不相同。把这七套规则集中在这里，
  主程序只管"读规则 -> 套规则"，不要到处写 if。

规则字段说明：
  folder        该客户的单据放哪个文件夹（按客户分单）
  doc_type      "printed" 电脑截图 / "handwritten" 手写照片
  effort        DeepSeek 思考强度："off" 快而省 / "high" 准而慢
  show_code     标签上是否打印客户编码
  code_keep     "full" 原样 / "nochinese" 砍中文尾巴 / "leadcode" 只留编号
  remark_groups "one" 整条 / "split" 按顿号拆组
  skip_items    整条跳过的品类名
  skip_contains 含某词就跳过
  keep_note     是否保留备注
  extra_note    拼进提示词的客户专属说明
"""

from __future__ import annotations
import copy

import json
import os
import re
from pathlib import Path

import 路径

HERE = 路径.程序目录()
客户档 = HERE / 'customers.json'
临时档 = HERE / 'customers.json.tmp'

# 所有客户档案的公共字段
_公共字段 = ('folder', 'doc_type', 'effort', 'show_code', 'code_keep',
             'remark_groups', 'skip_items', 'skip_contains', 'keep_note', 'extra_note')


def normalize(name):
    """去掉空格、全角括号统一，便于比对"""
    return str(name).replace(' ', '').replace('　', '').replace('（', '(').replace('）', ')')


# 内置七家客户档案（customers.json 不存在时种一份）
_内置档案 = {
    '南波旺': {
        'folder': '南波旺', 'doc_type': 'printed', 'effort': 'off',
        'show_code': False, 'code_keep': 'full', 'remark_groups': 'one',
        'skip_items': ['大肉姜(生姜)', '蒜米'], 'skip_contains': [],
        'keep_note': True,
        'extra_note': '这家客户的『客户编码』列里写的是客户名称（如 815/高平/融捷/淀粉/广铁 等），'
                     '不是编码，不要当作编码处理；最终标签不需要这些信息。『采购数量』列才是要打的斤数。'
                     '⚠️ 所有品类都照常提取输出（包括『大肉姜(生姜)』和『蒜米』），'
                     '不要因为任何原因把整段品类跳过不输出 —— 哪些要打印由程序按规则决定。',
        'category_alias': {}, 'category_order': [], 'sort_categories': False,
    },
    '中惠': {
        'folder': '中惠', 'doc_type': 'printed', 'effort': 'off',
        'show_code': True, 'code_keep': 'full', 'remark_groups': 'one',
        'skip_items': [], 'skip_contains': [], 'keep_note': True,
        'extra_note': '表头形如『2026-10-09 采购订单 供应商:03.0008 陈金月蒜姜』。'
                     '客户明细里每条形如『0.5/斤 (1078.4)』，括号里是客户编码。',
        'category_alias': {}, 'category_order': [], 'sort_categories': False,
    },
    '金勺子': {
        'folder': '金勺子', 'doc_type': 'printed', 'effort': 'off',
        'show_code': True, 'code_keep': 'nochinese', 'remark_groups': 'split',
        'skip_items': ['蒜米', '蒜米(蒜米先生)', '蒜米（蒜米先生）'], 'skip_contains': [],
        'keep_note': True,
        'extra_note': '表头形如『供应商采购汇总-按物料汇总 / 未发单 2026-10-09 采购订单 供应商:1103 姜蒜（陈金月）』。'
                     '明细里可能有『分组:xxx』（如『分组:陈凰蔬菜』）和『备注:xxx』两种标注。'
                     '⚠️『分组:』是分拣用的分组，不是客户备注，整条忽略、绝对不要当 remark 输出。'
                     '只需提取『备注:』后面的内容作为备注（只要有备注就一定要提取出来，别漏）。'
                     '⚠️『蒜米』和『蒜头』『手工蒜米』都要照常提取输出（打印时由程序按规则处理），'
                     '不要因为任何原因把整段品类跳过不输出。',
        'category_alias': {}, 'category_order': [], 'sort_categories': False,
    },
    '农和': {
        'folder': '农和', 'doc_type': 'handwritten', 'effort': 'high',
        'show_code': True, 'code_keep': 'full', 'remark_groups': 'one',
        'skip_items': [], 'skip_contains': [], 'keep_note': True, 'unit_fix': True,
        'extra_note': '手写单，竖排两三列，每列顶部是品类名（生姜/姜肉/蒜米/沙姜/竹蔗/毛根/子姜/蒜头 等），'
                     '下方每行是『编码:斤数』，例如『1136:15斤』『1368:10斤』。笔迹潦草，注意形近字；'
                     '划掉/涂黑的内容直接忽略。⚠️ 每个『编码:斤数』都算一条独立记录'
                     '（同一个编码出现两次就是两条）。⚠️ 这张单没有『汇总数量』列，不要去找总数，'
                     '也不要用总数当斤数。⚠️ 单位只有两种：绝大多数是『斤』，个别蒜米会写『包』。'
                     '手写潦草时容易把『斤』看成『个』——那还是『斤』。⚠️ 小数点一定要照抄：'
                     '『15:2.5斤』的 weight 是 2.5，不要写成 25，也不要把冒号当成小数点。',
        'category_alias': {}, 'category_order': [], 'sort_categories': False,
    },
    '嘉宝': {
        'folder': '嘉宝', 'doc_type': 'printed', 'effort': 'off',
        'show_code': True, 'code_keep': 'full', 'remark_groups': 'one',
        'skip_items': [], 'skip_contains': [], 'keep_note': True,
        'extra_note': '表头形如『10.14 周三 3.30之前到货』，表头上面还有一行 `10.14 周三 3.30之前到货`。'
                     '列＝单位编码 | 商品名称 | 规格 | 配送单位 | 预订数量 | 备注。'
                     '『单位编码』就是客户编码（K333/k222/K777）。『预订数量』是要打的斤数，『配送单位』是斤。'
                     '⚠️『规格』列（如『优』『小圆洋葱』）请放进 remark —— 由程序按规则处理'
                     '（『优』会被丢掉，其余留前两位）。',
        'category_alias': {}, 'category_order': [], 'sort_categories': False,
    },
    '晶美': {
        'folder': '晶美', 'doc_type': 'printed', 'effort': 'off',
        'show_code': True, 'code_keep': 'full', 'remark_groups': 'one',
        'skip_items': [], 'skip_contains': [], 'keep_note': True,
        'extra_note': '列＝编码 | 品类 | 数量 | 单位 | 备注（有时没表头）。客户编码有两套风格：'
                     '`A23.2`/`B6.1`/`A22.1` 这种，和 `C1-10`/`C1-9` 这种，两套都原样提取。'
                     '『备注』列请完整提取（如『不要泡过水的』『嫩的粉的』『大块完整新鲜』『大肉姜』'
                     '『去皮，本地，小的』『大块完整新鲜』）。⚠️ 品类名里带『肉』字的'
                     '（姜肉 / 红葱头肉 / 老姜肉 / 手工现剥蒜肉 / 马蹄肉）本身就是『去皮』的意思 '
                     '—— 但这不用你处理，照原样提取，程序会处理。',
        'category_alias': {}, 'category_order': [], 'sort_categories': False,
    },
    '粒粒香': {
        'folder': '粒粒香', 'doc_type': 'printed', 'effort': 'off',
        'show_code': True, 'code_keep': 'leadcode', 'upper_code': True,
        'remark_groups': 'one', 'skip_items': [], 'skip_contains': [], 'keep_note': True,
        'extra_note': '这家有**三种格式**，都要认：\n'
                     '①『采购计划』：表头形如『采购计划 - 2026-10-14 / 供应商：陈金月姜蒜』；'
                     '列＝行 | 鲜肉类/分类 | 计划 | 详情 | 实采 | 单价 | 总价；'
                     '还有分类行『姜蒜类』『干调类』（这两行不是商品，不要输出）。'
                     '『详情』列形如『(C19欢聚时代每日优选) 2斤____』，括号里『C19 + 名称』整体就是客户，'
                     '后面的数字+斤是斤数，下划线忽略。\n'
                     '②『按 C区 分组的领货清单』：橙底分组标题形如『C区13号水务局』，'
                     '下面是 品类 | 数量 | 单位 三列。\n'
                     '③『按 Cx 学校分组的清单』：分组标题形如『C3五中附属初级中学』『C2.五中东晓学校』。\n'
                     '⚠️ 分组的标题就是客户，请把**整条标题**放进 customer_code'
                     '（如『C区13号水务局』『C3五中附属初级中学』『C6五中滨江怡趣校区』）。',
        'category_alias': {
            '生姜': {'生姜': '', '大肉姜(生姜)': '', '新生姜': '', '大生姜': '大'},
            '姜肉': {'老姜肉': '', '去皮生姜': '', '生姜(去皮)': '', '生姜去皮': '', '去皮姜肉': ''},
            '蒜米': {'蒜米': '', '蒜肉': '', '蒜米(蒜米先生)': '', '蒜米(蒜肉)': '', '山东蒜米': ''},
            '马蹄肉': {'马蹄（去皮）': '', '去皮马蹄': '', '马蹄肉': ''},
        },
        'category_order': [], 'sort_categories': True,
    },
}

PROFILES = {}
_别名缓存 = {}


def is_handwritten(profile):
    """这张单是不是手写单 —— **全项目只走这一个判据**。

    ⚠️ 踩过的坑（2026-10-15，值得记一辈子）：
       档案里写的字段是 `doc_type: "handwritten"`，
       但我在三处新功能里顺手写成了 `profile.get("handwritten")`。
       结果 `get()` 返回 None，三个功能**全部静默不生效**：
         · fix_nonghe()      农和的品类/备注纠错 —— 一条都没纠
         · mark_uncertain()  可疑记录打问号     —— 一个问号都没打
         · extract_consensus() 手写单跑两遍投票 —— 一次都没跑
       而程序照常出标签、不报错、不告警，看日志一切正常。

       教训：**判据本身也是一个会出错的地方**。
       所以现在只留这一个函数，别处一律调它，并且都写完整路径
       （`doc_type` 优先，`handwritten` 是历史遗留的短写法，兼容保留）。
    """
    if not isinstance(profile, dict):
        return False
    if profile.get('doc_type') == 'handwritten':
        return True
    return bool(profile.get('handwritten'))


def _刷新派生表():
    _别名缓存.clear()


def 载入档案():
    """把 customers.json 读进 PROFILES；没有就按内置档案种一份"""
    global PROFILES
    try:
        if 客户档.is_file():
            with 客户档.open('r', encoding='utf-8') as f:
                PROFILES = json.load(f)
            return PROFILES
    except Exception:
        pass
    PROFILES = json.loads(json.dumps(_内置档案, ensure_ascii=False))
    保存档案()
    return PROFILES


def 保存档案():
    """把 PROFILES 写回 customers.json。先写临时文件再原子改名"""
    try:
        临时档.write_text(json.dumps(PROFILES, ensure_ascii=False, indent=1),
                          encoding='utf-8')
        os.replace(临时档, 客户档)
        return True
    except Exception:
        return False


def 重新载入():
    """从磁盘重读档案（在别处改过 customers.json 时用）。"""
    _刷新派生表()
    return 载入档案()


def 客户上限():
    """最多几个客户。数字从 settings.json 来，读不到就按 15。"""
    try:
        import app_settings
        return int(app_settings.取('客户.上限', 15))
    except Exception:
        return 15


def 还能加几个():
    return max(0, 客户上限() - len(PROFILES))


def 新建档案(客户名, 模板名, folder):
    """照着某个现有客户复制一份新档案（客户规则是提示词，没法从零填表）。

    返回 (成功吗, 给人看的一句话)。

    ⚠️ 这里**不建文件夹** —— 建文件夹是界面层的事，
       而且文件夹名有非法字符时，界面层报错比这里报错清楚。
    """
    if 客户名 in PROFILES:
        return False, '客户「%s」已经存在' % 客户名
    if 模板名 not in PROFILES:
        return False, '模板客户「%s」不存在' % 模板名
    新档 = json.loads(json.dumps(PROFILES[模板名], ensure_ascii=False))
    新档['folder'] = folder or 客户名
    PROFILES[客户名] = 新档
    保存档案()
    return True, '已新建客户「%s」' % 客户名


def 复制档案(原名, 新名):
    return 新建档案(新名, 原名, 新名)


def 删除档案(客户名):
    """删掉一个客户。

    ⚠️ 只删规则，**绝不碰那个客户文件夹里的图片**。
       父母攒了一年的单据照片，不能因为点了「删除客户」就没了。
    """
    if 客户名 not in PROFILES:
        return False, '没有这个客户'
    del PROFILES[客户名]
    保存档案()
    return True, '已删除客户「%s」' % 客户名


def 改名档案(原名, 新名):
    """改客户名。⚠️ 故意**不动 folder** —— 动了就读不到老图片了。"""
    if 原名 not in PROFILES:
        return False, '没有这个客户'
    if 新名 in PROFILES:
        return False, '客户「%s」已经存在' % 新名
    PROFILES[新名] = PROFILES.pop(原名)
    保存档案()
    return True, '已改名「%s」→「%s」' % (原名, 新名)


def 更新档案(客户名, 新档):
    """整份替换某个客户的规则（设置页的编辑框用）。"""
    if 客户名 not in PROFILES:
        return False, '没有这个客户'
    PROFILES[客户名] = 新档
    保存档案()
    return True, '已更新客户「%s」' % 客户名


def get_profile(customer):
    """取某客户的规则；没有就用默认"""
    return PROFILES.get(customer) or {
        'folder': customer or '其他', 'doc_type': 'printed', 'effort': 'off',
        'show_code': True, 'code_keep': 'full', 'remark_groups': 'one',
        'skip_items': [], 'skip_contains': [], 'keep_note': True,
        'extra_note': '', 'category_alias': {}, 'category_order': [],
        'sort_categories': False,
    }


def should_skip(category, profile):
    """判断某个品类是否整条跳过"""
    cat = normalize(category)
    for 项 in profile.get('skip_items', []):
        if normalize(项) == cat:
            return True
    for 词 in profile.get('skip_contains', []):
        if 词 and 词 in category:
            return True
    return False


def keep_code_digits(code):
    """把客户编码里的**中文尾巴**砍掉，只留前面的编号。

    用户 2026-10-14 晚明确更正过一次：
      『906.东莞看守所』 → 打 906      （「东莞看守所」是名字，不打）
      『1089B.2』        → 打 1089B.2  （字母数字混编的完整编码，必须全打！）

    所以规则是「砍掉第一个中文字符及其后面的东西」，
    而不是「只留纯数字」—— 后者会把 1089B.2 错砍成 1089。
    """
    s = str(code)
    for i, ch in enumerate(s):
        if '\u4e00' <= ch <= '\u9fff':
            return s[:i]
    return s


def keep_lead_code(code):
    """『C3 + 中文名字』这种编码，只留前面的字母数字（粒粒香专用）。

    用户 2026-10-14 定（m03375）：
        『关于Q1：（C3五中附属初级中学、C区13号水务局）只用打出C3和C13。』

    实现要点：不是简单砍掉第一个中文之后的所有东西 ——
    因为编号可能被中文**夹在中间**：
        『C区13号水务局』 → 第一个中文是「区」，砍完只剩 C，那就错了。
    正确做法是**把所有字母数字从头开始连续收集**，中文只是分隔符：
        C区13号水务局        → C13
        C3五中附属初级中学     → C3
        C6五中滨江怡趣校区     → C6
        C2.五中东晓学校       → C2
        C15-1号农技所        → C15-1
        C19欢聚时代每日优选    → C19
        C区14号建管中心       → C14
    """
    结果 = []
    for ch in str(code):
        if ch.isalnum() and ord(ch) < 128 or ch in '.-':
            结果.append(ch)
    return ''.join(结果)


def _别名表(profile):
    """把 {标准名: {写法: 补字}} 摊平成 {归一化写法: (标准名, 补字)}，带缓存。

    缓存键用档案内容本身，所以设置页改过之后缓存自动作废，不用手动清。
    """
    key = json.dumps(profile.get('category_alias', {}), ensure_ascii=False, sort_keys=True)
    if key in _别名缓存:
        return _别名缓存[key]
    表 = {}
    for 标准名, 写法们 in (profile.get('category_alias') or {}).items():
        for 写法, 补字 in 写法们.items():
            表[normalize(写法)] = (标准名, 补字)
    _别名缓存[key] = 表
    return 表


def 归一品类(rec, profile):
    """把这条记录的品类名换成标准名，需要时往备注里补一个字。

    返回值：改之前的品类名（没改就返回空串），调用方可以拿去打日志。

    ⚠️ 必须在 strip_alias_paren 之前调 —— 见上面第 2 条。

    ⚠️ 连带影响（不是 bug，是必然结果，测试里专门盯着）：
       归一成「生姜」之后，这个品类名里就没有「肉」字了，
       所以 prepare_remark 那条「品类带『肉』就把备注里的『去皮』丢掉」
       不会再触发。也就是说「姜肉 + 备注去皮」现在会变成「生姜 + 备注去皮」，
       标签上照样带着「去皮」。信息保住了，没有被悄悄删掉。
    """
    cat = str(rec.get('category', ''))
    表 = _别名表(profile)
    if normalize(cat) in 表:
        标准名, 补字 = 表[normalize(cat)]
        旧 = cat
        rec['category'] = 标准名
        if 补字:
            rec['remark'] = (str(rec.get('remark', '')) + 补字).strip()
        return 旧
    return ''


def 排序记录(records, customer):
    """把同一个品类的记录排到一起（分货时不用来回换货）。

    ⚠️ 设计前提（用户 2026-10-15 原话）：
       「因为这个客户的图片品类排序是乱序的，我希望能做到在程序内能为我自动排版，
         无论生姜先还是蒜米先都可以 但一定是要排好顺序。」
       —— 也就是**只要同品类聚在一起就行，品类之间谁先谁后无所谓**。
       所以默认按「第一次出现的顺序」分组：零配置，也不用维护名单。

    如果档案里另写了 category_order，就按那个固定顺序来；
    没写进名单的品类排在最后，仍然按第一次出现的顺序。

    ⚠️ 必须是**稳定**排序：Python 的 sorted 是稳定的，所以同一个品类内部
       原来的先后（先第 1 张图的、后第 2 张图的，同一张图上从上到下）
       原样保留 —— 排序只负责把品类聚拢，不重排品类里面。

    ⚠️ 只对档案里 sort_categories 为真的客户做。别家单据本来就有序，
       动了反而奇怪。（用户：「只对粒粒香做」。）
    """
    profile = get_profile(customer)
    if not profile.get('sort_categories'):
        return list(records)
    顺序 = profile.get('category_order') or []
    首次 = {}
    for i, r in enumerate(records):
        cat = r.get('category', '')
        if cat not in 首次:
            首次[cat] = i

    def 键(r):
        cat = r.get('category', '')
        if cat in 顺序:
            return (0, 顺序.index(cat))
        return (1, 首次.get(cat, 99999))

    return sorted(records, key=键)


def 生成归一文本(表):
    """把 category_alias 字典变回设置页文本框里的那套文本。"""
    行 = []
    for 标准名, 写法们 in (表 or {}).items():
        for 写法, 补字 in 写法们.items():
            if 补字:
                行.append('%s=%s' % (写法, 补字))
            else:
                行.append(写法)
    return '\n'.join(行)


def 解析归一文本(文本):
    """把设置页那个文本框里的内容解析成 category_alias 字典。

    `写法=补字` 表示归一之后还要往备注里补这个字。
    空行、以 # 开头的行忽略；解析不出来的行直接跳过
    （宁可少归一一条，也不能报个错把整张表吞掉）。
    """
    表 = {}
    for 行 in (文本 or '').splitlines():
        行 = 行.strip()
        if not 行 or 行.startswith('#'):
            continue
        if '=' in 行:
            写法, 补字 = 行.split('=', 1)
            写法, 补字 = 写法.strip(), 补字.strip()
        else:
            写法, 补字 = 行, ''
        if 写法:
            # 无法确定标准名，这里仅作为「写法」的键占位，实际标准名由调用方补
            # 此函数保留原始语义：返回 {标准名: {写法: 补字}}，标准名暂用写法本身
            表.setdefault(写法, {})[写法] = 补字
    return 表


def 归一冲突(表):
    """找出「同一个写法被两个标准名抢」的情况，返回 [(写法, [标准名...])]。"""
    反向 = {}
    for 标准名, 写法们 in (表 or {}).items():
        for 写法 in 写法们:
            反向.setdefault(normalize(写法), set()).add(标准名)
    冲突 = []
    for 写法, 标准们 in 反向.items():
        if len(标准们) > 1:
            冲突.append((写法, sorted(标准们)))
    return 冲突


def strip_alias_paren(category):
    """删掉品类末尾「别名式」的括号，保留「加工方式式」的括号。

    用户要的：`去皮马蹄(荸荠)` → `去皮马蹄`
    必须留的：`生姜(去皮)`、`蒜米(蒜米先生)`、`马蹄(去皮)`、`蒜米(蒜肉)`

    为什么要这么小心：这些名字是**给分货的人看的**。
    `生姜(去皮)` 和 `生姜` 在同一张单上同时存在，删了括号两张标签就一样了，
    分货的人会拿错。
    """
    s = str(category)
    # 保留的「加工方式式」括号词
    保留 = ('去皮', '先生', '蒜肉')
    m = re.search(r'[（(]([^（）()]*)[）)]\s*$', s)
    if m:
        内 = m.group(1).strip()
        if 内 not in 保留:
            return s[:m.start()].strip()
    return s


def strip_de(remark):
    """去掉备注里的「的」。

    用户给的例子：『嫩的粉的』→『嫩、粉』

    实测踩到的两个坑（都在下面处理了）：
      · 『本地，小的』被拆成『本地』+『小』→ 变成『本地，小』，
        丢掉「的」反而更难读。所以**逗号分隔**的短语里，「的」作为结尾时保留。
      · 『嫩的粉的』这种**重复用「的」串词**的写法，才是该拆的，
        拆出来的词用顿号连（用户明确要『嫩、粉』）。
    """
    s = str(remark)
    # 逗号分隔的短语：每段末尾的「的」保留
    if '，' in s or ',' in s:
        段 = re.split(r'[，,]', s)
        return '，'.join(段).replace('，', '，')
    # 重复用「的」串词：拆开用顿号连
    if s.count('的') >= 2:
        词 = [x for x in s.split('的') if x]
        return '、'.join(词)
    # 单个「的」结尾：去掉
    if s.endswith('的'):
        return s[:-1]
    return s


def prepare_remark(remark, category):
    """按用户规则整理备注（在渲染截断之前做）。

    顺序很重要：
      1. 固定替换表（不要泡过水的 → 不泡水）
      2. 品类带「肉」的，把「去皮」整段丢掉（『肉』本身就代表去皮）
      3. 去掉「的」，剩余词用顿号连（嫩的粉的 → 嫩、粉）
      4. 纯装饰性的（优 / - / 空）直接丢掉
    截断（留前 2 字 + …）留到渲染时做，那是排版的事。
    """
    s = str(remark or '').strip()
    # 1. 固定替换表
    替换 = {'不要泡过水的': '不泡水', '大块完整新鲜': '大块新', '完整新鲜': '完整新'}
    for 旧, 新 in 替换.items():
        s = s.replace(旧, 新)
    # 2. 品类带「肉」→ 去「去皮」
    if '肉' in str(category or ''):
        s = re.sub(r'去皮[,，、]?', '', s)
    # 3. 去「的」
    s = strip_de(s)
    # 4. 纯装饰性的丢掉
    装饰 = frozenset({'', '-', '/', '—', '优等', '合格', '优'})
    if s in 装饰:
        return ''
    return s


def make_code_upper(code):
    """把客户编码开头的字母统一成大写（粒粒香专用）。

    用户 2026-10-14 定：原单上手写的小写 `c43` 要打成 `C43`，
    因为同一张单上其他 11 个都是 `C45`/`C47`/`C49` 这种大写，
    只有一个是小写的话，工人核对时容易看错。

    ⚠️ **只动「第一个字母」，后面的一个字符都不碰** —— 这是刻意收窄的：
      · 粒粒香：`c43` → `C43`（要的效果）
      · 嘉宝：`k222` / `K333` / `K777` 是**客户原来的编号体系**，
        大小写混用是原样存在的（不是 OCR 认错），不能跟着改成 `K222`。
    所以这条规则挂在**客户档案**上（`upper_code` 字段），
    而不是做进通用的 simplify_code —— 别的客户没有这个需求。
    """
    s = str(code)
    if not s:
        return s
    return s[0].upper() + s[1:]


def simplify_code(code, profile):
    """按档案的 code_keep 处理客户编码"""
    keep = profile.get('code_keep', 'full')
    if keep == 'nochinese':
        return keep_code_digits(code)
    if keep == 'leadcode':
        return keep_lead_code(code)
    return str(code)


def split_remark_groups(remark):
    """按顿号/逗号把备注拆成若干组（用于金勺子）。

    AI 会把「品类属性」和「备注」用顿号连起来，例如
    `扁的、包装切成片的`。用户定：两组各截前3字 → `扁的` + `包装切…`。
    """
    if not remark:
        return []
    return [x.strip() for x in re.split(r'[、，,]', str(remark)) if x.strip()]


def normalize_unit(rec, profile):
    """就地改正单位（目前只服务农和）。

    三种情况都收口到「斤」或「包」：
      · 空           → 斤（农和的斤数后面一定有单位，空的当漏读）
      · 「包」        → 原样（蒜米的包）
      · 其他任何写法   → 斤（含被读错的「个」）
    """
    u = str(rec.get('unit', '')).strip()
    if not u:
        rec['unit'] = '斤'
    elif u == '包':
        rec['unit'] = '包'
    else:
        rec['unit'] = '斤'
    return rec


def _nh_looks_ocr_garbage(s):
    """这个括号内容像是「OCR 认糊了的字」吗？

    两种情况算：
      · 纯数字/字母（『4』『m』）—— 手写体上不会真的有这种东西
      · 长度 1 且不是常见汉字（『比』『么』）
    宁可多丢：丢掉只是少几个字，打错字会让分货的人拿错货。
    """
    s = str(s)
    if not s:
        return True
    if s.isalnum() and len(s) <= 3:
        return True
    if len(s) == 1 and s not in '姜蒜葱头米肉蔗葛茅根笋瓜豆薯芋菇':
        return True
    return False


def fix_nonghe(records):
    """农和手写单的品类/备注纠错（就地修改）。"""
    for r in records:
        cat = str(r.get('category', ''))
        # 品类名里混进的括号「OCR 垃圾」清掉
        cat = re.sub(r'[（(]([^（）()]*)[）)]', lambda m: '' if _nh_looks_ocr_garbage(m.group(1)) else m.group(0), cat)
        r['category'] = cat.strip()
        # 农和单位归一
        normalize_unit(r, {})
    return records


def apply_rules(records, customer):
    """按客户规则生成格式化副本，不过滤行或修改输入。

    返回 (格式化的记录, 空列表)，保留旧调用接口。

    旧的 skip_items/skip_contains 仅保留配置兼容，不再删除识别结果。
    以下规则只改变输出副本：
      · show_code=False        → 清空客户编码
      · code_keep="nochinese"  → 砍掉编码里的中文尾巴（906.东莞看守所 → 906）
      · code_keep="leadcode"   → 只留编号（C3五中附属初级中学 → C3）
      · remark_groups="split"  → 备注按顿号拆组，小组之间用空格连
      · 所有客户：备注走一遍 prepare_remark（去「的」、去多余的「去皮」等）
    """
    profile = get_profile(customer)
    保留 = []
    跳过 = []
    for 原记录 in records:
        r = copy.deepcopy(原记录)
        # 归一品类（别名 → 标准名）
        归一品类(r, profile)
        # 编码处理
        if not profile.get('show_code', True):
            r['code'] = ''
            r['customer_code'] = ''
        else:
            码 = simplify_code(r.get('customer_code', r.get('code', '')), profile)
            if profile.get('upper_code'):
                码 = make_code_upper(码)
            r['code'] = 码
            r['customer_code'] = 码
        # 备注处理
        r['remark'] = prepare_remark(r.get('remark', ''), r.get('category', ''))
        if profile.get('remark_groups') == 'split':
            组 = split_remark_groups(r.get('remark', ''))
            r['remark'] = 组  # 列表形式，供渲染层逐组截断
        保留.append(r)
    return 保留, 跳过


def format_record(record, customer):
    """只生成显示/打印副本，不删除识别行或改写原始字段和核对状态。"""
    return apply_rules([record], customer)[0][0]


def list_customers():
    """返回所有客户名（用于界面下拉框）"""
    return list(PROFILES.keys())


# 首次 import 时自动载入档案
载入档案()
