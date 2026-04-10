@echo off
REM VcaniTrade AI - Clean Launcher
REM Kills old processes, starts Ollama, runs the bot

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
cd /d C:\Users\vijin\vcantrade.com-2
python main.py

pause
