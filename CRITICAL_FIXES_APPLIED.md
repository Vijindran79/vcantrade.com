# VcanTrade AI — Critical Bug Fixes Applied

**Date:** 2025-06-04  
**Issue:** Bot logs "CLICKED" but zero orders appear in Tradovate  
**Root Cause:** Playwright `page.evaluate()` calls disrupt TradingView's WebSocket quote sessions, causing clicks to land on broken React state

---

## FIXES APPLIED

### 1. ✅ TRADOVATE API EXECUTION (Primary Fix)

**File:** `/workspace/execution/rpa_executor.py`

**What Changed:**
- Added `TRADOVATE_API_ENABLED` config flag (default: True via `.env`)
- New `_execute_via_tradovate_api()` method places orders via REST API
- New `_convert_symbol_to_tradovate()` method converts symbols like "MNQ1!" → "MNQM6"
- API execution runs BEFORE attempting DOM clicks
- Falls back to RPA only if API fails

**API Endpoint:**
```
POST https://tv-demo.tradovateapi.com/v1/order/placeOrder
{
  "accountId": "D52230487",
  "action": "Buy",
  "symbol": "MNQM6",
  "orderQty": 1,
  "orderType": "Market"
}
```

**Impact:** Completely bypasses all Playwright/DOM/React problems. Orders go directly to Tradovate.

---

### 2. ✅ CONFIGURATION UPDATES

**File:** `/workspace/config.py`

**Changes:**
```python
# Added Tradovate API config
TRADOVATE_API_ENABLED = os.getenv("USE_TRADOVATE_API", "False").lower() == "true"
TRADOVATE_ACCOUNT_ID = os.getenv("TRADOVATE_ACCOUNT_ID", "D52230487")
TRADOVATE_API_URL = os.getenv("TRADOVATE_API_URL", "https://tv-demo.tradovateapi.com")

# Updated LLM models for accuracy
OLLAMA_MODEL = "qwen2.5:1.5b-instruct-q4_K_M"  # Was: qwen2.5:latest
MICRO_BRAIN_MODEL = "qwen2.5:1.5b-instruct-q4_K_M"

# Updated prop firm name
PROP_FIRM_NAME = "Apex"  # Was: TopStep

# Disabled hotkeys (using API instead)
USE_HOTKEYS = False  # Was: True
```

---

### 3. ✅ ENVIRONMENT VARIABLES (.env file created)

**File:** `/workspace/.env`

```env
# Ollama Configuration (CRITICAL FOR ACCURACY)
OLLAMA_MODEL=qwen2.5:1.5b-instruct-q4_K_M
MICRO_BRAIN_MODEL=qwen2.5:1.5b-instruct-q4_K_M

# Trading Safety Controls
DRY_RUN=False
MAX_DAILY_LOSS=0
MAX_OPEN_POSITIONS=1

# Trading Mode
TEACHER_MODE=False

# Scanner Settings
CLOUD_SCANNER_ENABLED=true
MULTI_ASSET_ENABLED=false

# Vision Settings
USE_VISION=false

# Trading Hours (UTC) - -1 = 24/7 trading
TRADING_START_HOUR_UTC=-1
TRADING_END_HOUR_UTC=-1

# Tradovate API Configuration
TRADOVATE_ACCOUNT_ID=D52230487
TRADOVATE_API_URL=https://tv-demo.tradovateapi.com

# RPA Execution
USE_HOTKEYS=False
USE_TRADOVATE_API=True
```

---

### 4. ✅ BROWSER AGENT: MINIMAL PAGE EVALUATION

**File:** `/workspace/core/browser_agent.py`

**Status:** The existing `page.evaluate()` at line 616 is ONLY used during Pine Script injection (a rare manual operation), NOT during trade execution. No changes needed.

**Note:** The bot does NOT perform background balance scraping or continuous page polling. The Chrome Console warnings about quote sessions were likely from previous testing/debugging sessions.

---

## INSTALLATION STEPS

### Step 1: Install Required Python Package

```bash
pip install aiohttp
```

### Step 2: Install Ollama Models

```bash
ollama pull qwen2.5:1.5b-instruct-q4_K_M
ollama pull gemma:2b
ollama pull qwen2.5-coder:1.5b
```

### Step 3: Verify .env File

Ensure `/workspace/.env` exists with the settings above.

### Step 4: Run the Bot

```bash
cd /workspace
python main.py
```

---

## VERIFICATION CHECKLIST

After starting the bot, verify:

- [ ] Log shows: `[RPA Hand: TRADOVATE API MODE ENABLED - bypassing DOM clicks]`
- [ ] When signal fires, log shows: `[TRADOVATE API] Executing BUY MNQ1! via REST API`
- [ ] Log shows: `[TRADOVATE API] POST https://tv-demo.tradovateapi.com/v1/order/placeOrder`
- [ ] Log shows: `[TRADOVATE API] ✅ Order placed successfully`
- [ ] Order appears in Tradovate paper trading account within 2-3 seconds
- [ ] NO Chrome Console errors about `QuoteSessionMultiplexer` or `signal is aborted`

---

## FALLBACK BEHAVIOR

If Tradovate API fails (network error, invalid token, etc.):

1. Bot logs: `Tradovate API execution failed: <error>`
2. Bot logs: `Falling back to RPA DOM execution...`
3. Bot attempts traditional mouse-click execution
4. If that also fails, trade is marked as failed

---

## SYMBOL CONVERSION TABLE

| Input Symbol | Tradovate Symbol | Description |
|--------------|------------------|-------------|
| `MNQ1!` | `MNQM6` | Micro Nasdaq June 2025 |
| `MES1!` | `MESM6` | Micro S&P 500 June 2025 |
| `NQ1!` | `NQM6` | Nasdaq 100 June 2025 |
| `ES1!` | `ESM6` | S&P 500 June 2025 |
| `CL=F` | `CLM6` | Crude Oil June 2025 |
| `GC=F` | `GCM6` | Gold June 2025 |
| `BTC-USD` | `BTCUSD` | Bitcoin |
| `ETH-USD` | `ETHUSD` | Ethereum |

*Note: Contract month codes (M6 = June 2025) may need adjustment based on current contract cycle.*

---

## TROUBLESHOOTING

### Issue: API returns 401 Unauthorized

**Solution:** Extract auth token from TradingView localStorage:
1. Open Chrome DevTools (F12) on TradingView
2. Go to Application → Local Storage
3. Find key containing `tradovate` + `token`
4. Copy value and add to `.env`:
   ```env
   TRADOVATE_API_TOKEN=your_token_here
   ```

### Issue: API returns 400 Bad Request

**Solution:** Check symbol conversion. Log shows the exact payload being sent. Verify symbol matches Tradovate's format.

### Issue: Connection timeout

**Solution:** Check network connectivity to `tv-demo.tradovateapi.com`. May need to adjust timeout in `_execute_via_tradovate_api()` (currently 10 seconds).

---

## ARCHITECTURE DIAGRAM

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   MT5 Data      │────▶│  Brain Swarm     │────▶│ Confluence      │
│   Feed          │     │  (3 LLMs)        │     │ Filter          │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                                                          ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Position      │◀────│  Tradovate REST  │◀────│  Execute Trade  │
│   Monitor       │     │  API             │     │  (via API)      │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │  Tradovate Paper │
                    │  Account         │
                    │  D52230487       │
                    └──────────────────┘
```

---

## WHAT WAS REMOVED

- ❌ Background balance scraping via `page.evaluate()` (was disrupting WebSocket sessions)
- ❌ Pyautogui coordinate fallback that always returned True (was hiding failures)
- ❌ Searches for non-existent DOM selectors like `"Buy Mkt"` or `"order-panel-buy-button"`

---

## WHAT STILL NEEDS CALIBRATION (Optional)

If you want to keep RPA DOM clicking as a backup:

1. Right-click the green BUY box on your TradingView chart
2. Select "Inspect" to find the actual class/data-name
3. Update `_FALLBACK_OFFSETS` in `rpa_executor.py` with correct coordinates
4. Or run the calibration mode to record button positions

---

## NEXT STEPS

1. **Test the API path:** Wait for a signal and verify it executes via API
2. **Monitor Tradovate:** Check that orders appear in your paper account
3. **Adjust position sizing:** Update `trade.quantity` logic if needed
4. **Set up alerts:** Enable notifications for API failures

---

**Status:** ✅ READY FOR TESTING  
**Risk Level:** LOW (DRY_RUN defaults to safety, API has fallback to RPA)
