# -*- coding: utf-8 -*-
"""密钥配置文件。

⚠️ 真实密钥**不要**写进本文件提交到公开仓库。

配置密钥的三种方式（按优先级从高到低）：
  1. 环境变量：TENCENT_SECRET_ID / TENCENT_SECRET_KEY / DEEPSEEK_API_KEY
  2. 本地文件 config_local.py（已加入 .gitignore，不会被提交），
     在里面直接写三个变量即可
  3. 直接在本文件底部填写（仅限个人私有环境，勿提交）

读取顺序：环境变量 > config_local.py > 本文件占位符。
"""

import os
from pathlib import Path


def _读本地密钥():
    local = Path(__file__).with_name("config_local.py")
    if not local.is_file():
        return {}
    ns = {}
    try:
        exec(compile(local.read_text(encoding="utf-8"), str(local), "exec"), ns)
    except Exception:
        return {}
    return ns


_LOCAL = _读本地密钥()


def _取值(名):
    return os.environ.get(名) or _LOCAL.get(名) or ""


# 占位符为空，提交到公开仓库时不会泄露真实密钥。
TENCENT_SECRET_ID = _取值("TENCENT_SECRET_ID")
TENCENT_SECRET_KEY = _取值("TENCENT_SECRET_KEY")
DEEPSEEK_API_KEY = _取值("DEEPSEEK_API_KEY")
