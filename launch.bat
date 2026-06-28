@echo off
REM ===========================================================================
REM VcanTrade AI - ONE CLICK LAUNCHER
REM ===========================================================================
REM Put this on your Desktop. Double-click. That's it.
REM ===========================================================================

title VcanTrade AI

REM --- Go to the bot folder ---
cd /d "C:\Users\vijin\vcantrade.com-4"

REM --- Kill any old bot still running ---
taskkill /F /IM python.exe 2>nul >nul
timeout /t 2 /nobreak >nul

REM --- Start Ollama if not running ---
tasklist /FI "IMAGENAME eq ollama.exe" 2>nul | find /I "ollama.exe" >nul
if errorlevel 1 (
    echo Starting Ollama...
    start /B "" ollama serve
    timeout /t 4 /nobreak >nul
)

REM --- Start MetaTrader 5 if not running ---
tasklist /FI "IMAGENAME eq terminal64.exe" 2>nul | find /I "terminal64.exe" >nul
if errorlevel 1 (
    echo Starting MetaTrader 5...
    if exist "%ProgramFiles%\MetaTrader 5\terminal64.exe" (
        start "" "%ProgramFiles%\MetaTrader 5\terminal64.exe"
    ) else if exist "%ProgramFiles(x86)%\MetaTrader 5\terminal64.exe" (
        start "" "%ProgramFiles(x86)%\MetaTrader 5\terminal64.exe"
    ) else if exist "%AppData%\MetaQuotes\Terminal\*\terminal64.exe" (
        for /f "delims=" %%i in ('dir /b /s "%AppData%\MetaQuotes\Terminal\*\terminal64.exe" 2^>nul') do start "" "%%i"
    ) else (
        echo WARNING: MetaTrader 5 not found. MT5 data feed will be unavailable.
        echo Install MT5 or update the path in launch.bat.
    )
    echo Waiting for MT5 to initialize...
    timeout /t 10 /nobreak >nul
) else (
    echo MetaTrader 5 already running.
)

REM --- Open TradingView in Chrome with debug port (if not already open) ---
curl -s -o nul --max-time 2 http://127.0.0.1:9222/json/version >nul 2>&1
if errorlevel 1 (
    echo Opening TradingView...
    if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" (
        start "" "%LocalAppData%\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%LocalAppData%\VcanTrade\tv_profile" "https://www.tradingview.com/chart/"
    ) else if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
        start "" "%ProgramFiles%\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%LocalAppData%\VcanTrade\tv_profile" "https://www.tradingview.com/chart/"
    ) else if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" (
        start "" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222 --user-data-dir="%LocalAppData%\VcanTrade\tv_profile" "https://www.tradingview.com/chart/"
    ) else (
        echo ERROR: No Chrome or Edge found. Install Chrome first.
        pause
        exit /b 1
    )
    echo Waiting for browser...
    timeout /t 8 /nobreak >nul
) else (
    echo Browser already running on port 9222.
)

REM --- Start the bot ---
echo.
echo ============================================
echo   VcanTrade AI - Starting...
echo ============================================
echo.

".venv\Scripts\python.exe" main.py

echo.
echo Bot stopped. Press any key to close.
pause >nul
