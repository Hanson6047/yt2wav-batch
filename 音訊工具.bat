@echo off
:: 雙擊開啟音訊整理工具的選單
:: chcp 65001：讓命令提示字元用 UTF-8，中文才不會亂碼
:: PYTHONIOENCODING：讓 Python 也用 UTF-8 輸出
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
python "%~dp0audio_tool.py"
pause
