@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"
set "ACTIVE_EXECUTION_SURFACE=TRADINGVIEW"
set "BROWSER_CDP_URL=http://127.0.0.1:9222"
set "VENV_PY=%SCRIPT_DIR%\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo ERROR: Virtual environment not found at "%VENV_PY%"
    echo Run install.ps1 or start.bat once to repair the environment.
    pause
    exit /b 1
)
start "VcaniTrade AI" /D "%SCRIPT_DIR%" "%VENV_PY%" "main.py"
endlocal
