@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"
set "ACTIVE_EXECUTION_SURFACE=TRADINGVIEW"
set "BROWSER_CDP_URL=http://127.0.0.1:9222"
start "VcaniTrade AI" /D "%SCRIPT_DIR%" "C:\Users\vijin\AppData\Local\Programs\Python\Python311\python.exe" "main.py"
endlocal