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

# Local execution settings
LLM_TIMEOUT = 15  # Reduced for speed over perfection
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

# 10 Core Counters for Cloud Scanner (Mix of Crypto, Forex, Stocks - 24/7 coverage)
CLOUD_TICKERS = [
    "BTC-USD",  # Bitcoin (24/7 trading - ALWAYS OPEN)
    "ETH-USD",  # Ethereum (24/7 trading)
    "GC=F",  # Gold Futures
    "EURUSD=X",  # Euro/USD (Forex - 24/5)
    "GBPUSD=X",  # GBP/USD (Forex - 24/5)
    "TSLA",  # Tesla
    "SPY",  # S&P 500 ETF
    "QQQ",  # NASDAQ ETF
    "AAPL",  # Apple
    "NVDA",  # NVIDIA
]

# Local Watchlist (for local scanning when cloud unavailable)
LOCAL_ASSETS = ["EURUSD", "GBPUSD", "XAUUSD", "BTCUSD", "ETHUSD"]

# Technical Signal Thresholds
VOLUME_SPIKE_MULTIPLIER = 3.0  # Trigger if volume > 3x average
RSI_OVERBOUGHT = 70  # RSI > 70 = potential sell
RSI_OVERSOLD = 30  # RSI < 30 = potential buy
SMA_FAST = 20  # Fast SMA period
SMA_SLOW = 50  # Slow SMA period
SWARM_CONFIDENCE_THRESHOLD = 0.55  # Minimum confidence to trigger trade (0.0-1.0) - Nuclear mode for active trading

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

# ===== RPA EXECUTION =====
USE_HOTKEYS = True  # Prefer keyboard hotkeys over mouse clicks
HOTKEY_BUY = "<ctrl>+b"  # Buy order hotkey
HOTKEY_SELL = "<ctrl>+s"  # Sell order hotkey
HOTKEY_CLOSE = "<ctrl>+x"  # Close position hotkey

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
