@echo off
REM ===========================================================================
REM VcanTrade AI - ONE-CLICK LAUNCHER (Windows)
REM ---------------------------------------------------------------------------
REM This is the only thing the trader should double-click.
REM
REM What it does (in order):
REM   1. Checks if a CDP browser is already on port 9222 - reuses if yes
REM   2. Launches TradingView Desktop OR Chrome OR Edge with the correct flag
REM      (--remote-debugging-port=9222) so the bot can see the chart
REM   3. Waits up to 20 seconds for the browser to be ready
REM   4. Starts the bot
REM
REM IMPORTANT:
REM   ALWAYS launch via this script. If you open Chrome / TradingView a
REM   different way, the bot won't see your charts.
REM ===========================================================================

setlocal EnableExtensions EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"

echo ============================================================
echo  VcanTrade AI - One-Click Launcher
echo ============================================================
echo.

REM ------------------------------------------------------------------
REM 1. If port 9222 is already serving a CDP browser, reuse it.
REM ------------------------------------------------------------------
set "ALREADY_RUNNING="
for /f %%C in ('curl -s -o nul -w "%%{http_code}" --max-time 2 http://127.0.0.1:9222/json/version 2^>nul') do set "ALREADY_RUNNING=%%C"
if "%ALREADY_RUNNING%"=="200" (
    echo [1/3] CDP browser already running on port 9222 - reusing.
    goto :start_bot
)

echo [1/3] Looking for TradingView / Chrome / Edge ...

REM ------------------------------------------------------------------
REM 2. Find a browser and launch it with the debugging port.
REM    Order: TradingView Desktop -> Chrome -> Edge
REM    Uses a dedicated profile dir so your normal browser is untouched.
REM ------------------------------------------------------------------
set "TV_PROFILE=%LocalAppData%\VcanTrade\tv_profile"
if not exist "%TV_PROFILE%" mkdir "%TV_PROFILE%" >nul 2>&1

set "LAUNCHED="

REM --- Try TradingView Desktop ---
set "TV_DESKTOP_PATH="
if exist "%LocalAppData%\Programs\TradingView\TradingView.exe"     set "TV_DESKTOP_PATH=%LocalAppData%\Programs\TradingView\TradingView.exe"
if exist "%ProgramFiles%\TradingView\TradingView.exe"             set "TV_DESKTOP_PATH=%ProgramFiles%\TradingView\TradingView.exe"
if defined TV_DESKTOP_PATH (
    start "" "%TV_DESKTOP_PATH%" --remote-debugging-port=9222
    set "LAUNCHED=TradingView Desktop"
    goto :wait_for_browser
)

REM --- Try Chrome ---
set "CHROME_PATH="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe"      set "CHROME_PATH=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_PATH=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe"      set "CHROME_PATH=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if defined CHROME_PATH (
    start "" "%CHROME_PATH%" --remote-debugging-port=9222 --user-data-dir="%TV_PROFILE%" "https://www.tradingview.com/chart/"
    set "LAUNCHED=Chrome"
    goto :wait_for_browser
)

REM --- Try Edge ---
set "EDGE_PATH="
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"      set "EDGE_PATH=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "EDGE_PATH=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if defined EDGE_PATH (
    start "" "%EDGE_PATH%" --remote-debugging-port=9222 --user-data-dir="%TV_PROFILE%" "https://www.tradingview.com/chart/"
    set "LAUNCHED=Microsoft Edge"
    goto :wait_for_browser
)

echo.
echo [ERROR] Could not find TradingView Desktop, Chrome, or Edge.
echo Install one of them and run this launcher again.
echo   - Chrome:    https://www.google.com/chrome/
echo   - Edge:      already on Windows 10/11
echo   - TradingView Desktop: https://www.tradingview.com/desktop/
echo.
pause
exit /b 1

REM ------------------------------------------------------------------
REM 3. Wait until the browser's CDP endpoint is up (max 20 seconds).
REM ------------------------------------------------------------------
:wait_for_browser
echo     Launched %LAUNCHED%. Waiting for it to be ready ...
set /a "tries=0"
:wait_loop
timeout /t 1 /nobreak >nul
set "READY="
for /f %%C in ('curl -s -o nul -w "%%{http_code}" --max-time 1 http://127.0.0.1:9222/json/version 2^>nul') do set "READY=%%C"
if "%READY%"=="200" goto :start_bot
set /a "tries+=1"
if %tries% LSS 20 goto :wait_loop
echo [WARN] Browser did not respond on port 9222 within 20 seconds.
echo        Bot will still start. If trades fail to fire, close the browser
echo        and run this launcher again.

REM ------------------------------------------------------------------
REM 4. Start the bot via start.bat (which uses the venv Python).
REM ------------------------------------------------------------------
:start_bot
echo.
echo [2/3] Browser is ready on port 9222.
echo [3/3] Starting VcanTrade AI ...
echo.
call "%SCRIPT_DIR%\start.bat"
endlocal
