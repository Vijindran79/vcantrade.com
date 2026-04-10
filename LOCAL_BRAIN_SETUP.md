# 🧠 Local Brain Setup Guide

Your VcaniTrade AI now runs **100% locally** with:
- **Qwen 2.5:7b** as the trading analyst brain
- **Playwright** for autonomous browser price checking
- **No cloud dependencies** - no API tokens needed!

---

## 🚀 Quick Setup (3 Steps)

### 1️⃣ Install Ollama (if not already installed)

**Windows:**
```powershell
# Download from https://ollama.ai
# Or use winget:
winget install Ollama.Ollama
```

**Start Ollama:**
```powershell
ollama serve
```

### 2️⃣ Pull Qwen 2.5 Model

```powershell
# Pull the 7B model (requires ~4GB disk space)
ollama pull qwen2.5:7b

# Verify it's downloaded
ollama list
```

### 3️⃣ Setup Playwright Browser Agent

**Run the setup script:**
```powershell
.\setup_local_brain.bat
```

**Or manually:**
```powershell
# Install playwright
pip install playwright

# Install Chromium browser
playwright install chromium
```

---

## ✅ Verify Installation

```powershell
# Test Ollama connection
curl http://localhost:11434/api/tags

# Test local brain
python -c "from core.swarm_consensus import call_local_brain; print(call_local_brain('What is 2+2?'))"

# Test browser agent
python -c "from core.browser_agent import BrowserAgent; print('Browser Agent OK')"
```

---

## 🎯 Usage

### Start the Trading Bot:
```powershell
# Make sure Ollama is running first!
ollama serve

# Then start the bot (in another terminal)
python main.py
```

### What Happens Automatically:

1. **Market Scanning** - Monitors 10 tickers every 10 seconds
2. **Signal Detection** - Detects RSI, Volume, SMA signals
3. **Qwen 2.5 Analysis** - When signal detected, calls local brain
4. **Trade Execution** - If you approve, opens position with TP/SL
5. **Browser Fallback** - If yfinance fails, browser agent checks TradingView autonomously!

---

## 🌐 Browser Agent Capabilities

The bot can now **autonomously**:

✅ Open TradingView and read live prices  
✅ Check Yahoo Finance for verification  
✅ Average multiple sources for accuracy  
✅ Take screenshots for vision analysis  
✅ Navigate web pages without human help  

**When it's used:**
- Automatically when yfinance fails to get price data
- As a fallback for position monitoring
- You can trigger it manually for any symbol

---

## ⚙️ Configuration (config.py)

All local, no cloud needed:

```python
# Local Ollama brain
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b"  # Your local AI analyst
LLM_TIMEOUT = 60  # Generous timeout for 7B model

# Vision disabled by default for speed
USE_VISION = False

# No cloud tokens!
VAST_API_TOKEN = None
```

---

## 🔧 Troubleshooting

### "Cannot connect to Ollama"
```powershell
# Check if Ollama is running
ollama serve

# Test connection
curl http://localhost:11434/api/generate -d '{"model":"qwen2.5:7b","prompt":"test","stream":false}'
```

### "Browser agent failed"
```powershell
# Reinstall playwright browsers
playwright install chromium

# Test browser agent
python core/browser_agent.py
```

### "Model not found"
```powershell
# Pull the model again
ollama pull qwen2.5:7b

# Check available models
ollama list
```

---

## 📊 What Changed from Before

| Feature | Before | Now |
|---------|--------|-----|
| AI Brain | Cloud (Groq/Vast.ai) | **Local Qwen 2.5:7b** |
| API Tokens | Required | **Not needed!** |
| Price Data | yfinance only | **yfinance + Browser Agent** |
| Fallback | None | **Autonomous browser checks** |
| Privacy | Data sent to cloud | **100% local!** |

---

## 🎉 You're Ready!

Your bot now:
- 🧠 Thinks with **Qwen 2.5** locally
- 🌐 Browses the web autonomously with **Playwright**
- 📊 Checks prices from multiple sources
- 🤖 Works completely independently
- 🔒 Keeps all data private on your machine

**Just run:**
```powershell
ollama serve
python main.py
```

And watch your AI narrator tell you what it's doing! 🚀
