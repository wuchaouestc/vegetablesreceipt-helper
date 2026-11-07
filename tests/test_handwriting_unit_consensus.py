"""单据级单位一致性：字形混淆归一 + 共识回填。

对应两项拍板：
  · 决策 1——危险别名「斤→个」不停用，而是重新定位成「字形混淆映射」；
  · 决策 4——实现单据级单位一致性回填，且回填值必须标待核对。

铁律在测试里被逐条钉死：
  1. 单位字形不作裁决依据（冲突行只留线索，不改值）；
  2. 不覆盖任何已读出的值（只回填空单位行）；
  4. 词典只提示、不投票（vocabulary 来源权重为 0）；
  5. 任何改值必须同时标记待核对。
"""
import json
import tempfile
import unittest
from pathlib import Path

from handwriting.unit_consensus import (KNOWN_UNITS, apply_unit_consensus,
                                        collect_unit_votes, decide_consensus,
                                        load_image_unit_prior, load_unit_prior)
from handwriting.vocabulary import (Vocabulary, flag_dangerous_aliases,
                                    unit_confusion_pairs)


def _条目(entry_id, field, canonical, aliases=(), customers=(), source='human_correction',
          enabled=True):
    return {'id': entry_id, 'field': field, 'canonical': canonical,
            'aliases': list(aliases), 'scope': {'customers': list(customers)},
            'source': source, 'confirmed_at': '2026-11-07', 'enabled': enabled}


def _词典(*条目):
    return Vocabulary.from_dict({'schema_version': 1, 'revision': 'test-unit',
                                 'entries': list(条目)})


def _行(unit='', 候选=()):
    """构造一条带证据链候选的记录。unit 是记录上的最终单位字段。"""
    candidates = []
    for source, value in 候选:
        candidates.append({'field': 'unit', 'value': value, 'source': source,
                           'confidence': None, 'evidence': value})
    return {'row_id': 'r', 'category': '沙姜', 'customer_code': '1366',
            'weight': '1', 'unit': unit, 'remark': '',
            '_evidence': {'row_id': 'r', 'candidates': {'unit': candidates},
                          'bbox': None, 'heading_text': '', 'entry_text': ''}}


class 字形混淆映射Tests(unittest.TestCase):
    """决策 1：危险别名不停用，改造成字形混淆映射。"""

    def test_人工确认的单位别名产出映射(self):
        词典 = _词典(_条目('uni-1', 'unit', '斤', ['个']))
        self.assertEqual({'个': '斤'}, unit_confusion_pairs(词典))

    def test_机器学来的别名不产出映射(self):
        # 机器学来的读数规律不能当「字形事实」——只有人工确认才算。
        词典 = _词典(_条目('uni-1', 'unit', '斤', ['个'], source='auto_learned'))
        self.assertEqual({}, unit_confusion_pairs(词典))

    def test_非单位字段不参与(self):
        词典 = _词典(_条目('cat-1', 'category', '沙姜', ['生姜']))
        self.assertEqual({}, unit_confusion_pairs(词典))

    def test_客户作用域不匹配时不产出(self):
        词典 = _词典(_条目('uni-1', 'unit', '斤', ['个'], customers=['客户甲']))
        self.assertEqual({}, unit_confusion_pairs(词典, customer='客户乙'))
        self.assertEqual({'个': '斤'}, unit_confusion_pairs(词典, customer='客户甲'))
        # 条目写了作用域但调用方没给客户 → 不匹配，不能拿别家客户的字形事实。
        self.assertEqual({}, unit_confusion_pairs(词典, customer=None))

    def test_无作用域的词条对所有客户生效(self):
        词典 = _词典(_条目('uni-1', 'unit', '斤', ['个']))
        self.assertEqual({'个': '斤'}, unit_confusion_pairs(词典, customer='客户甲'))
        self.assertEqual({'个': '斤'}, unit_confusion_pairs(词典, customer=None))

    def test_双向别名作废(self):
        # 「斤→个」和「个→斤」同时存在 = 两种字形完全分不开，不能拿来归一。
        词典 = _词典(_条目('uni-1', 'unit', '斤', ['个']),
                     _条目('uni-2', 'unit', '个', ['斤']))
        self.assertEqual({}, unit_confusion_pairs(词典))

    def test_停用的词条不参与(self):
        词典 = _词典(_条目('uni-1', 'unit', '斤', ['个'], enabled=False))
        self.assertEqual({}, unit_confusion_pairs(词典))

    def test_危险别名仍保留审核可见性(self):
        # 决策 1 只改变「怎么用」，不改变「要不要报告」。
        词典 = _词典(_条目('uni-1', 'unit', '斤', ['个']))
        危险 = flag_dangerous_aliases(词典)
        self.assertEqual(1, len(危险))
        self.assertEqual('个', 危险[0]['alias'])
        self.assertEqual('斤', 危险[0]['canonical'])


class 投票与先验Tests(unittest.TestCase):
    def test_同一行多来源只算一行(self):
        记录 = [_行(unit='个', 候选=[('ocr_row', '个'), ('model', '个')])]
        票 = collect_unit_votes(记录)['votes']
        self.assertEqual(1, len(票['个']['rows']))
        self.assertEqual(3.0 + 1.0, 票['个']['weight'])

    def test_词典来源不投票(self):
        # 铁律 4：词典只提示，不参与投票。
        记录 = [_行(unit='斤', 候选=[('vocabulary', '斤')])]
        self.assertEqual({}, collect_unit_votes(记录)['votes'])

    def test_先验剔除人工标记删除的行(self):
        数据 = {'examples': [
            {'customer': '客户甲', 'values': {'unit': '斤', 'category': '沙姜'}},
            {'customer': '客户甲', 'values': {'unit': '斤', 'category': '马蹄肉'}},
            {'customer': '客户甲', 'values': {'unit': '斤', 'category': '马蹄肉'}},
            # 人工标记「没有这行，删除」——它的单位是噪声，不能计入先验。
            {'customer': '客户甲', 'values': {'unit': '个', 'category': '没有这行，删除'}},
            {'customer': '客户甲', 'values': {'unit': '', 'category': '沙姜'}},
        ]}
        with tempfile.TemporaryDirectory() as 目录:
            路径 = Path(目录) / 'confirmed_examples.json'
            路径.write_text(json.dumps(数据, ensure_ascii=False), encoding='utf-8')
            先验 = load_unit_prior(路径, customer='客户甲')
        self.assertTrue(先验['scoped'])
        self.assertEqual({'斤': 3}, 先验['units'])

    def test_客户样本不足时退回全局先验(self):
        数据 = {'examples': [
            {'customer': '客户甲', 'values': {'unit': '斤', 'category': '沙姜'}},
            {'customer': '客户乙', 'values': {'unit': '斤', 'category': '沙姜'}},
            {'customer': '客户乙', 'values': {'unit': '斤', 'category': '沙姜'}},
            {'customer': '客户乙', 'values': {'unit': '斤', 'category': '沙姜'}},
        ]}
        with tempfile.TemporaryDirectory() as 目录:
            路径 = Path(目录) / 'confirmed_examples.json'
            路径.write_text(json.dumps(数据, ensure_ascii=False), encoding='utf-8')
            先验 = load_unit_prior(路径, customer='客户甲')
        self.assertFalse(先验['scoped'])
        self.assertEqual(4, 先验['units']['斤'])

    def test_人工确认的行是最硬的票(self):
        # 人工整行确认过的单位，权重最高、且算硬证据——人已经对着原图核过了。
        记录 = [_行(unit='斤', 候选=[('model', '个')]) for _ in range(3)]
        记录[0]['_user_confirmed'] = True
        票 = collect_unit_votes(记录)['votes']
        self.assertEqual(10.0, 票['斤']['weight'])
        self.assertEqual(1, len(票['斤']['trusted_rows']))
        # 这一行不再看机器候选（没有 model 票混进来）
        self.assertEqual({'user_confirmed': 1}, 票['斤']['sources'])
        self.assertEqual({0}, 票['斤']['rows'])

    def test_本单人工确认单位是最强锚点(self):
        数据 = {'examples': [
            {'image_sha256': 'abc', 'customer': '客户甲',
             'values': {'unit': '斤', 'category': '马蹄肉'}},
            {'image_sha256': 'abc', 'customer': '客户甲',
             'values': {'unit': '斤', 'category': '去皮干葱头'}},
            {'image_sha256': 'other', 'customer': '客户甲',
             'values': {'unit': '个', 'category': '沙姜'}},
        ]}
        with tempfile.TemporaryDirectory() as 目录:
            路径 = Path(目录) / 'confirmed_examples.json'
            路径.write_text(json.dumps(数据, ensure_ascii=False), encoding='utf-8')
            锚点 = load_image_unit_prior(路径, image_sha256='abc')
            空 = load_image_unit_prior(路径, image_sha256='zzz')
        self.assertEqual('斤', 锚点['unit'])
        self.assertEqual(2, 锚点['rows'])
        self.assertIsNone(空['unit'])


class 共识决策Tests(unittest.TestCase):
    def test_原图读数压过大模型错读(self):
        # 目标图实测形态：ocr_row 读出「斤」，model 全读成「个」。
        记录 = [_行(unit='斤', 候选=[('ocr_row', '斤'), ('model', '个')]) for _ in range(3)]
        票 = collect_unit_votes(记录)['votes']
        共识 = decide_consensus(票, confusion={'个': '斤'}, prior={}, min_rows=2)
        self.assertEqual('斤', 共识['unit'])

    def test_折叠护栏_别名直接行数更多时不折叠(self):
        # 本单真的在数「个」：别名直接行数更多 → 不许把「个」掰成「斤」。
        记录 = ([_行(unit='个', 候选=[('ocr_row', '个')]) for _ in range(4)]
                 + [_行(unit='斤', 候选=[('ocr_row', '斤')])])
        票 = collect_unit_votes(记录)['votes']
        共识 = decide_consensus(票, confusion={'个': '斤'}, prior={}, min_rows=2)
        self.assertEqual('个', 共识['unit'])

    def test_票数不足时不给共识(self):
        记录 = [_行(unit='斤', 候选=[('ocr_row', '斤')])]
        票 = collect_unit_votes(记录)['votes']
        共识 = decide_consensus(票, confusion={'个': '斤'}, prior={}, min_rows=2)
        self.assertIsNone(共识['unit'])

    def test_没有硬证据也没有先验时不回填(self):
        记录 = [_行(unit='', 候选=[('model', '个')])]
        票 = collect_unit_votes(记录)['votes']
        共识 = decide_consensus(票, confusion={}, prior={}, min_rows=1)
        self.assertIsNone(共识['unit'])

    def test_客户维度先验可以授权折叠(self):
        # 本单只读出「个」，但客户维度人工先验明确偏向「斤」→ 可以归一。
        记录 = [_行(unit='个', 候选=[('ocr_row', '个')]) for _ in range(3)]
        票 = collect_unit_votes(记录)['votes']
        先验 = {'units': {'斤': 6, '个': 1}, 'scoped': True}
        共识 = decide_consensus(票, confusion={'个': '斤'}, prior=先验, min_rows=2)
        self.assertEqual('斤', 共识['unit'])

    def test_全局先验不能授权折叠(self):
        # 别的客户的历史不该把本单的「个」掰成「斤」。
        记录 = [_行(unit='个', 候选=[('ocr_row', '个')]) for _ in range(3)]
        票 = collect_unit_votes(记录)['votes']
        先验 = {'units': {'斤': 6, '个': 1}, 'scoped': False}
        共识 = decide_consensus(票, confusion={'个': '斤'}, prior=先验, min_rows=2)
        self.assertEqual('个', 共识['unit'])


class 回填Tests(unittest.TestCase):
    def test_只回填空单位行且标待核对(self):
        记录 = [_行(unit='斤', 候选=[('ocr_row', '斤')]) for _ in range(2)]
        记录.append(_行(unit='', 候选=[]))
        新记录, 说明 = apply_unit_consensus(记录, confusion={'个': '斤'}, prior={}, min_rows=2)
        self.assertEqual('斤', 新记录[2]['unit'])
        self.assertTrue(新记录[2]['_uncertain'])
        self.assertTrue(any('一致性' in 原因 for 原因 in 新记录[2]['_review_reasons']))
        self.assertIn('document_consensus', 新记录[2]['_unit_consensus']['source'])
        # 已读出的行不能被动
        self.assertFalse(新记录[0].get('_uncertain'))
        self.assertNotIn('_unit_consensus', 新记录[0])
        self.assertTrue(any('回填' in 文本 for 文本 in 说明))

    def test_不覆盖已读出的单位(self):
        记录 = [_行(unit='斤', 候选=[('ocr_row', '斤')]) for _ in range(2)]
        记录.append(_行(unit='个', 候选=[('ocr_row', '个')]))
        新记录, _ = apply_unit_consensus(记录, confusion={'个': '斤'}, prior={}, min_rows=2)
        self.assertEqual('个', 新记录[2]['unit'])

    def test_人工定过的行不动(self):
        记录 = [_行(unit='斤', 候选=[('ocr_row', '斤')]) for _ in range(2)]
        空行 = _行(unit='', 候选=[])
        空行['_user_confirmed'] = True
        记录.append(空行)
        新记录, _ = apply_unit_consensus(记录, confusion={'个': '斤'}, prior={}, min_rows=2)
        self.assertEqual('', 新记录[2]['unit'])

    def test_字形冲突行只留线索不改值(self):
        # 铁律 1：单位字形不作裁决依据 → 只记观察，不改值、不加疑点。
        记录 = [_行(unit='斤', 候选=[('ocr_row', '斤')]) for _ in range(2)]
        记录.append(_行(unit='个', 候选=[('ocr_row', '个')]))
        新记录, _ = apply_unit_consensus(记录, confusion={'个': '斤'}, prior={}, min_rows=2)
        self.assertEqual('个', 新记录[2]['unit'])
        self.assertFalse(新记录[2].get('_uncertain'))
        self.assertTrue(any('字形冲突' in 线索 for 线索 in 新记录[2]['_observations']))

    def test_无混淆映射也无先验时不动任何行(self):
        记录 = [_行(unit='', 候选=[]) for _ in range(3)]
        新记录, 说明 = apply_unit_consensus(记录, confusion={}, prior={}, min_rows=2)
        self.assertEqual(['', '', ''], [r['unit'] for r in 新记录])
        self.assertTrue(any('未生效' in 文本 for 文本 in 说明))

    def test_原记录不被就地修改(self):
        记录 = [_行(unit='斤', 候选=[('ocr_row', '斤')]) for _ in range(2)]
        记录.append(_行(unit='', 候选=[]))
        apply_unit_consensus(记录, confusion={'个': '斤'}, prior={}, min_rows=2)
        self.assertEqual('', 记录[2]['unit'])

    def test_本单人工确认锚点覆盖投票结果(self):
        # 人已经在这张单上确认过单位了 → 直接按它对齐，不再跟机器读数争。
        记录 = [_行(unit='个', 候选=[('ocr_row', '个')]) for _ in range(3)]
        记录.append(_行(unit='', 候选=[]))
        锚点 = {'unit': '斤', 'rows': 4, 'scoped': True}
        新记录, 说明 = apply_unit_consensus(记录, confusion={'个': '斤'}, prior={},
                                          image_prior=锚点, min_rows=2)
        self.assertEqual('斤', 新记录[3]['unit'])
        self.assertTrue(新记录[3]['_uncertain'])
        self.assertTrue(any('人工确认' in 文本 for 文本 in 说明))

    def test_行自身有原图硬证据时优先用自身的(self):
        # 这一行原图上就写着「斤」→ 用自己的读数，比全单统计更贴近事实。
        记录 = [_行(unit='斤', 候选=[('ocr_row', '斤')]) for _ in range(2)]
        空行 = _行(unit='', 候选=[('ocr_row', '斤')])
        记录.append(空行)
        新记录, _ = apply_unit_consensus(记录, confusion={'个': '斤'}, prior={}, min_rows=2)
        self.assertEqual('斤', 新记录[2]['unit'])
        self.assertEqual('本行原图读数', 新记录[2]['_unit_consensus']['basis'])


def new_uncertain(record):
    return bool(record.get('_uncertain'))


if __name__ == '__main__':
    unittest.main()
