# 独立手写识别库 · 阶段 0—4 + 人工核对闭环 + **已合入主流程**

运行环境：项目现有 Python 3.13 + Pillow；没有新增第三方依赖。导入 `handwriting` 不创建文件、不调用网络、不修改主程序。数据目录由 `路径.程序目录() / 'data'` 统一定位。

- 阶段 0—2：独立 Python API、CLI 和评测工具。方案第六节已被用户改为「图片增强暂不实现」，因此 `preprocess.py` 只读取原图信息；显式要求增强会报错，不偷偷处理图片。
- 阶段 3：证据与复核（`geometry` / `evidence` / `budget` / `review` / `gate`）。
- 阶段 4：组合与界面（`modes` / `compare` / `frozen`），生产接入点是 `_界面补丁/手写补丁.py`。
- 人工核对闭环（`model_records` / `learning` / `review_web`）：核对网页 → 标注落盘 → 学词典与示例 → 下次识别给出候选。
- **合入主流程（`detect.py`）**：每张图先判是不是手写单据，是就走手写识别库，不是走原流程。

## 合入后的识别分流

```
拖入图片 → 识别
            ├─ 判别器 detect.判别(OCR块)
            │    ├─ 判为手写 → 手写通道（完整框 + 行列关联 + 保守裁决 + 局部复核）
            │    └─ 判为印刷 → 原流程（现网原样，不变）
            └─ 手写通道失败 → 只退回**这一张**的原流程
```

判别器（`detect.py`）用两条**互相独立**的证据，两者同时成立才判手写：

| 证据 | 判据 | 手写样本实测 | 印刷样本实测 |
| --- | --- | --- | --- |
| 「编码:数量」格式率 | ≥ 0.20 | 0.43 — 0.95 | 0.00 — 0.015 |
| OCR 置信均值 | < 96.0 | 89.4 — 95.7 | 96.0 — 100 |

阈值来自**真实导入目录里 115 张图**（13 手写 + 102 印刷）网格搜索，零错分且两侧各留 0.2 以上余量；
115 张全量验证准确率 100%。

三条必须知道的事实：

1. **判据不靠客户档案**。档案是按客户分的，而同一个客户也会同时送来印刷单和手写单——
   `customers.json` 里 7 家只有「农和」标了 handwritten，可 13 张手写素材分散在 6 个日期下。
2. **不额外花钱**。判别用的是现网本来就要做的那一次 OCR 调用（`T.tencent_ocr` 有本地缓存），
   后续两条通道都命中同一份缓存。
3. **判错不致命**。判别器只是分流器：判不准一律退回原流程，两条通道都保留疑点、都标待核对。
   宁可让一张手写单少一次优化，也不要把印刷单误判成手写——后者会让手写库去处理规整表格，可能丢列。

> ⚠️ 第一版曾用「表格线检测」判断，**方向是反的已废弃**：手写照片的纸张阴影和折痕会形成大片暗带，
> 印刷截图反而干净，纯 Python 逐像素扫描完全不可靠。

界面上仍可手动切换：`自动判别`（默认）/ `原流程` / `手写辅助` / `逐张选择`，
逐张按钮的优先级高于自动判别。

## 模块速览

| 模块 | 职责 |
| --- | --- |
| `detect.py` | **手写单据自动判别**（「编码:数量」格式率 + OCR 置信度） |
| `geometry.py` | 完整框（polygon → 包围盒）、分栏、行聚类、标题从属、明细条目解析 |
| `evidence.py` | 候选追踪（模型 / OCR 行内 / OCR 标题 / 词典 / 复核）、保守裁决、保留疑点 |
| `budget.py` | 统一预算：请求数 / 总时限 / 费用上界 / 分类配额 |
| `review.py` | 疑点区域规划、原图裁剪、复核合并、迟到响应校验（任务 ID / 图片版本 / 行版本） |
| `gate.py` | 现网合计裁决与品类边界修正的门控 + 裁决前候选快照 |
| `modes.py` | 模式切换（自动判别 / 原流程 / 手写辅助 / 逐张）与不可变配置快照 |
| `compare.py` | 原图对照：区域、原文、各来源候选、建议 |
| `frozen.py` | 冻结验收报告：关键字段错误下降、未提示错误、核对负担、成本 |
| `pipeline.py` | `recognize()`（阶段 0—2 实验通道）、`assisted_recognize()`（手写辅助通道）、`规整记录()`（对齐现网 `clean_record` 契约） |
| `model_records.py` | 模型明细归一化：把「按标题分组」的返回结构展开成逐行扁平明细 |
| `learning.py` | 核对结果落标注、学词典别名、存已确认示例 |
| `review_web.py` | 本地核对网页（只监听 127.0.0.1）+ `review_page.html` |

## 手写通道与现网的记录契约

手写通道的输出要直接进打印与「待核对」筛选，所以字段形态必须与现网 `ttsb2.clean_record` 完全一致，
由 `pipeline.规整记录()` 统一处理：

- 五个字段（`category` / `customer_code` / `weight` / `unit` / `remark`）一律转成去空白的字符串；
- **单位读不出来时兜底「斤」**——对齐现网对手写单的既有规则（手写体上的「斤」被认成怪字很常见，
  不兜底就会印错）。这不是新增策略，是与原流程保持一致。
- 内部标记（`_uncertain` / `_review_reasons` / `_evidence`）原样保留，界面照样显示具体原因。

## 人工核对闭环：让软件越用越智能

模型读手写单据时经常理解成「若干标题，每个标题下挂若干条明细」，返回分组结构。
证据链按逐行扁平结构消费，不归一化的话每行都会落成「缺少同行证据」，整条裁决链直接失效。
`model_records.normalize_records()` 只做**结构转换不改数值**：标题读数原样保留成品类候选，
明细文字里读不出的编码/数量/单位一律留空并标疑点，绝不猜。

核对网页（本地 127.0.0.1）让人对着原图逐行核对，提交后一次性产出三份东西：

| 产物 | 位置 | 作用 |
| --- | --- | --- |
| 标注文件 | `data/annotations/<image_sha256>.json` | `review_status=confirmed`、`complete=true`，与 `dataset.prepare` 同 schema，可直接进冻结评测 |
| 词典别名 | `data/dictionaries/handwriting.json` | 「AI 读错的写法」→「人工确认的规范值」；下次读到同样的错写法就提示正确的值 |
| 已确认示例 | `data/learning/confirmed_examples.json` | 人工确认过的明细 + 原图坐标 + 纠错前后对照，核对时可直接参考 |

铁律：**只从人工确认的数据学习**；学到的东西**只作提示、不静默改值**（由 `vocabulary.preview_candidates` 保证）；
**数量不进词典**（数量是数不是词）；每条别名都能追溯到来源图片、行号与复核人；缺关键字段时**拒绝保存**。

## 快速使用

在项目根目录的 PowerShell 执行：

```powershell
# 不联网：清单、精确去重、相似图候选、非覆盖标注模板
.\.venv\Scripts\python.exe -m handwriting inventory

# 打开核对网页（用已保存的识别结果，不联网）
.\.venv\Scripts\python.exe -m handwriting review "data/手写素材/0a07371a8f56afba76dcb5de02f9b015.jpg" --records "data/evaluations/目标图实测/records.json"

# 不联网：重新生成阶段0/1离线报告与预览
.\.venv\Scripts\python.exe 设计和测试/手写阶段012离线报告.py

# 不联网：候选预览，输入为英文业务键的 JSON 数组
.\.venv\Scripts\python.exe -m handwriting preview --dictionary data/dictionaries/handwriting.json --records 路径/records.json --output 路径/preview.json

# 不联网：阶段 3/4 冻结验收（用已保存的真实响应离线重放）
.\.venv\Scripts\python.exe .setup/冻结评测_汇总.py

# 核对→学习→再识别的闭环验证（在临时目录跑，不动真实标注）
.\.venv\Scripts\python.exe .setup/闭环验证.py

# 独立库测试、原软件回归
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_handwriting*.py' -v
.\.venv\Scripts\python.exe 设计和测试/工作流核验.py
```

preview 同时输出 JSON 和同名 HTML，可在浏览器查看；所有候选与原记录分开，原图、前导零、未知值、重复明细、备注和元数据不会被自动修改。

真实调用会上传输入图片并可能收费，仅在明确授权后执行：

```powershell
# 13张原图高精度/旧手写OCR独立对照；现有两模型能力测试
.\.venv\Scripts\python.exe 设计和测试/手写阶段012实测.py --live

# 单图原始OCR实验；不指定 --model 则不做结构化明细提取
.\.venv\Scripts\python.exe -m handwriting recognize 路径/单据.jpg --live --output 路径/result.json
```

`--live` 是误操作防护，不等同于已经获得数据外传授权。当前本次批量实测尚未执行：自动审批要求用户明确允许将素材发送到腾讯云 OCR 和 DeepSeek；没有绕过审批。

## API

```python
from handwriting.pipeline import RecognitionRequest, recognize
from handwriting.experiments import Budget

# 显式调用才可能联网，生产主程序未调用此入口。
request = RecognitionRequest(
    image_path="单据.jpg",
    action="GeneralAccurateOCR",
    model=None,                 # 若要模型识别，必须显式选择经核验的模型
    vocabulary_path="data/dictionaries/handwriting.json",
)
result = recognize(request, budget=Budget(max_requests=2, max_seconds=120))
records, blocks, notes = result.as_legacy_tuple()
```

结果状态：`ocr_only` 仅 OCR 文字、`needs_review` 有实验模型明细但未经过阶段3证据复核、`error` 没有有效结构化明细或 OCR 失败。失败的某一路不会伪装成完整原流程成功。`suggestions` 单独保存词典建议，`evidence` 保存每一路来源。不要把实验结果直接用于自动打印。

词典 API：`load_vocabulary()`、`save_vocabulary()`、`preview_candidates()`、`build_prompt_context()`、`annotate_records()`。现有主程序英文字段使用：

```python
preview = annotate_records(vocabulary, records,
    field_map={k: k for k in ("category", "customer_code", "unit", "remark")})
```

当前 `annotate_records()` 默认兼容中文键；pipeline/CLI 已显式使用主程序英文键。词典 API 可生成可选提示上下文，本轮实验默认不注入，避免与 OCR 引擎对照混杂。
词典初始为空（未编造业务别名）；人工核对一次就会长出一条真别名。详情见 `data/dictionaries/README.md`。

## 词典外提示的边界

词典为空时**不**标「词典外」——那只是「还没建立词典」，不是「这个值错了」。
词典有内容之后，「词典外品类/编码」才真正代表「这个值没人确认过」，是有效提醒。

## 评测边界

- 图片哈希和解码后像素哈希用于精确去重；dHash 只生成相似候选，不自动合并两张单据。
- 模板不覆盖已有标注。视觉草稿可导入 pending 模板，不能自动变成人工确认；原图哈希不匹配会拒绝导入。
- `annotation_audit()` 阻止同来源组跨调试/验收集；草稿不能进入冻结验收集。
- `score_records()` 只对 confirmed + complete 的参考集计算指标，按稳定 row_id 一对一匹配。模型输出没有可靠行映射时，不应伪造 row_id 来计算正确率。
- `score_regions()` 对框内文字计算诊断字符误差，不等于整单明细正确率；draft 必须显式允许且标为诊断。
- OCR 返回完整 polygon、可选 confidence 和 request_id，不把全页出现某数字当作同行证据。
- 原始 provider 响应和字典候选只落本地；日志不打印密钥、Authorization 或完整图片 Base64。

## 预算、缓存与复跑

批量脚本累计最多 30 次尝试（包括已有账本里的失败/中断尝试），每次不超过45秒，单次执行总时限900秒，无自动重试。模型每次最多4096输出token。中断后结果及请求账本保留；相同输出目录复跑跳过已完成身份，代码/配置/图片不一致时拒绝复用。

账户单价及赠送额度尚未验证，因此目前只保证调用次数/时间/token上限，不声称限制到具体人民币金额。`Budget(max_cost=...)` 需要每次请求的保守上界估价，缺失则拒绝执行。业务可接受的等待时间仍需用户确认。

缓存按原图哈希、接口/模型、参数与提示词哈希隔离；增强当前禁用。失败不缓存为成功空结果；缓存损坏可重取。可选择 `use_cache=False` 做冷缓存对照；冷跑和重放文件分开。

阶段0基线保存在 `.setup/手写库阶段012基线_20261106`，含修改前源文件、配置和依赖。正式发布数据迁移属于阶段5，不在本轮改动 `build.py` 或现有发布目录。
