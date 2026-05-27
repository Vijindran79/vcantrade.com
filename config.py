"""
VcaniTrade AI - Configuration
Safety-first trading configuration with strict defaults
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

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
# These are only used by legacy broker-specific paths. TradingView and MT5 routes
# through their own execution handlers and should not be blocked by a prop firm list.
FUTURES_WHITELIST = ["MNQ1!", "MES1!", "MCL1!", "MGC1!", "MYM1!", "M2K1!", "M6A1!", "M6E1!", "MBT1!", "MET1!"]
# Block stocks like TSLA, AAPL, SPX from legacy futures-only routes.
BLOCKED_STOCKS = ["TSLA", "AAPL", "SPX", "SPY", "NVDA"]

# ===== SYMBOL BRIDGE (TradingView → MT5 Broker) =====
# TradingView chart symbols can differ from broker-specific MT5 names.
# Override any value with an environment variable if your broker labels differ.
TRADINGVIEW_TICKERS = ("NQM6", "ESM6", "CL1!", "MGC")

# TradingView is the sole charting surface. No legacy aliases remain.

# Muted tickers: scanner will NEVER scan these.
MUTED_TICKERS = set()

# ===== VISION ENGINE CHART CAPTURE REGION =====
# Default screenshot region for chart capture (x, y, width, height)
# These coordinates define the TradingView chart area to capture
CHART_REGION_X = int(os.getenv("CHART_REGION_X", "100"))
CHART_REGION_Y = int(os.getenv("CHART_REGION_Y", "100"))
CHART_REGION_W = int(os.getenv("CHART_REGION_W", "1200"))
CHART_REGION_H = int(os.getenv("CHART_REGION_H", "800"))

# =========================================================================
# UNIFIED SYMBOL MAPS — RESILIENT ALIAS SYNCHRONIZATION
# =========================================================================

# ----- Primary Symbol Map (TradingView chart -> MT5 broker) -----
SYMBOL_MAP = {
    # CME Micro Nasdaq 100
    "MNQ1!": "NAS100_SB",
    "MNQ": "NAS100_SB",
    "NQM6": "NAS100_SB",
    "NQ": "NAS100_SB",
    "NQ=F": "NAS100_SB",
    # CME Micro S&P 500
    "MES1!": "US500_SB",
    "MES": "US500_SB",
    "ESM6": "US500_SB",
    "ES": "US500_SB",
    "ES=F": "US500_SB",
    # NYMEX Micro Crude Oil
    "MCL1!": "Crude_SB",
    "MCL": "Crude_SB",
    "CL=F": "Crude_SB",
    "CL1!": "Crude_SB",
    "CL": "Crude_SB",
    # COMEX Micro Gold
    "MGC1!": "XAUUSD_SB",
    "MGC": "XAUUSD_SB",
    "GC=F": "XAUUSD_SB",
    "GC": "XAUUSD_SB",
    # CBOT Micro Dow
    "MYM1!": "DJ30_SB",
    "MYM": "DJ30_SB",
    "YM": "DJ30_SB",
    "YM=F": "DJ30_SB",
    # CME Micro Russell 2000
    "M2K1!": "RUS2000_SB",
    "M2K": "RUS2000_SB",
    "RTY": "RUS2000_SB",
    "RTY=F": "RUS2000_SB",
    # CME Micro AUD/USD
    "M6A1!": "AUDUSD",
    "M6A": "AUDUSD",
    # CME Micro EUR/USD
    "M6E1!": "EURUSD",
    "M6E": "EURUSD",
    # Crypto
    "MBT1!": "BTCUSD",
    "MBT": "BTCUSD",
    "BTCUSD": "BTCUSD",
    "MET1!": "ETHUSD",
    "MET": "ETHUSD",
    "ETHUSD": "ETHUSD",
}

# ----- MT5 Symbol Map (TradingView alias -> Exact MT5 terminal name) -----
MT5_SYMBOL_MAP = {
    # Apex micro futures -> Pepperstone Spread Betting symbols
    "MNQ1!": "NAS100_SB",
    "MES1!": "US500_SB",
    "MCL1!": "Crude_SB",
    "MGC1!": "Gold_SB",
    "MYM1!": "DJ30_SB",
    "M2K1!": "RUS2000_SB",
    "M6A1!": "AUDUSD",
    "M6E1!": "EURUSD",
    "MBT1!": "BTCUSD",
    "MET1!": "ETHUSD",
    # Continuous contracts -> MT5 symbols
    "CL=F": "Crude_SB",
    "GC=F": "Gold_SB",
    "NQ=F": "NAS100_SB",
    "ES=F": "US500_SB",
    "YM=F": "DJ30_SB",
    "RTY=F": "RUS2000_SB",
    # Plain roots -> MT5 symbols
    "MNQ": "NAS100_SB",
    "MES": "US500_SB",
    "MCL": "Crude_SB",
    "MGC": "Gold_SB",
    "MYM": "DJ30_SB",
    "M2K": "RUS2000_SB",
    "M6A": "AUDUSD",
    "M6E": "EURUSD",
    "MBT": "BTCUSD",
    "MET": "ETHUSD",
    # TradingView prefixed forms
    "CME_MINI:MNQ1!": "NAS100_SB",
    "CME_MINI:MES1!": "US500_SB",
    "NYMEX:MCL1!": "Crude_SB",
    "COMEX:MGC1!": "Gold_SB",
    "CBOT_MINI:MYM1!": "DJ30_SB",
    "CME_MINI:M2K1!": "RUS2000_SB",
    # Yahoo-style aliases
    "CL1!": "Crude_SB",
    "GC1!": "Gold_SB",
    "NQ1!": "NAS100_SB",
    "ES1!": "US500_SB",
    # Pepperstone exact terminal names (self-references)
    "NAS100_SB": "NAS100_SB",
    "US500_SB": "US500_SB",
    "Crude_SB": "Crude_SB",
    "Gold_SB": "Gold_SB",
    "DJ30_SB": "DJ30_SB",
    "RUS2000_SB": "RUS2000_SB",
    "AUDUSD": "AUDUSD",
    "EURUSD": "EURUSD",
    "BTCUSD": "BTCUSD",
    "ETHUSD": "ETHUSD",
}

# ----- TradingView Symbol Map (Any alias -> Exact TradingView chart symbol) -----
TRADINGVIEW_SYMBOL_MAP = {
    # Apex Micro futures (continuous contracts)
    "MNQ": "MNQ1!", "MNQ=F": "MNQ1!", "MNQ1!": "MNQ1!",
    "MES": "MES1!", "MES=F": "MES1!", "MES1!": "MES1!",
    "MCL": "MCL1!", "MCL=F": "MCL1!", "MCL1!": "MCL1!",
    "MGC": "MGC1!", "MGC=F": "MGC1!", "MGC1!": "MGC1!",
    "MYM": "MYM1!", "MYM=F": "MYM1!", "MYM1!": "MYM1!",
    "M2K": "M2K1!", "M2K=F": "M2K1!", "M2K1!": "M2K1!",
    "M6A": "M6A1!", "M6A1!": "M6A1!",
    "M6E": "M6E1!", "M6E1!": "M6E1!",
    "MBT": "MBT1!", "MBT1!": "MBT1!",
    "MET": "MET1!", "MET1!": "MET1!",
    # Yahoo aliases
    "NQ": "MNQ1!", "NQ=F": "MNQ1!",
    "ES": "MES1!", "ES=F": "MES1!",
    "CL": "MCL1!", "CL=F": "MCL1!",
    "GC": "MGC1!", "GC=F": "MGC1!",
    "YM": "MYM1!", "YM=F": "MYM1!",
    "RTY": "M2K1!", "RTY=F": "M2K1!",
    # TradingView prefixed forms
    "CME_MINI:MNQ1!": "MNQ1!",
    "CME_MINI:MES1!": "MES1!",
    "NYMEX:MCL1!": "MCL1!",
    "COMEX:MGC1!": "MGC1!",
    "CBOT_MINI:MYM1!": "MYM1!",
    "CME_MINI:M2K1!": "M2K1!",
    "CME_MINI:M6A1!": "M6A1!",
    "CME_MINI:M6E1!": "M6E1!",
    "CME:MBT1!": "MBT1!",
    "CME:MET1!": "MET1!",
    # Crypto
    "BTCUSD": "MBT1!",
    "ETHUSD": "MET1!",
    "BTC-USD": "MBT1!",
    "ETH-USD": "MET1!",
    "XAUUSD": "MGC1!",
    "XAGUSD": "XAGUSD",
}

# ----- Yahoo Finance Symbol Map (Any alias -> Yahoo Finance symbol) -----
YFINANCE_SYMBOL_MAP = {
    # Crypto
    "BTC": "BTC-USD", "BTCUSD": "BTC-USD", "BTCUSDT": "BTC-USD", "XBT": "BTC-USD", "XBTUSD": "BTC-USD",
    "ETH": "ETH-USD", "ETHUSD": "ETH-USD", "ETHUSDT": "ETH-USD",
    "SOL": "SOL-USD", "SOLUSD": "SOL-USD", "SOLUSDT": "SOL-USD",
    "XRP": "XRP-USD", "XRPUSD": "XRP-USD", "XRPUSDT": "XRP-USD",
    # Futures (map to continuous contracts)
    "MNQ1!": "MNQ=F", "MNQ=F": "MNQ=F", "NQM6": "MNQ=F",
    "MES1!": "MES=F", "MES=F": "MES=F", "ESM6": "MES=F",
    "MCL1!": "MCL=F", "MCL=F": "MCL=F", "CL=F": "CL=F", "CL1!": "CL=F",
    "MGC1!": "MGC=F", "MGC=F": "MGC=F", "GC=F": "GC=F", "GC": "GC=F",
    "MYM1!": "MYM=F", "MYM=F": "MYM=F",
    "M2K1!": "RTY=F", "M2K=F": "RTY=F",
    # TradingView prefixed
    "CME_MINI:MNQ1!": "MNQ=F",
    "CME_MINI:MES1!": "MES=F",
    "NYMEX:MCL1!": "MCL=F",
    "COMEX:MGC1!": "MGC=F",
}

# ----- Symbol Bridge Candidates (TradingView alias -> MT5 candidate list) -----
SYMBOL_BRIDGE_CANDIDATES = {
    "CL=F": ("WTI_SB", "Crude_SB", "Crude", "USOIL", "WTI", "XTIUSD", "OIL", "CL"),
    "MCL=F": ("Crude_SB", "WTI_SB", "Crude", "USOIL", "WTI", "MCL"),
    "CL1!": ("WTI_SB", "Crude_SB", "Crude", "USOIL", "WTI", "XTIUSD", "OIL", "CL"),
    "MCL1!": ("Crude_SB", "WTI_SB", "Crude", "USOIL", "WTI", "MCL"),
    "GC=F": ("XAUUSD_SB", "Gold_SB", "XAUUSD", "Gold", "XAU", "MGC", "GC"),
    "MGC": ("XAUUSD_SB", "XAUUSD", "Gold_SB", "Gold", "XAU", "MGC"),
    "MGC1!": ("XAUUSD_SB", "Gold_SB", "XAUUSD", "Gold", "XAU", "MGC"),
    "MNQ1!": ("NAS100_SB", "NAS100", "MNQ"),
    "MES1!": ("US500_SB", "US500", "MES"),
    "MYM1!": ("DJ30_SB", "DJ30", "MYM"),
    "M2K1!": ("RUS2000_SB", "RUS2000", "M2K"),
    "NQM6": ("NQM6", "NAS100_SB", "NAS100", "MNQ"),
    "ESM6": ("ESM6", "US500_SB", "US500", "MES"),
}

# ----- Symbol Fuzzy Terms (TradingView alias -> Fuzzy search terms) -----
SYMBOL_FUZZY_TERMS = {
    "CL=F": ("CL", "WTI", "Crude", "Oil"),
    "MCL=F": ("CL", "WTI", "Crude", "Oil"),
    "CL1!": ("CL", "WTI", "Crude", "Oil"),
    "MCL1!": ("CL", "WTI", "Crude", "Oil"),
    "GC=F": ("GC", "MGC", "XAU", "GOLD", "Gold"),
    "MGC": ("MGC", "XAU", "GOLD", "Gold"),
    "MGC1!": ("MGC", "XAU", "GOLD", "Gold"),
    "MNQ1!": ("NQ", "NAS", "Nasdaq"),
    "MES1!": ("ES", "SP500", "S&P"),
    "MYM1!": ("YM", "DJ30", "Dow"),
    "M2K1!": ("RTY", "RUS", "Russell"),
    "NQM6": ("NQ", "NAS", "Nasdaq"),
    "ESM6": ("ES", "SP500", "S&P"),
}

# ===== ACTIVE WATCHLIST =====
ACTIVE_WATCHLIST = [
    "CME_MINI:MNQ1!",   # Nasdaq Futures Index
    "CME_MINI:MES1!",   # S&P 500 Futures Index
    "CL=F",             # Continuous West Texas Crude Oil Index
    "GC=F"              # Continuous Comex Spot Gold Index
]

# ===== MULTI-ASSET HUNTER =====
MULTI_ASSET_TICKERS = ["MNQ1!", "MES1!", "CL=F", "GC=F"]
MULTI_ASSET_CYCLE_SECONDS = int(os.getenv("MULTI_ASSET_CYCLE_SECONDS", "15"))

# ===== EXECUTION MODE SWITCH =====
# "TV_DESKTOP" = Connect to TradingView Desktop via CDP on port 9222 (ghost JS injection)
# "MT5" = Send orders to MetaTrader 5 via mt5.order_send()
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "TV_DESKTOP").upper().strip()
TRADING_SURFACE = os.getenv("TRADING_SURFACE", "TRADINGVIEW_DESKTOP")

# ACTIVE EXECUTION SURFACE — runtime switchable between TradingView RPA and MT5
# "TRADINGVIEW" = Physical mouse clicks on TradingView web/paper interface
# "MT5" = Native MetaTrader 5 order execution
ACTIVE_EXECUTION_SURFACE = os.getenv("ACTIVE_EXECUTION_SURFACE", "TRADINGVIEW").upper().strip()

# TradingView account label to verify before clicking (e.g., "Paper Trading", "Live")
TRADINGVIEW_ACCOUNT_LABEL = os.getenv("TRADINGVIEW_ACCOUNT_LABEL", "Paper Trading").strip()
MT5_VOLUME = float(os.getenv("MT5_VOLUME", "0.1"))

# ===== SYNCHRONIZED CDP URL =====
BROWSER_CDP_URL = os.getenv("BROWSER_CDP_URL", "http://127.0.0.1:9222").strip()
TV_DESKTOP_CDP_URL = os.getenv("TV_DESKTOP_CDP_URL", "http://127.0.0.1:9222").strip()
TV_DESKTOP_CONNECT_TIMEOUT = int(os.getenv("TV_DESKTOP_CONNECT_TIMEOUT", "15"))

# ===== RPA EXECUTION =====
USE_HOTKEYS = True
HOTKEY_BUY = "<ctrl>+b"
HOTKEY_SELL = "<ctrl>+s"
HOTKEY_CLOSE = "<ctrl>+x"
HUMAN_LATENCY = os.getenv("HUMAN_LATENCY", "True").lower() == "true"

# Safe fallback coordinates for TradingView RPA button clicks
TRADINGVIEW_FALLBACK_COORDS = {
    "buy_button": (int(os.getenv("TRADINGVIEW_BUY_X", "960")), int(os.getenv("TRADINGVIEW_BUY_Y", "540"))),
    "sell_button": (int(os.getenv("TRADINGVIEW_SELL_X", "960")), int(os.getenv("TRADINGVIEW_SELL_Y", "580"))),
    "flatten_button": (int(os.getenv("TRADINGVIEW_FLATTEN_X", "960")), int(os.getenv("TRADINGVIEW_FLATTEN_Y", "620"))),
}
FALLBACK_COORDS = TRADINGVIEW_FALLBACK_COORDS

# ===== LLM CONFIGURATION =====
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_V1_URL = os.getenv("OLLAMA_V1_URL", "http://127.0.0.1:11434")
MICRO_BRAIN_ENABLED = os.getenv("MICRO_BRAIN_ENABLED", "true").lower() == "true"
MICRO_BRAIN_MODEL = os.getenv("MICRO_BRAIN_MODEL", "qwen2.5:1.5b-instruct-q4_K_M")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", MICRO_BRAIN_MODEL if MICRO_BRAIN_ENABLED else "qwen2.5:1.5b-instruct-q4_K_M")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "")
OLLAMA_TIMEOUT = 180
JSON_OUTPUT = True

# ===== GEMINI LIVE BRAIN =====
GEMINI_ENABLED = os.getenv("GEMINI_ENABLED", "True").lower() == "true"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", ""))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT", "20"))

# ===== LOGGING =====
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = "vcani_trade.log"

# ===== UNIFIED MODE HELPER =====
def get_active_mode() -> str:
    """Single source of truth for execution mode."""
    surface = str(globals().get("ACTIVE_EXECUTION_SURFACE", "") or "").upper().strip()
    if surface in ("TRADINGVIEW", "MT5"):
        return surface
    exec_mode = str(globals().get("EXECUTION_MODE", "") or "").upper().strip()
    if exec_mode == "MT5":
        return "MT5"
    return "TRADINGVIEW"