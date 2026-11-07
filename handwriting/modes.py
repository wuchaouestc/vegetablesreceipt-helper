"""模式切换：原流程 / 手写辅助。

《方案》第五节：「第一版建议每张图片可选择『原流程 / 手写辅助』，批量可统一选择」，
第九节：「提供模式选择；设置页提供增强、词典辅助、局部复核开关，
**首次升级默认仍走原流程**」。

本模块只读配置、不碰界面；界面补丁把选择结果传进来。
"""
from __future__ import annotations

from dataclasses import dataclass, field

MODE_LEGACY = 'legacy'          # 原流程
MODE_ASSISTED = 'assisted'      # 手写辅助
MODE_PER_IMAGE = 'per_image'    # 逐张选择
MODE_AUTO = 'auto'              # 自动判别（合入后的默认）

MODE_LABELS = {MODE_LEGACY: '原流程', MODE_ASSISTED: '手写辅助',
               MODE_PER_IMAGE: '逐张选择', MODE_AUTO: '自动判别'}

# settings.json →「识别」段可调项与默认值。
SETTING_DEFAULTS = {
    # 自动 / 关 / 全开 / 逐张
    # 「自动」＝每张图先判别是不是手写单据，是就走手写识别库，不是走原流程。
    '手写辅助模式': '自动',
    '手写局部复核': True,
    '手写词典辅助': '',
    '手写预算请求数': 8,
    '手写预算秒数': 120,
    '手写标题置信阈值': 90,
    '手写复核区域数': 2,
    # 单据级单位一致性（决策 4）：先在本单内取单位共识，再回填读不出单位的行。
    # 铁律：只回填**读不出单位**的行，绝不改写已读出的值；回填值必须标待核对。
    '手写单位一致性': True,
    '手写单位一致最低行数': 2,
    # 字段识别（独立 rec 模型，观察模式，默认关闭）
    '字段辅助': '关',
    '字段服务地址': 'http://127.0.0.1:18765/recognize_fields',
    '字段区域上限': 32,
    '字段批量大小': 16,
    '字段总时限': 10,
}


@dataclass(frozen=True)
class ModeConfig:
    """一次任务用的模式快照（不可变，后台线程直接用）。"""
    mode: str = MODE_LEGACY
    careful: bool = False
    enhancement: bool = False
    vocabulary_path: str | None = None
    review_enabled: bool = True
    budget_requests: int = 8
    budget_seconds: int = 120
    heading_confidence: float = 90.0
    review_regions: int = 2
    # 单据级单位一致性（决策 4）
    unit_consensus: bool = True
    unit_consensus_min_rows: int = 2
    # 字段识别（观察模式，默认关闭）
    field_assist: bool = False
    field_service: str = 'http://127.0.0.1:18765/recognize_fields'
    field_region_limit: int = 32
    field_batch_size: int = 16
    field_timeout: float = 10.0

    @property
    def assisted(self):
        return self.mode == MODE_ASSISTED

    def as_dict(self):
        return {'mode': self.mode, 'careful': self.careful, 'enhancement': self.enhancement,
                'vocabulary_path': self.vocabulary_path, 'review_enabled': self.review_enabled,
                'budget_requests': self.budget_requests, 'budget_seconds': self.budget_seconds,
                'heading_confidence': self.heading_confidence,
                'review_regions': self.review_regions,
                'unit_consensus': self.unit_consensus,
                'unit_consensus_min_rows': self.unit_consensus_min_rows,
                'field_assist': self.field_assist, 'field_service': self.field_service,
                'field_region_limit': self.field_region_limit,
                'field_batch_size': self.field_batch_size,
                'field_timeout': self.field_timeout}


def _as_bool(value, default):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on', '是', '开')


def _as_int(value, default):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number >= 0 else default


def _as_float(value, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if 0 <= number <= 100 else default


def resolve_mode(settings=None, *, explicit=None, careful=False, per_image=None):
    """从「识别」段配置解析出模式快照。

    `explicit` 是界面上的选择（None 表示按设置走）；
    `per_image` 是逐张选择的结果字典 {图片路径: 'assisted'|'legacy'}。
    """
    settings = settings or {}
    defaults = SETTING_DEFAULTS
    raw = str(settings.get('手写辅助模式', defaults['手写辅助模式']) or defaults['手写辅助模式'])
    if raw in ('全开', '开', '开全部', 'on'):
        mode = MODE_ASSISTED
    elif raw in ('逐张', '逐张选择', 'per_image'):
        mode = MODE_PER_IMAGE
    elif raw in ('自动', 'auto', '自动判别'):
        mode = MODE_AUTO
    else:
        mode = MODE_LEGACY
    if explicit in (MODE_LEGACY, MODE_ASSISTED, MODE_PER_IMAGE, MODE_AUTO):
        mode = explicit
    vocabulary = str(settings.get('手写词典辅助', defaults['手写词典辅助']) or '').strip() or None
    vocabulary = _绝对词典路径(vocabulary)
    return ModeConfig(
        mode=mode,
        careful=bool(careful),
        enhancement=_as_bool(settings.get('温和增强'), False),
        vocabulary_path=vocabulary,
        review_enabled=_as_bool(settings.get('手写局部复核', defaults['手写局部复核']),
                                defaults['手写局部复核']),
        budget_requests=_as_int(settings.get('手写预算请求数', defaults['手写预算请求数']),
                                defaults['手写预算请求数']),
        budget_seconds=_as_int(settings.get('手写预算秒数', defaults['手写预算秒数']),
                               defaults['手写预算秒数']) or defaults['手写预算秒数'],
        heading_confidence=_as_float(settings.get('手写标题置信阈值', defaults['手写标题置信阈值']),
                                     defaults['手写标题置信阈值']),
        review_regions=_as_int(settings.get('手写复核区域数', defaults['手写复核区域数']),
                               defaults['手写复核区域数']),
        unit_consensus=_as_bool(settings.get('手写单位一致性', defaults['手写单位一致性']),
                                defaults['手写单位一致性']),
        unit_consensus_min_rows=_as_int(settings.get('手写单位一致最低行数',
                                                     defaults['手写单位一致最低行数']),
                                        defaults['手写单位一致最低行数']) or 2,
        field_assist=_as_bool(settings.get('字段辅助', defaults['字段辅助']), False),
        field_service=str(settings.get('字段服务地址', defaults['字段服务地址'])
                          or defaults['字段服务地址']).strip() or defaults['字段服务地址'],
        field_region_limit=_as_int(settings.get('字段区域上限', defaults['字段区域上限']),
                                   defaults['字段区域上限']) or defaults['字段区域上限'],
        field_batch_size=_as_int(settings.get('字段批量大小', defaults['字段批量大小']),
                                 defaults['字段批量大小']) or defaults['字段批量大小'],
        field_timeout=_as_int(settings.get('字段总时限', defaults['字段总时限']),
                              defaults['字段总时限']) or defaults['字段总时限'],
    )


def _绝对词典路径(path):
    """把词典路径解析成绝对路径。

    为什么必须有这一步：`settings.json` 里存的是 `data/dictionaries/handwriting.json`
    这种**相对路径**。而 `Path(path).open()` 是相对「当前工作目录」解析的——
    用户从别的目录双击启动、或者打包后从桌面快捷方式启动时，工作目录不是程序目录，
    词典就会静默失效（load_vocabulary 对找不到的文件返回空词典、只留 warning，
    不报错、不影响识别，但「越用越智能」就断了）。

    规则：空路径返回 None（＝不启用词典）；绝对路径原样返回；
    相对路径一律锚定到程序目录。
    """
    if not path or not str(path).strip():
        return None
    from pathlib import Path as _Path
    p = _Path(path)
    if p.is_absolute():
        return str(p)
    try:
        import 路径
        return str((路径.程序目录() / p).resolve())
    except Exception:
        # 拿不到程序目录就退回原样，让下游 load_vocabulary 自己兜底（返回空词典）。
        return path


def mode_for_image(config, image_path, per_image=None):
    """逐张模式下取这张图该走哪条通道；其它模式直接返回配置里的模式。

    「自动」模式在这里返回 MODE_AUTO —— 由调用方（界面补丁）拿 OCR 块
    判别后再决定是 legacy 还是 assisted。判别逻辑在 `handwriting.detect`。
    """
    if config.mode != MODE_PER_IMAGE:
        return config.mode
    if per_image:
        choice = per_image.get(str(image_path))
        if choice in (MODE_LEGACY, MODE_ASSISTED):
            return choice
    return MODE_LEGACY
