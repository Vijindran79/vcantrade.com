"""
VcaniTrade AI - Configuration
Safety-first trading configuration with strict defaults
"""

# ===== LION MODE CONFIGURATION =====
MAX_DAILY_TRADES = 30          # Hard cap to prevent overtrading
RE_ENTRY_LOCKOUT_MINUTES = 5   # Cooldown after a trade closes
TRAILING_STOP_CANDLE_MIN = 3   # Use 3-min candles for smoother stops
RSI_VETO_THRESHOLD = 85        # Abort if RSI > 85 (Buy) or < 20 (Sell) — relaxed for Frenzy Strike
WINDOW_SETTLE_TIME = 1.5       # Seconds to wait for window focus
MOUSE_HUMAN_DELAY_MIN = 0.8    # Min reaction time
MOUSE_HUMAN_DELAY_MAX = 1.6    # Max reaction time

# ===== TARGET-LOCKED SCANNING =====
# The bot will scan ONLY these symbols. No weekday/holiday checks.
# If only one symbol, the scanner locks onto it and executes directly.
ACTIVE_SYMBOLS = ["BTCUSD"]
# Confidence-Based Take Profit Targets
TP_LOW_CONFIDENCE = 50.0       # Quick profit target when AI confidence < 85%  ($50)
TP_HIGH_CONFIDENCE_MIN = 150.0 # Minimum target when AI confidence >= 85% ($150)
TP_HIGH_CONFIDENCE_MAX = 200.0 # Maximum target when AI confidence >= 85% ($200)
# Fast-trailing stop: lock break-even + trail after N dollars in profit
# LEGACY: These fixed-dollar values are now overridden by ATR-based logic
# in the execution path. Kept as absolute fallbacks only.
TRAILING_STOP_ACTIVATE_AFTER_PROFIT = 30.0  # $30 profit before trailing activates
TRAILING_STOP_DISTANCE = 15.0              # $15 trail distance after activation

import os
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

# ===== PROP FIRM RULES (The "Professor") =====
PROP_FIRM_ENABLED = os.getenv("PROP_FIRM_ENABLED", "True").lower() == "true"
PROP_FIRM_NAME = os.getenv("PROP_FIRM_NAME", "Apex Trader Funding")
PROP_ACCOUNT_SIZE = float(os.getenv("PROP_ACCOUNT_SIZE", "50000.0"))
PROP_PHASE = int(os.getenv("PROP_PHASE", "1"))
PROP_IS_FUNDED = os.getenv("PROP_IS_FUNDED", "False").lower() == "true"

# ===== ACCOUNT BALANCE (MUST MATCH YOUR PROP FIRM OR BROKER ACCOUNT) =====
CURRENT_BALANCE = float(os.getenv("CURRENT_BALANCE", "50000.0"))
# Fallback equity when live scrape fails (e.g., TradingView dashboard unreadable)
HARDCODED_EQUITY_FALLBACK = float(os.getenv("HARDCODED_EQUITY_FALLBACK", "50000.0"))

# ===== SAFETY CONTROLS (ALWAYS ON BY DEFAULT) =====
# PRODUCTION RULE: DRY_RUN defaults to True. You MUST explicitly set DRY_RUN=False in .env to trade live.
DRY_RUN = os.getenv("DRY_RUN", "True").lower() == "true"
# UNIFIED DAILY LOSS LIMIT — single source of truth.
# Apex Trader Funding has no daily loss limit (only trailing drawdown), so the
# default is 0 = disabled. Override per firm in .env if needed.
_max_daily_loss_env = os.getenv("MAX_DAILY_LOSS", "0")
try:
    MAX_DAILY_LOSS = float(_max_daily_loss_env)
except (TypeError, ValueError):
    MAX_DAILY_LOSS = 0.0
# Aliases kept for backward compatibility — every consumer must pull from
# MAX_DAILY_LOSS. These mirror the unified value so legacy reads stay correct.
DAILY_LOSS_LIMIT = MAX_DAILY_LOSS
DAILY_LOSS_KILL = MAX_DAILY_LOSS
# MAX_TRADES_PER_DAY: Maximum number of trades allowed per day
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "20"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
COOLDOWN_AFTER_STOP = int(os.getenv("COOLDOWN_AFTER_STOP", "300"))
KILL_SWITCH = False

# News filter behaviour when Forex Factory is unreachable in AUTONOMOUS mode.
# Default = true = FAIL OPEN (keep trading). The news scraper is unreliable
# and should not block the entire bot when Forex Factory changes their HTML
# or your DNS can't reach them.
NEWS_FILTER_FAIL_OPEN = os.getenv("NEWS_FILTER_FAIL_OPEN", "true").lower() == "true"

# Maximum hold time. Set to 0 to disable the hard time-stop entirely; otherwise
# the trade engine flattens any open position after this many seconds.
# Old default was 1800 (30 min) — far too short for futures trends.
MAX_TRADE_HOLD_SECONDS = int(os.getenv("MAX_TRADE_HOLD_SECONDS", "0"))

# ===== TRADING HOURS (UTC) =====
# Set your allowed trading window. The bot will NOT trade outside these hours.
# CME Globex futures are open 23 hours with 1 hour maintenance (approx 21:00-22:00 UTC).
# Example: 12=12:00 PM UTC, 21=9:00 PM UTC. Use -1 to disable time restriction.
TRADING_START_HOUR_UTC = int(os.getenv("TRADING_START_HOUR_UTC", "12"))
TRADING_END_HOUR_UTC = int(os.getenv("TRADING_END_HOUR_UTC", "21"))

# ===== OPTIONAL SYMBOL SAFETY LISTS =====
# These are only used by legacy broker-specific paths. TradingView and MT5 route
# through their own execution handlers and should not be blocked by a prop firm list.
FUTURES_WHITELIST = ["CL=F", "CL1!", "NQM6", "ESM6", "MGC"]
# Block stocks like TSLA, AAPL, SPX from legacy futures-only routes.
BLOCKED_STOCKS = ["TSLA", "AAPL", "SPX", "SPY", "NVDA"]

# ===== SYMBOL BRIDGE (TradingView → MT5 Broker) =====
# TradingView chart symbols can differ from broker-specific MT5 names.
# Override any value with an environment variable if your broker labels differ.
TRADINGVIEW_TICKERS = ("NQM6", "ESM6", "CL1!", "MGC")
# TradingView is the sole charting surface. No legacy aliases remain.

# Muted tickers: scanner will NEVER scan these.
MUTED_TICKERS = set()

SYMBOL_MAP = {
    "NQM6": os.getenv("MT5_NQM6_SYMBOL", "NQM6"),
    "ESM6": os.getenv("MT5_ESM6_SYMBOL", "ESM6"),
    "CL=F": os.getenv("MT5_CL_SYMBOL", "WTI_SB"),
    "CL1!": os.getenv("MT5_CL_SYMBOL", "WTI_SB"),
    "CLM26": os.getenv("MT5_CLM26_SYMBOL", "WTI_SB"),  # Legacy alias only
    "MGC": os.getenv("MT5_MGC_SYMBOL", "XAUUSD_SB"),
}

# Extra exact candidates to try before fuzzy searching the MT5 symbol list.
SYMBOL_BRIDGE_CANDIDATES = {
    "CL=F": ("WTI_SB", "Crude_SB", "Crude", "USOIL", "WTI", "XTIUSD", "OIL", "CL"),
    "CL1!": ("WTI_SB", "Crude_SB", "Crude", "USOIL", "WTI", "XTIUSD", "OIL", "CL"),
    "CLM26": ("WTI_SB", "Crude_SB", "Crude", "USOIL", "WTI", "XTIUSD", "OIL", "CL"),  # Legacy alias only
    "MGC": ("XAUUSD_SB", "XAUUSD", "Gold_SB", "Gold", "XAU", "MGC"),
    "NQM6": ("NQM6", "NAS100_SB", "NAS100", "MNQ"),
    "ESM6": ("ESM6", "US500_SB", "US500", "MES"),
}

# Terms used for fuzzy MarketWatch fallback after exact candidates fail.
SYMBOL_FUZZY_TERMS = {
    "CL=F": ("CL", "WTI", "Crude", "Oil"),
    "CL1!": ("CL", "WTI", "Crude", "Oil"),
    "CLM26": ("CL", "WTI", "Crude", "Oil"),  # Legacy alias only
    "MGC": ("MGC", "XAU", "GOLD", "Gold"),
    "NQM6": ("NQ", "NAS", "Nasdaq"),
    "ESM6": ("ES", "SP500", "S&P"),
}

# ===== TRADING MODE =====
TEACHER_MODE = os.getenv("TEACHER_MODE", "False").lower() == "true"

# Hard lock to Teacher Mode. When True, the Autonomous button on the dashboard
# is disabled and any AUTONOMOUS request is downgraded to TEACHER. Set this in
# the .env file with TEACHER_ONLY_LOCK=true. Useful for installs where the
# user wants signals only and never wants the bot to click on its own.
TEACHER_ONLY_LOCK = os.getenv("TEACHER_ONLY_LOCK", "False").lower() == "true"

# ===== LLM CONFIGURATION (Local Ollama + Qwen 2.5) =====
# Native Ollama API (for /api/generate, /api/tags, model management)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
# OpenAI-compatible v1 endpoint (for /v1/chat/completions with vision)
OLLAMA_V1_URL = os.getenv("OLLAMA_V1_URL", "http://127.0.0.1:11434")
MICRO_BRAIN_ENABLED = os.getenv("MICRO_BRAIN_ENABLED", "true").lower() == "true"
MICRO_BRAIN_MODEL = os.getenv("MICRO_BRAIN_MODEL", "qwen2.5:1.5b-instruct-q4_K_M")
OLLAMA_MODEL = os.getenv(
    "OLLAMA_MODEL",
    MICRO_BRAIN_MODEL if MICRO_BRAIN_ENABLED else "qwen2.5:1.5b-instruct-q4_K_M",
)
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "")
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
# DEFAULT BRAIN: 'local' skips OpenRouter entirely, uses Ollama Predator only
DEFAULT_BRAIN = os.getenv("DEFAULT_BRAIN", "local")
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

# ===== ACCOUNT TARGETING =====
# Single account mode for retail brokers. Comma-separated if you run multi-account.
TARGET_ACCOUNTS = _parse_key_list(os.getenv("TARGET_ACCOUNTS", ""))

# Google API Key slot (reserved, stays empty as requested)
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# Local execution settings
LLM_TIMEOUT = 180  # Heavy local Qwen runs need more time to finish reliably
OLLAMA_TIMEOUT = LLM_TIMEOUT  # Alias used by llm_analyzer swarm runner
JSON_OUTPUT = True

# ===== VISION / VLM CONFIGURATION =====
# 1. Enable Vision & Screen capturing
USE_VISION = True
VLM_MODEL = os.getenv("VLM_MODEL", "moondream:latest")
VISION_TIMEOUT = 120
SAVE_DEBUG_SCREENSHOTS = True

# 2. Turn on the Alarms and Sound Effects
PLAY_ALERT_SOUNDS = os.getenv("PLAY_ALERT_SOUNDS", "True").lower() == "true"
ENABLE_AUDIO_NARRATION = os.getenv("ENABLE_AUDIO_NARRATION", "True").lower() == "true"
PLAY_SCAN_TICK_SOUNDS = os.getenv("PLAY_SCAN_TICK_SOUNDS", "True").lower() == "true"
SCAN_TICK_SOUND_INTERVAL_SECONDS = float(os.getenv("SCAN_TICK_SOUND_INTERVAL_SECONDS", "3.0"))

# Visual command-center feedback
CONFIDENCE_OVERLAY_ENABLED = os.getenv("CONFIDENCE_OVERLAY_ENABLED", "True").lower() == "true"
ENABLE_FLASHING_ALERTS = os.getenv("ENABLE_FLASHING_ALERTS", "True").lower() == "true"
ALERT_FLASH_DURATION_MS = int(os.getenv("ALERT_FLASH_DURATION_MS", "6500"))
REALTIME_SCAN_FEED = os.getenv("REALTIME_SCAN_FEED", "True").lower() == "true"
SCAN_ACTIVITY_THROTTLE_SECONDS = float(os.getenv("SCAN_ACTIVITY_THROTTLE_SECONDS", "3.0"))

# Default TradingView chart capture region.
# Safe fallbacks prevent startup crashes even when vision is disabled.
CHART_REGION_X = int(os.getenv("CHART_REGION_X", "0"))
CHART_REGION_Y = int(os.getenv("CHART_REGION_Y", "0"))
CHART_REGION_W = int(os.getenv("CHART_REGION_W", "1280"))
CHART_REGION_H = int(os.getenv("CHART_REGION_H", "720"))

# ===== MARKET DATA =====
SCAN_INTERVAL = 15  # Reduced from 5 — less DOM polling = less detection
WATCHLIST_INTERVAL = 60
SNIPER_SCAN_INTERVAL = float(os.getenv("SNIPER_SCAN_INTERVAL", "3.0"))
CLOUD_TICKERS = ["CME_MINI:MNQ1!", "CME_MINI:MES1!", "CL=F"]

# yfinance requires dashed crypto symbols. Keep chart/broker symbols separate.
YFINANCE_SYMBOL_MAP = {
    "BTC": "BTC-USD",
    "BTCUSD": "BTC-USD",
    "BTCUSDT": "BTC-USD",
    "BTC-USD": "BTC-USD",
    "XBT": "BTC-USD",
    "XBTUSD": "BTC-USD",
    "ETH": "ETH-USD",
    "ETHUSD": "ETH-USD",
    "ETHUSDT": "ETH-USD",
    "ETH-USD": "ETH-USD",
    "SOL": "SOL-USD",
    "SOLUSD": "SOL-USD",
    "SOLUSDT": "SOL-USD",
    "SOL-USD": "SOL-USD",
    "XRP": "XRP-USD",
    "XRPUSD": "XRP-USD",
    "XRPUSDT": "XRP-USD",
    "XRP-USD": "XRP-USD",
    "CL=F": "CL=F",
    "CL1!": "CL=F",
    "NYMEX:CL1!": "CL=F",
    "NYMEX:CLM26!": "CL=F",
    "CLM26": "CL=F",
    "CLM26!": "CL=F",
}

# ===== MULTI-ASSET HUNTER (Vision-Based Chart Cycling) =====
# Cycles through NQ / ES / Oil every 30 seconds, screenshots each chart,
# sends to Cloud Brain via SSH tunnel, and executes trades locally.
MULTI_ASSET_TICKERS = ["CL=F", "CME_MINI:MNQ1!", "CME_MINI:MES1!", "COMEX:MGC1!"]
MULTI_ASSET_CYCLE_SECONDS = int(os.getenv("MULTI_ASSET_CYCLE_SECONDS", "15"))

# Symbol mapping: TradingView (Hunter) -> Yahoo Finance (Scanner/Cloud)
SYMBOL_TO_YAHOO_MAP = {
    "CME_MINI:MNQ1!": "MNQ=F",
    "CME_MINI:MES1!": "MES=F",
    "CL=F": "CL=F",
    "CL1!": "CL=F",
    "NYMEX:CL1!": "CL=F",
    "NYMEX:CLM26!": "CL=F",  # Legacy alias only
    "COMEX:MGC1!": "GC=F",
}
# Merge TradingView-side aliases into the canonical SYMBOL_MAP without
# overwriting the broker (MT5) entries already defined above.
for _alias, _yahoo in SYMBOL_TO_YAHOO_MAP.items():
    SYMBOL_MAP.setdefault(_alias, _yahoo)
del _alias, _yahoo
# Symbol mapping: Yahoo / internal ticker -> TradingView chart symbol.
# Used by browser_agent/rpa_executor when a chart symbol needs to be resolved.
# NQ=F/ES=F/CL=F map to the current working TradingView chart codes.
TRADINGVIEW_SYMBOL_MAP = {
    # Yahoo futures -> CME_MINI / NYMEX contract names
    "NQ=F":  "NQM6",
    "MNQ=F": "NQM6",
    "ES=F":  "ESM6",
    "MES=F": "ESM6",
    "CL=F":  "CL1!",
    "MCL=F": "MCL1!",
    # Canonical short forms (F stripped by candidate generator)
    "NQ":  "NQM6",
    "MNQ": "NQM6",
    "ES":  "ESM6",
    "MES": "ESM6",
    "CL":  "CL1!",
    "MCL": "MCL1!",
    # TradingView futures contract codes.
    "CME_MINI:MNQ1!": "NQM6",
    "CME_MINI:MES1!": "ESM6",
    "NYMEX:CL1!": "CL1!",
    "NYMEX:CLM26!": "CL1!",  # Legacy alias only
    # Bare TradingView contract codes (user-specified analysis tickers)
    "MNQ1!": "NQM6",
    "MES1!": "ESM6",
    "CL1!": "CL1!",
    "CLM26!": "CL1!",  # Legacy alias only
    # Gold (COMEX Micro Gold)
    "COMEX:MGC1!": "MGC",
    "GC=F": "MGC",
    "GC": "MGC",
    "MGC": "MGC",
    "XAUUSD": "MGC",
}

# Symbol mapping: Any ticker alias -> MT5 broker symbol (Scanner/MT5 data feed)
# Pepperstone UK DEMO account — Spread Betting (GBP) uses _SB suffix.
# Chart tabs show: Crude_SB, NAS100_SB, US500_SB
MT5_SYMBOL_MAP = {
    # ===== TradingView futures aliases -> Pepperstone broker symbols =====
    # These are the symbols the scanner receives - map them FIRST.
    "CL=F": "Crude_SB",
    "CL1!": "Crude_SB",
    "CLM26": "Crude_SB",  # Legacy alias only
    "NQM6": "NAS100_SB",
    "ESM6": "US500_SB",
    "MGC": "Gold_SB",
    # CME / NYMEX prefixes -> Pepperstone exact terminal name
    "CME_MINI:MNQ1!": "NAS100_SB",
    "CME_MINI:MES1!": "US500_SB",
    "NYMEX:CL1!": "Crude_SB",
    "NYMEX:CLM26!": "Crude_SB",  # Legacy alias only
    "CLM26!": "Crude_SB",  # Legacy alias only
    # Yahoo-style aliases -> Pepperstone
    "MNQ=F": "NAS100_SB",
    "MES=F": "US500_SB",
    "CL=F": "Crude_SB",
    "NQ=F": "NAS100_SB",
    "ES=F": "US500_SB",
    "GC=F": "Gold_SB",
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
    "GC": "Gold_SB",
    "SI": "XAGUSD",
    "YM": "YM1!",
    # Gold / Silver
    "XAUUSD": "XAUUSD_SB",
    "Gold_SB": "XAUUSD_SB",
    "COMEX:MGC1!": "XAUUSD_SB",
    "GC=F": "XAUUSD_SB",
    "GC": "XAUUSD_SB",
    "MGC": "XAUUSD_SB",
    # Fuzzy fallback fragments (used by scanner fuzzy search)
    "Crude": "Crude_SB",
    "WTI": "Crude_SB",
    "NAS100": "NAS100_SB",
    "US500": "US500_SB",
    "Gold": "Gold_SB",
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
MULTI_ASSET_VISION_MODEL = os.getenv("MULTI_ASSET_VISION_MODEL", "moondream:latest")
MULTI_ASSET_ENABLED = os.getenv("MULTI_ASSET_ENABLED", "True").lower() == "true"

# ===== EXECUTION MODE SWITCH =====
# "TV_DESKTOP" = Connect to TradingView Desktop via CDP on port 9222 (ghost JS injection)
# "MT5" = Send orders to MetaTrader 5 via mt5.order_send()
# TEACHER/AUTONOMOUS is controlled separately by DRY_RUN and TEACHER_MODE.
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "TV_DESKTOP").upper().strip()
TRADING_SURFACE = os.getenv("TRADING_SURFACE", "TRADINGVIEW_DESKTOP")

# ACTIVE EXECUTION SURFACE — runtime switchable between TradingView RPA and MT5
# "TRADINGVIEW" = Physical mouse clicks on TradingView web/paper interface
# "MT5" = Native MetaTrader 5 order execution
ACTIVE_EXECUTION_SURFACE = os.getenv("ACTIVE_EXECUTION_SURFACE", "TRADINGVIEW").upper().strip()

# TradingView account label to verify before clicking (e.g., "Paper Trading", "Live")
TRADINGVIEW_ACCOUNT_LABEL = os.getenv("TRADINGVIEW_ACCOUNT_LABEL", "Paper Trading").strip()
MT5_VOLUME = float(os.getenv("MT5_VOLUME", "0.1"))

# Side-by-Side Execution: local Ghost-Hand socket for TradingView/MT5 workflows.
EXECUTION_HOST = os.getenv("EXECUTION_HOST", "127.0.0.1").strip()

# SYNCHRONIZED CDP URL — all modules use this for port 9222
# Legacy modules (browser_agent) and new modules (ghost_executor) both read this.
BROWSER_CDP_URL = os.getenv("BROWSER_CDP_URL", "http://127.0.0.1:9222").strip()

# TradingView Desktop CDP — explicit alias for ghost_executor
TV_DESKTOP_CDP_URL = os.getenv("TV_DESKTOP_CDP_URL", "http://127.0.0.1:9222").strip()
# How long (seconds) to wait for TradingView Desktop to be ready after launch
TV_DESKTOP_CONNECT_TIMEOUT = int(os.getenv("TV_DESKTOP_CONNECT_TIMEOUT", "15"))
LOW_LATENCY_EXECUTION_ENABLED = os.getenv("LOW_LATENCY_EXECUTION_ENABLED", "true").lower() == "true"
HYBRID_GATEWAY_PRIMARY = os.getenv("HYBRID_GATEWAY_PRIMARY", "broker_ws").lower().strip()
BROKER_WS_URL = os.getenv("BROKER_WS_URL", "").strip()
BROKER_WS_TOKEN = os.getenv("BROKER_WS_TOKEN", "").strip()
BROKER_WS_TIMEOUT_SECONDS = float(os.getenv("BROKER_WS_TIMEOUT_SECONDS", "0.75"))
BROKER_WS_DRY_RUN = os.getenv("BROKER_WS_DRY_RUN", "true").lower() == "true"
FAST_VISION_ENABLED = os.getenv("FAST_VISION_ENABLED", "true").lower() == "true"
FAST_VISION_BACKEND = os.getenv("FAST_VISION_BACKEND", "auto").lower().strip()
HUD_GLASS_ENABLED = os.getenv("HUD_GLASS_ENABLED", "true").lower() == "true"
SHOW_STARTUP_SWITCHBOARD = os.getenv("SHOW_STARTUP_SWITCHBOARD", "false").lower() == "true"
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
        "TradingView,Google Chrome,Chrome,Brave,Microsoft Edge,Edge",
    ).split(",")
    if value.strip()
]

# TradingView / browser window hints.
TRADING_WINDOW_HINTS = [
    value.strip()
    for value in os.getenv(
        "TRADING_WINDOW_HINTS",
        "TradingView,Google Chrome,Chrome,Brave,Microsoft Edge,Edge,MetaTrader 5,MetaTrader",
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
SWARM_INCUBATION_FLOOR = float(os.getenv("SWARM_INCUBATION_FLOOR", "50.0"))
SWARM_HIGH_PRIORITY_THRESHOLD = float(os.getenv("SWARM_HIGH_PRIORITY_THRESHOLD", "70.0"))
SWARM_CONFIDENCE_THRESHOLD = SWARM_INCUBATION_FLOOR / 100.0
MIN_CONFIDENCE_THRESHOLD = SWARM_INCUBATION_FLOOR
VISUAL_ALERT_MIN_CONFIDENCE = SWARM_HIGH_PRIORITY_THRESHOLD / 100.0
MTF_STRUCTURE_FILTER_ENABLED = os.getenv("MTF_STRUCTURE_FILTER_ENABLED", "True").lower() == "true"
MTF_STRUCTURE_ZONE_ATR_MULTIPLIER = float(os.getenv("MTF_STRUCTURE_ZONE_ATR_MULTIPLIER", "0.65"))
MTF_STRUCTURE_PROXIMITY_PCT = float(os.getenv("MTF_STRUCTURE_PROXIMITY_PCT", "0.0025"))
SIGNAL_COOLDOWN_SECONDS = int(os.getenv("SIGNAL_COOLDOWN_SECONDS", "300"))
MT5_REQUIRE_PROTECTIVE_STOP = os.getenv("MT5_REQUIRE_PROTECTIVE_STOP", "true").lower() == "true"
AUTONOMOUS_CLOSE_AND_REVERSE_ENABLED = os.getenv("AUTONOMOUS_CLOSE_AND_REVERSE_ENABLED", "false").lower() == "true"
AI_OVERLAY_START_PINNED = os.getenv("AI_OVERLAY_START_PINNED", "false").lower() == "true"

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

# Safe fallback coordinates for TradingView RPA button clicks when DOM targeting fails.
TRADINGVIEW_FALLBACK_COORDS = {
    "buy_button": (
        int(os.getenv("TRADINGVIEW_BUY_X", "960")),
        int(os.getenv("TRADINGVIEW_BUY_Y", "540")),
    ),
    "sell_button": (
        int(os.getenv("TRADINGVIEW_SELL_X", "960")),
        int(os.getenv("TRADINGVIEW_SELL_Y", "580")),
    ),
    "flatten_button": (
        int(os.getenv("TRADINGVIEW_FLATTEN_X", "960")),
        int(os.getenv("TRADINGVIEW_FLATTEN_Y", "620")),
    ),
}
FALLBACK_COORDS = TRADINGVIEW_FALLBACK_COORDS

# Multi-monitor support
MULTI_MONITOR_ENABLED = os.getenv("MULTI_MONITOR_ENABLED", "True").lower() == "true"
PRIMARY_MONITOR_WIDTH = int(os.getenv("PRIMARY_MONITOR_WIDTH", "1920"))
PRIMARY_MONITOR_HEIGHT = int(os.getenv("PRIMARY_MONITOR_HEIGHT", "1080"))

# ===== SLIPPAGE GUARD =====
MAX_SLIPPAGE_PERCENT = float(os.getenv("MAX_SLIPPAGE_PERCENT", "2.50"))
MAX_SPREAD_PERCENT = float(os.getenv("MAX_SPREAD_PERCENT", "0.30"))

# ===== RETAIL BROKER CONFIGURATION =====
# All execution routes through TradingView RPA or MT5 native API.
# Legacy desktop adapters have been fully removed.

# ===== AGGRESSIVE HUNTER =====
# If signal confidence >= this threshold, skip 1m/3m MTF alignment and strike on 5m alone.
AGGRESSIVE_HUNTER_CONFIDENCE_PCT = float(os.getenv("AGGRESSIVE_HUNTER_CONFIDENCE_PCT", "65.0"))

# ===== AUTONOMOUS RISK MANAGEMENT =====
# ATR-driven risk management (replaces fixed-dollar thresholds).
# Break-even triggers when trade is up 1× ATR from entry.
# Trailing stop follows at 1× ATR behind the best price.
# These ATR multipliers are the core risk logic:
ATR_STOP_MULTIPLIER = float(os.getenv("ATR_STOP_MULTIPLIER", "1.5"))       # SL = entry ± ATR × 1.5
ATR_TP_MULTIPLIER = float(os.getenv("ATR_TP_MULTIPLIER", "3.0"))           # TP = entry ± ATR × 3.0 (2:1 R:R)
ATR_BREAKEVEN_MULTIPLIER = float(os.getenv("ATR_BREAKEVEN_MULTIPLIER", "1.0"))  # Move to BE after 1× ATR profit
ATR_TRAIL_MULTIPLIER = float(os.getenv("ATR_TRAIL_MULTIPLIER", "1.0"))     # Trail at 1× ATR behind best price
# Legacy fixed-dollar fallbacks (used only when ATR data is unavailable):
AUTONOMOUS_BREAK_EVEN_TRIGGER_USD = float(os.getenv("AUTONOMOUS_BREAK_EVEN_TRIGGER_USD", "15.0"))
AUTONOMOUS_BREAK_EVEN_PLUS_USD = float(os.getenv("AUTONOMOUS_BREAK_EVEN_PLUS_USD", "2.0"))
AUTONOMOUS_TRAILING_LOOKBACK_BARS = 3
AUTONOMOUS_TRAILING_UPDATE_SECONDS = 60

# ===== LOGGING =====
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = "vcani_trade.log"
POSITION_OPEN_IMAGE = "assets/tv_position_open_label.png"

# ===== NEWS/EVENTS TIMEOUTS =====
NEWS_REQUEST_TIMEOUT = 10
NEWS_CONNECT_TIMEOUT = 5

DAILY_PROFIT_TARGET = 1500.0

POSITION_SIZE_HIGH_CONF = 10

DAILY_LOSS_KILL = 1000.0


# ===== UNIFIED MODE HELPER =====
def get_active_mode() -> str:
    """Single source of truth for execution mode.

    Returns "TRADINGVIEW" or "MT5".
    Dashboard toggle (ACTIVE_EXECUTION_SURFACE) takes priority,
    then falls back to EXECUTION_MODE env var.
    """
    surface = str(globals().get("ACTIVE_EXECUTION_SURFACE", "") or "").upper().strip()
    if surface in ("TRADINGVIEW", "MT5"):
        return surface
    exec_mode = str(globals().get("EXECUTION_MODE", "") or "").upper().strip()
    if exec_mode == "MT5":
        return "MT5"
    return "TRADINGVIEW"
