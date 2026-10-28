@echo off
:: 雙擊開啟音訊整理工具的選單
:: 有裝 Windows Terminal 就改用它開（顏色、圖示顯示比較完整）；沒裝就照舊用命令提示字元
:: WT_SESSION 是 Windows Terminal 會設定的變數，有它代表已經在 Windows Terminal 裡了
if not defined WT_SESSION (
    where wt >nul 2>nul && (
        start "" wt -d "%~dp0." cmd /c "%~f0"
        exit /b
    )
)
:: chcp 65001：讓命令提示字元用 UTF-8，中文才不會亂碼
:: PYTHONIOENCODING：讓 Python 也用 UTF-8 輸出
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
python "%~dp0audio_tool.py"
pause
