@echo off
echo ========================================
echo VcaniTrade AI - Setup Local Brain
echo ========================================
echo.

echo Step 1: Installing Playwright...
pip install playwright
echo.

echo Step 2: Installing Playwright browsers...
playwright install chromium
echo.

echo Step 3: Checking Ollama...
where ollama >nul 2>&1
if %errorlevel% neq 0 (
    echo WARNING: Ollama not found! Please install Ollama first.
    echo Download from: https://ollama.ai
    echo.
    echo Or run: curl -fsSL https://ollama.ai/install.sh | sh
    echo.
) else (
    echo Ollama found!
)
echo.

echo Step 4: Pulling Qwen 2.5:7b model...
ollama pull qwen2.5:7b
echo.

echo Step 5: Verifying installation...
python -c "from core.browser_agent import BrowserAgent; print('Browser Agent OK')"
python -c "from core.brain_swarm import call_local_brain; print('Local Brain OK')"
echo.

echo ========================================
echo Setup Complete!
echo ========================================
echo.
echo To start the bot:
echo   python main.py
echo.
echo Make sure Ollama is running first:
echo   ollama serve
echo.
pause
