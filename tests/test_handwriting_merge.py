"""合入测试：手写单据判别、自动分流模式、记录契约对齐。

背景：独立手写识别库要合入现网主流程，判据是「这张图是不是手写单据」。
判别用的是现网本来就要做的那次 OCR 调用，不额外花钱。
"""
import unittest

from handwriting.detect import (Detection, 判别, 判别说明, 文本特征, 置信特征)
from handwriting.modes import (MODE_ASSISTED, MODE_AUTO, MODE_LEGACY, MODE_PER_IMAGE,
                               mode_for_image, resolve_mode, _绝对词典路径)
from handwriting.pipeline import 规整记录


def block(text, confidence=98.0):
    """默认置信度取 98：印刷表格 OCR 的真实水平（实测 96—100）。"""
    return {'text': text, 'confidence': confidence}


def 手写块():
    """一张典型手写单的 OCR 结果：标题 + 「编码:数量」明细。"""
    return ([block('生姜'), block('沙姜'), block('1136:15斤', 88.0), block('1368:10个', 86.0),
             block('1368:10斤', 90.0), block('1366:3个', 89.0), block('1188:24', 87.0),
             block('935:6个', 91.0), block('933:4个', 88.0), block('15:3个', 89.0)])


def 印刷块():
    """一张典型印刷表格的 OCR 结果：独立单元格，没有「编码:数量」写法。"""
    return ([block('采购计划 - 2026-10-19'), block('供应商：陈金月姜蒜'), block('行'),
             block('鲜肉类/分类'), block('计划'), block('详情'), block('实采'), block('单价'),
             block('大肉姜(生姜)'), block('6斤'), block('(C48) 2斤'),
             block('红葱头'), block('0.5斤'), block('(C42) 0.5斤'),
             block('姜肉'), block('3斤'), block('(C19-2众聚时代小顿蒸蟹) 3斤'),
             block('南美'), block('0.5斤'), block('去皮姜肉'), block('6斤'),
             block('去皮生姜'), block('2斤'), block('蒜米'), block('1包')])


class DetectFeatureTests(unittest.TestCase):
    def test_text_features_count_colon_pattern(self):
        特征 = 文本特征(手写块())
        self.assertEqual(10, 特征['文本块数'])
        self.assertGreater(特征['编码数量率'], 0.5)
        self.assertLess(文本特征(印刷块())['编码数量率'], 0.1)

    def test_confidence_features(self):
        特征 = 置信特征(手写块())
        self.assertEqual(10, 特征['置信块数'])
        self.assertLess(特征['置信均值'], 92)
        self.assertGreater(特征['低置信占比'], 0.3)

    def test_confidence_absent_returns_none(self):
        块 = [{'text': '生姜'}, {'text': '1136:15斤'}]
        self.assertIsNone(置信特征(块))

    def test_empty_blocks_returns_none(self):
        self.assertIsNone(文本特征([]))
        self.assertIsNone(置信特征([]))


class DetectTests(unittest.TestCase):
    def test_handwriting_detected(self):
        结果 = 判别(手写块())
        self.assertTrue(结果.handwritten)
        self.assertTrue(结果.confident)
        self.assertIn('编码:数量', 结果.reason)

    def test_printed_not_detected(self):
        结果 = 判别(印刷块())
        self.assertFalse(结果.handwritten)
        self.assertTrue(结果.confident)
        self.assertIn('印刷', 结果.reason)

    def test_colon_pattern_but_high_confidence_is_printed(self):
        """像手写的格式但识别得很准 —— 按印刷处理（保守方向）。"""
        块 = [block('1136:15斤', 99.0), block('1368:10个', 99.0), block('1366:3个', 99.0),
               block('1188:24', 99.0), block('935:6个', 99.0), block('933:4个', 99.0),
               block('15:3个', 99.0), block('25:24', 99.0), block('1136:1斤', 99.0)]
        结果 = 判别(块)
        self.assertFalse(结果.handwritten)

    def test_low_confidence_without_colon_is_not_handwriting(self):
        """置信度低但没有「编码:数量」写法 —— 证据不足，走原流程。"""
        块 = [block('大肉姜(生姜)', 70.0), block('红葱头', 72.0), block('姜肉', 71.0),
               block('蒜米', 73.0), block('6斤', 74.0), block('3斤', 72.0),
               block('0.5斤', 71.0), block('21斤', 75.0)]
        结果 = 判别(块)
        self.assertFalse(结果.handwritten)
        self.assertFalse(结果.confident)

    def test_too_few_blocks_is_not_confident(self):
        结果 = 判别([block('1136:15斤', 80.0), block('1368:10个', 80.0)])
        self.assertFalse(结果.handwritten)
        self.assertFalse(结果.confident)
        self.assertIn('样本不足', 结果.reason)

    def test_empty_blocks(self):
        结果 = 判别([])
        self.assertFalse(结果.handwritten)
        self.assertFalse(结果.confident)

    def test_detection_never_raises(self):
        """判别器不能抛异常 —— 出错就退回原流程。"""
        class 坏块:
            def get(self, *a, **k):
                raise ValueError('坏数据')
        try:
            结果 = 判别([坏块()])
        except Exception as exc:  # pragma: no cover
            self.fail('判别器抛异常了：%r' % (exc,))
        self.assertFalse(结果.handwritten)

    def test_explanation_mentions_reason(self):
        结果 = 判别(手写块())
        self.assertIn('自动判别', 判别说明(结果))


class ModeTests(unittest.TestCase):
    def test_auto_is_default(self):
        配置 = resolve_mode({})
        self.assertEqual(MODE_AUTO, 配置.mode)

    def test_setting_auto(self):
        self.assertEqual(MODE_AUTO, resolve_mode({'手写辅助模式': '自动'}).mode)
        self.assertEqual(MODE_AUTO, resolve_mode({'手写辅助模式': 'auto'}).mode)

    def test_setting_off_stays_legacy(self):
        self.assertEqual(MODE_LEGACY, resolve_mode({'手写辅助模式': '关'}).mode)

    def test_explicit_overrides_setting(self):
        配置 = resolve_mode({'手写辅助模式': '自动'}, explicit=MODE_LEGACY)
        self.assertEqual(MODE_LEGACY, 配置.mode)

    def test_explicit_accepts_auto(self):
        配置 = resolve_mode({'手写辅助模式': '关'}, explicit=MODE_AUTO)
        self.assertEqual(MODE_AUTO, 配置.mode)

    def test_per_image_still_works(self):
        配置 = resolve_mode({'手写辅助模式': '逐张'})
        self.assertEqual(MODE_PER_IMAGE, 配置.mode)
        self.assertEqual(MODE_ASSISTED,
                         mode_for_image(配置, 'a.jpg', {'a.jpg': MODE_ASSISTED}))
        self.assertEqual(MODE_LEGACY, mode_for_image(配置, 'b.jpg', {}))

    def test_auto_mode_passes_through_for_judgement(self):
        """自动模式交给判别器决定，mode_for_image 原样返回 auto。"""
        配置 = resolve_mode({'手写辅助模式': '自动'})
        self.assertEqual(MODE_AUTO, mode_for_image(配置, 'a.jpg'))


class VocabularyPathTests(unittest.TestCase):
    """词典路径必须锚定到程序目录，换启动目录不能让它静默失效。"""

    def test_relative_path_is_anchored_to_program_dir(self):
        绝对 = _绝对词典路径('data/dictionaries/handwriting.json')
        from pathlib import Path
        p = Path(绝对)
        self.assertTrue(p.is_absolute())
        self.assertEqual('handwriting.json', p.name)
        self.assertIn('dictionaries', p.parts)

    def test_absolute_path_is_kept(self):
        self.assertEqual(r'C:\x\y.json', _绝对词典路径(r'C:\x\y.json'))

    def test_empty_and_none_disable_vocabulary(self):
        self.assertIsNone(_绝对词典路径(''))
        self.assertIsNone(_绝对词典路径(None))
        self.assertIsNone(_绝对词典路径('   '))

    def test_resolve_mode_anchors_vocabulary(self):
        配 = resolve_mode({'手写辅助模式': '自动', '手写词典辅助': 'data/dictionaries/handwriting.json'})
        from pathlib import Path
        self.assertTrue(Path(配.vocabulary_path).is_absolute())


class RecordContractTests(unittest.TestCase):
    """手写通道的输出要能直接进打印与「待核对」筛选，契约必须与现网一致。"""

    def test_empty_unit_falls_back_to_jin(self):
        """对齐现网 clean_record：手写单单位认不出来时兜底「斤」。"""
        结果 = 规整记录([{'category': '沙姜', 'customer_code': '1366',
                          'weight': '1', 'unit': '', 'remark': ''}])
        self.assertEqual('斤', 结果[0]['unit'])

    def test_existing_unit_is_not_changed(self):
        结果 = 规整记录([{'category': '沙姜', 'customer_code': '1366',
                          'weight': '1', 'unit': '个', 'remark': ''}])
        self.assertEqual('个', 结果[0]['unit'])

    def test_none_becomes_empty_string(self):
        结果 = 规整记录([{'category': '沙姜', 'customer_code': None,
                          'weight': '1', 'unit': '个', 'remark': None}])
        self.assertEqual('', 结果[0]['customer_code'])
        self.assertEqual('', 结果[0]['remark'])
        self.assertNotIn('None', str(结果[0]))

    def test_all_fields_are_strings(self):
        结果 = 规整记录([{'category': '沙姜', 'customer_code': 1366,
                          'weight': 1.5, 'unit': '个', 'remark': ''}])
        for field in ('category', 'customer_code', 'weight', 'unit', 'remark'):
            self.assertIsInstance(结果[0][field], str, field)

    def test_internal_marks_are_preserved(self):
        记录 = [{'category': '沙姜', '_uncertain': True, '_review_reasons': ['数量两路不一致']}]
        结果 = 规整记录(记录)
        self.assertTrue(结果[0]['_uncertain'])
        self.assertEqual(['数量两路不一致'], 结果[0]['_review_reasons'])

    def test_empty_list(self):
        self.assertEqual([], 规整记录([]))


if __name__ == '__main__':
    unittest.main()
