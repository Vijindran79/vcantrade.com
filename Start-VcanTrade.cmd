@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"
start "VcaniTrade AI" /D "%SCRIPT_DIR%" "C:\Program Files\Python312\python.exe" "main.py"
endlocal