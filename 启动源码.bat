@echo off
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\pythonw.exe" "启动器.py"
