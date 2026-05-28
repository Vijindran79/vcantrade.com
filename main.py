"""
VcaniTrade AI - Main Trading Engine
Unified, production-ready quantitative trading assistant
"""

import os
import sys
import json
import time
import logging
import threading
import concurrent.futures
from datetime import datetime, timezone
from collections import Counter
from typing import Optional, Dict, Any, Tuple, List

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

import config
from core.settings import settings_manager
from core.models import SignalAction, ConfidenceLevel, TradeResult, MarketDataPoint
from core.risk_governor import RiskGovernor
from core.sentiment_pulse import SentimentPulse
from core.profit_lock import ProfitLock
from core.trade_journal import TradeJournal
from core.meta_analyzer import MetaAnalyzer
from core.atr_stops import LooseATRStops
from core.visual_confirmation import VisualChartConfirmation
from core.vibe_adapter import VibeTradingAdapter
from core.code_architect import CodeArchitect
from core.brain_swarm import OllamaSwarmConsensus
from core.market_sessions import MarketSessionDetector
from core.slippage_guard import SlippageGuard
from core.trade_engine import TradeEngine
from core.trade_executor import TradeExecutor
from core.trade_monitor import TradeMonitor
from core.scanner import Scanner
from core.browser_agent import BrowserAgent
from core.ghost_executor import GhostExecutor
from core.hybrid_execution_gateway import HybridExecutionGateway
from execution.rpa_executor import RPAExecutor
from services.signal_dispatcher import SignalDispatcher
from threads.cloud_scanner import CloudScannerThread
from threads.signal_listener import SignalListenerThread
from threads.data_scout_listener import DataScoutListenerThread
from threads._utils import _speak_alert
# Import UI components after QApplication is available
from ui.dashboard import CommandCenter as Dashboard
from ui.ai_narrator import AINarratorOverlayClassWindow as AINarrator
from ui.lion_switchboard import LionSwitchboardDialog as LionSwitchboard

logger = logging.getLogger(__name__)


class AutomatedSignalBridge(QObject):
    """Enforces strict, thread-safe cross-boundary slot execution using native PyQt asymmetric communication slots."""
    execution_signal = pyqtSignal(dict)
    panic_reset_signal = pyqtSignal()

    def __init__(self, engine_context):
        super().__init__()
        self.engine = engine_context
        # Connect our communication vectors safely to slots bound to the core graphical surface thread
        self.execution_signal.connect(self._safe_slot_execute_trade)
        self.panic_reset_signal.connect(self._safe_slot_execute_panic_purge)

    def dispatch_execution_request(self, payload: dict):
        """Background worker threads invoke this thread-safe gateway instead of touching visual properties directly."""
        self.execution_signal.emit(payload)

    def dispatch_panic_request(self):
        """Thread-safe interface to pass emergency containment triggers down to the visual environment layout."""
        self.panic_reset_signal.emit()

    def _safe_slot_execute_trade(self, payload: dict):
        """This routine executes safely on the primary graphical thread canvas."""
        logger.info(f"[THREAD-SAFE-SLOT] Processing trade routing request safely for symbol: {payload.get('ticker')}")
        try:
            self.engine.process_validated_execution_path(payload)
        except Exception as e:
            logger.error(f"[SLOT-CRASH] Execution sequence failed inside slot handler: {str(e)}")

    def _safe_slot_execute_panic_purge(self):
        """This routine executes safely on the primary graphical thread canvas to handle panic flattens."""
        logger.warning("[THREAD-SAFE-SLOT] Processing master emergency panic reset request safely.")
        self.engine.execute_hardened_panic_reset()


# =========================================================================
# SINGLE-ASSET TARGET LOCK (ZERO OVERLAP)
# =========================================================================

class SingleAssetLock:
    """Thread-safe single-asset target lock.
    
    Enforces zero-overlap execution: while locked, all other symbols
    are ignored until the lock is released.
    """
    
    def __init__(self):
        self._lock = threading.RLock()
        self.is_currently_holding = False
        self.active_locked_ticker = None
        self.lock_acquired_at = 0.0
        self.lock_timeout_seconds = 300  # 5 minutes max hold
        
    def acquire(self, ticker: str) -> bool:
        """Acquire the lock for a specific ticker.
        
        Returns True if lock acquired, False if another ticker is active.
        """
        with self._lock:
            if self.is_currently_holding:
                if self.active_locked_ticker == ticker:
                    return True  # Already locked for this ticker
                logger.warning(
                    "[LOCK] Cannot acquire lock for %s — already holding %s",
                    ticker, self.active_locked_ticker
                )
                return False
            
            self.is_currently_holding = True
            self.active_locked_ticker = ticker
            self.lock_acquired_at = time.monotonic()
            logger.info("[LOCK] Single-asset lock ACQUIRED for %s", ticker)
            return True
    
    def release(self):
        """Release the lock."""
        with self._lock:
            if self.is_currently_holding:
                logger.info(
                    "[LOCK] Single-asset lock RELEASED for %s (held %.1fs)",
                    self.active_locked_ticker,
                    time.monotonic() - self.lock_acquired_at
                )
            self.is_currently_holding = False
            self.active_locked_ticker = None
            self.lock_acquired_at = 0.0
    
    def is_locked_for(self, ticker: str) -> bool:
        """Return True if locked for a DIFFERENT ticker."""
        with self._lock:
            if not self.is_currently_holding:
                return False
            return self.active_locked_ticker != ticker
    
    def check_timeout(self) -> bool:
        """Return True if lock has timed out and should be released."""
        with self._lock:
            if not self.is_currently_holding:
                return False
            if time.monotonic() - self.lock_acquired_at > self.lock_timeout_seconds:
                logger.warning(
                    "[LOCK] Single-asset lock for %s timed out after %.1fs — auto-releasing",
                    self.active_locked_ticker,
                    self.lock_timeout_seconds
                )
                self.release()
                return True
        return False

# =========================================================================
# DYNAMIC AI EXIT CONDITIONS
# =========================================================================

def evaluate_dynamic_ai_exit_conditions(
    ticker: str,
    position_data: dict,
    market_data: MarketDataPoint,
    regime_context: str = ""
) -> Tuple[bool, str]:
    """Evaluate dynamic AI exit conditions.
    
    Returns (should_exit, reason).
    
    Triggers exit when:
    - RSI is overbought (>85 for longs, <15 for shorts)
    - Market regime shifts to CHOPPY
    - Price hits dynamic ATR-based stop
    """
    should_exit = False
    reason = ""
    
    # 1. RSI overbought/oversold check
    rsi = float(market_data.indicators.get("RSI", 50.0) or 50.0)
    action = str(position_data.get("action", "")).upper()
    
    if action == "BUY" and rsi > 85:
        should_exit = True
        reason = f"RSI overbought: {rsi:.1f} > 85"
    elif action == "SELL" and rsi < 15:
        should_exit = True
        reason = f"RSI oversold: {rsi:.1f} < 15"
    
    # 2. CHOPPY regime check
    regime = str(regime_context or "").upper()
    if "CHOPPY" in regime:
        should_exit = True
        reason = f"Market regime shifted to CHOPPY: {regime_context}"
    
    # 3. ATR stop check (if position has dynamic stop)
    current_price = float(market_data.price or 0.0)
    stop_loss = float(position_data.get("stop_loss", 0.0) or 0.0)
    
    if stop_loss > 0 and current_price > 0:
        if action == "BUY" and current_price <= stop_loss:
            should_exit = True
            reason = f"ATR stop hit: {current_price:.2f} <= {stop_loss:.2f}"
        elif action == "SELL" and current_price >= stop_loss:
            should_exit = True
            reason = f"ATR stop hit: {current_price:.2f} >= {stop_loss:.2f}"
    
    if should_exit:
        logger.info(
            "[EXIT] Dynamic AI exit triggered for %s: %s",
            ticker, reason
        )
    return should_exit, reason

# =========================================================================
# UTILITY EXTENSION SECTION
# =========================================================================

def is_futures_ticker(ticker: str) -> bool:
    """Deterministic validation filter to identify futures and micro-futures index assets."""
    canonical_futures_roots = ["MNQ", "MES", "CL", "GC", "NAS100", "US500", "Crude", "Gold"]
    clean_ticker = ticker.upper().replace("CME_MINI:", "").replace("NYMEX:", "").replace("COMEX:", "")
    
    # Evaluate if the ticker string matches any known futures root architecture
    if any(root in clean_ticker for root in canonical_futures_roots) or "1!" in ticker or "=F" in ticker:
        return True
    return False


def is_crypto_ticker(ticker: str) -> bool:
    """Deterministic validation filter to identify digital asset crypto infrastructure pairs."""
    crypto_roots = ["BTC", "ETH", "SOL", "USDT", "USDC"]
    clean_ticker = ticker.upper()
    if any(root in clean_ticker for root in crypto_roots):
        return True
    return False


# =========================================================================
# MAIN TRADING ENGINE
# =========================================================================

class VcaniTradeEngine:
    """Main trading engine with single-asset lock and dynamic exits."""
    
    def __init__(self):
        # Initialize logging
        logging.basicConfig(
            level=getattr(logging, config.LOG_LEVEL),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            filename=config.LOG_FILE,
            filemode='a'
        )
        
        # Core components
        self.settings = settings_manager
        self.session_detector = MarketSessionDetector()
        self.slippage_guard = SlippageGuard()
        
        # Single-asset lock (ZERO OVERLAP)
        self.asset_lock = SingleAssetLock()
        
        # Risk & profit management
        self.risk_governor = RiskGovernor(
            max_risk_units=3,
            max_exposure_per_unit_pct=5.0,
            max_total_exposure_pct=15.0,
            correlation_threshold=0.85
        )
        self.sentiment_pulse = SentimentPulse(
            check_interval=300,
            red_folder_minutes_before=30,
            red_folder_minutes_after=15
        )
        self.profit_lock = ProfitLock(
            daily_profit_target_pct=3.0,
            daily_max_loss_pct=2.0,
            breakeven_buffer_pct=1.0,
            starting_balance=config.CURRENT_BALANCE
        )
        
        # AI components
        self.brain_swarm = OllamaSwarmConsensus()
        self.code_architect = CodeArchitect()
        self.atr_stops = LooseATRStops(atr_period=14, multiplier=1.5)
        self.visual_confirmation = VisualChartConfirmation(check_interval=60)
        self.vibe_adapter = VibeTradingAdapter()
        
        # Meta-analysis
        self.trade_journal = TradeJournal()
        self.meta_analyzer = MetaAnalyzer(
            journal=self.trade_journal,
            review_interval_hours=24,
            auto_apply=False
        )
        
        # Execution components
        self.rpa_executor = RPAExecutor()
        self._ghost_executor = GhostExecutor()
        self.trade_engine = TradeEngine()
        self.trade_executor = TradeExecutor()
        self.trade_monitor = TradeMonitor()
        self.scanner = Scanner()
        # Set engine lock reference for single-asset lock respect
        self.scanner.set_engine_lock(self.asset_lock)
        
        # Browser agent
        self.browser_agent = None
        self._init_browser_agent()
        
        # Hybrid gateway
        self.hybrid_gateway = HybridExecutionGateway(
            socket_client=None,
            mt5_executor=None,
            ghost_executor=self._ghost_executor
        )
        
        # UI components - PASS ENGINE REFERENCE TO DASHBOARD
        self.dashboard = Dashboard(self)
        self.ai_narrator = AINarrator()
        self.lion_switchboard = LionSwitchboard()
        
        # HAWK PROTOCOL: Set up timer for U-TURN Radar (trailing stops)
        self.hawk_timer = QTimer(self)
        self.hawk_timer.timeout.connect(self.update_trailing_stops)
        self.hawk_timer.start(1000)  # Check every 1 second
        logger.info("[HAWK] U-Turn Radar timer initialized (1000ms interval)")
        
        # Signal Dispatcher & Thread Bridge
        self.signal_bridge = AutomatedSignalBridge(self)
        
        # Threads
        self.cloud_scanner = CloudScannerThread()
        self.signal_listener = SignalListenerThread()
        # Explicitly map dispatcher events straight to dashboard update slots as requested
        self.signal_dispatcher = self.signal_listener.dispatcher
        
        self.data_scout_listener = DataScoutListenerThread()
        
        # State
        self.current_mode = "AUTONOMOUS" if not config.TEACHER_MODE else "TEACHER"
        self.is_running = False
        self.current_watchlist = self._normalize_watchlist(config.ACTIVE_WATCHLIST)
        self.set_watchlist(self.current_watchlist)
        self._wire_ui_signals()
        
        # HAWK PROTOCOL: Single-Asset Lock
        self.target_lock_active = False
        self.locked_asset = None
        self.hawk_mode = True  # Enable single-asset focus
        
        # Balance tracking
        self.balance = float(config.CURRENT_BALANCE)
        self.equity = float(config.CURRENT_BALANCE)
        self.daily_pnl = 0.0
        self.positions = []
        
        # Trading stats
        self.trades_today = 0
        self.daily_wins = 0
        self.max_drawdown = 0.0
        self.peak_balance = float(config.CURRENT_BALANCE)
        
        logger.info("VcaniTrade Engine initialized (Single-Asset Lock enabled)")

    def _wire_ui_signals(self):
        """Connect dashboard/narrator signals to the live engine state."""
        try:
            if getattr(self, "_ui_signals_wired", False):
                return
            self.dashboard.mode_changed.connect(self.set_runtime_mode)
            self.dashboard.watchlist_updated.connect(self.set_watchlist)
            self.dashboard.ticker_changed.connect(self.set_primary_ticker)
            self.cloud_scanner.technical_signal_detected.connect(self._on_technical_signal_detected)
            self.cloud_scanner.signal_detected.connect(self._on_brain_signal_detected)
            self.cloud_scanner.scanner_error.connect(self._on_scanner_error)
            self.cloud_scanner.ticker_status.connect(self._on_ticker_status)
            self.cloud_scanner.heartbeat_pulse.connect(self._on_scanner_heartbeat)
            self.signal_listener.signal_received.connect(self._on_external_signal_received)
            self.signal_listener.handshake_received.connect(self._on_bridge_handshake_received)
            self.signal_listener.listener_error.connect(self._on_listener_error)
            self.data_scout_listener.data_received.connect(self._on_data_scout_received)
            self.data_scout_listener.scout_error.connect(self._on_data_scout_error)
            if hasattr(self.signal_dispatcher, "signal_received"):
                self.signal_dispatcher.signal_received.connect(
                    lambda _payload: self.dashboard.set_bridge_status_connected()
                )
                self.signal_dispatcher.signal_received.connect(
                    lambda payload: self._log_dashboard(
                        f"[BRIDGE] Dispatcher received {payload.get('action', 'SIGNAL')} {payload.get('ticker', 'UNKNOWN')}"
                    )
                )
            if hasattr(self.ai_narrator, "set_watchlist"):
                self.ai_narrator.set_watchlist(self.current_watchlist)
            self._ui_signals_wired = True
            logger.info("[UI-WIRE] Dashboard, narrator, scanner, and engine signals connected.")
            logger.info("[RESTORE] Core signal bridge routes remapped cleanly.")
        except Exception as exc:
            logger.warning("[UI-WIRE] Failed to wire UI signals: %s", exc)

    def set_runtime_mode(self, mode: str):
        """Apply dashboard mode changes to backend components."""
        normalized = str(mode or "TEACHER").upper().strip()
        if normalized not in {"TEACHER", "AUTONOMOUS"}:
            normalized = "TEACHER"
        self.current_mode = normalized
        try:
            self.session_detector.set_runtime_mode(normalized)
        except Exception:
            pass
        try:
            if hasattr(self.ai_narrator, "add_activity"):
                self.ai_narrator.add_activity("[MODE]", f"Runtime mode synced: {normalized}")
        except Exception:
            pass
        self._log_dashboard(f"[ENGINE] Runtime mode synced: {normalized}")
        if normalized == "AUTONOMOUS" and self.is_running:
            QTimer.singleShot(100, self._run_scanner_cycle)

    def set_watchlist(self, tickers):
        """Push dashboard watchlist edits into both scanner paths."""
        self.current_watchlist = self._normalize_watchlist(tickers)
        self.scanner.tickers = list(self.current_watchlist)
        try:
            self.cloud_scanner.scanner.tickers = list(self.current_watchlist)
        except Exception:
            pass
        try:
            self.ai_narrator.set_watchlist(self.current_watchlist)
        except Exception:
            pass
        self._log_dashboard(f"[SCAN] Watchlist synced: {', '.join(self.current_watchlist)}")

    def set_primary_ticker(self, ticker: str):
        """Keep scanner responsive when the dashboard focus ticker changes."""
        ticker = str(ticker or "").strip().upper()
        if ticker and ticker not in self.current_watchlist:
            self.set_watchlist([ticker] + list(self.current_watchlist))

    def _log_dashboard(self, message: str):
        try:
            self.dashboard.log(message)
        except Exception:
            logger.info(message)

    def _on_ticker_status(self, ticker: str, status: str):
        try:
            self.ai_narrator.update_ticker_status(ticker, status)
            if status == "scanning":
                self.ai_narrator.notify_scan_tick(ticker)
        except Exception:
            pass
        self._log_dashboard(f"[SCAN] {ticker}: {status}")

    def _on_scanner_error(self, message: str):
        self._log_dashboard(f"[WARN] Scanner: {message}")
        try:
            self.ai_narrator.add_activity("[WARN]", f"Scanner: {message[:160]}")
        except Exception:
            pass

    def _on_scanner_heartbeat(self, healthy: bool):
        if not healthy:
            self._log_dashboard("[WARN] Scanner heartbeat delayed - watchdog recovering")
            try:
                self.dashboard.set_bridge_status("lost")
            except Exception:
                pass

    def _on_listener_error(self, message: str):
        self._log_dashboard(f"[BRIDGE] Listener error: {message}")
        try:
            self.dashboard.set_bridge_status_disconnected()
            self.ai_narrator.add_activity("[WARN]", f"Signal bridge error: {message[:140]}")
        except Exception:
            pass

    def _on_bridge_handshake_received(self, handshake_data: dict):
        source = handshake_data.get("source_ip", "unknown") if isinstance(handshake_data, dict) else "unknown"
        brain = handshake_data.get("brain", "external") if isinstance(handshake_data, dict) else "external"
        self._log_dashboard(f"[BRIDGE] Handshake online: {brain} from {source}")
        try:
            self.dashboard.set_bridge_status_connected()
            self.ai_narrator.add_activity("[BRIDGE]", f"Signal bridge online: {brain}")
        except Exception:
            pass

    def _on_external_signal_received(self, payload: dict):
        ticker = str(payload.get("ticker", "UNKNOWN") if isinstance(payload, dict) else "UNKNOWN")
        action = str(payload.get("action", "SIGNAL") if isinstance(payload, dict) else "SIGNAL")
        confidence = payload.get("confidence", 0.0) if isinstance(payload, dict) else 0.0
        self._log_dashboard(f"[BRIDGE] Signal received: {action} {ticker} ({confidence})")
        try:
            self.dashboard.set_bridge_status_connected()
            self.ai_narrator.add_activity("[BRIDGE]", f"External signal: {action} {ticker}")
            _speak_alert(f"External signal received. {action} {ticker}")
        except Exception:
            pass
        self.signal_bridge.dispatch_execution_request(payload)

    def _on_data_scout_received(self, payload: dict):
        self._log_dashboard(f"[DATA-SCOUT] Update received: {str(payload)[:160]}")

    def _on_data_scout_error(self, message: str):
        self._log_dashboard(f"[DATA-SCOUT] {message}")

    def _on_technical_signal_detected(self, payload: dict):
        ticker = payload.get("ticker", "UNKNOWN")
        action = payload.get("action", "SIGNAL")
        signal_type = payload.get("signal_type", "SIGNAL")
        confidence = float(payload.get("confidence", 0.0) or 0.0)
        self._log_dashboard(f"[TARGET] {ticker}: {signal_type} -> {action} ({confidence:.0%})")
        try:
            self.ai_narrator.add_activity("[TARGET]", f"{ticker} {signal_type} -> {action}")
        except Exception:
            pass

    def _on_brain_signal_detected(self, payload: dict):
        ticker = payload.get("ticker", "UNKNOWN")
        action = payload.get("action", "WAIT")
        reason = payload.get("reason", "")
        self._log_dashboard(f"[BRAIN] {ticker}: {action} | {reason}")
        try:
            verdict = f"[SIGNAL] {action}"
            self.ai_narrator.flash_brain_verdict(
                ticker,
                verdict,
                reason,
                hold_ms=1200,
                fallback_mode=bool(payload.get("fallback_mode", False)),
                brain_used=str(payload.get("brain_used", "LOCAL_BRAIN")),
            )
            _speak_alert(f"Brain verdict. {action} {ticker}. {reason}")
        except Exception:
            pass

    def process_validated_execution_path(self, payload: dict):
        """Route validated bridge/swarm signals into teacher or autonomous execution."""
        ticker = str(payload.get("ticker") or payload.get("asset") or "UNKNOWN").strip().upper()
        action = str(payload.get("action") or payload.get("signal") or "WAIT").strip().upper()
        reason = str(payload.get("reason") or payload.get("reasoning") or "Bridge signal").strip()
        if action not in {"BUY", "SELL"} or not ticker or ticker == "UNKNOWN":
            self._log_dashboard(f"[BRIDGE] Ignored non-executable signal: {action} {ticker}")
            return

        entry = float(payload.get("entry_price") or payload.get("price") or self._fetch_current_price(ticker) or 0.0)
        stop_loss = float(payload.get("stop_loss") or payload.get("sl") or 0.0)
        take_profit = float(payload.get("take_profit") or payload.get("tp") or 0.0)
        self._log_dashboard(f"[ROUTE] {self.current_mode}: {action} {ticker} | {reason[:140]}")

        try:
            self.ai_narrator.flash_brain_verdict(ticker, f"[SIGNAL] {action}", reason, hold_ms=900)
        except Exception:
            pass

        if self.current_mode != "AUTONOMOUS":
            self._log_dashboard(f"[TEACHER] Approval required for {action} {ticker}")
            return

        result = self.execute_trade(ticker, action, entry, stop_loss, take_profit)
        self._log_dashboard(f"[EXEC] {result.status}: {action} {ticker} {result.reason or ''}")

    def execute_hardened_panic_reset(self):
        """Emergency containment hook used by AutomatedSignalBridge."""
        self.stop()
        try:
            self.asset_lock.release()
        except Exception:
            pass
        self._log_dashboard("[PANIC] Engine paused and asset lock released")
    
    def _normalize_watchlist(self, raw_list):
        """Normalize watchlist and filter muted tickers."""
        muted = getattr(config, "MUTED_TICKERS", set())
        normalized = []
        for ticker in raw_list:
            t = str(ticker or "").strip()
            if t and t not in muted:
                normalized.append(t)
        return normalized or ["BTCUSD"]
    
    def _init_browser_agent(self):
        """Initialize browser agent for TradingView RPA."""
        try:
            self.browser_agent = BrowserAgent(headless=False)
            self.rpa_executor.set_browser_agent(self.browser_agent)
            logger.info("[BROWSER] Browser agent initialized")
        except Exception as e:
            logger.warning("[BROWSER] Browser agent init failed: %s", e)
            self.browser_agent = None
    
    def start(self):
        """Start the trading engine."""
        if self.is_running:
            logger.warning("Engine already running")
            return
        
        self.is_running = True
        
        # Start periodic scanner (scanner is NOT a thread - use QTimer)
        self._start_scanner_timer()
        QTimer.singleShot(250, self._run_scanner_cycle)
        
        # Start trade monitor (it manages its own internal thread)
        if hasattr(self.trade_monitor, 'start'):
            self.trade_monitor.start()
        
        # Start background threads
        if self.cloud_scanner:
            self.cloud_scanner.start()
        if self.signal_listener:
            self.signal_listener.start()
            self.dashboard.set_bridge_status_connected()
            self._log_dashboard(
                f"[BRIDGE] Signal listener armed on {config.LOCAL_LISTENER_HOST}:{config.LOCAL_LISTENER_PORT}"
            )
        if self.data_scout_listener:
            self.data_scout_listener.start()
            self._log_dashboard("[DATA-SCOUT] Listener armed")
        
        logger.info("[ENGINE] VcaniTrade Engine STARTED")
    
    def _start_scanner_timer(self):
        """Start periodic scanner using QTimer."""
        self.scanner_timer = QTimer()
        self.scanner_timer.timeout.connect(self.execute_market_scan_sequence)
        self.scanner_timer.start(60000)
        self._scanner_timer = self.scanner_timer  # Backward-compatible alias.
        logger.info("[RESTORE] Market scanning matrix loop aggressively restarted.")
        try:
            self.ai_narrator.notify_scan_start(len(self.current_watchlist))
        except Exception:
            pass
        self._log_dashboard(f"[SCAN] Scanner armed: {len(self.current_watchlist)} markets, 60s structural cycle")

    def execute_market_scan_sequence(self):
        """QTimer entrypoint for continuous multi-timeframe market sweeps."""
        self._run_scanner_cycle()
    
    def _run_scanner_cycle(self):
        """Run one scanner cycle."""
        try:
            if not self.is_running:
                return
            tickers = list(self.current_watchlist or self.scanner.tickers or [])
            self._log_dashboard(f"[SCAN] Cycle started: {', '.join(tickers[:10])}")
            signals = self.scanner.scan()
            logger.info("[SCAN] Cycle completed with %d technical signal(s)", len(signals or []))
            if not signals:
                self._log_dashboard("[SCAN] Cycle complete: no technical trigger yet")
                return

            self._log_dashboard(f"[TARGET] Found {len(signals)} technical trigger(s); background brain thread evaluating")
            for signal in signals:
                self._on_technical_signal_detected({
                    "ticker": getattr(signal, "ticker", "UNKNOWN"),
                    "action": self.scanner._action_from_signal(signal),
                    "signal_type": getattr(signal, "signal_type", "SIGNAL"),
                    "confidence": getattr(signal, "strength", 0.0),
                    "metadata": getattr(signal, "metadata", {}) or {},
                })
        except Exception as e:
            logger.error(f"[SCAN] Scanner cycle error: {e}")
            self._on_scanner_error(str(e))
    
    def stop(self):
        """Stop the trading engine."""
        self.is_running = False
        
        if hasattr(self.scanner, 'stop'):
            self.scanner.stop()
        if hasattr(self.trade_monitor, 'stop'):
            self.trade_monitor.stop()
        
        logger.info("[ENGINE] VcaniTrade Engine STOPPED")
    
    def execute_trade(self, ticker: str, action: str, entry: float, sl: float, tp: float) -> TradeResult:
        """Execute a trade with single-asset lock enforcement."""
        
        # 0. Enforce native timezone-aware asset class permission gates cleanly
        if not getattr(self, 'can_trade', True):
            if not is_crypto_ticker(ticker) and not is_futures_ticker(ticker):
                logger.warning(f"[RULE-GUARD] Execution blocked: {ticker} does not clear our active asset class clearance profiles.")
                return TradeResult(
                    status="REJECTED_ASSET_CLASS",
                    ticker=ticker,
                    reason=f"Asset class {ticker} not in allowed futures/crypto profiles"
                )
        
        # 1. Check if we're locked for a different ticker
        if self.asset_lock.is_locked_for(ticker):
            logger.warning(
                "[LOCK] Trade REJECTED for %s — locked for %s",
                ticker, self.asset_lock.active_locked_ticker
            )
            return TradeResult(
                status="REJECTED_LOCK",
                ticker=ticker,
                reason=f"Locked for {self.asset_lock.active_locked_ticker}"
            )
        
        # 2. Acquire lock
        if not self.asset_lock.acquire(ticker):
            return TradeResult(
                status="REJECTED_LOCK",
                ticker=ticker,
                reason="Failed to acquire asset lock"
            )
        
        try:
            # 3. Pre-trade audit
            if not self._run_pretrade_market_audit(ticker, entry):
                self.asset_lock.release()
                return TradeResult(
                    status="REJECTED_AUDIT",
                    ticker=ticker,
                    reason="Pre-trade audit failed"
                )
            
            # 4. Execute via appropriate executor
            if config.get_active_mode() == "TRADINGVIEW":
                success = self.rpa_executor.execute_trade(
                    type('Trade', (), {
                        'asset': ticker,
                        'action': action,
                        'entry_price': entry,
                        'stop_loss': sl,
                        'take_profit': tp
                    })()
                )
            else:
                # MT5 or other execution paths
                success = self.trade_executor.execute(
                    ticker=ticker,
                    action=action,
                    entry=entry,
                    sl=sl,
                    tp=tp
                )
            
            if success:
                logger.info("[EXEC] Trade executed: %s %s @ %.2f", action, ticker, entry)
                # HAWK LOCK ACTIVATION
                self.target_lock_active = True
                self.locked_asset = ticker
                logger.info(f"[HAWK] TARGET LOCKED: {ticker}. Scanners suspended.")
                self.suspend_scanners()
                
                return TradeResult(
                    status="EXECUTED",
                    ticker=ticker,
                    action=action,
                    entry_price=entry,
                    stop_loss=sl,
                    take_profit=tp
                )
            else:
                self.asset_lock.release()
                return TradeResult(
                    status="FAILED",
                    ticker=ticker,
                    reason="Execution failed"
                )
        
        except Exception as e:
            logger.error("[EXEC] Trade execution error: %s", e)
            self.asset_lock.release()
            return TradeResult(
                status="ERROR",
                ticker=ticker,
                reason=str(e)
            )
    
    def check_dynamic_exits(self):
        """Check all open positions for dynamic AI exit conditions."""
        for position in list(self.positions):
            ticker = position.get("asset")
            if not ticker:
                continue
            
            # Skip if locked for different ticker
            if self.asset_lock.is_locked_for(ticker):
                continue
            
            # Fetch current market data
            try:
                market_data = self.scanner._fetch_market_data(ticker)
                if not market_data:
                    continue
                
                # Get regime context
                regime = self.brain_swarm.mia.get_market_wisdom(ticker)
                regime_context = regime.get("regime", "")
                
                # Evaluate exit conditions
                should_exit, reason = evaluate_dynamic_ai_exit_conditions(
                    ticker=ticker,
                    position_data=position,
                    market_data=market_data,
                    regime_context=regime_context
                )
                
                if should_exit:
                    logger.info("[EXIT] Closing %s: %s", ticker, reason)
                    self.close_position(ticker, reason)
            
            except Exception as e:
                logger.warning("[EXIT] Error checking dynamic exit for %s: %s", ticker, e)
    
    def close_position(self, ticker: str, reason: str = ""):
        """Close a position and release the asset lock."""
        try:
            # Execute close
            if config.get_active_mode() == "TRADINGVIEW":
                self.rpa_executor.flatten_position(ticker)
            else:
                self.trade_executor.close_position(ticker)
            
            # Release lock
            if self.asset_lock.active_locked_ticker == ticker:
                self.asset_lock.release()
            
            logger.info("[CLOSE] Position closed: %s | Reason: %s", ticker, reason)
            
        except Exception as e:
            logger.error("[CLOSE] Error closing position %s: %s", ticker, e)
    
    def _run_pretrade_market_audit(self, ticker: str, entry_price: float) -> bool:
        """Run pre-trade market audit."""
        if entry_price <= 0:
            return False
        
        # Check slippage
        current_price = self._fetch_current_price(ticker)
        if current_price <= 0:
            return True  # Proceed if can't fetch price
        
        slippage_pct = abs(current_price - entry_price) / entry_price * 100
        if slippage_pct > config.MAX_SLIPPAGE_PERCENT:
            logger.warning(
                "[AUDIT] Slippage %.2f%% exceeds limit %.2f%% for %s",
                slippage_pct, config.MAX_SLIPPAGE_PERCENT, ticker
            )
            return False
        
        return True
    
    def _fetch_current_price(self, ticker: str) -> float:
        """Fetch current price for a ticker."""
        try:
            if config.get_active_mode() == "TRADINGVIEW" and self.browser_agent:
                # Use browser agent to fetch price
                return self.browser_agent.get_current_price(ticker)
            else:
                # Use MT5 or yfinance
                return self.scanner._fetch_market_data(ticker)["Close"].iloc[-1]
        except Exception:
            return 0.0

    # ==================== HAWK PROTOCOL METHODS ====================
    
    def suspend_scanners(self):
        """Halts all background scanning threads except for the locked asset."""
        if hasattr(self, 'scanner') and self.scanner:
            self.scanner.paused = True
            logger.info("[SYSTEM] Multi-ticker scanners PAUSED.")
        
        if hasattr(self, 'cloud_scanner') and self.cloud_scanner:
            if hasattr(self.cloud_scanner, 'stop'):
                self.cloud_scanner.stop()
            logger.info("[SYSTEM] Cloud scanner PAUSED.")
    
    def resume_scanners(self):
        """Resumes scanning after position close."""
        self.target_lock_active = False
        self.locked_asset = None
        
        if hasattr(self, 'scanner') and self.scanner:
            self.scanner.paused = False
            logger.info("[SYSTEM] Multi-ticker scanners RESUMED.")
        
        if hasattr(self, 'cloud_scanner') and self.cloud_scanner:
            if hasattr(self.cloud_scanner, 'start'):
                self.cloud_scanner.start()
            logger.info("[SYSTEM] Cloud scanner RESUMED.")
    
    def update_trailing_stops(self):
        """U-TURN RADAR & TRAILING STOP for locked asset."""
        if not hasattr(self, 'target_lock_active') or not self.target_lock_active:
            return
        
        if not hasattr(self, 'locked_asset') or not self.locked_asset:
            return
        
        # Get current price for locked asset
        current_price = self._fetch_current_price(self.locked_asset)
        if not current_price or current_price <= 0:
            return
        
        # Calculate Dynamic ATR Buffer (using your existing atr_stops logic)
        atr_value = self._calculate_atr(self.locked_asset, period=3)
        if atr_value is None or atr_value <= 0:
            return
        
        buffer = atr_value * 1.5  # 1:1.5 Risk Symmetry
        
        position = self._get_open_position(self.locked_asset)
        if not position:
            self.resume_scanners()
            return
        
        # U-TURN LOGIC
        if position.get('action') == 'BUY':
            trailing_stop = current_price - buffer
            entry_price = position.get('entry_price', 0)
            if entry_price > 0 and current_price < entry_price + (buffer * 0.5):  # Reversal detected
                logger.info(f"[HAWK U-TURN] Trend reversal detected on {self.locked_asset}. Exiting.")
                self.execute_global_profit_harvest(symbol=self.locked_asset)
        
        elif position.get('action') == 'SELL':
            trailing_stop = current_price + buffer
            entry_price = position.get('entry_price', 0)
            if entry_price > 0 and current_price > entry_price - (buffer * 0.5):  # Reversal detected
                logger.info(f"[HAWK U-TURN] Trend reversal detected on {self.locked_asset}. Exiting.")
                self.execute_global_profit_harvest(symbol=self.locked_asset)
    
    def _calculate_atr(self, ticker: str, period: int = 3) -> Optional[float]:
        """Calculate ATR for ticker. Override this with actual implementation."""
        # Placeholder - implement based on your ATR calculation
        logger.debug(f"[HAWK] Calculating ATR for {ticker} (period={period})")
        return None
    
    def _get_open_position(self, ticker: str) -> Optional[dict]:
        """Get open position for ticker."""
        for position in self.positions:
            if isinstance(position, dict) and position.get('asset') == ticker:
                return position
            elif hasattr(position, 'asset') and position.asset == ticker:
                return {
                    'asset': position.asset,
                    'action': position.action.value if hasattr(position.action, 'value') else str(position.action),
                    'entry_price': position.entry_price,
                    'stop_loss': position.stop_loss,
                    'take_profit': position.take_profit,
                }
        return None
    
    def execute_global_profit_harvest(self, symbol=None):
        """
        Instantly closes positions. 
        If symbol is provided, closes only that symbol (U-Turn).
        If None, closes ALL (Manual Button Press).
        """
        symbols_to_close = [symbol] if symbol else self._get_all_open_symbols()
        
        total_profit = 0
        for sym in symbols_to_close:
            pos = self._get_open_position(sym)
            if pos:
                profit = pos.get('pnl', 0)
                self._close_position(sym)
                total_profit += profit
                logger.info(f"[EXECUTION] Closed {sym}. Profit: ${profit}")
        
        # Reset State
        self.resume_scanners()
        
        # Announce via AI Narrator
        msg = f"Harvest complete. Total profit: ${total_profit:.2f}. Scanners re-engaged."
        self._speak(msg)  # Ensure self.speak connects to your TTS engine
        
        return total_profit
    
    def _get_all_open_symbols(self) -> List[str]:
        """Get all symbols with open positions."""
        symbols = []
        for position in self.positions:
            if isinstance(position, dict):
                asset = position.get('asset')
            else:
                asset = getattr(position, 'asset', None)
            if asset and asset not in symbols:
                symbols.append(asset)
        return symbols
    
    def _close_position(self, symbol: str) -> bool:
        """Close position for symbol."""
        try:
            if config.get_active_mode() == "TRADINGVIEW":
                if hasattr(self, 'rpa_executor') and self.rpa_executor:
                    self.rpa_executor.flatten_position(symbol)
            else:
                if hasattr(self, 'trade_executor') and self.trade_executor:
                    self.trade_executor.close_position(symbol)
            
            # Remove from positions list
            self.positions = [p for p in self.positions 
                             if (isinstance(p, dict) and p.get('asset') != symbol) or
                             (not isinstance(p, dict) and getattr(p, 'asset', None) != symbol)]
            
            logger.info(f"[HAWK] Position closed: {symbol}")
            return True
        except Exception as e:
            logger.error(f"[HAWK] Error closing position {symbol}: {e}")
            return False
    
    def _speak(self, msg: str):
        """AI Narrator TTS. Override this with actual TTS implementation."""
        logger.info(f"[AI NARRATOR] {msg}")
        # Try to use AI narrator if available
        if hasattr(self, 'ai_narrator') and self.ai_narrator:
            try:
                if hasattr(self.ai_narrator, 'speak'):
                    self.ai_narrator.speak(msg)
            except Exception:
                pass

# =========================================================================
# MAIN ENTRY POINT
# =========================================================================

def _force_window_visible(window, *, activate: bool = False, label: str = "window"):
    """Show a top-level Qt window and pull it back inside the primary desktop."""
    if not window:
        return

    try:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            width = min(max(window.width(), window.minimumWidth()), geo.width())
            height = min(max(window.height(), window.minimumHeight()), geo.height())
            if window.width() != width or window.height() != height:
                window.resize(width, height)

            max_x = geo.left() + geo.width() - window.width()
            max_y = geo.top() + geo.height() - window.height()
            x = min(max(window.x(), geo.left()), max_x)
            y = min(max(window.y(), geo.top()), max_y)
            if window.x() != x or window.y() != y:
                window.move(x, y)

        window.setWindowOpacity(max(0.75, float(window.windowOpacity() or 1.0)))
        if hasattr(window, "showNormal"):
            window.showNormal()
        window.show()
        window.raise_()
        if activate:
            window.activateWindow()
        logger.info("[BOOT-VISIBILITY] Forced %s visible at %s", label, window.geometry())
    except Exception as exc:
        logger.warning("[BOOT-VISIBILITY] Failed to force %s visible: %s", label, exc)


def main():
    """Master application bootloader ensuring thread-safe object creation order."""
    logger.info("[BOOT] Initializing VcaniTrade AI Production Stack...")
    
    # STEP 1: FORCEFULLY INITIALIZE THE APPLICATION RUNTIME CONTEXT FIRST
    # This completely eliminates the 'Must construct a QApplication before a QWidget' crash
    app = QApplication(sys.argv)
    app.setStyle('Fusion')  # Enforces stable, cross-platform UI drawing
    
    logger.info("[BOOT] QApplication context established successfully.")

    try:
        # STEP 2: INITIALIZE OUR TRADING ENGINE AFTER THE RUNTIME IS ACTIVE
        # Now the engine can safely construct dashboard widgets without memory runtime faults
        engine = VcaniTradeEngine()
        
        logger.info("[BOOT] VcaniTrade AI Engine successfully bound to graphical application thread.")
        
        # STEP 3: DISPLAY EVERY TOP-LEVEL UI SURFACE EXPLICITLY
        # Windows + High-DPI can leave successfully initialized QWidget trees
        # hidden until show/raise/activate happens after construction.
        if hasattr(engine, 'dashboard') and engine.dashboard:
            _force_window_visible(engine.dashboard, activate=True, label="dashboard")

        if hasattr(engine, 'ai_narrator') and engine.ai_narrator:
            _force_window_visible(engine.ai_narrator, activate=False, label="ai_narrator")

        # Re-assert visibility once deferred polish/layout events run. Frameless
        # and Tool windows can briefly hide themselves when flags are applied.
        QTimer.singleShot(
            0,
            lambda: (
                _force_window_visible(getattr(engine, "dashboard", None), activate=True, label="dashboard"),
                _force_window_visible(getattr(engine, "ai_narrator", None), activate=False, label="ai_narrator"),
            ),
        )
        QTimer.singleShot(
            250,
            lambda: (
                _force_window_visible(getattr(engine, "dashboard", None), activate=True, label="dashboard"),
                _force_window_visible(getattr(engine, "ai_narrator", None), activate=False, label="ai_narrator"),
            ),
        )
        
        # STEP 4: START THE TRADING ENGINE (scanner, monitor, listeners)
        engine.start()
        
        logger.info("[BOOT] Dashboard window displayed. Starting event loop.")
        
        # STEP 5: RELEASE CONTROL TO THE PYQT GRAPHICAL EVENT LOOP
        sys.exit(app.exec())
        
    except Exception as e:
        logger.critical(f"[BOOT-CRASH] Critical failure during unified system startup loop: {str(e)}")
        import traceback
        traceback.print_exc()
        print(f"[BOOT-CRASH] Critical failure: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
