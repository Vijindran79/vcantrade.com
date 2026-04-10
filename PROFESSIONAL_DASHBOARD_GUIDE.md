# 🚀 VcaniTrade AI - Professional Prop Trading Dashboard

## ✅ **What's Been Built For You:**

### **1. Professional Dashboard - All Controls Visible**
✅ **Scrollable layout** - everything is accessible, no hidden buttons
✅ **900px wide** - fits on any screen, uses 85% of monitor height
✅ **Always on top** - stays visible while you work

---

## 📊 **Dashboard Sections (Top to Bottom):**

### **💰 Account Panel (Top Step Funding)**
Shows in real-time:
- **Balance**: Your account balance
- **Equity**: Current equity (balance + unrealized P&L)
- **Daily P&L**: Today's profit/loss (resets at midnight)
- **Total P&L**: All-time profit/loss
- **Max Drawdown**: Worst drawdown from peak (prop firm critical!)
- **Trades Today**: Number of trades executed

### **⚙️ Trading Controls**
- **Mode Buttons**:
  - 👨‍🏫 **TEACHER**: Shows approval dialog for each signal (SAFE - Recommended)
  - 🤖 **AUTONOMOUS**: Auto-executes trades (RISKY - Only when confident)

- **💵 Default Investment ($)**: 
  - Set your standard amount (e.g., $10, $100, $1000)
  - Used for auto-execution in AUTONOMOUS mode
  
- **Take Profit (%)**: 
  - Default: 2%
  - Bot automatically closes position when price hits this
  
- **Stop Loss (%)**: 
  - Default: 1%
  - Bot automatically cuts loss when price goes against you
  
- **Max Daily Loss ($)**: 
  - Default: $500
  - Bot STOPS ALL TRADING if daily loss hits this (prop firm protection!)

- **💾 Save Settings**: Saves all your preferences

### **📊 Watchlist Management**
- **Add tickers**: Type any symbol (BTC-USD, AAPL, XAUUSD, etc.) and click "Add"
- **Remove tickers**: Select from table, click "Remove Selected"
- **Live status**: Shows which tickers are being monitored
- **Default 10 tickers**:
  1. **BTC-USD** (Bitcoin - 24/7 trading)
  2. **ETH-USD** (Ethereum - 24/7 trading)
  3. **GC=F** (Gold)
  4. **EURUSD=X** (Euro/USD Forex)
  5. **GBPUSD=X** (GBP/USD Forex)
  6. **TSLA** (Tesla)
  7. **SPY** (S&P 500 ETF)
  8. **QQQ** (NASDAQ ETF)
  9. **AAPL** (Apple)
  10. **NVDA** (NVIDIA)

### **📈 Live Positions (Auto-Monitored)**
Real-time table showing:
- **Asset**: What you're trading
- **Side**: BUY or SELL
- **Entry**: Entry price
- **Current**: Live current price (updates every 5 seconds)
- **P&L ($)**: Profit/loss in dollars
- **P&L (%)**: Profit/loss percentage
- **TP**: Take profit price
- **SL**: Stop loss price

**Bot automatically:**
- ✅ Monitors all open positions every 5 seconds
- ✅ Closes position when Take Profit is hit
- ✅ Closes position when Stop Loss is hit
- ✅ Updates P&L in real-time
- ✅ Logs all activity

### **📜 Trade Log & Activity**
- **Trade History Table**: All trades with time, asset, action, amount, P&L, status
- **Activity Log**: Real-time text log of all bot actions and decisions

### **🛑 Kill Switch**
- **EMERGENCY STOP**: Instantly halts all trading and disables the dashboard

---

## 🎯 **How It Works - Step by Step:**

### **Step 1: Bot Scans the Market**
- Every 10 seconds, bot checks all 10 tickers in your watchlist
- Looks for: RSI signals, Volume spikes, SMA crossovers
- Bitcoin/Ethereum trade 24/7, stocks trade during market hours

### **Step 2: AI Analyzes the Signal**
- When signal detected, AI (llama3.2) analyzes it
- Takes 2-5 seconds (local model on your GPU)
- Returns: BUY/SELL, entry price, TP, SL, confidence %

### **Step 3: You See the Signal**
Dashboard shows:
```
☁️ CLOUD SCANNER: BUY BTC-USD (confidence: 0.85)
```

### **Step 4: Approval Dialog Pops Up**
```
🚀 Trade Signal: BUY BTC-USD
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Confidence: 85%
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Signal Type: VOLUME_SPIKE
Entry Price: $65,432.00
Stop Loss:   $64,777.68
Take Profit: $66,740.64
Reason: Strong volume spike detected with bullish momentum
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💵 Investment Amount ($): [  10  ]

[✅ APPROVE & EXECUTE]  [❌ REJECT]
```

### **Step 5: You Approve**
- Enter amount (e.g., $10)
- Click "APPROVE & EXECUTE"
- Bot opens position with TP/SL set

### **Step 6: Bot Monitors Position**
Every 5 seconds, bot checks:
- **Current price vs Take Profit**: If hit → closes position, books profit
- **Current price vs Stop Loss**: If hit → closes position, cuts loss
- Updates P&L in real-time

### **Step 7: Position Closes Automatically**
- **Take Profit Hit**: 
  ```
  🎯 TAKE PROFIT HIT: BTC-USD @ $66,740.64 | P&L: +$2.50
  ✅ Position closed: BTC-USD | Take Profit | P&L: $2.50
  ```

- **Stop Loss Hit**:
  ```
  🛑 STOP LOSS HIT: BTC-USD @ $64,777.68 | P&L: -$1.00
  ✅ Position closed: BTC-USD | Stop Loss | P&L: -$1.00
  ```

---

## ⚡ **Key Features:**

### **1. No Emotions - Pure Rules**
- Bot follows TP/SL exactly
- No "hoping" price will come back
- No "holding" losing trades
- No "greed" on winning trades

### **2. Prop Firm Protection**
- **Max Daily Loss**: Stops trading if you hit limit
- **Drawdown Tracking**: Monitors peak-to-trough decline
- **Trade Logging**: Every trade recorded for review

### **3. 24/7 Monitoring**
- Bitcoin/Ethereum: Always trading (crypto never sleeps)
- Forex: 24/5 (Sunday 5pm EST - Friday 5pm EST)
- Stocks: Market hours (9:30am - 4pm EST)

### **4. Fast Local Execution**
- AI model runs on your RTX 4050 (6GB VRAM)
- Response time: 2-5 seconds (not 60+ seconds)
- No internet dependency for AI

---

## 🚀 **How to Run:**

```powershell
cd C:\Users\vijin\vcantrade.com-2
python main.py
```

### **What You'll See:**

**Dashboard opens with:**
- Account: $10,000 balance
- Watchlist: 10 tickers being monitored
- Activity Log: "✅ All systems connected - Ready to trade"

**Within 10-30 seconds:**
- Scanner checks all 10 tickers
- If signal detected → AI analyzes
- Dialog pops up for approval
- You approve → Position opens
- Bot monitors until TP/SL hit

---

## 💰 **Example Trade Flow:**

1. **10:00 AM**: Bot detects volume spike on BTC-USD @ $65,000
2. **10:00:05 AM**: Dialog pops up: "BUY BTC-USD - 85% confidence"
3. **You**: Enter $10, click APPROVE
4. **10:00:10 AM**: Position opened
   - Entry: $65,000
   - TP: $66,300 (2%)
   - SL: $64,350 (1%)
5. **10:15 AM**: Price hits $66,300 → TP triggered
6. **Bot closes**: +$2.03 profit logged
7. **Dashboard updates**: Balance $10,002.03, Daily P&L +$2.03

---

## 🎮 **Your Job as the Trader:**

1. **Set your investment amount** (e.g., $10 per trade)
2. **Adjust TP/SL** based on your strategy (e.g., 2% TP, 1% SL)
3. **Watch the signals** come in on the dashboard
4. **Approve or reject** each trade (in TEACHER mode)
5. **Monitor positions** close automatically at TP/SL
6. **Review trade log** at end of day

---

## 🛡️ **Safety Features:**

✅ **DRY_RUN=True** by default (paper trading - no real money)
✅ **TEACHER_MODE=True** by default (you approve every trade)
✅ **Max Daily Loss** stops trading if limit hit
✅ **Stop Loss** on every position (no unlimited losses)
✅ **Kill Switch** instantly stops everything
✅ **Position Monitoring** updates every 5 seconds

---

## 🎯 **Next Steps:**

1. **Run the bot**: `python main.py`
2. **Watch for signals** in the dashboard
3. **Approve trades** when dialog pops up
4. **See positions** open/close automatically
5. **Review results** in trade log

**The bot is now a professional, rule-based trading system that monitors markets 24/7 and executes with discipline!** 💰🔥
