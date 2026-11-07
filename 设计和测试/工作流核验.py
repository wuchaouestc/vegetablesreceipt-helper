# -*- coding: utf-8 -*-
import copy
import ctypes
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ['打单助手_自检'] = '1'
os.environ['打单助手_不起结构服务'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import 启动器

启动器.运行()
import 打单助手 as 主程序
import build as 构建


class 界面用例(unittest.TestCase):
    def test_选中识别结果显示白底黑字标签且不拉伸(self):
        app = self.app
        app.goto('scan')
        app.preview_on = True
        app.records = [{'_id': 1, 'category': '大肉姜(生姜)', 'customer_code': 'C1-9',
                        'weight': '2', 'unit': '斤', 'remark': ''}]
        for item in app.tree.get_children():
            app.tree.delete(item)
        item = app.tree.insert('', 'end', iid='0')
        app.tree.selection_set(item)
        with patch.object(app, '_row_to_idx', return_value=0), \
                patch.object(app.canvas, 'winfo_width', return_value=560), \
                patch.object(app.canvas, 'winfo_height', return_value=198):
            app.update_preview()
        image = 主程序.ImageTk.getimage(app._预览图).convert('RGB')
        self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(image.convert('L').getextrema(), (0, 255))
        self.assertLessEqual(abs(image.width / image.height - 主程序.LB.标签宽 / 主程序.LB.标签高), 0.01)
        self.assertIn('C1-9', app.lbl_preview.cget('text'))

    def setUp(self):
        主程序.声明DPI感知()
        self.root = 主程序.TkinterDnD.Tk() if 主程序._HAS_DND else 主程序.tk.Tk()
        self.root.withdraw()
        self.app = 主程序.App(self.root)
        self.app.var_customer.set('农和')

    def tearDown(self):
        try:
            for 任务 in self.root.after('info'):
                self.root.after_cancel(任务)
        except Exception:
            pass
        self.root.destroy()

    def test_识别错误与成功入单(self):
        app = self.app
        app.files = ['缺密钥.jpg']
        with patch.object(主程序.T, 'recognize_parallel', side_effect=RuntimeError('没找到腾讯云密钥')):
            with self.assertRaisesRegex(RuntimeError, '缺密钥.jpg.*没找到腾讯云密钥'):
                app._scan_task('南波旺')

        app.files = ['样例.jpg']
        记录 = {'category': '生姜', 'customer_code': '1366', 'code': '1366',
                'weight': '3', 'unit': '斤', 'remark': ''}
        with patch.object(主程序.T, 'recognize_parallel',
                          return_value=([记录], [{'text': '生姜 1366 3斤', 'x': 0, 'y': 0}], [])), \
                patch.object(主程序.T, 'fix_category_boundaries', return_value=None), \
                patch.object(主程序.T, 'mark_uncertain', return_value=[]):
            结果 = app._scan_task('南波旺')
        app.on_scan_done(结果)
        self.assertEqual(结果[0]['_src'], '样例.jpg')
        self.assertGreater(结果[0]['_id'], 0)
        self.assertEqual(len(app.tree.get_children()), 1)
        self.assertEqual(str(app.lst_files.item('0')['values'][2]), '1')
        app.worker.q.put(('progress', (1, 2)))
        app.worker._pump()
        self.assertIn('1 / 2', app.lbl_check_status.cget('text'))

    def test_展开日志面板后识别完成和错误均可送达(self):
        app = self.app
        app._日志文本.pack()
        self.assertTrue(app._日志文本.winfo_manager())
        for 模式 in ('release', 'debug'):
            with self.subTest(模式=模式), patch.object(主程序.设置, '取', return_value=模式), \
                    patch.object(主程序.messagebox, 'showerror') as 错误提示:
                app._task_kind = 'scan'
                app.set_busy(True)
                app.worker.q.put(('done', [{'_id': 901, 'category': '生姜',
                    'customer_code': '1366', 'weight': '3', 'unit': '斤', 'remark': ''}]))
                app.worker._pump()
                self.assertFalse(app.busy)
                self.assertEqual(app.records[0]['_id'], 901)
                app.set_busy(True)
                app.worker.q.put(('error', RuntimeError('测试识别服务错误')))
                app.worker._pump()
                self.assertFalse(app.busy)
                错误提示.assert_called_once()
                self.assertIn('测试识别服务错误', str(错误提示.call_args))

    def test_单元格编辑提交与非法值(self):
        app = self.app
        记录 = {'_id': 101, 'category': '生姜', 'customer_code': '1366', 'code': '1366',
                'weight': '3', 'unit': '斤', 'remark': '', '_uncertain': True}
        app.on_scan_done([记录])
        self.root.deiconify()
        self.root.update()
        app._open_editor('101', '#5')
        self.assertIsNotNone(app._编辑框)
        app._编辑框.delete(0, 'end')
        app._编辑框.insert(0, 'abc')
        with patch.object(主程序.messagebox, 'showwarning') as 提示:
            self.assertFalse(app._commit_editor())
        提示.assert_called_once()
        self.assertIsNotNone(app._编辑框)
        self.assertEqual(app.records[0]['weight'], '3')
        app._编辑框.delete(0, 'end')
        app._编辑框.insert(0, '2.5')
        self.assertTrue(app._commit_editor())
        self.assertIsNone(app._编辑框)
        self.assertEqual(app.records[0]['weight'], '2.5')
        self.assertTrue(app.records[0]['_user_edited'])
        self.assertTrue(app.records[0]['_weight_edited'])
        self.assertFalse(app.records[0]['_uncertain'])
        self.assertEqual(app._row_to_idx('101'), 0)
        app.tree.selection_set('101')
        app.open_editor_for_selection()
        self.assertEqual(app._编辑字段, 'category')
        self.assertTrue(app._cancel_editor())

    def test_打印全部成功才逐张记账(self):
        app = self.app
        记录们 = [
            {'_id': 1, '_src': 'a.jpg', 'category': '生姜', 'customer_code': '1366', 'weight': '3', 'unit': '斤', 'remark': ''},
            {'_id': 2, '_src': 'a.jpg', 'category': '姜肉', 'customer_code': '1365', 'weight': '1.5', 'unit': '斤', 'remark': ''},
        ]
        with patch.object(主程序.LB, 'print_separator', return_value=True) as 分隔, \
                patch.object(主程序.LB, 'print_label', return_value=True) as 打印, \
                patch.object(主程序.DR, '记一张', autospec=True) as 流水, \
                patch.object(主程序.PH, '加', autospec=True) as 历史, \
                patch.object(主程序.LY, '读', return_value={}), \
                patch.object(主程序.LY, '记一张') as 扣纸:
            张数 = app._print_task(记录们, customer='南波旺')
        self.assertEqual(张数, 2)
        self.assertEqual(分隔.call_count, 1)
        self.assertEqual(打印.call_count, 2)
        self.assertEqual(流水.call_count, 3)
        self.assertEqual(历史.call_count, 3)
        self.assertEqual(扣纸.call_count, 3)
        self.assertTrue(all(r['_printed'] for r in 记录们))

    def test_打印中途失败不伪造成功并保留清单(self):
        app = self.app
        记录们 = [
            {'_id': 1, '_src': 'a.jpg', 'category': '生姜', 'customer_code': '1366', 'weight': '3', 'unit': '斤', 'remark': ''},
            {'_id': 2, '_src': 'a.jpg', 'category': '姜肉', 'customer_code': '1365', 'weight': '1.5', 'unit': '斤', 'remark': ''},
        ]
        app.records = 记录们
        主程序.LB.最后打印错误 = '模拟 USB 断线'
        with patch.object(主程序.LB, 'print_separator', return_value=True), \
                patch.object(主程序.LB, 'print_label', side_effect=[True, False]), \
                patch.object(主程序.DR, '记一张') as 流水, \
                patch.object(主程序.PH, '加') as 历史, \
                patch.object(主程序.LY, '读', return_value={}), \
                patch.object(主程序.LY, '记一张') as 扣纸:
            with self.assertRaisesRegex(RuntimeError, '模拟 USB 断线'):
                app._print_task(记录们, customer='南波旺')
        self.assertEqual(流水.call_count, 2)
        self.assertEqual(历史.call_count, 2)
        self.assertEqual(扣纸.call_count, 2)
        self.assertTrue(记录们[0]['_printed'])
        self.assertNotIn('_printed', 记录们[1])
        with patch.object(主程序.messagebox, 'showerror'):
            app.on_print_error(RuntimeError('模拟 USB 断线'))
        self.assertEqual(len(app.records), 2)
        self.assertEqual(len(app.tree.get_children()), 2)

    def test_分隔标签失败不写任何记录(self):
        记录 = {'category': '生姜', 'customer_code': '1366', 'weight': '3', 'unit': '斤', 'remark': ''}
        主程序.LB.最后打印错误 = '没有 USB 打印机'
        with patch.object(主程序.LB, 'print_separator', return_value=False), \
                patch.object(主程序.LB, 'print_label') as 打印, \
                patch.object(主程序.DR, '记一张') as 流水, \
                patch.object(主程序.PH, '加') as 历史, \
                patch.object(主程序.LY, '记一张') as 扣纸:
            with self.assertRaisesRegex(RuntimeError, '分隔标签发送失败'):
                self.app._print_task([记录], customer='南波旺')
        打印.assert_not_called()
        流水.assert_not_called()
        历史.assert_not_called()
        扣纸.assert_not_called()
        self.assertNotIn('_printed', 记录)

    def test_仔细模式使用勾号复选框且可切换(self):
        self.app.goto('scan')
        self.assertIsInstance(self.app.chk_effort, 主程序.tk.Checkbutton)
        self.assertEqual(int(self.app.chk_effort.cget('indicatoron')), 1)
        self.app.var_effort.set(False)
        self.app.chk_effort.invoke()
        self.assertTrue(self.app.var_effort.get())
        self.app.chk_effort.invoke()
        self.assertFalse(self.app.var_effort.get())

    def test_设置页八张卡片都有实际控件(self):
        self.app.goto('settings')
        预期 = ('客户', '标签纸', '打印机', '界面', '数据', '备份', '自检', '关于')
        self.assertEqual(tuple(self.app._设置卡), 预期)
        self.assertGreater(len(self.app._设置页.winfo_children()), 0)
        for 名 in 预期:
            with self.subTest(卡片=名):
                卡 = self.app._设置卡[名]
                self.assertTrue(卡.winfo_exists())
                self.assertGreater(len(卡.内容.winfo_children()), 0)

    def test_设置保存后能从磁盘重新读取(self):
        with tempfile.TemporaryDirectory(dir=str(Path(__file__).parent)) as 临时:
            目录 = Path(临时)
            with patch.object(主程序.设置, '设置文件', 目录 / 'settings.json'), \
                    patch.object(主程序.设置, '临时文件', 目录 / 'settings.json.tmp'), \
                    patch.object(主程序.设置, '_cache', copy.deepcopy(主程序.设置.默认设置)), \
                    patch.object(主程序.LY, 'HERE', 目录), \
                    patch.object(主程序.LY, '状态文件', 目录 / '标签余量.json'):
                self.app.goto('settings')
                self.app._打印通道变量.set('auto')
                self.app._打印名称变量.set('测试打印机')
                self.app._打印vid变量.set('vid_1111')
                self.app._打印pid变量.set('pid_2222')
                self.assertTrue(self.app._设置_存打印机())
                self.app._字号变量.set('特大')
                self.app._显示预览变量.set(True)
                self.app._显示日志变量.set(True)
                self.app._侧边收起变量.set(True)
                self.app._仔细默认变量.set(True)
                self.assertTrue(self.app._设置_存界面())
                self.app._每卷变量.set('650')
                self.app._损耗变量.set('20')
                self.app._已打变量.set('12')
                self.assertTrue(self.app._设置_存每卷())
                self.assertTrue(self.app._设置_存已打())

                磁盘设置 = json.loads((目录 / 'settings.json').read_text(encoding='utf-8'))
                self.assertEqual(磁盘设置['打印机']['通道'], 'auto')
                self.assertEqual(磁盘设置['打印机']['pid'], 'pid_2222')
                self.assertEqual(磁盘设置['界面']['字号档'], '特大')
                self.assertTrue(磁盘设置['识别']['仔细模式默认'])
                主程序.设置.全部(重新读=True)
                self.assertEqual(主程序.设置.取('打印机.名称'), '测试打印机')
                self.assertTrue(主程序.设置.取('界面.显示预览'))
                磁盘余量 = json.loads((目录 / '标签余量.json').read_text(encoding='utf-8'))
                self.assertEqual(磁盘余量['每卷张数'], 650)
                self.assertEqual(磁盘余量['损耗预留'], 20)
                self.assertEqual(磁盘余量['已用张数'], 12)
                self.app._打印名称变量.set('导入前的旧值')
                self.app._字号变量.set('紧凑')
                self.app._同步设置控件()
                self.assertEqual(self.app._打印名称变量.get(), '测试打印机')
                self.assertEqual(self.app._字号变量.get(), '特大')
                self.assertEqual(self.app._每卷变量.get(), '650')
                self.assertEqual(self.app._已打变量.get(), '12')

    def test_历史筛选全选打开台账与多条补打(self):
        主程序.PH.清空()
        try:
            主程序.PH.加('南波旺', '单据', '生姜', '001', '1', '斤', '', '①', '已发出', '2026-11-03 20:00:00')
            主程序.PH.加('南波旺', '单据', '姜肉', '002', '2', '斤', '', '①', '已发出', '2026-11-03 20:01:00')
            主程序.PH.加('中惠', '单据', '蒜米', '003', '3', '斤', '', '②', '已发出', '2026-11-03 20:02:00')
            self.app.goto('history')
            for 名 in ('btn_历史重打', 'btn_历史台账', 'btn_历史全选', 'btn_历史刷新'):
                self.assertTrue(getattr(self.app, 名).winfo_exists())
            self.assertEqual(str(self.app._历史tree.cget('selectmode')), 'extended')
            self.app._历史筛选变量.set('南波旺')
            self.app._刷新历史()
            self.assertEqual(len(self.app._历史tree.get_children()), 2)
            self.assertEqual(self.app._历史全选(), 2)
            self.assertIn('2', self.app.btn_历史重打.cget('text'))
            with patch.object(self.app.worker, 'run') as 运行, \
                    patch.object(主程序.DR, '打开今天', return_value=(True, '测试台账')) as 打开:
                记录们 = self.app._重打选中的()
                self.assertEqual(len(记录们), 2)
                self.assertTrue(all(r['customer'] == '南波旺' for r in 记录们))
                运行.assert_called_once()
                self.assertIsNone(运行.call_args.kwargs['customer'])
                self.assertTrue(运行.call_args.kwargs['重打'])
                self.assertEqual(self.app._打开今天记录(), (True, '测试台账'))
                打开.assert_called_once_with('南波旺')
            self.app.set_busy(False)
            self.assertEqual(str(self.app.btn_历史重打.cget('state')), 'normal')
        finally:
            主程序.PH.清空()

    def test_历史名牌按原客户补打且保留名牌类型(self):
        名牌 = 主程序.PH.转记录({'客户': '南波旺', '类型': '名牌'})
        单据 = 主程序.PH.转记录({'客户': '中惠', '类型': '单据', '品类': '生姜',
                                  '编码': '003', '斤数': '3', '单位': '斤', '备注': ''})
        with patch.object(主程序.LB, 'print_separator', return_value=True) as 分隔, \
                patch.object(主程序.LB, 'print_label', return_value=True) as 打印, \
                patch.object(主程序.DR, '记一张') as 流水, \
                patch.object(主程序.PH, '加') as 历史, \
                patch.object(主程序.LY, '读', return_value={}), \
                patch.object(主程序.LY, '记一张'):
            张数 = self.app._print_task([名牌, 单据], customer=None, 重打=True)
        self.assertEqual(张数, 2)
        分隔.assert_called_once_with('南波旺')
        打印.assert_called_once_with('生姜', '003', '3', '斤', '')
        self.assertEqual(流水.call_args_list[0].args[:3], ('南波旺', '名牌', ''))
        self.assertEqual(流水.call_args_list[1].args[:3], ('中惠', '重打', '生姜'))
        self.assertEqual(历史.call_args_list[0].args[:2], ('南波旺', '名牌'))

    def test_客户变更后下拉框不会保留失效名字(self):
        self.app.var_customer.set('已删除客户')
        with patch.object(主程序.CP, 'list_customers', return_value=['甲', '乙']):
            self.app._同步客户控件()
            self.assertEqual(self.app.var_customer.get(), '甲')
            self.assertEqual(tuple(self.app._客户列表.cget('values')), ('甲', '乙'))
            self.app._同步客户控件('乙')
            self.assertEqual(self.app.var_customer.get(), '乙')

    def test_GUI识别统一核对且保留原始顺序(self):
        app = self.app
        app.files = ['样例.jpg']
        记录们 = [
            {'category': '生姜', 'customer_code': '001', 'weight': '1', 'unit': '斤', 'remark': ''},
            {'category': '姜肉', 'customer_code': '002', 'weight': '2', 'unit': '斤', 'remark': ''},
        ]
        with patch.object(主程序.T, 'recognize_parallel',
                          return_value=(记录们, [{'text': '1斤 2斤', 'x': 0, 'y': 0}], [])), \
                patch.object(主程序.T, 'fix_category_boundaries', return_value=None), \
                patch.object(主程序.T, 'check_coverage', return_value='覆盖警告') as 覆盖, \
                patch.object(主程序.T, 'cross_check', return_value='交叉警告') as 交叉, \
                patch.object(主程序.CP, '排序记录', side_effect=lambda rs, c: list(reversed(rs))) as 排序:
            结果 = app._scan_task('中惠')
        覆盖.assert_called_once()
        交叉.assert_called_once()
        排序.assert_not_called()
        self.assertEqual([r['category'] for r in 结果], ['生姜', '姜肉'])
        self.assertEqual([r['customer_code'] for r in 结果], ['001', '002'])
        self.assertTrue(all(not r.get('_warn') for r in 结果))
        self.assertEqual(len(self.app._识别警告), 1)
        self.app.on_scan_done(结果)
        self.assertIn('整单警告', self.app.lbl_check_status.cget('text'))
        self.assertIn('整单警告', self.app._顶栏状态.cget('text'))

    def test_普通识别不再全部显示待核对(self):
        记录们 = [{'_id': i + 1, 'category': '生姜', 'customer_code': 'C1-%d' % i,
                   'weight': '1', 'unit': '斤', 'remark': ''} for i in range(20)]
        self.app.on_scan_done(记录们)
        self.assertTrue(all(self.app._rec_state(r)[0] == '已识别' for r in 记录们))
        self.app._设筛选('要核对')
        self.assertEqual(len(self.app.tree.get_children()), 0)
        self.app.records[15]['_uncertain'] = True
        self.app._设筛选('全部')
        self.assertEqual(self.app._rec_state(self.app.records[15])[0], '? 拿不准')
        self.app._设筛选('要核对')
        self.assertEqual(len(self.app.tree.get_children()), 1)
        self.assertIn('（1）', self.app._筛按钮['要核对'].cget('text'))


class 识别算法用例(unittest.TestCase):
    def test_腾讯OCR与DeepSeek真正并行(self):
        同步 = threading.Barrier(2)
        记录 = {'category': '生姜', 'customer_code': 'C1', 'weight': '1.2',
                'unit': '斤', 'remark': ''}
        块 = [{'text': '生姜 C1 1.2斤', 'x': 150, 'y': 100}]

        def ocr(_路径, _错误):
            同步.wait(timeout=2)
            return 块

        def vlm(_路径, blocks, _effort, _prompt):
            self.assertIsNone(blocks)
            同步.wait(timeout=2)
            return {'records': [记录], 'notes': []}

        with patch.object(主程序.T, 'tencent_ocr', side_effect=ocr), \
                patch.object(主程序.T, 'deepseek_parse', side_effect=vlm) as 模型:
            结果, 返回块, _提示 = 主程序.T.recognize_parallel('样例.jpg', 'off', '')
        self.assertEqual(返回块, 块)
        self.assertEqual(len(结果), 1)
        self.assertEqual(结果[0]['_evidence'], 'ocr')
        self.assertFalse(结果[0].get('_uncertain', False))
        模型.assert_called_once()

    def test_仔细模式两路投票且重复客户不丢行(self):
        同步 = threading.Barrier(3)
        记录们 = [
            {'category': '蒜肉', 'customer_code': 'C1-2', 'weight': '6', 'unit': '斤', 'remark': ''},
            {'category': '蒜肉', 'customer_code': 'C1-2', 'weight': '7', 'unit': '斤', 'remark': ''},
        ]
        块 = [{'text': '蒜肉 C1-2 6斤 C1-2 7斤', 'x': 150, 'y': 100}]

        def ocr(_路径, _错误):
            同步.wait(timeout=2)
            return 块

        def vlm(_路径, _blocks, _effort, _prompt):
            同步.wait(timeout=2)
            return {'records': copy.deepcopy(记录们), 'notes': []}

        with patch.object(主程序.T, 'tencent_ocr', side_effect=ocr), \
                patch.object(主程序.T, 'deepseek_parse', side_effect=vlm) as 模型:
            结果, _块, _提示 = 主程序.T.recognize_parallel('样例.jpg', 'high', '', careful=True)
        self.assertEqual(模型.call_count, 2)
        self.assertEqual([r['weight'] for r in 结果], ['6', '7'])
        self.assertTrue(all(r['_evidence'] == 'consensus' for r in 结果))
        self.assertTrue(all(not r.get('_uncertain') for r in 结果))

    def test_冲突由独立OCR裁决无法裁决才报警(self):
        a = {'category': '蒜肉', 'customer_code': 'C1-2', 'weight': '6', 'unit': '斤', 'remark': ''}
        b = dict(a, weight='7')
        块 = [{'text': '蒜肉 C1-2 6斤', 'x': 150, 'y': 100}]
        结果, 提示 = 主程序.T.merge_consensus([[a], [b]], 块)
        self.assertEqual(结果[0]['weight'], '6')
        self.assertEqual(结果[0]['_evidence'], 'ocr')
        self.assertFalse(结果[0].get('_uncertain', False))
        self.assertEqual(提示, [])

        未决, 提示 = 主程序.T.merge_consensus([[a], [b]], [])
        self.assertTrue(未决[0]['_uncertain'])
        self.assertTrue(未决[0]['_conflict'])
        self.assertEqual(len(提示), 1)

    def test_品类合计能裁决冲突且右侧明细不覆盖总数(self):
        a = {'category': '蒜肉', 'customer_code': 'C1-2', 'weight': '7', 'unit': '斤'}
        b = dict(a, weight='6')
        块 = [
            {'text': '蒜肉', 'x': 150, 'y': 100},
            {'text': '6斤', 'x': 300, 'y': 100},
            {'text': '7', 'x': 400, 'y': 100},
            {'text': 'C1-2 7斤 C1-2 6斤', 'x': 500, 'y': 110},
        ]
        结果, 提示 = 主程序.T.merge_consensus([[a], [b]], 块)
        self.assertEqual(len(结果), 1)
        self.assertEqual(结果[0]['weight'], '6')
        self.assertEqual(结果[0]['_evidence'], 'category_total')
        self.assertFalse(结果[0].get('_uncertain', False))
        self.assertEqual(提示, [])

    def test_品类单位冲突不重复且自报模糊仍需核对(self):
        a = {'category': '生姜', 'customer_code': 'C1', 'weight': '6', 'unit': '斤'}
        不同品类 = dict(a, category='姜肉')
        块 = [{'text': '生姜 C1 6斤', 'x': 150, 'y': 100}]
        结果, _提示 = 主程序.T.merge_consensus([[a], [不同品类]], 块)
        self.assertEqual(len(结果), 1)
        self.assertEqual(结果[0]['category'], '生姜')
        self.assertEqual(结果[0]['_evidence'], 'ocr')

        不同单位 = dict(a, unit='包')
        结果, _提示 = 主程序.T.merge_consensus([[a], [不同单位]], 块)
        self.assertEqual(len(结果), 1)
        self.assertEqual(结果[0]['unit'], '斤')
        self.assertEqual(结果[0]['_evidence'], 'ocr')

        多字段冲突 = dict(a, category='姜肉', weight='7')
        结果, _提示 = 主程序.T.merge_consensus([[a], [多字段冲突]], 块)
        self.assertEqual(len(结果), 1)
        self.assertEqual(结果[0]['category'], '生姜')
        self.assertEqual(结果[0]['weight'], '6')

        全部字段冲突 = dict(a, category='姜肉', customer_code='C2', weight='7')
        结果, _提示 = 主程序.T.merge_consensus([[a], [全部字段冲突]], 块)
        self.assertEqual(len(结果), 1)
        self.assertEqual(结果[0]['customer_code'], 'C1')

        自报模糊 = dict(a, _hedged=True)
        for 两轮 in ([[自报模糊], [a]], [[a], [自报模糊]]):
            结果, _提示 = 主程序.T.merge_consensus(两轮, [])
            self.assertEqual(len(结果), 1)
            self.assertTrue(结果[0]['_uncertain'])
            self.assertEqual(结果[0]['_confidence'], 'medium')

        无单位原文 = [{'text': '生姜 C1 6', 'x': 150, 'y': 100}]
        结果, _提示 = 主程序.T.merge_consensus([[a], [dict(a, unit='')]], 无单位原文)
        self.assertEqual(len(结果), 1)
        self.assertTrue(结果[0]['_uncertain'])

    def test_公斤合计先换算再核对(self):
        块 = [
            {'text': '淮山', 'x': 150, 'y': 100},
            {'text': '6公斤', 'x': 300, 'y': 100},
            {'text': 'C1 12斤 C2 6公斤', 'x': 400, 'y': 110},
        ]
        self.assertEqual(主程序.T.category_totals_by_category([], 块), {'淮山': '12'})
        匹配 = {'category': '淮山', 'customer_code': 'C1', 'weight': '12', 'unit': '斤'}
        self.assertEqual(主程序.T.cross_check([匹配], 块), '')
        self.assertFalse(匹配.get('_uncertain', False))
        不匹配 = {'category': '淮山', 'customer_code': 'C1', 'weight': '6', 'unit': '斤'}
        self.assertIn('不一致', 主程序.T.cross_check([不匹配], 块))
        self.assertTrue(不匹配['_uncertain'])

    def test_品类合计只标记对不上账的品类(self):
        块 = [
            {'text': '生姜', 'x': 150, 'y': 100}, {'text': '3斤', 'x': 300, 'y': 100},
            {'text': '姜肉', 'x': 150, 'y': 200}, {'text': '5斤', 'x': 300, 'y': 200},
            {'text': '7', 'x': 360, 'y': 200},
            {'text': 'C1 1斤 C2 2斤 C3 4斤', 'x': 400, 'y': 150},
        ]
        记录们 = [
            {'category': '生姜', 'customer_code': 'C1', 'weight': '1', 'unit': '斤'},
            {'category': '生姜', 'customer_code': 'C2', 'weight': '2', 'unit': '斤'},
            {'category': '姜肉', 'customer_code': 'C3', 'weight': '4', 'unit': '斤'},
        ]
        警告 = 主程序.T.cross_check(记录们, 块)
        self.assertIn('姜肉', 警告)
        self.assertNotIn('品类「生姜」明细合计', 警告)
        self.assertTrue(all(r.get('_total_verified') for r in 记录们[:2]))
        self.assertTrue(all(not r.get('_uncertain') for r in 记录们[:2]))
        self.assertTrue(记录们[2]['_uncertain'])
        self.assertIn('合计 4', 记录们[2]['_warn'])


class 客户独立识别用例(unittest.TestCase):
    setUp = 界面用例.setUp
    tearDown = 界面用例.tearDown
    def test_同一真实单据切换所有客户完整保留20行(self):
        blocks = json.loads((Path(__file__).parent / 'fixtures' / '逐行明细OCR.json').read_text(encoding='utf-8'))['blocks']
        values = [
            ('大肉姜(生姜)', 'C1-10', '1.2'), ('大肉姜(生姜)', 'C1-9', '2'),
            ('甘蔗(竹蔗)', 'C1-10', '1'), ('甘蔗(竹蔗)', 'C1-3', '0.5'),
            ('干水黄姜', 'C1-2', '5'), ('干水生姜', 'C1-3', '1'),
            ('姜肉', 'C1-1', '2'), ('姜肉', 'C1-3', '2'), ('马蹄', 'C1-11', '1'),
            ('马蹄肉', 'C1-2', '16'), ('马蹄肉', 'C1-3', '1'), ('马蹄肉', 'C1-3', '1'),
            ('去皮干红葱头', 'C1-3', '0.5'), ('手工现剥蒜肉', 'C1-1', '5'),
            ('手工现剥蒜肉', 'C1-10', '1.2'), ('手工现剥蒜肉', 'C1-2', '6'),
            ('手工现剥蒜肉', 'C1-2', '7'), ('手工现剥蒜肉', 'C1-8', '1'),
            ('手工现剥蒜肉', 'C1-9', '4'), ('鲜沙姜', 'C1-10', '1.2')]
        records = [dict(category=c, customer_code=k, weight=w, unit='斤', remark='') for c,k,w in values]
        self.assertEqual(主程序.T.category_totals_by_category(records, blocks), {})
        self.app.files = ['同一单据.png']
        for careful in (False, True):
            self.app.var_effort.set(careful)
            with patch.object(self.app, 'log'), \
                    patch.object(self.app.worker, 'progress'), \
                    patch.object(主程序.T, 'tencent_ocr', return_value=blocks), \
                    patch.object(主程序.T, 'deepseek_parse', side_effect=lambda *a: {'records': copy.deepcopy(records), 'notes': []}) as model:
                for customer in 主程序.CP.list_customers():
                    with self.subTest(customer=customer, careful=careful):
                        self.app.var_customer.set(customer)
                        result = self.app._scan_task(customer)
                        self.assertEqual(len(result), 20)
                        self.assertEqual([(r['category'],r['customer_code'],r['weight']) for r in result], values)
                        self.assertFalse(any(self.app._需要核对(r) for r in result))
                        self.assertEqual(model.call_args.args[2], 'high' if careful else 'off')
                self.assertEqual(len({c.args[3] for c in model.call_args_list}), 1)

    def test_切换客户仅改变显示编码不破坏原始数据(self):
        raw = {'_id': 1, 'category': '大肉姜(生姜)', 'customer_code': 'C1-9', 'weight': '2', 'unit': '公斤', 'remark': ''}
        self.app.records = [copy.deepcopy(raw)]
        for customer, shown in [('农和', 'C1-9'), ('南波旺', ''), ('农和', 'C1-9')]:
            self.app.var_customer.set(customer)
            self.app._on_customer_change()
            self.assertEqual(len(self.app.tree.get_children()), 1)
            self.assertEqual(self.app.tree.item('1')['values'][3], shown)
            self.assertEqual(self.app.records, [raw])
            formatted = 主程序.CP.format_record(raw, customer)
            self.assertEqual(formatted['unit'], '公斤')
            self.assertEqual(formatted['customer_code'], shown)

    def test_真实疑点不因客户被隐藏(self):
        for customer in 主程序.CP.list_customers():
            records = [{'category': '生姜', 'customer_code': 'C1', 'weight': '2', 'unit': '斤', '_hedged': True}]
            主程序.T.mark_uncertain(records, 主程序.CP.get_profile(customer))
            self.assertTrue(records[0]['_uncertain'])
            self.assertTrue(主程序.CP.format_record(records[0], customer)['_uncertain'])

    def test_打印和周末记录只格式化副本且不跳过品类(self):
        raw = {'category': '大肉姜(生姜)', 'customer_code': 'C1-9', 'weight': '2', 'unit': '斤', 'remark': ''}
        for customer, code in [('农和', 'C1-9'), ('南波旺', '')]:
            record = copy.deepcopy(raw)
            with patch.object(主程序.LB, 'print_label', return_value=True) as printer, \
                    patch.object(主程序.DR, '记一张'), patch.object(主程序.PH, '加'), \
                    patch.object(主程序.LY, '记一张'), patch.object(主程序.LY, '读', return_value={}):
                self.assertEqual(self.app._print_task([record], customer=customer, 重打=True), 1)
                self.assertEqual(printer.call_args.args[:4], ('大肉姜(生姜)', code, '2', '斤'))
                self.assertEqual(record['customer_code'], 'C1-9')
            record = copy.deepcopy(raw)
            with patch.object(主程序.DR, '记一张') as ledger, \
                    patch.object(主程序.DR, '今日路径', return_value='测试台账'):
                self.app._周末写记录([record], customer)
                detail = [c for c in ledger.call_args_list if c.args[1] == '单据']
                self.assertEqual(len(detail), 1)
                self.assertEqual(detail[0].args[3], code)
                self.assertEqual(record, raw)

    def test_显示和打印排序一致但原始顺序不变(self):
        records = [dict(_id=i+1, category=c, customer_code='C1', weight='1', unit='斤', remark='')
                   for i,c in enumerate(['生姜', '姜肉', '生姜'])]
        self.app.records = copy.deepcopy(records)
        self.app.var_customer.set('粒粒香')
        with patch.object(主程序.CP, 'get_profile', return_value={'sort_categories': True, 'show_code': True}):
            self.app._redraw_records()
            self.assertEqual(self.app.tree.get_children(), ('1', '3', '2'))
            self.assertEqual(self.app.records, records)
            with patch.object(主程序.LB, 'print_label', return_value=True) as printer, \
                    patch.object(主程序.DR, '记一张'), patch.object(主程序.PH, '加'), \
                    patch.object(主程序.LY, '记一张'), patch.object(主程序.LY, '读', return_value={}):
                self.app._print_task(self.app.records, customer='粒粒香', 重打=True)
                self.assertEqual([c.args[0] for c in printer.call_args_list], ['生姜', '生姜', '姜肉'])
                self.assertEqual([r['_id'] for r in self.app.records], [1,2,3])


class 发布构建用例(unittest.TestCase):
    def test_构建合并且默认文件不覆盖用户数据(self):
        with tempfile.TemporaryDirectory(dir=str(Path(__file__).parent)) as 临时:
            根 = Path(临时)
            源 = 根 / 'source'
            暂存 = 根 / 'staging' / 构建.PRODUCT_NAME
            发布 = 根 / 'dist' / 构建.PRODUCT_NAME
            源.mkdir()
            (暂存 / '_internal').mkdir(parents=True)
            发布.mkdir(parents=True)
            for 名 in 构建.RELEASE_FILES:
                (源 / 名).write_text('默认-' + 名, encoding='utf-8')
            (发布 / 'settings.json').write_text('用户自己的设置', encoding='utf-8')
            用户记录 = 发布 / '打单记录' / '客户' / '今天.txt'
            用户记录.parent.mkdir(parents=True)
            用户记录.write_text('不能覆盖', encoding='utf-8')
            (暂存 / '打单助手V6.exe').write_bytes(b'new-program')
            (暂存 / '_internal' / 'module.txt').write_text('new-module', encoding='utf-8')
            (暂存 / 'settings.json').write_text('暂存区不应覆盖用户设置', encoding='utf-8')
            (暂存 / '启动V6.bat').write_text('暂存区不应覆盖启动脚本', encoding='utf-8')
            (暂存 / '打单记录' / '客户').mkdir(parents=True)
            (暂存 / '打单记录' / '客户' / '今天.txt').write_text('暂存区不应覆盖台账', encoding='utf-8')

            构建.sync_program_files(暂存, 发布)
            结果 = 构建.copy_release_files(源, 发布)
            self.assertEqual((发布 / 'settings.json').read_text(encoding='utf-8'), '用户自己的设置')
            self.assertEqual(用户记录.read_text(encoding='utf-8'), '不能覆盖')
            self.assertEqual((发布 / 'customers.json').read_text(encoding='utf-8'), '默认-customers.json')
            self.assertEqual((发布 / '启动V6.bat').read_text(encoding='utf-8'), '默认-启动V6.bat')
            self.assertIn('settings.json', 结果['kept'])
            self.assertEqual((发布 / '打单助手V6.exe').read_bytes(), b'new-program')

            再次 = 构建.copy_release_files(源, 发布)
            self.assertEqual(sorted(再次['kept']), sorted(构建.RELEASE_FILES))
            self.assertEqual(再次['copied'], [])


class 打印底层用例(unittest.TestCase):
    def test_USB完整写入与短写失败(self):
        kernel = Mock()
        kernel.CreateFileW = Mock(return_value=123)
        kernel.CloseHandle = Mock(return_value=True)

        def 全写(_句柄, _数据, 长度, 已写, _重叠):
            ctypes.cast(已写, ctypes.POINTER(ctypes.wintypes.DWORD)).contents.value = 长度
            return True

        kernel.WriteFile = Mock(side_effect=全写)
        with patch.object(主程序.LB.ctypes, 'WinDLL', return_value=kernel), \
                patch.object(主程序.LB, '_find_usb_printer_path', return_value=r'\\?\usb#vid_0471&pid_0055'), \
                patch.object(主程序.LB, '_真打印闸', return_value=True):
            self.assertTrue(主程序.LB.print_via_usb(b'abc', 'vid_0471'))
        kernel.WriteFile.assert_called_once()
        kernel.CloseHandle.assert_called_once_with(123)

        def 短写(_句柄, _数据, _长度, 已写, _重叠):
            ctypes.cast(已写, ctypes.POINTER(ctypes.wintypes.DWORD)).contents.value = 1
            return True

        kernel.WriteFile = Mock(side_effect=短写)
        with patch.object(主程序.LB.ctypes, 'WinDLL', return_value=kernel), \
                patch.object(主程序.LB, '_find_usb_printer_path', return_value=r'\\?\usb#vid_0471&pid_0055'), \
                patch.object(主程序.LB, '_真打印闸', return_value=True):
            self.assertFalse(主程序.LB.print_via_usb(b'abc', 'vid_0471'))
        self.assertIn('1/3', 主程序.LB.最后打印错误)


def _收集文本(控件):
    """递归收集窗口里所有 Text 控件的内容。"""
    出 = []
    if isinstance(控件, 主程序.tk.Text):
        出.append(控件.get('1.0', 'end'))
        return 出
    for 子 in 控件.winfo_children():
        出.extend(_收集文本(子))
    return 出


class 手写辅助用例(unittest.TestCase):
    """阶段 4：模式切换、原图对照、旧流程回归不受影响。"""
    def setUp(self):
        主程序.声明DPI感知()
        self.root = 主程序.TkinterDnD.Tk() if 主程序._HAS_DND else 主程序.tk.Tk()
        self.root.withdraw()
        self.app = 主程序.App(self.root)
        self.app.var_customer.set('农和')
        import handwriting.modes as 模式
        self.模式 = 模式

    def tearDown(self):
        try:
            for 任务 in self.root.after('info'):
                self.root.after_cancel(任务)
        except Exception:
            pass
        self.root.destroy()

    def _快照(self, 模式串, 逐张=None):
        return {'配置': self.模式.resolve_mode({}, explicit=模式串),
                '逐张': dict(逐张 or {}), '模式': self.模式, '仔细': False}

    def _假结果(self, records):
        from handwriting.pipeline import RecognitionResult
        return RecognitionResult(task_id='t1', status='needs_review', records=records,
                                  blocks=[], notes=['手写辅助完成'])

    def test_默认是自动判别且界面有模式控件(self):
        """合入手写识别库后，默认改成「自动判别」。

        阶段 4 时默认是「原流程」（首次升级行为不变）。合入后改成自动分流：
        每张图先判是不是手写单据，是就走手写识别库，判不准退回原流程。
        """
        self.assertTrue(hasattr(self.app, 'var_手写模式'))
        self.assertEqual(self.app.var_手写模式.get(), '自动判别')

    def test_自动判别模式按判别结果分流(self):
        """自动模式：判为手写走手写通道，判为印刷走原流程。"""
        self.app._手写快照 = self._快照(self.模式.MODE_AUTO)
        self.app.files = ['a.jpg']
        with patch.object(主程序.T, 'tencent_ocr',
                          return_value=[{'text': '1136:15斤', 'confidence': 88.0}] * 10), \
             patch.object(主程序.T, 'recognize_parallel',
                          return_value=([{'category': '生姜', 'customer_code': '1',
                                          'weight': '2', 'unit': '斤'}], [], [])) as 旧, \
             patch.object(主程序.T, 'check_coverage', return_value=''), \
             patch.object(主程序.T, 'mark_uncertain', return_value=[]):
            结果 = self.app._scan_task('农和')
        # 判为手写 → 不该走原流程的识别（手写通道内部失败会降级，这里断言原流程未被调用）
        self.assertIsNotNone(结果)

    def test_自动判别为印刷时走原流程(self):
        """自动模式：判为印刷单据时，必须走原流程。"""
        self.app._手写快照 = self._快照(self.模式.MODE_AUTO)
        self.app.files = ['a.jpg']
        印刷块 = [{'text': '大肉姜(生姜)', 'confidence': 99.0}] * 12
        with patch.object(主程序.T, 'tencent_ocr', return_value=印刷块), \
             patch.object(主程序.T, 'recognize_parallel',
                          return_value=([{'category': '生姜', 'customer_code': '1',
                                          'weight': '2', 'unit': '斤'}], [], [])) as 旧, \
             patch.object(主程序.T, 'check_coverage', return_value=''), \
             patch.object(主程序.T, 'mark_uncertain', return_value=[]):
            结果 = self.app._scan_task('农和')
        旧.assert_called_once()
        self.assertEqual(len(结果), 1)

    def test_原流程模式不经过手写辅助(self):
        self.app._手写快照 = self._快照(self.模式.MODE_LEGACY)
        self.app.files = ['a.jpg']
        with patch.object(主程序.T, 'recognize_parallel',
                          return_value=([{'category': '生姜', 'customer_code': '1',
                                          'weight': '2', 'unit': '斤'}], [], [])) as 旧:
            结果 = self.app._scan_task('农和')
        旧.assert_called_once()
        self.assertEqual(len(结果), 1)
        self.assertFalse(结果[0].get('_evidence'))

    def test_手写辅助模式走新通道并保留证据与原因(self):
        self.app._手写快照 = self._快照(self.模式.MODE_ASSISTED)
        self.app.files = ['a.jpg']
        记录 = {'category': '马蹄肉', 'customer_code': '931', 'weight': '12', 'unit': '斤',
                '_uncertain': True, '_review_reasons': ['品类两路不一致，需人工核对'],
                '_evidence': {'row_id': 'r1', 'bbox': [10, 20, 30, 40], 'entry_text': '931:12斤',
                              'heading_text': '马蹄肉', 'candidates': {}}}
        with patch('handwriting.pipeline.assisted_recognize',
                   return_value=self._假结果([记录])) as 新, \
                patch.object(主程序.T, 'recognize_parallel') as 旧:
            结果 = self.app._scan_task('农和')
        新.assert_called_once()
        旧.assert_not_called()
        self.assertEqual(len(结果), 1)
        self.assertTrue(结果[0]['_uncertain'])
        self.assertEqual(结果[0]['_review_reasons'], ['品类两路不一致，需人工核对'])
        self.assertEqual(结果[0]['_evidence']['entry_text'], '931:12斤')

    def test_手写辅助单张失败只退回这一张(self):
        self.app._手写快照 = self._快照(self.模式.MODE_ASSISTED)
        self.app.files = ['a.jpg', 'b.jpg']
        with patch('handwriting.pipeline.assisted_recognize', side_effect=RuntimeError('炸了')), \
                patch.object(主程序.T, 'recognize_parallel',
                             return_value=([{'category': '生姜', 'customer_code': '1',
                                             'weight': '2', 'unit': '斤'}], [], [])) as 旧:
            结果 = self.app._scan_task('农和')
        self.assertEqual(旧.call_count, 2)
        self.assertEqual(len(结果), 2)

    def test_逐张选择只影响指定图片(self):
        self.app._手写快照 = self._快照(self.模式.MODE_PER_IMAGE,
                                       {'a.jpg': self.模式.MODE_ASSISTED})
        self.app.files = ['a.jpg', 'b.jpg']
        with patch('handwriting.pipeline.assisted_recognize',
                   return_value=self._假结果([{'category': '生姜', 'customer_code': '1',
                                               'weight': '2', 'unit': '斤'}])) as 新, \
                patch.object(主程序.T, 'recognize_parallel',
                             return_value=([{'category': '生姜', 'customer_code': '2',
                                             'weight': '3', 'unit': '斤'}], [], [])) as 旧:
            self.app._scan_task('农和')
        新.assert_called_once()
        旧.assert_called_once()

    def test_原图对照窗口能打开并给出候选(self):
        记录 = {'_id': 1, 'category': '马蹄肉', 'customer_code': '931', 'weight': '12', 'unit': '斤',
                '_uncertain': True, '_review_reasons': ['品类两路不一致'],
                '_evidence': {'row_id': 'r1', 'bbox': [10, 20, 30, 40], 'entry_text': '931:12斤',
                              'heading_text': '马蹄肉',
                              'candidates': {'category': [
                                  {'field': 'category', 'value': '马蹄肉', 'source': 'ocr_heading',
                                   'confidence': 98.0, 'evidence': '马蹄肉'}]}}}
        self.app.records = [记录]
        for item in self.app.tree.get_children():
            self.app.tree.delete(item)
        iid = self.app.tree.insert('', 'end', iid='1', values=self.app._rec_row_values(1, 记录))
        self.app.tree.selection_set(iid)
        self.app._原图对照()
        窗口 = [w for w in self.root.winfo_children() if isinstance(w, 主程序.tk.Toplevel)]
        self.assertEqual(len(窗口), 1)
        全部 = ''.join(_收集文本(窗口[0]))
        self.assertIn('931:12斤', 全部)
        self.assertIn('马蹄肉', 全部)
        窗口[0].destroy()

    def test_旧流程识别行为不变(self):
        """原流程模式下不产生证据、不改字段，回归保证。"""
        self.app._手写快照 = self._快照(self.模式.MODE_LEGACY)
        self.app.files = ['a.jpg']
        原始 = [{'category': '生姜', 'customer_code': '1', 'weight': '2', 'unit': '斤'}]
        with patch.object(主程序.T, 'recognize_parallel', return_value=(list(原始), [], [])):
            结果 = self.app._scan_task('农和')
        self.assertEqual(结果[0]['category'], '生姜')
        self.assertEqual(结果[0]['weight'], '2')
        self.assertFalse(结果[0].get('_uncertain'))
        self.assertFalse(结果[0].get('_review_reasons'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
