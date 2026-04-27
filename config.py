"""
VcaniTrade AI - Configuration
Safety-first trading configuration with strict defaults
"""

# --- LION MODE CONFIGURATION ---
MAX_DAILY_TRADES = 30          # Hard cap to prevent overtrading
RE_ENTRY_LOCKOUT_MINUTES = 5   # Cooldown after a trade closes
TRAILING_STOP_CANDLE_MIN = 3   # Use 3-min candles for smoother stops
RSI_VETO_THRESHOLD = 85        # Abort if RSI > 85 (Buy) or < 20 (Sell) — relaxed for Frenzy Strike
WINDOW_SETTLE_TIME = 1.5       # Seconds to wait for window focus
MOUSE_HUMAN_DELAY_MIN = 0.8    # Min reaction time
MOUSE_HUMAN_DELAY_MAX = 1.6    # Max reaction time

import os
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

# ===== PROP FIRM RULES (The "Professor") =====
PROP_FIRM_ENABLED = os.getenv("PROP_FIRM_ENABLED", "True").lower() == "true"
PROP_FIRM_NAME = os.getenv("PROP_FIRM_NAME", "TopStep")
PROP_ACCOUNT_SIZE = float(os.getenv("PROP_ACCOUNT_SIZE", "50000.0"))
PROP_PHASE = int(os.getenv("PROP_PHASE", "1"))
PROP_IS_FUNDED = os.getenv("PROP_IS_FUNDED", "False").lower() == "true"

# ===== ACCOUNT BALANCE (MUST MATCH YOUR PROP FIRM OR BROKER ACCOUNT) =====
CURRENT_BALANCE = float(os.getenv("CURRENT_BALANCE", "50000.0"))
# Fallback equity when live scrape fails (e.g., TradingView dashboard unreadable)
HARDCODED_EQUITY_FALLBACK = float(os.getenv("HARDCODED_EQUITY_FALLBACK", "77500.0"))

# ===== SAFETY CONTROLS (ALWAYS ON BY DEFAULT) =====
# PRODUCTION RULE: DRY_RUN defaults to True. You MUST explicitly set DRY_RUN=False in .env to trade live.
DRY_RUN = os.getenv("DRY_RUN", "True").lower() == "true"
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", "100.00"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
COOLDOWN_AFTER_STOP = int(os.getenv("COOLDOWN_AFTER_STOP", "300"))
KILL_SWITCH = False

# ===== TRADING HOURS (UTC) =====
# Set your allowed trading window. The bot will NOT trade outside these hours.
# CME Globex futures are open 23 hours with 1 hour maintenance (approx 21:00-22:00 UTC).
# Example: 12=12:00 PM UTC, 21=9:00 PM UTC. Use -1 to disable time restriction.
TRADING_START_HOUR_UTC = int(os.getenv("TRADING_START_HOUR_UTC", "12"))
TRADING_END_HOUR_UTC = int(os.getenv("TRADING_END_HOUR_UTC", "21"))

# ===== TRADING MODE =====
TEACHER_MODE = os.getenv("TEACHER_MODE", "False").lower() == "true"

# ===== LLM CONFIGURATION (Local Ollama + Qwen 2.5) =====
# Native Ollama API (for /api/generate, /api/tags, model management)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
# OpenAI-compatible v1 endpoint (for /v1/chat/completions with vision)
OLLAMA_V1_URL = os.getenv("OLLAMA_V1_URL", "http://127.0.0.1:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:latest")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "ollama")
VAST_API_TOKEN = None

# ===== GEMINI LIVE BRAIN =====
GEMINI_ENABLED = os.getenv("GEMINI_ENABLED", "True").lower() == "true"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", ""))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT", "20"))


# ===== EXTERNAL BRAIN PROVIDERS =====
# SECURITY: Load API keys from .env file only. Never hardcode keys in production.
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_API_KEYS = os.getenv("OPENROUTER_API_KEYS", "")
GROQ_API_KEYS = os.getenv("GROQ_API_KEYS", "")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
BRAINSTORM_API_KEY = os.getenv("BRAINSTORM_API_KEY", "")


def _parse_key_list(raw: str) -> list[str]:
    """Parse a comma-separated key string into a clean list."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(k).strip() for k in raw if str(k).strip()]
    return [k.strip() for k in str(raw).split(",") if k.strip()]


# Parsed key lists for rotation logic.
# Budget-friendly order: free/low-cost keys should be listed FIRST in .env.
OPENROUTER_KEY_LIST = _parse_key_list(OPENROUTER_API_KEYS) or _parse_key_list(OPENROUTER_API_KEY)
GROQ_KEY_LIST = _parse_key_list(GROQ_API_KEYS)
NVIDIA_KEY_LIST = _parse_key_list(NVIDIA_API_KEY)
BRAINSTORM_KEY_LIST = _parse_key_list(BRAINSTORM_API_KEY)

# Google API Key slot (reserved, stays empty as requested)
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# Local execution settings
LLM_TIMEOUT = 180  # Heavy local Qwen runs need more time to finish reliably
OLLAMA_TIMEOUT = LLM_TIMEOUT  # Alias used by llm_analyzer swarm runner
JSON_OUTPUT = True

# ===== VISION / VLM CONFIGURATION =====
USE_VISION = os.getenv("USE_VISION", "False").lower() == "true"
VLM_MODEL = os.getenv("VLM_MODEL", "llava:7b")
VISION_TIMEOUT = 120
SAVE_DEBUG_SCREENSHOTS = os.getenv("SAVE_DEBUG_SCREENSHOTS", "False").lower() == "true"

# Default TradingView chart capture region.
# Safe fallbacks prevent startup crashes even when vision is disabled.
CHART_REGION_X = int(os.getenv("CHART_REGION_X", "0"))
CHART_REGION_Y = int(os.getenv("CHART_REGION_Y", "0"))
CHART_REGION_W = int(os.getenv("CHART_REGION_W", "1280"))
CHART_REGION_H = int(os.getenv("CHART_REGION_H", "720"))

# ===== MARKET DATA =====
SCAN_INTERVAL = 10
WATCHLIST_INTERVAL = 60
SNIPER_SCAN_INTERVAL = float(os.getenv("SNIPER_SCAN_INTERVAL", "1.5"))
CLOUD_TICKERS = ["BTC-USD", "ES=F", "NQ=F"]

# ===== MULTI-ASSET HUNTER (Vision-Based Chart Cycling) =====
# Cycles through NQ / ES / Oil every 30 seconds, screenshots each chart,
# sends to Cloud Brain via SSH tunnel, and executes trades locally.
MULTI_ASSET_TICKERS = ["NYMEX:MCL1!"]
MULTI_ASSET_CYCLE_SECONDS = int(os.getenv("MULTI_ASSET_CYCLE_SECONDS", "15"))

# Symbol mapping: TradingView (Hunter) -> Yahoo Finance (Scanner/Cloud)
SYMBOL_MAP = {
    "CME_MINI:MNQ1!": "MNQ=F",
    "CME_MINI:MES1!": "MES=F",
    "NYMEX:MCL1!": "CL=F",
}

# Symbol mapping: Yahoo / internal ticker -> WealthCharts chart symbol (M6 contract codes)
# Used by browser_agent and rpa_executor to navigate to the correct chart.
# NQ=F/ES=F/CL=F map to June 2026 (M6) futures contract codes for WealthCharts.
TRADINGVIEW_SYMBOL_MAP = {
    # Yahoo futures -> CME_MINI / NYMEX contract names
    "NQ=F":  "NQM6",
    "MNQ=F": "NQM6",
    "ES=F":  "ESM6",
    "MES=F": "ESM6",
    "CL=F":  "MCLM6",
    "MCL=F": "MCLM6",
    # Canonical short forms (F stripped by candidate generator)
    "NQ":  "NQM6",
    "MNQ": "NQM6",
    "ES":  "ESM6",
    "MES": "ESM6",
    "CL":  "MCLM6",
    "MCL": "MCLM6",
    # WealthCharts June 2026 (M6) contract codes — exact symbols on dashboard
    "CME_MINI:MNQ1!": "NQM6",
    "CME_MINI:MES1!": "ESM6",
    "NYMEX:MCL1!": "MCLM6",
}

# Symbol mapping: Any ticker alias -> MT5 broker symbol (Scanner/MT5 data feed)
# Pepperstone UK DEMO account — Spread Betting (GBP) uses _SB suffix.
# Chart tabs show: Crude_SB, NAS100_SB, US500_SB
MT5_SYMBOL_MAP = {
    # CME / NYMEX prefixes -> Pepperstone exact terminal name
    "CME_MINI:MNQ1!": "NAS100_SB",
    "CME_MINI:MES1!": "US500_SB",
    "NYMEX:MCL1!": "Crude_SB",
    "MCL1!": "Crude_SB",  # bare contract form
    # Yahoo-style aliases -> Pepperstone
    "MNQ=F": "NAS100_SB",
    "MES=F": "US500_SB",
    "CL=F": "Crude_SB",
    "NQ=F": "NAS100_SB",
    "ES=F": "US500_SB",
    "GC=F": "XAUUSD",
    "SI=F": "XAGUSD",
    "YM=F": "YM1!",
    "RTY=F": "M2K1!",
    "HG=F": "HG1!",
    # Canonical short forms (F stripped) -> Pepperstone
    "ES": "US500_SB",
    "NQ": "NAS100_SB",
    "CL": "Crude_SB",
    "MES": "US500_SB",
    "MNQ": "NAS100_SB",
    "MCL": "Crude_SB",
    "GC": "XAUUSD",
    "SI": "XAGUSD",
    "YM": "YM1!",
    # PEPPERSTONE EXACT TERMINAL NAMES (self-references for suffix-stripped candidates)
    "Crude_SB": "Crude_SB",
    "NAS100_SB": "NAS100_SB",
    "US500_SB": "US500_SB",
    # Crypto (may not be available on SB account)
    "BTC-USD": "BTCUSD",
    "ETH-USD": "ETHUSD",
    "BTCUSD": "BTCUSD",
    "ETHUSD": "ETHUSD",
    # Stocks
    "TSLA": "TSLA",
    "NVDA": "NVDA",
    "AAPL": "AAPL",
}
MULTI_ASSET_VISION_MODEL = os.getenv("MULTI_ASSET_VISION_MODEL", "llava:7b")
MULTI_ASSET_ENABLED = os.getenv("MULTI_ASSET_ENABLED", "True").lower() == "true"

# ===== EXECUTION MODE SWITCH =====
# "UI"  = Click buttons on screen via Playwright/RPA (default)
# "MT5" = Send orders to MetaTrader 5 via mt5.order_send()
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "UI")
TRADING_SURFACE = os.getenv("TRADING_SURFACE", "WEALTHCHARTS")
MT5_VOLUME = float(os.getenv("MT5_VOLUME", "0.1"))
BROWSER_CDP_URL = os.getenv("BROWSER_CDP_URL", "http://127.0.0.1:9223").strip()

# WealthCharts platform URL (replaces TradingView)
WEALTHCHARTS_URL = os.getenv("WEALTHCHARTS_URL", "https://app.wealthcharts.com").strip().rstrip("/")
SHOW_STARTUP_SWITCHBOARD = os.getenv("SHOW_STARTUP_SWITCHBOARD", "true").lower() == "true"
SMART_EYE_ENABLED = os.getenv("SMART_EYE_ENABLED", "true").lower() == "true"
AUTO_SYMBOL_DETECTION = os.getenv("AUTO_SYMBOL_DETECTION", "true").lower() == "true"
DETECTED_TRADING_WINDOW_TITLE = os.getenv("DETECTED_TRADING_WINDOW_TITLE", "").strip()
MT5_WINDOW_HINTS = [
    value.strip()
    for value in os.getenv(
        "MT5_WINDOW_HINTS",
        "MetaTrader 5,MetaTrader5,Exness MetaTrader 5,IC Markets MetaTrader 5",
    ).split(",")
    if value.strip()
]
BROWSER_WINDOW_HINTS = [
    value.strip()
    for value in os.getenv(
        "BROWSER_WINDOW_HINTS",
        "WealthCharts,TradingView,Google Chrome,Chrome,Brave,Microsoft Edge,Edge",
    ).split(",")
    if value.strip()
]
WINDOW_TITLE_BLACKLIST = [
    value.strip()
    for value in os.getenv(
        "WINDOW_TITLE_BLACKLIST",
        "PowerShell,pwsh,Command Prompt,cmd.exe,Terminal,Visual Studio Code",
    ).split(",")
    if value.strip()
]
MT5_CHART_CROP_LEFT_PCT = float(os.getenv("MT5_CHART_CROP_LEFT_PCT", "0.04"))
MT5_CHART_CROP_TOP_PCT = float(os.getenv("MT5_CHART_CROP_TOP_PCT", "0.11"))
MT5_CHART_CROP_WIDTH_PCT = float(os.getenv("MT5_CHART_CROP_WIDTH_PCT", "0.92"))
MT5_CHART_CROP_HEIGHT_PCT = float(os.getenv("MT5_CHART_CROP_HEIGHT_PCT", "0.78"))
FRIDAY_CLOSE_CUTOFF_UTC = int(os.getenv("FRIDAY_CLOSE_CUTOFF_UTC", "18"))

# Technical Signal Thresholds
VOLUME_SPIKE_MULTIPLIER = 3.0
PRICE_SPIKE_THRESHOLD_PCT = float(os.getenv("PRICE_SPIKE_THRESHOLD_PCT", "1.5"))  # % move in 5 bars = spike
RSI_OVERBOUGHT = 85
RSI_OVERSOLD = 30
SMA_FAST = 20
SMA_SLOW = 50
SWARM_CONFIDENCE_THRESHOLD = 0.60  # TEMPORARY TEST: lowered from 0.50 for Sunday signal testing
MIN_CONFIDENCE_THRESHOLD = 60.0    # TEMPORARY TEST: lowered from 50.0 for Sunday signal testing

# ===== CLOUD SCANNER =====
CLOUD_SCANNER_ENABLED = True
CLOUD_SCANNER_URL = os.getenv("CLOUD_SCANNER_URL", "http://localhost:17199")
LOCAL_LISTENER_HOST = os.getenv("LOCAL_LISTENER_HOST", "0.0.0.0")
LOCAL_LISTENER_HEALTH_HOST = os.getenv("LOCAL_LISTENER_HEALTH_HOST", "127.0.0.1")
LOCAL_LISTENER_PORT = int(os.getenv("LOCAL_LISTENER_PORT", "17199"))
PUBLIC_SIGNAL_URL = os.getenv("PUBLIC_SIGNAL_URL", "").strip()
SIGNAL_API_KEY = os.getenv("SIGNAL_API_KEY", "").strip()
SIGNAL_API_HEADER = os.getenv("SIGNAL_API_HEADER", "X-Signal-Key").strip() or "X-Signal-Key"
LOCAL_EXECUTION_TIMEOUT = float(os.getenv("LOCAL_EXECUTION_TIMEOUT", "30"))

# ===== RPA EXECUTION =====
USE_HOTKEYS = True
HOTKEY_BUY = "<ctrl>+b"
HOTKEY_SELL = "<ctrl>+s"
HOTKEY_CLOSE = "<ctrl>+x"
HUMAN_LATENCY = os.getenv("HUMAN_LATENCY", "True").lower() == "true"

# Safe fallback coordinates for RPA button clicks when color detection fails.
# Update these to match your screen resolution and WealthCharts layout.
FALLBACK_COORDS = {
    "buy_button": (int(os.getenv("FALLBACK_BUY_X", "960")), int(os.getenv("FALLBACK_BUY_Y", "540"))),
    "sell_button": (int(os.getenv("FALLBACK_SELL_X", "960")), int(os.getenv("FALLBACK_SELL_Y", "580"))),
}

# ===== SLIPPAGE GUARD =====
MAX_SLIPPAGE_PERCENT = float(os.getenv("MAX_SLIPPAGE_PERCENT", "2.50"))
MAX_SPREAD_PERCENT = float(os.getenv("MAX_SPREAD_PERCENT", "0.30"))

# ===== AGGRESSIVE HUNTER =====
# If signal confidence >= this threshold, skip 1m/3m MTF alignment and strike on 5m alone.
AGGRESSIVE_HUNTER_CONFIDENCE_PCT = float(os.getenv("AGGRESSIVE_HUNTER_CONFIDENCE_PCT", "65.0"))

# ===== AUTONOMOUS RISK MANAGEMENT =====
AUTONOMOUS_BREAK_EVEN_TRIGGER_USD = 15.0
AUTONOMOUS_BREAK_EVEN_PLUS_USD = 2.0
AUTONOMOUS_TRAILING_LOOKBACK_BARS = 3
AUTONOMOUS_TRAILING_UPDATE_SECONDS = 60

# ===== LOGGING =====
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = "vcani_trade.log"
POSITION_OPEN_IMAGE = "assets/tv_position_open_label.png"

# ===== NEWS/EVENTS TIMEOUTS =====
NEWS_REQUEST_TIMEOUT = 10
NEWS_CONNECT_TIMEOUT = 5
