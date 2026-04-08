"""
VcaniTrade AI - Configuration
Safety-first trading configuration with strict defaults
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

# ===== SAFETY CONTROLS (ALWAYS ON BY DEFAULT) =====
DRY_RUN = (
    os.getenv("DRY_RUN", "True").lower() == "true"
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
    os.getenv("TEACHER_MODE", "True").lower() == "true"
)  # Default to teacher mode for safety

# ===== LLM CONFIGURATION =====
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:1.5b")
LLM_TIMEOUT = 90  # Seconds before LLM request times out
JSON_OUTPUT = True  # Force strict JSON output from LLM

# ===== VISION / VLM CONFIGURATION =====
# Optimized for RTX 4050 Laptop GPU (6GB VRAM)
# Primary: moondream (~1.5GB VRAM, fastest)
# Fallback: llava:7b-v1.5-q4_K_M (~4GB VRAM)
VLM_MODEL = os.getenv("VLM_MODEL", "moondream")
VISION_TIMEOUT = 120  # Max seconds before graceful degradation to text-only
USE_VISION = (
    os.getenv("USE_VISION", "True").lower() == "true"
)  # Enable chart screenshot analysis
CHART_REGION_X = int(os.getenv("CHART_REGION_X", "100"))
CHART_REGION_Y = int(os.getenv("CHART_REGION_Y", "100"))
CHART_REGION_W = int(os.getenv("CHART_REGION_W", "1280"))
CHART_REGION_H = int(os.getenv("CHART_REGION_H", "720"))
SAVE_DEBUG_SCREENSHOTS = os.getenv("SAVE_DEBUG_SCREENSHOTS", "False").lower() == "true"

# ===== MARKET DATA =====
SCAN_INTERVAL = 2  # Seconds between market scans
ASSETS = ["EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]  # Assets to monitor

# ===== RPA EXECUTION =====
USE_HOTKEYS = True  # Prefer keyboard hotkeys over mouse clicks
HOTKEY_BUY = "<ctrl>+b"  # Buy order hotkey
HOTKEY_SELL = "<ctrl>+s"  # Sell order hotkey
HOTKEY_CLOSE = "<ctrl>+x"  # Close position hotkey

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
