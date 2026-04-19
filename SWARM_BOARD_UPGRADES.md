# 🚀 Swarm Board Upgrades - Complete Implementation

## Overview

Your **Swarm Board** multi-agent system has been significantly upgraded with three major features:

1. **😈 Devil's Advocate Agent** - Challenges every trade signal
2. **👁️ Vision Confirmation Dialog** - Visual chart analysis with Browser Agent
3. **💰 Financial Safety Manager** - Micro-Lot auto-switch + News Filter

---

## 1. 😈 Devil's Advocate Agent

### What It Does

The Devil's Advocate is a **contrarian agent** that specifically tries to find reasons NOT to take a trade. It acts as the skeptical voice in your Swarm debate, protecting capital by being hyper-conservative.

### How It Works

1. **After** the primary agents (Technical Sniper, Macro Analyst, Risk Manager) approve a trade...
2. The Devil's Advocate **challenges** the trade with a dedicated prompt
3. It identifies 2-3 specific reasons to avoid the trade
4. Finds hidden risks other agents missed
5. Applies a **confidence penalty** (-0.10 to -0.15) if risks are identified

### Implementation

**File**: `core/devils_advocate.py`

**Key Features**:
- **Rating System**: `STRONG_AVOID`, `CAUTIOUS`, or `NEUTRAL`
- **Rejection Reasons**: Lists 2-3 specific reasons to avoid
- **Hidden Risks**: Identifies what other agents missed
- **Better Timing**: Suggests when would be safer to enter
- **Confidence Penalty**: Automatically reduces signal confidence

**Example Output**:
```json
{
  "rating": "CAUTIOUS",
  "rejection_reasons": [
    "RSI showing bearish divergence on 15m timeframe",
    "Volume 40% below average - weak conviction"
  ],
  "hidden_risks": "Fed announcement in 2 hours could cause volatility spike",
  "better_entry_timing": "Wait for RSI to confirm oversold bounce below 30",
  "override_conditions": "Strong volume breakout above resistance would change my mind",
  "confidence_penalty": -0.12
}
```

### UI Integration

**File**: `ui/signal_dialog.py`

When Devil's Advocate rates a trade as `STRONG_AVOID` or `CAUTIOUS`, the approval dialog now shows:

- 🔴 **Red warning box** with Devil's Advocate header
- ⚠️ **List of rejection reasons** (bullet points)
- 🔍 **Hidden risks** highlighted in orange
- **Reduced confidence score** in the main signal

### Swarm Consensus Integration

**File**: `core/swarm_consensus.py`

```python
# Devil's Advocate Challenge - Find reasons NOT to take this trade
devils_challenge = self.devils_advocate.challenge_trade(
    market_data=market_data,
    suggested_action=output.action.value,
    entry_price=output.entry_price,
    stop_loss=output.stop_loss,
    take_profit=output.take_profit,
    confidence=output.confidence.value,
)

# Apply confidence penalty if risks identified
if devils_challenge.get("rating") in ["STRONG_AVOID", "CAUTIOUS"]:
    penalty = devils_challenge.get("confidence_penalty", -0.10)
```

### Scanner Integration

**File**: `core/scanner.py`

The `_calculate_confidence` method now applies the Devil's Advocate penalty:

```python
# Devil's Advocate Penalty - Reduce confidence if risks identified
devils_penalty = 0.0
if transcript.devils_advocate.get("rating") in ["STRONG_AVOID", "CAUTIOUS"]:
    devils_penalty = transcript.devils_advocate.get("confidence_penalty", -0.10)

final_confidence = max(0.0, min(1.0, 
    base_confidence + alignment_bonus + signal_weight + devils_penalty
))
```

**Result**: Signals with high Devil's Advocate penalties may drop below the dispatch threshold, preventing risky trades from reaching you.

---

## 2. 👁️ Vision Confirmation Dialog

### What It Does

Uses your **Browser Agent** to capture a screenshot of the 15-minute chart, then sends it to a **multimodal vision model** (Qwen-VL or LLaVA) to visually confirm RSI and MACD patterns before allowing trade approval.

### How It Works

1. **Browser Agent** navigates to TradingView and captures chart screenshot
2. Screenshot is sent to **vision language model** via Ollama
3. Vision model analyzes:
   - Current RSI value and signal (oversold/overbought/neutral)
   - MACD confirmation (bullish/bearish divergence)
   - Trend direction (uptrend/downtrend/sideways)
   - Support/resistance levels
   - Overall confidence score
4. Dialog shows **visual results** with screenshot and analysis
5. User can **confirm**, **skip vision**, or **reject** the trade

### Implementation

**File**: `ui/vision_dialog.py`

**Key Features**:
- 📸 **Screenshot Display**: Shows captured chart in dialog
- 🔍 **Vision Analysis Panel**: Real-time RSI/MACD breakdown
- 🎯 **Verdict System**: `CONFIRMED`, `PARTIAL`, `WEAK`, `FAILED`
- ⏭️ **Skip Option**: Can bypass vision and approve anyway
- 🎨 **Color-Coded Results**: Green (confirmed), Orange (partial), Red (weak)

**Vision Prompt**:
```
Analyze this 15-minute chart and provide STRICT JSON:
{
  "rsi_value": 45.2,
  "rsi_confirmed": true,
  "rsi_signal": "oversold or overbought or neutral",
  "macd_confirmed": true,
  "macd_signal": "bullish or bearish or neutral",
  "trend": "uptrend or downtrend or sideways",
  "support_level": 180.50,
  "resistance_level": 185.20,
  "confidence": 0.85,
  "notes": "Brief analysis in 1-2 sentences"
}
```

### Integration with Main App

To use Vision Confirmation, replace the standard `SignalApprovalDialog` with `VisionConfirmationDialog` in `main.py`:

```python
from ui.vision_dialog import VisionConfirmationDialog

# In _on_cloud_signal method:
if self.current_mode == "TEACHER":
    # Use vision confirmation instead of standard dialog
    dialog = VisionConfirmationDialog(
        signal_data, 
        browser_agent=self.browser_agent, 
        parent=self.cmd
    )
    dialog.confirmed.connect(self._on_signal_approved)
    dialog.rejected.connect(self._on_signal_rejected)
    dialog.exec()
```

### Requirements

- **Vision Model**: Install `qwen2.5-vl` or `llava` in Ollama
  ```bash
  ollama pull llava
  ```
- **Config Update**: Set `USE_VISION = True` in `config.py`
- **Browser Agent**: Must be running for screenshot capture

---

## 3. 💰 Financial Safety Manager

### What It Does

The Financial Safety Manager provides **two critical safety mechanisms**:

1. **Micro-Lot Auto-Switch**: When daily loss hits 70%, automatically reduces position sizes to 10% (or 5% at 90%)
2. **News Filter**: Pauses trading 15 minutes before high-impact news, resumes 15 minutes after

### Part A: Micro-Lot Auto-Switch

#### How It Works

The system monitors your **daily loss ratio** and automatically switches position size modes:

| Daily Loss Ratio | Mode | Position Size | Purpose |
|-----------------|------|---------------|---------|
| 0-70% | `NORMAL` | 100% | Standard trading |
| 70-90% | `MICRO` | 10% | Protect account, collect data |
| 90-100% | `MINIMAL` | 5% | Emergency protection |

#### Implementation

**File**: `core/financial_safety.py`

**Example Flow**:
```
Daily P&L: $0 / $100 → Mode: NORMAL (100% size)
Daily P&L: -$50 / $100 → Mode: NORMAL (100% size)
Daily P&L: -$70 / $100 → Mode: MICRO (10% size) ⚠️
  → $100 trade becomes $10 trade
Daily P&L: -$90 / $100 → Mode: MINIMAL (5% size) 🔴
  → $100 trade becomes $5 trade
Daily P&L: -$60 / $100 → Mode: NORMAL (100% size) ✅
  → Recovered, back to normal
```

**Key Methods**:
```python
# Check if we should switch modes
mode = safety_manager.check_position_size_mode(daily_pnl=-70, max_daily_loss=100)
# Returns: PositionSizeMode.MICRO

# Get position size multiplier
multiplier = safety_manager.get_position_size_multiplier()
# Returns: 0.10 (10%)

# Calculate safe position size
safe_amount, mode = safety_manager.calculate_safe_position_size(
    base_amount=1000.0,
    daily_pnl=-70.0,
    max_daily_loss=100.0
)
# Returns: (100.0, PositionSizeMode.MICRO)
```

#### Integration with Trade Execution

**File**: `main.py` - `_execute_cloud_signal` method

```python
# FINANCIAL SAFETY CHECK
if self.financial_safety.trading_paused:
    self.cmd.log("🛑 NEWS FILTER BLOCKED")
    return

# Calculate safe position size
safe_amount, size_mode = self.financial_safety.calculate_safe_position_size(
    base_amount,
    self.daily_pnl,
    self.max_daily_loss
)

if size_mode.value != "normal":
    self.cmd.log(f"📏 SAFETY MODE: Position reduced to {size_mode.value}")
```

### Part B: News Filter

#### How It Works

1. **Scrapes** economic calendar from Forex Factory and Investing.com every 5 minutes
2. **Filters** for high-impact (red/orange) news events in next 2 hours
3. **Pauses** trading 15 minutes before event
4. **Resumes** trading 15 minutes after event
5. **Logs** all pauses/resumes with reasons

#### Implementation

**File**: `core/financial_safety.py`

**Web Scraping**:
```python
async def _scrape_forex_factory(self) -> List[Dict]:
    """Scrape Forex Factory economic calendar."""
    url = "https://www.forexfactory.com/calendar"
    
    # Parse calendar rows
    # Extract: time, currency, event name, impact level
    # Return list of upcoming events
```

**News Event Structure**:
```json
{
  "time": "8:30 AM",
  "datetime": "2026-04-11T08:30:00",
  "currency": "USD",
  "event": "Non-Farm Payrolls",
  "impact": "HIGH",
  "source": "ForexFactory"
}
```

**Pause Logic**:
```python
def should_pause_trading(self) -> Tuple[bool, str]:
    """Check if we should pause trading due to upcoming news."""
    for event in self.upcoming_news:
        time_until = (event_dt - now).total_seconds()
        
        # Pause 15 minutes before high-impact news
        if 0 < time_until <= 900:  # 15 minutes
            return True, f"High-impact news in {int(time_until / 60)}min: {event['event']}"
        
        # Resume 15 minutes after news
        time_after = (now - event_dt).total_seconds()
        if 0 < time_after <= 900:
            return True, f"Recent news ({int(time_after / 60)}min ago): {event['event']}"
    
    return False, ""
```

#### Safety Timer

**File**: `main.py`

A timer checks news every 60 seconds:
```python
# News filter & safety timer
self.safety_timer = QTimer()
self.safety_timer.timeout.connect(self._update_safety_controls)
self.safety_timer.start(60000)  # Check every 60 seconds

def _update_safety_controls(self):
    """Update financial safety controls and check news filter."""
    # Update news filter
    loop.run_until_complete(self.financial_safety.update_news_filter())
    
    # Check if trading should be paused
    if self.financial_safety.trading_paused:
        self.cmd.log(f"🛑 Trading paused: {self.financial_safety.pause_reason}")
```

#### Dashboard Integration

**File**: `ui/dashboard.py`

The `update_safety_status` method logs safety events:
```python
def update_safety_status(self, safety_data: dict):
    if paused:
        self.log(f"🛑 Trading paused: {pause_reason}")
    elif mode != "normal":
        self.log(f"📏 Position mode: {mode} ({multiplier:.0%} size)")
```

### Requirements

**File**: `requirements.txt`

Added:
```
beautifulsoup4>=4.12.0  # News filter web scraping
```

Install:
```bash
pip install beautifulsoup4
```

---

## 📊 Architecture Summary

### New Files Created

| File | Purpose | Lines |
|------|---------|-------|
| `core/devils_advocate.py` | Devil's Advocate agent | ~180 |
| `core/financial_safety.py` | Micro-Lot + News Filter | ~380 |
| `ui/vision_dialog.py` | Vision Confirmation UI | ~400 |

### Modified Files

| File | Changes | Impact |
|------|---------|--------|
| `core/models.py` | Added `devils_advocate` field to `DebateTranscript` | Enables Devil's Advocate in transcript |
| `core/swarm_consensus.py` | Integrated Devil's Advocate challenge | All trades now challenged |
| `core/scanner.py` | Applied Devil's Advocate penalty | Confidence scores reduced for risky trades |
| `ui/signal_dialog.py` | Added Devil's Advocate warning box | Visual warnings in approval dialog |
| `main.py` | Added Financial Safety Manager | Micro-Lot + News Filter active |
| `ui/dashboard.py` | Added safety status logging | Dashboard shows safety events |
| `requirements.txt` | Added beautifulsoup4 | News filter web scraping |

---

## 🎯 How to Use

### Enable Devil's Advocate

**Already Active!** No configuration needed. It runs automatically with every Swarm debate.

**Monitor in Logs**:
```
😈 Devil's Advocate: CAUTIOUS - 2 reasons found
😈 Devil's Advocate PENALTY: -0.12 | Reasons: ["RSI divergence", "Low volume"]
```

### Enable Vision Confirmation

1. **Install Vision Model**:
   ```bash
   ollama pull llava
   ```

2. **Update Config** (`config.py`):
   ```python
   USE_VISION = True
   VLM_MODEL = "llava:7b"
   VISION_TIMEOUT = 120
   ```

3. **Update Main App** (`main.py`):
   ```python
   # In _on_cloud_signal method, replace:
   dialog = SignalApprovalDialog(signal_data, self.cmd)
   
   # With:
   from ui.vision_dialog import VisionConfirmationDialog
   dialog = VisionConfirmationDialog(signal_data, self.browser_agent, self.cmd)
   ```

4. **Restart App** - Vision confirmation will now appear before trade approval

### Enable News Filter

**Already Active!** Runs automatically every 60 seconds.

**Monitor in Logs**:
```
📰 High-impact news detected: 3 events
   • 8:30 AM - USD Non-Farm Payrolls (Impact: HIGH)
   • 10:00 AM - EUR ECB Rate Decision (Impact: HIGH)
🛑 Trading paused: High-impact news in 12min: Non-Farm Payrolls
▶️ Trading resumed: Non-Farm Payrolls (16min ago)
```

### Monitor Micro-Lot Mode

**Automatic Activation**:
- 70% daily loss → MICRO mode (10% size)
- 90% daily loss → MINIMAL mode (5% size)

**Monitor in Logs**:
```
🟡 MICRO-LOT MODE ACTIVATED | Daily loss: $70.00 / $100.00 (70%) | Position size reduced to 10% of normal
📏 Position size adjusted: $1000.00 → $100.00 (10%) | Mode: micro
🟢 RETURNED TO NORMAL MODE | Daily loss ratio decreased to 45%
```

---

## 🔒 Safety Features Summary

### Multi-Layer Protection

1. **😈 Devil's Advocate** - Prevents bad trades before they reach you
2. **💰 Micro-Lot Mode** - Reduces size when approaching loss limits
3. **📰 News Filter** - Pauses during high-impact events
4. **🛑 Kill Switch** - Emergency stop (already existed)
5. **👁️ Vision Confirmation** - Visual chart verification (optional)

### Confidence Score Pipeline

```
Original Signal (0.85)
  ↓
Devil's Advocate Penalty (-0.12)
  ↓
Final Confidence (0.73)
  ↓
Below Threshold? → Trade blocked
Above Threshold? → Trade reaches you
```

---

## 🚀 Next Steps (Optional Enhancements)

1. **Vision Model Integration**: Install `llava` or `qwen2.5-vl` for chart analysis
2. **News Filter Sources**: Add more economic calendar sources (e.g., Bloomberg, Reuters)
3. **Dashboard Widgets**: Add visual indicators for:
   - Current position mode (NORMAL/MICRO/MINIMAL)
   - News filter status (active/paused)
   - Devil's Advocate success rate
4. **Backtesting**: Test Devil's Advocate on historical data to measure effectiveness
5. **Machine Learning**: Train Devil's Advocate on past trade autopsies to improve risk detection

---

## 📝 Configuration Reference

### Devil's Advocate

```python
# core/devils_advocate.py (defaults)
temperature: 0.3  # Slightly creative for skepticism
confidence_penalty: -0.10 to -0.15  # Applied to risky trades
ratings: STRONG_AVOID, CAUTIOUS, NEUTRAL
```

### Vision Confirmation

```python
# config.py
USE_VISION = True  # Enable vision analysis
VLM_MODEL = "llava:7b"  # Vision language model
VISION_TIMEOUT = 120  # 2 minutes max
```

### Financial Safety Manager

```python
# core/financial_safety.py (defaults)
micro_lot_threshold = 0.70  # 70% of daily loss
minimal_lot_threshold = 0.90  # 90% of daily loss
news_pause_before_minutes = 15  # Pause before news
news_pause_after_minutes = 15  # Resume after news
news_check_interval = 300  # Check every 5 minutes
```

---

## 🎉 Summary

Your Swarm Board is now a **professional-grade trading system** with:

✅ **4-Agent Debate** (Technical Sniper, Macro Analyst, Risk Manager, **Devil's Advocate**)
✅ **Visual Chart Confirmation** (Browser Agent + Vision Model)
✅ **Auto-Protecting Position Sizes** (NORMAL → MICRO → MINIMAL)
✅ **News-Aware Trading** (Pauses before high-impact events)
✅ **Multi-Layer Safety Controls** (Confidence penalties, size reductions, news pauses)

**Built for safety, designed for performance.** 🚀
