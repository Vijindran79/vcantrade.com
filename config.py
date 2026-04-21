"""
VcaniTrade AI - Configuration
Safety-first trading configuration with strict defaults
"""

# --- LION MODE CONFIGURATION ---
MAX_DAILY_TRADES = 30          # Hard cap to prevent overtrading
RE_ENTRY_LOCKOUT_MINUTES = 5   # Cooldown after a trade closes
TRAILING_STOP_CANDLE_MIN = 3   # Use 3-min candles for smoother stops
RSI_VETO_THRESHOLD = 80        # Abort if RSI > 80 (Buy) or < 20 (Sell)
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

# ===== SAFETY CONTROLS (ALWAYS ON BY DEFAULT) =====
DRY_RUN = os.getenv("DRY_RUN", "False").lower() == "true"
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", "100.00"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
COOLDOWN_AFTER_STOP = int(os.getenv("COOLDOWN_AFTER_STOP", "300"))
KILL_SWITCH = False

# ===== TRADING MODE =====
TEACHER_MODE = os.getenv("TEACHER_MODE", "False").lower() == "true"

# ===== LLM CONFIGURATION (Local Ollama + Qwen 2.5) =====
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:latest")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "ollama")
VAST_API_TOKEN = None

# ===== GEMINI LIVE BRAIN =====
GEMINI_ENABLED = os.getenv("GEMINI_ENABLED", "True").lower() == "true"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", ""))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT", "20"))


# ===== EXTERNAL BRAIN PROVIDERS =====
# Primary OpenRouter Key (Using your main account)
OPENROUTER_API_KEY = "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"

# Backup OpenRouter Keys (The bot will rotate if one fails)
OPENROUTER_API_KEYS = "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,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"

# Groq Keys (For ultra-fast secondary analysis)
GROQ_API_KEYS = "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,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,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"

# Nvidia and Brainstorm (The "Deep Research" layers)
NVIDIA_API_KEY = "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"
BRAINSTORM_API_KEY = "sk-8226971a2ecd43adb234d88b2e102597"

# Local execution settings
LLM_TIMEOUT = 90
OLLAMA_TIMEOUT = LLM_TIMEOUT
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

# Technical Signal Thresholds
VOLUME_SPIKE_MULTIPLIER = 3.0
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
SMA_FAST = 20
SMA_SLOW = 50
SWARM_CONFIDENCE_THRESHOLD = 0.50
MIN_CONFIDENCE_THRESHOLD = 50.0

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

# ===== SLIPPAGE GUARD =====
MAX_SLIPPAGE_PERCENT = float(os.getenv("MAX_SLIPPAGE_PERCENT", "2.50"))
MAX_SPREAD_PERCENT = float(os.getenv("MAX_SPREAD_PERCENT", "0.30"))

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
