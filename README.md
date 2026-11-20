# 打单助手 V6（vegetablesreceipt-helper）

Windows 桌面打单工具：把采购单照片识别成结构化清单，人工核对后按客户规则生成输出副本，
打印 40×30 mm 标签或导出 PDF，并留可对账的台账。

- 识别通道：腾讯云 OCR + DeepSeek 结构化（普通单），手写辅助通道（手写单，自动判别分流）
- 本地能力：标签位图排版、USB/驱动双通道打印、PDF 虚拟打印机导出、标签余量估算
- 数据形态：JSON 配置 + 追加式 TSV 台账 + 图片与诊断日志，无数据库

## 快速开始

```bash
# 1. 依赖
.venv/Scripts/python.exe -m pip install -r requirements.txt

# 2. 密钥（config_local.py 已被 .gitignore 排除，不会进仓库）
copy config_local.example.py config_local.py
#    填入 TENCENT_SECRET_ID / TENCENT_SECRET_KEY / DEEPSEEK_API_KEY

# 3. 启动
启动源码.bat                     # 或 .venv/Scripts/pythonw.exe 启动器.py
启动器.py --自检                 # 只装配主程序与全部补丁，不进 mainloop（CI 用）
```

## 源码目录

```
.
├── 启动器.py               打包入口：定位根目录 → 装配补丁 → 进主循环
├── 打单助手.py             主模块：窗口、三个页面、Worker、打印调度（补丁的执行命名空间）
├── 路径.py                 路径契约：程序目录 / 数据目录 / 密钥回退
├── config.py               密钥解析（环境变量 > config_local.py > 空占位）
├── config_local.example.py 密钥模板
├── settings.json           运行期设置
├── customers.json          客户档案
├── build.py                构建：暂存 → 保护用户数据 → 合并发布
├── 打单助手V6.spec         PyInstaller onedir 配置
├── app/                    业务领域层（设置、客户档案、台账、历史、余量、导入落地）
├── core/                   基础设施层（识别管线、标签渲染与打印）
├── handwriting/            手写识别子系统（分流、证据、裁决、核对、学习）
├── patches/                生产界面补丁（按序装配到主模块命名空间）
├── field_service/          本地结构分析服务（独立虚拟环境，可选）
├── data/                   词典、示例库、标注、评测产物
├── tests/                  单元测试
├── tools/                  静态导入校验等工程工具
└── docs/                   设计说明书、使用说明、历史实验报告
```

**根目录为什么只放这几个 .py**：`启动器.py` 是 PyInstaller 分析入口，`打单助手.py` 是补丁执行命名空间，
`路径.py` 的 `__file__` 决定数据落盘位置。这三处被打包机制与硬编码路径锁定，移动即破坏运行时定位；
其余实现全部下沉到包目录。详见 `docs/打单助手V6-软件设计说明书.md` 第 2.2 节。

## 验证

```bash
.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"   # 264 个单测
.venv/Scripts/python.exe 启动器.py --自检                                # 装配冒烟（含补丁）
.venv/Scripts/python.exe tools/静态导入校验.py                           # AST 校验包内导入
```

## 注意

- 仓库为公开仓库：`config_local.py`（真实密钥）、`build/`、`dist/`、`导入/`、`*.log` 均在 `.gitignore` 中。
- 发布给外部前删掉发布目录里的 `config_local.py`，让对方用 `config_local.example.py` 自填密钥。
- `data/` 是「越用越智能」的积累数据，构建时存在即不覆盖。
