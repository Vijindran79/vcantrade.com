# VcanTrade AI

Autonomous trading bot for **TradingView Desktop** and **MetaTrader 5**, configured for **Apex Trader Funding**.
One switch on the dashboard chooses where the orders go. Both **Teacher Mode** (analyze only) and **Autonomous Mode** (auto-execute) are supported.

---

## ⚡ Install in one line

> Replace `Vijindran79/vcantrade.com` in the commands below with your actual GitHub link once you push this code.

There are **two installers**. Pick the one that matches the user.

### 🟢 Standard install (Teacher + Autonomous, both modes available)

For traders who want the bot to be able to click on its own.

**Windows** — open **PowerShell** and paste:

```powershell
iwr -useb https://raw.githubusercontent.com/Vijindran79/vcantrade.com/main/install.ps1 | iex
```

**macOS / Linux** — open **Terminal** and paste:

```bash
curl -fsSL https://raw.githubusercontent.com/Vijindran79/vcantrade.com/main/install.sh | bash
```

### 🟣 Teacher-only install (suggestions only, never clicks)

For traders who want the bot to analyze and alert, but always click Buy/Sell themselves. The Autonomous button is permanently greyed out on the dashboard.

**Windows** — open **PowerShell** and paste:

```powershell
iwr -useb https://raw.githubusercontent.com/Vijindran79/vcantrade.com/main/install-teacher.ps1 | iex
```

**macOS / Linux** — open **Terminal** and paste:

```bash
curl -fsSL https://raw.githubusercontent.com/Vijindran79/vcantrade.com/main/install-teacher.sh | bash
```

That's it. The installer will:
1. Install Python 3.11 (if missing)
2. Install Git (if missing)
3. Download the bot to `%USERPROFILE%\VcanTrade` (Windows) or `~/VcanTrade` (Mac/Linux)
4. Install all Python dependencies inside a clean virtual environment
5. Drop a **VcanTrade** shortcut on the Desktop (Windows) or set up `start.sh` (Mac/Linux)

When it finishes, **double-click the Desktop icon** (Windows) or **run `~/VcanTrade/start.sh`** (Mac/Linux) to start the bot.

> **Important for Mac users:** MetaTrader 5 has no Mac Python connector. If you trade through MT5, install the bot on the Windows machine that runs MT5 (or a Parallels Windows VM). The Mac install only supports TradingView mode.

---

## 🚀 Daily use

### Before you start the bot
1. **Start Ollama.** In a separate terminal, run `ollama serve` (Mac/Linux) or just open the Ollama app (Windows).
2. **Pull the model** (one-time only):
   ```bash
   ollama pull qwen2.5:1.5b-instruct-q4_K_M
   ollama pull llava:7b
   ```
   On a small Mac the 1.5b text model and llava:7b vision model are enough.
3. **Open MetaTrader 5** *or* **TradingView Desktop**, depending on which one you're trading.
   - For **TradingView Desktop** specifically, launch it with remote debugging enabled (the bot connects via Chrome DevTools on port 9222). The bot's launcher does this automatically.
   - For **MetaTrader 5**, just have it open and logged into your Apex account. Enable Algo Trading in the toolbar.

### Start the bot
- **Windows:** double-click the **VcanTrade** icon on the Desktop. That's it. The launcher will:
  1. Open Chrome / Edge / TradingView Desktop on port 9222 (the port the bot listens on)
  2. Wait for it to be ready
  3. Start the bot

  > **Always launch this way.** If you open Chrome a different way (or someone else's Chrome window is already open without the debug port), the bot won't see your charts.
  >
  > If the Desktop icon ever goes missing, run `create-desktop-shortcut.ps1` (right-click → "Run with PowerShell") to put it back.

- **Mac/Linux:** run `~/VcanTrade/start.sh`

### Flip the surface switch (in the bot dashboard)
The dashboard has two big buttons:
- **TradingView** → orders go to TradingView Desktop via JS click (used by you).
- **MetaTrader 5** → orders go to MT5 via the native API (used by your brother).

Press whichever one matches your platform. The bot remembers it.

### Teacher vs Autonomous
- **Teacher Mode:** the bot reads the chart, runs its analysis, and tells you what it would do — but doesn't click. Use this for the first day.
- **Autonomous Mode:** the bot clicks Buy/Sell on its own using all safety guards.

The mode is picked from the same dashboard. Start in Teacher Mode, watch it for a session, then flip to Autonomous when you trust the calls.

---

## 🔄 Update to the latest version

Whenever you push a new fix to GitHub, your brother can update with one line.

### Windows
```powershell
iwr -useb https://raw.githubusercontent.com/Vijindran79/vcantrade.com/main/install.ps1 | iex
```
Same line as the install. The installer detects an existing copy and just pulls the latest code.

### macOS / Linux
```bash
curl -fsSL https://raw.githubusercontent.com/Vijindran79/vcantrade.com/main/install.sh | bash
```

---

## 🛡️ Safety controls (already on by default)

- **`DRY_RUN=True`** in `.env` is the boot default. The bot will NOT click real orders until you set this to `False`.
- **News filter fails closed:** if Forex Factory can't be reached, the bot pauses trading instead of trading blind.
- **Walk-Away Protocol:** if daily loss crosses the threshold, the bot shuts down for 24 hours.
- **Apex preset:** `PROP_FIRM_NAME=Apex Trader Funding`, `PROP_ACCOUNT_SIZE=50000`, trailing-drawdown rules baked in.
- **One armed surface at a time:** when you flip to MT5, TradingView is silent — and vice versa. No accidental double orders.

---

## 📂 Where things live

```
%USERPROFILE%\VcanTrade\        (Windows)
~/VcanTrade/                    (Mac/Linux)
├── main.py              # the bot
├── start.bat / start.sh # daily launcher
├── .env                 # your settings — edit this for your account
├── requirements.txt     # Python dependencies
├── core/                # trading engine, surface router, risk
├── ui/                  # dashboard
└── threads/             # background workers
```

The only file you usually need to edit is `.env`.

---

## ❓ Troubleshooting

**"Python 3.11 not found"** during install on Windows
Open PowerShell as Administrator and re-run the install line.

**"Cannot connect to TradingView Desktop"**
TradingView Desktop must be running with remote debugging enabled. Close it fully, then start it again from the bot's shortcut so the launcher passes the right flag.

**"MT5 executor not initialised"**
Open MetaTrader 5 first. Click `Tools → Options → Expert Advisors` and tick **Allow algorithmic trading**. Restart the bot.

**"Ollama not running"**
Run `ollama serve` in a separate terminal, or start the Ollama desktop app.

**Bot reacts too slowly when price drops fast**
You're already running the new build — the old yfinance probe that caused 5-second delays is gone. Live ticks come from MT5 (microseconds) on the MT5 surface, and TradingView clicks fire instantly because the JS executes against the visible Buy/Sell buttons.

---

## 🗺️ What's planned next

- ATR-based stops and trailing (replacing the fixed `$15/$30/$2` thresholds)
- Single unified position sizer (volatility-targeted, Kelly-fractional)
- Real backtest harness so every threshold change is gated on out-of-sample Sharpe
- Per-route latency dashboard

Open an issue or DM if something breaks. The bot logs everything to `vcani_trade.log`.
