@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"
set "ACTIVE_EXECUTION_SURFACE=TRADINGVIEW"
set "BROWSER_CDP_URL=http://127.0.0.1:9222"
set "VENV_PY=%SCRIPT_DIR%\.venv\Scripts\python.exe"
set "STARTUP_LOG=%SCRIPT_DIR%\startup_log.txt"
if not exist "%VENV_PY%" (
    echo ERROR: Virtual environment not found at "%VENV_PY%"
    echo Run install.ps1 or start.bat once to repair the environment.
    pause
    exit /b 1
)

echo ============================================================>>"%STARTUP_LOG%"
echo [%DATE% %TIME%] Start-VcanTrade.cmd launching VcaniTrade AI>>"%STARTUP_LOG%"
echo ============================================================>>"%STARTUP_LOG%"
echo Launching VcaniTrade AI from:
echo %SCRIPT_DIR%
echo.
echo Logs: %STARTUP_LOG%
echo This window will stay open if the bot stops or crashes.
echo.
"%VENV_PY%" "%SCRIPT_DIR%\main.py" 1>>"%STARTUP_LOG%" 2>>&1
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo VcaniTrade stopped with exit code %EXIT_CODE%.
echo Check startup_log.txt and vcani_trade.log for the exact reason.
pause
exit /b %EXIT_CODE%
endlocal
