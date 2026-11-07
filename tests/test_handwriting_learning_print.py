"""打印确认后学习：正式软件里人工核对 → 词典/示例库积累。"""
import json
import tempfile
import unittest
from pathlib import Path

from handwriting.learning import learn_from_confirmed_records


def _记录(row_id, category, code, 改过=False, 确认过=False, 模型品类=None, 模型编码=None):
    rec = {'row_id': row_id, 'category': category, 'customer_code': code,
           'weight': '1', 'unit': '斤', 'remark': ''}
    if 改过:
        rec['_user_edited'] = True
    if 确认过:
        rec['_user_confirmed'] = True
    candidates = {}
    if 模型品类:
        candidates['category'] = [{'field': 'category', 'value': 模型品类,
                                   'source': 'model', 'confidence': None, 'evidence': 模型品类}]
    if 模型编码:
        candidates['customer_code'] = [{'field': 'customer_code', 'value': 模型编码,
                                        'source': 'model', 'confidence': None, 'evidence': 模型编码}]
    rec['_evidence'] = {'row_id': row_id, 'candidates': candidates,
                        'bbox': None, 'heading_text': '标题', 'entry_text': '原文'}
    return rec


class LearnFromConfirmedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.词典 = self.tmp / 'dictionaries' / 'handwriting.json'
        self.示例 = self.tmp / 'learning' / 'examples.json'

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_未改动不学(self):
        结果 = learn_from_confirmed_records(
            [_记录('r1', '沙姜', '1366')], vocabulary_path=self.词典, examples_path=self.示例)
        self.assertEqual(0, 结果['changed'])
        self.assertEqual(0, 结果['examples'])

    def test_改过的行学成别名(self):
        记录 = _记录('r1', '马蹄肉', '935', 改过=True, 模型品类='马兄弟的')
        结果 = learn_from_confirmed_records([记录], vocabulary_path=self.词典, examples_path=self.示例)
        self.assertGreaterEqual(结果['changed'], 1)
        # 词典里应该有「马兄弟的 → 马蹄肉」的别名
        数据 = json.loads(self.词典.read_text(encoding='utf-8'))
        for e in 数据['entries']:
            if e['canonical'] == '马蹄肉':
                self.assertIn('马兄弟的', e['aliases'])
                break
        else:
            self.fail('没找到马蹄肉条目')

    def test_确认过的行学成示例(self):
        记录 = _记录('r1', '沙姜', '1366', 确认过=True)
        结果 = learn_from_confirmed_records([记录], vocabulary_path=self.词典, examples_path=self.示例)
        self.assertEqual(1, 结果['examples'])
        self.assertTrue(self.示例.exists())

    def test_数量不进词典(self):
        # 即使改过，weight 也不该进词典（词典只认 category/code/unit/remark）
        记录 = _记录('r1', '沙姜', '1366', 改过=True)
        记录['weight'] = '99'
        记录['_evidence']['candidates']['weight'] = [
            {'field': 'weight', 'value': '9', 'source': 'model', 'confidence': None, 'evidence': '9'}]
        结果 = learn_from_confirmed_records([记录], vocabulary_path=self.词典, examples_path=self.示例)
        # 词典里不该有 weight 字段条目
        if self.词典.exists():
            数据 = json.loads(self.词典.read_text(encoding='utf-8'))
            for e in 数据['entries']:
                self.assertNotEqual('weight', e['field'])

    def test_无证据的行也学规范值(self):
        # 普通（非手写）识别结果没有 _evidence，但人工改过的值本身就是高准确率参考，
        # 应直接作为规范值收进词典（品类/编码/单位/备注），只是没有「读错别名」可学。
        记录 = {'category': '沙姜', 'customer_code': '1366', 'weight': '1', 'unit': '斤',
                'remark': '', '_user_edited': True}
        结果 = learn_from_confirmed_records([记录], vocabulary_path=self.词典, examples_path=self.示例)
        self.assertEqual(0, 结果['changed'])          # 无 AI 原值，无别名
        self.assertGreaterEqual(结果['learned'], 1)   # 沙姜 / 1366 / 斤 被收为规范值
        self.assertEqual(1, 结果['examples'])         # 示例照常落盘
        数据 = json.loads(self.词典.read_text(encoding='utf-8'))
        规范 = {(e['field'], e['canonical']) for e in 数据['entries']}
        self.assertIn(('category', '沙姜'), 规范)
        self.assertIn(('customer_code', '1366'), 规范)
        self.assertIn(('unit', '斤'), 规范)
        # 数量不该进词典
        self.assertNotIn(('weight', '1'), 规范)

    def test_无证据未改动的行仍不学(self):
        记录 = {'category': '沙姜', 'customer_code': '1366', 'weight': '1', 'unit': '斤', 'remark': ''}
        结果 = learn_from_confirmed_records([记录], vocabulary_path=self.词典, examples_path=self.示例)
        self.assertEqual(0, 结果['changed'])
        self.assertEqual(0, 结果['learned'])
        self.assertEqual(0, 结果['examples'])

    def test_异常静默返回空(self):
        # vocabulary_path 是非法路径（指向一个文件而非目录）也不该抛异常
        结果 = learn_from_confirmed_records(
            [_记录('r1', '沙姜', '1366', 改过=True)],
            vocabulary_path=self.tmp / 'dict.txt' / 'x', examples_path=self.示例)
        self.assertIn('changed', 结果)

    def test_空记录列表(self):
        结果 = learn_from_confirmed_records([], vocabulary_path=self.词典, examples_path=self.示例)
        self.assertEqual(0, 结果['changed'])


class 编辑即学习回归Tests(unittest.TestCase):
    """决策 3：「编辑即学习」**不能**收紧为仅 `_user_confirmed`。

    人工改过（`_user_edited`）和人工确认（`_user_confirmed`）**任一**成立就触发学习，
    与识别通道无关（手写辅助 / 普通识别都一样），也与是否点过打印无关。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.词典 = self.tmp / 'dictionaries' / 'handwriting.json'
        self.示例 = self.tmp / 'learning' / 'examples.json'

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_仅改过也必须学习(self):
        # 只有 _user_edited、没有 _user_confirmed —— 仍要学。
        记录 = _记录('r1', '马蹄肉', '935', 改过=True, 模型品类='马兄弟的')
        self.assertNotIn('_user_confirmed', 记录)
        结果 = learn_from_confirmed_records([记录], vocabulary_path=self.词典,
                                          examples_path=self.示例)
        self.assertGreaterEqual(结果['changed'], 1)
        self.assertGreaterEqual(结果['examples'], 1)
        数据 = json.loads(self.词典.read_text(encoding='utf-8'))
        别名 = [a for e in 数据['entries'] if e['canonical'] == '马蹄肉'
                for a in e['aliases']]
        self.assertIn('马兄弟的', 别名)

    def test_仅确认也必须学习(self):
        # 只有 _user_confirmed、没有 _user_edited —— 同样要学。
        记录 = _记录('r1', '沙姜', '1366', 确认过=True)
        self.assertNotIn('_user_edited', 记录)
        结果 = learn_from_confirmed_records([记录], vocabulary_path=self.词典,
                                          examples_path=self.示例)
        self.assertGreaterEqual(结果['examples'], 1)

    def test_普通通道无证据仅改过也必须学(self):
        # 非手写识别结果没有 _evidence；只要人工改过，值本身就是高准确率参考。
        记录 = {'category': '沙姜', 'customer_code': '1366', 'weight': '1', 'unit': '斤',
                'remark': '', '_user_edited': True}
        结果 = learn_from_confirmed_records([记录], vocabulary_path=self.词典,
                                          examples_path=self.示例)
        self.assertGreaterEqual(结果['learned'], 1)
        self.assertEqual(1, 结果['examples'])

    def test_两个标记都没有则不学(self):
        记录 = _记录('r1', '沙姜', '1366')
        结果 = learn_from_confirmed_records([记录], vocabulary_path=self.词典,
                                          examples_path=self.示例)
        self.assertEqual(0, 结果['changed'])
        self.assertEqual(0, 结果['learned'])
        self.assertEqual(0, 结果['examples'])

    def test_触发条件不得收紧为仅确认(self):
        # 源码级守卫：触发判断必须同时引用两个标记。
        import re

        import handwriting.learning as learning_module
        源码 = Path(learning_module.__file__).read_text(encoding='utf-8')
        同行 = (re.search(r"_user_edited[^\n]{0,120}_user_confirmed", 源码)
                or re.search(r"_user_confirmed[^\n]{0,120}_user_edited", 源码))
        self.assertIsNotNone(
            同行,
            '「编辑即学习」的触发条件被改动了：必须保持「_user_edited 或 _user_confirmed」，'
            '不能收紧为仅 _user_confirmed。')


if __name__ == '__main__':
    unittest.main()
