"""
VcanTrade AI - Main Application (Hybrid Architecture)

Hybrid Data-Driven System:
- Cloud Scanner (Vast.ai): Monitors 10 tickers using yfinance, triggers Swarm Debate
- Signal Dispatch: High-confidence signals (>0.70) sent to Local Executor
- Local Executor (Laptop): Receives signals, performs Vision Confirmation, executes via RPA

Architecture:
- All heavy work runs in QThreads (never blocks the GUI)
- Backend threads emit signals -> CommandCenter updates on main thread
- Signal Dispatcher runs as async HTTP server in background thread
- Vision Engine captures screenshots in AnalysisWorker thread
- Watchtower runs independently, feeds anomalies to Swarm
"""

# Fix Windows terminal encoding issues - runs before ANY other import
import sys
import os
import io
import faulthandler

if sys.platform == 'win32':
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    # Qt can hard-crash on some Windows desktop/RDP/admin DPI transitions.
    # Set these before importing PyQt so the UI uses the most stable path.
    os.environ.setdefault('QT_QPA_PLATFORM', 'windows:dpiawareness=0')
    os.environ.setdefault('QT_OPENGL', 'software')
    os.environ.setdefault('QT_QUICK_BACKEND', 'software')
    try:
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding='ascii', errors='ignore', line_buffering=True
        )
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding='ascii', errors='ignore', line_buffering=True
        )
    except AttributeError:
        pass

try:
    _startup_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "startup_log.txt")
    _startup_crash_log = open(_startup_log_path, "a", encoding="utf-8", buffering=1)
    faulthandler.enable(file=_startup_crash_log, all_threads=True)
except Exception:
    pass

import signal
import socket
import time
import random
import asyncio
import logging
import threading
import queue
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Optional
from playwright.async_api import async_playwright  # Async Playwright only - no sync mix

# Trade Alert Sound — Windows built-in, no extra dependencies
try:
    import winsound
    _ALERT_SOUND_AVAILABLE = True
except ImportError:
    _ALERT_SOUND_AVAILABLE = False  # Non-Windows platforms

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, QThread, pyqtSignal, QObject, Qt
from PyQt6.QtGui import QShortcut, QKeySequence

import config
from core.models import (
    MarketDataPoint,
    OverlaySignal,
    TradeRecord,
    SignalAction,
    ConfidenceLevel,
    DebateTranscript,
    WatchlistAlert,
)
from core.llm_analyzer import LLMAnalyzer
from core.trade_engine import TradeEngine
from core.grader import Grader
# STAGE 2: AI Strategist & Dynamic Architect
from core.code_architect import CodeArchitect
from core.atr_stops import LooseATRStops
from core.visual_confirmation import VisualChartConfirmation
# STAGE 3: Institutional Governor & Risk Architect
from core.risk_governor import RiskGovernor
from core.sentiment_pulse import SentimentPulse
from core.profit_lock import ProfitLock
# STAGE 4: Meta-Cognition & Alpha Hunter
from core.meta_analyzer import MetaAnalyzer, TradeJournal
from core.watchtower import WatchtowerScanner
from core.vision_engine import VisionCapture
from core.scanner import CloudScanner
from services.signal_dispatcher import SignalDispatcher
from core.surface_router import (
    SurfaceRouter,
    SURFACE_TRADINGVIEW,
    SURFACE_MT5,
    get_active_surface,
    set_active_surface,
)
from core.browser_agent import BrowserAgent
from core.settings import settings_manager
from core.financial_safety import FinancialSafetyManager
from core.executor import UnifiedTradeExecutor, ExchangeLimitExecutor, ExchangeInterface, SlippageGuard
from core.risk import calculate_position_size, build_hard_stop_plan
from core.journal import TradeJournalDB
from core.risk_manager import RiskManager, PositionSizer
from core.symbol_mapper import normalize_yfinance_symbol, root_matches
from core.vibe_adapter import VibeTradingAdapter
from execution.rpa_executor import RPAExecutor
from core.ghost_executor import GhostExecutor
from core.hybrid_execution_gateway import HybridExecutionGateway
from core.trade_executor import TradeExecutor
from core.market_sessions import MarketSessionDetector
from ui.dashboard import CommandCenter
from ui.signal_dialog import SignalApprovalDialog
from ui.ai_narrator import AINarratorOverlay
from ui.calibration_dialog import CalibrationWizardDialog
from ui.lion_switchboard import choose_launch_profile
from ui.vision_dialog import VisionConfirmationDialog, VisionTestDialog

# Setup logging with UTF-8 encoding to prevent emoji crashes
# Clear any existing handlers and set up fresh
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)


class _SafeStreamHandler(logging.StreamHandler):
    """StreamHandler that NEVER raises UnicodeEncodeError.

    Strategy (belt-and-suspenders):
    1. Strip every non-ASCII character from the formatted message *before*
       writing, so the underlying stream (even cp1252) never sees an emoji.
    2. If a write still fails for any reason, encode with errors='ignore'
       and decode back to a plain ASCII string as a final fallback.
    3. Always flush after each record so logs appear immediately.
    """

    # Matches any character outside the 7-bit ASCII range (includes all emojis).
    _NON_ASCII = re.compile(r'[^\x00-\x7F]+')

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            # Step 1: strip all non-ASCII characters.
            msg = self._NON_ASCII.sub('', msg)
            stream = self.stream
            try:
                stream.write(msg + self.terminator)
            except UnicodeEncodeError:
                # Step 2: final fallback - encode as ASCII ignoring errors.
                safe = msg.encode('ascii', errors='ignore').decode('ascii')
                stream.write(safe + self.terminator)
            self.flush()
        except RecursionError:
            raise
        except Exception:
            self.handleError(record)


logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding='utf-8', mode='a'),
        _SafeStreamHandler(sys.stdout),
    ],
    force=True  # Force reconfiguration
)
logger = logging.getLogger(__name__)


def _teacher_mode_forced_by_config() -> bool:
    """Return True when env/config explicitly pins the app to Teacher mode."""
    if bool(getattr(config, "TEACHER_ONLY_LOCK", False)):
        return True
    execution_mode = str(getattr(config, "EXECUTION_MODE", "") or "").strip().upper()
    trading_surface = str(getattr(config, "TRADING_SURFACE", "") or "").strip().upper()
    runtime_mode = str(os.getenv("RUNTIME_MODE", os.getenv("TRADING_MODE", "")) or "").strip().upper()
    return bool(getattr(config, "TEACHER_MODE", False)) or execution_mode == "TEACHER" or trading_surface == "TEACHER" or runtime_mode == "TEACHER"


def _is_passive_visual_mode() -> bool:
    """Return True when the bot must not capture browser/desktop screenshots."""
    # OVERRIDE: Active TradingView execution needs screenshots for vision analysis
    active_surface = str(getattr(config, "ACTIVE_EXECUTION_SURFACE", "")).upper().strip()
    if active_surface == "TRADINGVIEW":
        return False
    execution_mode = str(getattr(config, "EXECUTION_MODE", "")).upper().strip()
    trading_surface = str(getattr(config, "TRADING_SURFACE", "")).upper().strip()
    return execution_mode in {
        "TV_DESKTOP",
        "TRADOVATE",
    } or trading_surface in {
        "TV_DESKTOP",
        "TRADOVATE",
        "TRADINGVIEW_DESKTOP",
        "TRADINGVIEW_TRADOVATE",
    }


def _play_trade_alert(action: str, success: bool):
    """Play a distinctive alert sound when a trade is executed.
    BUY = rising tone (optimistic). SELL = falling tone (urgent).
    Failed trade = low buzz."""
    if not bool(getattr(config, "PLAY_ALERT_SOUNDS", False)):
        return
    if not _ALERT_SOUND_AVAILABLE:
        return
    try:
        if success:
            if action == "BUY":
                # Rising two-tone: positive, optimistic
                winsound.Beep(800, 200)
                winsound.Beep(1200, 300)
            else:
                # Falling two-tone: urgent, caution
                winsound.Beep(1200, 200)
                winsound.Beep(800, 300)
        else:
            # Low buzz: something went wrong
            winsound.Beep(400, 500)
    except Exception:
        pass  # Sound may fail in sandboxed or remote environments


_last_scan_tick_sound_at = 0.0
_last_spoken_alert_at = 0.0


def _play_ui_alert(kind: str, action: str = "", confidence: float = 0.0):
    """Play non-blocking dashboard/narrator alert sounds."""
    if not bool(getattr(config, "PLAY_ALERT_SOUNDS", False)):
        return
    if not _ALERT_SOUND_AVAILABLE:
        return

    kind = str(kind or "signal").lower()
    if kind == "scan":
        if not bool(getattr(config, "PLAY_SCAN_TICK_SOUNDS", False)):
            return
        global _last_scan_tick_sound_at
        now = time.monotonic()
        interval = float(getattr(config, "SCAN_TICK_SOUND_INTERVAL_SECONDS", 8.0) or 8.0)
        if now - _last_scan_tick_sound_at < interval:
            return
        _last_scan_tick_sound_at = now

    action = str(action or "").upper()
    if kind == "gatekeeper":
        pattern = [(420, 140), (360, 170), (300, 260)]
    elif kind == "error":
        pattern = [(320, 280), (320, 280)]
    elif kind == "scan":
        pattern = [(640, 55)]
    elif action == "SELL":
        pattern = [(1450, 110), (1050, 140), (720, 190)]
    elif action == "BUY":
        pattern = [(720, 110), (1050, 140), (1450, 190)]
    else:
        pattern = [(880, 110), (1180, 140), (980, 110)]

    def _runner():
        try:
            for frequency, duration in pattern:
                winsound.Beep(int(frequency), int(duration))
                time.sleep(0.03)
        except Exception:
            pass

    threading.Thread(target=_runner, daemon=True).start()


def _speak_alert(message: str, min_interval_seconds: float = 3.0):
    """Use Windows speech synthesis for important alerts when enabled."""
    if not bool(getattr(config, "ENABLE_AUDIO_NARRATION", False)):
        return
    if sys.platform != "win32":
        return

    global _last_spoken_alert_at
    now = time.monotonic()
    if now - _last_spoken_alert_at < min_interval_seconds:
        return
    _last_spoken_alert_at = now

    text = re.sub(r"[^A-Za-z0-9 .,:;%$\\-]", " ", str(message or "")).strip()
    if not text:
        return
    text = text[:180]

    def _runner():
        try:
            import subprocess

            env = os.environ.copy()
            env["VCAN_SPEAK_TEXT"] = text
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        "Add-Type -AssemblyName System.Speech; "
                        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                        "$s.Rate = 1; "
                        "$s.Speak($env:VCAN_SPEAK_TEXT)"
                    ),
                ],
                env=env,
                capture_output=True,
                timeout=6,
                creationflags=flags,
            )
        except Exception:
            pass

    threading.Thread(target=_runner, daemon=True).start()
logger.info("Logging system initialized")

VISION_ANALYSIS_COOLDOWN_SECONDS = 3.0
_vision_analysis_lock = threading.Lock()
_last_vision_analysis_at = 0.0


def _wait_for_vision_analysis_slot(label: str = "vision") -> None:
    """Throttle vision/AI image requests so browser and CPU stay responsive."""
    global _last_vision_analysis_at
    with _vision_analysis_lock:
        now = time.monotonic()
        wait_time = VISION_ANALYSIS_COOLDOWN_SECONDS - (now - _last_vision_analysis_at)
        if wait_time > 0:
            logger.info("[VISION] Cooldown %.2fs before %s analysis", wait_time, label)
            time.sleep(wait_time)
        _last_vision_analysis_at = time.monotonic()


def _create_mt5_executor():
    """Lazy factory so UI mode can boot without an MT5 dependency chain."""
    try:
        from core.mt5_executor import MT5Executor

        return MT5Executor()
    except Exception as exc:
        logging.getLogger(__name__).warning("[MT5] Executor unavailable: %s", exc)
        return None

# Cloud Dashboard: Capture last log message for remote bridge broadcast
_last_log_message = ""


class DashboardLogCapture(logging.Handler):
    """Captures the most recent log record for remote dashboard display."""

    def emit(self, record: logging.LogRecord) -> None:
        global _last_log_message
        try:
            _last_log_message = self.format(record)
        except Exception:
            pass


_dashboard_handler = DashboardLogCapture()
_dashboard_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)
_dashboard_handler.setLevel(logging.INFO)
logging.getLogger().addHandler(_dashboard_handler)


def _acquire_single_instance_lock() -> bool:
    """Prevent multiple dashboard instances from running simultaneously."""
    global _instance_lock_socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 47654))
        sock.listen(1)
        _instance_lock_socket = sock
        return True
    except OSError:
        return False


def _release_single_instance_lock() -> None:
    """Release single-instance lock socket."""
    global _instance_lock_socket
    if _instance_lock_socket:
        try:
            _instance_lock_socket.close()
        except Exception:
            pass
        _instance_lock_socket = None


from services.signal_dispatcher import SignalDispatcher
def is_whitelisted_futures(ticker: str) -> bool:
    """Check if ticker is in the Futures whitelist (MCLM6, NQM6, ESM6, MGC)."""
    if not ticker:
        return False
    ticker_up = ticker.upper().strip()
    return ticker_up in [f.upper() for f in config.FUTURES_WHITELIST]


def is_blocked_stock(ticker: str) -> bool:
    """Check if ticker is a blocked stock (TSLA, AAPL, SPX, etc.)."""
    if not ticker:
        return False
    ticker_up = ticker.upper().strip()
    return ticker_up in [s.upper() for s in config.BLOCKED_STOCKS]


import pyautogui


class MainThreadInvoker(QObject):
    """Execute callables on the Qt main thread via signal dispatch."""

    call_requested = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.call_requested.connect(self._invoke)

    def _invoke(self, callback):
        try:
            callback()
        except Exception:
            logging.getLogger(__name__).exception("MainThreadInvoker callback failed")

    def submit(self, callback):
        self.call_requested.emit(callback)

# ---------------------------------------------------------------------------
# Data Scout Listener - Listens for Vast.ai signals on port 5000
# ---------------------------------------------------------------------------

class DataScoutListenerThread(QThread):
    """
    Listens for POST requests on port 5000 from Vast.ai server.
    Triggers immediate TradingView symbol flip via keyboard.
    """
    signal_received = pyqtSignal(str)  # Emits the symbol

    def __init__(self):
        super().__init__()
        self.running = True
        self.loop = None  # Store event loop reference

    def run(self):
        logger.info("Data Scout Listener started on port 5000")
        try:
            # Create new event loop for this thread
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._run_server())
        except asyncio.CancelledError:
            logger.info("Data Scout Listener stopped")
        except Exception as e:
            logger.error(f"Data Scout Listener error: {e}")
        finally:
            if self.loop and not self.loop.is_closed():
                self.loop.close()

    async def _run_server(self):
        from aiohttp import web

        async def handle_signal(request):
            try:
                data = await request.json()
                # Expecting: {"symbol": "TICKER", "action": "BUY/SELL", "confidence": 0.85}
                if data.get("symbol"):
                    symbol = data.get("symbol")
                    action = data.get("action", "SIGNAL")
                    confidence = data.get("confidence", 0.0)

                    print(f"[FIRE] CLOUD SIGNAL RECEIVED FOR {symbol}")
                    self.signal_received.emit(symbol)
                    return web.Response(text="Signal processed", status=200)
                return web.Response(text="Invalid data", status=400)
            except Exception as e:
                return web.Response(text=str(e), status=500)

        app = web.Application()
        app.router.add_post("/signal", handle_signal)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", 5000)
        await site.start()

        logger.info("Data Scout Listener HTTP server running")

        while self.running:
            await asyncio.sleep(1)

        logger.info("Data Scout Listener shutting down...")
        await runner.cleanup()

    def stop(self):
        self.running = False
        if self.loop and not self.loop.is_closed():
            # Schedule task cancellation on the event loop's own thread
            self.loop.call_soon_threadsafe(self._cancel_all_tasks)

    def _cancel_all_tasks(self):
        """Cancel all tasks from within the event loop thread."""
        try:
            for task in asyncio.all_tasks(self.loop):
                task.cancel()
        except Exception:
            pass


class CloudScannerThread(QThread):
    """
    PREDATOR-CLASS: Background thread that runs the Cloud Scanner on Vast.ai server.
    Monitors 10 tickers using yfinance and triggers Swarm Debate.
    
    WATCHDOG HEARTBEAT: If scanner stops for >10 seconds, force-reinitialize connection.
    """

    signal_detected = pyqtSignal(object)  # Emits trade signal data
    technical_signal_detected = pyqtSignal(object)  # Emits raw scanner opportunities before swarm dispatch
    scanner_error = pyqtSignal(str)  # Emits error message
    ticker_status = pyqtSignal(str, str)  # Emits per-ticker status updates
    heartbeat_pulse = pyqtSignal(bool)  # Emits True when heartbeat is healthy

    def __init__(self):
        super().__init__()
        self.running = True
        self.scanner = CloudScanner()
        self.scanner.status_callback = self._emit_ticker_status
        
        # WATCHDOG HEARTBEAT TRACKING
        self.last_scan_time = time.time()
        # Allow room for a full scan plus local LLM analysis before declaring the scanner stale.
        self.heartbeat_timeout = max(float(getattr(config, "SCAN_INTERVAL", 10)) * 6.0, 60.0)
        self.consecutive_failures = 0
        self.max_failures_before_reinit = 3

    def _emit_ticker_status(self, ticker: str, status: str):
        self.ticker_status.emit(ticker, status)

    def _technical_signal_payload(self, signal) -> dict:
        """Convert a scanner TechnicalSignal into a UI-friendly opportunity payload."""
        signal_type = str(getattr(signal, "signal_type", "SIGNAL") or "SIGNAL").upper()
        strength = float(getattr(signal, "strength", 0.0) or 0.0)
        metadata = getattr(signal, "metadata", {}) or {}
        if "BUY" in signal_type or "BULLISH" in signal_type:
            action = "BUY"
        elif "SELL" in signal_type or "BEARISH" in signal_type:
            action = "SELL"
        elif "OVERSOLD" in signal_type:
            action = "BUY"
        elif "OVERBOUGHT" in signal_type:
            action = "SELL"
        else:
            action = str(
                metadata.get("action_hint")
                or metadata.get("liquidity_bias")
                or metadata.get("direction")
                or metadata.get("action")
                or "SIGNAL"
            ).upper()
            if action == "UP":
                action = "BUY"
            elif action == "DOWN":
                action = "SELL"
        return {
            "ticker": str(getattr(signal, "ticker", "UNKNOWN") or "UNKNOWN"),
            "action": action,
            "signal_type": signal_type,
            "confidence": max(0.0, min(1.0, strength)),
            "metadata": metadata,
        }

    def run(self):
        logger.info("=" * 60)
        logger.info("[CLOUD] CLOUD SCANNER THREAD STARTED")
        logger.info(f"   Monitoring {len(self.scanner.tickers)} tickers")
        logger.info(f"   Target: {config.CLOUD_SCANNER_URL}/api/signal")
        logger.info("=" * 60)
        
        try:
            # Run async scanner in event loop
            asyncio.run(self._run_scanner())
        except Exception as e:
            error_msg = f"Cloud Scanner error: {e}"
            self.scanner_error.emit(error_msg)
            logger.error(error_msg)

    async def _run_scanner(self):
        """
        PREDATOR-CLASS: Run the cloud scanner loop with WATCHDOG HEARTBEAT monitoring.
        
        WATCHDOG LOGIC:
        - Track last successful scan time
        - If no scan for >10 seconds, force-reinitialize connection
        - Emit heartbeat pulse for UI monitoring
        """
        while self.running:
            try:
                # WATCHDOG: Check if we've exceeded heartbeat timeout
                elapsed = time.time() - self.last_scan_time
                if elapsed > self.heartbeat_timeout and self.last_scan_time > 0:
                    logger.warning(
                        f"[DOG] WATCHDOG: Scanner idle for {elapsed:.1f}s "
                        f"(>{self.heartbeat_timeout:.0f}s threshold). "
                        f"Forcing reinitialization..."
                    )
                    self.consecutive_failures += 1
                    
                    if self.consecutive_failures >= self.max_failures_before_reinit:
                        logger.critical(
                            f"[DOG] WATCHDOG: {self.consecutive_failures} consecutive failures. "
                            f"Reinitializing scanner connection..."
                        )
                        try:
                            # Force scanner reinitialization
                            self.scanner = CloudScanner()
                            self.scanner.status_callback = self._emit_ticker_status
                            self.consecutive_failures = 0
                            logger.info("[DOG] WATCHDOG: Scanner reinitialized successfully")
                        except Exception as reinit_err:
                            logger.error(f"[DOG] WATCHDOG: Reinitialization failed: {reinit_err}")
                    
                    # Emit unhealthy heartbeat
                    self.heartbeat_pulse.emit(False)
                
                # Scan all tickers
                signals = await self.scanner.scan_all_tickers()

                for signal in signals or []:
                    self.technical_signal_detected.emit(self._technical_signal_payload(signal))
                
                # Update heartbeat timestamp on successful market sweep
                self.last_scan_time = time.time()
                self.consecutive_failures = 0
                
                # Emit healthy heartbeat
                self.heartbeat_pulse.emit(True)

                # Process through Swarm
                if signals:
                    trade_signal = await self.scanner.process_signals(signals)
                    self.last_scan_time = time.time()

                    if trade_signal:
                        # Dispatch to local executor
                        success = await self.scanner.dispatch_to_local(trade_signal)
                        self.last_scan_time = time.time()

                        if success:
                            self.signal_detected.emit(trade_signal)
                            logger.info(f"Signal dispatched: {trade_signal}")
                        else:
                            streak = int(getattr(self.scanner, "dispatch_failure_streak", 0) or 0)
                            if streak >= 3 and not bool(getattr(self.scanner, "dispatch_alert_emitted", False)):
                                dispatch_error = str(
                                    getattr(self.scanner, "last_dispatch_error_message", "") or "Signal dispatch failed"
                                )
                                self.scanner_error.emit(
                                    f"Signal dispatch failed after {streak} consecutive attempts: {dispatch_error}"
                                )
                                self.scanner.dispatch_alert_emitted = True

                # Mark the loop healthy before sleeping between scans.
                self.last_scan_time = time.time()

                # Wait before next scan
                await asyncio.sleep(config.SCAN_INTERVAL)

            except asyncio.CancelledError:
                logger.info("Cloud Scanner task cancelled")
                break
            except Exception as e:
                error_msg = f"Scan error: {type(e).__name__}: {e}"
                self.scanner_error.emit(error_msg)
                logger.error(f"[CLOUD] SCANNER ERROR: {error_msg}")
                self.consecutive_failures += 1
                await asyncio.sleep(5)  # Wait before retry

    def stop(self):
        self.running = False


class SignalListenerThread(QThread):
    """
    Background thread that listens for incoming signals from Cloud Scanner.
    Runs HTTP server on local laptop to receive trade signals.
    """

    signal_received = pyqtSignal(object)  # Emits received signal data
    handshake_received = pyqtSignal(object)  # Emits handshake metadata
    listener_error = pyqtSignal(str)  # Emits error message

    def __init__(self):
        super().__init__()
        self.running = True
        self.dispatcher = SignalDispatcher()

    def run(self):
        logger.info(
            "Signal Listener started on %s:%s",
            config.LOCAL_LISTENER_HOST,
            config.LOCAL_LISTENER_PORT,
        )
        try:
            # Set callback
            self.dispatcher.set_signal_callback(self._on_signal_received)
            self.dispatcher.set_handshake_callback(self._on_handshake_received)

            # Run async HTTP server
            asyncio.run(self._run_server())
        except Exception as e:
            self.listener_error.emit(f"Signal Listener error: {e}")
            logger.error(f"Signal Listener thread error: {e}")

    async def _run_server(self):
        """Run the HTTP server loop."""
        try:
            runner = await self.dispatcher.start_server()
            logger.info(
                "[OK] Signal Dispatcher listening on %s:%s",
                config.LOCAL_LISTENER_HOST,
                config.LOCAL_LISTENER_PORT,
            )
            if config.PUBLIC_SIGNAL_URL:
                logger.info("[GLOBE] Public signal URL armed: %s", config.PUBLIC_SIGNAL_URL)
            logger.info(
                "[LOCK] Signal listener auth: %s",
                "ENABLED" if config.SIGNAL_API_KEY else "DISABLED",
            )
            
            # Verify server is accessible
            from aiohttp import ClientSession
            try:
                health_url = f"http://{config.LOCAL_LISTENER_HEALTH_HOST}:{config.LOCAL_LISTENER_PORT}/api/health"
                async with ClientSession() as session:
                    async with session.get(health_url, timeout=3) as response:
                        if response.status == 200:
                            logger.info("[OK] Signal Dispatcher health check passed")
                        else:
                            logger.warning(f"[WARN] Signal Dispatcher health check returned {response.status}")
            except Exception as e:
                logger.error(f"[WARN] Signal Dispatcher health check failed: {e}")
            
            try:
                # Keep running until stopped
                while self.running:
                    await asyncio.sleep(1)
            finally:
                await runner.cleanup()
                logger.info("Signal Dispatcher server stopped")
        except Exception as e:
            error_msg = f"Signal Dispatcher failed to start: {e}"
            self.listener_error.emit(error_msg)
            logger.error(error_msg)
            raise

    def _on_signal_received(self, signal_data: dict):
        """Handle incoming signal from cloud."""
        self.signal_received.emit(signal_data)
        logger.info(f"Signal received from cloud: {signal_data}")

    def _on_handshake_received(self, handshake_data: dict):
        """Handle authenticated bridge handshake."""
        self.handshake_received.emit(handshake_data)
        logger.info(f"Handshake received from external brain: {handshake_data}")

    def stop(self):
        self.running = False


class CloudBridgeThread(QThread):
    """
    Remote dashboard bridge for Vast.ai cloud server.
    Serves WebSocket + REST endpoints so a local Windows client
    can monitor P&L, positions, and trigger the kill switch.
    """

    kill_requested = pyqtSignal()  # Marshalled to main Qt thread automatically

    def __init__(self, app, host="0.0.0.0", port=8765):
        super().__init__()
        self.app = app
        self.host = host
        self.port = port
        self.running = True
        self.loop = None
        self._clients = set()

    def run(self):
        logger.info("[BRIDGE] Cloud dashboard bridge thread starting...")
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._run_server())
        except Exception as e:
            logger.error("[BRIDGE] Server loop error: %s", e)
        finally:
            if self.loop and not self.loop.is_closed():
                self.loop.close()

    async def _run_server(self):
        from aiohttp import web, WSMsgType

        @web.middleware
        async def cors_middleware(request, handler):
            response = await handler(request)
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return response

        async def health(request):
            return web.json_response({"status": "ok"})

        async def status(request):
            return web.json_response(self._build_status())

        async def kill(request):
            logger.critical("[BRIDGE] Remote KILL SWITCH received from %s", request.remote)
            self.kill_requested.emit()
            return web.json_response({"status": "kill_signal_sent"})

        async def websocket_handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            self._clients.add(ws)
            logger.info("[BRIDGE] Dashboard client connected: %s", request.remote)
            try:
                async for msg in ws:
                    if msg.type == WSMsgType.TEXT:
                        if msg.data == "ping":
                            await ws.send_str("pong")
                    elif msg.type == WSMsgType.ERROR:
                        break
            except Exception:
                pass
            finally:
                self._clients.discard(ws)
                logger.info("[BRIDGE] Dashboard client disconnected: %s", request.remote)
            return ws

        app = web.Application(middlewares=[cors_middleware])
        app.router.add_get("/api/health", health)
        app.router.add_get("/api/status", status)
        app.router.add_post("/api/kill", kill)
        app.router.add_get("/ws", websocket_handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        logger.info("[BRIDGE] Dashboard server listening on %s:%s", self.host, self.port)

        # Broadcast loop: push status every 5 seconds
        while self.running:
            try:
                data = self._build_status()
                dead = set()
                for ws in self._clients:
                    if ws.closed:
                        dead.add(ws)
                        continue
                    try:
                        await ws.send_json(data)
                    except Exception:
                        dead.add(ws)
                self._clients -= dead
            except Exception as e:
                logger.error("[BRIDGE] Broadcast error: %s", e)
            await asyncio.sleep(5)

        logger.info("[BRIDGE] Dashboard server shutting down...")
        await runner.cleanup()

    def _build_status(self):
        app = self.app
        return {
            "account_id": "APEX-314327-18",
            "current_balance": getattr(app, "balance", 0.0),
            "equity": getattr(app, "equity", 0.0),
            "daily_pnl": getattr(app, "daily_pnl", 0.0),
            "total_pnl": getattr(app, "total_pnl", 0.0),
            "active_positions": len(getattr(app, "positions", [])),
            "positions": [
                {
                    "asset": p.get("asset"),
                    "side": p.get("side"),
                    "entry": p.get("entry"),
                    "current": p.get("current"),
                    "pnl": p.get("pnl"),
                    "pnl_pct": p.get("pnl_pct"),
                }
                for p in getattr(app, "positions", [])
            ],
            "mode": getattr(app, "current_mode", "UNKNOWN"),
            "bridge_status": getattr(app, "bridge_status", "unknown"),
            "trades_today": getattr(app, "trades_today", 0),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "last_log_message": _last_log_message,
        }

    def stop(self):
        self.running = False
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._cancel_all_tasks)

    def _cancel_all_tasks(self):
        try:
            for task in asyncio.all_tasks(self.loop):
                task.cancel()
        except Exception:
            pass


class MultiAssetHunterThread(QThread):
    """
    Cycles through MNQ / ES / Oil every 30 seconds.
    Navigates TradingView, screenshots, sends to Cloud Brain (Ollama v1),
    and emits trade signals when BUY/SELL is detected.
    """

    status_update = pyqtSignal(str, str, str)  # symbol, status, message
    trade_signal = pyqtSignal(str, str, str, int, str)  # symbol, action, reason, confidence, threat
    narrator_update = pyqtSignal(str, str)      # icon, message (thread-safe Activity Feed)

    def __init__(self, app, symbols=None, interval_sec=None):
        super().__init__()
        self.app = app
        self.symbols = symbols or config.MULTI_ASSET_TICKERS
        self.interval_sec = interval_sec or config.MULTI_ASSET_CYCLE_SECONDS
        self.running = True
        self.index = 0
        self._last_focus_log_ts = 0.0

    def _get_ready_browser_agent(self, symbol: str, require_page: bool = True):
        """Return BrowserAgent only when the async startup path is ready."""
        agent = getattr(self.app, "browser_agent", None)
        status = str(getattr(self.app, "browser_agent_status", "unknown") or "unknown")
        if agent is None:
            logger.debug("[HUNTER] Browser agent unavailable for %s (status=%s)", symbol, status)
            self.status_update.emit(symbol, "WAITING", f"Browser agent {status}; retrying next cycle")
            return None
        if require_page and getattr(agent, "page", None) is None:
            logger.debug("[HUNTER] Browser page not ready for %s (status=%s)", symbol, status)
            self.status_update.emit(symbol, "WAITING", f"Browser page not ready ({status})")
            return None
        return agent

    def run(self):
        from core.market_sessions import is_crypto_ticker
        from datetime import timezone

        logger.info("[HUNTER] Multi-Asset Hunter thread started")
        while self.running:
            # RECOMPUTE active symbols EACH cycle based on current UTC time
            now_utc = datetime.now(timezone.utc)
            weekday = now_utc.weekday()
            hour_utc = now_utc.hour

            # Automatic Switchboard Flip:
            # Saturday = weekend mode (crypto only)
            # Sunday before 22:00 UTC = weekend mode (CME closed until ~22:15)
            # Sunday 22:00 UTC+ = normal mode resumes
            is_weekend = (weekday == 5) or (weekday == 6 and hour_utc < 22)

            watchlist = getattr(self.app, "current_watchlist", [])
            if is_weekend:
                # Only crypto on weekends — MNQ/MES/OIL are closed
                active_symbols = [s for s in watchlist if is_crypto_ticker(s)]
                if not active_symbols:
                    active_symbols = ["BTC-USD"]  # Fallback: always scan at least BTC
                logger.debug("[HUNTER] Weekend mode: only crypto: %s", active_symbols)
            else:
                # RESPECT DASHBOARD WATCHLIST: Only scan what the user manually entered.
                # Never auto-add MNQ/MES/MCL — the user controls the watchlist.
                active_symbols = list(watchlist) if watchlist else []  # strict: no fallback, user must set watchlist

            if not active_symbols:
                self.status_update.emit("WATCHLIST", "WAITING", "No active symbols armed")
                time.sleep(min(self.interval_sec, 5))
                continue

            if bool(getattr(config, "SINGLE_TRADE_FOCUS_MODE", True)) and getattr(self.app, "positions", []):
                focus_assets = [
                    str(pos.get("asset", "") or "").strip()
                    for pos in getattr(self.app, "positions", [])
                    if str(pos.get("asset", "") or "").strip()
                ]
                if focus_assets:
                    focus_symbol = focus_assets[0]
                    parked_count = max(0, len(active_symbols) - 1)
                    active_symbols = [focus_symbol]
                    now_ts = time.time()
                    if now_ts - self._last_focus_log_ts >= 60:
                        logger.info(
                            "[FOCUS] Hunter locked on %s; parking %d other symbol(s)",
                            focus_symbol,
                            parked_count,
                        )
                        self.narrator_update.emit(
                            "[FOCUS]",
                            f"Hunter locked on {focus_symbol}; other symbols parked",
                        )
                        self._last_focus_log_ts = now_ts

            # Monday State Re-Sync (Anti-Ghosting) - properly awaited via asyncio
            if not is_weekend and not getattr(self.app, "_monday_resync_done", False):
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self._perform_monday_resync())
                    loop.close()
                except Exception as resync_err:
                    logger.warning("[RESYNC] Monday resync failed (non-fatal): %s", resync_err)

            symbol = active_symbols[self.index % len(active_symbols)]
            self._cycle_symbol(symbol)
            self.index = (self.index + 1) % len(active_symbols)
            # Sleep in 1-second chunks for responsive stop
            for _ in range(self.interval_sec):
                if not self.running:
                    break
                time.sleep(1)
        logger.info("[HUNTER] Multi-Asset Hunter stopped.")

    def _cycle_symbol(self, symbol: str):
        try:
            # Silent skip for weekend-closed symbols so Activity Feed stays clean
            from core.market_sessions import is_weekend_closed
            from datetime import timezone
            now_utc = datetime.now(timezone.utc)
            # Saturday = weekend, Sunday before 22:00 UTC = weekend (CME closed)
            is_weekend_now = (now_utc.weekday() == 5) or (now_utc.weekday() == 6 and now_utc.hour < 22)
            if is_weekend_now and is_weekend_closed(symbol):
                logger.debug("[HUNTER] Skipping weekend-closed symbol: %s", symbol)
                return

            agent = self._get_ready_browser_agent(symbol, require_page=True)
            if agent is None:
                return

            # STURDY BRIDGE: Skip this cycle if browser is busy (user interacting / chart loading)
            if hasattr(agent, "is_browser_busy") and agent.is_browser_busy():
                logger.debug("[BRIDGE] Browser busy — skipping %s cycle (user may be interacting)", symbol)
                self.status_update.emit(symbol, "WAITING", "Browser busy — will retry next cycle")
                return

            self.status_update.emit(symbol, "OBSERVING", f"Monitoring active tab for {symbol}")

            # 1. Passive observer sync. BrowserAgent never navigates or switches symbols.
            loop = self.app._browser_loop if hasattr(self.app, "_browser_loop") else None
            if not loop or loop.is_closed():
                self.status_update.emit(symbol, "ERROR", "Browser loop not available")
                return

            navigate_to_symbol = getattr(agent, "navigate_to_symbol", None)
            if not callable(navigate_to_symbol):
                logger.warning("[HUNTER] Browser agent has no navigate_to_symbol method for %s", symbol)
                self.status_update.emit(symbol, "WAITING", "Browser navigation unavailable")
                return

            future = asyncio.run_coroutine_threadsafe(
                navigate_to_symbol(symbol),
                loop,
            )
            nav_ok = future.result(timeout=35)
            if not nav_ok:
                self.status_update.emit(symbol, "ERROR", "Navigation failed")
                # Trigger self-healing: record error and attempt browser restart
                if hasattr(agent, "record_error"):
                    agent.record_error("Navigation failed for " + symbol)
                if getattr(agent, "error_count", 0) >= getattr(agent, "error_threshold", 999999):
                    logger.warning("[WRENCH] Navigation failures reached threshold — triggering browser self-heal")
                    try:
                        self_heal_restart = getattr(agent, "self_heal_restart", None)
                        if not callable(self_heal_restart):
                            return
                        heal_future = asyncio.run_coroutine_threadsafe(
                            self_heal_restart(), loop,
                        )
                        heal_future.result(timeout=30)
                        logger.info("[WRENCH] Browser self-heal completed after navigation failure")
                    except Exception as heal_err:
                        logger.error("[WRENCH] Browser self-heal failed: %s", heal_err)
                return

            # Sync scanner to the symbol now visible in the browser
            if hasattr(self.app, "cloud_scanner") and self.app.cloud_scanner:
                try:
                    self.app.cloud_scanner.scanner.set_eye_symbol(symbol)
                except Exception:
                    pass

            # Force exit before screenshot triggers in passive modes.
            browser_passive = False
            if getattr(self.app, "browser_agent", None):
                browser_passive = bool(
                    getattr(
                        self.app.browser_agent,
                        "_is_passive_observer_mode",
                        lambda: False,
                    )()
                )
            passive_visual = _is_passive_visual_mode() or browser_passive
            if passive_visual:
                logger.debug("[VISION] Skipping visual screenshot in passive mode.")
                return

            self.status_update.emit(symbol, "SCREENSHOT", "Capturing chart...")

            # 2. Take screenshot
            agent = self._get_ready_browser_agent(symbol, require_page=True)
            if agent is None:
                return
            take_screenshot = getattr(agent, "take_screenshot", None)
            if not callable(take_screenshot):
                logger.warning("[HUNTER] Browser agent has no take_screenshot method for %s", symbol)
                self.status_update.emit(symbol, "WAITING", "Screenshot unavailable")
                return

            future = asyncio.run_coroutine_threadsafe(
                take_screenshot(),
                loop,
            )
            screenshot_b64 = future.result(timeout=15)
            if not screenshot_b64:
                self.status_update.emit(symbol, "ERROR", "Screenshot failed")
                # Trigger self-healing for screenshot failures
                if hasattr(agent, "record_error"):
                    agent.record_error("Screenshot failed for " + symbol)
                if getattr(agent, "error_count", 0) >= getattr(agent, "error_threshold", 999999):
                    logger.warning("[WRENCH] Screenshot failures reached threshold — triggering browser self-heal")
                    try:
                        self_heal_restart = getattr(agent, "self_heal_restart", None)
                        if not callable(self_heal_restart):
                            return
                        heal_future = asyncio.run_coroutine_threadsafe(
                            self_heal_restart(), loop,
                        )
                        heal_future.result(timeout=30)
                        logger.info("[WRENCH] Browser self-heal completed after screenshot failure")
                    except Exception as heal_err:
                        logger.error("[WRENCH] Browser self-heal failed: %s", heal_err)
                return

            self.status_update.emit(symbol, "ANALYZING", "Sending to Cloud Brain...")

            # 3. Send to Ollama vision
            from core.brain_swarm import analyze_chart_with_vision
            _wait_for_vision_analysis_slot(f"hunter:{symbol}")
            result = analyze_chart_with_vision(screenshot_b64, symbol)
            signal = result.get("signal", "NONE")
            confidence = result.get("confidence", 50)
            threat = result.get("threat", "MEDIUM")
            reason = result.get("reason", "No reason")

            self.status_update.emit(symbol, f"SIGNAL_{signal}", f"{confidence}% | {reason}")

            # 4. INTELLIGENCE LAYER: Rich analysis narrative to Activity Feed
            self._emit_hunter_intelligence(symbol, signal, confidence, threat, reason)

            # 5. Sunday Gap Guard: block execution during gap window
            if self._is_sunday_gap_window():
                logger.warning(
                    "[SUNDAY-GAP] %s %s signal BLOCKED: Sunday gap window active "
                    "(22:00-22:15 UTC). Waiting for spreads to stabilize.",
                    symbol, signal
                )
                self.status_update.emit(symbol, "SUNDAY_GAP_BLOCKED", "Gap guard active - no execution")
                self.narrator_update.emit(
                    "[STOP]",
                    f"SUNDAY GAP GUARD: {symbol} {signal} blocked (22:00-22:15 UTC)"
                )
                return

            # 6. Execute if BUY/SELL AND confidence meets threshold
            if signal in ("BUY", "SELL"):
                if confidence >= config.MIN_CONFIDENCE_THRESHOLD:
                    logger.critical(
                        "[HUNTER] %s SIGNAL: %s | Confidence: %d%% | Threat: %s | %s",
                        symbol, signal, confidence, threat, reason
                    )
                    self.trade_signal.emit(symbol, signal, reason, int(confidence), str(threat))
                else:
                    logger.info(
                        "[HUNTER] %s %s signal REJECTED | Confidence %d%% < threshold %d%% | %s",
                        symbol, signal, confidence, config.MIN_CONFIDENCE_THRESHOLD, reason
                    )
                    self.status_update.emit(
                        symbol, "SKIPPED_LOW_CONFIDENCE",
                        f"Confidence {confidence}% below {config.MIN_CONFIDENCE_THRESHOLD}%"
                    )
            else:
                logger.info("[HUNTER] %s no trade setup | %s", symbol, reason)

        except Exception as e:
            logger.error("[HUNTER] Cycle error for %s: %s", symbol, e)
            self.status_update.emit(symbol, "ERROR", str(e)[:100])

    def _emit_hunter_intelligence(self, symbol: str, signal: str, confidence: int, threat: str, reason: str):
        """
        Emit rich trade intelligence to the Activity Feed so the operator
        understands WHY the bot is making this decision.
        Uses narrator_update signal for thread-safe GUI updates.
        """
        app = self.app
        if not hasattr(app, "ai_narrator") or not app.ai_narrator:
            return

        # Step 1: Brain analysis start
        self.narrator_update.emit("[BRAIN]", f"Analyzing {symbol} chart...")

        # Step 2: Threat / Opportunity assessment
        if threat == "HIGH":
            threat_icon = "[STOP]"
            threat_msg = f"Threat Level: HIGH | Chop/uncertain conditions detected"
        elif threat == "MEDIUM":
            threat_icon = "[YELLOW]"
            threat_msg = f"Threat Level: MEDIUM | Caution advised"
        else:
            threat_icon = "[GREEN]"
            threat_msg = f"Threat Level: LOW | Clean setup"
        self.narrator_update.emit(threat_icon, threat_msg)

        # Step 3: Confidence / Conviction score
        if confidence >= 85:
            conv_icon = "[TARGET]"
            conv_msg = f"Conviction: {confidence}% | HIGH CONFIDENCE setup"
        elif confidence >= 70:
            conv_icon = "[COMPASS]"
            conv_msg = f"Conviction: {confidence}% | Moderate confidence"
        elif confidence >= 50:
            conv_icon = "[YELLOW]"
            conv_msg = f"Conviction: {confidence}% | Weak edge"
        else:
            conv_icon = "[RED]"
            conv_msg = f"Conviction: {confidence}% | Low probability"
        self.narrator_update.emit(conv_icon, conv_msg)

        # Step 4: Setup reason
        self.narrator_update.emit("[CHART]", f"Setup: {reason}")

        # Step 5: Verdict
        if signal in ("BUY", "SELL"):
            if confidence >= config.MIN_CONFIDENCE_THRESHOLD:
                verdict_icon = "[BOLT]" if confidence >= 80 else "[OK]"
                verdict_msg = f"VERDICT: {signal} {symbol} | Passing to execution gate"
            else:
                verdict_icon = "[PAUSE]"
                verdict_msg = f"VERDICT: {signal} {symbol} | BLOCKED by confidence gate (< {config.MIN_CONFIDENCE_THRESHOLD}%)"
            self.narrator_update.emit(verdict_icon, verdict_msg)
        else:
            self.narrator_update.emit("[PAUSE]", f"VERDICT: NO TRADE | {reason[:60]}")

    async def _perform_monday_resync(self):
        """
        Monday State Re-Sync (Anti-Ghosting).
        Called once on the first weekday scan after a weekend.
        Clears stale weekend signals and pulls a fresh account summary.
        """
        logger.info("[RESYNC] Monday state re-sync initiated. Clearing weekend ghosts...")
        self.narrator_update.emit("[BROOM]", "Monday Re-Sync: Clearing weekend stale state...")

        app = self.app

        # 1. Clear any pending/weekend signals from the scanner
        if hasattr(app, "cloud_scanner") and app.cloud_scanner:
            try:
                scanner = app.cloud_scanner.scanner
                # Reset eye symbol and any cached weekend state
                scanner.eye_symbol = None
                scanner.eye_symbol_at = None
                scanner.priority_scan_list = []
                logger.info("[RESYNC] Scanner eye symbol and priority list cleared")
            except Exception as e:
                logger.warning("[RESYNC] Scanner clear failed: %s", e)

        # 2. Pull fresh account summary from UI/MT5 if available
        try:
            if hasattr(app, "_sync_live_balance"):
                await app._sync_live_balance()
                logger.info("[RESYNC] Live balance re-synced")
            if hasattr(app, "_update_institutional_governor_ui"):
                app._update_institutional_governor_ui()
                logger.info("[RESYNC] Governor UI refreshed")
        except Exception as e:
            logger.warning("[RESYNC] Account re-sync failed: %s", e)

        # 3. Mark re-sync as complete for this week
        self.app._monday_resync_done = True
        logger.info("[RESYNC] Monday state re-sync COMPLETE. Clean slate for the new week.")
        self.narrator_update.emit("[OK]", "Monday Re-Sync COMPLETE. Fresh week, fresh slate.")

    def _is_sunday_gap_window(self) -> bool:
        """Return True if we are in the Sunday gap guard window (22:00-22:15 UTC)."""
        from datetime import timezone
        now = datetime.now(timezone.utc)
        return now.weekday() == 6 and now.hour == 22 and now.minute < 15

    def stop(self):
        self.running = False


class AnalysisWorker(QThread):
    """
    Analyzes market data using Swarm Consensus multi-agent debate.
    Runs in separate thread to keep GUI responsive.

    Dual-Vision: When config.USE_VISION is True, captures a chart
    screenshot and passes it to the Technical Sniper for visual analysis.
    """

    analysis_complete = pyqtSignal(object, object)

    def __init__(self, trade_engine=None):
        super().__init__()
        self.analyzer = LLMAnalyzer()
        self.market_data_queue = queue.Queue()
        self._running = True
        self.trade_engine = trade_engine
        self.vision = (
            VisionCapture(
                chart_region=(
                    config.CHART_REGION_X,
                    config.CHART_REGION_Y,
                    config.CHART_REGION_W,
                    config.CHART_REGION_H,
                ),
                save_debug=config.SAVE_DEBUG_SCREENSHOTS,
            )
            if config.USE_VISION
            else None
        )

    def add_to_queue(self, market_data: MarketDataPoint):
        self.market_data_queue.put(market_data)

    def stop(self):
        """Gracefully unblock the consumer thread for clean closing procedures."""
        self._running = False
        # Inject a poison pill packet to instantly break the blocking .get() state
        self.market_data_queue.put(None)

    def run(self):
        logger.info("Analysis worker started (Swarm Consensus mode)")

        while self._running:
            market_data = self.market_data_queue.get()  # Native OS-block; 0% CPU when idle

            # Check for clean tear-down token sequence
            if market_data is None:
                self.market_data_queue.task_done()
                break

            try:
                # Capture chart screenshot if vision is enabled
                chart_base64 = None
                if self.vision and not _is_passive_visual_mode():
                    try:
                        screenshot = self.vision.capture_active_chart(asset=market_data.asset)
                        if screenshot:
                            chart_base64 = screenshot.to_base64()
                            logger.info(
                                f"Chart screenshot captured for {market_data.asset}"
                            )
                        else:
                            logger.warning(
                                f"Screenshot failed for {market_data.asset} - text-only"
                            )
                    except Exception as vision_error:
                        logger.error(f"Vision capture failed for {market_data.asset}: {vision_error}")
                        chart_base64 = None  # Fallback to text-only
                elif self.vision:
                    logger.debug("[VISION] Skipping visual screenshot in passive mode.")

                # Run swarm debate (with or without vision)
                try:
                    if chart_base64:
                        _wait_for_vision_analysis_slot(f"analysis:{market_data.asset}")
                    output, transcript = self.analyzer.analyze_market(
                        market_data, chart_image_base64=chart_base64
                    )
                    # Store enriched indicators (trend, EMA, MTF) on trade engine
                    # so process_signal's trend filter can use them
                    self.trade_engine.last_indicators = dict(market_data.indicators)
                    self.analysis_complete.emit(output, transcript)
                except Exception as analysis_error:
                    logger.error(f"Swarm analysis failed for {market_data.asset}: {analysis_error}")
                    # Emit None to indicate failure - UI should handle gracefully
                    self.analysis_complete.emit(None, None)

            except Exception as worker_error:
                logger.error(f"Analysis worker critical error: {worker_error}")
                # Continue loop - don't crash the thread
            finally:
                self.market_data_queue.task_done()


class VibeStrategyWorker(QThread):
    """Shield Vibe CLI execution from the main Qt loop."""

    strategy_ready = pyqtSignal(object)
    strategy_failed = pyqtSignal(str, str)

    def __init__(self, prompt: str, command: str, timeout_seconds: int = 10):
        super().__init__()
        self.prompt = prompt
        self.command = command
        self.timeout_seconds = timeout_seconds

    def run(self):
        try:
            adapter = VibeTradingAdapter(command=self.command)
            result = adapter.generate_strategy(self.prompt, timeout=self.timeout_seconds)
            if result.get("ok"):
                result["source_prompt"] = self.prompt
                self.strategy_ready.emit(result)
            else:
                self.strategy_failed.emit(self.prompt, result.get("error", "Vibe CLI failed"))
        except Exception as exc:
            logger.exception("VibeStrategyWorker crashed")
            self.strategy_failed.emit(self.prompt, str(exc))


class VcaniTradeApp:
    """
    Main application controller - Hybrid Architecture.
    
    Cloud Scanner (Vast.ai):
    - Monitors 10 tickers using yfinance
    - Detects technical signals (RSI, Volume, SMA)
    - Triggers Swarm Debate when signals detected
    - Dispatches high-confidence signals (>0.70) to local
    
    Local Executor (Laptop):
    - Receives signals via HTTP listener
    - Switches TradingView to target ticker
    - Performs Vision Confirmation (screenshot)
    - Executes Trade via RPA locally
    """

    def __init__(self):
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.launch_profile = choose_launch_profile()
        self._main_thread_invoker = MainThreadInvoker()

        # Initialize account state early so downstream components can safely
        # reference balance during construction.
        # PRODUCTION: CURRENT_BALANCE must match your prop firm / broker account.
        self.starting_balance = float(config.CURRENT_BALANCE)
        self.balance = float(config.CURRENT_BALANCE)
        self.equity = float(config.CURRENT_BALANCE)
        self.daily_pnl = 0.0
        self.total_pnl = 0.0
        self.peak_balance = float(config.CURRENT_BALANCE)
        self.max_drawdown = 0.0
        self.trades_today = 0
        self.session_peak_pnl = 0.0
        self.protected_session_pnl = 0.0
        self.daily_profit_ladder_triggered = False
        self.positions = []
        self.locked_tickers = {}
        self.rpa_execution_enabled = True
        self.can_trade = True
        self.command_posture = "SCANNING"
        self.bridge_last_seen_ts = 0.0
        self.bridge_status = "disconnected"
        self.bridge_warning_emitted = False
        self.bridge_timeout_seconds = 120.0
        self.balance_diverged = False

        # UI - Command Center (main dashboard)
        self.cmd = CommandCenter()
        self.cmd.set_bridge_status("disconnected")
        self.cmd.log(f"[SWITCHBOARD] {self.launch_profile.headline}")
        
        # AI Narrator Overlay (glassmorphic assistant)
        self.ai_narrator = AINarratorOverlay()
        screens = self.app.screens()
        if len(screens) > 1:
            target_geo = screens[1].availableGeometry()
            self.ai_narrator.move(target_geo.left() + 20, target_geo.top() + 20)
        else:
            self.ai_narrator.move(20, 20)
        self.ai_narrator.show()
        self.ai_narrator.set_rpa_execution_enabled(True)
        self._mirror_shortcut = None

        # Core
        self.trade_engine = TradeEngine()
        self.trade_engine.reset_daily_limits()  # Reset PnL to $0.00 on startup
        self.grader = Grader()
        self.financial_safety = FinancialSafetyManager()
        self.executor = None  # Will be initialized when browser agent is ready
        self.risk_manager = RiskManager(risk_per_trade_pct=1.0)
        self.trade_executor = TradeExecutor(exchange_client=None)
        # Surface Router: one armed surface (TRADINGVIEW or MT5) at a time.
        # The legacy port-5555 socket has been retired; orders go directly to
        # MT5 (native API) or TradingView Desktop (CDP / JS click).
        self.execution_socket_client = None  # legacy attribute, kept None
        self.side_by_side_enabled = False
        self._verify_ghost_hand_socket_async()
        exchange_provider = os.getenv("EXCHANGE_PROVIDER", "binance")
        exchange_api_key = os.getenv("EXCHANGE_API_KEY") or os.getenv("BINANCE_API_KEY") or os.getenv("BYBIT_API_KEY")
        exchange_api_secret = os.getenv("EXCHANGE_API_SECRET") or os.getenv("BINANCE_API_SECRET") or os.getenv("BYBIT_API_SECRET")
        self.exchange_executor = ExchangeLimitExecutor(
            provider=exchange_provider,
            api_key=exchange_api_key,
            api_secret=exchange_api_secret,
        )
        # RPA Hand - clicks TradingView paper trading UI directly (no API keys needed)
        self.rpa_hand = RPAExecutor()  # removed broken on_blind_error callback
        self._ghost_executor = GhostExecutor()
        # MT5 Executor - routes trades to MetaTrader 5 when active mode is MT5
        self.mt5_executor = None
        if self._is_mt5_mode():
            self.mt5_executor = self._get_mt5_executor()
        self._ghost_executor.set_mt5_executor(self.mt5_executor)
        self.hybrid_gateway = HybridExecutionGateway(
            socket_client=None,
            mt5_executor=self.mt5_executor,
            ghost_executor=self._ghost_executor,
        )
        self.sql_journal = TradeJournalDB(db_path="trades.db")
        self.vibe_adapter = VibeTradingAdapter()
        self._vibe_strategy_worker = None
        self.session_detector = MarketSessionDetector()
        self.slippage_guard = SlippageGuard()
        self.support_resistance_levels = {}
        self.latest_confidence_score = 0.0
        self.analysis_mode_status = "READY"
        self.gatekeeper_block_stats = Counter()
        self._gatekeeper_summary_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        self._last_audio_alerts = {}
        self._last_gatekeeper_alert_at = 0.0

        # Load persistent trading settings before deriving any session state from them.
        self.settings = settings_manager
        self.cmd.log(f"[GEAR] Settings loaded: {self.settings.get('investment_mode')} mode")

        saved_watchlist = self.settings.get("session_watchlist", [])
        if not isinstance(saved_watchlist, list):
            saved_watchlist = []
        raw_watchlist = self.settings.normalize_watchlist(saved_watchlist)
        # TURBO-SYNC: Filter out muted tickers at startup
        muted = getattr(config, "MUTED_TICKERS", set())
        self.current_watchlist = [t for t in raw_watchlist if t not in muted]
        if not self.current_watchlist:
            # Fallback: use CLOUD_TICKERS only if watchlist is truly empty at startup
            fallback = self.settings.normalize_watchlist(config.CLOUD_TICKERS)
            self.current_watchlist = [t for t in fallback if t not in muted]
        self.force_action_armed = False
        
        # STAGE 2: AI Strategist & Dynamic Architect
        self.code_architect = CodeArchitect()  # Pine Script/MQL5 generator
        self.atr_stops = LooseATRStops(atr_period=14, multiplier=1.5)  # Loose ATR stops
        self.visual_confirmation = VisualChartConfirmation(check_interval=60)  # Chart reader
        
        # STAGE 3: Institutional Governor & Risk Architect
        self.risk_governor = RiskGovernor(
            max_risk_units=3,
            max_exposure_per_unit_pct=5.0,
            max_total_exposure_pct=15.0,
            correlation_threshold=0.85
        )  # Correlation-aware risk management
        self.sentiment_pulse = SentimentPulse(
            check_interval=300,  # 5 minutes
            red_folder_minutes_before=30,
            red_folder_minutes_after=15
        )  # News filter & RPA kill switch
        self.profit_lock = ProfitLock(
            daily_profit_target_pct=3.0,
            daily_max_loss_pct=2.0,
            breakeven_buffer_pct=1.0,
            starting_balance=self.balance
        )  # Dynamic equity guard

        # STAGE 4: Meta-Cognition & Alpha Hunter
        self.trade_journal = TradeJournal()  # Persistent trade journal
        self.meta_analyzer = MetaAnalyzer(
            journal=self.trade_journal,
            review_interval_hours=24,  # Self-review every 24 hours
            auto_apply=False  # Suggest only, don't auto-change config
        )  # Self-Correction Engine

        # Browser Agent (Autonomous price checking via Playwright)
        self.browser_agent = None  # Will be started asynchronously when needed
        self.browser_agent_status = "idle"  # idle, starting, ready, error
        self._browser_error_message = ""
        self._browser_executor_initialized = False
        self._browser_start_announced = False
        self._browser_ready_announced = False
        self._browser_error_announced = False

        # Prop Firm Rule Engine (The "Professor")
        if config.PROP_FIRM_ENABLED:
            from core.prop_firm_rules import PropFirmRuleEngine, PropFirmName
            firm_map = {
                "TopStep": PropFirmName.TOPSTEP,
                "Apex Trader Funding": PropFirmName.APEX,
                "Apex": PropFirmName.APEX,
                "MyFundedFutures": PropFirmName.MYFUNDED,
                "FTMO": PropFirmName.FTMO,
            }
            firm = firm_map.get(config.PROP_FIRM_NAME, PropFirmName.TOPSTEP)
            self.prop_engine = PropFirmRuleEngine(firm)
            # Override account size from config
            self.prop_engine.rules.account_size = config.PROP_ACCOUNT_SIZE
            self.prop_engine.compliance.starting_balance = config.PROP_ACCOUNT_SIZE
            self.prop_engine.compliance.current_balance = config.PROP_ACCOUNT_SIZE
            self.prop_engine.compliance.peak_balance = config.PROP_ACCOUNT_SIZE
            logger.info(f"Prop Firm Rule Engine active: {config.PROP_FIRM_NAME}")
        else:
            self.prop_engine = None
            logger.info("Prop Firm Rule Engine disabled")

        # Hybrid Architecture Threads
        self.cloud_scanner = CloudScannerThread()  # Cloud-based scanning
        self.signal_listener = SignalListenerThread()  # Local HTTP listener
        self.data_scout_listener = DataScoutListenerThread()  # Vast.ai scout listener
        self.watchtower = WatchtowerScanner()  # Local fallback scanner
        self.analysis_worker = AnalysisWorker(trade_engine=self.trade_engine)  # Local analysis (vision + swarm)
        self.cloud_bridge = CloudBridgeThread(self, host="0.0.0.0", port=8765)  # Remote dashboard bridge
        self.hunter = MultiAssetHunterThread(self) if config.MULTI_ASSET_ENABLED else None  # Vision-based multi-asset hunter
        self.cloud_scanner.scanner.tickers = list(self.current_watchlist)

        # State
        self._manual_mode_override = False
        self._settings_mode_cache = "TEACHER" if _teacher_mode_forced_by_config() else "AUTONOMOUS"
        # Boot in TEACHER only when config explicitly forces it. DRY_RUN alone
        # does NOT force TEACHER any more — paper Autonomous is the normal way
        # to test the bot.
        self.current_mode = "TEACHER" if _teacher_mode_forced_by_config() else "AUTONOMOUS"
        self.analysis_mode = False
        self.latest_signals = {}
        self.ticker_selector = self.current_watchlist[0] if self.current_watchlist else "BTC-USD"
        self.test_status_text = "Ready"  # Test execution status

        # Balance & P/L tracking
        self.balance = float(config.CURRENT_BALANCE)
        self.equity = float(config.CURRENT_BALANCE)
        self.daily_pnl = 0.0
        self.total_pnl = 0.0
        self.peak_balance = float(config.CURRENT_BALANCE)
        self.max_drawdown = 0.0
        self.trades_today = 0
        self.daily_wins = 0
        self.session_peak_pnl = 0.0
        self.protected_session_pnl = 0.0
        self.daily_profit_ladder_triggered = False
        self.positions = []  # Live positions

        # Trading settings
        self.default_investment = 10.0
        self.max_daily_loss = 500.0
        self.trade_ledger = []  # List of executed trades

        self._sync_runtime_session_context()

        self._connect_signals()
        self._apply_initial_mode_ui()
        self._initialize_vibe_status()
        logger.info("VcaniTrade AI initialized (Hybrid Architecture)")
        
        # Auto-start browser agent for testing
        self._start_browser_agent_background()

    def _run_on_ui_thread(self, callback):
        """Run callback on the Qt main thread safely."""
        try:
            if QThread.currentThread() == self.app.thread():
                callback()
            else:
                self._main_thread_invoker.submit(callback)
        except Exception:
            logger.exception("Failed to schedule UI callback")

    def _log_ui(self, message: str):
        self._run_on_ui_thread(lambda: self.cmd.log(message))

    def _activity_ui(self, icon: str, message: str):
        """Thread-safe narrator activity update."""
        self._run_on_ui_thread(lambda: self.ai_narrator.add_activity(icon, message))

    def _is_mt5_mode(self) -> bool:
        """Return True when MT5 should handle execution."""
        return config.get_active_mode() == "MT5"

    def _get_mt5_executor(self):
        if not self._is_mt5_mode():
            return None
        if self.mt5_executor is None:
            self.mt5_executor = _create_mt5_executor()
            if self.mt5_executor is None:
                logger.error("[MT5] Launch requested MetaTrader 5 mode, but the executor could not be created.")
        return self.mt5_executor

    def _normalize_gatekeeper_category(self, category: str) -> str:
        mapping = {
            "confidence": "Confidence",
            "news": "News",
            "risk": "Risk",
            "mtf": "MTF",
            "visibility": "Visibility",
            "watchlist": "Watchlist",
            "price validation": "Price Validation",
            "spread/slippage": "Spread/Slippage",
            "other": "Other",
        }
        key = str(category or "other").strip().lower()
        return mapping.get(key, str(category or "Other").strip() or "Other")

    def _log_gatekeeper_abort(self, category: str, reason: str):
        normalized_category = self._normalize_gatekeeper_category(category)
        reason_text = str(reason or "Unknown execution gate").strip()
        self.gatekeeper_block_stats[normalized_category] += 1
        message = f"[GATEKEEPER] TRADE ABORTED: {reason_text}"
        logger.error(message)
        self._log_ui(
            f'<span style="color:#F85149;font-weight:bold;font-size:14px">[STOP] {message}</span>'
        )
        now = time.monotonic()
        if now - getattr(self, "_last_gatekeeper_alert_at", 0.0) >= 2.0:
            self._last_gatekeeper_alert_at = now
            _play_ui_alert("gatekeeper")
            _speak_alert(f"Trade aborted. {normalized_category}.")
        if hasattr(self, "ai_narrator") and self.ai_narrator:
            self._run_on_ui_thread(
                lambda: self.ai_narrator.notify_error(f"{normalized_category}: {reason_text[:120]}")
            )

    def _emit_gatekeeper_summary_if_due(self):
        current_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        if current_hour <= self._gatekeeper_summary_hour:
            return

        total_blocked = sum(self.gatekeeper_block_stats.values())
        ordered_categories = [
            "News",
            "Risk",
            "MTF",
            "Visibility",
            "Confidence",
            "Watchlist",
            "Price Validation",
            "Spread/Slippage",
            "Other",
        ]
        parts = [
            f"{self.gatekeeper_block_stats[category]} {category}"
            for category in ordered_categories
            if self.gatekeeper_block_stats.get(category, 0) > 0
        ]
        detail = f" ({', '.join(parts)})" if parts else ""
        message = f"[GATEKEEPER] Stats: {total_blocked} Trades Blocked{detail}"
        logger.info(message)
        self._log_ui(
            f'<span style="color:#D29922;font-weight:bold">[WARN] {message}</span>'
        )
        self.gatekeeper_block_stats.clear()
        self._gatekeeper_summary_hour = current_hour

    def _canonical_market_ticker(self, ticker: str) -> str:
        raw = str(ticker or "").strip()
        yf_map = getattr(config, "YFINANCE_SYMBOL_MAP", {})
        normalized = self.settings.normalize_ticker(raw)
        compact = re.sub(r"[^A-Z0-9]", "", raw.upper())
        for candidate in (raw, raw.upper(), raw.replace("-", "").upper(), compact, normalized, normalized.upper()):
            if candidate in yf_map:
                return yf_map[candidate]
        crypto_match = re.fullmatch(r"(BTC|ETH|SOL|XRP|DOGE|ADA|BNB|LTC|BCH|DOT|AVAX|LINK)(?:USD|USDT)?", compact)
        if crypto_match:
            return f"{crypto_match.group(1)}-USD"
        if hasattr(config, "SYMBOL_MAP") and raw in config.SYMBOL_MAP:
            return normalize_yfinance_symbol(config.SYMBOL_MAP[raw])
        if hasattr(config, "SYMBOL_MAP") and normalized in config.SYMBOL_MAP:
            return normalize_yfinance_symbol(config.SYMBOL_MAP[normalized])
        return normalize_yfinance_symbol(normalized or raw)

    def _fetch_mt5_price_for_m6(self, m6_ticker: str) -> float | None:
        """Fetch live midpoint price from MT5 for WealthCharts M6 contract codes.
        M6 codes (NQM6, ESM6, MCLM6) are NOT on Yahoo Finance — MT5 is the source of truth."""
        try:
            import MetaTrader5 as mt5
            import config as _cfg

            tv_map = getattr(_cfg, "TRADINGVIEW_SYMBOL_MAP", {})
            # Reverse-map M6 -> original alias (e.g. NQM6 -> ES=F or CME_MINI:MNQ1!)
            original = None
            for k, v in tv_map.items():
                if v == m6_ticker.upper():
                    original = k
                    break
            if not original:
                logger.warning("[MT5 PRICE] No reverse mapping for %s", m6_ticker)
                return None

            mt5_map = getattr(_cfg, "MT5_SYMBOL_MAP", {})
            mt5_symbol = mt5_map.get(original, original)

            if not mt5.initialize():
                logger.warning("[MT5 PRICE] MT5 not initialized")
                return None

            if not mt5.symbol_select(mt5_symbol, True):
                logger.warning("[MT5 PRICE] Symbol %s not selectable in MarketWatch", mt5_symbol)
                return None

            tick = mt5.symbol_info_tick(mt5_symbol)
            if tick is None:
                logger.warning("[MT5 PRICE] No tick data for %s", mt5_symbol)
                return None

            midpoint = (tick.bid + tick.ask) / 2.0
            logger.info("[MT5 PRICE] %s -> %s | bid=%.4f ask=%.4f mid=%.4f", m6_ticker, mt5_symbol, tick.bid, tick.ask, midpoint)
            return midpoint
        except Exception as e:
            logger.warning("[MT5 PRICE] Error fetching MT5 price for %s: %s", m6_ticker, e)
            return None

    def _run_pretrade_market_audit(self, ticker: str, entry_price: float, force_execute: bool = False) -> bool:
        if force_execute:
            return True

        if entry_price <= 0:
            self._log_gatekeeper_abort(
                "Price Validation",
                f"{ticker} | invalid setup entry price ${entry_price:.2f}",
            )
            return False

        try:
            import concurrent.futures

            current_market_price = None

            # PRICE SOURCE PIVOT: M6 contract codes (NQM6, ESM6, MCLM6) and XAUUSD/Gold are WealthCharts-specific
            # and NOT recognized by Yahoo Finance. Use MT5 as source of truth for these.
            if ticker and (ticker.upper().endswith("M6") or ticker.upper() in {"XAUUSD", "GC", "GC=F", "MGC", "COMEX:MGC1!"}):
                current_market_price = self._fetch_mt5_price_for_m6(ticker)
                if current_market_price and current_market_price > 0:
                    logger.info("[GATEKEEPER] MT5 price for %s: %.4f", ticker, current_market_price)

            # Fallback to Yahoo Finance for everything else
            if current_market_price is None:
                import yfinance as yf
                market_ticker = self._canonical_market_ticker(ticker)

                def fetch_price():
                    symbol = yf.Ticker(market_ticker)
                    history = symbol.history(period="1d", interval="1m")
                    if history.empty or "Close" not in history or history["Close"].dropna().empty:
                        return None
                    return float(history["Close"].dropna().iloc[-1])

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(fetch_price)
                    current_market_price = future.result(timeout=5.0)

            if current_market_price is None or current_market_price <= 0:
                self._log_gatekeeper_abort(
                    "Price Validation",
                    f"{ticker} | unable to fetch live market price via {market_ticker if 'market_ticker' in dir() else 'MT5'}",
                )
                return False

            bid = current_market_price * 0.9995
            ask = current_market_price * 1.0005
            slippage_ok, slippage_pct = self.slippage_guard.check_slippage(entry_price, current_market_price)
            spread_ok, spread_pct = self.slippage_guard.check_spread(bid, ask)

            self.cmd.log(
                f'<span style="color:#8B949E">[CHART] PRE-STRIKE AUDIT</span>: '
                f'{ticker} live=${current_market_price:.2f} | setup=${entry_price:.2f} | '
                f'slippage={slippage_pct:.3f}% | spread={spread_pct:.3f}%'
            )

            if not slippage_ok:
                self._log_gatekeeper_abort(
                    "Price Validation",
                    f"{ticker} | market moved {slippage_pct:.2f}% from setup (${entry_price:.2f} -> ${current_market_price:.2f}) | limit {config.MAX_SLIPPAGE_PERCENT:.2f}%",
                )
                return False

            if not spread_ok:
                self._log_gatekeeper_abort(
                    "Spread/Slippage",
                    f"{ticker} | spread {spread_pct:.2f}% exceeds limit {config.MAX_SPREAD_PERCENT:.2f}%",
                )
                return False

            return True

        except Exception as exc:
            self._log_gatekeeper_abort(
                "Price Validation",
                f"{ticker} | live price audit failed: {exc}",
            )
            return False

    def _gatekeeper_abort_from_execution_result(self, result) -> tuple[str, str]:
        status_value = getattr(result.status, "value", str(result.status or "OTHER"))
        ticker = getattr(result, "ticker", "UNKNOWN")
        if status_value == "ABORTED_SLIPPAGE":
            slippage_pct = float(getattr(result, "slippage_pct", 0.0) or 0.0)
            signal_price = float(getattr(result, "signal_price", 0.0) or 0.0)
            execution_price = float(getattr(result, "execution_price", 0.0) or 0.0)
            return (
                "Price Validation",
                f"{ticker} | market moved {slippage_pct:.2f}% from setup (${signal_price:.2f} -> ${execution_price:.2f}) | limit {config.MAX_SLIPPAGE_PERCENT:.2f}%",
            )
        if status_value == "ABORTED_SPREAD":
            spread_pct = float(getattr(result, "spread_pct", 0.0) or 0.0)
            return (
                "Spread/Slippage",
                f"{ticker} | spread {spread_pct:.2f}% exceeds limit {config.MAX_SPREAD_PERCENT:.2f}%",
            )
        if status_value == "FAILED_PRICE_FETCH":
            return ("Price Validation", f"{ticker} | {getattr(result, 'error_message', 'price audit failed')}")

        detail = str(getattr(result, "error_message", "") or status_value)
        lowered = detail.lower()
        if "visibility gate" in lowered or "professor is blind" in lowered:
            return ("Visibility", f"{ticker} | {detail}")
        if "confidence" in lowered:
            return ("Confidence", f"{ticker} | {detail}")
        return ("Other", f"{ticker} | {detail}")

    def _initialize_vibe_status(self):
        """Reflect shielded Vibe availability on the dashboard."""
        if self.vibe_adapter.is_available():
            self._set_vibe_status_ui("Standby", "standby")
        else:
            self._set_vibe_status_ui("Fallback", "fallback")
            self._log_ui("[SHIELD] VIBE SHIELD: CLI unavailable, local market intelligence will be used")

    def _set_copilot_status_ui(self, text: str):
        self._run_on_ui_thread(lambda: self.cmd.update_copilot_status(text))

    def _set_vibe_status_ui(self, status: str, mode: str = "standby"):
        self._run_on_ui_thread(lambda: self.cmd.update_vibe_status(status, mode))

    def _sync_brain_runtime_ui(self, brain_used: str, fallback_mode: bool):
        """Keep dashboard and mirror aligned with the currently active strike brain."""
        normalized_brain = str(brain_used or "OPENROUTER").strip().upper()
        if fallback_mode:
            self._set_vibe_status_ui("Fallback Mode", "fallback")
            self.ai_narrator.notify_fallback_mode(normalized_brain)
        else:
            self._set_vibe_status_ui("OpenRouter Active", "active")

    def _add_copilot_response_ui(self, thoughts: str, verdict: str, adjustment: str):
        self._run_on_ui_thread(
            lambda: self.cmd.add_copilot_response(
                thoughts=thoughts,
                verdict=verdict,
                adjustment=adjustment,
            )
        )

    def _append_executor_position_ui(self, result, signal_data: dict):
        """Append executor trade result to local position ledger on UI thread."""
        if not self.executor or result.ticker not in self.executor.active_trades:
            return

        position = {
            "asset": result.ticker,
            "side": result.action,
            "entry": result.execution_price,
            "current": result.execution_price,
            "amount": signal_data.get("investment_amount", 0),
            "quantity": result.quantity,
            "tp_price": signal_data.get("take_profit", 0),
            "sl_price": signal_data.get("stop_loss", 0),
            "pnl": 0.0,
            "pnl_pct": 0.0,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "order_id": result.order_id,
            "bot_opened": True,
        }
        self.positions.append(position)
        self.trades_today += 1
        self.cmd.update_positions(self.positions)
        self.cmd.add_trade_log(
            result.ticker,
            result.action,
            signal_data.get("investment_amount", 0),
            0,
            "Open"
        )
    
    def _start_browser_agent_background(self):
        """Start browser agent in background thread with persistent event loop."""
        import asyncio
        import threading
        
        def run_browser_loop():
            """Run persistent event loop for browser agent."""
            try:
                # Create persistent event loop
                self._browser_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._browser_loop)
                
                # Run browser agent
                self._browser_loop.run_until_complete(self._init_browser_agent())
                
                # Keep loop alive for future tasks
                self._browser_loop.run_forever()
            except Exception as e:
                self.browser_agent_status = "error"
                self._browser_error_message = str(e)
                logger.error(f"Browser agent background loop failed: {e}")
        
        # Start in background thread
        self._browser_thread = threading.Thread(target=run_browser_loop, daemon=True)
        self._browser_thread.start()
        self.cmd.log("[GLOBE] Browser agent launching in background...")
    
    async def _init_browser_agent(self):
        """Initialize browser agent and executor asynchronously."""
        try:
            self.browser_agent_status = "starting"
            # Launch visible browser + TradingView automatically (bot controls its own browser)
            self.browser_agent = BrowserAgent(headless=False)
            await self.browser_agent.start()
            if self.browser_agent and self.browser_agent.page:
                self._ghost_executor.set_page(self.browser_agent.page)
                self._ghost_executor.enabled = True  # Force GhostExecutor (JS injection)
            # Connect CDP page to RPA hand for hybrid click strategy
            if hasattr(self, 'rpa_hand') and self.browser_agent.page:
                self.rpa_hand.set_controlled_page(self.browser_agent.page, self._browser_loop)
            # Wire page-switch callback so RPA hand follows tab changes
            def _page_switched(new_page):
                if hasattr(self, 'rpa_hand'):
                    self.rpa_hand.set_controlled_page(new_page, self._browser_loop)
            self.browser_agent._on_page_switched = _page_switched
            self.browser_agent_status = "ready"
        except Exception as e:
            self.browser_agent_status = "error"
            self._browser_error_message = str(e)
            logger.error(f"Failed to start browser agent: {e}")

    def _cloud_heartbeat(self):
        """Log a heartbeat every 10s so the operator knows the bot is alive on Vast.ai."""
        # HEARTBEAT SUPPRESSION: if RPA is executing, don't touch the browser
        if self.executor and getattr(self.executor.rpa_executor, "is_executing", False):
            logger.info("[HEARTBEAT] Lion is executing trade — heartbeat suppressed")
            return
        try:
            url = ""
            if self.browser_agent and self.browser_agent.is_running and self.browser_agent.page:
                import asyncio
                future = asyncio.run_coroutine_threadsafe(
                    self.browser_agent.get_current_url(),
                    self._browser_loop if hasattr(self, '_browser_loop') and self._browser_loop and not self._browser_loop.is_closed() else asyncio.get_event_loop()
                )
                try:
                    url = future.result(timeout=2.0)
                except Exception:
                    url = "(pending)"
            mode = self.current_mode
            positions = len(self.positions)
            logger.info(
                "[HEARTBEAT] Lion is awake | mode=%s | positions=%s | url=%s",
                mode, positions, url[:60] if url else "(no page)"
            )
        except Exception as e:
            logger.warning("[HEARTBEAT] Heartbeat error: %s", e)

    def _sync_browser_agent_state(self):
        """Finalize browser-agent state transitions on the Qt main thread."""
        if self.browser_agent_status == "starting" and not self._browser_start_announced:
            self._browser_start_announced = True
            self.cmd.log("[GLOBE] Browser agent starting...")

        if self.browser_agent_status == "ready" and not self._browser_executor_initialized:
            if self.browser_agent:
                self.executor = UnifiedTradeExecutor(
                    browser_agent=self.browser_agent,
                    cmd_logger=self.cmd.log,
                    ai_narrator=self.ai_narrator,
                )
                self._browser_executor_initialized = True
                # Ensure RPA hand has the controlled page
                if hasattr(self, 'rpa_hand') and self.browser_agent.page:
                    self.rpa_hand.set_controlled_page(self.browser_agent.page, self._browser_loop)
                if not self._browser_ready_announced:
                    self._browser_ready_announced = True
                    self.cmd.log("[GLOBE] Browser agent ready - autonomous price checking ready")
                    self.cmd.log("[SUCCESS] Trade executor initialized with Slippage Guard")
                    self.ai_narrator.add_activity("[GLOBE]", "Browser agent ready for autonomous tasks")

        if self.browser_agent_status == "error" and not self._browser_error_announced:
            self._browser_error_announced = True
            msg = self._browser_error_message or "Unknown browser-agent startup error"
            self.cmd.log(f"[WARN] Browser agent failed: {msg}")
            self.ai_narrator.notify_error(f"Browser agent: {msg}")

    def _connect_signals(self):
        """Wire all backend threads to the CommandCenter UI."""
        # Command Center
        self.cmd.mode_changed.connect(self._on_mode_changed)
        if hasattr(self.cmd, "dry_run_changed"):
            self.cmd.dry_run_changed.connect(self._on_dry_run_changed)
        self.cmd.kill_switch_triggered.connect(self._on_kill_switch)
        self.cmd.watchlist_updated.connect(self._on_watchlist_updated)
        self.cmd.settings_changed.connect(self._on_settings_changed)
        self.cmd.test_browser_requested.connect(self._on_test_browser)
        self.cmd.force_test_trade_requested.connect(self._on_force_test_trade)
        self.cmd.user_command_sent.connect(self._on_copilot_command)  # NEW: Co-Pilot Command Bridge
        self.ai_narrator.stealth_toggled.connect(self._handle_manual_stealth_toggle)

        # Cloud Bridge kill switch (thread-safe via pyqtSignal)
        self.cloud_bridge.kill_requested.connect(self._on_kill_switch)

        # Multi-Asset Hunter signals (QueuedConnection for thread safety)
        if self.hunter:
            self.hunter.status_update.connect(self._on_hunter_status_update, Qt.ConnectionType.QueuedConnection)
            self.hunter.trade_signal.connect(self._on_hunter_trade_signal, Qt.ConnectionType.QueuedConnection)
            self.hunter.narrator_update.connect(self._on_hunter_narrator_update, Qt.ConnectionType.QueuedConnection)

        # Cloud Scanner -> UI + Narrator (QueuedConnection ensures main-thread delivery)
        self.cloud_scanner.signal_detected.connect(self._on_cloud_signal, Qt.ConnectionType.QueuedConnection)
        self.cloud_scanner.technical_signal_detected.connect(self._on_technical_signal_detected, Qt.ConnectionType.QueuedConnection)
        self.cloud_scanner.scanner_error.connect(self._on_scanner_error, Qt.ConnectionType.QueuedConnection)
        self.cloud_scanner.ticker_status.connect(self._on_ticker_status_update, Qt.ConnectionType.QueuedConnection)

        # Signal Listener -> UI + Narrator (QueuedConnection for thread safety)
        self.signal_listener.signal_received.connect(self._on_signal_received, Qt.ConnectionType.QueuedConnection)
        self.signal_listener.handshake_received.connect(self._on_bridge_handshake_received, Qt.ConnectionType.QueuedConnection)
        self.signal_listener.listener_error.connect(self._on_listener_error, Qt.ConnectionType.QueuedConnection)

        # Data Scout Listener -> UI + TV Flip + Narrator
        self.data_scout_listener.signal_received.connect(self._on_data_scout_signal, Qt.ConnectionType.QueuedConnection)

        # Watchtower -> UI alerts + Swarm handoff + Narrator
        self.watchtower.alert_detected.connect(self._on_watchtower_alert, Qt.ConnectionType.QueuedConnection)
        self.watchtower.market_data_ready.connect(self._on_market_data, Qt.ConnectionType.QueuedConnection)

        # Analysis worker -> Trade engine + UI + Narrator
        self.analysis_worker.analysis_complete.connect(self._on_analysis_complete, Qt.ConnectionType.QueuedConnection)

        # Position monitoring timer
        self.position_timer = QTimer()
        self.position_timer.timeout.connect(self._update_positions)
        self.position_timer.start(5000)  # Update every 5 seconds

        self._mirror_shortcut = QShortcut(QKeySequence("Ctrl+Shift+H"), self.cmd)
        self._mirror_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._mirror_shortcut.activated.connect(self._toggle_mirror_visibility)

    def _apply_initial_mode_ui(self):
        """Align the dashboard controls with the backend's initial runtime mode."""
        self._apply_mode_to_dashboard(self.current_mode)

    def _teacher_mode_forced_by_config(self) -> bool:
        """Return True when env/config explicitly pins the app to Teacher mode."""
        return _teacher_mode_forced_by_config()

    def _normalize_runtime_mode(self, mode: str, source: str = "runtime") -> str:
        requested = str(mode or "").strip().upper()
        if requested not in {"TEACHER", "AUTONOMOUS"}:
            requested = self.current_mode if hasattr(self, "current_mode") else "TEACHER"
        if requested == "AUTONOMOUS" and self._teacher_mode_forced_by_config():
            logger.info("[MODE] Ignoring AUTONOMOUS request from %s because Teacher mode is config-forced", source)
            return "TEACHER"
        # IMPORTANT: DRY_RUN no longer forces TEACHER. Autonomous in paper mode
        # is the whole point of paper testing — you watch the bot click on its
        # own with simulated fills. Real-money safety stays guarded by DRY_RUN
        # checks deeper in the executor, not here.
        if requested == "AUTONOMOUS" and bool(getattr(config, "DRY_RUN", False)):
            logger.info(
                "[MODE] %s switched to AUTONOMOUS in PAPER mode (DRY_RUN=True) - simulated orders only",
                source,
            )
        return requested

    def _set_runtime_mode(self, mode: str, source: str = "runtime", manual: bool = False) -> str:
        """Single authority for runtime mode changes.

        Settings sync can follow this state, but it must not override it.
        """
        normalized = self._normalize_runtime_mode(mode, source=source)
        self.current_mode = normalized
        self._settings_mode_cache = normalized
        if manual:
            self._manual_mode_override = True
        self._sync_runtime_session_context()
        logger.info("Mode changed to %s", normalized)
        return normalized

    def _apply_mode_to_dashboard(self, mode: str):
        """Update dashboard button state without re-emitting mode_changed."""
        from PyQt6.QtCore import QSignalBlocker

        normalized = self._normalize_runtime_mode(mode, source="dashboard_sync")
        blocker = QSignalBlocker(self.cmd)
        try:
            if normalized == "AUTONOMOUS":
                self.cmd._set_autonomous_mode()
            else:
                self.cmd._set_teacher_mode()
        finally:
            del blocker

    def _sync_runtime_session_context(self):
        """Keep session-awareness aligned with the active dashboard watchlist and operator mode."""
        watchlist = [ticker for ticker in self.current_watchlist if str(ticker).strip()]
        self.session_detector.set_runtime_context(self.current_mode, watchlist)
        self.cloud_scanner.scanner.set_runtime_context(self.current_mode, watchlist)
        self.financial_safety.set_runtime_mode(self.current_mode)

        # News filter & safety timer
        if not hasattr(self, "safety_timer"):
            self.safety_timer = QTimer()
            self.safety_timer.timeout.connect(self._safety_timer_wrapper)
            self.safety_timer.start(60000)  # Check every 60 seconds

        # Heartbeat monitor - logs system health every 60 seconds
        if not hasattr(self, "heartbeat_timer"):
            self.heartbeat_timer = QTimer()
            self.heartbeat_timer.timeout.connect(self._heartbeat_check)
            self.heartbeat_timer.start(60000)  # 60 seconds

        if not hasattr(self, "bridge_status_timer"):
            self.bridge_status_timer = QTimer()
            self.bridge_status_timer.timeout.connect(self._check_bridge_heartbeat)
            self.bridge_status_timer.start(5000)
        
        # STAGE 3: Institutional Governor update timer (every 30 seconds)
        if not hasattr(self, "governor_timer"):
            self.governor_timer = QTimer()
            self.governor_timer.timeout.connect(self._update_institutional_governor_ui)
            self.governor_timer.start(30000)  # 30 seconds

        # STAGE 4: Meta-Cognition Self-Review timer (every 24 hours = 86400000 ms)
        if not hasattr(self, "meta_review_timer"):
            self.meta_review_timer = QTimer()
            self.meta_review_timer.timeout.connect(self._run_meta_cognition_review)
            self.meta_review_timer.start(86400000)  # 24 hours
            self.cmd.log("[BRAIN] Meta-Cognition self-review scheduled (every 24 hours)")

        # Browser agent state sync timer (main-thread finalization)
        if not hasattr(self, "browser_state_timer"):
            self.browser_state_timer = QTimer()
            self.browser_state_timer.timeout.connect(self._sync_browser_agent_state)
            self.browser_state_timer.start(1000)

        # CLOUD HEARTBEAT: Log status every 10s so you know the Lion is awake
        if not hasattr(self, "cloud_heartbeat_timer"):
            self.cloud_heartbeat_timer = QTimer()
            self.cloud_heartbeat_timer.timeout.connect(self._cloud_heartbeat)
            self.cloud_heartbeat_timer.start(10000)  # 10 seconds

        self.cmd.log("[OK] All systems connected - Ready to trade")
        
        # Notify AI Narrator that system is ready
        self.ai_narrator.notify_system_ready()
        self.ai_narrator.set_watchlist(self.current_watchlist)

    def _on_watchlist_updated(self, watchlist: list):
        """Handle watchlist update from dashboard."""
        raw_watchlist = self.settings.normalize_watchlist(watchlist)
        # TURBO-SYNC: Filter out muted tickers (MCLM6 Oil is suspended)
        muted = getattr(config, "MUTED_TICKERS", set())
        self.current_watchlist = [t for t in raw_watchlist if t not in muted]
        config.CLOUD_TICKERS = list(self.current_watchlist)
        self.cloud_scanner.scanner.tickers = list(self.current_watchlist)
        self.cloud_scanner.scanner.priority_scan_list = []
        self.rpa_hand.active_watchlist = list(self.current_watchlist)
        self._sync_runtime_session_context()
        if muted:
            self.cmd.log(f"[MUTE] Suspended tickers: {', '.join(sorted(muted))}")
        self.cmd.log(f"[CHART] Watchlist updated: {len(self.current_watchlist)} tickers")
        self.ai_narrator.notify_scan_start(len(self.current_watchlist))
        self.ai_narrator.set_watchlist(self.current_watchlist)
        for ticker in self.current_watchlist:
            self._on_ticker_status_update(ticker, "scanning")

    def _handle_manual_stealth_toggle(self, enabled: bool):
        """Mirror toggle for pausing/resuming physical RPA execution."""
        self.rpa_execution_enabled = bool(enabled)
        state = "enabled" if self.rpa_execution_enabled else "paused"
        self.cmd.log(f"[MOUSE] RPA Hand manually {state} from the Mirror")
        self.ai_narrator.set_rpa_execution_enabled(self.rpa_execution_enabled)
        self._update_institutional_governor_ui()

    def _on_ticker_status_update(self, ticker: str, status: str):
        """Update dashboard and mirror with per-ticker scanner state."""
        self.cmd.update_watchlist_status(ticker, status)
        self.ai_narrator.update_ticker_status(ticker, status)
        if status == "scanning" and bool(getattr(config, "REALTIME_SCAN_FEED", False)):
            if hasattr(self.ai_narrator, "notify_scan_tick"):
                self.ai_narrator.notify_scan_tick(ticker)
            _play_ui_alert("scan")
        if status.startswith("brain_reasoning:"):
            self.ai_narrator.notify_brain_thinking(ticker, status.split(":", 1)[1])
        elif status.startswith("brain_fallback:"):
            self._sync_brain_runtime_ui(status.split(":", 1)[1], True)

    def _announce_signal_alert(self, ticker: str, action: str, confidence, source: str = "signal"):
        """Debounced audible announcement for actionable opportunities."""
        ticker = str(ticker or "UNKNOWN").upper()
        action = str(action or "SIGNAL").upper()
        confidence_score = self._confidence_to_score(confidence)
        if not self._is_loud_signal(confidence_score, {}):
            logger.info(
                "[INCUBATION] Muting audible alert for %s %s at %.1f%% from %s",
                action,
                ticker,
                confidence_score,
                source,
            )
            return
        key = f"{source}:{ticker}:{action}"
        now = time.monotonic()
        last = float(getattr(self, "_last_audio_alerts", {}).get(key, 0.0) or 0.0)
        if now - last < 3.0:
            return
        self._last_audio_alerts[key] = now
        _play_ui_alert("signal", action, confidence_score)
        _speak_alert(f"{action} signal on {ticker}. Confidence {confidence_score:.0f} percent.")

    def _toggle_mirror_visibility(self):
        """Hide/show the mirror instantly for single-screen research."""
        if self.ai_narrator.isVisible():
            self.ai_narrator.hide()
            self.cmd.log("[WINDOW] Mirror hidden via Ctrl+Shift+H")
        else:
            self.ai_narrator.show()
            self.cmd.log("[WINDOW] Mirror shown via Ctrl+Shift+H")

    def _format_liquidity_label(self, signal_data: dict) -> str:
        """Create a readable liquidity label for the mirror broadcast strip."""
        liquidity_zone = signal_data.get("liquidity_zone") or {}
        if liquidity_zone:
            zone_type = str(liquidity_zone.get("type", "zone")).replace("_", " ").upper()
            level = float(liquidity_zone.get("level", 0.0) or 0.0)
            if level > 0:
                return f"{zone_type} @ {level:.4f}"
            return zone_type
        return str(signal_data.get("liquidity_label") or signal_data.get("brain_reasoning") or "--")

    def _sync_signal_to_visible_chart_price(self, asset: str) -> float | None:
        """Optional visible-chart price sync.

        TradingView execution no longer depends on legacy WealthCharts DOM reads.
        Scanner/MT5 data remains the source of truth for signal prices.
        """
        return None

    def _broadcast_trade_levels(self, signal_data: dict):
        """Send large SL/TP/liquidity text to the mirror for teacher-mode readability.
        SIGNAL DECAY: Levels auto-clear after 2 seconds to prevent stale display.
        Wrapped in try/except — UI timer bugs must NEVER crash trade execution."""
        try:
            ticker = signal_data.get("ticker", "UNKNOWN")
            self.ai_narrator.update_trade_levels(
                ticker=ticker,
                stop_loss=signal_data.get("stop_loss"),
                take_profit=signal_data.get("take_profit"),
                liquidity_label=self._format_liquidity_label(signal_data),
            )
            if hasattr(self.ai_narrator, "update_confidence_meter"):
                self.ai_narrator.update_confidence_meter(
                    ticker,
                    signal_data.get("action", "SIGNAL"),
                    signal_data.get("confidence", self.latest_confidence_score),
                    status="LEVELS LIVE",
                )
            # SIGNAL DECAY: Clear stale levels after 2 seconds
            from PyQt6.QtCore import QTimer
            old_timer = getattr(self, '_signal_decay_timer', None)
            if old_timer:
                try:
                    old_timer.stop()
                except Exception:
                    pass
            self._signal_decay_timer = QTimer()  # No parent — avoids QMainWindow/QObject type mismatch
            self._signal_decay_timer.setSingleShot(True)
            self._signal_decay_timer.timeout.connect(lambda: self.ai_narrator.clear_trade_levels(announce=False))
            self._signal_decay_timer.start(2000)
        except Exception as timer_err:
            logger.warning("[BROADCAST] Trade levels UI error (non-fatal): %s", timer_err)

    def _on_settings_changed(self, settings: dict):
        """Handle trading settings update from dashboard."""
        settings = dict(settings or {})
        incoming_mode = str(
            settings.pop("runtime_mode", settings.pop("current_mode", settings.pop("mode", "")))
            or ""
        ).upper()
        if incoming_mode:
            normalized_incoming = self._normalize_runtime_mode(incoming_mode, source="settings_sync")
            if normalized_incoming != self.current_mode:
                logger.info(
                    "[MODE] Ignoring stale settings mode %s while live mode is %s",
                    incoming_mode,
                    self.current_mode,
                )
            self._settings_mode_cache = self.current_mode

        # Update persistent settings
        self.settings.update(settings)
        if self._normalize_runtime_mode(self.current_mode, source="post_settings_sync") != self.current_mode:
            corrected = self._set_runtime_mode(self.current_mode, source="post_settings_sync")
            self._apply_mode_to_dashboard(corrected)
        elif self._settings_mode_cache != self.current_mode:
            self._settings_mode_cache = self.current_mode

        # Update local variables
        self.default_investment = settings.get("investment_amount", settings.get("investment", 1000.0))
        self.max_daily_loss = settings.get("max_daily_loss", 500.0)
        self.rpa_hand.set_human_latency(bool(settings.get("human_latency", self.settings.get("human_latency", True))))

        firm_name = str(settings.get("prop_firm_name", self.settings.get("prop_firm_name", config.PROP_FIRM_NAME)))
        if self.prop_engine:
            from core.prop_firm_rules import PropFirmName, PropFirmRuleEngine

            firm_map = {
                "TopStep": PropFirmName.TOPSTEP,
                "Apex Trader Funding": PropFirmName.APEX,
                "Apex": PropFirmName.APEX,
                "MyFundedFutures": PropFirmName.MYFUNDED,
                "FTMO": PropFirmName.FTMO,
                "TradeDay": PropFirmName.TRADEDAY,
            }
            selected_firm = firm_map.get(firm_name, PropFirmName.TOPSTEP)
            if self.prop_engine.firm != selected_firm:
                self.prop_engine = PropFirmRuleEngine(selected_firm)
                self.cmd.log(f"[GRADUATE] Prop firm profile switched to {firm_name}")

        mode = self.settings.get("investment_mode", "dollar")
        risk_mode = (
            "AUTO-RISK (structure)"
            if self.settings.get("auto_risk_enabled", True)
            else f"manual TP {self.settings.get('take_profit_pct', 2.0):.1f}% / SL {self.settings.get('stop_loss_pct', 1.0):.1f}%"
        )
        if mode == "lots":
            lot_size = self.settings.get("lot_size", 2.0)
            self.cmd.log(f"[GEAR] Settings updated: {lot_size} lots/trade, {risk_mode}")
        else:
            self.cmd.log(f"[GEAR] Settings updated: ${self.default_investment}/trade, {risk_mode}")

        # Notify AI Narrator
        self.ai_narrator.add_activity(
            "[GEAR]",
            f"Settings updated - {mode} mode"
        )

    def _on_copilot_command(self, command: str):
        """Handle Co-Pilot Command Bridge - Process user suggestions with DYNAMIC STYLE SWITCHING"""
        import re
        
        self.cmd.log(f"[SUCCESS] CO-PILOT COMMAND: {command}")
        self.cmd.update_copilot_status("Processing...")

        # ========== DYNAMIC STYLE SWITCHING ==========
        command_upper = command.upper()
        
        # "BE AGGRESSIVE" mode - Decrease MTF Confirmation requirements
        if "BE AGGRESSIVE" in command_upper or "GO AGGRESSIVE" in command_upper:
            self.cmd.log("[FIRE] AGGRESSIVE MODE ACTIVATED: Lowering MTF confirmation requirements")
            self.cmd.add_copilot_response(
                thoughts="User wants more aggressive trading. Reducing confirmation gates to capture more opportunities.",
                verdict="AGGRESSIVE MODE ENABLED",
                adjustment="MTF confirmation reduced from 2/3 agents to 1/3. Position size increased to 100%. Faster execution enabled."
            )
            # Store aggressive mode flag for scanner/executor to use
            self._aggressive_mode = True
            self.ai_narrator.set_aggression_mode(True)
            self.ai_narrator.set_command_posture("BE AGGRESSIVE")
            self.cmd.update_copilot_mode("BE AGGRESSIVE", "#F85149")
            self.cmd.update_vibe_status("Aggressive - Low Confirmations", "active")
            self.cmd.update_copilot_status("AGGRESSIVE MODE")
            return
        
        # "PROTECT ACCOUNT" mode - Switch to Prop Firm Mode with tight trailing stops
        if "PROTECT ACCOUNT" in command_upper or "CONSERVATIVE" in command_upper or "SAFE MODE" in command_upper:
            self.cmd.log("[SHIELD] PROP FIRM MODE ACTIVATED: Maximum protection enabled")
            self.cmd.add_copilot_response(
                thoughts="User wants maximum capital protection. Enabling strict prop firm compliance rules.",
                verdict="PROP FIRM MODE ENABLED",
                adjustment="MTF confirmation increased to 3/3 agents. Position size reduced to 50%. Tight trailing stops enabled. Daily loss limit enforced."
            )
            self._aggressive_mode = False
            self._prop_firm_mode = True
            self.ai_narrator.set_aggression_mode(False)
            self.ai_narrator.set_command_posture("PROTECT ACCOUNT")
            self.cmd.update_copilot_mode("PROTECT ACCOUNT", "#58A6FF")
            self.cmd.update_vibe_status("Protected - Prop Firm Rules", "active")
            self.cmd.update_copilot_status("PROP FIRM MODE")
            return

        force_action_phrase = "FORCE ACTION"
        force_action_requested = force_action_phrase in command.upper()
        if force_action_requested:
            self.force_action_armed = True
            self.cmd.log("[DICE] GAMBLER MODE ARMED: next [SIGNAL] BUY/SELL will ignore the confidence gate")
            self.ai_narrator.set_aggression_mode(True)
            self.ai_narrator.add_activity("[DICE]", "Gambler Mode armed for next BUY/SELL Co-Pilot signal")
            command = re.sub(force_action_phrase, "", command, flags=re.IGNORECASE).strip() or "Analyze the active ticker now"
        
        # Parse command intent
        command_lower = command.lower()

        if "force strike test" in command_lower:
            self._handle_force_strike_test_command(command)
            return
        
        # Store current command for AI analysis
        self._current_copilot_command = command
        
        # Detect command type
        if self._looks_like_strategy_request(command):
            self._generate_vibe_strategy(command)
        elif any(keyword in command_lower for keyword in ["analysis mode", "focus mode", "chart mode", "professor mode"]):
            self._handle_analysis_mode_command(command)
        elif any(keyword in command_lower for keyword in ["mirror", "overlay", "assistant panel"]):
            self._handle_mirror_control_command(command)
        elif any(keyword in command_lower for keyword in ["support", "resistance", "trend", "break", "blink", "draw line"]):
            self._handle_chart_markup_command(command)
        elif any(keyword in command_lower for keyword in ["switch to", "change timeframe", "use timeframe"]):
            # Timeframe switching
            self._handle_timeframe_change(command)
        elif any(keyword in command_lower for keyword in ["buy now", "sell now", "force buy", "force sell", "force trade"]):
            # Force trade with safety checks
            self._handle_force_trade(command)
        elif any(keyword in command_lower for keyword in ["news", "sentiment", "analyze"]):
            # News analysis
            self._handle_news_analysis(command)
        elif any(keyword in command_lower for keyword in ["show levels", "plot zones", "draw zones", "generate script"]):
            # STAGE 2: Generate Pine Script zones
            self._handle_show_levels(command)
        else:
            # General analysis with user suggestion
            self._handle_general_command(command)

    def _handle_analysis_mode_command(self, command: str):
        """Enable/disable mirror analysis mode for chart-focused trading workflow."""
        cmd = command.lower()
        enable = not any(x in cmd for x in ["off", "disable", "stop", "exit"])
        self.analysis_mode = enable

        context = "Chart lock + visual commentary"
        if hasattr(self, "ai_narrator") and self.ai_narrator:
            self.ai_narrator.set_analysis_mode(enable, context)
            if enable:
                self.ai_narrator.snap_to("top-right", screen_index=1)

        if enable:
            self.cmd.copilot_mode.setText("Current Mode: ANALYSIS MODE")
            self.cmd.log("[COMPASS] Analysis Mode enabled: mirror focused on chart workflow")
        else:
            self.cmd.copilot_mode.setText("Current Mode: SCANNING")
            self.cmd.log("[COMPASS] Analysis Mode disabled")
        self.cmd.update_copilot_status("Ready")

    def _handle_mirror_control_command(self, command: str):
        """Process mirror panel controls through natural language commands."""
        cmd = command.lower()
        if not hasattr(self, "ai_narrator") or not self.ai_narrator:
            self.cmd.log("[FAIL] Mirror not available")
            return

        if "pin" in cmd:
            should_pin = not any(x in cmd for x in ["unpin", "off", "disable"])
            self.ai_narrator.pin_btn.setChecked(should_pin)
            self.ai_narrator._toggle_pin()
            self.cmd.log(f"[PIN] Mirror {'pinned' if should_pin else 'unpinned'}")

        if "opacity" in cmd:
            import re
            m = re.search(r"(\d{2,3})", cmd)
            if m:
                value = max(55, min(100, int(m.group(1))))
                self.ai_narrator.opacity_slider.setValue(value)
                self.cmd.log(f"[GLASSES] Mirror opacity set to {value}%")

        if "font" in cmd:
            if any(x in cmd for x in ["bigger", "larger", "increase", "up", "+"]):
                self.ai_narrator._change_font_scale(0.1)
                self.cmd.log("[MAGNIFY] Mirror font increased")
            elif any(x in cmd for x in ["smaller", "decrease", "down", "-"]):
                self.ai_narrator._change_font_scale(-0.1)
                self.cmd.log("[MAGNIFY] Mirror font decreased")

        if "snap" in cmd or "monitor" in cmd:
            if "bottom right" in cmd:
                self.ai_narrator.snap_to("bottom-right", 1)
                self.cmd.log("[MAGNET] Mirror snapped to monitor 2 bottom-right")
            elif "bottom left" in cmd:
                self.ai_narrator.snap_to("bottom-left", 1)
                self.cmd.log("[MAGNET] Mirror snapped to monitor 2 bottom-left")
            elif "center" in cmd:
                self.ai_narrator.snap_to("center", 1)
                self.cmd.log("[MAGNET] Mirror centered on monitor 2")
            elif "left" in cmd:
                self.ai_narrator.snap_to("top-left", 1)
                self.cmd.log("[MAGNET] Mirror snapped to monitor 2 top-left")
            else:
                self.ai_narrator.snap_to("top-right", 1)
                self.cmd.log("[MAGNET] Mirror snapped to monitor 2 top-right")

        self.cmd.update_copilot_status("Ready")

    def _handle_chart_markup_command(self, command: str):
        """Generate chart markup scripts (S/R, trend breaks, blink lines) and inject to WealthCharts."""
        import re

        self.cmd.log(f"[THREAD] CHART MARKUP requested: {command}")

        asset_match = re.search(r'(BTC|ETH|SOL|XRP|BNB|ADA|EURUSD|GOLD|SPY|TSLA|AAPL|NVDA)', command, re.IGNORECASE)
        asset = asset_match.group(0).upper() + "-USD" if asset_match else "BTC-USD"

        tf_match = re.search(r'(\d+[mh])', command.lower())
        timeframe = tf_match.group(1).upper() if tf_match else "2H"

        # Base zones + markup overlay hooks.
        demand_zones = [
            {"low": 69000.0, "high": 69500.0, "strength": 0.82},
            {"low": 68200.0, "high": 68800.0, "strength": 0.70},
        ]
        supply_zones = [
            {"low": 71000.0, "high": 71500.0, "strength": 0.80},
            {"low": 72000.0, "high": 72800.0, "strength": 0.66},
        ]

        self.support_resistance_levels[asset] = {
            "supports": [z["low"] for z in demand_zones],
            "resistances": [z["high"] for z in supply_zones],
        }

        base_script = self.code_architect.generate_pine_script_zones(
            asset=asset,
            demand_zones=demand_zones,
            supply_zones=supply_zones,
            timeframe=timeframe
        )

        markup = self._build_markup_overlay_snippet(command)
        self._last_pine_script = f"{base_script}\n\n// --- MARKUP OVERLAY ---\n{markup}\n"

        self.cmd.add_copilot_response(
            thoughts=f"Generated chart markup overlay for {asset} on {timeframe}",
            verdict="MARKUP_READY",
            adjustment="[OK] Support/resistance and trend-break overlays prepared. Injecting to WealthCharts now."
        )

        self._inject_pine_script_to_chart()
        self.cmd.update_copilot_status("Ready")

    def _build_markup_overlay_snippet(self, command: str) -> str:
        """Build Pine snippet for visual trade coaching overlays."""
        cmd = command.lower()
        lines = []
        lines.append("var line ai_sr_top = na")
        lines.append("var line ai_sr_bot = na")
        lines.append("if barstate.islast")
        lines.append("    ai_sr_top := line.new(bar_index-150, high * 1.01, bar_index, high * 1.01, color=color.new(color.red, 0), width=2)")
        lines.append("    ai_sr_bot := line.new(bar_index-150, low * 0.99, bar_index, low * 0.99, color=color.new(color.green, 0), width=2)")

        if any(x in cmd for x in ["trend", "break", "breakout"]):
            lines.append("var line ai_trend = na")
            lines.append("if barstate.islast")
            lines.append("    ai_trend := line.new(bar_index-80, close[80], bar_index, close, color=color.new(color.yellow, 0), width=2, style=line.style_dashed)")
            lines.append("break_up = ta.crossover(close, ta.sma(close, 20))")
            lines.append("break_dn = ta.crossunder(close, ta.sma(close, 20))")
            lines.append("plotshape(break_up, title='Trend Break Up', color=color.lime, style=shape.triangleup, size=size.small)")
            lines.append("plotshape(break_dn, title='Trend Break Down', color=color.red, style=shape.triangledown, size=size.small)")

        if "blink" in cmd:
            lines.append("blink = bar_index % 2 == 0")
            lines.append("bgcolor(blink ? color.new(color.orange, 92) : na)")

        if any(x in cmd for x in ["support", "resistance"]):
            lines.append("plot(ta.highest(high, 50), title='AI Resistance', color=color.new(color.red, 10), linewidth=2)")
            lines.append("plot(ta.lowest(low, 50), title='AI Support', color=color.new(color.green, 10), linewidth=2)")

        lines.append("label.new(bar_index, high, text='AI: Watch break + reaction', style=label.style_label_down, color=color.new(color.black, 75), textcolor=color.new(color.aqua, 0), size=size.tiny)")
        return "\n".join(lines)

    def _handle_timeframe_change(self, command: str):
        """Handle timeframe switching commands like 'Switch to 2H BTC longs'"""
        import re
        
        # Extract timeframe
        tf_match = re.search(r'(\d+[mh])', command.lower())
        if tf_match:
            new_timeframe = tf_match.group(1)
            self.cmd.log(f"[CHART] Timeframe change requested: {new_timeframe}")
            
            # Update config dynamically (no restart needed)
            if 'h' in new_timeframe:
                hours = int(new_timeframe.replace('h', ''))
                config.SCAN_INTERVAL = hours * 3600  # Convert to seconds
                self.cmd.copilot_mode.setText(f"Current Mode: {new_timeframe.upper()} SCANNING")
            elif 'm' in new_timeframe:
                minutes = int(new_timeframe.replace('m', ''))
                config.SCAN_INTERVAL = minutes * 60
                self.cmd.copilot_mode.setText(f"Current Mode: {new_timeframe.upper()} SCANNING")

            asset_match = re.search(r'\b([A-Z]{2,6}(?:[-=][A-Z]{1,6})?)\b', command.upper())
            ticker_hint = asset_match.group(1) if asset_match else self.ticker_selector
            switched = self.rpa_hand.switch_timeframe(new_timeframe, ticker_hint=ticker_hint)
            self.cmd.log(
                f"{'[OK]' if switched else '[WARN]'} TradingView timeframe sync "
                f"{'completed' if switched else 'failed'} for {ticker_hint} -> {new_timeframe.upper()}"
            )
            
            self.cmd.log(f"[OK] SCAN_INTERVAL updated to {config.SCAN_INTERVAL}s")
        
        # Trigger AI analysis with user suggestion
        self._trigger_ai_analysis(command)

    def _handle_force_trade(self, command: str):
        """Handle force trade commands - RPA safety STILL APPLIES"""
        self.cmd.log(f"[WARN] FORCE TRADE requested: {command}")
        self.cmd.log(f"[STOP] RPA Safety layer ACTIVE: Slippage Guard + Spread Check will run")
        
        # Trigger AI analysis with warning
        self._trigger_ai_analysis(command)

    def _handle_news_analysis(self, command: str):
        """Handle news-based analysis requests"""
        self.cmd.log(f"[NEWS] NEWS ANALYSIS requested: {command}")
        
        # Trigger AI analysis
        self._trigger_ai_analysis(command)

    def _handle_show_levels(self, command: str):
        """STAGE 2: Generate Pine Script zones and optionally inject to TradingView"""
        self.cmd.log(f"[BUILD] SHOW LEVELS requested: {command}")
        
        # Parse asset from command
        import re
        asset_match = re.search(r'(BTC|ETH|EURUSD|GOLD|SPY|TSLA|AAPL|NVDA)', command, re.IGNORECASE)
        asset = asset_match.group(0) + "-USD" if asset_match else "BTC-USD"
        
        # Parse timeframe
        tf_match = re.search(r'(\d+[mh])', command.lower())
        timeframe = tf_match.group(1).upper() if tf_match else "2H"
        
        self.cmd.log(f"[CHART] Generating zones for {asset} on {timeframe}")
        
        # Generate sample zones (would normally come from AI analysis)
        demand_zones = [
            {"low": 69000.0, "high": 69500.0, "strength": 0.85},
            {"low": 68200.0, "high": 68800.0, "strength": 0.72}
        ]
        supply_zones = [
            {"low": 71000.0, "high": 71500.0, "strength": 0.78},
            {"low": 72000.0, "high": 72800.0, "strength": 0.65}
        ]

        self.support_resistance_levels[asset] = {
            "supports": [z["low"] for z in demand_zones],
            "resistances": [z["high"] for z in supply_zones],
        }
        
        # Generate Pine Script
        pine_code = self.code_architect.generate_pine_script_zones(
            asset=asset,
            demand_zones=demand_zones,
            supply_zones=supply_zones,
            timeframe=timeframe
        )
        
        self.cmd.log(f"[OK] Pine Script generated ({len(pine_code)} chars)")
        
        # Display in copilot chat
        self.cmd.add_copilot_response(
            thoughts=f"Generated Institutional Demand & Retail Supply zones for {asset}",
            verdict=f"SCRIPT_GENERATED",
            adjustment=f"[OK] Pine Script v6 ready. {len(demand_zones)} demand zones, {len(supply_zones)} supply zones. Click 'Inject to Chart' to auto-add to WealthCharts."
        )
        
        # Store for later injection
        self._last_pine_script = pine_code
        
        # If user said "inject" or "add to chart", do it autonomously
        if any(keyword in command.lower() for keyword in ["inject", "add to chart", "auto"]):
            self._inject_pine_script_to_chart()
        
        self.cmd.update_copilot_status("Ready")

    def _looks_like_strategy_request(self, command: str) -> bool:
        """Detect free-form strategy generation requests for Vibe-Trading."""
        cmd = command.lower().strip()
        if not cmd:
            return False

        if any(keyword in cmd for keyword in [
            "analysis mode",
            "focus mode",
            "chart mode",
            "professor mode",
            "mirror",
            "overlay",
            "support",
            "resistance",
            "show levels",
            "plot zones",
            "draw zones",
            "switch to",
            "change timeframe",
            "use timeframe",
            "buy now",
            "sell now",
            "force buy",
            "force sell",
            "force trade",
            "news",
            "sentiment",
        ]):
            return False

        if any(keyword in cmd for keyword in [
            "generate strategy",
            "build strategy",
            "create strategy",
            "pine script",
            "pine strategy",
            "strategy for",
        ]):
            return True

        style_terms = [
            "scalp",
            "breakout",
            "momentum",
            "mean reversion",
            "trend",
            "reversal",
            "long-short",
            "long short",
            "playbook",
            "setup",
        ]
        has_timeframe = bool(re.search(r"\b\d+\s*[mh]\b|\b\d+[mh]\b", cmd))
        return has_timeframe and any(term in cmd for term in style_terms)

    def _generate_vibe_strategy(self, prompt: str):
        """Generate a Pine strategy through a shielded Vibe worker."""
        self.cmd.log(f"[BRAIN] VIBE STRATEGY requested: {prompt}")
        self.cmd.update_copilot_status("Generating Vibe strategy...")

        if not self.vibe_adapter.is_available():
            self._handle_vibe_strategy_failure(prompt, "vibe-trading CLI not installed")
            return

        if self._vibe_strategy_worker and self._vibe_strategy_worker.isRunning():
            self.cmd.log("[WARN] VIBE STRATEGY already running - wait for shielded result")
            self.cmd.add_copilot_response(
                thoughts="A shielded Vibe strategy task is already in progress.",
                verdict="VIBE_BUSY",
                adjustment="[WAIT] Wait for the current Vibe subprocess to finish or timeout before sending another strategy request.",
            )
            self.cmd.update_copilot_status("Vibe busy")
            return

        command = self.vibe_adapter.command_path or self.vibe_adapter.command
        self._vibe_strategy_worker = VibeStrategyWorker(prompt, command, timeout_seconds=10)
        self._vibe_strategy_worker.strategy_ready.connect(self._handle_vibe_strategy_success)
        self._vibe_strategy_worker.strategy_failed.connect(self._handle_vibe_strategy_failure)
        self._vibe_strategy_worker.finished.connect(self._finalize_vibe_strategy_worker)
        self._set_vibe_status_ui("Active", "active")
        self._vibe_strategy_worker.start()

    def _handle_vibe_strategy_success(self, result: dict):
        """Handle a successful shielded Vibe strategy run."""
        if QThread.currentThread() != self.app.thread():
            self._run_on_ui_thread(lambda result=result: self._handle_vibe_strategy_success(result))
            return

        pine_script = result.get("pine_script")
        run_id = result.get("run_id") or "N/A"
        prompt = str(result.get("source_prompt") or "")
        self._set_vibe_status_ui("Active", "active")

        if pine_script:
            self._last_pine_script = pine_script
            self._log_ui(f"[OK] VIBE STRATEGY ready (run_id={run_id})")
            self._add_copilot_response_ui(
                thoughts=f"Vibe generated a Pine strategy for: {prompt}",
                verdict="VIBE_STRATEGY_READY",
                adjustment=f"[OK] Pine script ready from Vibe run {run_id}. Use Inject to Chart to deploy it.",
            )
            if any(keyword in prompt.lower() for keyword in ["inject", "add to chart", "auto"]):
                self._run_on_ui_thread(self._inject_pine_script_to_chart)
        else:
            self._log_ui(f"[WARN] VIBE STRATEGY run completed without Pine export (run_id={run_id})")
            self._add_copilot_response_ui(
                thoughts=f"Vibe completed the strategy run for: {prompt}",
                verdict="VIBE_RUN_COMPLETE",
                adjustment=f"[WARN] Run {run_id} completed but no Pine export was returned yet.",
            )

        self._set_copilot_status_ui("Ready")

    def _handle_vibe_strategy_failure(self, prompt: str, error: str):
        """Trip the shield and fall back to Professor logic."""
        if QThread.currentThread() != self.app.thread():
            self._run_on_ui_thread(lambda prompt=prompt, error=error: self._handle_vibe_strategy_failure(prompt, error))
            return

        reason = error or "Vibe CLI failed"
        self._set_vibe_status_ui("Fallback", "fallback")
        self._log_ui(f"[SHIELD] VIBE SHIELD: {reason} - local market intelligence is taking over")
        self._add_copilot_response_ui(
            thoughts=f"Vibe strategy generation failed for: {prompt}",
            verdict="VIBE_FALLBACK",
            adjustment=f"[WARN] {reason}. Local market intelligence is taking over automatically.",
        )
        self._set_copilot_status_ui("Local market intelligence active")
        self._trigger_ai_analysis(prompt)

    def _finalize_vibe_strategy_worker(self):
        """Release the active Vibe worker reference once it finishes."""
        if QThread.currentThread() != self.app.thread():
            self._run_on_ui_thread(self._finalize_vibe_strategy_worker)
            return

        worker = self._vibe_strategy_worker
        self._vibe_strategy_worker = None
        if worker:
            worker.deleteLater()

    def _inject_pine_script_to_chart(self):
        """STAGE 2: Auto-inject Pine Script to TradingView via BrowserAgent"""
        import asyncio
        
        if not hasattr(self, '_last_pine_script'):
            self.cmd.log("[FAIL] No Pine Script to inject")
            return
        
        if not self.browser_agent or not self.browser_agent.is_running:
            self.cmd.log("[FAIL] Browser agent not running")
            return
        
        self.cmd.log("[BUILD] Injecting Pine Script to WealthCharts...")
        self.cmd.update_copilot_status("Injecting script...")
        
        # Submit to browser event loop
        asyncio.run_coroutine_threadsafe(
            self._do_inject_pine_script(),
            self._browser_loop
        )

    async def _do_inject_pine_script(self):
        """Actually inject the Pine Script via BrowserAgent"""
        try:
            success = await self.browser_agent.inject_pine_script_to_tradingview(
                self._last_pine_script
            )
            
            if success:
                self._log_ui("[OK] Pine Script injected successfully!")
                self._add_copilot_response_ui(
                    thoughts="Pine Script added to your TradingView chart",
                    verdict="INJECTED",
                    adjustment="[OK] Zones now visible on chart. AI's thoughts are now lines on your live chart!"
                )
                self._set_copilot_status_ui("Ready")
            else:
                self._log_ui("[FAIL] Pine Script injection failed")
                self._add_copilot_response_ui(
                    thoughts="Failed to inject script",
                    verdict="INJECTION_FAILED",
                    adjustment="[WARN] Manual steps needed: Open Pine Editor (Ctrl+P), paste code, click 'Add to Chart'"
                )
                self._set_copilot_status_ui("Error")
                
        except Exception as e:
            self._log_ui(f"[FAIL] Injection error: {e}")
            self._set_copilot_status_ui("Error")

    def _handle_general_command(self, command: str):
        """Handle general Co-Pilot commands"""
        self.cmd.log(f"[CHAT] GENERAL COMMAND: {command}")
        
        # Trigger AI analysis
        self._trigger_ai_analysis(command)

    def _handle_force_strike_test_command(self, command: str):
        """Bypass analysis and physically click the calibrated BUY button immediately."""
        ticker_hint = self.current_watchlist[0] if self.current_watchlist else self.ticker_selector
        self.cmd.log(f"[BOLT] FORCE STRIKE TEST: immediate calibrated BUY click requested on {ticker_hint}")
        self.ai_narrator.add_activity("[BOLT]", f"Force strike test armed on {ticker_hint}")

        def run_force_strike_in_thread():
            try:
                success = self.rpa_hand.force_strike_test(action="BUY", ticker_hint=ticker_hint)
                if success:
                    self.cmd.log(
                        f'<span style="color:#3FB950;font-weight:bold">[BOLT] FORCE STRIKE TEST PASSED</span>: '
                        f'clicked BUY on {ticker_hint}'
                    )
                    self.ai_narrator.add_activity("[OK]", f"Force strike BUY click fired on {ticker_hint}")
                else:
                    failure_reason = self.rpa_hand.last_failure_reason or "unable to click calibrated BUY button"
                    self.cmd.log(
                        f'<span style="color:#F85149;font-weight:bold">[WARN] FORCE STRIKE TEST FAILED</span>: '
                        f'{failure_reason}'
                    )
                    self.ai_narrator.add_activity("[WARN]", f"Force strike test failed for {ticker_hint}")
            except Exception as e:
                self.cmd.log(f'<span style="color:#F85149">[FAIL] FORCE STRIKE TEST ERROR</span>: {e}')

        strike_thread = threading.Thread(target=run_force_strike_in_thread, daemon=True)
        strike_thread.start()
        self.cmd.log("[BOLT] Force strike test dispatched directly to RPA executor")

    def _trigger_ai_analysis(self, user_suggestion: str):
        """Trigger Swarm Consensus analysis with user suggestion"""
        try:
            # Get current market data for first ticker
            from core.models import MarketDataPoint

            asset = self.ticker_selector or (self.current_watchlist[0] if self.current_watchlist else "BTC-USD")
            price = 69500.0
            vibe_memory = self.sql_journal.get_vibe_penalty(asset)
            indicators = {
                "RSI": 45.0,
                "SIGNAL_TYPE": "VOLUME_SPIKE",
                "SIGNAL_STRENGTH": 0.75,
                "RECENT_CANDLES": [],
                "LIQUIDITY_ZONE": "N/A",
                "VIBE_MEMORY_SUMMARY": vibe_memory.get("summary", ""),
                "VIBE_MEMORY_PENALTY": float(vibe_memory.get("penalty", 0.0)),
            }
            volume = 0.0

            try:
                scanner = getattr(self.cloud_scanner, "scanner", None)
                cached_df = None
                if scanner is not None:
                    preferred_keys = [
                        (asset, "1d", "1m"),
                        (asset, "2d", "1m"),
                        (asset, "2d", "5m"),
                    ]
                    for key in preferred_keys:
                        cached_df = scanner.market_data_cache.get(key)
                        if cached_df is not None and not cached_df.empty:
                            break
                    if cached_df is None:
                        for (ticker_key, _, _), df in scanner.market_data_cache.items():
                            if ticker_key == asset and df is not None and not df.empty:
                                cached_df = df
                                break

                if cached_df is not None and not cached_df.empty:
                    recent = cached_df.tail(10)
                    if "Close" in recent.columns and not recent["Close"].dropna().empty:
                        price = float(recent["Close"].dropna().iloc[-1])
                    if "Volume" in recent.columns and not recent["Volume"].dropna().empty:
                        volume = float(recent["Volume"].dropna().iloc[-1])
                    if "RSI" in recent.columns and not recent["RSI"].dropna().empty:
                        indicators["RSI"] = float(recent["RSI"].dropna().iloc[-1])
                    candle_lines = []
                    for index, row in recent.iterrows():
                        try:
                            candle_lines.append(
                                f"{index.strftime('%H:%M')} O:{float(row.get('Open', 0.0)):.2f} "
                                f"H:{float(row.get('High', 0.0)):.2f} "
                                f"L:{float(row.get('Low', 0.0)):.2f} "
                                f"C:{float(row.get('Close', 0.0)):.2f}"
                            )
                        except Exception:
                            continue
                    indicators["RECENT_CANDLES"] = candle_lines[-10:]
                    if scanner is not None:
                        liquidity_zone = scanner._detect_liquidity_zone(cached_df, asset)
                        if liquidity_zone:
                            indicators["LIQUIDITY_ZONE"] = (
                                f"{liquidity_zone.get('type', 'zone')} @ {float(liquidity_zone.get('level', 0.0)):.4f}"
                            )

                latest_signal = self.latest_signals.get(asset)
                if latest_signal:
                    latest_entry = getattr(latest_signal, "entry_price", None)
                    if latest_entry not in (None, 0, 0.0):
                        price = float(latest_entry)
                    indicators["SIGNAL_TYPE"] = getattr(latest_signal, "action", indicators["SIGNAL_TYPE"])
                    indicators["SIGNAL_STRENGTH"] = float(self.latest_confidence_score / 100.0) if self.latest_confidence_score else indicators["SIGNAL_STRENGTH"]
            except Exception as e:
                logger.warning("Co-Pilot data collection fallback activated for %s: %s", asset, e)
                indicators.setdefault("DATA_COLLECTION", "N/A")

            market_data = MarketDataPoint(
                asset=asset or "BTC-USD",
                price=price or 0.0,
                volume=volume,
                timestamp=datetime.now(timezone.utc),
                indicators=indicators,
            )
            
            # Run analysis with user suggestion
            import asyncio

            loop = getattr(self, '_browser_loop', None)
            if loop is None or loop.is_closed():
                self.cmd.log("[WARN] AI engine not ready yet — please wait a moment and try again")
                self.cmd.update_copilot_status("Warming up...")
                return

            future = asyncio.run_coroutine_threadsafe(
                self._run_copilot_analysis(market_data, user_suggestion),
                loop,
            )
            # Check for errors from the async coroutine
            def _check_future(fut):
                try:
                    exc = fut.exception(timeout=0)
                    if exc:
                        logger.error("Co-Pilot async analysis failed: %s", exc)
                        self._run_on_ui_thread(lambda: self.cmd.log(f"[FAIL] AI analysis error: {exc}"))
                        self._run_on_ui_thread(lambda: self.cmd.add_copilot_response(
                            thoughts="AI engine returned an error",
                            verdict="ERROR",
                            adjustment=str(exc),
                        ))
                        self._run_on_ui_thread(lambda: self.cmd.update_copilot_status("Error"))
                except Exception:
                    pass
            future.add_done_callback(_check_future)
            
        except Exception as e:
            self.cmd.log(f"[FAIL] Co-Pilot analysis failed: {e}")
            self.cmd.add_copilot_response(
                thoughts="System error occurred",
                verdict="ERROR",
                adjustment=f"Failed to process command: {e}"
            )
            self.cmd.update_copilot_status("Error")

    def _confidence_to_score(self, confidence_value) -> float:
        """Convert confidence value into a 0-100 score for Professor Mode gating."""
        if isinstance(confidence_value, (int, float)):
            if confidence_value <= 1:
                return float(confidence_value) * 100.0
            return float(confidence_value)

        if isinstance(confidence_value, str):
            mapping = {
                "LOW": 60.0,
                "MEDIUM": 80.0,
                "HIGH": 90.0,
                "VERY_HIGH": 96.0,
            }
            return mapping.get(confidence_value.strip().upper(), 0.0)

        return 0.0

    def _is_loud_signal(self, confidence_score: float, signal_data: dict | None = None) -> bool:
        """True only for 85%+ signals or signals promoted by swarm incubation."""
        signal_data = signal_data or {}
        if bool(signal_data.get("swarm_incubated")) or str(signal_data.get("incubation_route", "")).lower() == "promoted":
            return True
        threshold = float(getattr(config, "SWARM_HIGH_PRIORITY_THRESHOLD", 85.0))
        return float(confidence_score or 0.0) >= threshold

    def _is_incubation_signal(self, confidence_score: float, signal_data: dict | None = None) -> bool:
        """True for muted 60-84% signals that have not earned incubation promotion."""
        if self._is_loud_signal(confidence_score, signal_data):
            return False
        floor = float(getattr(config, "SWARM_INCUBATION_FLOOR", 60.0))
        return floor <= float(confidence_score or 0.0) < float(getattr(config, "SWARM_HIGH_PRIORITY_THRESHOLD", 85.0))

    def _extract_signal_tag(self, *parts) -> str:
        """Extract explicit [SIGNAL] BUY/SELL/WAIT output, falling back to standard action words."""
        import re

        for part in parts:
            text = str(part or "")
            match = re.search(r"\[SIGNAL\]\s*:?\s*(BUY|SELL|WAIT|HOLD)\b", text, re.IGNORECASE)
            if match:
                signal = match.group(1).upper()
                return "HOLD" if signal in {"WAIT", "HOLD"} else signal

        combined = " ".join(str(part or "") for part in parts).upper()
        if " WAIT" in f" {combined} ":
            return "HOLD"
        if " SELL" in f" {combined} ":
            return "SELL"
        if " BUY" in f" {combined} ":
            return "BUY"
        return "HOLD"

    def _signal_label(self, action: str) -> str:
        """Map internal HOLD semantics to the user-facing WAIT label."""
        return "WAIT" if str(action or "").upper() == "HOLD" else str(action or "").upper()

    def _brain_override_action(self, signal_data: dict) -> str:
        """Return BUY/SELL when OpenRouter explicitly approved execution."""
        verdict = str(signal_data.get("brain_verdict", "") or "").strip().upper()
        if verdict == "[SIGNAL] BUY":
            return "BUY"
        if verdict == "[SIGNAL] SELL":
            return "SELL"
        return "HOLD"

    def _has_brain_override(self, signal_data: dict) -> bool:
        """True when OpenRouter explicitly approved immediate execution."""
        return self._brain_override_action(signal_data) in {"BUY", "SELL"}

    def _dispatch_copilot_signal(self, signal_data: dict):
        """Route Co-Pilot BUY/SELL output through the normal teacher/autonomous execution path."""
        if signal_data.get("force_execute"):
            self._on_signal_approved(signal_data)
            return

        if self.current_mode == "AUTONOMOUS":
            self._on_signal_approved(signal_data)
            return

        dialog = SignalApprovalDialog(signal_data, self.cmd)
        dialog.approved.connect(self._on_signal_approved)
        dialog.rejected.connect(self._on_signal_rejected)
        dialog.exec()

    def _resolve_directional_action(self, signal_data: dict) -> str:
        """
        Resolve trade direction from action/signal context.

        Priority rule for Professor Mode discipline:
        - If any bearish marker appears, force SELL
        - Else if any bullish marker appears, force BUY
        - Else fallback to explicit action if valid
        """
        tagged_signal = self._extract_signal_tag(
            signal_data.get("llm_signal"),
            signal_data.get("reason"),
            signal_data.get("ceo_verdict"),
            signal_data.get("action"),
        )
        if tagged_signal in {"BUY", "SELL"}:
            return tagged_signal
        if tagged_signal == "HOLD":
            return "HOLD"

        raw_action = str(signal_data.get("action", "")).upper().strip()
        signal_type = str(signal_data.get("signal_type", "")).upper()
        reason = str(signal_data.get("reason", "")).upper()
        combined = f"{raw_action} {signal_type} {reason}"

        if "BEARISH" in combined or "SELL" in combined:
            return "SELL"
        if "BULLISH" in combined or "BUY" in combined:
            return "BUY"
        if raw_action in {"BUY", "SELL"}:
            return raw_action
        return "HOLD"

    def _passes_mtf_sniper_gate(self, ticker: str, action: str, signal_data: Optional[dict] = None, confidence: float = 0.0) -> tuple[bool, dict]:
        """5m/3m/1m sniper gate with oversold reversal override for BUY conflicts.

        AGGRESSIVE HUNTER: If confidence >= AGGRESSIVE_HUNTER_CONFIDENCE_PCT (default 75%),
        skip 1m/3m alignment and strike on 5m chart alone."""
        action = str(action or "").upper()
        if action not in {"BUY", "SELL"}:
            return False, {}

        # TOTAL YAHOO BAN: M6 futures and Gold/XAUUSD are NOT on Yahoo Finance
        if ticker and (ticker.upper().endswith("M6") or ticker.upper() in {"XAUUSD", "GC", "GC=F", "MGC", "COMEX:MGC1!"}):
            logger.info("[MTF GATE] Bypassing Yahoo Finance for %s — using MT5 price gate", ticker)
            return True, {"bypass": "mt5_only_ticker"}

        try:
            import yfinance as yf
            import pandas_ta as ta
            import pandas as pd
        except Exception as e:
            logger.error("MTF gate unavailable (dependency import failed): %s", e)
            return False, {"error": "dependency_unavailable"}

        tf_map = [
            ("5m", "2d", "5m"),
            ("3m", "2d", "3m"),
            ("1m", "1d", "1m"),
        ]
        votes = {}
        timeframe_rsi = {}
        market_ticker = self._canonical_market_ticker(ticker)

        transcript = dict((signal_data or {}).get("transcript") or {})
        technical_sniper = dict(transcript.get("technical_sniper") or {})
        scanner_action = str(technical_sniper.get("action", "") or "").upper()
        brain_verdict = str((signal_data or {}).get("brain_verdict", "") or "").upper()
        ai_scanner_agree = action == "BUY" and scanner_action == "BUY" and "BUY" in brain_verdict

        def _resample_to_3m(df_1m):
            if df_1m is None or df_1m.empty:
                return None
            try:
                out = pd.DataFrame()
                out["Open"] = df_1m["Open"].resample("3min").first()
                out["High"] = df_1m["High"].resample("3min").max()
                out["Low"] = df_1m["Low"].resample("3min").min()
                out["Close"] = df_1m["Close"].resample("3min").last()
                out["Volume"] = df_1m["Volume"].resample("3min").sum()
                return out.dropna()
            except Exception:
                return None

        for label, period, interval in tf_map:
            try:
                if interval == "3m":
                    one_min_df = yf.Ticker(market_ticker).history(period=period, interval="1m")
                    df = _resample_to_3m(one_min_df)
                else:
                    df = yf.Ticker(market_ticker).history(period=period, interval=interval)
            except Exception as e:
                logger.warning("MTF %s fetch failed for %s via %s: %s", label, ticker, market_ticker, e)
                votes[label] = "WAIT"
                continue

            if df is None or df.empty or len(df) < 30:
                votes[label] = "WAIT"
                continue

            required_cols = {"Open", "High", "Low", "Close", "Volume"}
            if not required_cols.issubset(df.columns):
                votes[label] = "WAIT"
                continue

            if df[list(required_cols)].tail(1).isnull().any(axis=1).iloc[0]:
                logger.info("[WAIT] Local MTF wait-mode: %s %s has partial candle data", ticker, label)
                votes[label] = "WAIT"
                continue

            fast = ta.sma(df["Close"], length=9)
            slow = ta.sma(df["Close"], length=21)
            rsi = ta.rsi(df["Close"], length=14)

            f = fast.iloc[-1]
            s = slow.iloc[-1]
            r = rsi.iloc[-1]
            if pd.isna(f) or pd.isna(s) or pd.isna(r):
                votes[label] = "WAIT"
            elif f > s and r >= 50:
                votes[label] = "BUY"
            elif f < s and r <= 50:
                votes[label] = "SELL"
            else:
                votes[label] = "WAIT"
            timeframe_rsi[label] = float(r) if not pd.isna(r) else None

        # Require 2 out of 3 timeframes to agree (AUTONOMOUS mode)
        matching = sum(1 for tf in ["5m", "3m", "1m"] if votes.get(tf) == action)
        passed = matching >= 2
        buy_votes = sum(1 for tf in ["5m", "3m", "1m"] if votes.get(tf) == "BUY")
        sell_votes = sum(1 for tf in ["5m", "3m", "1m"] if votes.get(tf) == "SELL")
        one_min_rsi = timeframe_rsi.get("1m")
        five_min_rsi = timeframe_rsi.get("5m")
        mtf_bias = "SELL" if sell_votes > buy_votes else "BUY" if buy_votes > sell_votes else "MIXED"
        votes["1m_rsi"] = round(one_min_rsi, 2) if isinstance(one_min_rsi, (int, float)) else None
        votes["5m_rsi"] = round(five_min_rsi, 2) if isinstance(five_min_rsi, (int, float)) else None
        votes["mtf_bias"] = mtf_bias

        # AGGRESSIVE HUNTER: High-confidence BUY signals can strike on 5m alone.
        # SELL signals require more confirmation to avoid shorting BTC/ETH
        # continuation moves after equal-high liquidity tags.
        hunter_threshold = getattr(config, "AGGRESSIVE_HUNTER_CONFIDENCE_PCT", 75.0)
        if confidence >= hunter_threshold and votes.get("5m") == action and action == "BUY":
            votes["override"] = "AGGRESSIVE_HUNTER"
            logger.warning(
                "[FIRE] AGGRESSIVE HUNTER: %s %s confidence=%.1f%% >= %.1f%% — striking on 5m alone | votes=%s",
                action, ticker, confidence, hunter_threshold, votes,
            )
            return True, votes

        # Strong 5m BUY override: let the higher timeframe lead if 1m is neutral.
        strong_5m_buy = (
            action == "BUY"
            and votes.get("5m") == "BUY"
            and votes.get("1m") == "WAIT"
            and isinstance(five_min_rsi, (int, float))
            and five_min_rsi >= getattr(config, "MTF_STRONG_BUY_RSI", 58.0)
        )
        if (
            not passed
            and getattr(config, "MTF_ALLOW_STRONG_5M_BUY_WITH_NEUTRAL_1M", True)
            and strong_5m_buy
        ):
            votes["override"] = "STRONG_5M_BUY_NEUTRAL_1M"
            logger.warning(
                "[TARGET] STRONG 5M BUY OVERRIDE: BUY %s allowed with neutral 1m because 5m RSI is %.2f | votes=%s",
                ticker,
                five_min_rsi,
                votes,
            )
            return True, votes

        # LIQUIDITY SIGNAL OVERRIDE: SMAs naturally oppose sweep direction.
        # If scanner already approved the liquidity signal, don't let lagging MTF block it
        # unless the 5m trend is strongly opposed.
        signal_type = str((signal_data or {}).get("signal_type", "")).upper()
        is_liquidity = "LIQUIDITY" in signal_type
        if not passed and is_liquidity:
            five_m_opposite = votes.get("5m") == ("SELL" if action == "BUY" else "BUY")
            if action == "SELL":
                logger.info(
                    "[TARGET] SELL liquidity override disabled for %s; requiring 2/3 MTF confirmation | votes=%s",
                    ticker,
                    votes,
                )
            elif not five_m_opposite:
                votes["override"] = "LIQUIDITY_RELAXED_MTF"
                logger.warning(
                    "[TARGET] LIQUIDITY OVERRIDE: %s %s allowed against MTF because liquidity sweep/rejection signal detected and 5m is not opposing | votes=%s",
                    action,
                    ticker,
                    votes,
                )
                return True, votes

        if (
            not passed
            and action == "BUY"
            and ai_scanner_agree
            and mtf_bias == "SELL"
            and isinstance(one_min_rsi, (int, float))
            and one_min_rsi < 30.0
        ):
            votes["override"] = "REVERSAL_BUY_1M_RSI_OVERSOLD"
            logger.warning(
                "[TARGET] REVERSAL OVERRIDE: BUY %s allowed against SELL-biased MTF because AI+Scanner agree and 1m RSI is oversold (%.2f)",
                ticker,
                one_min_rsi,
            )
            return True, votes

        return passed, votes

    def _parse_confidence_line(self, text: str) -> Optional[float]:
        """Extract confidence percentage from a line like: Confidence: 92%."""
        if not text:
            return None
        match = re.search(r"(?im)^\s*confidence\s*:\s*(\d{1,3}(?:\.\d+)?)\s*%", str(text))
        if not match:
            return None
        try:
            return max(0.0, min(100.0, float(match.group(1))))
        except ValueError:
            return None

    def _resolve_analysis_confidence(self, output, transcript) -> float:
        """Resolve confidence from AI text first, then fallback to structured fields."""
        text_candidates = []
        text_candidates.append(getattr(output, "reason", ""))
        text_candidates.append(getattr(transcript, "ceo_full_statement", ""))
        text_candidates.append(getattr(transcript, "cto_full_statement", ""))
        text_candidates.append(getattr(transcript, "cfo_full_statement", ""))

        for text in text_candidates:
            parsed = self._parse_confidence_line(text)
            if parsed is not None:
                return parsed

        confidence_obj = getattr(output, "confidence", None)
        if confidence_obj is not None:
            raw_value = getattr(confidence_obj, "value", confidence_obj)
            return self._confidence_to_score(raw_value)

        return 0.0

    async def _run_copilot_analysis(self, market_data, user_suggestion: str):
        """Run AI analysis with user suggestion and display response"""
        try:
            # Import swarm consensus
            from core.swarm_consensus import OllamaSwarmConsensus
            from core.models import MarketDataPoint

            try:
                data_snapshot = {
                    "asset": getattr(market_data, "asset", "N/A") or "N/A",
                    "price": getattr(market_data, "price", "N/A") if getattr(market_data, "price", None) not in (None, "") else "N/A",
                    "volume": getattr(market_data, "volume", "N/A") if getattr(market_data, "volume", None) not in (None, "") else "N/A",
                    "rsi": (getattr(market_data, "indicators", {}) or {}).get("RSI", "N/A"),
                    "signal_type": (getattr(market_data, "indicators", {}) or {}).get("SIGNAL_TYPE", "N/A"),
                    "signal_strength": (getattr(market_data, "indicators", {}) or {}).get("SIGNAL_STRENGTH", "N/A"),
                }
            except Exception as e:
                logger.warning("Co-Pilot snapshot fallback activated: %s", e)
                data_snapshot = {
                    "asset": "N/A",
                    "price": "N/A",
                    "volume": "N/A",
                    "rsi": "N/A",
                    "signal_type": "N/A",
                    "signal_strength": "N/A",
                }

            safe_market_data = MarketDataPoint(
                asset=data_snapshot["asset"] if data_snapshot["asset"] != "N/A" else "BTC-USD",
                price=float(data_snapshot["price"]) if isinstance(data_snapshot["price"], (int, float)) else 0.0,
                volume=float(data_snapshot["volume"]) if isinstance(data_snapshot["volume"], (int, float)) else 0.0,
                indicators={
                    "RSI": float(data_snapshot["rsi"]) if isinstance(data_snapshot["rsi"], (int, float)) else 50.0,
                    "SIGNAL_TYPE": data_snapshot["signal_type"],
                    "SIGNAL_STRENGTH": float(data_snapshot["signal_strength"]) if isinstance(data_snapshot["signal_strength"], (int, float)) else 0.0,
                    "RECENT_CANDLES": (getattr(market_data, "indicators", {}) or {}).get("RECENT_CANDLES", []),
                    "LIQUIDITY_ZONE": (getattr(market_data, "indicators", {}) or {}).get("LIQUIDITY_ZONE", "N/A"),
                    "VIBE_MEMORY_SUMMARY": (getattr(market_data, "indicators", {}) or {}).get("VIBE_MEMORY_SUMMARY", ""),
                    "VIBE_MEMORY_PENALTY": float((getattr(market_data, "indicators", {}) or {}).get("VIBE_MEMORY_PENALTY", 0.0)),
                },
            )

            copilot_context = (
                f"{user_suggestion}\n\n"
                f"PRICE ACTION SNAPSHOT:\n"
                f"- Asset: {data_snapshot['asset']}\n"
                f"- RSI: {data_snapshot['rsi']}\n"
                f"- Nearest Liquidity: {(getattr(market_data, 'indicators', {}) or {}).get('LIQUIDITY_ZONE', 'N/A')}\n"
                f"- Last 10 Candles:\n  - " + "\n  - ".join((getattr(market_data, 'indicators', {}) or {}).get('RECENT_CANDLES', [])[:10] or ["N/A"]) + "\n"
                f"- Reply with explicit [SIGNAL] BUY, SELL, or WAIT in your verdict/reasoning."
            )
            
            # Initialize if not exists
            if not hasattr(self, 'copilot_swarm'):
                self.copilot_swarm = OllamaSwarmConsensus()
            
            # Run analysis with user suggestion
            output, transcript = await self.copilot_swarm.run(
                market_data=safe_market_data,
                user_suggestion=copilot_context,
                skip_vibe_debate=self.force_action_armed,
            )

            confidence_score = self._resolve_analysis_confidence(output, transcript)
            self.latest_confidence_score = confidence_score
            vibe_context = dict(getattr(transcript, "vibe_context", {}) or {})
            vibe_context.setdefault(
                "memory_summary",
                (getattr(market_data, "indicators", {}) or {}).get("VIBE_MEMORY_SUMMARY", ""),
            )
            vibe_context.setdefault(
                "confidence_penalty",
                float((getattr(market_data, "indicators", {}) or {}).get("VIBE_MEMORY_PENALTY", 0.0)),
            )
            llm_signal = self._extract_signal_tag(
                output.reason,
                getattr(output, "action", "HOLD"),
                getattr(transcript, "ceo_verdict", ""),
                getattr(transcript, "ceo_full_statement", ""),
            )
            signal_label = self._signal_label(llm_signal)
            signal_banner = f"[SIGNAL] {signal_label}"
            force_action_override = self.force_action_armed and llm_signal in {"BUY", "SELL"}
            self.analysis_mode_status = "APPROVED" if (confidence_score >= config.MIN_CONFIDENCE_THRESHOLD or force_action_override) and llm_signal in {"BUY", "SELL"} else "READY"
            
            # Parse AI response
            thoughts = f"{signal_banner} {output.reason}"
            verdict = f"{signal_banner} (Confidence: {output.confidence.value})"
            
            # Check if there's user_verdict in the response
            user_verdict = "AGREE"
            user_explanation = ""
            
            # Try to extract user-specific fields from transcript
            if hasattr(transcript, 'ceo_full_statement'):
                # The AI should have included user_verdict in JSON response
                # For now, use the reason field
                user_explanation = thoughts
            
            # Detect agreement/conflict based on analysis
            if output.confidence.value in ["HIGH", "VERY_HIGH"]:
                user_verdict = "AGREE"
            elif output.confidence.value == "LOW":
                user_verdict = "DISAGREE"
            else:
                user_verdict = "FORCE_WITH_WARNING"
            
            # Format response for UI
            adjustment = ""
            if confidence_score < 85 and not force_action_override:
                self._log_ui("Trade Ignored: Low AI Confidence")
                self.analysis_mode_status = "REJECTED - Low Confidence"
                self._set_copilot_status_ui(self.analysis_mode_status)
                self._run_on_ui_thread(lambda: self.ai_narrator.set_status("error", self.analysis_mode_status))
                adjustment = f"Trade blocked by Professor confidence gate (< 85). {signal_banner} preserved for review."
            elif force_action_override:
                adjustment = f"[DICE] Gambler Mode override active. Executing {signal_banner} despite confidence {confidence_score:.0f}/100."
            elif user_verdict == "DISAGREE":
                adjustment = f"[WARN] Safer alternative: Wait for better entry zone. {user_explanation}"
            elif user_verdict == "FORCE_WITH_WARNING":
                adjustment = f"[WARN] Proceeding with caution. {user_explanation}"
            elif signal_label == "WAIT":
                adjustment = "[PAUSE] No execution. Co-Pilot returned [SIGNAL] WAIT."
            else:
                entry_value = output.entry_price if output.entry_price is not None else safe_market_data.price
                adjustment = f"[OK] Your suggestion aligns with technical analysis. Entry: ${entry_value:.2f}"
            
            # Display in copilot chat
            self._add_copilot_response_ui(
                thoughts=thoughts,
                verdict=verdict,
                adjustment=adjustment
            )

            if (confidence_score >= 85 or force_action_override) and llm_signal in {"BUY", "SELL"}:
                signal_data = {
                    "ticker": safe_market_data.asset,
                    "action": llm_signal,
                    "llm_signal": signal_banner,
                    "confidence": output.confidence.value,
                    "entry_price": float(output.entry_price or safe_market_data.price or 0.0),
                    "stop_loss": float(output.stop_loss or 0.0) if output.stop_loss is not None else 0.0,
                    "take_profit": float(output.take_profit or 0.0) if output.take_profit is not None else 0.0,
                    "reason": f"{signal_banner} {output.reason}",
                    "ceo_verdict": getattr(transcript, "ceo_verdict", signal_banner),
                    "signal_type": safe_market_data.indicators.get("SIGNAL_TYPE", "COPILOT"),
                    "investment_amount": self.default_investment,
                    "force_execute": force_action_override,
                    "vibe_context": vibe_context,
                }
                if force_action_override:
                    self.force_action_armed = False
                    self.ai_narrator.set_aggression_mode(False)
                    self._log_ui(f"[DICE] Gambler Mode consumed by {signal_banner} for {safe_market_data.asset}")
                else:
                    self._log_ui(f"[ROBOT] Co-Pilot emitted {signal_banner} for {safe_market_data.asset}")
                self._run_on_ui_thread(lambda data=signal_data: self._dispatch_copilot_signal(data))
            else:
                if self.force_action_armed and signal_label == "WAIT":
                    self._log_ui("[DICE] Gambler Mode still armed - waiting for next BUY/SELL signal")
                self._log_ui(f"[ROBOT] Co-Pilot emitted {signal_banner} - no RPA execution requested")
            
            self._set_copilot_status_ui("Ready")
            
        except Exception as e:
            self._log_ui(f"[FAIL] Co-Pilot analysis error: {e}")
            self._add_copilot_response_ui(
                thoughts="Analysis failed",
                verdict="ERROR",
                adjustment=f"Error: {str(e)}"
            )
            self._set_copilot_status_ui("Error")


    def _on_test_browser(self):
        """Handle test browser click request."""
        import asyncio
        
        self.cmd.log("[TEST] TEST: Testing browser agent...")
        
        if not self.browser_agent or not self.browser_agent.is_running:
            self.cmd.log("[FAIL] TEST: Browser agent not running - launching now...")
            # Can't use ensure_future here since loop is in different thread
            # Just log and wait for auto-start
            return
        
        # Submit coroutine to browser's event loop
        asyncio.run_coroutine_threadsafe(
            self._test_browser_navigation(),
            self._browser_loop
        )

    async def _test_browser_navigation(self):
        """Test browser navigation and price fetching."""
        try:
            self._log_ui("[TEST] TEST: Navigating to BTC-USD chart...")
            await self.browser_agent.navigate_to_chart("BTC-USD")
            
            self._log_ui("[TEST] TEST: Fetching live price...")
            price = await self.browser_agent.get_live_price()
            
            if price > 0:
                self._log_ui(f"[OK] TEST SUCCESS: Browser fetched BTC-USD @ ${price:.2f}")
                self.test_status_text = f"Browser working - BTC @ ${price:.2f}"
            else:
                self._log_ui("[FAIL] TEST FAILED: Price fetch returned 0")
                self.test_status_text = "Browser test failed"
        except Exception as e:
            self._log_ui(f"[FAIL] TEST ERROR: {e}")
            self.test_status_text = f"Error: {e}"

    def _on_force_test_trade(self):
        """Force a single BUY through the active surface (TradingView or MT5).

        This is a smoke test — it bypasses confidence and risk gates so you can
        verify the click/order pipeline against your paper account.
        """
        import threading

        ticker_hint = self.current_watchlist[0] if self.current_watchlist else self.ticker_selector
        surface = get_active_surface()
        self.cmd.log(f"[BOLT] FORCE TEST: BUY {ticker_hint} via {surface}")
        self.ai_narrator.add_activity("[MOUSE]", f"Force test: BUY {ticker_hint} on {surface}")

        def run_force_test_in_thread():
            try:
                router = SurfaceRouter(
                    mt5_executor=self.mt5_executor,
                    ghost_executor=self._ghost_executor,
                )
                browser_loop = getattr(self, "_browser_loop", None)
                result = router.execute(
                    symbol=ticker_hint,
                    action="BUY",
                    quantity=float(getattr(config, "MT5_VOLUME", 0.1) or 0.1),
                    browser_loop=browser_loop,
                )
                if result.success:
                    self._log_ui(
                        f'<span style="color:#3FB950;font-weight:bold">[OK] FORCE TEST PASSED</span>: '
                        f'{result.surface} accepted BUY {ticker_hint} in {result.latency_ms:.1f}ms'
                    )
                    self._activity_ui("[OK]", f"Force test {result.surface} BUY {ticker_hint}")
                else:
                    self._log_ui(
                        f'<span style="color:#F85149;font-weight:bold">[WARN] FORCE TEST FAILED</span> on {result.surface}: '
                        f'{result.message}'
                    )
                    self._activity_ui("[WARN]", f"Force test {result.surface} failed: {result.message[:80]}")
            except Exception as e:
                self._log_ui(f'<span style="color:#F85149">[FAIL] FORCE TEST ERROR</span>: {e}')

        threading.Thread(target=run_force_test_in_thread, daemon=True).start()
        self.cmd.log(f"[BOLT] Force test dispatched to {surface}")

    def _verify_ghost_hand_socket_async(self):
        """No-op preserved for backward compatibility.

        The legacy port-5555 socket has been retired. The router now talks
        directly to MT5 (native API) or TradingView Desktop (CDP/JS) so there
        is nothing to handshake here. Kept as a stub so older callers that
        may still reference it don't crash.
        """
        return None

    def _on_rpa_blind(self, reason: Optional[str] = None):
        """
        Safety interlock callback: fired by RPAExecutor when TradingView is
        minimized or covered by another app. Displays a prominent alert on the
        mirror overlay so the user knows the bot cannot see the chart.
        """
        msg = "CHART HIDDEN - CANNOT EXECUTE"
        reason_txt = reason or "window visibility interlock triggered"
        logger.error(f"[RPA INTERLOCK] {msg} | {reason_txt}")
        self._log_gatekeeper_abort("Visibility", reason_txt)

        try:
            screen = self.app.primaryScreen()
            if screen and self.ai_narrator:
                geo = screen.availableGeometry()
                x = geo.left() + max(0, (geo.width() - self.ai_narrator.width()) // 2)
                y = geo.top() + max(0, (geo.height() - self.ai_narrator.height()) // 2)
                self.ai_narrator.move(x, y)
        except Exception:
            pass

        self.cmd.log(
            f'<span style="color:#F85149;font-weight:bold;font-size:14px">'
            f'\u26d4 {msg}</span>'
        )
        try:
            self.ai_narrator.add_activity("\u26d4", msg)
            self.ai_narrator.notify_error(msg)
        except Exception:
            pass

    def _refresh_live_ledger(self):
        """Push current stats to the mirror's Live Ledger strip."""
        active = len(self.positions)
        unrealized = sum(p.get("pnl", 0.0) for p in self.positions)
        total_closed = self.trades_today
        rate = (self.daily_wins / total_closed * 100.0) if total_closed > 0 else 0.0
        self.ai_narrator.update_live_ledger(
            active_trades=active,
            unrealized_pnl=unrealized,
            daily_success_rate=rate,
        )
        self.ai_narrator.set_daily_bullets(self.trades_today, config.MAX_DAILY_TRADES)
        self._refresh_lockout_timer()

    def _refresh_lockout_timer(self):
        """Keep the Mirror lockout HUD aligned with the next expiring cooldown."""
        now = time.time()
        active_lockouts = {}
        nearest_ticker = ""
        nearest_remaining = None

        for ticker, started_at in self.locked_tickers.items():
            remaining = int((config.RE_ENTRY_LOCKOUT_MINUTES * 60) - (now - started_at))
            if remaining <= 0:
                continue
            active_lockouts[ticker] = started_at
            if nearest_remaining is None or remaining < nearest_remaining:
                nearest_ticker = ticker
                nearest_remaining = remaining

        self.locked_tickers = active_lockouts
        if nearest_remaining is None:
            self.ai_narrator.clear_lockout_timer()
        else:
            self.ai_narrator.update_lockout_timer(nearest_remaining, nearest_ticker)

    def _check_rsi_veto(self, action: str, rsi_value: float):
        """Return veto label + reason when the setup is too stretched to strike."""
        if action == "BUY" and rsi_value > config.RSI_VETO_THRESHOLD:
            return True, f"RSI overbought ({rsi_value:.1f})", "rsi_veto_overbought"
        if action == "SELL" and rsi_value < 20:
            return True, f"RSI oversold ({rsi_value:.1f})", "rsi_veto_oversold"
        return False, "", ""

    def _manual_risk_targets(self, action: str, entry_price: float) -> tuple[float, float]:
        """Build fixed-percentage SL/TP levels when AUTO-RISK is disabled."""
        stop_pct = float(self.settings.get("stop_loss_pct", 1.0) or 1.0)
        take_pct = float(self.settings.get("take_profit_pct", 2.0) or 2.0)
        if action == "SELL":
            stop_loss = entry_price * (1 + stop_pct / 100.0)
            take_profit = entry_price * (1 - take_pct / 100.0)
        else:
            stop_loss = entry_price * (1 - stop_pct / 100.0)
            take_profit = entry_price * (1 + take_pct / 100.0)
        return float(stop_loss), float(take_profit)

    def _resolve_trade_entry_price(self, ticker: str, fallback_price: float = 0.0) -> float:
        """Resolve a live-ish entry price for simple BUY/SELL paths."""
        if fallback_price and fallback_price > 0:
            return float(fallback_price)

        try:
            mt5_price = self._fetch_mt5_price_for_m6(ticker)
            if mt5_price and mt5_price > 0:
                return float(mt5_price)
        except Exception as exc:
            logger.debug("[PRICE] MT5 price lookup failed for %s: %s", ticker, exc)

        try:
            scanner = getattr(self, "cloud_scanner", None)
            cache = getattr(scanner, "market_data_cache", {}) if scanner else {}
            for (ticker_key, _, _), df in cache.items():
                if str(ticker_key).upper() != str(ticker).upper() or df is None or df.empty:
                    continue
                close = df.get("Close")
                if close is not None and not close.dropna().empty:
                    return float(close.dropna().iloc[-1])
        except Exception as exc:
            logger.debug("[PRICE] scanner cache lookup failed for %s: %s", ticker, exc)

        try:
            import yfinance as yf
            market_ticker = self._canonical_market_ticker(ticker)
            history = yf.Ticker(market_ticker).history(period="1d", interval="1m")
            if not history.empty and "Close" in history and not history["Close"].dropna().empty:
                return float(history["Close"].dropna().iloc[-1])
        except Exception as exc:
            logger.debug("[PRICE] yfinance price lookup failed for %s: %s", ticker, exc)

        return 0.0

    def _build_simple_execution_plan(
        self,
        symbol: str,
        action: str,
        reason: str = "",
        entry_price: float = 0.0,
    ) -> dict:
        """Build a minimal protected trade plan for direct Hunter BUY/SELL execution."""
        side = str(action or "").upper()
        if side not in {"BUY", "SELL"}:
            return {"ok": False, "reason": f"Unsupported action: {action}"}

        entry = self._resolve_trade_entry_price(symbol, entry_price)
        if entry <= 0:
            return {
                "ok": False,
                "reason": "No live entry price available; refusing naked order.",
            }

        atr_proxy = max(entry * 0.005, 0.01)
        stop_distance = atr_proxy * float(getattr(config, "ATR_STOP_MULTIPLIER", 1.5))
        target_distance = atr_proxy * float(getattr(config, "ATR_TP_MULTIPLIER", 3.0))

        if side == "BUY":
            stop_loss = entry - stop_distance
            take_profit = entry + target_distance
        else:
            stop_loss = entry + stop_distance
            take_profit = entry - target_distance

        if stop_loss <= 0 or take_profit <= 0:
            return {"ok": False, "reason": "Risk plan produced invalid stop/target."}

        quantity = float(getattr(config, "MT5_VOLUME", 0.1) or 0.1)
        return {
            "ok": True,
            "symbol": symbol,
            "action": side,
            "entry_price": float(entry),
            "stop_loss": float(stop_loss),
            "take_profit": float(take_profit),
            "quantity": quantity,
            "reason": reason or "Predator/Hunter protected execution plan.",
            "risk_text": (
                f"{side} {symbol} @ ${entry:.2f} | "
                f"SL=${stop_loss:.2f} | TP=${take_profit:.2f} | R:R=2.0:1"
            ),
        }

    def _derive_structure_stop_loss(
        self,
        action: str,
        entry_price: float,
        signal_data: dict,
        risk_eval: dict,
    ) -> float:
        """Prefer the scanner's liquidity boundary over any fixed-percentage fallback."""
        if entry_price <= 0:
            return 0.0

        liquidity_zone = signal_data.get("liquidity_zone") or {}
        zone_low = float(liquidity_zone.get("low", 0.0) or 0.0)
        zone_high = float(liquidity_zone.get("high", 0.0) or 0.0)
        zone_level = float(liquidity_zone.get("level", 0.0) or 0.0)
        side = str(action or "").upper()

        if side == "BUY":
            for candidate in (zone_low, zone_level):
                if 0 < candidate < entry_price:
                    return candidate
        elif side == "SELL":
            for candidate in (zone_high, zone_level):
                if candidate > entry_price:
                    return candidate

        return float(risk_eval.get("stop_loss") or 0.0)

    def _pick_more_protective_stop(self, position: dict, updates: list[dict]) -> Optional[dict]:
        """Choose the stop update that protects more profit for the current side."""
        if not updates:
            return None
        side = str(position.get("side", "") or "").upper()
        if side == "SELL":
            return min(updates, key=lambda item: float(item.get("new_stop", 0.0) or 0.0))
        return max(updates, key=lambda item: float(item.get("new_stop", 0.0) or 0.0))

    def _apply_managed_stop_update(self, position: dict, stop_update: dict) -> bool:
        """Push an autonomous stop adjustment into TradingView and local state."""
        new_stop = float(stop_update.get("new_stop", 0.0) or 0.0)
        reason = str(stop_update.get("reason", "Managed stop update") or "Managed stop update")
        if new_stop <= 0:
            return False

        if not self.rpa_hand.update_stop_loss(new_stop, ticker_hint=position["asset"]):
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[WARN] STOP UPDATE FAILED</span>: '
                f'{position["asset"]} {reason} -> ${new_stop:.4f}'
            )
            return False

        position["sl_price"] = new_stop
        if stop_update.get("break_even_locked"):
            position["break_even_locked"] = True
        position["last_stop_update_reason"] = reason
        position["last_stop_update_ts"] = time.time()
        self.profit_lock.update_position_stop(
            asset=position["asset"],
            new_stop=new_stop,
            reason=reason,
            stop_locked=bool(stop_update.get("stop_locked")),
            break_even_locked=bool(stop_update.get("break_even_locked")),
        )
        self.cmd.log(
            f'<span style="color:#58A6FF;font-weight:bold">[SHIELD] STOP UPDATED</span>: '
            f'{position["asset"]} {reason} -> ${new_stop:.4f}'
        )
        return True

    def _manage_position_stop(self, position: dict, hist) -> None:
        """Run break-even shield and 3-bar trailing logic for one open position."""
        current_price = float(position.get("current", 0.0) or 0.0)
        if current_price <= 0:
            return

        stop_updates = []
        break_even_update = self.profit_lock.check_break_even(position, current_price)
        if break_even_update:
            stop_updates.append(break_even_update)

        now_ts = time.time()
        last_trailing_check_ts = float(position.get("last_trailing_check_ts", 0.0) or 0.0)
        if now_ts - last_trailing_check_ts >= config.AUTONOMOUS_TRAILING_UPDATE_SECONDS:
            position["last_trailing_check_ts"] = now_ts
            trail_update = self.profit_lock.calculate_three_bar_trailing_stop(
                position=position,
                recent_candles=hist,
                current_price=current_price,
                lookback_bars=config.AUTONOMOUS_TRAILING_LOOKBACK_BARS,
            )
            if trail_update:
                stop_updates.append(trail_update)

        chosen_update = self._pick_more_protective_stop(position, stop_updates)
        if chosen_update:
            self._apply_managed_stop_update(position, chosen_update)

    def _calculate_position_pnl(self, position: dict, current_price: float) -> tuple[float, float]:
        """Calculate open P&L using futures contract point values when known."""
        entry = float(position.get("entry", 0.0) or 0.0)
        if entry <= 0 or current_price <= 0:
            return 0.0, 0.0

        pnl_usd = self.profit_lock.calculate_open_profit(position, current_price)
        side = str(position.get("side", "") or "").upper()
        if side == "SELL":
            pnl_pct = ((entry - current_price) / entry) * 100.0
        else:
            pnl_pct = ((current_price - entry) / entry) * 100.0
        return pnl_usd, pnl_pct

    def _track_peak_open_profit(self, position: dict, pnl_usd: float) -> None:
        peak = max(float(position.get("peak_pnl", 0.0) or 0.0), float(pnl_usd))
        position["peak_pnl"] = peak
        if peak > 0:
            giveback = max(0.0, peak - float(pnl_usd))
            position["profit_giveback_usd"] = giveback
            position["profit_giveback_pct"] = (giveback / peak) * 100.0
        else:
            position["profit_giveback_usd"] = 0.0
            position["profit_giveback_pct"] = 0.0

    def _profit_ladder_floor(self, peak_profit: float) -> float:
        """Return the locked profit floor for the current high-water mark."""
        if not bool(getattr(config, "PROFIT_LADDER_SHIELD_ENABLED", True)):
            return 0.0

        ladder_text = str(getattr(config, "PROFIT_LADDER_STEPS_USD", "") or "")
        floor = 0.0
        for raw_step in ladder_text.split(","):
            if ":" not in raw_step:
                continue
            trigger_text, floor_text = raw_step.split(":", 1)
            try:
                trigger = float(trigger_text.strip())
                candidate_floor = float(floor_text.strip())
            except ValueError:
                continue
            if peak_profit >= trigger:
                floor = max(floor, candidate_floor)
        return floor

    def _profit_giveback_should_exit(self, position: dict) -> bool:
        if not bool(getattr(config, "PROFIT_GIVEBACK_SHIELD_ENABLED", True)):
            return False
        if position.get("profit_shield_triggered"):
            return False

        peak = float(position.get("peak_pnl", 0.0) or 0.0)
        pnl = float(position.get("pnl", 0.0) or 0.0)
        min_profit = float(getattr(config, "PROFIT_GIVEBACK_MIN_PROFIT_USD", 150.0))
        if peak < min_profit:
            return False

        ladder_floor = self._profit_ladder_floor(peak)
        position["profit_ladder_floor_usd"] = ladder_floor
        if ladder_floor > 0 and pnl <= ladder_floor:
            position["profit_shield_exit_reason"] = (
                f"ladder floor ${ladder_floor:.2f} after peak ${peak:.2f}"
            )
            return True

        max_retrace = float(getattr(config, "PROFIT_GIVEBACK_MAX_RETRACE_PCT", 35.0))
        min_lock = float(getattr(config, "PROFIT_GIVEBACK_MIN_LOCK_USD", 50.0))
        giveback_pct = float(position.get("profit_giveback_pct", 0.0) or 0.0)
        if giveback_pct >= max_retrace and pnl >= min_lock:
            position["profit_shield_exit_reason"] = (
                f"{giveback_pct:.1f}% giveback from peak ${peak:.2f}"
            )
            return True
        return False

    def _execute_profit_giveback_exit(self, position: dict) -> bool:
        asset = position.get("asset", "unknown")
        peak = float(position.get("peak_pnl", 0.0) or 0.0)
        pnl = float(position.get("pnl", 0.0) or 0.0)
        giveback_pct = float(position.get("profit_giveback_pct", 0.0) or 0.0)
        exit_reason = str(position.get("profit_shield_exit_reason", "") or "").strip()
        self.cmd.log(
            f'<span style="color:#F0B90B;font-weight:bold">[PROFIT SHIELD]</span> '
            f'{asset} peak=${peak:.2f}, now=${pnl:.2f}, gave back {giveback_pct:.1f}%'
            f'{f" ({exit_reason})" if exit_reason else ""} - protecting profit'
        )

        if not bool(getattr(config, "PROFIT_GIVEBACK_AUTO_FLATTEN", True)):
            self.cmd.log(
                f'<span style="color:#D29922;font-weight:bold">[PROFIT SHIELD]</span> '
                f'Auto-flatten disabled for {asset}; manual close recommended'
            )
            return False

        now = time.time()
        cooldown = int(getattr(config, "PROFIT_GIVEBACK_COOLDOWN_SECONDS", 120))
        last_attempt = float(position.get("last_profit_shield_exit_attempt", 0.0) or 0.0)
        if now - last_attempt < cooldown:
            return False
        position["last_profit_shield_exit_attempt"] = now

        flatten = getattr(self.rpa_hand, "flatten_position", None)
        if not callable(flatten):
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[PROFIT SHIELD]</span> '
                f'No RPA flatten method available for {asset}'
            )
            return False

        try:
            success = bool(flatten(asset))
        except Exception as e:
            logger.error("[PROFIT SHIELD] Flatten failed for %s: %s", asset, e)
            success = False

        if success:
            position["profit_shield_triggered"] = True
            position["profit_shield_ts"] = time.time()
            self._close_position(position, "Profit Giveback Shield")
            return True

        self.cmd.log(
            f'<span style="color:#F85149;font-weight:bold">[PROFIT SHIELD]</span> '
            f'Flatten attempt failed for {asset}; check TradingView position manually'
        )
        return False

    def _update_daily_profit_ladder(self, live_positions_pnl: float) -> None:
        """Pause fresh entries if the day's high-water profit gives back too much."""
        if not bool(getattr(config, "DAILY_PROFIT_LADDER_SHIELD_ENABLED", True)):
            return
        if self.daily_profit_ladder_triggered:
            return

        session_pnl = float(getattr(self, "protected_session_pnl", 0.0) or 0.0) + float(live_positions_pnl or 0.0)
        self.session_peak_pnl = max(float(getattr(self, "session_peak_pnl", 0.0) or 0.0), session_pnl)
        floor = self._profit_ladder_floor(self.session_peak_pnl)
        if floor <= 0 or session_pnl > floor:
            return

        self.daily_profit_ladder_triggered = True
        self.cmd.log(
            f'<span style="color:#F0B90B;font-weight:bold">[DAILY LADDER]</span> '
            f'Session peak ${self.session_peak_pnl:.2f}, now ${session_pnl:.2f}; '
            f'locking the board above ${floor:.2f}'
        )
        self.ai_narrator.add_activity(
            "[LOCK]",
            f"Daily ladder protected ${floor:.0f}+; fresh entries paused",
        )
        if bool(getattr(config, "DAILY_PROFIT_LADDER_PAUSE_ON_TRIGGER", True)):
            self.can_trade = False
            self.rpa_execution_enabled = False
            try:
                self.ai_narrator.set_rpa_execution_enabled(False)
            except Exception:
                pass

    def _update_positions(self):
        """Update live positions with current prices and check TP/SL."""
        import yfinance as yf
        import concurrent.futures

        updated_positions = []
        for pos in self.positions:
            try:
                # Get current price with timeout protection
                current_price = None
                asset = pos.get("asset", "")

                # PRICE SOURCE PIVOT: M6 contract codes use MT5, not Yahoo Finance. Gold/XAUUSD too.
                if asset and (asset.upper().endswith("M6") or asset.upper() in {"XAUUSD", "GC", "GC=F", "MGC", "COMEX:MGC1!"}):
                    current_price = self._fetch_mt5_price_for_m6(asset)
                    if current_price and current_price > 0:
                        logger.debug("[POSITIONS] MT5 price for %s: %.4f", asset, current_price)

                # Fallback to Yahoo Finance for non-M6 tickers
                if current_price is None:
                    market_ticker = self._canonical_market_ticker(asset)
                    ticker = yf.Ticker(market_ticker)

                    def fetch_price():
                        return ticker.history(period="1d", interval="1m")

                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(fetch_price)
                        hist = future.result(timeout=10)

                    if hist.empty:
                        self.cmd.log(f"[WARN] No price data for {asset} - trying browser agent")
                        self._verify_price_with_browser(pos)
                        updated_positions.append(pos)
                        continue

                    current_price = hist["Close"].iloc[-1]

                pos["current"] = current_price

                # Calculate P&L using contract point values where known.
                pnl_usd, pnl_pct = self._calculate_position_pnl(pos, current_price)
                pos["pnl"] = pnl_usd
                pos["pnl_pct"] = pnl_pct
                self._track_peak_open_profit(pos, pnl_usd)

                # MANUAL TRADE PROTECTION
                if not pos.get("bot_opened"):
                    self.cmd.log(
                        f"[MANUAL] Manual position detected: {asset} | "
                        f"P&L: ${pnl_usd:.2f} ({pnl_pct:.2f}%) — NOT managed by bot"
                    )
                    updated_positions.append(pos)
                    continue

                self._manage_position_stop(pos, None)

                if self._profit_giveback_should_exit(pos):
                    if self._execute_profit_giveback_exit(pos):
                        continue

                # Check Take Profit
                if pos.get("tp_price", 0) > 0:
                    if pos["side"] == "BUY" and current_price >= pos["tp_price"]:
                        self.cmd.log(f"[TARGET] TAKE PROFIT HIT: {asset} @ ${current_price:.2f} | P&L: +${pnl_usd:.2f}")
                        self._close_position(pos, "Take Profit")
                        continue
                    elif pos["side"] == "SELL" and current_price <= pos["tp_price"]:
                        self.cmd.log(f"[TARGET] TAKE PROFIT HIT: {asset} @ ${current_price:.2f} | P&L: +${pnl_usd:.2f}")
                        self._close_position(pos, "Take Profit")
                        continue

                # Check Stop Loss
                if pos.get("sl_price", 0) > 0:
                    if pos["side"] == "BUY" and current_price <= pos["sl_price"]:
                        self.cmd.log(f"[STOP] STOP LOSS HIT: {asset} @ ${current_price:.2f} | P&L: ${pnl_usd:.2f}")
                        self._close_position(pos, "Stop Loss")
                        continue
                    elif pos["side"] == "SELL" and current_price >= pos["sl_price"]:
                        self.cmd.log(f"[STOP] STOP LOSS HIT: {asset} @ ${current_price:.2f} | P&L: ${pnl_usd:.2f}")
                        self._close_position(pos, "Stop Loss")
                        continue

                updated_positions.append(pos)

                # Update Position Monitor in narrator overlay with live MT5 data
                try:
                    self.ai_narrator.update_position_monitor(updated_positions)
                except Exception:
                    pass

            except concurrent.futures.TimeoutError:
                self.cmd.log(f"[WARN] Timeout fetching {pos.get('asset')} price - network lag")
                updated_positions.append(pos)

            except Exception as e:
                self.cmd.log(f"[WARN] Error updating {pos.get('asset', 'unknown')}: {e}")
                updated_positions.append(pos)

        self.positions = updated_positions
        self.cmd.update_positions(self.positions)
        live_positions_pnl = sum(p.get("pnl", 0.0) for p in self.positions)
        self._update_daily_profit_ladder(live_positions_pnl)
        self.profit_lock.update_balance(self.balance + live_positions_pnl)
        self.ai_narrator.update_live_pnl(self.total_pnl + live_positions_pnl, len(self.positions))
        self._refresh_live_ledger()
        
        # Update AI Narrator with position status (only if there are positions)
        if self.positions:
            self.ai_narrator.notify_position_update(len(self.positions), self.daily_pnl)
        else:
            # No positions - set to idle/scanning
            self.ai_narrator.set_status("scanning", "No active positions")
    
    async def _update_safety_controls(self):
        """Update financial safety controls and check news filter."""
        import asyncio

        # FORCE ZERO: Hardcode daily P/L to prevent $-33k error from bad data
        self.daily_pnl = 0.0

        # Check news filter - use the browser event loop if available
        try:
            if hasattr(self, '_browser_loop') and self._browser_loop and not self._browser_loop.is_closed():
                future = asyncio.run_coroutine_threadsafe(
                    self.financial_safety.update_news_filter(),
                    self._browser_loop,
                )
                # Add error logging callback so failures are not silently swallowed
                def _on_news_done(fut):
                    try:
                        fut.result()
                    except Exception as exc:
                        logger.error(f"News filter async update failed: {exc}")
                future.add_done_callback(_on_news_done)
            else:
                # Fallback: run synchronously via a temporary loop
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(self.financial_safety.update_news_filter())
                finally:
                    loop.close()
        except Exception as e:
            logger.error(f"News filter update failed: {e}")

        # Check position size mode
        mode = self.financial_safety.current_mode
        paused = self.financial_safety.trading_paused

        if paused:
            self.cmd.log(f"[STOP] Trading paused: {self.financial_safety.pause_reason}")
            self.ai_narrator.notify_error(f"Trading paused: {self.financial_safety.pause_reason}")
        elif mode.value != "normal":
            multiplier = self.financial_safety.get_position_size_multiplier()
            self.cmd.log(f"[RULER] Position size mode: {mode.value} ({multiplier:.0%} of normal)")
            self.ai_narrator.add_activity(
                "[RULER]",
                f"Position size: {mode.value} ({multiplier:.0%})"
            )

        # Update dashboard with safety status
        safety_status = self.financial_safety.get_safety_status()
        self.cmd.update_safety_status(safety_status)

        # LIVE BALANCE SYNC: scrape real broker balance every 60s
        try:
            await self._sync_live_balance()
        except Exception as e:
            logger.warning("Live balance sync error in safety controls: %s", e)

    def _safety_timer_wrapper(self):
        """Qt timer sync wrapper to call async _update_safety_controls."""
        try:
            if hasattr(self, '_browser_loop') and self._browser_loop and not self._browser_loop.is_closed():
                asyncio.run_coroutine_threadsafe(
                    self._update_safety_controls(),
                    self._browser_loop
                )
            else:
                # Fallback: run in new event loop
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self._update_safety_controls())
                loop.close()
        except Exception as e:
            logger.error("Safety timer wrapper failed: %s", e)

    def _update_institutional_governor_ui(self):
        """STAGE 3: Update Institutional Governor panel in dashboard."""
        # Trigger live news scan if due — dispatch to browser event loop
        if self.sentiment_pulse.should_check():
            try:
                import asyncio
                browser_loop = getattr(self, '_browser_loop', None)
                if browser_loop and not browser_loop.is_closed():
                    future = asyncio.run_coroutine_threadsafe(
                        self.sentiment_pulse.check_news(), browser_loop
                    )
                    future.result(timeout=10)
                else:
                    # Fallback: run in a dedicated event loop
                    loop = asyncio.new_event_loop()
                    try:
                        loop.run_until_complete(self.sentiment_pulse.check_news())
                    finally:
                        loop.close()
            except Exception as e:
                logger.warning("[SAT] Sentiment news check failed: %s", e)
        
        # Collect risk governor data
        risk_summary = self.risk_governor.get_portfolio_summary()
        
        # Collect sentiment pulse data
        sentiment_summary = self.sentiment_pulse.get_dashboard_summary()
        
        # Collect profit lock data
        profit_lock_summary = self.profit_lock.get_dashboard_summary()
        
        # Detect sentiment changes for Activity Feed
        sentiment_label = sentiment_summary.get("sentiment_label", "NEUTRAL - Mixed Signals")
        market_context = sentiment_summary.get("market_context", "")
        if hasattr(self, "_last_sentiment_label"):
            if self._last_sentiment_label != sentiment_label and market_context:
                self.cmd.log(f"[SAT] Market Context: {sentiment_label}")
                self.ai_narrator.add_activity("[SAT]", f"{sentiment_label} | {market_context}")
        self._last_sentiment_label = sentiment_label
        
        # Merge data for dashboard
        governor_data = {
            # Risk Governor
            "total_exposure_pct": risk_summary["total_exposure_pct"],
            "max_total_exposure_pct": self.risk_governor.max_total_exposure_pct,
            "avg_correlation": risk_summary["avg_correlation"],
            
            # Sentiment Pulse
            "next_event": sentiment_summary["next_event"],
            "time_to_event": sentiment_summary["time_to_event"],
            "rpa_enabled": (sentiment_summary["rpa_status"] == "ACTIVE") and self.rpa_execution_enabled,
            "sentiment_label": sentiment_label,
            "market_context": market_context,
            "headline_count": sentiment_summary.get("headline_count", 0),
            
            # Profit Lock
            "stops_locked": profit_lock_summary["stops_locked"],
            "daily_pnl_pct": profit_lock_summary["daily_pnl_pct"],
            "daily_pnl_dollars": profit_lock_summary["daily_pnl_dollars"],
            "progress_to_target": profit_lock_summary["progress_to_target"],
            
            # Walk Away Protocol
            "walk_away_can_trade": profit_lock_summary["can_trade"],
            "walk_away_remaining_hours": (
                profit_lock_summary["walk_away"]["remaining_hours"]
                if profit_lock_summary["walk_away"]["active"] else 0
            )
        }
        
        # Update dashboard UI
        self.cmd.update_institutional_governor(governor_data)

    def _run_meta_cognition_review(self):
        """
        STAGE 4: Meta-Cognition Self-Review.

        Every 24 hours, the AI reviews the trade_ledger.json and:
        1. Identifies worst performing asset
        2. Identifies best performing timeframe
        3. Suggests config adjustments
        4. Updates Alpha Score
        """
        self.cmd.log("=" * 60)
        self.cmd.log("[BRAIN] META-COGNITION SELF-REVIEW INITIATED")
        self.cmd.log("=" * 60)

        try:
            # Run self-review
            review_result = self.meta_analyzer.perform_self_review()

            if review_result["status"] == "INSUFFICIENT_DATA":
                self.cmd.log(
                    f"[BRAIN] Meta-Review skipped: Only {review_result.get('trades_analyzed', 0)} trades (need 5+)"
                )
                self.ai_narrator.add_activity("[BRAIN]", "Meta-review: Insufficient data")
                return

            # Log review results
            self.cmd.log(f"[CHART] Trades Analyzed: {review_result['trades_analyzed']}")
            self.cmd.log(f"[TARGET] Alpha Score: {review_result['alpha_score']:.1f}/100")

            if review_result.get("worst_asset"):
                worst = review_result["worst_asset"]
                self.cmd.log(
                    f"[DOWN] Worst Performer: {worst.get('asset', 'N/A')} "
                    f"(${worst.get('total_pnl', 0):.2f} PnL, "
                    f"{worst.get('win_rate', 0):.0%} win rate)"
                )

            if review_result.get("best_asset"):
                best = review_result["best_asset"]
                self.cmd.log(
                    f"[UP] Best Performer: {best.get('asset', 'N/A')} "
                    f"(${best.get('total_pnl', 0):.2f} PnL, "
                    f"{best.get('win_rate', 0):.0%} win rate)"
                )

            if review_result.get("best_timeframe"):
                best_tf = review_result["best_timeframe"]
                self.cmd.log(
                    f"[TIMER]  Best Timeframe: {best_tf.get('timeframe', 'N/A')} "
                    f"({best_tf.get('win_rate', 0):.0%} win rate)"
                )

            if review_result.get("adjustments_suggested", 0) > 0:
                self.cmd.log(
                    f"[IDEA] Adjustments Suggested: {review_result['adjustments_suggested']}"
                )
                if review_result.get("adjustments_applied", 0) > 0:
                    self.cmd.log(
                        f"[OK] Adjustments Applied: {review_result['adjustments_applied']}"
                    )

            # Update dashboard with Alpha Score
            learning_summary = self.meta_analyzer.get_learning_summary()
            self.cmd.update_meta_cognition(learning_summary)

            # Notify AI Narrator
            self.ai_narrator.add_activity(
                "[BRAIN]",
                f"Meta-review complete | Alpha: {review_result['alpha_score']:.1f}"
            )

            self.cmd.log("=" * 60)
            self.cmd.log("[BRAIN] META-COGNITION REVIEW COMPLETE")
            self.cmd.log("=" * 60)

        except Exception as e:
            self.cmd.log(f"[FAIL] Meta-Cognition review failed: {e}")
            logger.error(f"Meta-Cognition review error: {e}")
            self.ai_narrator.notify_error(f"Meta-review failed: {e}")

    def _heartbeat_check(self):
        """
        Heartbeat Monitor - Logs system health every 60 seconds.
        Now includes Market Session context.
        """
        # HEARTBEAT SUPPRESSION: if RPA is executing, don't run checks that touch browser state
        if self.executor and getattr(self.executor.rpa_executor, "is_executing", False):
            logger.info("[HEARTBEAT] Execution in progress — 60s health check suppressed")
            return
        self._emit_gatekeeper_summary_if_due()

        # Get system stats
        active_trades = len(self.positions)
        daily_pnl = self.daily_pnl
        mode = self.current_mode
        
        # Get session status
        session_status = self.session_detector.get_session_status_log()
        
        # Check executor stats if available
        executor_status = ""
        if self.executor:
            stats = self.executor.get_execution_stats()
            if stats['consecutive_failures'] > 0:
                executor_status += f" | [WARN] {stats['consecutive_failures']} consecutive failures"
            if stats['safety_stop_active']:
                executor_status += " | [STOP] Safety Stop Active"
        
        # Log heartbeat with session context
        self.cmd.log(
            f'[HEARTBEAT] System Healthy - {session_status} | {mode} mode | '
            f'Positions: {active_trades} | Daily P/L: ${daily_pnl:.2f} | '
            f'Trades Today: {self.trades_today}{executor_status}'
        )
        
        # Update AI Narrator status if idle
        if active_trades == 0:
            session_context = self.session_detector.get_session_context()
            if session_context.get("dashboard_override_active"):
                self.ai_narrator.set_status("scanning", "Dashboard override active")
            elif self.session_detector.is_weekend():
                self.ai_narrator.set_status("scanning", "Weekend - Crypto only")
            else:
                self.ai_narrator.set_status("scanning", f"{session_context['primary_session']} session active")

    def _verify_price_with_browser(self, position: dict):
        asset = position.get("asset", "UNKNOWN")
        self.cmd.log(f"[GLOBE] Browser agent checking {asset} price autonomously...")
        self.ai_narrator.set_status("analyzing", f"Browser checking {asset}")
        
        # Use the existing browser event loop if available
        if hasattr(self, '_browser_loop') and self._browser_loop and not self._browser_loop.is_closed():
            import concurrent.futures
            future = asyncio.run_coroutine_threadsafe(
                self._browser_price_check(asset),
                self._browser_loop,
            )
            try:
                result = future.result(timeout=30)  # 30 second timeout
                self._apply_browser_price_result(result, position, asset)
            except concurrent.futures.TimeoutError:
                self.cmd.log(f"[WARN] Browser price check timeout for {asset}")
            except Exception as e:
                logger.error(f"Browser price check failed for {asset}: {e}")
        else:
            # Fallback: run in a thread with a temporary loop
            def browser_check():
                try:
                    loop = asyncio.new_event_loop()
                    try:
                        return loop.run_until_complete(
                            self._browser_price_check(asset)
                        )
                    finally:
                        loop.close()
                except Exception as e:
                    logger.error(f"Browser price check failed: {e}")
                    return None

            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(browser_check)
                try:
                    result = future.result(timeout=30)
                    self._apply_browser_price_result(result, position, asset)
                except concurrent.futures.TimeoutError:
                    self.cmd.log(f"[WARN] Browser price check timeout for {asset}")

    def _apply_browser_price_result(self, result, position: dict, asset: str):
        """Apply browser price check result to position."""
        if result and "price" in result:
            self.cmd.log(f"[OK] Browser found {asset} @ ${result['price']:.2f}")
            position["current"] = result["price"]
            if position.get("entry", 0) > 0:
                qty = position.get("quantity", 0)
                if position.get("side") == "BUY":
                    position["pnl"] = (position["current"] - position["entry"]) * qty
                else:
                    position["pnl"] = (position["entry"] - position["current"]) * qty
            self.ai_narrator.add_activity(
                "[GLOBE]",
                f"Browser verified {asset} @ ${result['price']:.2f}"
            )
        else:
            self.cmd.log(f"[WARN] Browser couldn't find price for {asset}")
    
    async def _browser_price_check(self, asset: str):
        """Async browser price check using TradingView."""
        if not self.browser_agent:
            self.browser_agent = BrowserAgent(headless=True)
            await self.browser_agent.start()
        
        return await self.browser_agent.execute_autonomous_task(asset, "check_price")

    def _ensure_balance_state(self):
        """Defensive balance-state initialization for late/mocked call paths."""
        if not hasattr(self, "balance"):
            self.balance = float(config.CURRENT_BALANCE)
        if not hasattr(self, "equity"):
            self.equity = float(self.balance)
        if not hasattr(self, "daily_pnl"):
            self.daily_pnl = 0.0
        # Reset daily_pnl to 0.00 on every script restart
        if not hasattr(self, "_daily_pnl_initialized"):
            self.daily_pnl = 0.0
            self._daily_pnl_initialized = True
        if not hasattr(self, "total_pnl"):
            self.total_pnl = 0.0
        if not hasattr(self, "peak_balance"):
            self.peak_balance = float(self.balance)
        if not hasattr(self, "max_drawdown"):
            self.max_drawdown = 0.0
        if not hasattr(self, "starting_balance"):
            self.starting_balance = float(config.CURRENT_BALANCE)
        if not hasattr(self, "balance_diverged"):
            self.balance_diverged = False

    def _fallback_balance_value(self) -> float:
        """Return the configured fail-open balance used when live scrape is unavailable."""
        try:
            fallback = float(getattr(config, "HARDCODED_EQUITY_FALLBACK", 77500.0) or 77500.0)
        except Exception:
            fallback = 77500.0
        return fallback if fallback > 0 else 77500.0

    def _apply_balance_fallback(self, reason: str, journal_pnl: Optional[float] = None) -> float:
        """Sync internal equity to the fallback so balance-read failures do not block RPA."""
        fallback = self._fallback_balance_value()
        self.balance = fallback
        self.equity = fallback
        self.balance_diverged = False
        self.peak_balance = max(float(getattr(self, "peak_balance", fallback)), fallback)

        if journal_pnl is None:
            try:
                journal_pnl = float(self.sql_journal.get_total_realized_pnl())
            except Exception:
                journal_pnl = None
        if journal_pnl is not None:
            self.starting_balance = fallback - float(journal_pnl)

        logger.warning("[BALANCE] Fallback sync active: $%.2f (%s)", fallback, reason)
        try:
            self.cmd.log(
                f'<span style="color:#D29922;font-weight:bold">[BALANCE FALLBACK]</span>: '
                f'using ${fallback:,.2f} because {reason}'
            )
        except Exception:
            pass
        return fallback

    def _get_open_risk_dollars(self) -> float:
        """Sum of initial risk amounts for all open positions."""
        return sum(
            float(p.get("initial_risk_amount", 0.0) or 0.0)
            for p in getattr(self, "positions", [])
        )

    def _reconcile_balance(self) -> bool:
        """Compare internal balance against journal-derived balance. Warn if >5% divergence."""
        self._ensure_balance_state()
        try:
            journal_pnl = self.sql_journal.get_total_realized_pnl()
            expected_balance = self.starting_balance + journal_pnl
            divergence = abs(self.balance - expected_balance)
            divergence_pct = (divergence / max(1.0, expected_balance)) * 100.0

            if divergence_pct > 5.0:
                logger.warning(
                    "BALANCE DRIFT: internal=%.2f journal=%.2f diff=%.2f (%.2f%%). Applying fallback sync.",
                    self.balance,
                    expected_balance,
                    divergence,
                    divergence_pct,
                )
                self.cmd.log(
                    f'<span style="color:#D29922;font-weight:bold">[WARN] BALANCE DRIFT</span>: '
                    f'Live ${self.balance:.2f} vs Ledger ${expected_balance:.2f} '
                    f'(diff {divergence_pct:.2f}%). Applying fallback sync.'
                )
                self._apply_balance_fallback("balance divergence detected", journal_pnl=journal_pnl)
                return True

            self.balance_diverged = False
            return True
        except Exception as exc:
            logger.warning("Balance reconciliation failed: %s", exc)
            # Fail-open: allow trading if reconciliation itself fails, but log loudly
            return True

    async def _sync_live_balance(self):
        """Scrape live balance from broker dashboard and sync internal state.
        Runs every 60 seconds alongside safety controls."""
        try:
            scrape_result = await self.rpa_hand.scrape_live_balance()
            if scrape_result is None:
                self._apply_balance_fallback("live balance scrape returned no clean read")
                return

            live_balance = scrape_result.get("net_liq")
            day_pl = scrape_result.get("day_pl")

            if live_balance is None:
                self._apply_balance_fallback("live balance scrape returned no equity value")
                return

            if scrape_result.get("fallback"):
                self._apply_balance_fallback("live balance scraper used fallback")
                if day_pl is not None:
                    # FORCE ZERO: Commented out to prevent $-33k error
                    # self.daily_pnl = day_pl
                    self.daily_pnl = 0.0
                return

            previous_balance = getattr(self, "balance", float(config.CURRENT_BALANCE))
            diff = live_balance - previous_balance

            # Log the dashboard snapshot every sync cycle
            logger.info(
                "[LION EYE] Dashboard Sync: Net Liq = $%.2f | Day P/L = %s%s",
                live_balance,
                f"${day_pl:.2f}" if day_pl is not None else "N/A",
                " (fallback)" if scrape_result.get("fallback") else "",
            )

            # Only act if change is meaningful (> $5) to avoid noise from floating decimals
            if abs(diff) < 5.0:
                return

            # Update internal balance immediately to match real Apex account
            self.balance = live_balance
            self.equity = live_balance

            # FORCE ZERO: Hardcode daily P/L to prevent $-33k error from bad data
            self.daily_pnl = 0.0
            # Sync daily P/L from dashboard if available - COMMENTED OUT
            # if day_pl is not None:
            #     self.daily_pnl = day_pl

            # Manual profit / loss detection
            if diff > 0:
                logger.info(
                    "[SYSTEM] Manual profit detected: $%.2f added to account. "
                    "Adjusting equity buffer to $%.2f",
                    diff,
                    live_balance,
                )
                self.cmd.log(
                    f'<span style="color:#3FB950;font-weight:bold">[SYSTEM] LIVE SYNC</span>: '
                    f'Manual profit +${diff:.2f} detected. Equity updated to ${live_balance:.2f}'
                )
                self.ai_narrator.add_activity(
                    "[SYSTEM]",
                    f"Live balance sync: +${diff:.2f} manual profit"
                )
            elif diff < 0:
                logger.info(
                    "[SYSTEM] Balance decreased by $%.2f (possible manual loss or fees). "
                    "Equity adjusted to $%.2f",
                    abs(diff),
                    live_balance,
                )
                self.cmd.log(
                    f'<span style="color:#D29922;font-weight:bold">[SYSTEM] LIVE SYNC</span>: '
                    f'Balance decreased by ${abs(diff):.2f}. Equity updated to ${live_balance:.2f}'
                )

            # Adjust peak balance if new balance is higher (resets drawdown calc)
            if live_balance > getattr(self, "peak_balance", live_balance):
                self.peak_balance = live_balance
                logger.info("[SYSTEM] New peak balance: $%.2f", live_balance)

            # Notify prop firm engine of updated balance
            if hasattr(self, "prop_engine") and self.prop_engine:
                self.prop_engine.compliance.current_balance = live_balance

            # Update profit lock tracker
            if hasattr(self, "profit_lock"):
                self.profit_lock.update_balance(live_balance)

        except Exception as e:
            logger.warning("Live balance sync failed: %s", e)

    def _close_reentry_lockout_seconds(self, reason: str, pnl: float) -> int:
        """Keep loss cooldown strict while allowing faster reversal after wins."""
        base_seconds = int(float(getattr(config, "RE_ENTRY_LOCKOUT_MINUTES", 5)) * 60)
        reason_text = str(reason or "").lower()
        if "stop" in reason_text or pnl < 0:
            return base_seconds
        if "profit" in reason_text or "take" in reason_text:
            return min(base_seconds, int(os.getenv("PROFIT_EXIT_REENTRY_LOCKOUT_SECONDS", "30")))
        return min(base_seconds, int(os.getenv("WIN_EXIT_REENTRY_LOCKOUT_SECONDS", "60")))

    def _apply_close_reentry_lockout(self, asset: str, reason: str, pnl: float) -> None:
        lockout_seconds = max(0, self._close_reentry_lockout_seconds(reason, pnl))
        if lockout_seconds <= 0:
            self.locked_tickers.pop(asset, None)
            return

        base_seconds = max(1, int(float(getattr(config, "RE_ENTRY_LOCKOUT_MINUTES", 5)) * 60))
        adjusted_started_at = time.time() - max(0, base_seconds - lockout_seconds)
        self.locked_tickers[asset] = adjusted_started_at

    def _close_position(self, position: dict, reason: str):
        """Close a position and update P&L."""
        self._ensure_balance_state()
        pnl = position.get("pnl", 0)
        self._apply_close_reentry_lockout(position["asset"], reason, pnl)
        self.balance += pnl
        self.daily_pnl += pnl
        self.total_pnl += pnl
        self.protected_session_pnl = float(getattr(self, "protected_session_pnl", 0.0) or 0.0) + float(pnl or 0.0)

        # Update peak balance and drawdown
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        self.max_drawdown = max(0, self.peak_balance - self.balance)

        # Record trade with Prop Firm Engine (The "Professor" tracks everything)
        if self.prop_engine:
            self.prop_engine.record_trade(pnl, position["asset"])
            compliance = self.prop_engine.get_dashboard_data()
            self.cmd.update_prop_firm_compliance(compliance)

        # Update UI
        self.cmd.update_balance(
            self.balance,
            self.equity,
            self.daily_pnl,
            self.total_pnl,
            drawdown=self.max_drawdown,
            drawdown_pct=(self.max_drawdown / self.peak_balance * 100.0) if self.peak_balance else 0.0,
            trades_today=self.trades_today,
        )
        self.cmd.add_trade_log(
            position["asset"],
            "CLOSE",
            position["amount"],
            pnl,
            f"Closed - {'Profit' if pnl > 0 else 'Loss'} ({reason})"
        )
        
        self.cmd.log(f"[OK] Position closed: {position['asset']} | {reason} | P&L: ${pnl:.2f}")
        journal_id = position.get("journal_id")
        if journal_id:
            outcome = f"{reason} | PnL={pnl:.2f}"
            self.sql_journal.update_outcome(journal_id, outcome)
            self.sql_journal.update_trade_vibe_outcome(journal_id, outcome, pnl=pnl)
        self.profit_lock.remove_position(position["asset"])
        if pnl > 0:
            self.daily_wins += 1
        self.ai_narrator.update_live_pnl(self.total_pnl, len(self.positions))
        self._refresh_live_ledger()

    def _on_market_data(self, market_data: MarketDataPoint):
        """Queue market data for Swarm analysis."""
        self.analysis_worker.add_to_queue(market_data)

    def _on_cloud_signal(self, signal_data: dict):
        """Mirror cloud-dispatch success without executing twice.

        The HTTP listener is the single source of truth for local execution.
        CloudScannerThread emits this signal only after a successful HTTP dispatch,
        so executing here would double-fire the same trade.
        """
        from core.market_sessions import is_crypto_ticker
        ticker = signal_data.get("ticker", "UNKNOWN")
        if not self.can_trade and not is_crypto_ticker(ticker) and not is_futures_ticker(ticker):
            self.cmd.log(
                '<span style="color:#D29922;font-weight:bold">[APEX BLOCK]</span> '
                'Cloud signal rejected — trading halted by Apex gate'
            )
            return

        ticker = signal_data.get("ticker", "UNKNOWN")
        if self.current_watchlist and ticker not in self.current_watchlist:
            logger.info("CLOUD_SIGNAL: ignoring inactive ticker %s", ticker)
            self._on_ticker_status_update(ticker, "trade_rejected")
            return

        self.cmd.log(
            f'<span style="color:#00D4FF;font-weight:bold">[CLOUD] CLOUD SCANNER</span>: '
            f'{signal_data["action"]} {signal_data["ticker"]} '
            f'(confidence: {signal_data["confidence"]:.2f})'
        )
        self.cmd.update_watchlist_status(
            signal_data["ticker"],
            "awaiting_strike",
            confidence=float(signal_data.get("confidence", 0.0) or 0.0),
            last_signal=str(signal_data.get("action", "WAIT")).upper(),
        )
        
        # Update AI Narrator
        self.ai_narrator.notify_signal_detected(
            signal_data["ticker"],
            signal_data.get("action", signal_data.get("signal_type", "Signal")),
            signal_data["confidence"]
        )
        self._announce_signal_alert(
            signal_data["ticker"],
            signal_data.get("action", "SIGNAL"),
            signal_data.get("confidence", 0.0),
            source="cloud",
        )
        self.cmd.log(
            f'<span style="color:#8B949E">[MAIL] CLOUD ROUTE</span>: '
            f'{signal_data["action"]} {signal_data["ticker"]} delivered to local listener'
        )
        if self.current_mode == "AUTONOMOUS":
            QTimer.singleShot(750, lambda payload=dict(signal_data): self._cloud_signal_listener_watchdog(payload))

    def _on_technical_signal_detected(self, signal_data: dict):
        """Light up the UI as soon as the scanner finds an opportunity."""
        ticker = str(signal_data.get("ticker", "UNKNOWN") or "UNKNOWN").upper()
        action = str(signal_data.get("action", "SIGNAL") or "SIGNAL").upper()
        signal_type = str(signal_data.get("signal_type", "SIGNAL") or "SIGNAL").upper()
        confidence = float(signal_data.get("confidence", 0.0) or 0.0)
        confidence_score = self._confidence_to_score(confidence)

        logger.info(
            "[UI] Technical opportunity: %s %s | %s | %.0f%%",
            action,
            ticker,
            signal_type,
            confidence_score,
        )
        label = action if action in {"BUY", "SELL"} else "SIGNAL"
        color = "#3FB950" if label == "BUY" else "#F85149" if label == "SELL" else "#F2CC60"
        self.cmd.log(
            f'<span style="color:{color};font-weight:bold">[RADAR] {label}</span>: '
            f'{signal_type} on {ticker} | {label} bias | confidence {confidence_score:.0f}%'
        )
        self.cmd.update_watchlist_status(
            ticker,
            "awaiting_strike" if action in {"BUY", "SELL"} else "analyzing_liquidity",
            confidence=confidence,
            last_signal=action if action in {"BUY", "SELL"} else signal_type,
        )
        if self._is_loud_signal(confidence_score, signal_data) and hasattr(self.ai_narrator, "notify_signal_detected"):
            self.ai_narrator.notify_signal_detected(ticker, action, confidence)
        elif hasattr(self.ai_narrator, "update_confidence_meter"):
            self.ai_narrator.update_confidence_meter(ticker, action, confidence, status=signal_type)
        self._announce_signal_alert(ticker, action, confidence_score, source="technical")

    def _on_signal_approved(self, signal_data: dict):
        """Handle user approval of trade signal."""
        resolved_action = self._resolve_directional_action(signal_data)
        signal_data["action"] = resolved_action
        amount = signal_data.get("investment_amount", 0)
        approval_color = "#F85149" if resolved_action == "SELL" else "#3FB950"
        approval_label = "SELL" if resolved_action == "SELL" else "BUY"
        self.cmd.log(
            f'<span style="color:{approval_color};font-weight:bold">[OK] APPROVED: {approval_label}</span>: '
            f'{resolved_action} {signal_data["ticker"]} '
            f'with ${amount:.2f}'
        )
        self.cmd.update_watchlist_status(
            signal_data["ticker"],
            "executing",
            confidence=float(signal_data.get("confidence", 0.0) or 0.0),
            last_signal=resolved_action,
        )
        
        # Update AI Narrator
        self.ai_narrator.notify_trade_approved(
            signal_data["ticker"],
            resolved_action,
            amount
        )

        # Execute the trade
        self._execute_cloud_signal(signal_data)

    def _on_signal_rejected(self, signal_data: dict):
        """Handle user rejection of trade signal."""
        self.cmd.log(
            f'<span style="color:#F85149;font-weight:bold">[FAIL] REJECTED</span>: '
            f'User declined {signal_data["action"]} {signal_data["ticker"]}'
        )
        
        # Update AI Narrator
        self.ai_narrator.notify_trade_rejected(signal_data["ticker"])
        self._broadcast_trade_levels(signal_data)

    def _on_signal_received(self, signal_data: dict):
        """Handle signal received from cloud via HTTP.
        CRITICAL: UI errors must NEVER crash this method — trade execution must proceed."""
        try:
            self._on_signal_received_inner(signal_data)
        except Exception as outer_err:
            logger.critical("[SIGNAL] CRASH in signal handler: %s — attempting emergency execution", outer_err)
            # Emergency: still try to execute the trade even if UI crashed
            try:
                ticker = signal_data.get("ticker", "UNKNOWN")
                action = signal_data.get("action", "UNKNOWN")
                logger.info("[SIGNAL] EMERGENCY EXEC: %s %s", action, ticker)
                self._execute_cloud_signal(signal_data)
            except Exception as exec_err:
                logger.critical("[SIGNAL] EMERGENCY EXEC ALSO FAILED: %s", exec_err)

    def _on_signal_received_inner(self, signal_data: dict):
        """Inner signal handler — UI-safe implementation."""
        self._mark_bridge_alive()
        self._mark_signal_listener_seen(signal_data)
        brain_override_action = self._brain_override_action(signal_data)
        resolved_action = self._resolve_directional_action(signal_data)
        action = brain_override_action if brain_override_action in {"BUY", "SELL"} else resolved_action
        signal_data["action"] = action
        self._mark_signal_listener_seen(signal_data)
        signal_data["force_execute"] = bool(signal_data.get("force_execute"))
        ticker = signal_data.get("ticker", "UNKNOWN")
        confidence = signal_data.get("confidence", 0.0)
        entry_price = signal_data.get("entry_price", 0.0)
        brain_verdict = str(signal_data.get("brain_verdict", "") or "").upper()
        brain_reasoning = str(signal_data.get("brain_reasoning", "") or "").strip()
        brain_model = str(signal_data.get("brain_model", "") or "").strip()
        brain_used = str(signal_data.get("brain_used", "OPENROUTER") or "OPENROUTER").strip().upper()
        fallback_mode = bool(signal_data.get("fallback_mode"))
        brain_override = self._has_brain_override(signal_data)

        # DETAILED CONSENSUS LOGGING: expose when brain disagrees with scanner
        if brain_verdict and brain_verdict not in {"", "HOLD"}:
            if brain_override_action in {"BUY", "SELL"} and brain_override_action != resolved_action:
                logger.warning(
                    "[CONSENSUS] Brain override (%s) conflicts with scanner signal (%s) for %s | "
                    "brain='%s' model=%s reasoning='%s'",
                    brain_override_action,
                    resolved_action,
                    ticker,
                    brain_used,
                    brain_model,
                    brain_reasoning[:120],
                )
            elif brain_override_action == "HOLD" and resolved_action in {"BUY", "SELL"}:
                logger.info(
                    "[CONSENSUS] Brain says WAIT/HOLD while scanner wants %s for %s | "
                    "brain='%s' model=%s reasoning='%s' — falling back to scanner",
                    resolved_action,
                    ticker,
                    brain_used,
                    brain_model,
                    brain_reasoning[:120],
                )
            elif brain_override_action == resolved_action and resolved_action in {"BUY", "SELL"}:
                logger.info(
                    "[CONSENSUS] Brain agrees with scanner: %s %s | brain='%s' model=%s",
                    resolved_action,
                    ticker,
                    brain_used,
                    brain_model,
                )

        self._sync_brain_runtime_ui(brain_used, fallback_mode)

        if self.current_watchlist and ticker not in self.current_watchlist:
            logger.info("APP_SIGNAL_HANDLER: ignoring inactive ticker %s", ticker)
            self._log_gatekeeper_abort(
                "Watchlist",
                f"{ticker} | ticker is not armed in the dashboard watchlist",
            )
            self.cmd.log(f"[WARN] Ignoring inactive dashboard ticker: {ticker}")
            self._on_ticker_status_update(ticker, "trade_rejected")
            return

        logger.info(
            "APP_SIGNAL_HANDLER: received %s %s confidence=%s entry=%s mode=%s brain_verdict=%s force_execute=%s",
            action,
            ticker,
            confidence,
            entry_price,
            self.current_mode,
            brain_verdict,
            signal_data["force_execute"],
        )

        confidence_score = self._confidence_to_score(confidence)
        required_confidence_score = self._confidence_to_score(config.SWARM_CONFIDENCE_THRESHOLD)
        if self._is_incubation_signal(confidence_score, signal_data):
            logger.info(
                "[INCUBATION] Main listener muted %s %s at %.1f%%. No popup, narrator alert, sound, or execution.",
                action,
                ticker,
                confidence_score,
            )
            self.cmd.update_watchlist_status(
                ticker,
                "incubating",
                confidence=confidence_score / 100.0,
                last_signal=action,
            )
            return
        if confidence_score < required_confidence_score and not brain_override:
            logger.info(
                "APP_SIGNAL_HANDLER: rejected by confidence gate (%s%% < %s%%) for %s %s",
                confidence_score,
                required_confidence_score,
                action,
                ticker,
            )
            self._log_gatekeeper_abort(
                "Confidence",
                f"{action} {ticker} | confidence {confidence_score:.0f}% below listener threshold {required_confidence_score:.0f}%",
            )
            self.analysis_mode_status = "REJECTED - Low Confidence"
            self.cmd.update_copilot_status(self.analysis_mode_status)
            self.ai_narrator.set_status("error", self.analysis_mode_status)
            self.cmd.log("Trade Ignored: Low AI Confidence")
            return
        if confidence_score < required_confidence_score and brain_override:
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[BRAIN] BRAIN OVERRIDE</span>: '
                f'bypassing listener confidence gate for {action} {ticker} | brain={brain_used} verdict={brain_verdict}'
            )
        
        # DEBUG: Log current mode and signal details
        self.cmd.log(
            f'<span style="color:#3FB950;font-weight:bold">[SAT] SIGNAL RECEIVED</span>: '
            f'{action} {ticker} (confidence: {confidence:.2f}, entry: ${entry_price:.2f})'
        )
        self.cmd.log(
            f'<span style="color:#8B949E">[MAGNIFY] DEBUG</span>: '
            f'Mode={self.current_mode}, Action={action}, Confidence={confidence}, EntryPrice={entry_price}'
        )
        confidence_display = confidence_score / 100.0
        self.cmd.update_watchlist_status(
            ticker,
            "awaiting_strike" if action in {"BUY", "SELL"} else "monitoring",
            confidence=confidence_display,
            last_signal=action,
        )
        if hasattr(self.ai_narrator, "update_confidence_meter"):
            self.ai_narrator.update_confidence_meter(
                ticker,
                action,
                confidence_display,
                status="SIGNAL RECEIVED",
            )
        if action in {"BUY", "SELL"} and self._is_loud_signal(confidence_score, signal_data):
            self.ai_narrator.notify_signal_detected(
                ticker,
                action,
                confidence_display,
            )
            self._announce_signal_alert(ticker, action, confidence_score, source="listener")

        # Update trade ledger
        self._add_to_trade_ledger(signal_data)
        self._broadcast_trade_levels(signal_data)

        if action not in ["BUY", "SELL"]:
            self.cmd.log(
                f'<span style="color:#8B949E">[NOTE] LOGGED</span>: '
                f'{action} {ticker} signal logged (no trade execution requested)'
            )
            return

        if entry_price <= 0:
            self._log_gatekeeper_abort(
                "Price Validation",
                f"{ticker} | no valid detected setup price for {action}",
            )
            self.cmd.log(
                f'<span style="color:#D29922">[WARN] SIGNAL BLOCKED</span>: '
                f'{ticker} has no valid entry price for {action}'
            )
            return

        if brain_override:
            amount = float(signal_data.get("investment_amount", self.default_investment) or self.default_investment)
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[BRAIN] BRAIN FORCE EXECUTION</span>: '
                f'{action} {ticker} approved by {brain_used} {brain_verdict}'
            )
            if brain_reasoning:
                self.cmd.log(f'<span style="color:#58A6FF">[RECEIPT] BRAIN</span>: {brain_reasoning}')
            self.ai_narrator.notify_trade_approved(ticker, action, amount)
            self._execute_cloud_signal(signal_data)
            return

        if self.current_mode == "AUTONOMOUS" and self._is_loud_signal(confidence_score, signal_data):
            approval_color = "#F85149" if action == "SELL" else "#3FB950"
            self.cmd.log(
                f'<span style="color:{approval_color};font-weight:bold">[BOLT] HIGH CONFIDENCE {action}</span>: '
                f'Auto-executing {action} {ticker} (confidence: {confidence:.2f})'
            )
            self.ai_narrator.notify_trade_approved(ticker, action, 1000.0)
            self.cmd.log(f'<span style="color:#D29922">[WRENCH] Calling _execute_cloud_signal...</span>')
            try:
                self._execute_cloud_signal(signal_data)
                logger.info("APP_SIGNAL_HANDLER: _execute_cloud_signal returned for %s %s", action, ticker)
                self.cmd.log(f'<span style="color:#3FB950">[OK] _execute_cloud_signal completed</span>')
            except Exception as e:
                logger.exception("APP_SIGNAL_HANDLER: execution failed for %s %s", action, ticker)
                self.cmd.log(f'<span style="color:#F85149">[FAIL] Execution failed: {e}</span>')
                import traceback
                self.cmd.log(f'<span style="color:#F85149">[NOTE] {traceback.format_exc()}</span>')
            return

        if self.current_mode == "AUTONOMOUS":
            logger.info(
                "[INCUBATION] AUTONOMOUS execution blocked for %s %s at %.1f%% pending 85%%/promotion.",
                action,
                ticker,
                confidence_score,
            )
            self.cmd.update_watchlist_status(
                ticker,
                "incubating",
                confidence=confidence_score / 100.0,
                last_signal=action,
            )
            return

        self.cmd.log(
            f'<span style="color:#58A6FF;font-weight:bold">[TEACHER] TEACHER REVIEW</span>: '
            f'{action} {ticker} queued for approval'
        )
        dialog = SignalApprovalDialog(signal_data, self.cmd)
        dialog.approved.connect(self._on_signal_approved)
        dialog.rejected.connect(self._on_signal_rejected)
        dialog.exec()

    def _on_scanner_error(self, error: str):
        """Handle cloud scanner error."""
        self.cmd.log(
            f'<span style="color:#F85149;font-weight:bold">[CLOUD] SCANNER ERROR</span>: '
            f'{error}'
        )
        self.ai_narrator.notify_error(f"Scanner: {error}")

    def _on_listener_error(self, error: str):
        """Handle signal listener error."""
        self.cmd.log(
            f'<span style="color:#F85149;font-weight:bold">[SAT] LISTENER ERROR</span>: '
            f'{error}'
        )
        self.ai_narrator.notify_error(f"Listener: {error}")

    def _mark_bridge_alive(self):
        """Record a healthy external-brain handshake or signal."""
        self.bridge_last_seen_ts = time.time()
        self.bridge_warning_emitted = False
        if self.bridge_status != "online":
            self.bridge_status = "online"
            self.cmd.set_bridge_status("online")

    def _check_bridge_heartbeat(self):
        """Flip the bridge indicator red when the external heartbeat goes stale.
        KILL SWITCH OVERRIDE: Disabled per user request.
        External Brain heartbeat failures will NOT trigger kill switch.
        Scanner keeps trades alive even if AI Brain is slow."""
        if self.bridge_last_seen_ts <= 0:
            if self.bridge_status != "disconnected":
                self.bridge_status = "disconnected"
                self.cmd.set_bridge_status("disconnected")
            return
        
        elapsed = time.time() - self.bridge_last_seen_ts
        if elapsed > self.bridge_timeout_seconds:
            if self.bridge_status != "lost":
                self.bridge_status = "lost"
                self.cmd.set_bridge_status("lost")
            if not self.bridge_warning_emitted:
                logger.warning("[SYSTEM] Warning: External Brain heartbeat lost (Kill Switch DISABLED)")
                self.cmd.log(
                    '<span style="color:#F85149;font-weight:bold">[SYSTEM] Warning: External Brain heartbeat lost — Kill Switch DISABLED, trades remain active.</span>'
                )
                self.bridge_warning_emitted = True
        elif self.bridge_status == "lost":
            self.bridge_status = "online"
            self.bridge_warning_emitted = False
            self.cmd.set_bridge_status("online")

    def _on_bridge_handshake_received(self, handshake_data: dict):
        """Show a high-visibility confirmation when the external brain connects."""
        self._mark_bridge_alive()
        logger.info("[BRIDGE] [GREEN] External Brain Connected & Authenticated")
        self.cmd.log(
            '<span style="color:#00FF41;font-weight:bold">[BRIDGE] [GREEN] External Brain Connected & Authenticated</span>'
        )
        source_ip = handshake_data.get("source_ip", "unknown")
        brain_name = handshake_data.get("brain", "external")
        self.cmd.log(
            f'<span style="color:#8B949E">[SAT] HANDSHAKE</span>: {brain_name} @ {source_ip}'
        )
        self.ai_narrator.set_status("standby", "External Brain Connected")
        self.ai_narrator.add_activity("[BRIDGE]", "External Brain handshake confirmed")

    def _send_side_by_side_command(self, action: str, confidence: float,
                                    entry_price: float = 0.0, stop_loss: float = 0.0,
                                    take_profit: float = 0.0) -> bool:
        """Compatibility shim for legacy callers.

        The old port-5555 "side-by-side" socket has been retired. This now
        forwards the request to the active surface via SurfaceRouter so the
        operator gets a real fill on whichever platform (TradingView or MT5)
        is currently armed.
        """
        if not action:
            return False
        ticker_hint = self.current_watchlist[0] if self.current_watchlist else self.ticker_selector

        try:
            router = SurfaceRouter(
                mt5_executor=self.mt5_executor,
                ghost_executor=self._ghost_executor,
            )
            browser_loop = getattr(self, "_browser_loop", None)
            quantity = float(getattr(config, "MT5_VOLUME", 0.1) or 0.1)
            result = router.execute(
                symbol=ticker_hint,
                action=action.upper(),
                quantity=quantity,
                stop_loss=stop_loss,
                take_profit=take_profit,
                browser_loop=browser_loop,
            )
            self.cmd.log(
                f'<span style="color:#58A6FF;font-weight:bold">[ROUTER]</span>: '
                f'{action.upper()} on {result.surface} ({result.latency_ms:.1f}ms) — {result.message}'
            )
            return result.success
        except Exception as exc:
            logger.exception("[ROUTER] _send_side_by_side_command failed: %s", exc)
            return False

    def _signal_dispatch_key(self, signal_data: dict) -> str:
        """Stable key used to confirm the HTTP listener saw a scanner signal."""
        ticker = str(signal_data.get("ticker", "UNKNOWN") or "UNKNOWN").upper()
        action = str(signal_data.get("action", signal_data.get("signal_type", "UNKNOWN")) or "UNKNOWN").upper()
        confidence = self._confidence_to_score(signal_data.get("confidence", 0.0))
        entry_price = float(signal_data.get("entry_price", 0.0) or 0.0)
        reason = str(signal_data.get("reason", signal_data.get("signal_type", "")) or "")[:80]
        return f"{ticker}|{action}|{confidence:.1f}|{entry_price:.4f}|{reason}"

    def _mark_signal_listener_seen(self, signal_data: dict) -> None:
        """Record that the local HTTP dispatcher delivered this signal into main.py."""
        if not hasattr(self, "_listener_signal_seen"):
            self._listener_signal_seen = {}
        now = time.time()
        self._listener_signal_seen = {
            key: ts for key, ts in self._listener_signal_seen.items()
            if now - ts < 30.0
        }
        self._listener_signal_seen[self._signal_dispatch_key(signal_data)] = now

    def _cloud_signal_listener_watchdog(self, signal_data: dict) -> None:
        """
        Autonomous fail-safe: CloudScannerThread emits after posting to the local
        dispatcher. If the Qt callback from that dispatcher is not observed,
        route the same payload directly into the normal execution pipeline.
        """
        if self.current_mode != "AUTONOMOUS":
            return

        action = str(signal_data.get("action", "") or "").upper()
        if action not in {"BUY", "SELL"}:
            return

        confidence_score = self._confidence_to_score(signal_data.get("confidence", 0.0))
        if not self._is_loud_signal(confidence_score, signal_data):
            return

        key = self._signal_dispatch_key(signal_data)
        seen = getattr(self, "_listener_signal_seen", {})
        if key in seen and time.time() - seen[key] < 10.0:
            logger.info("[BRIDGE] Listener callback confirmed for autonomous signal %s", key)
            return

        ticker = signal_data.get("ticker", "UNKNOWN")
        self.cmd.log(
            f'<span style="color:#D29922;font-weight:bold">[BRIDGE]</span>: '
            f'HTTP listener callback was not observed for {action} {ticker}; routing directly to execution pipeline'
        )
        logger.warning("[BRIDGE] Listener callback missing; direct autonomous routing for %s", key)
        self._on_signal_received(dict(signal_data))

    def _tradingview_execution_selectors(self) -> dict:
        """Selectors used by the browser-native fallback when screen coords fail."""
        return {
            "buy_market": "button[data-type=\"buy-mkt\"]",
            "sell_market": "button[data-type=\"sell-mkt\"]",
            "close_popup": (
                "[class*=\"popup\"] [class*=\"close\"], "
                "[class*=\"modal\"] [class*=\"close\"], "
                "[role=\"dialog\"] [class*=\"close\"], "
                "[class*=\"modal\"] button"
            ),
            "generic_button_query": "button, div[role=\"button\"], span[role=\"button\"]",
        }

    def _resolve_autonomous_entry_target(self, action: str, ticker: str) -> dict | None:
        """
        Resolve the exact TradingView click target for autonomous execution.

        This mirrors the working Force Hand Test payload: ask RPA for active
        viewport/window adjusted coordinates, then fall back to calibrated
        TradingView coordinates from config so the socket gets a concrete target.
        """
        normalized_action = str(action or "").upper()
        target_key = (
            "buy_button" if normalized_action == "BUY"
            else "sell_button" if normalized_action == "SELL"
            else "flatten_button"
        )

        try:
            target_info = self.rpa_hand.describe_entry_target(normalized_action, ticker_hint=ticker)
            if target_info and target_info.get("absolute"):
                target_info["source"] = target_info.get("source", "rpa_executor")
                return target_info
        except Exception as exc:
            logger.warning("[TARGET] RPA target resolution failed for %s %s: %s", normalized_action, ticker, exc)

        fallback = getattr(config, "FALLBACK_COORDS", {}).get(target_key)
        if fallback:
            abs_x, abs_y = fallback
            logger.warning(
                "[TARGET] Using configured TradingView fallback for %s %s: abs=(%s,%s)",
                normalized_action,
                ticker,
                abs_x,
                abs_y,
            )
            return {
                "point_name": target_key,
                "absolute": (int(abs_x), int(abs_y)),
                "relative": (int(abs_x), int(abs_y)),
                "source": "config_fallback",
            }

        return None

    def _execute_cloud_signal(self, signal_data: dict):
        """Execute a cloud-generated signal locally with Professor Mode controls."""
        brain_override_action = self._brain_override_action(signal_data)
        action = brain_override_action if brain_override_action in {"BUY", "SELL"} else self._resolve_directional_action(signal_data)
        signal_data["action"] = action
        ticker = signal_data.get("ticker", "UNKNOWN")
        entry_price = float(signal_data.get("entry_price", 0.0) or 0.0)
        brain_verdict = str(signal_data.get("brain_verdict", "") or "").strip().upper()
        brain_reasoning = str(signal_data.get("brain_reasoning", "") or "").strip()
        brain_model = str(signal_data.get("brain_model", "") or "").strip()
        brain_used = str(signal_data.get("brain_used", "OPENROUTER") or "OPENROUTER").strip().upper()
        fallback_mode = bool(signal_data.get("fallback_mode"))
        # SAFETY: force_execute comes ONLY from explicit manual override or test mode.
        # Brain approval (BUY/SELL) no longer bypasses safety gates.
        force_execute = bool(signal_data.get("force_execute"))
        rsi_value = float(signal_data.get("rsi", signal_data.get("RSI", 50.0)) or 50.0)
        signal_data["force_execute"] = force_execute
        signal_data["brain_used"] = brain_used
        signal_data["fallback_mode"] = fallback_mode
        self._sync_brain_runtime_ui(brain_used, fallback_mode)
        raw_confidence_score = self._confidence_to_score(
            signal_data.get("confidence", self.latest_confidence_score)
        )
        vibe_context = dict(signal_data.get("vibe_context") or {})
        penalty_info = self.sql_journal.get_vibe_penalty(ticker, vibe_context)
        confidence_score = max(0.0, raw_confidence_score - float(penalty_info.get("penalty", 0.0)))
        signal_data["vibe_memory_penalty"] = float(penalty_info.get("penalty", 0.0))

        if self.current_watchlist and ticker not in self.current_watchlist:
            logger.info("EXEC_CLOUD: blocked inactive dashboard ticker %s", ticker)
            self._log_gatekeeper_abort(
                "Watchlist",
                f"{ticker} | ticker is not armed in the dashboard watchlist",
            )
            self.cmd.log(f"[WARN] Trade blocked: {ticker} is not active on dashboard")
            self._on_ticker_status_update(ticker, "trade_rejected")
            return

        # -- Balance Reconciliation Gate -----------------------------------
        if not self._reconcile_balance():
            self._log_gatekeeper_abort(
                "Balance",
                f"{ticker} | balance divergence detected; trading halted",
            )
            self._on_ticker_status_update(ticker, "trade_rejected")
            return

        logger.info(
            "EXEC_CLOUD: start action=%s ticker=%s raw_confidence=%.2f adjusted_confidence=%.2f entry=%.4f force_execute=%s brain=%s brain_verdict=%s model=%s",
            action,
            ticker,
            raw_confidence_score,
            confidence_score,
            entry_price,
            force_execute,
            brain_used,
            brain_verdict,
            brain_model,
        )

        if ticker in self.locked_tickers and not force_execute:
            remaining = int((config.RE_ENTRY_LOCKOUT_MINUTES * 60) - (time.time() - self.locked_tickers[ticker]))
            if remaining > 0:
                self.cmd.log(f"[WAIT] LOCKOUT: {ticker} cooling down for {remaining}s")
                self.cmd.update_watchlist_status(
                    ticker,
                    f"[WAIT] LOCKOUT {remaining}s",
                    confidence=confidence_score / 100.0,
                    last_signal=action,
                )
                self._refresh_lockout_timer()
                return

        if penalty_info.get("summary") and not force_execute:
            self.cmd.log(
                f'<span style="color:#58A6FF;font-weight:bold">[BRAIN] VIBE MEMORY</span>: '
                f'{penalty_info["summary"]} (penalty -{penalty_info["penalty"]:.0f})'
            )

        if penalty_info.get("block") and penalty_info.get("matched_patterns") and not force_execute:
            logger.info("EXEC_CLOUD: blocked by repeated losing vibe for %s", ticker)
            self.analysis_mode_status = "REJECTED - Losing Vibe Pattern"
            self.cmd.update_copilot_status("TRADE SKIPPED: Losing vibe pattern")
            self.ai_narrator.set_status("error", "TRADE SKIPPED: Losing vibe pattern")
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[STOP] VIBE MEMORY BLOCKED</span>: '
                f'{ticker} matches a recent losing pattern. Await a cleaner setup.'
            )
            self._on_ticker_status_update(ticker, "trade_rejected")
            return

        # -- Confidence Gate -----------------------------------------------
        if confidence_score < config.MIN_CONFIDENCE_THRESHOLD and not force_execute:
            logger.info("EXEC_CLOUD: rejected by confidence gate for %s %s", action, ticker)
            self._log_gatekeeper_abort(
                "Confidence",
                f"{action} {ticker} | confidence {confidence_score:.0f}% below execution threshold {config.MIN_CONFIDENCE_THRESHOLD:.0f}%",
            )
            self.analysis_mode_status = "REJECTED - Low Confidence"
            self.cmd.update_copilot_status("TRADE SKIPPED: Criteria not met")
            self.ai_narrator.set_status("error", "TRADE SKIPPED: Criteria not met")
            self.cmd.log(
                f'<span style="color:#D29922;font-weight:bold">[WARN] TRADE SKIPPED: Criteria not met</span> '
                f'- Confidence {confidence_score:.0f}% < {config.MIN_CONFIDENCE_THRESHOLD:.0f}% required'
            )
            self._on_ticker_status_update(ticker, "trade_rejected")
            return
        elif confidence_score < config.MIN_CONFIDENCE_THRESHOLD and force_execute:
            logger.info("EXEC_CLOUD: force_execute bypassed confidence gate for %s %s", action, ticker)
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[DICE] FORCE EXECUTE</span>: '
                f'bypassing confidence gate for {action} {ticker} at {confidence_score:.0f}%'
            )

        # Bypass incubation for high-strength liquidity signals
        is_high_strength_liquidity = (
            signal_data.get("signal_type", "").startswith("LIQUIDITY")
            and confidence_score >= 0.82
        )

        if self._is_incubation_signal(confidence_score, signal_data) and not force_execute and not is_high_strength_liquidity:
            logger.info(
                "EXEC_CLOUD: blocked by swarm incubation gate for %s %s at %.1f%%",
                action,
                ticker,
                confidence_score,
            )
            self.cmd.update_watchlist_status(
                ticker,
                "incubating",
                confidence=confidence_score / 100.0,
                last_signal=action,
            )
            self.cmd.log(
                f'<span style="color:#D29922;font-weight:bold">[INCUBATION]</span>: '
                f'{action} {ticker} held silently at {confidence_score:.0f}% pending 85% or positive paper-trade validation'
            )
            return

        if action not in ["BUY", "SELL"]:
            logger.info("EXEC_CLOUD: unsupported action %s for %s", action, ticker)
            self.cmd.log(f"[WARN] Unsupported action for execution: {action}")
            return

        if entry_price <= 0:
            logger.info("EXEC_CLOUD: invalid entry price for %s (%s)", ticker, entry_price)
            self._log_gatekeeper_abort(
                "Price Validation",
                f"{ticker} | no valid detected setup price for {action}",
            )
            self.cmd.log(f"[WARN] No valid entry price for {ticker} - cannot execute trade")
            self.ai_narrator.notify_error(f"No entry price for {ticker}")
            return

        if not self.rpa_execution_enabled and not force_execute:
            self.cmd.log(f"[NO_ENTRY] RPA hand paused - blocked cloud execution for {action} {ticker}")
            self._on_ticker_status_update(ticker, "trade_rejected")
            return

        # Fast focus/duplicate gate: reject parked symbols before expensive audits,
        # ATR planning, or RPA setup. The later gate stays as defense in depth.
        capacity_ok_early, capacity_note_early = self._check_position_capacity(ticker, action)
        if not capacity_ok_early:
            logger.info(
                "EXEC_CLOUD: early position/focus block for %s %s (%s)",
                action,
                ticker,
                capacity_note_early,
            )
            self._on_ticker_status_update(ticker, "focus_locked")
            return

        should_exec_early, conflict_note_early = self._check_position_conflict(
            ticker,
            action,
            confidence_score=confidence_score,
        )
        if not should_exec_early:
            logger.info(
                "EXEC_CLOUD: early position conflict block for %s %s (%s)",
                action,
                ticker,
                conflict_note_early,
            )
            self._on_ticker_status_update(ticker, "trade_rejected")
            return

        vetoed, veto_reason, veto_status = self._check_rsi_veto(action, rsi_value)
        if vetoed and not force_execute:
            self.cmd.log(f"[SHIELD] [VETO] RSI blocked {action} {ticker}: {veto_reason}")
            self.cmd.update_watchlist_status(
                ticker,
                veto_status,
                confidence=confidence_score / 100.0,
                last_signal=action,
            )
            self.ai_narrator.notify_trade_rejected(ticker)
            return

        if not self._run_pretrade_market_audit(ticker, entry_price, force_execute=force_execute):
            self._on_ticker_status_update(ticker, "trade_rejected")
            return

        # Check if trading is paused due to news
        if self.financial_safety.trading_paused and not force_execute:
            logger.info("EXEC_CLOUD: blocked by financial_safety pause for %s", ticker)
            self._log_gatekeeper_abort(
                "News",
                f"{ticker} | {self.financial_safety.pause_reason}"
            )
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[STOP] NEWS FILTER BLOCKED</span>: '
                f'{ticker} - {self.financial_safety.pause_reason}'
            )
            self.ai_narrator.notify_error(f"News filter blocked: {ticker}")
            return
        elif self.financial_safety.trading_paused and force_execute:
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[BRAIN] OPENROUTER OVERRIDE</span>: '
                f'bypassing news pause for {action} {ticker}'
            )

        # -- Trading Hours Gate --------------------------------------------
        if config.TRADING_START_HOUR_UTC >= 0 and config.TRADING_END_HOUR_UTC >= 0:
            from datetime import datetime, timezone
            now_utc = datetime.now(timezone.utc)
            current_hour = now_utc.hour
            in_window = False
            start = config.TRADING_START_HOUR_UTC
            end = config.TRADING_END_HOUR_UTC
            if start <= end:
                in_window = start <= current_hour < end
            else:
                # Window crosses midnight (e.g. 23:00 to 08:00)
                in_window = current_hour >= start or current_hour < end
            if not in_window and not force_execute:
                logger.info("EXEC_CLOUD: blocked outside trading hours for %s (UTC hour=%d, window=%d-%d)", ticker, current_hour, start, end)
                self._log_gatekeeper_abort(
                    "Hours",
                    f"{ticker} | outside trading window (UTC {start:02d}:00-{end:02d}:00)"
                )
                self.cmd.log(f"[STOP] OUTSIDE TRADING HOURS (UTC {start:02d}:00-{end:02d}:00) - {ticker}")
                self.ai_narrator.notify_error("Outside trading hours")
                return

        levels = self.support_resistance_levels.get(ticker, {
            "supports": signal_data.get("support_levels", []),
            "resistances": signal_data.get("resistance_levels", []),
        })

        # -- PositionSizer Risk Gate ---------------------------------------
        open_risk = self._get_open_risk_dollars()
        sizer = PositionSizer(balance=self.balance, risk_pct=1.0, open_risk=open_risk)
        risk_eval = sizer.evaluate(entry_price=entry_price, side=action, levels=levels)
        auto_risk_enabled = bool(self.settings.get("auto_risk_enabled", True))
        tp_price = 0.0
        if auto_risk_enabled:
            # ATR-DRIVEN STOP LOSS & TAKE PROFIT
            # The bot uses the 14-period ATR to set stops that breathe with
            # volatility. No more fixed-dollar thresholds that get stopped out
            # by noise on NQ but are too wide on CL.
            atr_value = float(signal_data.get("atr", 0.0) or 0.0)
            if atr_value <= 0:
                # Fallback: estimate ATR from recent price action if scanner didn't provide it
                atr_value = entry_price * 0.005  # 0.5% of price as rough ATR proxy

            atr_sl_multiplier = float(getattr(config, "ATR_STOP_MULTIPLIER", 1.5))
            atr_tp_multiplier = float(getattr(config, "ATR_TP_MULTIPLIER", 3.0))
            atr_stop_distance = atr_value * atr_sl_multiplier
            atr_tp_distance = atr_value * atr_tp_multiplier

            # Primary: ATR-based stop
            if action == "BUY":
                atr_sl = entry_price - atr_stop_distance
                atr_tp = entry_price + atr_tp_distance
            else:
                atr_sl = entry_price + atr_stop_distance
                atr_tp = entry_price - atr_tp_distance

            # Secondary: Structure-based stop (nearest S/R level)
            structure_sl = self._derive_structure_stop_loss(action, entry_price, signal_data, risk_eval)

            # Use the TIGHTER of ATR stop and structure stop (more protective)
            # but never tighter than 0.3% (avoids noise stop-outs)
            min_stop_distance = entry_price * 0.003  # 0.3% absolute minimum
            if structure_sl > 0:
                if action == "BUY":
                    sl_price = max(atr_sl, structure_sl)  # Tighter = higher for BUY
                    sl_price = min(sl_price, entry_price - min_stop_distance)  # But not too tight
                else:
                    sl_price = min(atr_sl, structure_sl)  # Tighter = lower for SELL
                    sl_price = max(sl_price, entry_price + min_stop_distance)
            else:
                sl_price = atr_sl

            tp_price = atr_tp

            # Log the ATR-driven risk plan
            rr_ratio = atr_tp_distance / max(atr_stop_distance, 0.0001)
            logger.info(
                "[ATR-RISK] %s %s | ATR=%.4f | SL=%.4f (%.2f%% from entry) | "
                "TP=%.4f (%.2f%% from entry) | R:R=%.1f:1",
                ticker, action, atr_value,
                sl_price, abs(entry_price - sl_price) / entry_price * 100,
                tp_price, abs(tp_price - entry_price) / entry_price * 100,
                rr_ratio,
            )
            self.cmd.log(
                f'<span style="color:#00D4FF;font-weight:bold">[ATR RISK]</span> '
                f'{action} {ticker} | ATR={atr_value:.2f} | '
                f'SL=${sl_price:.2f} | TP=${tp_price:.2f} | R:R={rr_ratio:.1f}:1'
            )
        else:
            sl_price, tp_price = self._manual_risk_targets(action, entry_price)
            self.cmd.log(
                f"[TARGET] Manual risk profile active on {ticker}: TP {self.settings.get('take_profit_pct', 2.0):.1f}% / "
                f"SL {self.settings.get('stop_loss_pct', 1.0):.1f}%"
            )
        stop_distance_pct = (
            (abs(entry_price - sl_price) / entry_price) * 100.0
            if entry_price > 0 and sl_price > 0
            else 0.0
        )
        actual_risk_reason = (
            f"{'Structure' if auto_risk_enabled else 'Manual'} stop @ ${sl_price:.4f} ({stop_distance_pct:.2f}% distance)"
            if sl_price > 0
            else "No valid liquidity-structure stop available"
        )

        if (sl_price <= 0 or stop_distance_pct > PositionSizer.MAX_STOP_DISTANCE_PCT) and not force_execute:
            logger.info("EXEC_CLOUD: rejected by risk gate for %s - %s", ticker, actual_risk_reason)
            self._log_gatekeeper_abort(
                "Risk",
                f"{ticker} | {actual_risk_reason}"
            )
            self.analysis_mode_status = "REJECTED - Too Risky"
            self.cmd.update_copilot_status("TRADE SKIPPED: Criteria not met")
            self.ai_narrator.set_status("error", "TRADE SKIPPED: Criteria not met")
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[STOP] TRADE SKIPPED: Criteria not met</span> '
                f'- RiskScore=High | {actual_risk_reason}'
            )
            self.ai_narrator.add_activity("[STOP]", f"TRADE SKIPPED - {actual_risk_reason[:80]}")
            self._on_ticker_status_update(ticker, "trade_rejected")
            return
        elif sl_price <= 0 or stop_distance_pct > PositionSizer.MAX_STOP_DISTANCE_PCT:
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[BRAIN] OPENROUTER OVERRIDE</span>: '
                f'bypassing risk gate for {action} {ticker} | {actual_risk_reason}'
            )

        per_unit_risk = abs(entry_price - sl_price)

        # Shared futures point values keep risk sizing aligned with execution symbols.
        multiplier = self.profit_lock._point_value_for_asset(ticker)
        dollar_risk_per_contract = per_unit_risk * multiplier

        risk_dollar = max(float(risk_eval.get("risk_amount") or 0.0), self.balance * 0.01)
        quantity = (risk_dollar / dollar_risk_per_contract) if dollar_risk_per_contract > 0 else 0.0

        logger.info(
            "[POSITION] %s %s | per_unit_risk=%.4f multiplier=%.2f risk_per_contract=$%.2f "
            "risk_dollar=$%.2f quantity=%.4f",
            ticker, action, per_unit_risk, multiplier, dollar_risk_per_contract, risk_dollar, quantity,
        )

        # MICRO TEST MODE: force 1 contract for MNQ/MES/MCL during testing
        if ticker in {"NQ=F", "ES=F", "CL=F"}:
            quantity = 1.0
            logger.info("[POSITION] MICRO TEST MODE: forcing quantity=1 for %s", ticker)

        if quantity <= 0 and force_execute:
            fallback_amount = float(signal_data.get("investment_amount", self.default_investment) or self.default_investment or 1000.0)
            quantity = max(fallback_amount, 100.0) / max(entry_price, 1.0)
            risk_dollar = abs(entry_price - sl_price) * quantity
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[BRAIN] OPENROUTER OVERRIDE</span>: '
                f'using fallback quantity {quantity:.4f} for {action} {ticker}'
            )
        elif quantity <= 0:
            logger.info("EXEC_CLOUD: invalid quantity after sizing for %s (%s)", ticker, quantity)
            self._log_gatekeeper_abort(
                "Risk",
                f"{ticker} | invalid quantity after sizing"
            )
            self.cmd.log(f"[WARN] Invalid risk sizing for {ticker}; aborting trade")
            return

        amount = quantity * entry_price
        safe_amount, size_mode = self.financial_safety.calculate_safe_position_size(
            amount,
            self.daily_pnl,
            self.max_daily_loss,
        )
        if size_mode.value != "normal" and amount > 0:
            scale = safe_amount / amount
            quantity *= max(0.0, scale)
            amount = quantity * entry_price
            self.cmd.log(
                f'<span style="color:#D29922;font-weight:bold">[RULER] SAFETY MODE</span>: '
                f'Position reduced to ${amount:.2f} ({size_mode.value})'
            )

        if self.daily_pnl <= -self.max_daily_loss and not force_execute:
            logger.info("EXEC_CLOUD: blocked by max daily loss for %s", ticker)
            self._log_gatekeeper_abort(
                "Risk",
                f"{ticker} | max daily loss reached (${self.daily_pnl:.2f} / -${self.max_daily_loss:.2f})"
            )
            self.cmd.log(f"[STOP] MAX DAILY LOSS REACHED (${self.daily_pnl:.2f} / -${self.max_daily_loss:.2f}) - Trading halted")
            self.ai_narrator.notify_error("Max daily loss reached")
            return
        elif self.daily_pnl <= -self.max_daily_loss and force_execute:
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[BRAIN] OPENROUTER OVERRIDE</span>: '
                f'bypassing max-daily-loss lock for {action} {ticker}'
            )

        # Prop firm risk gate uses hard-stop implied loss.
        if self.prop_engine:
            potential_loss = abs(entry_price - sl_price) * quantity
            can_trade, violations = self.prop_engine.check_before_trade(ticker, potential_loss)
            if not can_trade and not force_execute:
                logger.info("EXEC_CLOUD: blocked by prop rules for %s: %s", ticker, violations)
                self._log_gatekeeper_abort(
                    "Risk",
                    f"{ticker} | {'; '.join(violations) if violations else 'prop firm rule violation'}"
                )
                self.cmd.log(f'<span style="color:#F85149;font-weight:bold">[STOP] PROP FIRM BLOCKED</span>: {ticker}')
                for v in violations:
                    self.cmd.log(f'<span style="color:#F85149">   {v}</span>')
                self.ai_narrator.notify_error(f"Prop firm blocked: {ticker}")
                return
            elif not can_trade:
                self.cmd.log(
                    f'<span style="color:#F85149;font-weight:bold">[BRAIN] OPENROUTER OVERRIDE</span>: '
                    f'bypassing prop rules for {action} {ticker}'
                )

        # -- EXECUTION: UI (RPA) or MT5 --
        if self._is_mt5_mode():
            self._execute_cloud_signal_mt5(
                ticker, action, entry_price, sl_price, tp_price,
                quantity, amount, signal_data, brain_used,
                confidence_score, risk_dollar, vibe_context
            )
            return

        # -- TradingView UI path: execute through GhostExecutor/RPA.
        # Legacy broker-specific futures whitelists do not apply here.

        # FORCE GHOSTEXECUTOR: When enabled, never use RPA mouse movement
        if getattr(self._ghost_executor, "enabled", False):
            logger.info("EXEC_CLOUD: Using GhostExecutor (JS injection) for %s %s", action, ticker)
            success = False
            try:
                browser_loop = getattr(self, "_browser_loop", None)
                if browser_loop is not None and not browser_loop.is_closed():
                    future = asyncio.run_coroutine_threadsafe(
                        self._ghost_executor.execute_trade(
                            ticker,
                            action,
                            volume=float(quantity or config.MT5_VOLUME),
                        ),
                        browser_loop,
                    )
                    success = bool(future.result(timeout=5.0))
                else:
                    success = bool(
                        asyncio.run(
                            self._ghost_executor.execute_trade(
                                ticker,
                                action,
                                volume=float(quantity or config.MT5_VOLUME),
                            )
                        )
                    )
            except Exception as exc:
                logger.exception("EXEC_CLOUD: GhostExecutor failed for %s %s: %s", action, ticker, exc)
            if success:
                logger.info("EXEC_CLOUD: GhostExecutor successfully executed %s %s", action, ticker)
            return success

        # AGGRESSIVE BYPASS: High-strength liquidity signals execute immediately
        is_high_strength_liquidity = (
            signal_data.get("signal_type", "").startswith("LIQUIDITY") and confidence_score >= 0.82
        )

        if is_high_strength_liquidity:
            logger.info("EXEC_CLOUD: HIGH-STRENGTH LIQUIDITY BYPASS for %s %s (%.0f%%)", action, ticker, confidence_score * 100)
            # Skip all remaining gates (MTF, LEVEL2, incubation, etc.)
            pass

        if not (signal_data.get("signal_type", "").startswith("LIQUIDITY") and confidence_score >= 0.82):
            try:
                mtf_passed, mtf_votes = self._passes_mtf_sniper_gate(ticker, action, signal_data=signal_data, confidence=confidence_score)
            except Exception as e:
                if "pandas_ta" in str(e).lower():
                    mtf_passed, mtf_votes = True, {"override": "MTF_SKIPPED_NO_PANDAS_TA"}
                else:
                    mtf_passed, mtf_votes = False, {"error": str(e)}
        else:
            mtf_passed, mtf_votes = True, {"override": "HIGH_STRENGTH_LIQUIDITY_BYPASS"}
        if not mtf_passed and not force_execute:
            logger.info("EXEC_CLOUD: blocked by MTF sniper gate for %s %s: %s", action, ticker, mtf_votes)
            self._log_gatekeeper_abort(
                "MTF",
                f"{action} {ticker} | votes={mtf_votes}"
            )
            self.cmd.log(
                f'<span style="color:#D29922;font-weight:bold">[TARGET] SNIPER GATE BLOCKED</span>: '
                f'{action} {ticker} rejected by 5m/3m/1m alignment {mtf_votes}'
            )
            self.ai_narrator.add_activity("[TARGET]", f"MTF gate blocked {action} {ticker}")
            self._on_ticker_status_update(ticker, "trade_rejected")
            return
        elif mtf_votes.get("override") == "AGGRESSIVE_HUNTER":
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[FIRE] AGGRESSIVE HUNTER</span>: '
                f'{action} {ticker} confidence={confidence_score:.0f}% — striking on 5m alone!'
            )
            self.ai_narrator.add_activity("[FIRE]", f"Aggressive Hunter armed for {action} {ticker}")
        elif mtf_votes.get("override") == "REVERSAL_BUY_1M_RSI_OVERSOLD":
            self.cmd.log(
                f'<span style="color:#3FB950;font-weight:bold">[REFRESH] REVERSAL OVERRIDE</span>: '
                f'BUY {ticker} allowed against SELL-biased MTF because 1m RSI is oversold ({mtf_votes.get("1m_rsi")})'
            )
            self.ai_narrator.add_activity("[REFRESH]", f"Reversal override armed for BUY {ticker}")
        elif mtf_votes.get("override") == "STRONG_5M_BUY_NEUTRAL_1M":
            self.cmd.log(
                f'<span style="color:#3FB950;font-weight:bold">[TARGET] STRONG 5M BUY</span>: '
                f'BUY {ticker} allowed while 1m is neutral (5m RSI {mtf_votes.get("5m_rsi")})'
            )
            self.ai_narrator.add_activity("[TARGET]", f"Strong 5m BUY override armed for {ticker}")
        elif not mtf_passed:
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[BRAIN] OPENROUTER OVERRIDE</span>: '
                f'bypassing MTF gate for {action} {ticker} {mtf_votes}'
            )

        signal_data["stop_loss"] = sl_price
        signal_data["take_profit"] = tp_price
        signal_data["liquidity_label"] = self._format_liquidity_label(signal_data)
        self._broadcast_trade_levels(signal_data)

        if config.DRY_RUN:
            self.cmd.log(
                f'<span style="color:#3FB950;font-weight:bold">[DRY RUN]</span>: '
                f'would execute {action} {ticker} @ ${entry_price:.2f}; no live RPA click sent'
            )
            logger.info("EXEC_CLOUD: dry-run blocked live RPA click for %s %s", action, ticker)
            self._on_ticker_status_update(ticker, "dry_run")
            return

        # Position Safety Check for cloud RPA path
        capacity_ok_rpa, capacity_note_rpa = self._check_position_capacity(ticker, action)
        if not capacity_ok_rpa:
            self.cmd.log(
                f'<span style="color:#8B949E;font-weight:bold">[SKIP]</span> '
                f'Cloud signal {action} {ticker} skipped: {capacity_note_rpa}'
            )
            return

        should_exec_rpa, conflict_note_rpa = self._check_position_conflict(
            ticker,
            action,
            confidence_score=confidence_score,
        )
        if not should_exec_rpa:
            self.cmd.log(
                f'<span style="color:#8B949E;font-weight:bold">[SKIP]</span> '
                f'Cloud signal {action} {ticker} skipped: {conflict_note_rpa}'
            )
            return

        # Short position: SL above price, TP below price (already computed above)
        rpa_trade = TradeRecord(
            asset=ticker,
            action=SignalAction.BUY if action == "BUY" else SignalAction.SELL,
            entry_price=entry_price,
            stop_loss=sl_price,
            take_profit=tp_price if tp_price > 0 else None,
            confidence=ConfidenceLevel.HIGH if confidence_score >= 80 else ConfidenceLevel.MEDIUM,
            ai_reason=signal_data.get("reason", ""),
            mode="AUTONOMOUS",
        )

        if force_execute and brain_verdict in {"[SIGNAL] BUY", "[SIGNAL] SELL"}:
            verdict_reason = brain_reasoning or f"{brain_used} approved {action} {ticker}"
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[BRAIN] FINAL CYBERNETIC HANDSHAKE</span>: '
                f'{brain_verdict} from {brain_used} for {ticker}'
            )
            self.ai_narrator.flash_brain_verdict(
                ticker,
                brain_verdict,
                verdict_reason,
                hold_ms=3000,
                fallback_mode=fallback_mode,
                brain_used=brain_used,
            )
            focus_locked = self.rpa_hand.bring_tradingview_to_front(ticker_hint=ticker)
            logger.info("EXEC_CLOUD: pre-strike TradingView focus for %s -> %s", ticker, focus_locked)
            if not focus_locked:
                self.cmd.log(
                    f'<span style="color:#F85149;font-weight:bold">[WARN] STRIKE BLOCKED</span>: '
                    f'could not bring TradingView to front for {ticker}'
                )
                self.ai_narrator.notify_error(f"TradingView focus failed: {ticker}")
                return

        liquidity_zone = signal_data.get("liquidity_zone")
        if liquidity_zone:
            zone_drawn = self.rpa_hand.draw_liquidity_zone(ticker, liquidity_zone)
            logger.info("EXEC_CLOUD: liquidity zone draw for %s -> %s", ticker, zone_drawn)
        target_info = self._resolve_autonomous_entry_target(action, ticker)
        tradingview_selectors = self._tradingview_execution_selectors()
        if target_info:
            signal_data["rpa_target"] = target_info
            signal_data["tradingview_selectors"] = tradingview_selectors
            rel_x, rel_y = target_info["relative"]
            abs_x, abs_y = target_info["absolute"]
            logger.info(
                "EXEC_CLOUD: %s %s target=%s relative=(%s,%s) absolute=(%s,%s)",
                action,
                ticker,
                target_info["point_name"],
                rel_x,
                rel_y,
                abs_x,
                abs_y,
            )
            self.cmd.log(
                f'<span style="color:#58A6FF;font-weight:bold">[TARGET] RPA TARGET</span>: '
                f'{action} {ticker} -> {target_info["point_name"]} rel=({rel_x}, {rel_y}) abs=({abs_x}, {abs_y})'
            )
            self.cmd.log(
                f'<span style="color:#58A6FF;font-weight:bold">[SOCKET] AUTONOMOUS STRIKE PACKET</span>: '
                f'{action} {ticker} target={target_info["point_name"]} abs=({abs_x}, {abs_y}) source={target_info.get("source", "unknown")}'
            )
        else:
            logger.warning("EXEC_CLOUD: no entry target resolved for %s %s", action, ticker)
            self.cmd.log(
                f'<span style="color:#D29922;font-weight:bold">[WARN] RPA TARGET</span>: '
                f'no calibrated {action} button coordinates resolved for {ticker}; socket will fall back to TradingView selectors'
            )
        if not self.rpa_execution_enabled:
            self.cmd.log(f"[NO_ENTRY] Manual stealth toggle blocked execution for {action} {ticker}")
            self.cmd.update_watchlist_status(
                ticker,
                "[NO_ENTRY] Hand Disabled",
                confidence=confidence_score / 100.0,
                last_signal=action,
            )
            self.ai_narrator.notify_error(f"RPA Hand paused: {ticker}")
            return

        # HYBRID GATEWAY: broker WebSocket -> local socket -> MT5 -> TradingView JS.
        rpa_success = False
        execution_route = "legacy_rpa"
        try:
            if bool(getattr(config, "LOW_LATENCY_EXECUTION_ENABLED", True)):
                ghost = getattr(self, "_ghost_executor", None)
                if ghost and self.browser_agent and self.browser_agent.page:
                    ghost.set_page(self.browser_agent.page)
                if ghost:
                    ghost.set_mt5_executor(self.mt5_executor)
                gateway = getattr(self, "hybrid_gateway", None)
                if gateway:
                    gateway.mt5_executor = self.mt5_executor
                    gateway.ghost_executor = ghost
                    gateway_loop = asyncio.new_event_loop()
                    try:
                        gateway_result = gateway_loop.run_until_complete(
                            gateway.execute(
                                symbol=ticker,
                                action=action,
                                confidence=confidence_score,
                                quantity=float(quantity or config.MT5_VOLUME),
                                entry_price=entry_price,
                                stop_loss=sl_price,
                                take_profit=tp_price,
                                target=target_info or {},
                                selectors=tradingview_selectors,
                                browser_loop=getattr(self, "_browser_loop", None),
                            )
                        )
                    finally:
                        gateway_loop.close()

                    execution_route = gateway_result.route
                    rpa_success = gateway_result.success
                    self.cmd.log(
                        f'<span style="color:{"#3FB950" if rpa_success else "#D29922"};font-weight:bold">'
                        f'[HYBRID]</span>: {execution_route} '
                        f'{"filled" if rpa_success else "failed"} in {gateway_result.latency_ms:.1f}ms'
                    )
                    if not rpa_success and gateway_result.message:
                        logger.warning("[HYBRID] %s", gateway_result.message)

            # FINAL FALLBACK: legacy local RPA only.
            if not rpa_success:
                self.cmd.log(
                    f'<span style="color:#D29922">[BACKUP]</span>: '
                    f'Falling back to legacy RPA execution for {action} {ticker}'
                )
                rpa_success = self.rpa_hand.execute_trade(rpa_trade)
                execution_route = "legacy_rpa"
        except Exception as exec_err:
            # SILENT ERROR ALERT: Voice + Pop-up notification
            error_msg = f"RPA Execution FAILED for {action} {ticker}: {exec_err}"
            logger.critical(f"[SIREN] EXECUTION ERROR: {error_msg}")
            
            # Voice Alert (Windows TTS)
            try:
                import ctypes
                # Simple Windows voice alert via SAPI
                sapi = ctypes.windll.LoadLibrary("sapi.dll")
                # Fallback: use PowerShell for voice alert
                import subprocess
                subprocess.run(
                    ["powershell", "-Command", 
                     f"Add-Type -AssemblyName System.Speech; "
                     f"$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                     f"$synth.Speak('Alert. Trade execution failed for {ticker}.')"],
                    capture_output=True, timeout=2
                )
            except Exception:
                pass  # Voice not available, continue with popup
            
            # Pop-up Alert (PyQt6 MessageBox)
            from PyQt6.QtWidgets import QMessageBox
            from PyQt6.QtCore import Qt
            msg_box = QMessageBox()
            msg_box.setIcon(QMessageBox.Icon.Critical)
            msg_box.setWindowTitle("[SIREN] TRADE EXECUTION FAILED")
            msg_box.setText(f"Failed to execute {action} order for {ticker}")
            msg_box.setInformativeText(error_msg)
            msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg_box.exec()
            
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[SIREN] EXECUTION ALERT</span>: {error_msg}'
            )
        
        order_status = "executed" if rpa_success else "execution_failed"
        logger.info(
            "EXEC_CLOUD: execution result for %s %s via %s -> %s",
            action, ticker, execution_route, order_status
        )
        # ALERT SOUND: Play distinctive tone on every trade execution
        _play_trade_alert(action, rpa_success)
        self.cmd.log(
            f'<span style="color:{"#3FB950" if rpa_success else "#F85149"};font-weight:bold">'
            f'{"[OK] EXECUTED" if rpa_success else "[WARN] EXECUTION FAILED"}</span>: '
            f'{action} {ticker} @ ${entry_price:.2f}'
        )
        # Mirror the chart: ensure TradingView is on the right ticker
        self.ai_narrator.add_activity("[MONITOR]", f"Taking over screen -> {action} {ticker}" + (" [OK]" if rpa_success else " [FAIL]"))

        if not rpa_success:
            self.cmd.log(
                f'<span style="color:#F85149">[RECEIPT] JOURNAL</span>: skipped DB save because no confirmed position was opened for {ticker}'
            )
            self._on_ticker_status_update(ticker, "rpa_failed")
            return

        journal_id = self.sql_journal.save_trade(
            coin=ticker,
            entry=entry_price,
            stop_loss=sl_price,
            ai_confidence=confidence_score,
            ai_reasoning=signal_data.get("reason", "No reasoning provided"),
            brain_used=brain_used,
            outcome="OPEN",
        )
        self.sql_journal.save_trade_vibe(
            trade_id=journal_id,
            asset=ticker,
            vibe_context=vibe_context,
            confidence_penalty=float(signal_data.get("vibe_memory_penalty", 0.0)),
        )
        logger.info("EXEC_CLOUD: journal saved trade_id=%s for %s", journal_id, ticker)
        try:
            import sqlite3
            with sqlite3.connect("trades.db") as conn:
                row = conn.execute("SELECT id FROM trades WHERE id = ?", (journal_id,)).fetchone()
            logger.info("EXEC_CLOUD: trade journal verification for %s -> %s", ticker, bool(row))
        except Exception as e:
            logger.warning("EXEC_CLOUD: trade journal verification failed for %s: %s", ticker, e)

        position = {
            "asset": ticker,
            "side": action,
            "entry": entry_price,
            "current": entry_price,
            "amount": amount,
            "quantity": quantity,
            "tp_price": tp_price,
            "sl_price": sl_price,
            "point_value": self.profit_lock._point_value_for_asset(ticker),
            "initial_risk_amount": abs(entry_price - sl_price) * quantity * self.profit_lock._point_value_for_asset(ticker),
            "break_even_locked": False,
            "last_trailing_check_ts": 0.0,
            "peak_pnl": 0.0,
            "pnl": 0.0,
            "pnl_pct": 0.0,
            "order_id": f"rpa_{ticker}_{int(datetime.now().timestamp())}",
            "journal_id": journal_id,
            "liquidity_zone": signal_data.get("liquidity_zone"),
            "vibe_context": vibe_context,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "bot_opened": True,
        }

        self.profit_lock.add_position(
            asset=ticker,
            entry_price=entry_price,
            stop_loss=sl_price,
            take_profit=None,
            position_size=quantity,
        )
        self.positions.append(position)
        self.trades_today += 1

        self.cmd.update_positions(self.positions)
        self.cmd.add_trade_log(ticker, action, amount, 0, "Open")

        self.cmd.log(
            f'<span style="color:#3FB950;font-weight:bold">[OK] POSITION OPENED</span>: '
            f'{action} {ticker} @ ${entry_price:.2f} | Amount: ${amount:.2f} | '
            f'Qty: {quantity:.4f} | Managed SL: ${sl_price:.2f}'
        )
        self.cmd.log(
            f'<span style="color:#8B949E">[RECEIPT] JOURNAL</span>: SAVING TO DB... '
            f'Trade #{journal_id} | ORDER={order_status}'
        )
        reason_text = signal_data.get("reason", "n/a")
        self.cmd.log(
            f'<span style="color:#58A6FF">[CHART] Professor</span>: '
            f'Confidence[{confidence_score:.0f}%] | RISK: ${risk_dollar:.2f} | '
            f'REASON: {reason_text[:120]}'
        )
        # -- Mirror: Professor Dashboard Lines ----------------------------
        self.ai_narrator.add_activity("[GRADUATE]", f"Confidence [{confidence_score:.0f}%] {'HIGH CONVICTION' if confidence_score >= 92 else 'APPROVED'}")
        self.ai_narrator.add_activity("[SHIELD]", f"RISK: ${risk_dollar:.2f} | LEV: 5x | RiskScore: LOW")
        self.ai_narrator.add_activity("[BRAIN]", f"REASON: {reason_text[:80]}")
        self.ai_narrator.add_activity("[RECEIPT]", "SAVING TO DB...")

        if self.browser_agent and action in ["BUY", "SELL"]:
            self.cmd.log(f"[GLOBE] Browser agent verifying {ticker} price...")
            self._verify_price_with_browser(position)

        self.ai_narrator.notify_trade_executed(ticker, action, entry_price)
        live_positions_pnl = sum(p.get("pnl", 0.0) for p in self.positions)
        self.ai_narrator.update_live_pnl(self.total_pnl + live_positions_pnl, len(self.positions))
        self._refresh_live_ledger()

    async def _execute_with_unified_executor(self, signal_data: dict, force_execute: bool = False):
        """
        Execute trade using the Unified Trade Executor with Slippage Guard.
        This is the production-ready execution path with full safety checks.
        
        Args:
            signal_data: Trade signal data
            force_execute: If True, bypasses confidence and slippage guards (TEST MODE)
        """
        ticker = signal_data.get("ticker", "UNKNOWN")
        from core.market_sessions import is_crypto_ticker
        if not self.can_trade and not force_execute and not is_crypto_ticker(ticker) and not is_futures_ticker(ticker):
            self._log_ui(
                '<span style="color:#D29922;font-weight:bold">[APEX BLOCK]</span> '
                'Unified executor blocked — trading halted by Apex gate'
            )
            return

        action = signal_data.get("action", "HOLD")

        mode_label = "[BOLT] FORCE TEST" if force_execute else "[SUCCESS] UNIFIED EXECUTOR"
        self._log_ui(
            f'<span style="color:#00D4FF;font-weight:bold">{mode_label}</span>: '
            f'Processing {action} {ticker}'
        )

        # Check if executor is available
        if not self.executor:
            self._log_ui("[WARN] Trade executor not available - falling back to standard execution")
            if self.browser_agent:
                # Initialize executor now
                self.executor = UnifiedTradeExecutor(
                    browser_agent=self.browser_agent,
                    cmd_logger=self.cmd.log,
                    ai_narrator=self.ai_narrator,
                )
            else:
                self._log_ui("[FAIL] Browser agent not available - cannot execute trade")
                return

        # Execute via unified executor (includes Slippage Guard)
        try:
            auto_execute = self.current_mode == "AUTONOMOUS" and not config.DRY_RUN
            result = await self.executor.execute_signal(
                signal_data=signal_data,
                auto_execute=auto_execute,
                force_execute=force_execute,  # Bypass guards if requested
            )

            # Log result
            if result.status.value == "SUCCESS":
                self._log_ui(
                    f'<span style="color:#3FB950;font-weight:bold">[OK] EXECUTION SUCCESS</span>: '
                    f'{result.ticker} {result.action} @ ${result.execution_price:.2f} | '
                    f'Slippage: {result.slippage_pct:.3f}% | '
                    f'Spread: {result.spread_pct:.3f}%'
                )
                self._run_on_ui_thread(lambda: self._append_executor_position_ui(result, signal_data))

            else:
                # Execution failed or skipped
                color = "#F85149" if "ABORT" in result.status.value or "FAIL" in result.status.value else "#D29922"
                category, reason = self._gatekeeper_abort_from_execution_result(result)
                self._log_gatekeeper_abort(category, reason)
                self._log_ui(
                    f'<span style="color:{color};font-weight:bold">[WARN] {result.status.value}</span>: '
                    f'{result.ticker} | {result.error_message}'
                )

        except Exception as e:
            self._log_ui(f'<span style="color:#F85149;font-weight:bold">[FAIL] EXECUTOR ERROR</span>: {e}')
            import traceback
            self._log_ui(f'<span style="color:#F85149">[NOTE] {traceback.format_exc()}</span>')

    def flip_chart(self, symbol: str):
        """
        Immediately flip TradingView to the target symbol via keyboard RPA.
        Triggers vision confirmation and updates UI status.
        """
        self.cmd.log(f'<span style="color:#00D4FF;font-weight:bold">[SAT] CLOUD SIGNAL</span>: Processing {symbol}...')
        # self.cmd.set_vision_status(True, "Confirming...")  # Removed - no status indicators in new dashboard

        # 1. TradingView Flip Logic
        try:
            # Type the symbol and press enter
            pyautogui.typewrite(symbol, interval=0.1)
            time.sleep(0.5)
            pyautogui.press("enter")
            self.cmd.log(f'<span style="color:#3FB950">[OK] FLIPPED</span>: Chart switched to {symbol}')
        except Exception as e:
            self.cmd.log(f'<span style="color:#F85149">[FAIL] FLIP FAILED</span>: {e}')
            return

        # 2. Trigger Vision Confirmation
        # We create a temporary MarketDataPoint to feed the analyzer
        data_point = MarketDataPoint(
            asset=symbol,
            price=0.0, # Will be filled by vision/analysis if possible
            timestamp=datetime.now(timezone.utc),
        )
        self.analysis_worker.add_to_queue(data_point)
        self.cmd.log(f'<span style="color:#D29922">[EYE] VISION</span>: Final confirmation triggered for {symbol}')

    def _on_data_scout_signal(self, symbol: str):
        """Handler for signal from Data Scout (Vast.ai)."""
        self.flip_chart(symbol)

    def _on_watchtower_alert(self, alert: WatchlistAlert):
        """Handle Watchtower anomaly alert."""
        self.cmd.log(
            f'<span style="color:#F85149;font-weight:bold">WATCHTOWER</span>: '
            f"[{alert.severity}] {alert.alert_type} on {alert.asset} - {alert.reason}"
        )
        if hasattr(self.ai_narrator, "trigger_signal_alert"):
            self.ai_narrator.trigger_signal_alert(kind="warning")
        self.ai_narrator.add_activity("[WATCH]", f"{alert.alert_type} on {alert.asset}: {alert.reason[:90]}")
        _play_ui_alert("signal", "WATCH")
        _speak_alert(f"Watchtower alert on {alert.asset}.")

    def _on_analysis_complete(self, analysis, transcript: DebateTranscript = None):
        """Handle Swarm Consensus result - all UI updates on main thread."""
        # Handle failed analysis
        if analysis is None:
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[WARN] ANALYSIS FAILED</span>: '
                f'Swarm debate failed to complete. Skipping trade.'
            )
            return
            
        # Build overlay signal
        chart_price = self._sync_signal_to_visible_chart_price(analysis.asset)
        if chart_price and chart_price > 0:
            logger.info("[PRICE-LOCK] Visible chart price for %s: %.2f", analysis.asset, chart_price)

        overlay_signal = OverlaySignal(
            asset=analysis.asset,
            action=analysis.action,
            confidence=analysis.confidence,
            entry_price=chart_price if chart_price and chart_price > 0 else analysis.entry_price,
            stop_loss=analysis.stop_loss,
            take_profit=analysis.take_profit,
            reason=analysis.reason,
        )

        # Update dashboard
        # self.overlay.update_signal_handler(overlay_signal)  # Removed overlay
        entry_display = overlay_signal.entry_price or 0.0
        self.cmd.log(f"[CHART] Signal: {overlay_signal.action} {overlay_signal.asset} @ ${entry_display:.2f}")

        # Log debate to terminal
        if transcript:
            self.cmd.log(
                f'<span style="color:#8B949E">Sniper: [{transcript.technical_sniper.action}] '
                f"{transcript.technical_sniper.conviction}</span>"
            )
            self.cmd.log(
                f'<span style="color:#8B949E">Macro:  [{transcript.macro_analyst.action}] '
                f"{transcript.macro_analyst.conviction}</span>"
            )
            self.cmd.log(
                f'<span style="color:#8B949E">Risk:   [{transcript.risk_manager.verdict}] '
                f"{transcript.risk_manager.conviction}</span>"
            )

            # Display CEO verdict (ceo_verdict is a string, not an object)
            self.cmd.log(f"[TARGET] CEO Verdict: {transcript.ceo_verdict}")
            # self.overlay.update_debate_transcript(transcript)  # Removed overlay
        else:
            self.cmd.log(
                f"{analysis.action.value} {analysis.asset} - {analysis.confidence.value}"
            )

        # Process through trade engine
        trade = self.trade_engine.process_signal(analysis, self.current_mode)

        # Run post-trade autopsy if trade was closed
        if trade and trade.status == "CLOSED":
            autopsy = self.grader.autopsy_trade(trade)
            self.cmd.log(
                f'<span style="color:#D29922">AUTOPSY</span>: '
                f"{trade.asset} Grade: {autopsy.grade} - {autopsy.explanation[:100]}"
            )

        self.latest_signals[analysis.asset] = analysis

    def _on_mode_changed(self, mode: str):
        requested = str(mode or "").strip().upper()
        normalized = self._set_runtime_mode(requested, source="gui", manual=True)
        if normalized != requested:
            self._apply_mode_to_dashboard(normalized)
            self.cmd.log(f"[LOCK] {requested} blocked by config; staying in {normalized}")
        self.cmd.log(f"[REFRESH] Mode changed to: {normalized}")
        self.ai_narrator.set_status("idle", f"Mode: {normalized}")

    def _on_dry_run_changed(self, is_dry_run: bool):
        """Keep the runtime engine aligned with the dashboard dry-run toggle."""
        config.DRY_RUN = bool(is_dry_run)
        # PAPER mode no longer forces TEACHER. Autonomous + DRY_RUN means
        # "let the bot click on its own with simulated fills" — exactly what
        # paper trading is for.
        if is_dry_run:
            self.cmd.log("[PAPER] DRY_RUN is ON - orders will be simulated, no real money at risk")
        else:
            self.cmd.log("[LIVE] DRY_RUN is OFF - orders will be sent to your broker")
        self._set_runtime_mode(self.current_mode, source="dry_run")
        logger.info("Dry run changed to %s", config.DRY_RUN)

    def _on_ticker_changed(self, ticker: str):
        """Handle ticker selection change."""
        self.ticker_selector = ticker
        self.cmd.log(f'Ticker changed to: {ticker}')

    def _add_to_trade_ledger(self, signal_data: dict):
        """Add signal to trade ledger for UI display."""
        trade_record = {
            "asset": signal_data.get("ticker", "N/A"),
            "action": signal_data.get("action", "HOLD"),
            "price": signal_data.get("entry_price", 0.0),
            "confidence": signal_data.get("confidence", 0.0),
            "result": "PENDING",
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S")
        }
        self.trade_ledger.append(trade_record)

        # Keep only last 50 trades
        if len(self.trade_ledger) > 50:
            self.trade_ledger = self.trade_ledger[-50:]

        # Update UI - add to trade log
        self.cmd.add_trade_log(
            trade_record.get("asset", ""),
            trade_record.get("action", ""),
            trade_record.get("amount", 0),
            trade_record.get("pnl", 0),
            trade_record.get("status", "Closed")
        )

    def _on_kill_switch(self):
        self.trade_engine.activate_kill_switch()
        self.cloud_scanner.stop()
        self.signal_listener.stop()
        self.data_scout_listener.stop()
        self.watchtower.stop()
        if self.hunter:
            self.hunter.stop()
        logger.critical("Kill switch activated - all systems halted")

    def _on_hunter_status_update(self, symbol: str, status: str, message: str):
        """Update UI when the Multi-Asset Hunter cycles through symbols."""
        self.cmd.log(f"[HUNTER] {symbol} | {status}: {message}")
        self.ai_narrator.add_activity("[HUNTER]", f"{symbol} {status}")

        # Route hunter status to dashboard watchlist for live signal/confidence display
        import re as _re
        if status.startswith("SIGNAL_"):
            signal = status.replace("SIGNAL_", "")
            conf_match = _re.search(r'(\d+)%', message)
            confidence = int(conf_match.group(1)) / 100.0 if conf_match else 0.85
            self.cmd.update_watchlist_status(symbol, "awaiting_strike", confidence=confidence, last_signal=signal)
        elif status == "OBSERVING":
            self.cmd.update_watchlist_status(symbol, "scanning")
        elif status == "SCREENSHOT":
            self.cmd.update_watchlist_status(symbol, "scanning")
        elif status == "ANALYZING":
            self.cmd.update_watchlist_status(symbol, "analyzing_liquidity")
        elif status.startswith("SKIPPED_"):
            self.cmd.update_watchlist_status(symbol, "trade_rejected")
        elif status == "ERROR":
            self.cmd.update_watchlist_status(symbol, "trade_rejected")

    def _on_hunter_narrator_update(self, icon: str, message: str):
        """Thread-safe relay: Hunter thread -> main GUI thread -> Activity Feed."""
        if self.ai_narrator:
            self.ai_narrator.add_activity(icon, message)

    def _has_same_symbol_position(self, ticker: str) -> bool:
        return any(pos.get("asset") == ticker for pos in self.positions)

    def _check_position_capacity(self, ticker: str, action: str) -> tuple[bool, str]:
        """Block brand-new entries when the configured position cap is full."""
        if self._has_same_symbol_position(ticker):
            return True, "existing_symbol"
        if bool(getattr(config, "SINGLE_TRADE_FOCUS_MODE", True)) and self.positions:
            focus_asset = self.positions[0].get("asset", "active trade")
            logger.info(
                "[FOCUS] %s %s blocked while focusing on open position %s",
                ticker,
                action,
                focus_asset,
            )
            self.cmd.log(
                f'<span style="color:#58A6FF;font-weight:bold">[FOCUS MODE]</span> '
                f'{action} {ticker} blocked - managing {focus_asset} until it closes'
            )
            self.ai_narrator.add_activity("[FOCUS]", f"Managing {focus_asset}; new entries paused")
            return False, "single_trade_focus_active"
        max_positions = int(getattr(config, "MAX_OPEN_POSITIONS", 3) or 3)
        if len(self.positions) >= max_positions:
            logger.info(
                "[POSITION] %s %s blocked: max open positions reached %s/%s",
                ticker,
                action,
                len(self.positions),
                max_positions,
            )
            self.cmd.log(
                f'<span style="color:#D29922;font-weight:bold">[POSITION CAP]</span> '
                f'{action} {ticker} blocked: max open positions {len(self.positions)}/{max_positions}'
            )
            self.ai_narrator.add_activity("[LOCK]", f"Max positions reached: {len(self.positions)}/{max_positions}")
            return False, "max_open_positions"
        return True, "capacity_ok"

    def _flatten_profitable_opposite_position(
        self,
        position: dict,
        ticker: str,
        action: str,
        confidence_score: float,
    ) -> bool:
        """Competition mode: bank a profitable opposite position before a strong reversal."""
        if not bool(getattr(config, "COMPETITION_REVERSAL_FLATTEN_ENABLED", True)):
            return False

        min_confidence = float(getattr(config, "COMPETITION_REVERSAL_MIN_CONFIDENCE", 75.0))
        if float(confidence_score or 0.0) < min_confidence:
            return False

        open_profit = float(position.get("pnl", 0.0) or 0.0)
        min_profit = float(getattr(config, "COMPETITION_REVERSAL_MIN_OPEN_PROFIT_USD", 50.0))
        if open_profit < min_profit:
            return False

        flatten = getattr(self.rpa_hand, "flatten_position", None)
        if not callable(flatten):
            return False

        current_side = str(position.get("side", "") or "").upper()
        self.cmd.log(
            f'<span style="color:#F0B90B;font-weight:bold">[REVERSAL SHIELD]</span> '
            f'{ticker} has profitable {current_side} (${open_profit:.2f}); flattening before {action}'
        )
        try:
            success = bool(flatten(ticker))
        except Exception as exc:
            logger.error("[REVERSAL SHIELD] Flatten failed for %s: %s", ticker, exc)
            success = False

        if not success:
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[REVERSAL SHIELD]</span> '
                f'Could not flatten {ticker}; opposite {action} remains blocked'
            )
            return False

        self._close_position(position, "Competition Reversal Flatten")
        self.positions = [pos for pos in self.positions if pos is not position]
        self.cmd.update_positions(self.positions)
        return True

    def _check_position_conflict(
        self,
        ticker: str,
        action: str,
        is_closing: bool = False,
        confidence_score: float = 0.0,
    ) -> tuple[bool, str]:
        """
        Position Safety Check: If we already hold an opposing position,
        decide whether to close it first (Close-and-Reverse) or block.
        Returns (should_execute, note).
        Set is_closing=True when calling from a close-and-reverse close to prevent recursion.
        """
        action = str(action).upper()
        for pos in self.positions:
            if pos.get("asset") == ticker:
                current_side = str(pos.get("side", "")).upper()
                if current_side and current_side != action and not is_closing:
                    if not bool(getattr(config, "AUTONOMOUS_CLOSE_AND_REVERSE_ENABLED", False)):
                        if self._flatten_profitable_opposite_position(pos, ticker, action, confidence_score):
                            return True, "competition_reversal_flattened"
                        logger.info(
                            "[POSITION] %s already %s | Opposite %s blocked by close-and-reverse safety",
                            ticker,
                            current_side,
                            action,
                        )
                        self.cmd.log(
                            f'<span style="color:#D29922;font-weight:bold">[POSITION LOCK]</span> '
                            f'{ticker} already has a {current_side} position; opposite {action} signal blocked'
                        )
                        self.ai_narrator.add_activity(
                            "[LOCK]",
                            f"{ticker} opposite {action} blocked while {current_side} is open"
                        )
                        return False, "opposite_position_locked"

                    # Opposing position exists — Close-and-Reverse
                    close_action = "SELL" if current_side == "BUY" else "BUY"
                    logger.info(
                        "[POSITION] %s already %s | Signal: %s -> Close-and-Reverse",
                        ticker, current_side, action
                    )
                    self.cmd.log(
                        f'<span style="color:#D29922;font-weight:bold">[REVERSAL]</span> '
                        f'{ticker} reversing {current_side} -> {action} | Closing first'
                    )
                    self.ai_narrator.add_activity(
                        "[REVERSAL]",
                        f"{ticker} closing {current_side} position before {action}"
                    )
                    # Dispatch the close trade first (marked as closing to prevent recursion)
                    self._dispatch_trade_execution(ticker, close_action, "Close-and-Reverse close")
                    time.sleep(1.5)  # Brief pause for broker fill
                    return True, "close_and_reverse"
                elif current_side == action and not is_closing:
                    # Same-side position already open — skip duplicate
                    logger.info("[POSITION] %s %s position already open — skipping duplicate", ticker, action)
                    self.cmd.log(
                        f'<span style="color:#8B949E;font-weight:bold">[SKIP]</span> '
                        f'{ticker} {action} position already open'
                    )
                    return False, "already_open"
        return True, "no_conflict"

    def _dispatch_trade_execution(self, symbol: str, action: str, reason: str = "") -> bool:
        """
        Unified trade execution dispatcher.
        Routes to MT5 or TradingView (RPA) based on active mode.
        Returns True if execution succeeded.
        """
        action = str(action or "").upper()
        if action not in {"BUY", "SELL"}:
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[BLOCKED]</span> '
                f'Unsupported trade action: {action or "EMPTY"}'
            )
            return False
        if not self.rpa_execution_enabled:
            self.cmd.log(
                f'<span style="color:#D29922;font-weight:bold">[NO_ENTRY]</span> '
                f'RPA hand paused - blocked Hunter execution for {action} {symbol}'
            )
            return False

        from core.market_sessions import is_crypto_ticker, is_futures_ticker
        is_always_on = is_crypto_ticker(symbol) or is_futures_ticker(symbol)
        if not self.can_trade and not is_always_on:
            self.cmd.log(
                f'<span style="color:#D29922;font-weight:bold">[BLOCKED]</span> '
                f'Trade execution blocked: Apex gate or safety stop active'
            )
            return False

        plan = self._build_simple_execution_plan(symbol, action, reason)
        if not plan.get("ok"):
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[BLOCKED]</span> '
                f'{action} {symbol}: {plan.get("reason", "invalid trade plan")}'
            )
            logger.warning("[EXECUTION] Refused unprotected %s %s: %s", action, symbol, plan.get("reason"))
            return False

        self.cmd.log(
            f'<span style="color:#58A6FF;font-weight:bold">[RISK PLAN]</span> '
                f'{plan["risk_text"]}'
        )

        def _record_hunter_position(route: str) -> None:
            amount = plan["quantity"] * plan["entry_price"]
            position = {
                "asset": symbol,
                "side": action,
                "entry": plan["entry_price"],
                "current": plan["entry_price"],
                "amount": amount,
                "quantity": plan["quantity"],
                "tp_price": plan["take_profit"],
                "sl_price": plan["stop_loss"],
                "point_value": self.profit_lock._point_value_for_asset(symbol),
                "initial_risk_amount": (
                    abs(plan["entry_price"] - plan["stop_loss"])
                    * plan["quantity"]
                    * self.profit_lock._point_value_for_asset(symbol)
                ),
                "break_even_locked": False,
                "last_trailing_check_ts": 0.0,
                "peak_pnl": 0.0,
                "pnl": 0.0,
                "pnl_pct": 0.0,
                "order_id": f"hunter_{route}_{symbol}_{int(datetime.now().timestamp())}",
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "bot_opened": True,
                "source": "HUNTER",
                "ai_reason": plan["reason"],
            }
            self.positions.append(position)
            self.cmd.update_positions(self.positions)
            self.cmd.add_trade_log(symbol, action, amount, 0, "Open")
            self.ai_narrator.update_live_pnl(self.total_pnl + sum(p.get("pnl", 0.0) for p in self.positions), len(self.positions))
            self.cmd.log(
                f'<span style="color:#3FB950;font-weight:bold">[TRACKED]</span> '
                f'Hunter position tracked: {action} {symbol} | SL=${plan["stop_loss"]:.2f} TP=${plan["take_profit"]:.2f}'
            )

        # Position Safety Check: Close-and-Reverse or skip duplicate
        capacity_ok, capacity_note = self._check_position_capacity(symbol, action)
        if not capacity_ok:
            return False

        should_exec, conflict_note = self._check_position_conflict(
            symbol,
            action,
            confidence_score=float(getattr(config, "COMPETITION_REVERSAL_MIN_CONFIDENCE", 75.0)),
        )
        if not should_exec:
            return False

        if self._is_mt5_mode():
            mt5_executor = self._get_mt5_executor()
            if mt5_executor is None:
                self.cmd.log(
                    '<span style="color:#F85149;font-weight:bold">[MT5 FAIL]</span> '
                    'MetaTrader 5 mode selected but executor is unavailable'
                )
                return False
            self.cmd.log(
                f'<span style="color:#00D4FF;font-weight:bold">[MT5]</span> '
                f'Sending protected {action} {symbol} to MetaTrader 5'
            )
            success = mt5_executor.execute_trade(
                symbol,
                action,
                volume=plan["quantity"],
                stop_loss=plan["stop_loss"],
                take_profit=plan["take_profit"],
            )
            if success:
                self.cmd.log(
                    f'<span style="color:#3FB950;font-weight:bold">[MT5 OK]</span> '
                    f'{action} {symbol} executed on MT5 | SL=${plan["stop_loss"]:.2f} TP=${plan["take_profit"]:.2f}'
                )
                self.ai_narrator.add_activity("[MT5]", f"{action} {symbol}")
                _record_hunter_position("mt5")
            else:
                self.cmd.log(
                    f'<span style="color:#F85149;font-weight:bold">[MT5 FAIL]</span> '
                    f'MT5 execution failed for {action} {symbol}'
                )
            return success
        else:
            # UI mode: use RPA hand
            try:
                from core.models import TradeRecord, SignalAction, ConfidenceLevel
                trade = TradeRecord(
                    asset=symbol,
                    action=SignalAction.BUY if action == "BUY" else SignalAction.SELL,
                    entry_price=plan["entry_price"],
                    stop_loss=plan["stop_loss"],
                    take_profit=plan["take_profit"],
                    confidence=ConfidenceLevel.MEDIUM,
                    ai_reason=plan["reason"],
                    mode="HUNTER",
                    status="OPEN",
                )
                # Position Safety Check for Hunter RPA path
                should_exec_hunter, _ = self._check_position_conflict(
                    symbol,
                    action,
                    confidence_score=float(getattr(config, "COMPETITION_REVERSAL_MIN_CONFIDENCE", 75.0)),
                )
                if not should_exec_hunter:
                    return False
                success = self.rpa_hand.execute_trade(trade)
                if success:
                    self.cmd.log(
                        f'<span style="color:#3FB950;font-weight:bold">[OK]</span> '
                        f'RPA executed {action} {symbol} | SL=${plan["stop_loss"]:.2f} TP=${plan["take_profit"]:.2f}'
                    )
                    self.ai_narrator.add_activity("[OK]", f"RPA {action} {symbol}")
                    _record_hunter_position("rpa")
                else:
                    failure = getattr(self.rpa_hand, "last_failure_reason", "RPA hand failed")
                    self.cmd.log(
                        f'<span style="color:#F85149;font-weight:bold">[FAIL]</span> '
                        f'RPA execution failed: {failure}'
                    )
                return success
            except Exception as e:
                logger.error("[RPA] Execution error: %s", e)
                self.cmd.log(
                    f'<span style="color:#F85149">[FAIL]</span> RPA execution error: {e}'
                )
                return False

    def _execute_cloud_signal_mt5(
        self,
        ticker: str,
        action: str,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        quantity: float,
        amount: float,
        signal_data: dict,
        brain_used: str,
        confidence_score: float,
        risk_dollar: float,
        vibe_context: str,
    ):
        """MT5 execution path for cloud signals. Skips RPA pre-work and sends directly to MT5."""
        execution_ticker = self._resolve_mt5_execution_symbol(ticker, signal_data)
        self.cmd.log(
            f'<span style="color:#00D4FF;font-weight:bold">[MT5]</span> '
            f'Cloud signal {action} {ticker} -> MetaTrader 5'
            + (f' as {execution_ticker}' if execution_ticker != ticker else '')
        )
        mt5_executor = self._get_mt5_executor()
        if mt5_executor is None:
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[MT5 FAIL]</span> '
                f'Cloud signal {action} {ticker} aborted because MT5 executor is unavailable'
            )
            self._on_ticker_status_update(ticker, "mt5_unavailable")
            return

        if bool(getattr(config, "MT5_REQUIRE_PROTECTIVE_STOP", True)) and sl_price <= 0:
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">[MT5 FAIL]</span> '
                f'{action} {ticker} blocked: no protective stop-loss was calculated'
            )
            self._on_ticker_status_update(ticker, "mt5_missing_stop")
            return

        # PRIMARY: MT5 native order_send with protective SL/TP attached.
        self.cmd.log(
            f'<span style="color:#00D4FF;font-weight:bold">[MT5 PROTECTED]</span> '
            f'Sending {action} {execution_ticker} with SL={sl_price:.4f} TP={tp_price:.4f}'
        )
        success = mt5_executor.execute_trade(
            execution_ticker,
            action,
            volume=quantity,
            stop_loss=sl_price,
            take_profit=tp_price,
        )
        order_status = "mt5_executed" if success else "mt5_failed"
        # ALERT SOUND: Play distinctive tone on every trade execution
        _play_trade_alert(action, success)

        self.cmd.log(
            f'<span style="color:{"#3FB950" if success else "#F85149"};font-weight:bold">'
            f'{"[MT5 OK] ORDER FILLED" if success else "[MT5 FAIL] ORDER REJECTED"}</span>: '
            f'{action} {execution_ticker}'
        )

        if not success:
            self.cmd.log(
                f'<span style="color:#F85149">[RECEIPT] JOURNAL</span>: '
                f'skipped DB save because MT5 order was not filled for {execution_ticker}'
            )
            self._on_ticker_status_update(ticker, "mt5_failed")
            return

        # Journal & Position tracking (mirrors RPA post-execution logic)
        signal_data["requested_ticker"] = ticker
        signal_data["execution_ticker"] = execution_ticker
        journal_id = self.sql_journal.save_trade(
            coin=execution_ticker,
            entry=entry_price,
            stop_loss=sl_price,
            ai_confidence=confidence_score,
            ai_reasoning=signal_data.get("reason", "No reasoning provided"),
            brain_used=brain_used,
            outcome="OPEN",
        )
        self.sql_journal.save_trade_vibe(
            trade_id=journal_id,
            asset=execution_ticker,
            vibe_context=vibe_context,
            confidence_penalty=float(signal_data.get("vibe_memory_penalty", 0.0)),
        )
        logger.info("EXEC_CLOUD MT5: journal saved trade_id=%s for %s", journal_id, execution_ticker)

        position = {
            "asset": execution_ticker,
            "requested_asset": ticker,
            "side": action,
            "entry": entry_price,
            "current": entry_price,
            "amount": amount,
            "quantity": quantity,
            "tp_price": tp_price,
            "sl_price": sl_price,
            "point_value": self.profit_lock._point_value_for_asset(execution_ticker),
            "initial_risk_amount": abs(entry_price - sl_price) * quantity * self.profit_lock._point_value_for_asset(execution_ticker),
            "break_even_locked": False,
            "last_trailing_check_ts": 0.0,
            "peak_pnl": 0.0,
            "pnl": 0.0,
            "pnl_pct": 0.0,
            "order_id": f"mt5_{execution_ticker}_{int(datetime.now().timestamp())}",
            "journal_id": journal_id,
            "liquidity_zone": signal_data.get("liquidity_zone"),
            "vibe_context": vibe_context,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "bot_opened": True,
        }

        self.profit_lock.add_position(
            asset=execution_ticker,
            entry_price=entry_price,
            stop_loss=sl_price,
            take_profit=None,
            position_size=quantity,
        )
        self.positions.append(position)
        self.trades_today += 1

        self.cmd.update_positions(self.positions)
        self.cmd.add_trade_log(execution_ticker, action, amount, 0, "Open")

        self.cmd.log(
            f'<span style="color:#3FB950;font-weight:bold">[OK] POSITION OPENED (MT5)</span>: '
            f'{action} {execution_ticker} @ ${entry_price:.2f} | Amount: ${amount:.2f} | '
            f'Qty: {quantity:.4f} | Managed SL: ${sl_price:.2f}'
        )

    def _resolve_mt5_execution_symbol(self, ticker: str, signal_data: dict) -> str:
        """
        In MT5 mode, prefer the visible chart's broker symbol when vision can
        read it and it belongs to the same instrument group.
        """
        if not getattr(config, "AUTO_SYMBOL_DETECTION", True):
            return ticker

        try:
            vision = getattr(self.analysis_worker, "vision", None)
            if vision is None:
                vision = VisionCapture(save_debug=config.SAVE_DEBUG_SCREENSHOTS)

            screenshot = vision.capture_active_chart(asset=ticker)
            if not screenshot:
                return ticker

            from core.brain_swarm import detect_symbol_details_from_chart

            _wait_for_vision_analysis_slot(f"symbol-detect:{ticker}")
            details = detect_symbol_details_from_chart(
                screenshot.to_base64(),
                model=config.MULTI_ASSET_VISION_MODEL,
                timeout=45,
            )
            if not details:
                return ticker

            signal_data["vision_symbol_details"] = details
            detected_symbol = str(details.get("mt5_symbol") or details.get("raw_symbol") or "").strip()
            analysis_symbol = str(details.get("analysis_symbol") or "").strip()
            instrument_name = str(details.get("instrument_name") or detected_symbol or "").strip()

            if detected_symbol and (
                root_matches(ticker, detected_symbol)
                or root_matches(ticker, analysis_symbol)
                or ticker in {"UNKNOWN", ""}
            ):
                self.cmd.log(
                    f'<span style="color:#58A6FF;font-weight:bold">[SMART SYMBOL]</span>: '
                    f'Vision read {detected_symbol} as {instrument_name}; using MT5 broker symbol'
                )
                logger.info(
                    "[SMART SYMBOL] %s resolved to MT5 broker label %s via vision details=%s",
                    ticker,
                    detected_symbol,
                    details,
                )
                return detected_symbol

            logger.warning(
                "[SMART SYMBOL] Vision read %s (%s), but it did not match signal ticker %s. Keeping original ticker.",
                detected_symbol,
                instrument_name,
                ticker,
            )
            self.cmd.log(
                f'<span style="color:#D29922;font-weight:bold">[SMART SYMBOL]</span>: '
                f'Vision read {detected_symbol or "unknown"}, but signal was {ticker}; keeping signal symbol'
            )
            return ticker
        except Exception as exc:
            logger.warning("[SMART SYMBOL] MT5 symbol auto-detection failed for %s: %s", ticker, exc)
            return ticker
        self.cmd.log(
            f'<span style="color:#8B949E">[RECEIPT] JOURNAL</span>: SAVING TO DB... '
            f'Trade #{journal_id} | ORDER={order_status}'
        )
        reason_text = signal_data.get("reason", "n/a")
        self.cmd.log(
            f'<span style="color:#58A6FF">[CHART] Professor</span>: '
            f'Confidence[{confidence_score:.0f}%] | RISK: ${risk_dollar:.2f} | '
            f'REASON: {reason_text[:120]}'
        )
        self.ai_narrator.add_activity("[GRADUATE]", f"Confidence [{confidence_score:.0f}%] {'HIGH CONVICTION' if confidence_score >= 92 else 'APPROVED'}")
        self.ai_narrator.add_activity("[SHIELD]", f"RISK: ${risk_dollar:.2f} | LEV: 5x | RiskScore: LOW")
        self.ai_narrator.add_activity("[BRAIN]", f"REASON: {reason_text[:80]}")
        self.ai_narrator.add_activity("[RECEIPT]", "SAVING TO DB...")

        if self.browser_agent and action in ["BUY", "SELL"]:
            self.cmd.log(f"[GLOBE] Browser agent verifying {ticker} price...")
            self._verify_price_with_browser(position)

        self.ai_narrator.notify_trade_executed(ticker, action, entry_price)
        live_positions_pnl = sum(p.get("pnl", 0.0) for p in self.positions)
        self.ai_narrator.update_live_pnl(self.total_pnl + live_positions_pnl, len(self.positions))
        self._refresh_live_ledger()

    def _reconcile_mt5_positions(self):
        """
        Reality Check: Query MT5 for actual open positions and sync internal state.
        If MT5 shows no positions, reset internal memory to match reality.
        Runs every 30 seconds when active mode is MT5.
        """
        mt5_executor = self._get_mt5_executor()
        if not mt5_executor or not mt5_executor.initialized:
            return

        try:
            mt5_positions = mt5_executor.get_positions()
            mt5_symbols = {p["symbol"] for p in mt5_positions}
            internal_symbols = {p["asset"] for p in self.positions}

            # 1. Positions in MT5 but missing internally -> add them
            for p in mt5_positions:
                if p["symbol"] not in internal_symbols:
                    logger.warning(
                        "[RECONCILE] MT5 has %s %s that bot did not track. Adding to internal state.",
                        p["type"], p["symbol"]
                    )
                    self.cmd.log(
                        f'<span style="color:#D29922;font-weight:bold">[RECONCILE]</span> '
                        f'MT5 has {p["type"]} {p["symbol"]} (ticket {p["ticket"]}) that bot did not track. Syncing...'
                    )
                    self.positions.append({
                        "asset": p["symbol"],
                        "side": p["type"],
                        "entry": p["open_price"],
                        "current": p["current_price"],
                        "amount": 0.0,
                        "quantity": p["volume"],
                        "tp_price": 0.0,
                        "sl_price": 0.0,
                        "pnl": p["profit"],
                        "pnl_pct": 0.0,
                        "order_id": f"mt5_sync_{p['ticket']}",
                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                    })

            # 2. Positions internally but missing in MT5 -> remove them
            removed = []
            for pos in list(self.positions):
                if pos["asset"] not in mt5_symbols:
                    logger.warning(
                        "[RECONCILE] Bot thought %s was open, but MT5 shows it closed. Removing.",
                        pos["asset"]
                    )
                    self.cmd.log(
                        f'<span style="color:#D29922;font-weight:bold">[RECONCILE]</span> '
                        f'Bot thought {pos["side"]} {pos["asset"]} was open, but MT5 shows closed. Resetting.'
                    )
                    if pos in self.positions:
                        self.positions.remove(pos)
                    else:
                        logger.warning("[RECONCILE] Position %s not in list, skipping removal", pos.get("asset"))
                    removed.append(pos["asset"])

            # 3. Update P&L for positions that exist in both
            for pos in self.positions:
                for mt5_pos in mt5_positions:
                    if pos["asset"] == mt5_pos["symbol"]:
                        pos["current"] = mt5_pos["current_price"]
                        pos["pnl"] = mt5_pos["profit"]
                        break

            if removed or len(mt5_positions) != len(internal_symbols):
                self.cmd.update_positions(self.positions)
                self._refresh_live_ledger()
                self.ai_narrator.add_activity(
                    "[RECONCILE]",
                    f"Synced {len(mt5_positions)} MT5 positions"
                )
            else:
                logger.debug("[RECONCILE] Internal state matches MT5. No action needed.")

        except Exception as e:
            logger.error("[RECONCILE] Position reconciliation failed: %s", e)
            self.cmd.log(f'<span style="color:#F85149">[RECONCILE ERROR]</span> {e}')

    def check_apex_closing_time(self):
        """
        Apex Safety Gate: Enforce market close discipline.
        - After 16:30 ET: Block new trades (can_trade = False)
        - After 16:45 ET: Force-close all open positions
        Called every 30 seconds via heartbeat timer.
        """
        try:
            from zoneinfo import ZoneInfo
            from core.market_sessions import is_crypto_ticker, is_futures_ticker
            now_et = datetime.now(ZoneInfo("America/New_York"))
            hour = now_et.hour
            minute = now_et.minute
            time_val = hour * 100 + minute  # e.g. 1630 for 4:30 PM

            # CRYPTO + FUTURES NEVER BLOCKED: Both trade nearly 24/7
            watchlist = getattr(self, "current_watchlist", [])
            positions = getattr(self, "positions", [])
            has_always_on = (
                any(is_crypto_ticker(t) or is_futures_ticker(t) for t in watchlist)
                or any(is_crypto_ticker(p.get("asset", "")) or is_futures_ticker(p.get("asset", "")) for p in positions)
            )
            all_always_on = (
                (bool(watchlist) and all(is_crypto_ticker(t) or is_futures_ticker(t) for t in watchlist))
                or (bool(positions) and all(is_crypto_ticker(p.get("asset", "")) or is_futures_ticker(p.get("asset", "")) for p in positions))
            )
            # If watchlist/positions are purely crypto or futures, skip Apex gate entirely
            if all_always_on:
                if not self.can_trade:
                    self.can_trade = True
                    logger.info("[APEX] Crypto/futures-only mode — Apex gate lifted, trading ALLOWED 24/7")
                return
            # If mixed, individual execution paths use is_crypto_ticker/is_futures_ticker to bypass
            if has_always_on and not self.can_trade:
                logger.info("[APEX] Mixed watchlist — crypto/futures bypass Apex block, equities blocked")

            # After 4:30 PM ET — block new trades
            if time_val >= 1630 and self.can_trade:
                self.can_trade = False
                logger.warning("[APEX] Market closing time reached (16:30 ET). Equity trades BLOCKED.")
                self.cmd.log(
                    '<span style="color:#D29922;font-weight:bold">[APEX GATE]</span> '
                    '16:30 ET reached — Equity trades BLOCKED until next session'
                )
                self.ai_narrator.add_activity("[APEX]", "16:30 ET — No equity trades")

            # After 4:45 PM ET — force close equity-only positions (crypto+futures preserved)
            if time_val >= 1645 and self.positions:
                safe_positions = [p for p in self.positions if is_crypto_ticker(p.get("asset", "")) or is_futures_ticker(p.get("asset", ""))]
                equity_positions = [p for p in self.positions if not (is_crypto_ticker(p.get("asset", "")) or is_futures_ticker(p.get("asset", "")))]

                if equity_positions:
                    logger.warning(
                        "[APEX] Forced position flattening at 16:45 ET. Non-crypto positions: %s. "
                        "Crypto positions PRESERVED: %s",
                        [p["asset"] for p in equity_positions],
                        [p["asset"] for p in safe_positions],
                    )
                    self.cmd.log(
                        '<span style="color:#F85149;font-weight:bold">[APEX GATE]</span> '
                        '16:45 ET — FORCING CLOSE of non-crypto positions (crypto preserved)'
                    )
                    self.ai_narrator.add_activity("[APEX]", "16:45 ET — Flattening non-crypto positions")

                    # Close non-crypto via executor if available
                    if self.executor:
                        try:
                            for p in equity_positions:
                                self.executor.close_position(p.get("asset", ""), reason="Apex 16:45 ET forced close")
                        except Exception as e:
                            logger.error("[APEX] Executor close_position failed: %s", e)

                    # Close non-crypto via MT5 if in MT5 mode
                    if self._is_mt5_mode():
                        mt5_executor = self._get_mt5_executor()
                    else:
                        mt5_executor = None
                    if mt5_executor:
                        try:
                            mt5_positions = mt5_executor.get_positions()
                            for p in mt5_positions:
                                if is_crypto_ticker(p.get("symbol", "")) or is_futures_ticker(p.get("symbol", "")):
                                    continue  # Skip crypto+futures positions
                                close_action = "SELL" if p["type"] == "BUY" else "BUY"
                                mt5_executor.execute_trade(p["symbol"], close_action, volume=p["volume"])
                                logger.info("[APEX] MT5 closed %s %s", p["type"], p["symbol"])
                        except Exception as e:
                            logger.error("[APEX] MT5 position close failed: %s", e)

                    # Update positions: keep crypto+futures, remove equities
                    self.positions = safe_positions
                    self.cmd.update_positions(self.positions)
                    self._refresh_live_ledger()
                    self.ai_narrator.notify_error("Equity positions flattened — Apex closing time (crypto+futures preserved)")
                else:
                    # All positions are crypto — do nothing
                    logger.info("[APEX] 16:45 ET — All positions are crypto, no flattening needed")

        except Exception as e:
            logger.error("[APEX] check_apex_closing_time error: %s", e)

    def _on_hunter_trade_signal(
        self,
        symbol: str,
        action: str,
        reason: str,
        confidence: int = 50,
        threat: str = "MEDIUM",
    ):
        """Execute a trade when the Cloud Brain returns BUY/SELL from vision analysis."""
        confidence_score = max(0.0, min(100.0, float(confidence or 50)))
        self.cmd.log(
            f'<span style="color:#00D4FF;font-weight:bold">[HUNTER]</span> '
            f'Vision signal: {action} {symbol} | {confidence_score:.0f}% | Threat: {threat}'
        )
        self.ai_narrator.add_activity("[HUNTER]", f"{action} {symbol}")
        self.ai_narrator.notify_signal_detected(symbol, action, confidence_score / 100.0)
        self._announce_signal_alert(symbol, action, confidence_score, source="hunter")

        if config.TEACHER_MODE:
            self.cmd.log(f"[TEACHER] Would execute {action} {symbol} ({confidence_score:.0f}%) | {reason}")
            return

        self._dispatch_trade_execution(symbol, action, reason)

    def _on_calibrate(self):
        """Open the RPA Coordinate Mapper wizard."""
        dialog = CalibrationWizardDialog(self.cmd)
        dialog.calibration_complete.connect(self._refresh_calibration_status)
        dialog.exec()

    def _on_test_vision(self):
        """Capture a screenshot and display it for sanity check."""
        if not self.analysis_worker.vision:
            self.cmd.log("Vision Engine not available - cannot test")
            return

        screenshot = self.analysis_worker.vision.capture_active_chart(asset="TEST")
        if screenshot:
            self.cmd.log(
                f"Vision test: captured {screenshot.dimensions[0]}x{screenshot.dimensions[1]} "
                f"({screenshot.file_size_estimate_kb:.0f}KB)"
            )
            preview = VisionTestDialog(screenshot._resize_for_vlm(), self.cmd)
            preview.show()
        else:
            self.cmd.log("Vision test failed - screenshot capture error")

    def _on_reset_calibration(self):
        """Reset all RPA calibration data."""
        from core.calibration import CalibrationManager

        cal = CalibrationManager()
        cal.reset()
        self._refresh_calibration_status()
        self.cmd.log("Calibration reset - all coordinates cleared")

    def _on_eod_report(self):
        """Generate and display End-of-Day report."""
        from core.analytics_reporter import AnalyticsReporter

        reporter = AnalyticsReporter()
        report_text = reporter.generate_eod_report()
        filepath = reporter.save_report()

        # Display in terminal
        self.cmd.log("-" * 40)
        for line in report_text.split("\n"):
            self.cmd.log(line)
        self.cmd.log("-" * 40)
        self.cmd.log(f"Report saved: {filepath}")

        # Also generate HTML
        html_path = reporter.save_html_report()
        self.cmd.log(f"HTML report: {html_path}")

    def _refresh_calibration_status(self):
        """Update the calibration status label in the UI."""
        from core.calibration import CalibrationManager

        cal = CalibrationManager()
        status = cal.get_calibration_status()
        done = sum(1 for v in status.values() if v)
        total = len(status)
        self.cmd.update_calibration_status(cal.is_calibrated(), done, total)

    def run(self):
        """Start the application."""
        logger.info("Starting VcaniTrade AI (Hybrid)...")
        self._ensure_balance_state()

        # Show dashboard - this MUST succeed regardless of any service errors.
        try:
            self.cmd.show()
        except Exception as exc:
            logger.error("Dashboard show() failed: %s - attempting re-init", exc)
            self.cmd = CommandCenter()
            self.cmd.show()

        # Initialize UI with balance and ledger
        try:
            self.cmd.update_balance(
                self.balance,
                self.equity,
                self.daily_pnl,
                self.total_pnl,
                drawdown=self.max_drawdown,
                drawdown_pct=(self.max_drawdown / self.peak_balance * 100.0) if self.peak_balance else 0.0,
                trades_today=self.trades_today,
            )
            self._refresh_live_ledger()
        except Exception as exc:
            logger.warning("update_balance failed (non-fatal): %s", exc)

        # Start background threads
        if config.CLOUD_SCANNER_ENABLED:
            try:
                self.cloud_scanner.start()
                self.cmd.log("Cloud Scanner started")
            except Exception as exc:
                logger.warning("Cloud Scanner start failed (non-fatal): %s", exc)
                self.cmd.log(f"Cloud Scanner start failed: {exc}")
        else:
            self.cmd.log("Cloud Scanner disabled - using local watchlist only")

        for svc_name, svc in [
            ("signal_listener", self.signal_listener),
            ("data_scout_listener", self.data_scout_listener),
            ("watchtower", self.watchtower),
            ("analysis_worker", self.analysis_worker),
            ("cloud_bridge", self.cloud_bridge),
            ("hunter", self.hunter),
        ]:
            try:
                svc.start()
            except Exception as exc:
                logger.warning("%s start failed (non-fatal): %s", svc_name, exc)
                self.cmd.log(f"{svc_name} start failed: {exc}")

        # MT5 Position Reconciliation Timer (every 30 seconds)
        if self._is_mt5_mode():
            self._mt5_reconcile_timer = QTimer()
            self._mt5_reconcile_timer.timeout.connect(self._reconcile_mt5_positions)
            self._mt5_reconcile_timer.start(30000)  # 30 seconds
            self.cmd.log("MT5 Position Reconciliation: active (30s interval)")

        # Apex Safety Gate Timer (every 30 seconds)
        if not hasattr(self, "_apex_gate_timer"):
            self._apex_gate_timer = QTimer()
            self._apex_gate_timer.timeout.connect(self.check_apex_closing_time)
            self._apex_gate_timer.start(30000)  # 30 seconds
            self.cmd.log("Apex Safety Gate: active (30s interval)")

        # Startup messages
        self.cmd.log("VcaniTrade AI started - Hybrid Architecture")
        self.cmd.log(f"Cloud Scanner: {'ENABLED' if config.CLOUD_SCANNER_ENABLED else 'DISABLED'}")
        self.cmd.log(f"Signal Listener: {config.LOCAL_LISTENER_HOST}:{config.LOCAL_LISTENER_PORT}")
        if config.PUBLIC_SIGNAL_URL:
            self.cmd.log(f"Public Signal URL: {config.PUBLIC_SIGNAL_URL}")
        self.cmd.log(f"Signal Auth: {'ENABLED' if config.SIGNAL_API_KEY else 'DISABLED'}")
        self.cmd.log(f"Data Scout: Port 5000")
        self.cmd.log(f"Monitoring: {len(config.CLOUD_TICKERS)} tickers")
        if config.DRY_RUN:
            self.cmd.log("Mode: DRY RUN - orders simulated only")
            self.cmd.log("VALIDATION MODE: Run for 2+ hours before disabling DRY_RUN")
        elif self.current_mode == "AUTONOMOUS":
            self.cmd.log("Mode: AUTONOMOUS - execution armed")
        else:
            self.cmd.log("Mode: TEACHER - execution disarmed")

        logger.info("Application running")
        sys.exit(self.app.exec())

    def cleanup(self):
        self.cloud_scanner.stop()
        self.signal_listener.stop()
        self.data_scout_listener.stop()
        self.watchtower.stop()
        if self.hunter:
            self.hunter.stop()
        self.cloud_bridge.stop()
        self.trade_engine.cleanup()
        if hasattr(self, "_mt5_reconcile_timer") and self._mt5_reconcile_timer:
            self._mt5_reconcile_timer.stop()
        if hasattr(self, "_apex_gate_timer") and self._apex_gate_timer:
            self._apex_gate_timer.stop()
        if self.mt5_executor:
            self.mt5_executor.shutdown()
        logger.info("Application shutdown complete")


def main():
    """Entry point."""
    if not _acquire_single_instance_lock():
        print("Another VcaniTrade dashboard instance is already running. Exiting.")
        return

    initial_mode = "TEACHER" if _teacher_mode_forced_by_config() else "AUTONOMOUS"
    trading_mode = "PAPER (dry run)" if config.DRY_RUN else "LIVE"

    print("=" * 60)
    print("VcaniTrade AI - Hybrid Trading Assistant")
    print("=" * 60)
    print(f"Mode:      {initial_mode}")
    print(f"Trading:   {trading_mode}")
    print(f"Executor:  {config.get_active_mode()} (TRADINGVIEW=RPA, MT5=MetaTrader)")
    print(f"Surface:   {config.TRADING_SURFACE}")
    print(
        f"Vision:    {config.VLM_MODEL}" if config.USE_VISION else "Vision:    Disabled"
    )
    print(f"Cloud:     {'ENABLED' if config.CLOUD_SCANNER_ENABLED else 'DISABLED'}")
    print(f"Listener:  {config.LOCAL_LISTENER_HOST}:{config.LOCAL_LISTENER_PORT}")
    print(f"SignalKey: {'SET' if config.SIGNAL_API_KEY else 'NOT SET'}")
    if config.PUBLIC_SIGNAL_URL:
        print(f"Public:    {config.PUBLIC_SIGNAL_URL}")
    print(f"Kill:      OFF")
    print("=" * 60)

    app = VcaniTradeApp()

    # -- RPA Permission Gate ----------------------------------------------
    # Only required when the execution surface actually uses the local hand.
    if config.get_active_mode() != "MT5":
        try:
            app.rpa_hand.assert_permissions_or_die()
        except RuntimeError as perm_err:
            print(f"\nFATAL: {perm_err}")
            print("Relaunch with: Right-click -> 'Run as administrator'")
            sys.exit(1)

    def signal_handler(sig, frame):
        print("\nShutdown signal received...")
        app.cleanup()
        _release_single_instance_lock()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        app.run()
    except KeyboardInterrupt:
        print("\nShutting down...")
        app.cleanup()
    except Exception as e:
        logger.error(f"Application error: {e}")
        app.cleanup()
        raise
    finally:
        _release_single_instance_lock()


if __name__ == "__main__":
    main()
