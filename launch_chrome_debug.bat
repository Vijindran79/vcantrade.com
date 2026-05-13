@echo off
echo ====================================
echo  LION BOT - Chrome Launcher (Debug Mode)
echo ====================================
echo.

REM Check if Chrome is already running with debug port
netstat -ano | find "9222" >nul 2>&1
if %errorlevel%==0 (
    echo [WARNING] Port 9222 is already in use!
    echo.
    echo Options:
    echo   1. Close existing Chrome windows and try again
    echo   2. Use a different port in config.py
    echo.
    pause
    exit /b 1
)

REM Set Chrome path
set CHROME_PATH="C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist %CHROME_PATH% (
    set CHROME_PATH="C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
)

if not exist %CHROME_PATH% (
    echo [ERROR] Chrome not found! Please install Chrome or update CHROME_PATH.
    pause
    exit /b 1
)

REM Create debug directory if it doesn't exist
set DEBUG_DIR=C:\Users\vijin\ChromeDebug_LionBot
if not exist "%DEBUG_DIR%" mkdir "%DEBUG_DIR%"

echo [INFO] Launching Chrome with remote debugging...
echo [INFO] Debug Port: 9222
echo [INFO] User Data: %DEBUG_DIR%
echo.

REM Launch Chrome with remote debugging
start "" %CHROME_PATH% ^
    --remote-debugging-port=9222 ^
    --user-data-dir="%DEBUG_DIR%" ^
    --no-first-run ^
    --no-default-browser-check ^
    "https://app.wealthcharts.com"

echo [SUCCESS] Chrome launched!
echo.
echo Next steps:
echo   1. Log into WealthCharts in the Chrome window
echo   2. Run your execution_server.py
echo   3. Let the Lion trade!
echo.
pause
