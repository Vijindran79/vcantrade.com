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

# Fix Windows terminal encoding issues - runs before ANY other import so that
# even early import-time prints/logs never hit cp1252 raw.
import sys
import os
import io

if sys.platform == 'win32':
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    # Re-wrap stdout/stderr with ASCII + ignore so emojis are silently dropped
    # instead of raising UnicodeEncodeError on cp1252 / cp850 terminals.
    try:
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding='ascii', errors='ignore', line_buffering=True
        )
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding='ascii', errors='ignore', line_buffering=True
        )
    except AttributeError:
        # Already wrapped (e.g. pytest capture) - nothing to do.
        pass

# Patch asyncio to allow nested event loops (sync Playwright inside async QThreads)
import nest_asyncio
nest_asyncio.apply()

import signal
import socket
import time
import random
import asyncio
import logging
import threading
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

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
from core.browser_agent import BrowserAgent
from core.settings import settings_manager
from core.financial_safety import FinancialSafetyManager
from core.executor import UnifiedTradeExecutor, ExchangeLimitExecutor, ExchangeInterface, SlippageGuard
from core.risk import calculate_position_size, build_hard_stop_plan
from core.journal import TradeJournalDB
from core.risk_manager import RiskManager, PositionSizer
from core.symbol_mapper import root_matches
from core.vibe_adapter import VibeTradingAdapter
from execution.rpa_executor import RPAExecutor
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


def _play_trade_alert(action: str, success: bool):
    """Play a distinctive alert sound when a trade is executed.
    BUY = rising tone (optimistic). SELL = falling tone (urgent).
    Failed trade = low buzz."""
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
            "account_id": "PAAPEX3143270000002",
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

    def run(self):
        from core.market_sessions import is_weekend_closed
        from datetime import timezone

        # Use UTC-aware time checks for market transitions
        now_utc = datetime.now(timezone.utc)
        weekday = now_utc.weekday()
        hour_utc = now_utc.hour

        # Automatic Switchboard Flip:
        # Saturday = weekend mode (crypto only)
        # Sunday before 23:00 UTC = weekend mode
        # Sunday 23:00 UTC+ = normal mode resumes
        is_weekend = (weekday == 5) or (weekday == 6 and hour_utc < 23)

        # Weekend: pull from the live watchlist (crypto) instead of MULTI_ASSET_TICKERS
        if is_weekend:
            watchlist = getattr(self.app, "current_watchlist", [])
            if watchlist:
                active_symbols = [s for s in watchlist if not is_weekend_closed(s)]
                logger.info("[HUNTER] Weekend mode: Hunter using session watchlist: %s", active_symbols)
            else:
                active_symbols = []
            # Reset Monday re-sync flag so it triggers again next week
            if getattr(self.app, "_monday_resync_done", False):
                self.app._monday_resync_done = False
                logger.info("[RESYNC] Monday re-sync flag reset for next week")
        else:
            active_symbols = list(self.symbols)

        if not active_symbols:
            logger.info("[HUNTER] No active symbols to hunt. Hunter paused.")
            return

        # Monday State Re-Sync (Anti-Ghosting):
        # On first weekday scan after weekend, clear stale signals and re-sync account.
        if not is_weekend and not getattr(self.app, "_monday_resync_done", False):
            self._perform_monday_resync()

        logger.info("[HUNTER] Multi-Asset Hunter started. Symbols: %s", active_symbols)
        while self.running:
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
            # Saturday = weekend, Sunday before 23:00 UTC = weekend
            is_weekend_now = (now_utc.weekday() == 5) or (now_utc.weekday() == 6 and now_utc.hour < 23)
            if is_weekend_now and is_weekend_closed(symbol):
                logger.debug("[HUNTER] Skipping weekend-closed symbol: %s", symbol)
                return

            self.status_update.emit(symbol, "NAVIGATING", f"Switching to {symbol}...")

            # 1. Navigate browser to symbol
            loop = self.app._browser_loop if hasattr(self.app, "_browser_loop") else None
            if not loop or loop.is_closed():
                self.status_update.emit(symbol, "ERROR", "Browser loop not available")
                return

            future = asyncio.run_coroutine_threadsafe(
                self.app.browser_agent.navigate_to_symbol(symbol),
                loop,
            )
            nav_ok = future.result(timeout=35)
            if not nav_ok:
                self.status_update.emit(symbol, "ERROR", "Navigation failed")
                # Trigger self-healing: record error and attempt browser restart
                self.app.browser_agent.record_error("Navigation failed for " + symbol)
                if self.app.browser_agent.error_count >= self.app.browser_agent.error_threshold:
                    logger.warning("[WRENCH] Navigation failures reached threshold — triggering browser self-heal")
                    try:
                        heal_future = asyncio.run_coroutine_threadsafe(
                            self.app.browser_agent.self_heal_restart(), loop,
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

            self.status_update.emit(symbol, "SCREENSHOT", "Capturing chart...")

            # 2. Take screenshot
            future = asyncio.run_coroutine_threadsafe(
                self.app.browser_agent.take_screenshot(),
                loop,
            )
            screenshot_b64 = future.result(timeout=15)
            if not screenshot_b64:
                self.status_update.emit(symbol, "ERROR", "Screenshot failed")
                # Trigger self-healing for screenshot failures
                self.app.browser_agent.record_error("Screenshot failed for " + symbol)
                if self.app.browser_agent.error_count >= self.app.browser_agent.error_threshold:
                    logger.warning("[WRENCH] Screenshot failures reached threshold — triggering browser self-heal")
                    try:
                        heal_future = asyncio.run_coroutine_threadsafe(
                            self.app.browser_agent.self_heal_restart(), loop,
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

    def _perform_monday_resync(self):
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
                app._sync_live_balance()
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

    def __init__(self):
        super().__init__()
        self.analyzer = LLMAnalyzer()
        self.market_data_queue = []
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
        self.market_data_queue.append(market_data)

    def run(self):
        logger.info("Analysis worker started (Swarm Consensus mode)")

        while True:
            if self.market_data_queue:
                market_data = self.market_data_queue.pop(0)

                try:
                    # Capture chart screenshot if vision is enabled
                    chart_base64 = None
                    if self.vision:
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

                    # Run swarm debate (with or without vision)
                    try:
                        if chart_base64:
                            _wait_for_vision_analysis_slot(f"analysis:{market_data.asset}")
                        output, transcript = self.analyzer.analyze_market(
                            market_data, chart_image_base64=chart_base64
                        )
                        self.analysis_complete.emit(output, transcript)
                    except Exception as analysis_error:
                        logger.error(f"Swarm analysis failed for {market_data.asset}: {analysis_error}")
                        # Emit None to indicate failure - UI should handle gracefully
                        self.analysis_complete.emit(None, None)
                        
                except Exception as worker_error:
                    logger.error(f"Analysis worker critical error: {worker_error}")
                    # Continue loop - don't crash the thread
            else:
                time.sleep(0.1)


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
        self.grader = Grader()
        self.financial_safety = FinancialSafetyManager()
        self.executor = None  # Will be initialized when browser agent is ready
        self.risk_manager = RiskManager(risk_per_trade_pct=1.0)
        self.trade_executor = TradeExecutor(exchange_client=None)
        exchange_provider = os.getenv("EXCHANGE_PROVIDER", "binance")
        exchange_api_key = os.getenv("EXCHANGE_API_KEY") or os.getenv("BINANCE_API_KEY") or os.getenv("BYBIT_API_KEY")
        exchange_api_secret = os.getenv("EXCHANGE_API_SECRET") or os.getenv("BINANCE_API_SECRET") or os.getenv("BYBIT_API_SECRET")
        self.exchange_executor = ExchangeLimitExecutor(
            provider=exchange_provider,
            api_key=exchange_api_key,
            api_secret=exchange_api_secret,
        )
        # RPA Hand - clicks TradingView paper trading UI directly (no API keys needed)
        # on_blind_error fires when TradingView is minimized or covered by another app
        self.rpa_hand = RPAExecutor(on_blind_error=self._on_rpa_blind)
        # MT5 Executor - routes trades to MetaTrader 5 when EXECUTION_MODE == "MT5"
        self.mt5_executor = None
        if self._is_mt5_mode():
            self.mt5_executor = self._get_mt5_executor()
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

        # Load persistent trading settings before deriving any session state from them.
        self.settings = settings_manager
        self.cmd.log(f"[GEAR] Settings loaded: {self.settings.get('investment_mode')} mode")

        saved_watchlist = self.settings.get("session_watchlist", [])
        if not isinstance(saved_watchlist, list):
            saved_watchlist = []
        self.current_watchlist = self.settings.normalize_watchlist(saved_watchlist)
        if not self.current_watchlist:
            self.current_watchlist = self.settings.normalize_watchlist(config.CLOUD_TICKERS)
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
        self.analysis_worker = AnalysisWorker()  # Local analysis (vision + swarm)
        self.cloud_bridge = CloudBridgeThread(self, host="0.0.0.0", port=8765)  # Remote dashboard bridge
        self.hunter = MultiAssetHunterThread(self) if config.MULTI_ASSET_ENABLED else None  # Vision-based multi-asset hunter
        self.cloud_scanner.scanner.tickers = list(self.current_watchlist)

        # State
        self.current_mode = "TEACHER" if config.TEACHER_MODE or config.DRY_RUN else "AUTONOMOUS"
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

    def _is_mt5_mode(self) -> bool:
        return str(getattr(config, "EXECUTION_MODE", "UI") or "UI").upper() == "MT5"

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
        return self.settings.normalize_ticker(ticker)

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
                    f"{ticker} | unable to fetch live market price via {market_ticker}",
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
            self.browser_agent = BrowserAgent(headless=True)
            await self.browser_agent.start()
            # Auto-navigate to TradingView MNQ1! chart
            await self.browser_agent.navigate_to_tradingview()
            self.browser_agent_status = "ready"
        except Exception as e:
            self.browser_agent_status = "error"
            self._browser_error_message = str(e)
            logger.error(f"Failed to start browser agent: {e}")

    def _cloud_heartbeat(self):
        """Log a heartbeat every 10s so the operator knows the bot is alive on Vast.ai."""
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

        # Multi-Asset Hunter signals
        if self.hunter:
            self.hunter.status_update.connect(self._on_hunter_status_update)
            self.hunter.trade_signal.connect(self._on_hunter_trade_signal)
            self.hunter.narrator_update.connect(self._on_hunter_narrator_update)

        # Cloud Scanner -> UI + Narrator
        self.cloud_scanner.signal_detected.connect(self._on_cloud_signal)
        self.cloud_scanner.scanner_error.connect(self._on_scanner_error)
        self.cloud_scanner.ticker_status.connect(self._on_ticker_status_update)

        # Signal Listener -> UI + Narrator
        self.signal_listener.signal_received.connect(self._on_signal_received)
        self.signal_listener.handshake_received.connect(self._on_bridge_handshake_received)
        self.signal_listener.listener_error.connect(self._on_listener_error)

        # Data Scout Listener -> UI + TV Flip + Narrator
        self.data_scout_listener.signal_received.connect(self._on_data_scout_signal)

        # Watchtower -> UI alerts + Swarm handoff + Narrator
        self.watchtower.alert_detected.connect(self._on_watchtower_alert)
        self.watchtower.market_data_ready.connect(self._on_market_data)

        # Analysis worker -> Trade engine + UI + Narrator
        self.analysis_worker.analysis_complete.connect(self._on_analysis_complete)

        # Position monitoring timer
        self.position_timer = QTimer()
        self.position_timer.timeout.connect(self._update_positions)
        self.position_timer.start(5000)  # Update every 5 seconds

        self._mirror_shortcut = QShortcut(QKeySequence("Ctrl+Shift+H"), self.cmd)
        self._mirror_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._mirror_shortcut.activated.connect(self._toggle_mirror_visibility)

    def _apply_initial_mode_ui(self):
        """Align the dashboard controls with the backend's initial runtime mode."""
        if self.current_mode == "AUTONOMOUS":
            self.cmd._set_autonomous_mode()
        else:
            self.cmd._set_teacher_mode()

    def _sync_runtime_session_context(self):
        """Keep session-awareness aligned with the active dashboard watchlist and operator mode."""
        watchlist = [ticker for ticker in self.current_watchlist if str(ticker).strip()]
        self.session_detector.set_runtime_context(self.current_mode, watchlist)
        self.cloud_scanner.scanner.set_runtime_context(self.current_mode, watchlist)
        self.financial_safety.set_runtime_mode(self.current_mode)

        # News filter & safety timer
        if not hasattr(self, "safety_timer"):
            self.safety_timer = QTimer()
            self.safety_timer.timeout.connect(self._update_safety_controls)
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
        self.current_watchlist = self.settings.normalize_watchlist(watchlist)
        config.CLOUD_TICKERS = list(self.current_watchlist)
        self.cloud_scanner.scanner.tickers = list(self.current_watchlist)
        self.cloud_scanner.scanner.priority_scan_list = []
        self.rpa_hand.active_watchlist = list(self.current_watchlist)
        self._sync_runtime_session_context()
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
        if status.startswith("brain_reasoning:"):
            self.ai_narrator.notify_brain_thinking(ticker, status.split(":", 1)[1])
        elif status.startswith("brain_fallback:"):
            self._sync_brain_runtime_ui(status.split(":", 1)[1], True)

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

    def _broadcast_trade_levels(self, signal_data: dict):
        """Send large SL/TP/liquidity text to the mirror for teacher-mode readability."""
        ticker = signal_data.get("ticker", "UNKNOWN")
        self.ai_narrator.update_trade_levels(
            ticker=ticker,
            stop_loss=signal_data.get("stop_loss"),
            take_profit=signal_data.get("take_profit"),
            liquidity_label=self._format_liquidity_label(signal_data),
        )

    def _on_settings_changed(self, settings: dict):
        """Handle trading settings update from dashboard."""
        # Update persistent settings
        self.settings.update(settings)

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
        """Generate chart markup scripts (S/R, trend breaks, blink lines) and inject to TradingView."""
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
            adjustment="[OK] Support/resistance and trend-break overlays prepared. Injecting to TradingView now."
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
            adjustment=f"[OK] Pine Script v6 ready. {len(demand_zones)} demand zones, {len(supply_zones)} supply zones. Click 'Inject to Chart' to auto-add to TradingView."
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
        
        self.cmd.log("[BUILD] Injecting Pine Script to TradingView...")
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
            # This will use the NEW swarm consensus with user_suggestion parameter
            import asyncio
            
            # Create task for async analysis
            asyncio.run_coroutine_threadsafe(
                self._run_copilot_analysis(market_data, user_suggestion),
                self._browser_loop if hasattr(self, '_browser_loop') else asyncio.get_event_loop()
            )
            
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
                    one_min_df = yf.Ticker(ticker).history(period=period, interval="1m")
                    df = _resample_to_3m(one_min_df)
                else:
                    df = yf.Ticker(ticker).history(period=period, interval=interval)
            except Exception as e:
                logger.warning("MTF %s fetch failed for %s: %s", label, ticker, e)
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

        # AGGRESSIVE HUNTER: High-confidence signals strike on 5m alone
        hunter_threshold = getattr(config, "AGGRESSIVE_HUNTER_CONFIDENCE_PCT", 75.0)
        if confidence >= hunter_threshold and votes.get("5m") == action:
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
            if not five_m_opposite:
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
        """Handle manual force-hand request with visible cursor motion on TradingView."""
        import threading

        ticker_hint = self.current_watchlist[0] if self.current_watchlist else self.ticker_selector
        self.cmd.log(f"[BOLT] FORCE HAND TEST: visible cursor move requested on {ticker_hint}")
        self.ai_narrator.add_activity("[MOUSE]", f"Force hand move diagnostic on {ticker_hint}")

        def run_force_test_in_thread():
            try:
                success = self.rpa_hand.force_hand_test_move(ticker_hint=ticker_hint)
                if success:
                    self.cmd.log(f'<span style="color:#3FB950;font-weight:bold">[MOUSE] FORCE HAND TEST PASSED</span>: cursor moved on {ticker_hint}')
                    self.ai_narrator.add_activity("[OK]", f"Visible hand move completed on {ticker_hint}")
                else:
                    self.cmd.log(f'<span style="color:#F85149;font-weight:bold">[WARN] FORCE HAND TEST FAILED</span>: unable to move cursor on {ticker_hint}')
                    self.ai_narrator.add_activity("[WARN]", f"Visible hand move failed for {ticker_hint}")
            except Exception as e:
                self.cmd.log(f'<span style="color:#F85149">[FAIL] FORCE HAND TEST ERROR</span>: {e}')

        hand_test_thread = threading.Thread(target=run_force_test_in_thread, daemon=True)
        hand_test_thread.start()
        self.cmd.log("[BOLT] Force hand test dispatched to RPA executor")

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

    def _update_positions(self):
        """Update live positions with current prices and check TP/SL."""
        import yfinance as yf
        import concurrent.futures

        updated_positions = []
        for pos in self.positions:
            try:
                # Get current price with timeout protection
                try:
                    market_ticker = self._canonical_market_ticker(pos["asset"])
                    ticker = yf.Ticker(market_ticker)
                    
                    # Run yfinance in thread with timeout
                    def fetch_price():
                        return ticker.history(period="1d", interval="1m")
                    
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(fetch_price)
                        hist = future.result(timeout=10)  # 10 second timeout
                    
                    if hist.empty:
                        self.cmd.log(f"[WARN] No price data for {pos['asset']} - trying browser agent")
                        # Fallback to browser agent if yfinance fails
                        self._verify_price_with_browser(pos)
                        updated_positions.append(pos)
                        continue
                    
                    current_price = hist["Close"].iloc[-1]
                except concurrent.futures.TimeoutError:
                    self.cmd.log(f"[WARN] Timeout fetching {pos['asset']} price - network lag")
                    updated_positions.append(pos)
                    continue
                except Exception as e:
                    self.cmd.log(f"[WARN] Failed to fetch {pos['asset']} price: {e}")
                    updated_positions.append(pos)
                    continue
                
                pos["current"] = current_price

                # Calculate P&L
                if pos["side"] == "BUY":
                    pnl_pct = ((current_price - pos["entry"]) / pos["entry"]) * 100
                    pnl_usd = (current_price - pos["entry"]) * pos["quantity"]
                else:  # SELL
                    pnl_pct = ((pos["entry"] - current_price) / pos["entry"]) * 100
                    pnl_usd = (pos["entry"] - current_price) * pos["quantity"]

                pos["pnl"] = pnl_usd
                pos["pnl_pct"] = pnl_pct

                # MANUAL TRADE PROTECTION: Skip auto-close for positions the bot didn't open
                if not pos.get("bot_opened"):
                    self.cmd.log(
                        f"[MANUAL] Manual position detected: {pos['asset']} | "
                        f"P&L: ${pnl_usd:.2f} ({pnl_pct:.2f}%) — NOT managed by bot"
                    )
                    updated_positions.append(pos)
                    continue

                self._manage_position_stop(pos, hist)

                # Check Take Profit (skip if tp_price is 0/unset)
                if pos.get("tp_price", 0) > 0:
                    if pos["side"] == "BUY" and current_price >= pos["tp_price"]:
                        self.cmd.log(f"[TARGET] TAKE PROFIT HIT: {pos['asset']} @ ${current_price:.2f} | P&L: +${pnl_usd:.2f}")
                        self._close_position(pos, "Take Profit")
                        continue
                    elif pos["side"] == "SELL" and current_price <= pos["tp_price"]:
                        self.cmd.log(f"[TARGET] TAKE PROFIT HIT: {pos['asset']} @ ${current_price:.2f} | P&L: +${pnl_usd:.2f}")
                        self._close_position(pos, "Take Profit")
                        continue

                # Check Stop Loss (skip if sl_price is 0/unset)
                if pos.get("sl_price", 0) > 0:
                    if pos["side"] == "BUY" and current_price <= pos["sl_price"]:
                        self.cmd.log(f"[STOP] STOP LOSS HIT: {pos['asset']} @ ${current_price:.2f} | P&L: ${pnl_usd:.2f}")
                        self._close_position(pos, "Stop Loss")
                        continue
                    elif pos["side"] == "SELL" and current_price >= pos["sl_price"]:
                        self.cmd.log(f"[STOP] STOP LOSS HIT: {pos['asset']} @ ${current_price:.2f} | P&L: ${pnl_usd:.2f}")
                        self._close_position(pos, "Stop Loss")
                        continue

                updated_positions.append(pos)

            except Exception as e:
                self.cmd.log(f"[WARN] Error updating {pos['asset']}: {e}")
                updated_positions.append(pos)

        self.positions = updated_positions
        self.cmd.update_positions(self.positions)
        live_positions_pnl = sum(p.get("pnl", 0.0) for p in self.positions)
        self.profit_lock.update_balance(self.balance + live_positions_pnl)
        self.ai_narrator.update_live_pnl(self.total_pnl + live_positions_pnl, len(self.positions))
        self._refresh_live_ledger()
        
        # Update AI Narrator with position status (only if there are positions)
        if self.positions:
            self.ai_narrator.notify_position_update(len(self.positions), self.daily_pnl)
        else:
            # No positions - set to idle/scanning
            self.ai_narrator.set_status("scanning", "No active positions")
    
    def _update_safety_controls(self):
        """Update financial safety controls and check news filter."""
        import asyncio
        
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
            self._sync_live_balance()
        except Exception as e:
            logger.warning("Live balance sync error in safety controls: %s", e)

    def _update_institutional_governor_ui(self):
        """STAGE 3: Update Institutional Governor panel in dashboard."""
        # Trigger live news scan if due
        if self.sentiment_pulse.should_check():
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                loop.run_until_complete(self.sentiment_pulse.check_news())
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

    def _sync_live_balance(self):
        """Scrape live balance from broker dashboard and sync internal state.
        Runs every 60 seconds alongside safety controls."""
        try:
            scrape_result = self.rpa_hand.scrape_live_balance()
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
                    self.daily_pnl = day_pl
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

            # Sync daily P/L from dashboard if available
            if day_pl is not None:
                self.daily_pnl = day_pl

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

    def _close_position(self, position: dict, reason: str):
        """Close a position and update P&L."""
        self._ensure_balance_state()
        pnl = position.get("pnl", 0)
        self.locked_tickers[position["asset"]] = time.time()
        self.balance += pnl
        self.daily_pnl += pnl
        self.total_pnl += pnl

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
        if not self.can_trade and not is_crypto_ticker(ticker):
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
            signal_data.get("signal_type", "Signal"),
            signal_data["confidence"]
        )
        self.cmd.log(
            f'<span style="color:#8B949E">[MAIL] CLOUD ROUTE</span>: '
            f'{signal_data["action"]} {signal_data["ticker"]} delivered to local listener'
        )

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
        """Handle signal received from cloud via HTTP."""
        self._mark_bridge_alive()
        brain_override_action = self._brain_override_action(signal_data)
        resolved_action = self._resolve_directional_action(signal_data)
        action = brain_override_action if brain_override_action in {"BUY", "SELL"} else resolved_action
        signal_data["action"] = action
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

        if self.current_mode == "AUTONOMOUS":
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
        """Flip the bridge indicator red when the external heartbeat goes stale."""
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
                logger.warning("[SYSTEM] Warning: External Brain heartbeat lost.")
                self.cmd.log(
                    '<span style="color:#F85149;font-weight:bold">[SYSTEM] Warning: External Brain heartbeat lost.</span>'
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
            sl_price = self._derive_structure_stop_loss(action, entry_price, signal_data, risk_eval)
            # Auto-risk TP: 1.5x the SL distance (short: TP below entry, long: TP above entry)
            sl_dist = abs(entry_price - sl_price) if sl_price > 0 else entry_price * 0.01
            if action == "SELL":
                tp_price = entry_price - (sl_dist * 1.5)
            else:
                tp_price = entry_price + (sl_dist * 1.5)
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

        # Micro futures contract multipliers ($ per price unit) — MUST match rpa_executor mapping
        contract_multipliers = {
            "NQ=F": 2.0,    # MNQ: $2 per point
            "ES=F": 5.0,    # MES: $5 per point
            "CL=F": 100.0,  # MCL: $100 per $1.00 barrel move
            "GC=F": 10.0,   # MGC: $10 per point
            "SI=F": 10.0,   # Micro Silver: $10 per point
        }
        multiplier = contract_multipliers.get(ticker, 1.0)
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

        # -- RPA Hand: move mouse and click on TradingView paper trading --
        mtf_passed, mtf_votes = self._passes_mtf_sniper_gate(ticker, action, signal_data=signal_data, confidence=confidence_score)
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
        should_exec_rpa, conflict_note_rpa = self._check_position_conflict(ticker, action)
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
        target_info = self.rpa_hand.describe_entry_target(action, ticker_hint=ticker)
        if target_info:
            signal_data["rpa_target"] = target_info
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
        else:
            logger.warning("EXEC_CLOUD: no entry target resolved for %s %s", action, ticker)
            self.cmd.log(
                f'<span style="color:#D29922;font-weight:bold">[WARN] RPA TARGET</span>: '
                f'no calibrated {action} button coordinates resolved for {ticker}'
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
        # PREDATOR-CLASS: Silent Error Alerting with try/except wrapper
        rpa_success = False
        try:
            rpa_success = self.rpa_hand.execute_trade(rpa_trade)
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
        
        order_status = "rpa_executed" if rpa_success else "rpa_failed"
        logger.info("EXEC_CLOUD: rpa result for %s %s -> %s", action, ticker, order_status)
        # ALERT SOUND: Play distinctive tone on every trade execution
        _play_trade_alert(action, rpa_success)
        self.cmd.log(
            f'<span style="color:{"#3FB950" if rpa_success else "#F85149"};font-weight:bold">'
            f'{"[MOUSE] RPA HAND CLICKED" if rpa_success else "[WARN] RPA HAND FAILED"}</span>: '
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
        if not self.can_trade and not force_execute and not is_crypto_ticker(ticker):
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
        overlay_signal = OverlaySignal(
            asset=analysis.asset,
            action=analysis.action,
            confidence=analysis.confidence,
            entry_price=analysis.entry_price,
            stop_loss=analysis.stop_loss,
            take_profit=analysis.take_profit,
            reason=analysis.reason,
        )

        # Update dashboard
        # self.overlay.update_signal_handler(overlay_signal)  # Removed overlay
        self.cmd.log(f"[CHART] Signal: {overlay_signal.action} {overlay_signal.asset} @ ${overlay_signal.entry_price:.2f}")

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
        self.current_mode = mode
        self._sync_runtime_session_context()
        logger.info(f"Mode changed to {mode}")
        self.cmd.log(f"[REFRESH] Mode changed to: {mode}")
        self.ai_narrator.set_status("idle", f"Mode: {mode}")

    def _on_dry_run_changed(self, is_dry_run: bool):
        """Keep the runtime engine aligned with the dashboard dry-run toggle."""
        config.DRY_RUN = bool(is_dry_run)
        if is_dry_run and self.current_mode == "AUTONOMOUS":
            self.current_mode = "TEACHER"
            self._sync_runtime_session_context()
            self.cmd._set_teacher_mode()
        else:
            self._sync_runtime_session_context()
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
        Routes to MT5 or UI (RPA) based on config.EXECUTION_MODE.
        Returns True if execution succeeded.
        """
        from core.market_sessions import is_crypto_ticker
        is_crypto = is_crypto_ticker(symbol)
        if not self.can_trade and not is_crypto:
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
        success = mt5_executor.execute_trade(execution_ticker, action)
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
        Runs every 30 seconds when EXECUTION_MODE == 'MT5'.
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
                    self.positions.remove(pos)
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
            from core.market_sessions import is_crypto_ticker
            now_et = datetime.now(ZoneInfo("America/New_York"))
            hour = now_et.hour
            minute = now_et.minute
            time_val = hour * 100 + minute  # e.g. 1630 for 4:30 PM

            # CRYPTO NEVER BLOCKED: If watchlist or positions are all crypto, skip Apex gate
            watchlist = getattr(self, "current_watchlist", [])
            positions = getattr(self, "positions", [])
            # Check if ANY crypto is in the watchlist or positions — don't block just because stocks exist
            has_crypto = (
                any(is_crypto_ticker(t) for t in watchlist)
                or any(is_crypto_ticker(p.get("asset", "")) for p in positions)
            )
            all_crypto = (
                (bool(watchlist) and all(is_crypto_ticker(t) for t in watchlist))
                or (bool(positions) and all(is_crypto_ticker(p.get("asset", "")) for p in positions))
            )
            # If everything is crypto, skip Apex gate entirely
            if all_crypto:
                if not self.can_trade:
                    self.can_trade = True
                    logger.info("[APEX] Crypto-only mode detected — Apex gate lifted, trading ALLOWED")
                return
            # If mixed (crypto + stocks), only block the stock portion — crypto still allowed
            if has_crypto and not self.can_trade:
                logger.info("[APEX] Mixed watchlist — crypto positions bypass Apex block, stocks blocked")
                # Don't return; let the stock block apply, but crypto execution paths
                # use is_crypto_ticker() to bypass can_trade individually

            # After 4:30 PM ET — block new trades
            if time_val >= 1630 and self.can_trade:
                self.can_trade = False
                logger.warning("[APEX] Market closing time reached (16:30 ET). New trades BLOCKED.")
                self.cmd.log(
                    '<span style="color:#D29922;font-weight:bold">[APEX GATE]</span> '
                    '16:30 ET reached — New trades BLOCKED until next session'
                )
                self.ai_narrator.add_activity("[APEX]", "16:30 ET — No new trades")

            # After 4:45 PM ET — force close all NON-CRYPTO positions
            if time_val >= 1645 and self.positions:
                # Split positions: crypto stays open, everything else gets flattened
                crypto_positions = [p for p in self.positions if is_crypto_ticker(p.get("asset", ""))]
                non_crypto_positions = [p for p in self.positions if not is_crypto_ticker(p.get("asset", ""))]

                if non_crypto_positions:
                    logger.warning(
                        "[APEX] Forced position flattening at 16:45 ET. Non-crypto positions: %s. "
                        "Crypto positions PRESERVED: %s",
                        [p["asset"] for p in non_crypto_positions],
                        [p["asset"] for p in crypto_positions],
                    )
                    self.cmd.log(
                        '<span style="color:#F85149;font-weight:bold">[APEX GATE]</span> '
                        '16:45 ET — FORCING CLOSE of non-crypto positions (crypto preserved)'
                    )
                    self.ai_narrator.add_activity("[APEX]", "16:45 ET — Flattening non-crypto positions")

                    # Close non-crypto via executor if available
                    if self.executor:
                        try:
                            for p in non_crypto_positions:
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
                                if is_crypto_ticker(p.get("symbol", "")):
                                    continue  # Skip crypto positions
                                close_action = "SELL" if p["type"] == "BUY" else "BUY"
                                mt5_executor.execute_trade(p["symbol"], close_action, volume=p["volume"])
                                logger.info("[APEX] MT5 closed %s %s", p["type"], p["symbol"])
                        except Exception as e:
                            logger.error("[APEX] MT5 position close failed: %s", e)

                    # Update positions: keep crypto, remove non-crypto
                    self.positions = crypto_positions
                    self.cmd.update_positions(self.positions)
                    self._refresh_live_ledger()
                    self.ai_narrator.notify_error("Non-crypto positions flattened — Apex closing time (crypto preserved)")
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

    initial_mode = "TEACHER" if config.TEACHER_MODE or config.DRY_RUN else "AUTONOMOUS"
    trading_mode = "PAPER (dry run)" if config.DRY_RUN else "LIVE"

    print("=" * 60)
    print("VcaniTrade AI - Hybrid Trading Assistant")
    print("=" * 60)
    print(f"Mode:      {initial_mode}")
    print(f"Trading:   {trading_mode}")
    print(f"Executor:  {config.EXECUTION_MODE} (UI=RPA, MT5=MetaTrader)")
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
    if str(getattr(config, "EXECUTION_MODE", "UI") or "UI").upper() != "MT5":
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
