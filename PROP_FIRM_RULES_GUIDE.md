# 🎓 Prop Firm Rule Engine - "The Professor"

## What Is It?

"The Professor" is your **prop firm compliance expert**. It knows every rule for every major prop firm and **enforces them automatically** before every trade.

---

## 🎯 How It Works:

### **1. Before Every Trade:**
```
Signal Detected → Professor Checks Rules → Allows or Blocks Trade
```

Example:
```
Signal: BUY BTC-USD @ $85,000
Professor: "Checking TopStep rules..."
  ✅ Daily loss: $45 / $150 limit - OK
  ✅ Drawdown: $800 / $3000 limit - OK  
  ✅ No consistency violations
  ✅ Trading day count: 3 - OK
Result: ✅ TRADE ALLOWED
```

### **2. After Every Trade:**
```
Trade Closed → Professor Records P&L → Updates Compliance → Checks for Violations
```

Example:
```
Trade: SELL ETH-USD | P&L: -$75
Professor: "Updating compliance..."
  Daily P&L: $45 - $75 = -$30
  Drawdown from peak: $875
  Win rate: 60% (3W/2L)
Result: ✅ STILL COMPLIANT
```

---

## 📊 Supported Firms (Pre-Built Templates):

### **TopStep**
- Max Daily Loss: $150
- Max Trailing Drawdown: $3,000
- Profit Target (Phase 1): $3,000
- Min Trading Days: 1
- Profit Split: 80/20
- Consistency Rule: No single day > 50% of total profit

### **Apex Trader Funding**
- Max Daily Loss: None
- Max Trailing Drawdown: $3,000
- Profit Target: $3,000
- Min Trading Days: 1
- Profit Split: 90/10 (Best in industry!)

### **MyFundedFutures**
- Max Daily Loss: $1,000
- Max Trailing Drawdown: $2,500
- Profit Target: $3,000
- Min Trading Days: 1
- Profit Split: 80/20

### **FTMO (Forex/CFD)**
- Max Daily Loss: 5% ($2,500 on $50K)
- Max Overall Drawdown: 10% ($5,000 on $50K)
- Profit Target Phase 1: 10% ($5,000)
- Profit Target Phase 2: 5% ($2,500)
- Min Trading Days: 4
- Phases: 2

---

## 🎛️ Dashboard Panel:

The new **"🎓 PROP FIRM RULES (The Professor)"** panel shows:

1. **Firm Selector**: Choose your prop firm (TopStep, Apex, etc.)
2. **Compliance Status**: ✅ COMPLIANT or 🛑 BLOCKED
3. **Daily Loss Bar**: Visual bar showing how much of daily limit used
4. **Drawdown Bar**: Visual bar showing how close to max drawdown
5. **Profit Progress Bar**: How close to funding target
6. **Key Metrics**: Balance, Daily P&L, Win/Loss record
7. **Violations**: Lists any rule violations in red

---

## 🛡️ What The Professor Blocks:

### **Daily Loss Limit**
```
🛑 PROP FIRM BLOCKED: BTC-USD
   ❌ DAILY LOSS LIMIT: $155.00 / $150.00
```

### **Max Drawdown**
```
🛑 PROP FIRM BLOCKED: ETH-USD
   ❌ MAX DRAWDOWN: $3,050.00 / $3,000.00
```

### **Consistency Violations**
```
🛑 PROP FIRM BLOCKED: ES
   ❌ CONSISTENCY: Today's profit exceeds 50% of total
```

---

## 🎮 How To Use:

### **1. Set Your Firm in Config**
```python
# config.py
PROP_FIRM_ENABLED = True
PROP_FIRM_NAME = "TopStep"  # or "Apex", "MyFundedFutures", "FTMO"
PROP_ACCOUNT_SIZE = 50000.0  # Your account size
```

### **2. Run The Bot**
```powershell
python main.py
```

### **3. Watch The Dashboard**
The "Professor" panel will show:
- ✅ Green "COMPLIANT" when rules are met
- 🛑 Red "BLOCKED" when you've hit a limit
- Progress bars showing how close you are to limits

### **4. Trade With Confidence**
- Every trade is **pre-checked** against all rules
- If a trade would violate a rule, it's **blocked automatically**
- You'll see exactly WHY it was blocked

---

## 🧠 Future Enhancements (Phase 2 & 3):

### **Phase 2: Vision-Based Rule Detection**
- Take screenshot of prop firm dashboard
- VLM reads: Firm name, balance, rules
- Auto-configures Professor with exact rules

### **Phase 3: Level 2 Order Book Analysis**
- Volume Profile shows buyer/seller clusters
- Identifies liquidity zones without paying for Level 2
- Smart entries toward high-volume nodes

### **Phase 4: Full Platform Navigation**
- VLM reads trading platform UI
- Identifies Buy/Sell buttons, TP/SL fields
- Executes trades automatically with precision

---

## 💡 Why This Is Critical:

**Most traders fail prop firms because:**
1. They don't know the rules well enough
2. They emotion-trade and violate limits
3. They don't track daily P&L or drawdown in real-time

**The Professor solves all three:**
1. ✅ **Knows every rule** - TopStep, Apex, FTMO, etc.
2. ✅ **No emotions** - Blocks trades that violate rules
3. ✅ **Real-time tracking** - Updates compliance after every trade

**Result: You pass the challenge and keep the funded account.** 💰🎯

---

## 🚀 Run It Now:

```powershell
cd C:\Users\vijin\vcantrade.com-2
python main.py
```

**Watch the "🎓 PROP FIRM RULES" panel on the dashboard - it shows real-time compliance for your chosen firm!**
