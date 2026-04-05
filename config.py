"""
VcaniTrade AI - Configuration
Safety-first trading configuration with strict defaults
"""

# ===== SAFETY CONTROLS (ALWAYS ON BY DEFAULT) =====
DRY_RUN = True  # Paper trading only - NEVER changes to False without explicit user action
MAX_DAILY_LOSS = 100.00  # Maximum loss per day in account currency
MAX_OPEN_POSITIONS = 3  # Maximum concurrent trades
COOLDOWN_AFTER_STOP = 300  # Seconds to wait after hitting stop loss (5 min)
KILL_SWITCH = False  # Emergency stop - halts all trading immediately

# ===== TRADING MODE =====
# TEACHER_MODE: Show signals overlay but don't execute (manual trading)
# AUTO_MODE: AI executes trades automatically (requires DRY_RUN=False)
TEACHER_MODE = True  # Default to teacher mode for safety

# ===== LLM CONFIGURATION =====
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "llama3"
LLM_TIMEOUT = 10  # Seconds before LLM request times out
JSON_OUTPUT = True  # Force strict JSON output from LLM

# ===== MARKET DATA =====
SCAN_INTERVAL = 2  # Seconds between market scans
ASSETS = ["EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]  # Assets to monitor

# ===== RPA EXECUTION =====
USE_HOTKEYS = True  # Prefer keyboard hotkeys over mouse clicks
HOTKEY_BUY = "<ctrl>+b"  # Buy order hotkey
HOTKEY_SELL = "<ctrl>+s"  # Sell order hotkey
HOTKEY_CLOSE = "<ctrl>+x"  # Close position hotkey

# ===== UI CONFIGURATION =====
OVERLAY_ALPHA = 0.15  # Transparency level for HUD overlay (0.0-1.0)
OVERLAY_UPDATE_MS = 2000  # Overlay refresh rate in milliseconds
SHOW_REASONING = True  # Display AI reasoning in overlay

# ===== LOGGING =====
LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR
LOG_FILE = "vcani_trade.log"
