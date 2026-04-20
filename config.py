"""
VcaniTrade AI - Configuration
Safety-first trading configuration with strict defaults
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

# ===== PROP FIRM RULES (The "Professor" - Knows Every Firm's Rules) =====
PROP_FIRM_ENABLED = (
    os.getenv("PROP_FIRM_ENABLED", "True").lower() == "true"
)  # Enable prop firm rule enforcement
PROP_FIRM_NAME = os.getenv("PROP_FIRM_NAME", "TopStep")  # TopStep, Apex, MyFunded, FTMO
PROP_ACCOUNT_SIZE = float(os.getenv("PROP_ACCOUNT_SIZE", "50000.0"))  # Starting balance
PROP_PHASE = int(os.getenv("PROP_PHASE", "1"))  # Phase 1 or 2
PROP_IS_FUNDED = (
    os.getenv("PROP_IS_FUNDED", "False").lower() == "true"
)  # Are we already funded?

# ===== SAFETY CONTROLS (ALWAYS ON BY DEFAULT) =====
DRY_RUN = (
    os.getenv("DRY_RUN", "False").lower() == "true"
)  # Paper trading only - NEVER changes to False without explicit user action
MAX_DAILY_LOSS = float(
    os.getenv("MAX_DAILY_LOSS", "100.00")
)  # Maximum loss per day in account currency
MAX_OPEN_POSITIONS = int(
    os.getenv("MAX_OPEN_POSITIONS", "3")
)  # Maximum concurrent trades
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
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:latest")  # Fixed: Use actual model name from ollama list
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "ollama")  # Not needed for local, kept for compatibility
VAST_API_TOKEN = None  # No longer using Vast.ai - running locally!

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
USE_HOTKEYS = True  # Prefer keyboard hotkeys over mouse clicks
HOTKEY_BUY = "<ctrl>+b"  # Buy order hotkey
HOTKEY_SELL = "<ctrl>+s"  # Sell order hotkey
HOTKEY_CLOSE = "<ctrl>+x"  # Close position hotkey
POSITION_OPEN_IMAGE = os.getenv("POSITION_OPEN_IMAGE", "assets/tv_position_open_label.png")

# ===== SLIPPAGE GUARD (Execution Safety) =====
# Relaxed for crypto volatility - was 0.50/0.10
MAX_SLIPPAGE_PERCENT = float(
    os.getenv("MAX_SLIPPAGE_PERCENT", "2.50")
)  # Max 2.5% price movement allowed (crypto-friendly)
MAX_SPREAD_PERCENT = float(
    os.getenv("MAX_SPREAD_PERCENT", "0.30")
)  # Max 0.3% bid-ask spread allowed (thin market tolerant)

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
