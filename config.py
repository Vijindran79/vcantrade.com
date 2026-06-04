"""
VcaniTrade AI - Configuration
Safety-first trading configuration with strict defaults
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

# ===== TRADOVATE API CONFIGURATION =====
TRADOVATE_API_ENABLED = os.getenv("USE_TRADOVATE_API", "False").lower() == "true"
TRADOVATE_ACCOUNT_ID = os.getenv("TRADOVATE_ACCOUNT_ID", "D52230487")
TRADOVATE_API_URL = os.getenv("TRADOVATE_API_URL", "https://tv-demo.tradovateapi.com")
TRADOVATE_API_TOKEN = None  # Will be extracted from TradingView localStorage at runtime

# ===== PROP FIRM RULES (The "Professor" - Knows Every Firm's Rules) =====
PROP_FIRM_ENABLED = (
    os.getenv("PROP_FIRM_ENABLED", "True").lower() == "true"
)  # Enable prop firm rule enforcement
PROP_FIRM_NAME = os.getenv("PROP_FIRM_NAME", "Apex")  # TopStep, Apex, MyFunded, FTMO
PROP_ACCOUNT_SIZE = float(os.getenv("PROP_ACCOUNT_SIZE", "50000.0"))  # Starting balance
PROP_PHASE = int(os.getenv("PROP_PHASE", "1"))  # Phase 1 or 2
PROP_IS_FUNDED = (
    os.getenv("PROP_IS_FUNDED", "False").lower() == "true"
)  # Are we already funded?

# ===== LION MODE: ANTI-OVERTRADING CONTROLS =====
MAX_DAILY_TRADES = int(os.getenv("MAX_DAILY_TRADES", "30"))  # Hard limit: 30 trades/day max

# ===== SAFETY CONTROLS (ALWAYS ON BY DEFAULT) =====
DRY_RUN = (
    os.getenv("DRY_RUN", "False").lower() == "true"
)  # Paper trading only - NEVER changes to False without explicit user action
MAX_DAILY_LOSS = float(
    os.getenv("MAX_DAILY_LOSS", "100.00")
)  # Maximum loss per day in account currency
MAX_OPEN_POSITIONS = int(
    os.getenv("MAX_OPEN_POSITIONS", "1")  # Reduced from 3 to 1 for focused trading
)  # Maximum concurrent trades - REDUCED to prevent overload
COOLDOWN_AFTER_STOP = int(
    os.getenv("COOLDOWN_AFTER_STOP", "300")
)  # Seconds to wait after hitting stop loss (5 min)
KILL_SWITCH = False  # Emergency stop - halts all trading immediately

# ===== TRADING MODE =====
# TEACHER_MODE: Show signals overlay but don't execute (manual trading)
# AUTO_MODE: AI executes trades automatically (requires DRY_RUN=False)
TEACHER_MODE = (
    os.getenv("TEACHER_MODE", "False").lower() == "true"
)  # Changed to False for Autonomous action

# ===== LLM CONFIGURATION (Local Ollama + Qwen 2.5) =====
# Running 100% locally - NO cloud tokens needed!
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b-instruct-q4_K_M")  # Fixed: Use accurate model for JSON output
MICRO_BRAIN_MODEL = os.getenv("MICRO_BRAIN_MODEL", "qwen2.5:1.5b-instruct-q4_K_M")  # For parallel swarm
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "ollama")  # Not needed for local, kept for compatibility
VAST_API_TOKEN = None  # No longer using Vast.ai - running locally!

# Required models for parallel swarm (install via: ollama pull <model>)
# - qwen2.5:1.5b-instruct-q4_K_M (main brain)
# - gemma:2b (second opinion)
# - qwen2.5-coder:1.5b (code analysis)

# ===== GEMINI LIVE BRAIN =====
GEMINI_ENABLED = os.getenv("GEMINI_ENABLED", "True").lower() == "true"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", ""))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT", "20"))

# ===== EXTERNAL BRAIN PROVIDERS =====
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_API_KEYS = [
    key.strip()
    for key in os.getenv("OPENROUTER_API_KEYS", "").split(",")
    if key.strip()
]
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat")
OPENROUTER_SITE_URL = os.getenv("OPENROUTER_SITE_URL", "")
OPENROUTER_APP_NAME = os.getenv("OPENROUTER_APP_NAME", "VcanTrade AI")
BRAIN_PROVIDER = os.getenv(
    "BRAIN_PROVIDER",
    "openrouter" if (OPENROUTER_API_KEYS or OPENROUTER_API_KEY) else "gemini",
).strip().lower()
GROQ_API_KEYS = [
    key.strip()
    for key in os.getenv("GROQ_API_KEYS", "").split(",")
    if key.strip()
]
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
BRAINSTORM_API_KEY = os.getenv("BRAINSTORM_API_KEY", "")
BRAINSTORM_BASE_URL = os.getenv("BRAINSTORM_BASE_URL", "/api/v1/google/search")

# Local execution settings
LLM_TIMEOUT = 90  # Heavy local Qwen runs need more time to finish reliably
JSON_OUTPUT = True

# ===== VISION / VLM CONFIGURATION =====
# Optional: Use local vision model for chart analysis
VLM_MODEL = os.getenv("VLM_MODEL", "llava:7b")  # LLaVA for vision if available
VISION_TIMEOUT = 120  # Max seconds before graceful degradation to text-only
USE_VISION = (
    os.getenv("USE_VISION", "False").lower() == "true"  # Default OFF for faster local execution
)  # Enable chart screenshot analysis
CHART_REGION_X = int(os.getenv("CHART_REGION_X", "100"))
CHART_REGION_Y = int(os.getenv("CHART_REGION_Y", "100"))
CHART_REGION_W = int(os.getenv("CHART_REGION_W", "1280"))
CHART_REGION_H = int(os.getenv("CHART_REGION_H", "720"))
SAVE_DEBUG_SCREENSHOTS = os.getenv("SAVE_DEBUG_SCREENSHOTS", "False").lower() == "true"

# ===== MARKET DATA =====
# Cloud Scanner Settings (Vast.ai Server)
SCAN_INTERVAL = 10  # Seconds between market scans
WATCHLIST_INTERVAL = 60  # Seconds between watchlist scans (slower)

# ===== SYMBOL PRIORITY SYSTEM - REDUCE OVERLOAD =====
# Limit active symbols to prevent system overload and improve execution speed
MAX_ACTIVE_SYMBOLS = int(os.getenv("MAX_ACTIVE_SYMBOLS", "4"))  # Maximum symbols to monitor per cycle

# Priority tiers: lower number = higher priority (analyzed first)
PRIORITY_SYMBOLS = {
    # Tier 1: Primary focus (analyze every cycle)
    "GC=F": 1,      # Gold - high liquidity
    "CL=F": 1,      # Crude Oil - high volatility
    
    # Tier 2: Secondary (analyze every 2nd cycle if system loaded)
    "CME_MINI:MNQ1!": 2,   # Nasdaq
    "CME_MINI:MES1!": 2,   # S&P 500
    
    # Tier 3: Tertiary (only when system has capacity)
    "YM=F": 3,      # Dow Jones
}

# Optional sniper override. Leave empty to let the live dashboard watchlist drive the session.
# Format from env: "BTC-USD,ES=F,NQ=F".
_active_scan_raw = os.getenv("ACTIVE_SCAN_LIST", "").strip()
ACTIVE_SCAN_LIST = [s.strip() for s in _active_scan_raw.split(",") if s.strip()]
SNIPER_SCAN_INTERVAL = float(os.getenv("SNIPER_SCAN_INTERVAL", "1.5"))

# RPA window control for TradingView auto-focus.
TRADINGVIEW_WINDOW_X = int(os.getenv("TRADINGVIEW_WINDOW_X", "0"))
TRADINGVIEW_WINDOW_Y = int(os.getenv("TRADINGVIEW_WINDOW_Y", "0"))

# Starter watchlist shown on first launch only. The live dashboard watchlist becomes the session authority.
CLOUD_TICKERS = [
    "BTC-USD",  # Bitcoin spot
    "ES=F",  # S&P 500 futures
    "NQ=F",  # NASDAQ futures
]

# Local Watchlist (for local scanning when cloud unavailable)
LOCAL_ASSETS = ["BTC-USD", "ES=F", "NQ=F"]

# Technical Signal Thresholds
VOLUME_SPIKE_MULTIPLIER = 3.0  # Trigger if volume > 3x average
RSI_OVERBOUGHT = 70  # RSI > 70 = potential sell
RSI_OVERSOLD = 30  # RSI < 30 = potential buy
SMA_FAST = 20  # Fast SMA period
SMA_SLOW = 50  # Slow SMA period
SWARM_CONFIDENCE_THRESHOLD = 0.50  # Minimum confidence to trigger trade (0.0-1.0) for testing the live execution path
MIN_CONFIDENCE_THRESHOLD = 50.0  # Minimum execution confidence score (0-100) before the RPA hand is allowed to click

# ===== ENTRY CONFIDENCE THRESHOLDS - PREDATOR CLASS =====
# Lower threshold for faster entries, higher for position sizing
ENTRY_CONFIDENCE_THRESHOLD = int(os.getenv("ENTRY_CONFIDENCE_THRESHOLD", "70"))  # 70% for fast entries
HIGH_CONFIDENCE_THRESHOLD = int(os.getenv("HIGH_CONFIDENCE_THRESHOLD", "85"))   # 85% for pyramiding/larger size

# ===== MARKET REGIME FILTERS - RUSH HOUR PROTECTION =====
# Prevent trades in choppy conditions, adapt to volatility
ALLOW_TRADES_IN_CHOP = False  # Block trades in choppy markets (CHOP regime)
CHOP_MAX_POSITION_SIZE = 0.5  # If allowed, max 50% size in chop
TREND_FULL_SIZE = True        # Full size in trending markets
HOT_VOLATILITY_SIZE_ADJUST = 0.7  # Reduce to 70% size in HOT volatility

# ===== CLOUD SCANNER =====
CLOUD_SCANNER_ENABLED = True  # Enable local market scanning
CLOUD_SCANNER_URL = os.getenv("CLOUD_SCANNER_URL", "http://localhost:17199")  # Fixed: Match listener port
LOCAL_LISTENER_PORT = int(
    os.getenv("LOCAL_LISTENER_PORT", "17199")
)  # Local HTTP listener

# ===== SIGNAL DISPATCH =====
SIGNAL_DISPATCH_METHOD = os.getenv(
    "SIGNAL_DISPATCH_METHOD", "http"
)  # http or websocket
LOCAL_EXECUTION_TIMEOUT = 30  # Max seconds to wait for local execution

# ===== NEWS SCRAPER SAFETY =====
NEWS_REQUEST_TIMEOUT = float(os.getenv("NEWS_REQUEST_TIMEOUT", "5.0"))
NEWS_CONNECT_TIMEOUT = float(os.getenv("NEWS_CONNECT_TIMEOUT", "3.0"))
NEWS_DNS_FALLBACK = tuple(
    server.strip()
    for server in os.getenv("NEWS_DNS_FALLBACK", "8.8.8.8,1.1.1.1").split(",")
    if server.strip()
)

# ===== RPA EXECUTION =====
USE_HOTKEYS = False  # Use Tradovate API instead of hotkeys
HOTKEY_BUY = "<ctrl>+b"  # Buy order hotkey (fallback)
HOTKEY_SELL = "<ctrl>+s"  # Sell order hotkey (fallback)
HOTKEY_CLOSE = "<ctrl>+x"  # Close position hotkey (fallback)
POSITION_OPEN_IMAGE = os.getenv("POSITION_OPEN_IMAGE", "assets/tv_position_open_label.png")

# ===== TRADOVATE DIRECT API EXECUTION =====
# When enabled, orders are placed via REST API instead of DOM clicks
# This bypasses all Playwright/React/quote-session problems
TRADOVATE_API_ENABLED = os.getenv("USE_TRADOVATE_API", "False").lower() == "true"

# ===== SLIPPAGE GUARD (Execution Safety) =====
# Increased for volatile futures like CL=F, GC=F - was 2.50%
MAX_SLIPPAGE_PERCENT = float(
    os.getenv("MAX_SLIPPAGE_PERCENT", "5.0")  # Increased from 2.50% to 5.0% for volatile markets
)  # Max 5.0% price movement allowed (futures-friendly)
MAX_SPREAD_PERCENT = float(
    os.getenv("MAX_SPREAD_PERCENT", "0.30")
)  # Max 0.3% bid-ask spread allowed (thin market tolerant)

# Volatile assets get even higher slippage tolerance
VOLATILE_ASSETS = ["CL=F", "NG=F", "BTCUSD", "ETHUSD", "XAUUSD"]
MAX_SLIPPAGE_VOLATILE = float(os.getenv("MAX_SLIPPAGE_VOLATILE", "8.0"))  # 8% for high volatility

# ===== AUTONOMOUS RISK MANAGEMENT =====
# Fixed TP/SL percent targets are intentionally disabled. Entries now rely on
# structure-based stops, a break-even shield, and a 3-bar trailing stop.
AUTONOMOUS_BREAK_EVEN_BUFFER_PCT = float(
    os.getenv("AUTONOMOUS_BREAK_EVEN_BUFFER_PCT", "0.5")
)
AUTONOMOUS_TRAILING_LOOKBACK_BARS = int(
    os.getenv("AUTONOMOUS_TRAILING_LOOKBACK_BARS", "3")
)
AUTONOMOUS_TRAILING_UPDATE_SECONDS = int(
    os.getenv("AUTONOMOUS_TRAILING_UPDATE_SECONDS", "60")
)

# ===== UI CONFIGURATION =====
OVERLAY_ALPHA = float(
    os.getenv("OVERLAY_ALPHA", "0.15")
)  # Transparency level for HUD overlay (0.0-1.0)
OVERLAY_UPDATE_MS = int(
    os.getenv("OVERLAY_UPDATE_MS", "2000")
)  # Overlay refresh rate in milliseconds
SHOW_REASONING = (
    os.getenv("SHOW_REASONING", "True").lower() == "true"
)  # Display AI reasoning in overlay

# ===== LOGGING =====
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")  # DEBUG, INFO, WARNING, ERROR
LOG_FILE = "vcani_trade.log"
