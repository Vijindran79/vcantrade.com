@echo off
REM ===========================================================================
REM VcanTrade AI - Daily Launcher (Windows)
REM Double-click this OR the Desktop shortcut to start the bot.
REM ===========================================================================

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"

echo ============================================
echo VcanTrade AI - Starting...
echo ============================================
echo.

REM Stop any old Python instance from a previous run
taskkill /F /IM python.exe 2>nul >nul
timeout /t 2 /nobreak >nul

REM Make sure Ollama is running
tasklist /FI "IMAGENAME eq ollama.exe" 2>nul | find /I "ollama.exe" >nul
if errorlevel 1 (
    echo Starting Ollama...
    start /B "" ollama serve
    timeout /t 5 /nobreak >nul
)

REM Use the virtual environment Python
set "VENV_PY=%SCRIPT_DIR%\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo.
    echo ERROR: Virtual environment not found.
    echo Re-run install.ps1 to fix this.
    echo.
    pause
    exit /b 1
)

echo Launching VcanTrade AI...
echo.
"%VENV_PY%" main.py
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo VcanTrade exited with code %EXIT_CODE%
pause
exit /b %EXIT_CODE%
