@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 创建虚拟环境...
  python -m venv .venv
)
echo 安装 PaddlePaddle + PaddleOCR + PaddleX OCR 扩展（首次约 1~2 GB，慢的话多等一会）...
".venv\Scripts\python.exe" -m pip install --upgrade pip --timeout 120 --retries 10
".venv\Scripts\python.exe" -m pip install --timeout 120 --retries 10 paddlepaddle paddleocr "paddlex[ocr]"
echo.
echo 装完后：双击 启动服务.bat，或直接启动 V6（主程序会自动拉起服务）。
pause
