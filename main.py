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

if sys.platform == 'win32':
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    try:
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding='ascii', errors='ignore', line_buffering=True
        )
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding='ascii', errors='ignore', line_buffering=True
        )
    except AttributeError:
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
    trade_signal = pyqtSignal(str, str, str)    # symbol, action, reason
    narrator_update = pyqtSignal(str, str)      # icon, message (thread-safe Activity Feed)

    def __init__(self, app, symbols=None, interval_sec=None):
        super().__init__()
        self.app = app
        self.symbols = symbols or config.MULTI_ASSET_TICKERS
        self.interval_sec = interval_sec or config.MULTI_ASSET_CYCLE_SECONDS
        self.running = True
        self.index = 0

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

            self.status_update.emit(symbol, f"SIGNAL_{signal}", reason)

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
                    self.trade_signal.emit(symbol, signal, reason)
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
        # Removed dead call to _verify_ghost_hand_socket_async (method no longer exists)
        exchange_provider = os.getenv("EXCHANGE_PROVIDER", "binance")
        exchange_api_key = os.getenv("EXCHANGE_API_KEY") or os.getenv("BINANCE_API_KEY") or os.getenv("BYBIT_API_KEY")
        exchange_api_secret = os.getenv("EXCHANGE_API_SECRET") or os.getenv("BINANCE_API_SECRET") or os.getenv("BYBIT_API_SECRET")
        self.exchange_executor = ExchangeLimitExecutor(
            provider=exchange_provider,
            api_key=exchange_api_key,
            api_secret=exchange_api_secret,
        )
        # RPA Hand - clicks TradingView paper trading UI directly (no API keys needed)
        self.rpa_hand = RPAExecutor()  # removed on_blind_error callback (method was missing)
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
            # Auto-launch visible browser + TradingView on startup (user no longer needs to pick browser)
            self.browser_agent = BrowserAgent(headless=False)
            await self.browser_agent.start()
            if self.browser_agent and self.browser_agent.page:
                self._ghost_executor.set_page(self.browser_agent.page)
                # Force visible RPA clicks so user can watch the bot trade live
                ghost.enabled = False  # disable invisible JS injection

                # Pass controlled page to RPA executor for highest reliability
                if hasattr(self.rpa_executor, "_controlled_page"):
                    self.rpa_executor._controlled_page = self.browser_agent.page
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
            "initial_risk_amount": abs(entry_price - sl_price) * quantity,
            "break_even_locked": False,
            "last_trailing_check_ts": 0.0,
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

    def _on_hunter_narrator_update(self, icon: str, message: str):
        """Thread-safe relay: Hunter thread -> main GUI thread -> Activity Feed."""
        if self.ai_narrator:
            self.ai_narrator.add_activity(icon, message)

    def _check_position_conflict(self, ticker: str, action: str, is_closing: bool = False) -> tuple[bool, str]:
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
        from core.market_sessions import is_crypto_ticker, is_futures_ticker
        is_always_on = is_crypto_ticker(symbol) or is_futures_ticker(symbol)
        if not self.can_trade and not is_always_on:
            self.cmd.log(
                f'<span style="color:#D29922;font-weight:bold">[BLOCKED]</span> '
                f'Trade execution blocked: Apex gate or safety stop active'
            )
            return False

        # Position Safety Check: Close-and-Reverse or skip duplicate
        should_exec, conflict_note = self._check_position_conflict(symbol, action)
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
                f'Sending {action} {symbol} to MetaTrader 5'
            )
            success = mt5_executor.execute_trade(symbol, action)
            if success:
                self.cmd.log(
                    f'<span style="color:#3FB950;font-weight:bold">[MT5 OK]</span> '
                    f'{action} {symbol} executed on MT5'
                )
                self.ai_narrator.add_activity("[MT5]", f"{action} {symbol}")
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
                    entry_price=0.0,
                    stop_loss=0.0,
                    take_profit=0.0,
                    confidence=ConfidenceLevel.MEDIUM,
                    ai_reason=reason,
                    mode="HUNTER",
                    status="OPEN",
                )
                # Position Safety Check for Hunter RPA path
                should_exec_hunter, _ = self._check_position_conflict(symbol, action)
                if not should_exec_hunter:
                    return False
                success = self.rpa_hand.execute_trade(trade)
                if success:
                    self.cmd.log(
                        f'<span style="color:#3FB950;font-weight:bold">[OK]</span> '
                        f'RPA executed {action} {symbol}'
                    )
                    self.ai_narrator.add_activity("[OK]", f"RPA {action} {symbol}")
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
            "initial_risk_amount": abs(entry_price - sl_price) * quantity,
            "break_even_locked": False,
            "last_trailing_check_ts": 0.0,
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

    def _on_hunter_trade_signal(self, symbol: str, action: str, reason: str):
        """Execute a trade when the Cloud Brain returns BUY/SELL from vision analysis."""
        self.cmd.log(
            f'<span style="color:#00D4FF;font-weight:bold">[HUNTER]</span> '
            f'Vision signal: {action} {symbol}'
        )
        self.ai_narrator.add_activity("[HUNTER]", f"{action} {symbol}")
        # Trigger the green border blink — this is the missing feature!
        self.ai_narrator.notify_signal_detected(symbol, action, 0.85)
        self._announce_signal_alert(symbol, action, 85.0, source="hunter")

        if config.TEACHER_MODE:
            self.cmd.log(f"[TEACHER] Would execute {action} {symbol} | {reason}")
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
