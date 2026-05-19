@echo off
REM VcaniTrade AI - Clean Launcher
REM Kills old processes, starts Ollama, runs the bot

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

echo ============================================
echo VcaniTrade AI - Starting...
echo ============================================

REM Kill old Python processes
taskkill /F /IM python.exe 2>nul
timeout /t 2 /nobreak >nul

REM Start Ollama if not running
tasklist | findstr ollama.exe >nul
if %ERRORLEVEL% NEQ 0 (
    echo Starting Ollama...
    start /B ollama serve
    timeout /t 5 /nobreak >nul
)

echo Ollama is running
echo Starting VcaniTrade AI...
echo.

REM Run the bot
cd /d "%SCRIPT_DIR%"
"C:\Users\vijin\AppData\Local\Programs\Python\Python311\python.exe" main.py
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo VcaniTrade exited with code %EXIT_CODE%

pause
exit /b %EXIT_CODE%
