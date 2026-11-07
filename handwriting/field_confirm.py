"""字段区域人工核对：把核对结论落成 confirmed 真值标注。

任务书第七节要求：「自动定位只生成草稿，人核对后进入 confirmed」。
本模块负责从「自动定位草稿」+「人工核对结论」生成可评测、可训练的 confirmed 标注。

设计原则（与保守裁决铁律一致）：
  · 人工核对结论**优先**于任何自动推断；
  · 人工只改了值、没改别的字段时，**只**改那一个字段，不连带改数量/编码；
  · 人工明确说「单位都是斤」这类**通则**时，规则只作用在单位这一项；
  · 人工没有表态的区域，**保持草稿原值**并标注来源为「草稿未复核」，
    绝不冒充人工确认；
  · 任何删除都留痕（removed_regions），不悄悄丢数据；
  · 逐条人工结论永远压过通则规则（规则只在人工没表态时兜底）。

核对结论用一份 JSON 描述（人工可读、可复核、可版本化）：

    {
      "267e319e....json": {
        "unit_rule": {                       # 通则：只改「末尾单位」这一个维度
          "to": "斤",                        #   目标单位
          "keep": ["包"],                    #   例外：这些单位保持不变
          "digit_suffix": true               #   末尾单位被 OCR 读成数字时，用目标单位替换最后一位
        },
        "regions": {
          "cat-1": {"transcript": "姜肉", "normalized_value": "姜肉"},
          "unit-5": {"transcript": "185:1斤", "normalized_value": "斤"},
          "cat-2": {"remove": true, "reason": "没有这个区域"},
          "unit-11": {"transcript": "931:0.5斤", "source": "cross_evidence"}
        }
      }
    }

source 取值：
  human           —— 人工逐条明确表态（默认）
  cross_evidence  —— 交叉证据 + 视觉复核推定，人工未逐条表态
  rule            —— 由通则规则兜底推导
"""

from __future__ import annotations

import json
from pathlib import Path

# 单位通则：只改「末尾单位」这一个维度，不碰编码与数量。
_TAIL_UNITS = ('公斤', '千克', 'kg', '斤', '个', '只', '袋', '包', '件', '箱',
               '条', '把', '盒', '支', '瓶', '片', '棵', '枚', '扎', '提',
               '罐', '卷', '对', '双', '打')

# 来源常量
SRC_HUMAN = 'human'
SRC_CROSS = 'cross_evidence'
SRC_RULE = 'rule'
SRC_DRAFT = 'draft_unchanged'


def _split_tail_unit(transcript):
    """把「编码:数量单位」拆成 (前缀, 末尾单位)。

    末尾单位识别不到就返回 (整串, '')——不猜、不填。
    """
    text = (transcript or '').strip()
    for unit in sorted(_TAIL_UNITS, key=len, reverse=True):
        if text.endswith(unit):
            return text[:-len(unit)].rstrip(), unit
    return text, ''


def _rewrite_tail_unit(transcript, target):
    """把末尾单位换成 target，返回新串。

    末尾本来就没有单位时，原样返回（不猜），由调用方决定是否补。
    """
    head, unit = _split_tail_unit(transcript)
    if unit:
        return head + target
    return transcript


def _rewrite_digit_suffix(transcript, target):
    """末尾单位被 OCR 读成了数字（例：'1188:24' 其实是 '1188:2斤'）。

    只在通则显式开启 digit_suffix 时使用：把最后一位数字换成目标单位。
    末位不是数字时原样返回。
    """
    text = (transcript or '').strip()
    if not text or not text[-1].isdigit():
        return transcript
    return text[:-1] + target


def _apply_unit_rule(entry, unit_rule, override):
    """按通则改单位。返回 (新文本, 新归一值, 来源标记) 或 None。

    人工已经给过 transcript / normalized_value 的区域不受通则影响。
    """
    target = unit_rule.get('to')
    if not target:
        return None
    if override and ('transcript' in override or 'normalized_value' in override):
        return None

    keep = tuple(unit_rule.get('keep') or ())
    text = entry.get('transcript', '')
    _head, unit = _split_tail_unit(text)

    if unit:
        if unit in keep or unit == target:
            return None                      # 已经是目标单位或例外单位 → 不动
        new_text = _rewrite_tail_unit(text, target)
    elif unit_rule.get('digit_suffix'):
        new_text = _rewrite_digit_suffix(text, target)
        if new_text == text:
            return None
    else:
        return None

    return new_text, target, SRC_RULE


def apply_confirmations(draft, corrections):
    """把人工核对结论应用到草稿区域上，返回 confirmed 区域列表。

    draft: {'fields': [{region_id, field, bbox, transcript, normalized_value, ...}]}
    corrections: {'unit_rule': {...}, 'regions': {region_id: {...}}}
    """
    fields = list(draft.get('fields') or [])
    regions = dict(corrections.get('regions') or {})
    unit_rule = dict(corrections.get('unit_rule') or {})

    confirmed = []
    removed = []
    for item in fields:
        region_id = item.get('region_id')
        override = regions.get(region_id)
        if override and override.get('remove'):
            removed.append({'region_id': region_id, 'field': item.get('field'),
                            'transcript': item.get('transcript'),
                            'reason': override.get('reason', '人工核对：不存在该区域')})
            continue

        entry = dict(item)
        sources = []
        override_src = (override or {}).get('source') or SRC_HUMAN

        # ① 人工显式纠正：transcript / normalized_value 各自独立生效。
        if override:
            if 'transcript' in override:
                entry['transcript'] = override['transcript']
                sources.append('human_transcript' if override_src == SRC_HUMAN
                               else '%s_transcript' % override_src)
            if 'normalized_value' in override:
                entry['normalized_value'] = override['normalized_value']
                sources.append('human_value' if override_src == SRC_HUMAN
                               else '%s_value' % override_src)

        # ② 单位通则：只作用在 unit 字段；人工已表态的区域跳过。
        if entry.get('field') == 'unit':
            applied = _apply_unit_rule(entry, unit_rule, override)
            if applied:
                new_text, target, src = applied
                entry['transcript'] = new_text
                entry['normalized_value'] = target
                sources.append('%s_transcript' % src)
                sources.append('%s_value' % src)

        # ③ 人工没表态 → 保持草稿原值，明确标注「未经人工复核」。
        if not sources:
            sources.append(SRC_DRAFT)
        entry['source'] = ('human_confirmed' if any(s.startswith('human') for s in sources)
                           else 'cross_evidence' if any(s.startswith(SRC_CROSS) for s in sources)
                           else 'rule_derived' if any(s.startswith(SRC_RULE) for s in sources)
                           else 'draft_unreviewed')
        entry['confirm_sources'] = sources
        entry['confirmed'] = True
        confirmed.append(entry)

    return {'fields': confirmed, 'removed_regions': removed}


def load_corrections(path):
    """读人工核对结论。文件不存在或损坏时返回空字典（不阻断流程）。"""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_confirmed(path, payload):
    """写 confirmed 标注（原子替换）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8')
    tmp.replace(target)
    return target


def confirmed_payload(draft, result, *, image_sha256, image_path, reviewer,
                      confirmed_at, notes=''):
    """组装 confirmed 标注文件内容。"""
    return {
        'schema_version': 1,
        'image_sha256': image_sha256,
        'image_path': image_path,
        'review_status': 'confirmed',
        'reviewer': reviewer,
        'confirmed_at': confirmed_at,
        'source_group': draft.get('source_group'),
        'split': draft.get('split') or 'unassigned',
        'fields': result.get('fields') or [],
        'regions': draft.get('regions') or [],
        'removed_regions': result.get('removed_regions') or [],
        'complete': True,
        'notes': notes or '字段区域人工核对确认（品类 / 单位）。',
    }
