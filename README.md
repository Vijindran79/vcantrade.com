# 🎯 VcaniTrade AI - Intelligent Trading Assistant

**AI-powered trading assistant with transparent overlay HUD, teacher mode, and safety-first execution**

---

## 🌟 Features

### ✨ **Transparent Glass Overlay HUD**
- Always-on-top overlay floats above your trading platform (MetaTrader, TradingView, etc.)
- Shows Entry, Stop Loss, and Take Profit zones with color coding
- Displays AI reasoning in plain English
- **100% click-through** - never blocks your mouse or trading platform
- Updates in real-time with live signals

### 🎓 **Teacher Mode (Default)**
- AI analyzes markets and shows signals overlay
- **No automatic execution** - you manually trade based on AI suggestions
- Perfect for learning and validating AI accuracy
- Green zone = Take Profit target
- Red zone = Stop Loss level
- Plain English explanation of why AI recommends each trade

### 🤖 **Auto Mode (Optional)**
- AI executes trades automatically via RPA (keyboard hotkeys)
- Strict safety controls with kill switch
- Paper trading mode for testing
- Only enable when you're confident in the system

### 🛡️ **Safety Controls (Always Active)**
- **Kill Switch**: Emergency stop - halts all trading instantly
- **Paper Trading**: Dry-run mode logs trades without real money
- **Daily Loss Limit**: Auto-stops trading after max loss threshold
- **Cooldown Period**: Wait time after stop-loss hits
- **Max Positions**: Limits concurrent open trades
- **JSON Schema Validation**: LLM output strictly validated with Pydantic

### 📊 **AI Grader (Report Card)**
- Grades AI performance: A, B, C, D, F
- Analyzes win rate, profit factor, and sample size
- Grades by confidence level accuracy
- Identifies best trading hours and assets

### 🧠 **Local LLM Integration (Ollama)**
- Runs **100% locally** via Ollama - no API keys, no cloud, no privacy concerns
- Supports llama3, mistral, codellama, and other models
- Forces strict JSON output for reliable parsing
- Falls back to mock analysis if Ollama unavailable

---

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- (Optional) Ollama installed locally: https://ollama.ai

### Installation

```bash
# Install dependencies
pip install -r requirements.txt

# (Optional) Pull Ollama model
ollama pull llama3

# Run the app
python main.py
```

### What You'll See
1. **Control Window**: Mode switches, kill switch, activity log
2. **Transparent Overlay**: Glass HUD floating above your screen showing trading signals
3. **Live Updates**: Overlay refreshes every 2 seconds with new signals

---

## 🎮 How to Use

### First Run (Safe Mode)
1. App starts in **Teacher Mode + Paper Trading** by default
2. Transparent overlay shows AI signals above your chart
3. You manually trade based on AI suggestions
4. Log tracks all decisions for review

### After Testing (Optional Auto Mode)
1. Switch to **Auto Mode** in control panel
2. Keep **Paper Mode ON** for risk-free automated testing
3. Monitor log to see AI executing trades
4. Review performance grade in log

### Going Live (Not Recommended Without Extensive Testing)
⚠️ **Only after 2+ weeks of paper trading with positive results:**
1. Toggle **Paper Mode OFF** (switches to Live)
2. Set hotkeys in your trading platform:
   - `Ctrl+B` = Buy
   - `Ctrl+S` = Sell
   - `Ctrl+X` = Close position
3. Keep **Kill Switch** button ready
4. Start with minimal position sizes

---

## 📁 Project Structure

```
vcantrade.com/
├── main.py                      # Application entry point
├── config.py                    # Safety controls & settings
├── requirements.txt             # Python dependencies
├── core/
│   ├── models.py               # Pydantic data models
│   ├── llm_analyzer.py         # Ollama LLM integration
│   ├── trade_engine.py         # Trade execution & safety controls
│   └── grader.py               # Performance grading (A-F)
├── execution/
│   └── rpa_executor.py         # RPA via hotkeys/PyAutoGUI
└── ui/
    └── dashboard.py            # Control window + transparent overlay
```

---

## ⚙️ Configuration

Edit `config.py` to customize:

```python
# Safety Controls
DRY_RUN = True                   # Always True by default
MAX_DAILY_LOSS = 100.00          # Max loss per day
MAX_OPEN_POSITIONS = 3           # Concurrent position limit
COOLDOWN_AFTER_STOP = 300        # 5 min cooldown after stop loss

# Trading Mode
TEACHER_MODE = True              # Show signals only (no auto-execution)

# LLM Settings
OLLAMA_MODEL = "llama3"          # Local AI model
JSON_OUTPUT = True               # Strict JSON validation

# Overlay
OVERLAY_ALPHA = 0.15             # Transparency level
OVERLAY_UPDATE_MS = 2000         # Refresh rate (2 seconds)
```

---

## 🔒 Safety First

### Why This Architecture is Safe
| Feature | Purpose |
|---------|---------|
| **Paper Mode Default** | Can't lose money until you explicitly disable it |
| **Teacher Mode Default** | You control execution, AI only suggests |
| **Kill Switch** | One-click emergency halt |
| **Daily Loss Limit** | Prevents runaway losses |
| **Cooldown Period** | Stops revenge trading after losses |
| **Local LLM** | No cloud dependency, no API costs, 100% private |
| **JSON Schema** | Prevents LLM hallucination from breaking parser |
| **Hotkey Execution** | 3-5x faster than mouse, less error-prone |

### Testing Protocol (Recommended)
1. **Week 1-2**: Teacher Mode + Paper Trading only
2. **Week 3-4**: Review grader report, validate accuracy
3. **Week 5+**: If Grade A/B + 60%+ win rate, consider auto mode
4. **Month 2+**: If paper auto-mode profitable, test live with minimal size

---

## 🔮 Future Enhancements (Phase 2)

- [ ] Live WebSocket market data feed (ccxt, yfinance, Polygon.io)
- [ ] OpenCV screen calibration for any broker platform
- [ ] News sentiment pipeline (RSS + NewsAPI)
- [ ] Multi-timeframe analysis (1m, 5m, 15m, 1h)
- [ ] Backtesting engine with historical data
- [ ] Mobile notifications (Telegram/Discord bot)
- [ ] Performance dashboard web UI

---

## 🐛 Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError: PyQt6` | Run `pip install -r requirements.txt` |
| Overlay not visible | Press Alt+Tab; overlay stays on top but click-through |
| Ollama connection failed | App falls back to mock analysis automatically |
| Hotkeys not working | Configure hotkeys in your trading platform first |
| App won't start | Check Python 3.10+ with `python --version` |

---

## 📝 License

MIT License - Use at your own risk. This is a trading **assistant**, not financial advice.

---

## 🤝 Support

For issues, questions, or contributions, open an issue on GitHub.

**Built with ❤️ for safer, smarter trading**
