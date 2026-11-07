@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 结构服务（PP-Structure sidecar）由 _界面补丁\加载.py 自动拉起，
rem 这里只负责启动主程序。
start "" "打单助手V6.exe"
