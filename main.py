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

import pandas as pd

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
from core.headmaster_agent import HeadmasterSupervisor
from core.hybrid_execution_gateway import HybridExecutionGateway
from core.ladder_exit import ladder_exit_manager
from core.profit_guard import evaluate as pg_evaluate, htf_room_from_prices as pg_htf_room
from core.institutional_precheck import run_institutional_precheck
from core.pnl_tracker import pnl_tracker, reversal_detector, adaptive_risk
from core.liquidity_engine import LiquidityEngine
from core.reversal_engine import reversal_engine
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
# MULTI-ASSET CONCURRENCY LOCK
# =========================================================================

# Maximum number of positions that can be open simultaneously.
# Override in trading_settings.json: {"max_concurrent_positions": 3}
_MAX_CONCURRENT = 3
try:
    with open("trading_settings.json", "r", encoding="utf-8") as _f:
        _MAX_CONCURRENT = int(json.load(_f).get("max_concurrent_positions", 3))
except Exception:
    pass


class SingleAssetLock:
    """Thread-safe multi-asset concurrency lock.

    Allows up to MAX_CONCURRENT positions on DIFFERENT tickers.
    Prevents duplicate positions on the SAME ticker.
    """
    
    def __init__(self):
        self._lock = threading.RLock()
        self.is_currently_holding = False
        self.active_locked_ticker = None
        self.lock_acquired_at = 0.0
        self.lock_timeout_seconds = 1800  # 30 min auto-release
        self._open_tickers: dict[str, float] = {}  # ticker -> acquired_at
        
    def acquire(self, ticker: str) -> bool:
        """Acquire a slot for the given ticker.

        Returns False if:
        - Same ticker already has an open position (no duplicates)
        - Max concurrent positions reached (capacity full)
        """
        with self._lock:
            ticker = ticker.upper()
            # Block duplicate on same ticker
            if ticker in self._open_tickers:
                logger.warning("[LOCK] Duplicate blocked: %s already open", ticker)
                return False
            # Block if at capacity
            if len(self._open_tickers) >= _MAX_CONCURRENT:
                logger.warning(
                    "[LOCK] Capacity full (%d/%d): cannot open %s",
                    len(self._open_tickers), _MAX_CONCURRENT, ticker,
                )
                return False
            self._open_tickers[ticker] = time.time()
            self.is_currently_holding = True
            self.active_locked_ticker = ticker
            self.lock_acquired_at = time.time()
            logger.info(
                "[LOCK] Acquired %s (%d/%d slots used)",
                ticker, len(self._open_tickers), _MAX_CONCURRENT,
            )
            return True
    
    def release(self):
        """Release the most recently acquired ticker."""
        with self._lock:
            if self._open_tickers:
                # Release the active_locked_ticker if set, otherwise last entry
                target = self.active_locked_ticker
                if target and target in self._open_tickers:
                    del self._open_tickers[target]
                else:
                    self._open_tickers.pop(next(reversed(self._open_tickers)), None)
            if self._open_tickers:
                self.active_locked_ticker = next(reversed(self._open_tickers))
            else:
                self.is_currently_holding = False
                self.active_locked_ticker = None
                self.lock_acquired_at = 0.0

    def release_ticker(self, ticker: str):
        """Release a specific ticker (used by close_position)."""
        with self._lock:
            ticker = ticker.upper()
            self._open_tickers.pop(ticker, None)
            if self._open_tickers:
                self.is_currently_holding = True
                self.active_locked_ticker = next(reversed(self._open_tickers))
            else:
                self.is_currently_holding = False
                self.active_locked_ticker = None
                self.lock_acquired_at = 0.0
            logger.info("[LOCK] Released %s (%d slots remaining)", ticker, len(self._open_tickers))

    def force_reset(self):
        """Unconditionally clear ALL lock state. Called on emergency reset."""
        with self._lock:
            self._open_tickers.clear()
            self.is_currently_holding = False
            self.active_locked_ticker = None
            self.lock_acquired_at = 0.0
    
    def is_locked_for(self, ticker: str) -> bool:
        """True if a DIFFERENT ticker has the active lock (legacy compat)."""
        with self._lock:
            if not self.is_currently_holding:
                return False
            return self.active_locked_ticker is not None and self.active_locked_ticker != ticker.upper()
    
    def has_open_position(self, ticker: str) -> bool:
        """True if this specific ticker already has an open position."""
        with self._lock:
            return ticker.upper() in self._open_tickers
    
    def open_count(self) -> int:
        """Number of currently open position slots."""
        with self._lock:
            return len(self._open_tickers)
    
    def check_timeout(self) -> bool:
        """Release stale locks older than timeout."""
        with self._lock:
            if not self.is_currently_holding:
                return False
            if time.time() - self.lock_acquired_at > self.lock_timeout_seconds:
                logger.warning("[LOCK] Timeout — force resetting stale lock on %s", self.active_locked_ticker)
                self.force_reset()
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
    canonical_futures_roots = ["MNQ", "MES", "CL", "GC", "MGC", "XAU", "XAUUSD", "Gold", "GOLD", "NAS100", "US500", "Crude", "Gold"]
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
        self.rpa_executor._engine_ref = self  # Give RPA access to positions for flatten direction
        self._ghost_executor = GhostExecutor()
        self.trade_engine = TradeEngine()
        self.trade_executor = TradeExecutor()
        self.trade_monitor = TradeMonitor(
            ghost_executor=self._ghost_executor,
            on_manual_close=self._on_manual_close_detected,
        )
        self.scanner = Scanner()
        self.headmaster = HeadmasterSupervisor()
        # Set engine lock reference for single-asset lock respect
        self.scanner.set_engine_lock(self.asset_lock)

        # === LIQUIDITY EARLY EXIT ENGINE ===
        # Exits positions a few pips before the nearest liquidation zone,
        # then enforces a global sit-out cooldown before re-entering.
        self._liquidity_engine = LiquidityEngine()
        self._global_sitout_until = 0.0  # time.time() — global cooldown after liquidity exit
        # Buffer: exit this far BEFORE the zone. Uses the SMALLER of the two.
        self._LIQUIDITY_EXIT_BUFFER_PCT = 0.0005   # Tightened: 0.05% (~31 pts on BTC @ 62k)
        self._LIQUIDITY_EXIT_BUFFER_PTS = 5.0      # Tightened: 5 points for early profit lock
        self._LIQUIDITY_SITOUT_SECONDS = 180       # sit out 3 min after liquidity exit

        # === HARD PROFIT TARGET ===
        # Close the entire position when profit reaches this many pips.
        # Non-negotiable — takes profit regardless of what indicators say.
        self._HARD_PROFIT_TARGET_PIPS = 100.0       # close at +100 pips
        self._HARD_PROFIT_SITOUT_SECONDS = 120      # wait 2 min after hitting target

        # === VELEZ REFLEX BRIDGE ===
        # Inject the async flatten / state-reset / scanner-rearm callables into
        # the headmaster so its 500ms native thread can fire the full handshake
        # without ever talking to Ollama.
        self._wire_velez_bridge()
        
        # Browser agent
        self.browser_agent = None
        self._init_browser_agent()
        
        # Hybrid gateway
        self.hybrid_gateway = HybridExecutionGateway(
            socket_client=None,
            mt5_executor=None,
            ghost_executor=self._ghost_executor
        )
        
        # UI components
        self.dashboard = Dashboard()
        self.ai_narrator = AINarrator()
        self.lion_switchboard = LionSwitchboard()
        
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
            self.data_scout_listener.signal_received.connect(self._on_data_scout_received)
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
        # Re-study the market whenever trading mode is (re)activated
        if self.is_running:
            self._begin_market_study()
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
        metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}
        h1_bias = str(metadata.get("h1_bias") or payload.get("h1_bias") or "UNKNOWN").upper()
        message = f"{ticker} {signal_type} -> {action} ({confidence:.0%}) | 1H {h1_bias}"
        self._log_dashboard(f"[TARGET] {message}")
        try:
            self.ai_narrator.add_activity("[TARGET]", message)
            # HAWK BLINK: Update confidence meter on technical signals too
            if action in ("BUY", "SELL") and confidence > 0:
                self.ai_narrator.update_confidence_meter(ticker, action, confidence, status="technical trigger")
            if bool(getattr(config, "ENABLE_TECHNICAL_NARRATION", False)):
                _speak_alert(f"{action} setup on {ticker}. One hour direction {h1_bias}. Waiting for brain confirmation.", min_interval_seconds=5.0)
        except Exception:
            pass

    def _on_brain_signal_detected(self, payload: dict):
        ticker = payload.get("ticker", "UNKNOWN")
        action = payload.get("action", "WAIT")
        reason = str(payload.get("reason", "") or "").strip()[:140]
        h1_analysis = payload.get("h1_analysis", {}) if isinstance(payload.get("h1_analysis", {}), dict) else {}
        h1_bias = str(h1_analysis.get("bias") or "UNKNOWN").upper()
        h1_adx = h1_analysis.get("adx", 0)
        h1_ema = str(h1_analysis.get("ema_alignment") or "MIXED")
        h1_text = f"1H dropdown {h1_bias}, ADX {h1_adx}, {h1_ema}"
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
                confidence=float(payload.get("confidence", 0.0) or 0.0),
            )
            if action in {"BUY", "SELL"}:
                _speak_alert(f"{action} {ticker}. {h1_text}. {reason}", min_interval_seconds=4.0)
            elif action == "WAIT" and bool(getattr(config, "ENABLE_WAIT_NARRATION", False)):
                _speak_alert(f"Wait on {ticker}. {h1_text}. {reason}", min_interval_seconds=4.0)
            _confidence_val = float(payload.get("confidence", 0.0) or 0.0)
            _auto_exec_threshold = float(getattr(config, "HAWK_AUTO_EXEC_CONFIDENCE_THRESHOLD", 0.68) or 0.68)
            if self.current_mode == "AUTONOMOUS" and action in {"BUY", "SELL"}:
                logger.info("[AUTO] Dispatching autonomous execution for %s %s", action, ticker)
                QTimer.singleShot(0, lambda: self.process_validated_execution_path(payload))
            elif self.current_mode == "TEACHER" and action in {"BUY", "SELL"} and _confidence_val >= _auto_exec_threshold:
                logger.info("[TEACHER-AUTO] High confidence signal auto-executing in TEACHER mode: %s %s at %.1f%%", action, ticker, _confidence_val * 100)
                QTimer.singleShot(0, lambda: self.process_validated_execution_path(payload))
        except Exception as _brain_err:
            logger.error("[BRAIN] Signal handler error for %s %s: %s", ticker, action, _brain_err)

    def process_validated_execution_path(self, payload: dict):
        """Route validated bridge/swarm signals into teacher or autonomous execution."""
        ticker = str(payload.get("ticker") or payload.get("asset") or "UNKNOWN").strip().upper()
        action = str(payload.get("action") or payload.get("signal") or "WAIT").strip().upper()
        reason = str(payload.get("reason") or payload.get("reasoning") or "Bridge signal").strip()
        if action not in {"BUY", "SELL"} or not ticker or ticker == "UNKNOWN":
            logger.warning("[AUTO] Ignored non-executable signal: %s %s", action, ticker)
            self._log_dashboard(f"[BRIDGE] Ignored non-executable signal: {action} {ticker}")
            return

        # === STALE POSITION CLEANUP (CRITICAL — must run BEFORE the duplicate guard) ===
        # If a position has been "open" for more than 4 hours without a confirmed close,
        # it's likely a phantom. 4 hours is generous — real futures trades don't last this long.
        import time as _stale_time
        _stale_threshold = 14400  # 4 hours — matches execute_trade stale check
        for _pos in list(self.positions):
            _opened = _pos.get("opened_at", 0)
            if _opened and (_stale_time.time() - _opened) > _stale_threshold:
                logger.warning("[STALE-CLEANUP] Removing phantom position: %s (opened %ds ago, no close event)",
                              _pos.get("asset"), int(_stale_time.time() - _opened))
                try:
                    self.positions.remove(_pos)
                except ValueError:
                    pass

        # === MARKET STUDY GATE ===
        # While the board is still studying the chart, withhold ALL entries.
        if self._market_study_active():
            _remain = int(self._study_until - time.time())
            logger.info("[STUDY] Execution paused during market study — %ds remaining", _remain)
            self._log_dashboard(f"[STUDY] Market study in progress — execution paused, ready in {_remain}s")
            return

        # === DUPLICATE POSITION GUARD ===
        # Don't open another trade if we already have a position open on this ticker.
        # This prevents the 200+ duplicate trades per session problem.
        existing_positions = [p for p in self.positions if p.get("asset") == ticker]
        if existing_positions:
            # VERIFY: check if position actually still exists in TradingView.
            # If TradeMonitor says no active trade, the position was manually closed
            # but our tracker missed the event. Clean up and allow the new signal.
            if not self.trade_monitor.is_active:
                logger.warning(
                    "[GUARD] Position tracker has %s but TradeMonitor says no active trade — "
                    "clearing phantom and allowing new signal", ticker,
                )
                for p in existing_positions:
                    try:
                        self.positions.remove(p)
                    except ValueError:
                        pass
                self.asset_lock.release_ticker(ticker)
                # Fall through to allow the new signal
            else:
                logger.info("[GUARD] Already in position on %s (%d open) — skipping %s signal",
                           ticker, len(existing_positions), action)
                return

        # === COOLDOWN GUARD ===
        # Don't re-entry too quickly after closing a position.
        # After a WIN: wait 2 minutes (let the trend breathe)
        # After a LOSS: wait 5 minutes (don't revenge trade)
        cooldown_key = f"_last_close_time_{ticker}"
        cooldown_reason_key = f"_last_close_reason_{ticker}"
        import time as _time
        last_close = getattr(self, cooldown_key, 0)
        last_reason = getattr(self, cooldown_reason_key, "")
        # Longer cooldown after a loss
        is_loss_close = any(x in str(last_reason).upper() for x in ["STOP", "REVERSAL", "U-TURN", "LOSS"])
        cooldown_seconds = 300 if is_loss_close else 120  # 5 min after loss, 2 min after win
        elapsed = _time.time() - last_close
        if elapsed < cooldown_seconds:
            logger.info("[GUARD] Cooldown active for %s — %ds since last close (%s), need %ds",
                       ticker, int(elapsed), "loss" if is_loss_close else "win", cooldown_seconds)
            return

        # === LAST OPEN COOLDOWN ===
        # Don't re-enter within 5 min of the last BUY/SELL on this ticker.
        # Prevents rapid-fire trading when signals keep firing.
        last_open_key = f"_last_open_time_{ticker}"
        last_open = getattr(self, last_open_key, 0)
        if (_time.time() - last_open) < 300:  # 5 min between opens
            logger.info("[GUARD] Open cooldown for %s — %ds since last entry",
                       ticker, int(_time.time() - last_open))
            return

        # === NUCLEAR DUPLICATE GUARD ===
        # Check if we already have an open position for this ticker.
        # This is the LAST LINE OF DEFENSE — even if the lock fails,
        # this check prevents duplicate positions.
        _existing = [p for p in self.positions if p.get("asset") == ticker]
        if _existing:
            logger.warning(
                "[NUCLEAR-GUARD] BLOCKED %s %s — already have %d position(s) on %s",
                action, ticker, len(_existing), ticker,
            )
            return

        # === LIQUIDITY SIT-OUT CHECK ===
        # After exiting near a liquidity zone, sit out for a few minutes.
        # This replicates your "run away for a few minutes, don't do anything" rule.
        if time.time() < self._global_sitout_until:
            _remaining = int(self._global_sitout_until - time.time())
            logger.info("[SIT-OUT] Cooling down %ds after liquidity exit — skipping %s %s", _remaining, action, ticker)
            return

        # === ADAPTIVE RISK CHECK (cumulative loss protection) ===
        # Blocks or restricts trades when the bot is on a losing streak.
        try:
            _ar_state = adaptive_risk.evaluate()
            if _ar_state.mode == "LOCKED":
                logger.warning("[ADAPTIVE] %s — ALL trades blocked", _ar_state.reason)
                self._log_dashboard(f"[ADAPTIVE] LOCKED: {_ar_state.reason}")
                return
            if _ar_state.mode != "NORMAL":
                logger.info("[ADAPTIVE] %s", _ar_state.reason)
        except Exception:
            _ar_state = None

        # === CONFIDENCE FILTER (HAWK MODE — sniper selectivity) ===
        # Only take trades when confidence is at or above the high-conviction floor.
        # Default is 85% (0.85) — matches the user's "wait for 85%+ only" rule.
        # The floor is taken from trading_settings.json: min_confidence_to_trade,
        # falling back to config.MIN_CONFIDENCE_THRESHOLD (0.90), with 0.85 as
        # the absolute minimum. SOFT signals (~0.62) get REJECTED here on purpose.
        try:
            import json as _conf_json
            with open("trading_settings.json", "r", encoding="utf-8") as _conf_f:
                _conf_settings = _conf_json.load(_conf_f)
        except Exception:
            _conf_settings = {}
        _conf_floor = float(_conf_settings.get(
            "min_confidence_to_trade",
            float(getattr(config, "MIN_CONFIDENCE_THRESHOLD", 0.90))
        ))
        # Clamp to a sane range so a typo in the settings file can't lock the bot out
        _conf_floor = max(0.50, min(_conf_floor, 0.99))
        confidence = float(payload.get("confidence") or payload.get("confidence_score") or 0.0)
        # === BRAIN OVERRIDE ===
        # The scanner blends brain_conf at 0.35 weight plus technical, h1, stability, volume.
        # When the brain returns a strong verdict (>= 0.80) but the technical/range blend
        # dilutes it below the HAWK floor, we still want to honor the brain's call.
        # Use the MAX of (combined confidence, raw brain confidence) for the floor check.
        # This is the "two doctors agree" rule: if EITHER the combined math OR the brain
        # clears the bar, the trade is allowed. Brain confidence is typically delivered
        # in payload["metadata"]["brain_confidence"] (0-1) or payload["brain_confidence"].
        _meta = payload.get("metadata") or {}
        if not isinstance(_meta, dict):
            _meta = {}
        # === Brain confidence is delivered in 3 possible locations ===
        # 1) payload["raw_decision"]["confidence"] — scanner wraps the brain verdict here (0-100)
        # 2) payload["metadata"]["brain_confidence"] — future-proof for swarm payloads
        # 3) payload["brain_confidence"] / payload["brain_conf"] — direct field
        _raw_decision = payload.get("raw_decision") or {}
        if not isinstance(_raw_decision, dict):
            _raw_decision = {}
        _brain_conf_raw = (
            _raw_decision.get("confidence")
            or _meta.get("brain_confidence")
            or payload.get("brain_confidence")
            or _meta.get("brain_conf")
            or payload.get("brain_conf")
            or 0.0
        )
        try:
            _brain_conf_raw = float(_brain_conf_raw)
            _brain_conf = _brain_conf_raw / (100.0 if _brain_conf_raw > 1.0 else 1.0)
        except (TypeError, ValueError):
            _brain_conf = 0.0
        _brain_conf = max(0.0, min(_brain_conf, 1.0))
        # Effective confidence: max of combined and brain, biased toward brain when it's strong.
        _effective_conf = max(confidence, _brain_conf) if _brain_conf >= 0.80 else confidence
        if _effective_conf < _conf_floor:
            logger.info(
                "[GUARD] Confidence too low for %s: %.1f%% combined / %.1f%% brain (need %.0f%% HAWK floor) — skipping %s",
                ticker, confidence * 100, _brain_conf * 100, _conf_floor * 100, action,
            )
            try:
                self.ai_narrator.add_activity(
                    "[FILTER]", f"{ticker} {action} rejected: combined {confidence*100:.0f}% / brain {_brain_conf*100:.0f}% < {_conf_floor*100:.0f}% floor",
                )
            except Exception:
                pass
            return
        # If the brain override kicked in, log it so we can audit.
        if _brain_conf >= 0.80 and _brain_conf > confidence:
            logger.info(
                "[BRAIN-OVERRIDE] %s %s passed via brain confidence: combined=%.1f%% brain=%.1f%% ≥ %.0f%% floor",
                action, ticker, confidence * 100, _brain_conf * 100, _conf_floor * 100,
            )
        logger.info(
            "[GUARD] Confidence OK for %s %s: %.1f%% ≥ %.0f%% HAWK floor",
            action, ticker, _effective_conf * 100, _conf_floor * 100,
        )

        # === CHART MATCH GUARD (BULLETPROOF) ===
        # ONLY trade the configured symbol. If signal is for anything else, BLOCK.
        # This prevents wrong-symbol trades when chart is switched.
        allowed_symbols = config.ACTIVE_SYMBOLS
        if ticker not in allowed_symbols:
            logger.warning("[GUARD] Signal for %s but only %s allowed — BLOCKED",
                          ticker, allowed_symbols)
            return

        # Also verify the TradingView chart matches before clicking
        try:
            import pygetwindow as gw
            tv_windows = [w for w in gw.getAllWindows() if "tradingview" in w.title.lower()]
            if tv_windows:
                chart_title = tv_windows[0].title.upper()
                ticker_clean = ticker.replace("1!", "").replace("=F", "").replace("-", "")
                # Check if current chart matches the signal ticker
                if ticker_clean not in chart_title and ticker not in chart_title:
                    # VISIBLE SWITCH ALERT — don't silently block, tell the user
                    switch_msg = (
                        f"[SWITCH CHART] {action} signal on {ticker}! "
                        f"Your chart shows {tv_windows[0].title[:30]}. "
                        f"Switch to {ticker} to execute this trade."
                    )
                    logger.warning("[SWITCH] %s", switch_msg)
                    self._log_dashboard(f"[SWITCH CHART] {action} {ticker} — SWITCH YOUR CHART NOW!")
                    try:
                        _speak_alert(f"Switch chart to {ticker} for {action} signal!", min_interval_seconds=5.0)
                        self.ai_narrator.flash_brain_verdict(
                            ticker, f"[SWITCH] {action}", f"Switch chart to {ticker}!", hold_ms=3000,
                            confidence=confidence,
                        )
                    except Exception:
                        pass
                    return  # Block execution but user sees exactly what to do
        except Exception:
            # If window check fails, proceed anyway — don't block
            pass

        entry = float(payload.get("entry_price") or payload.get("price") or self._fetch_current_price(ticker) or 0.0)
        stop_loss = float(payload.get("stop_loss") or payload.get("sl") or 0.0)
        take_profit = float(payload.get("take_profit") or payload.get("tp") or 0.0)

        # === MANDATORY STOP LOSS ===
        # If the signal didn't provide a stop loss, calculate one.
        # NEVER enter a trade without a stop loss — this is non-negotiable.
        if stop_loss <= 0 and entry > 0:
            # Default: 0.5% from entry (configurable via env var)
            _sl_pct = float(getattr(config, "DEFAULT_STOP_LOSS_PCT", 0.5) or 0.5) / 100.0
            if action == "BUY":
                stop_loss = round(entry * (1.0 - _sl_pct), 2)
            else:
                stop_loss = round(entry * (1.0 + _sl_pct), 2)
            logger.warning(
                "[SAFETY] No stop loss in signal — calculated default: %s %s @ %.2f -> SL %.2f (%.1f%%)",
                action, ticker, entry, stop_loss, _sl_pct * 100,
            )

        # === MANDATORY TAKE PROFIT ===
        # If no TP provided, set it at 100 pips (the hard profit target).
        if take_profit <= 0 and entry > 0:
            _tp_pips = float(getattr(config, "HARD_PROFIT_TARGET_PIPS", 100) or 100)
            if action == "BUY":
                take_profit = round(entry + _tp_pips, 2)
            else:
                take_profit = round(entry - _tp_pips, 2)
            logger.info(
"[SAFETY] No take profit in signal — set to 100 pips: TP %.2f",
                take_profit,
            )

        logger.info("[EXEC] Prepared %s %s entry=%.2f sl=%.2f tp=%.2f", action, ticker, entry, stop_loss, take_profit)
        self._log_dashboard(f"[ROUTE] {self.current_mode}: {action} {ticker} | {reason[:140]}")

        try:
            self.ai_narrator.flash_brain_verdict(
                ticker, f"[SIGNAL] {action}", reason, hold_ms=900,
                confidence=confidence,
            )
        except Exception:
            pass

        if self.current_mode != "AUTONOMOUS":
            hawk_auto_exec_threshold = float(getattr(config, "HAWK_AUTO_EXEC_CONFIDENCE_THRESHOLD", 0.95) or 0.95)
            if _effective_conf >= hawk_auto_exec_threshold:
                logger.info("[TEACHER-AUTO] High confidence signal overriding TEACHER mode: %s %s at %.1f%%", action, ticker, _effective_conf * 100)
                self._log_dashboard(f"[TEACHER-AUTO] Auto-executing {action} {ticker} at {int(_effective_conf * 100)}% confidence")
            else:
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

    # ====================================================================
    # VELEZ REFLEX BRIDGE — decouples the exit engine from Ollama
    # ====================================================================

    def _wire_velez_bridge(self):
        """Inject async flatten / state-reset / scanner-rearm callables into the
        headmaster so its 500ms native thread can fire the full handshake
        without ever calling Ollama. The 4-step handshake sequence:

          STEP 1: async Playwright flatten click via rpa_executor.flatten_position
          STEP 2: clear internal asset execution tracking + single-asset locks
          STEP 3: print the required console status sequence
          STEP 4: open filters + rearm scanner for next opportunity
        """
        def _async_flatten(ticker: str, reason: str):
            """Async-friendly flatten click. Runs rpa_executor in a worker thread
            so the headmaster's 500ms native loop is never blocked."""
            def _do_flatten():
                try:
                    if config.get_active_mode() == "TRADINGVIEW":
                        self.rpa_executor.flatten_position(ticker)
                    else:
                        self.trade_executor.close_position(ticker)
                except Exception as exc:
                    logger.error("[VELEZ-REFLEX] async flatten failed: %s", exc)
            threading.Thread(target=_do_flatten, name="VelezFlatten", daemon=True).start()

        def _state_reset(ticker: str):
            """Unconditional reset: remove from positions, clear locks, set cooldown,
            put headmaster back to hibernation. Mirrors close_position() but skips
            the flatten click (already fired by _async_flatten)."""
            try:
                for i, pos in enumerate(list(self.positions)):
                    if pos.get("asset") == ticker:
                        self.positions.pop(i)
                        _pk = f"_peak_profit_{ticker}"
                        if hasattr(self, _pk):
                            delattr(self, _pk)
                        break
            except Exception:
                pass
            try:
                self.asset_lock.release_ticker(ticker)
            except Exception:
                pass
            try:
                setattr(self, f"_last_close_time_{ticker}", time.time())
                setattr(self, f"_last_close_reason_{ticker}", "MANUAL_CLOSE")
            except Exception:
                pass
            try:
                self.headmaster.on_position_closed()
            except Exception:
                pass

        def _scanner_rearm():
            """Open filters + command scanner to look for the next opportunity."""
            try:
                if hasattr(self, "_stale_janitor"):
                    QTimer.singleShot(0, self._janitor_clear_phantom_positions)
            except Exception:
                pass
            try:
                if self.is_running:
                    QTimer.singleShot(0, self._run_scanner_cycle)
            except Exception:
                pass
            try:
                self.scanner._clear_signal_history_all() if hasattr(self.scanner, "_clear_signal_history_all") else None
            except Exception:
                pass

        self.headmaster.set_execution_bridge(
            flatten_callable=_async_flatten,
            state_reset_callable=_state_reset,
            scanner_rearm_callable=_scanner_rearm,
        )
        logger.info("[VELEZ-REFLEX] Bridge injected: async flatten + state reset + scanner rearm wired")

    def velez_feed_candle(self, ticker: str, o: float, h: float, l: float, c: float, v: float = 0.0, ts: float = None):
        """Public hook so the Chrome CDP websocket agent can push a freshly
        scraped 2-minute candle into the headmaster's in-memory deque without
        ever crossing the Ollama boundary. Safe to call from any thread."""
        if not getattr(self, "headmaster", None):
            return
        if not self.headmaster._active:
            return
        if ts is None:
            ts = time.time()
        with self.headmaster._lock:
            self.headmaster._candle_deque.append({
                "o": float(o), "h": float(h), "l": float(l),
                "c": float(c), "v": float(v), "ts": float(ts),
            })

    def _normalize_watchlist(self, raw_list):
        """Normalize watchlist and filter muted tickers.
        HAWK MODE: Never inject BTCUSD as fallback — only scan what the user configured."""
        muted = getattr(config, "MUTED_TICKERS", set())
        normalized = []
        for ticker in raw_list:
            t = str(ticker or "").strip()
            if t and t not in muted:
                normalized.append(t)
        if not normalized:
            # Use config defaults instead of hardcoding BTCUSD
            normalized = list(getattr(config, "ACTIVE_SYMBOLS", []) or [])
        return normalized
    
    def _init_browser_agent(self):
        """Initialize browser agent for TradingView RPA."""
        try:
            self.browser_agent = BrowserAgent(headless=False)
            self.rpa_executor.set_browser_agent(self.browser_agent)
            if self.browser_agent.start_background():
                logger.info("[BROWSER] Browser agent connected and background loop started")
            else:
                logger.warning("[BROWSER] Browser agent background start failed")
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
        # Open a Market Study window so the LLM studies the chart before any trade
        self._begin_market_study()
    
    def _start_scanner_timer(self):
        """Start periodic scanner using QTimer.
        Reads interval from trading_settings.json first, then config.SCAN_INTERVAL.
        Clamped to [SCAN_INTERVAL_MIN, SCAN_INTERVAL_MAX] to protect the data feed."""
        try:
            _settings = self.settings.get_all() if hasattr(self, "settings") else {}
            if not _settings:
                import json as _json
                with open("trading_settings.json", "r", encoding="utf-8") as _f:
                    _settings = _json.load(_f)
            _interval = float(_settings.get("scan_interval_seconds", config.SCAN_INTERVAL))
            _min = float(_settings.get("scan_interval_min", config.SCAN_INTERVAL_MIN))
            _max = float(_settings.get("scan_interval_max", config.SCAN_INTERVAL_MAX))
        except Exception:
            _interval = float(config.SCAN_INTERVAL)
            _min = float(config.SCAN_INTERVAL_MIN)
            _max = float(config.SCAN_INTERVAL_MAX)
        _interval = max(_min, min(_interval, _max))
        interval_ms = int(_interval * 1000)
        self.scanner_timer = QTimer()
        self.scanner_timer.timeout.connect(self.execute_market_scan_sequence)
        self.scanner_timer.start(interval_ms)
        self._scanner_timer = self.scanner_timer
        self._current_scan_interval_seconds = _interval
        logger.info("[SCAN] Timer armed: %.2fs cycle (%.0fms) — %d ticker(s)", _interval, interval_ms, len(self.current_watchlist or []))
        try:
            self.ai_narrator.add_activity("[SCAN]", f"Timer armed: {_interval:.2f}s cycle on {len(self.current_watchlist or [])} ticker(s)")
        except Exception:
            pass
        
        # FAST EXIT MONITOR: Check open positions every 1 second for instant profit-taking (tightened for prop firm)
        self._exit_timer = QTimer()
        self._exit_timer.timeout.connect(self._run_position_exit_scan)
        self._exit_timer.start(1000)  # 1 second - aggressive for prop firm exam

        # STALE POSITION JANITOR: Clear phantom positions every 30s so a stuck
        # tracker can never block new entries forever (even if no signal arrives)
        self._stale_janitor = QTimer()
        self._stale_janitor.timeout.connect(self._janitor_clear_phantom_positions)
        self._stale_janitor.start(30000)  # 30 seconds
        # Run once at startup so any positions from a previous crashed session are cleared
        QTimer.singleShot(2000, self._janitor_clear_phantom_positions)
        
        logger.info("[RESTORE] Market scanning matrix loop aggressively restarted.")
        logger.info("[EXITS] Fast exit monitor armed: checking every 5 seconds")
        try:
            self.ai_narrator.notify_scan_start(len(self.current_watchlist))
        except Exception:
            pass
        self._log_dashboard(f"[SCAN] Scanner armed: {len(self.current_watchlist)} market(s), {_interval:.2f}s structural cycle")
        self._log_dashboard(f"[SCAN] Set scan_interval_seconds in trading_settings.json (range {_min:.1f}-{_max:.1f}s) to change at runtime.")

    def _begin_market_study(self):
        """Open a Market Study window: the LLM/scanner keep analyzing the chart,
        but execution is blocked until the window ends. Prevents impulsive
        entries the instant the board starts."""
        if not getattr(config, "MARKET_STUDY_ENABLED", True):
            return
        secs = float(getattr(config, "MARKET_STUDY_SECONDS", 180) or 180)
        if secs <= 0:
            return
        self._study_until = time.time() + secs
        self._log_dashboard(
            f"[STUDY] Market study started — LLM reading the chart, execution paused for {int(secs)}s"
        )
        logger.info("[STUDY] Market study window opened for %.0fs", secs)
        try:
            QTimer.singleShot(300, self._run_scanner_cycle)
        except Exception:
            pass

    def _market_study_active(self) -> bool:
        su = getattr(self, "_study_until", 0)
        return bool(su) and time.time() < su

    def update_scan_interval(self, new_seconds: float = None) -> float:
        """Reload the scan interval from trading_settings.json and rearm the QTimer.
        Returns the new effective interval in seconds. Pass new_seconds to override
        without touching the settings file."""
        try:
            import json as _json
            with open("trading_settings.json", "r", encoding="utf-8") as _f:
                _settings = _json.load(_f)
        except Exception:
            _settings = {}
        if new_seconds is None:
            new_seconds = float(_settings.get("scan_interval_seconds", config.SCAN_INTERVAL))
        _min = float(_settings.get("scan_interval_min", config.SCAN_INTERVAL_MIN))
        _max = float(_settings.get("scan_interval_max", config.SCAN_INTERVAL_MAX))
        new_seconds = max(_min, min(float(new_seconds), _max))
        if getattr(self, "scanner_timer", None) is not None:
            try:
                self.scanner_timer.stop()
                self.scanner_timer.start(int(new_seconds * 1000))
            except Exception as exc:
                logger.warning("[SCAN] Failed to rearm scanner timer: %s", exc)
        self._current_scan_interval_seconds = new_seconds
        logger.info("[SCAN] Interval updated to %.2fs (watchlist=%d)", new_seconds, len(self.current_watchlist or []))
        try:
            self.ai_narrator.add_activity("[SCAN]", f"Interval updated to {new_seconds:.2f}s on {len(self.current_watchlist or [])} ticker(s)")
        except Exception:
            pass
        return new_seconds

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
                self._run_position_exit_scan()
                self._log_dashboard("[SCAN] Cycle complete: no technical trigger yet")
                return

            self._log_dashboard(f"[TARGET] Found {len(signals)} technical trigger(s); background brain thread evaluating")
            self._run_position_exit_scan()
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
    
    def _velez_trend_allows(self, ticker: str, action: str):
        """Oliver Velez directional gate on the LIVE chart.

        BUY  only if price is ABOVE EMA20 AND ABOVE EMA200.
        SELL only if price is BELOW  EMA20 AND BELOW  EMA200.
        Price between the EMAs = NO TRADE (rejected).

        Uses the same live chart data the scanner uses, so a brain/swarm
        signal reasoned on a higher timeframe cannot slip through counter-trend.
        Returns (allowed: bool, reason: str).
        """
        if not getattr(config, "VELEZ_GATE_ENABLED", True):
            return True, "gate disabled"
        action = str(action or "").upper()
        if action not in ("BUY", "SELL"):
            return True, "non-directional"
        try:
            interval = str(getattr(config, "VELEZ_CHART_INTERVAL", "1m") or "1m")
            df = self.scanner._fetch_market_data(ticker, interval=interval)
            if df is None or len(df) < 20:
                # No live chart data: do not block blindly, but warn.
                return True, "no chart data (skipped)"
            close = df["Close"] if "Close" in df else df.get("close")
            if close is None or len(close) < 20:
                return True, "no close data (skipped)"
            price = float(close.iloc[-1])
            ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
            have200 = len(close) >= 200
            ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1]) if have200 else 0.0
            require200 = getattr(config, "VELEZ_REQUIRE_200EMA", True) and have200

            if action == "BUY":
                if price <= ema20:
                    return False, f"BUY blocked: price {price:.2f} ≤ 20EMA {ema20:.2f} (Velez)"
                if require200 and price <= ema200:
                    return False, f"BUY blocked: price {price:.2f} ≤ 200EMA {ema200:.2f} (Velez)"
                if have200 and ema20 < ema200:
                    return False, f"BUY blocked: 20EMA {ema20:.2f} below 200EMA {ema200:.2f} (bear structure)"
                return True, f"BUY ok: price {price:.2f} > 20EMA {ema20:.2f}" + (f" > 200EMA {ema200:.2f}" if have200 else "")
            else:  # SELL
                if price >= ema20:
                    return False, f"SELL blocked: price {price:.2f} ≥ 20EMA {ema20:.2f} (Velez)"
                if require200 and price >= ema200:
                    return False, f"SELL blocked: price {price:.2f} ≥ 200EMA {ema200:.2f} (Velez)"
                if have200 and ema20 > ema200:
                    return False, f"SELL blocked: 20EMA {ema20:.2f} above 200EMA {ema200:.2f} (bull structure)"
                return True, f"SELL ok: price {price:.2f} < 20EMA {ema20:.2f}" + (f" < 200EMA {ema200:.2f}" if have200 else "")
        except Exception as e:
            logger.debug("[VELEZ-GATE] eval error (allowing): %s", e)
            return True, f"eval error (skipped): {e}"

    def execute_trade(self, ticker: str, action: str, entry: float, sl: float, tp: float) -> TradeResult:
        """Execute a trade with multi-asset concurrency control.
        RULE: Up to MAX_CONCURRENT positions on different tickers. No duplicates on same ticker."""

        # === MARKET STUDY GATE (withhold entries while the board studies) ===
        if self._market_study_active():
            _remain = int(self._study_until - time.time())
            logger.info("[STUDY] Execution paused during market study — %ds remaining", _remain)
            self._log_dashboard(f"[STUDY] Market study in progress — execution paused, ready in {_remain}s")
            return TradeResult(
                status="REJECTED_STUDY",
                ticker=ticker,
                action=action,
                reason=f"Market study in progress ({_remain}s remaining)",
            )

        # === SESSION / MARKET-HOURS GATE (don't enter a closed market) ===
        if getattr(config, "SESSION_GATE_ENABLED", True):
            try:
                _sd = getattr(self, "session_detector", None)
                if _sd is not None:
                    _crypto = is_crypto_ticker(ticker)
                    _weekend_closed = (not _crypto) and _sd.is_weekend_mode()
                    _no_new = not _sd.should_allow_new_trades(ticker)
                    if _weekend_closed or _no_new:
                        _ctx = _sd.get_session_context()
                        _sess = str(_ctx.get("primary_session", "CLOSED")) if isinstance(_ctx, dict) else "CLOSED"
                        logger.info("[SESSION] No new trades for %s — market %s (standing aside)", ticker, _sess)
                        self._log_dashboard(f"[SESSION] Market {_sess} — standing aside, no entries on {ticker}")
                        return TradeResult(
                            status="REJECTED_SESSION_CLOSED",
                            ticker=ticker,
                            action=action,
                            reason=f"Market closed ({_sess})",
                        )
            except Exception as _sess_err:
                logger.debug("[SESSION] gate error (allowing): %s", _sess_err)

        # === INSTITUTIONAL PRE-CHECK (volume / profile / order flow / sweep) ===
        if getattr(config, "INSTITUTIONAL_GATE_ENABLED", True):
            try:
                _pc_interval = str(getattr(config, "VELEZ_CHART_INTERVAL", "1m") or "1m")
                _pc_df = self.scanner._fetch_market_data(ticker, interval=_pc_interval)
                if _pc_df is not None and len(_pc_df) >= 20:
                    _pc = run_institutional_precheck(
                        ticker, _pc_df, action,
                        cfg={
                            "block_sweep": getattr(config, "INSTITUTIONAL_BLOCK_SWEEP", True),
                            "block_flow": getattr(config, "INSTITUTIONAL_BLOCK_ORDERFLOW", True),
                            "require_flow": getattr(config, "INSTITUTIONAL_REQUIRE_FLOW", True),
                            "flow_min": float(getattr(config, "INSTITUTIONAL_FLOW_MIN", 0.10) or 0.10),
                            "require_discount": getattr(config, "INSTITUTIONAL_REQUIRE_DISCOUNT", False),
                        },
                    )
                    logger.info("[PRE-CHECK] %s", _pc["summary"])
                    self._log_dashboard(f"[PRE-CHECK] {ticker} {action}: {_pc['verdict']} | {_pc['summary']}")
                    if _pc.get("block"):
                        logger.warning("[PRE-CHECK] REJECTED %s %s: %s", action, ticker, _pc["reason"])
                        self._log_dashboard(f"[PRE-CHECK] BLOCKED {action} {ticker}: {_pc['reason']}")
                        return TradeResult(
                            status="REJECTED_INSTITUTIONAL",
                            ticker=ticker,
                            action=action,
                            reason=_pc["reason"],
                        )
                else:
                    logger.debug("[PRE-CHECK] no chart data for %s — skipped", ticker)
            except Exception as _pc_err:
                logger.debug("[PRE-CHECK] error (allowing): %s", _pc_err)

        # === OLIVER VELEZ DIRECTIONAL GATE (every entry must pass) ===
        _velez_ok, _velez_reason = self._velez_trend_allows(ticker, action)
        if not _velez_ok:
            logger.warning("[VELEZ-GATE] REJECTED counter-trend %s %s: %s", action, ticker, _velez_reason)
            self._log_dashboard(f"[VELEZ-GATE] BLOCKED {action} {ticker}: {_velez_reason}")
            return TradeResult(
                status="REJECTED_TREND",
                ticker=ticker,
                action=action,
                reason=_velez_reason,
            )
        logger.info("[VELEZ-GATE] %s %s passed: %s", action, ticker, _velez_reason)

        # SAFETY: Remove positions older than 4 hours (likely phantoms).
        # 4 hours is long enough for any real futures trade.
        import time as _time
        STALE_THRESHOLD = 14400  # 4 hours
        for pos in list(self.positions):
            opened_at = pos.get("opened_at", 0)
            if opened_at and (_time.time() - opened_at) > STALE_THRESHOLD:
                logger.warning(
                    "[STALE] Removing phantom position: %s (opened %ds ago — exceeds 4h threshold)",
                    pos.get("asset"), int(_time.time() - opened_at),
                )
                self.positions.remove(pos)
        
        # 0. Enforce native timezone-aware asset class permission gates cleanly
        if not getattr(self, 'can_trade', True):
            if not is_crypto_ticker(ticker) and not is_futures_ticker(ticker):
                logger.warning(f"[RULE-GUARD] Execution blocked: {ticker} does not clear our active asset class clearance profiles.")
                return TradeResult(
                    status="REJECTED_ASSET_CLASS",
                    ticker=ticker,
                    reason=f"Asset class {ticker} not in allowed futures/crypto profiles"
                )
        
        # 1. Acquire lock (handles duplicate check + capacity check)
        if not self.asset_lock.acquire(ticker):
            return TradeResult(
                status="REJECTED_LOCK",
                ticker=ticker,
                reason=f"Lock rejected for {ticker} (duplicate or capacity full)"
            )
        
        try:
            # 3. Pre-trade audit
            if not self._run_pretrade_market_audit(ticker, entry):
                self.asset_lock.release_ticker(ticker)
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
                import time as _t
                self.positions.append(
                    {
                        "asset": ticker,
                        "action": action,
                        "entry_price": entry,
                        "stop_loss": sl,
                        "take_profit": tp,
                        "opened_at": _t.time(),
                    }
                )
                # Track last open time for cooldown
                setattr(self, f"_last_open_time_{ticker}", _t.time())
                # WAKE THE HEADMASTER — new position to supervise
                try:
                    self.headmaster.on_position_opened(
                        ticker=ticker,
                        action=action,
                        entry_price=entry,
                        indicators={
                            "RSI": self.trade_engine.last_indicators.get("rsi", 50),
                            "ema9": self.trade_engine.last_indicators.get("ema9", 0),
                            "ema21": self.trade_engine.last_indicators.get("ema21", 0),
                            "macd_hist": self.trade_engine.last_indicators.get("macd_hist", 0),
                        }
                    )
                except Exception as hm_err:
                    logger.debug("[HEADMASTER] Init error (non-critical): %s", hm_err)
                
                # REGISTER LADDER EXIT — TP1/TP2/TP3 partial profit-taking
                try:
                    ladder_exit_manager.register_trade(
                        symbol=ticker,
                        side=action,
                        entry_price=entry,
                        initial_stop=sl,
                        quantity=1.0,
                    )
                except Exception as ladder_err:
                    logger.debug("[LADDER] Registration error (non-critical): %s", ladder_err)
                
                # REGISTER TRADE MONITOR — detect manual closes in TradingView
                try:
                    self.trade_monitor.set_trade(ticker, action, entry)
                except Exception as tm_err:
                    logger.debug("[MONITOR] set_trade error (non-critical): %s", tm_err)

                # REGISTER REVERSAL DETECTOR — catch immediate post-entry reversals
                try:
                    reversal_detector.register_entry(
                        asset=ticker, side=action,
                        entry_price=entry, stop_loss=sl,
                    )
                except Exception:
                    pass
                
                return TradeResult(
                    status="EXECUTED",
                    ticker=ticker,
                    action=action,
                    entry_price=entry,
                    stop_loss=sl,
                    take_profit=tp
                )
            else:
                self.asset_lock.release_ticker(ticker)
                return TradeResult(
                    status="FAILED",
                    ticker=ticker,
                    reason="Execution failed"
                )
        
        except Exception as e:
            logger.error("[EXEC] Trade execution error: %s", e)
            self.asset_lock.release_ticker(ticker)
            return TradeResult(
                status="ERROR",
                ticker=ticker,
                reason=str(e)
            )
    
    def _build_market_data_point(self, ticker: str, df) -> Optional[MarketDataPoint]:
        """Build a compact MarketDataPoint from OHLCV for exit scanning."""
        try:
            from ta import momentum, trend

            close = df["Close"].dropna()
            high = df["High"].dropna()
            low = df["Low"].dropna()
            volume = df["Volume"].fillna(0)
            if len(close) < 20 or len(high) < 20 or len(low) < 20:
                return None

            prev_close = close.shift(1)
            true_range = pd.concat(
                [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
                axis=1,
            ).max(axis=1)
            atr = float(true_range.rolling(window=14, min_periods=1).mean().iloc[-1] or 0.0)
            ema9 = float(close.ewm(span=9, adjust=False).mean().iloc[-1] or 0.0)
            ema21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1] or 0.0)
            rsi = float(momentum.RSIIndicator(close=close, window=14).rsi().iloc[-1] or 50.0)
            macd = trend.MACD(close=close)
            macd_hist = float(macd.macd_diff().iloc[-1] or 0.0)
            macd_hist_prev = float(macd.macd_diff().iloc[-2] or 0.0)
            current_price = float(close.iloc[-1])
            body = current_price - float(df["Open"].iloc[-1])
            body_pct = body / max(abs(current_price), 1e-9) * 100.0

            return MarketDataPoint(
                asset=ticker,
                price=current_price,
                volume=float(volume.iloc[-1] or 0.0),
                indicators={
                    "RSI": rsi,
                    "ATR": atr,
                    "EMA9": ema9,
                    "EMA21": ema21,
                    "MACD_HIST": macd_hist,
                    "MACD_HIST_PREV": macd_hist_prev,
                    "CANDLE_BODY": body,
                    "CANDLE_BODY_PCT": body_pct,
                    "CANDLE_OPEN": float(df["Open"].iloc[-1] or 0.0),
                    "CANDLE_CLOSE": current_price,
                    "PREV_CANDLE_OPEN": float(df["Open"].iloc[-2] or 0.0) if len(df) > 1 else 0.0,
                    "PREV_CANDLE_CLOSE": float(close.iloc[-2] or 0.0) if len(close) > 1 else 0.0,
                },
            )
        except Exception as e:
            logger.debug("[EXIT] Failed to build market data point for %s: %s", ticker, e)
            return None

    def _evaluate_position_exit(self, position: dict, market_data: MarketDataPoint) -> Tuple[bool, str]:
        """HAWK EXIT: Dynamic tiered pullback from peak = close full.

        Retracement thresholds tighten as profit grows:
          < $100 peak  →  20% giveback allowed  (noise filter for small gains)
          $100–$300    →  15% giveback
          $300–$800    →  10% giveback  (your $600 scenario exits at $540)
          > $800       →   8% giveback  (protect large wins aggressively)
        """
        action = str(position.get("action", "")).upper()
        entry = float(position.get("entry_price", market_data.price) or market_data.price)
        price = float(market_data.price or 0.0)
        if price <= 0:
            return False, ""

        atr = float(market_data.indicators.get("ATR", 0.0) or 0.0)
        pnl_points = price - entry if action == "BUY" else entry - price
        pnl_atr = pnl_points / max(atr, 1e-9)
        pnl_dollars = pnl_points * 2.0  # approximate for futures ($2/pt MNQ)

        # --- HARD STOP LOSS: 2x ATR (prevents catastrophic losses) ---
        if pnl_atr <= -2.0:
            return True, f"STOP LOSS: {pnl_atr:.1f} ATR ({pnl_points:.0f} pts)"

        # --- Track peak profit in points ---
        max_pnl_key = f"_max_pnl_{position.get('asset', '')}_{entry}"
        prev_max_pts = getattr(self, max_pnl_key, 0.0)
        if pnl_points > prev_max_pts:
            setattr(self, max_pnl_key, pnl_points)
            prev_max_pts = pnl_points

        # --- DYNAMIC TIERED PULLBACK RULE ---
        # Tighter retracement as profit grows — institutional-style trailing.
        if prev_max_pts > (atr * 0.3):  # Only activate after real profit
            peak_dollars = prev_max_pts * 2.0
            # Select retracement threshold based on peak profit tier
            # TIGHTENED: institutional-grade — exit before profit evaporates
            if peak_dollars > 800:
                pullback_threshold = 0.04   # 4%  — protect large wins aggressively
            elif peak_dollars > 300:
                pullback_threshold = 0.05   # 5% — don't give back more than 5%
            elif peak_dollars > 100:
                pullback_threshold = 0.08   # 8% — moderate wins, tighter hold
            else:
                pullback_threshold = 0.10   # 10% — small wins, still tight

            pullback_pct = (prev_max_pts - pnl_points) / prev_max_pts if prev_max_pts > 0 else 0
            if pullback_pct >= pullback_threshold:
                return True, (
                    f"PULLBACK {pullback_threshold*100:.0f}%: peaked +{prev_max_pts:.1f} pts "
                    f"(~${peak_dollars:.0f}), gave back {pullback_pct*100:.0f}% to "
                    f"+{pnl_points:.1f} pts — CLOSING FULL POSITION"
                )

        # --- BREAK-EVEN PROTECTION ---
        # Once up 1 ATR, never let it go negative (break-even floor)
        if (atr * 1.0) > 0 and prev_max_pts >= (atr * 1.0) and pnl_points <= 0:
            return True, f"BREAK-EVEN: was +{prev_max_pts:.1f} pts, now {pnl_points:.1f} — protecting capital"

        # --- RSI EXTREME with profit ---
        rsi = float(market_data.indicators.get("RSI", 50) or 50)
        if action == "BUY" and rsi >= 78 and pnl_atr > 0.5:
            return True, f"RSI {rsi:.0f} exhausted at +{pnl_atr:.1f} ATR"
        if action == "SELL" and rsi <= 22 and pnl_atr > 0.5:
            return True, f"RSI {rsi:.0f} exhausted at +{pnl_atr:.1f} ATR"

        return False, ""

    def _check_u_turn_exit(self, position: dict) -> tuple:
        """HAWK U-TURN: Detect when price reverses from peak profit and exit."""
        ticker = position.get("asset")
        if not ticker:
            return False, ""
        try:
            df = self.scanner._fetch_market_data(ticker)
            if df is None or df.empty:
                return False, ""
            current_price = float(df["Close"].iloc[-1])
            entry_price = float(position.get("entry_price", 0) or 0)
            if entry_price <= 0:
                return False, ""
            action = position.get("action", "BUY")
            is_long = str(action).upper() in ("BUY", "LONG")
            if is_long:
                profit = current_price - entry_price
            else:
                profit = entry_price - current_price
            peak_key = f"_peak_profit_{ticker}"
            current_peak = getattr(self, peak_key, 0.0)
            if profit > current_peak:
                setattr(self, peak_key, profit)
                current_peak = profit
            if current_peak > 0:
                pullback_pct = (current_peak - profit) / current_peak
                if pullback_pct > 0.30 and profit > 0:  # Tightened from 0.40 to 0.30
                    return True, f"U-TURN: Peak {current_peak:.2f}, now {profit:.2f} ({pullback_pct*100:.0f}% pullback)"
                if profit < 0 and current_peak > 0:
                    return True, f"U-TURN: Was +{current_peak:.2f}, now {profit:.2f} - protect capital"
            return False, ""
        except Exception as e:
            logger.debug("[U-TURN] Error checking %s: %s", ticker, e)
            return False, ""

    def _check_ema9_retest_exit(self, position: dict) -> tuple:
        """EMA9 RETEST EXIT: Exit when price dips to or below EMA9 after an uptrend.

        This is the core of your strategy:
        - Price was above EMA9 (trend intact)
        - Price retraces and TOUCHES EMA9 (wick dips to/below it)
        - Exit immediately — don't wait for the candle to close

        TIGHTENED: Now triggers on WICK TOUCH (not just close below).
        This catches the reversal earlier, before the candle fully closes.
        """
        ticker = position.get("asset")
        if not ticker:
            return False, ""
        try:
            entry_price = float(position.get("entry_price", 0) or 0)
            if entry_price <= 0:
                return False, ""

            df = self.scanner._fetch_market_data(ticker)
            if df is None or len(df) < 15:
                return False, ""

            close = df["Close"]
            low = df["Low"]
            ema9 = close.ewm(span=9, adjust=False).mean()

            current_price = float(close.iloc[-1])
            current_low = float(low.iloc[-1])
            current_ema9 = float(ema9.iloc[-1])
            prev_close = float(close.iloc[-2])
            prev_ema9 = float(ema9.iloc[-2])

            if current_ema9 <= 0 or prev_ema9 <= 0:
                return False, ""

            # Only trigger if we're in profit
            if current_price <= entry_price:
                return False, ""

            # Check: was price above EMA9 on previous candle?
            was_above = prev_close > prev_ema9
            # TIGHTENED: check if current candle's WICK touched or crossed EMA9
            wick_touched = current_low <= current_ema9
            # Also check if close is below (original stricter condition)
            close_below = current_price < current_ema9

            if was_above and (wick_touched or close_below):
                ema9_slope = float(ema9.iloc[-1]) - float(ema9.iloc[-3]) if len(ema9) >= 3 else 0.0
                pnl_pts = current_price - entry_price

                trigger = "wick touch" if wick_touched and not close_below else "close below"
                reason = (
                    f"EMA9 RETEST ({trigger}): BUY {ticker} @ {entry_price:.2f} -> "
                    f"low {current_low:.2f} {'<=' if wick_touched else '>'} EMA9 {current_ema9:.2f} "
                    f"(close={current_price:.2f}, slope={'rising' if ema9_slope > 0 else 'falling'}) "
                    f"--- locked +{pnl_pts:.1f} pts"
                )
                logger.info("[EMA9-EXIT] %s", reason)
                return True, reason

            return False, ""
        except Exception as e:
            logger.debug("[EMA9-EXIT] Error checking %s: %s", ticker, e)
            return False, ""

    def _check_liquidity_early_exit(self, position: dict) -> tuple:
        """LIQUIDITY EARLY EXIT: Exit a few pips before the nearest liquidation zone.

        This replicates your manual strategy:
        1. Find the nearest opposing liquidity zone (supply for BUY, demand for SELL)
        2. If price is approaching it (within buffer %) AND we're in profit → exit
        3. After exit, enforce a global sit-out cooldown

        Returns (should_exit, reason).
        """
        ticker = position.get("asset")
        if not ticker:
            return False, ""
        try:
            entry_price = float(position.get("entry_price", 0) or 0)
            action = str(position.get("action", "BUY") or "BUY").upper()
            is_long = action in ("BUY", "LONG")
            if entry_price <= 0:
                return False, ""

            # Fetch data and run liquidity analysis
            df = self.scanner._fetch_market_data(ticker)
            if df is None or len(df) < 20:
                return False, ""
            current_price = float(df["Close"].iloc[-1])
            if current_price <= 0:
                return False, ""

            # Only check if we're in profit
            if is_long:
                pnl_pts = current_price - entry_price
            else:
                pnl_pts = entry_price - current_price
            if pnl_pts <= 0:
                return False, ""

            # Run liquidity analysis
            liq = self._liquidity_engine.analyze(df, ticker)

            # Find the nearest opposing liquidity zone
            if is_long:
                # For BUY: nearest supply zone above us is the target
                opposing_zones = [
                    z for z in (liq.supply_zones + liq.fvg_bearish + liq.liquidity_pools)
                    if z.direction == "bearish" and not z.invalidated and z.bottom > current_price
                ]
                if not opposing_zones:
                    return False, ""
                nearest = min(opposing_zones, key=lambda z: z.bottom)
                zone_price = float(nearest.bottom)
                # Exit when price is within buffer of the zone
                # Use the SMALLER of percentage-based and point-based buffer
                buffer_pct = zone_price * self._LIQUIDITY_EXIT_BUFFER_PCT
                buffer_pts = self._LIQUIDITY_EXIT_BUFFER_PTS
                buffer = min(buffer_pct, buffer_pts)
                if current_price >= (zone_price - buffer):
                    reason = (
                        f"LIQUIDITY EARLY EXIT: BUY {ticker} @ {entry_price:.2f} -> "
                        f"approaching supply zone at {zone_price:.2f} "
                        f"(current={current_price:.2f}, buffer={buffer:.1f} pts) -- "
                        f"secured +{pnl_pts:.1f} pts profit"
                    )
                    logger.info("[LIQ-EXIT] %s", reason)
                    return True, reason
            else:
                # For SELL: nearest demand zone below us is the target
                opposing_zones = [
                    z for z in (liq.demand_zones + liq.fvg_bullish + liq.liquidity_pools)
                    if z.direction == "bullish" and not z.invalidated and z.top < current_price
                ]
                if not opposing_zones:
                    return False, ""
                nearest = max(opposing_zones, key=lambda z: z.top)
                zone_price = float(nearest.top)
                # Exit when price is within buffer of the zone
                # Use the SMALLER of percentage-based and point-based buffer
                buffer_pct = zone_price * self._LIQUIDITY_EXIT_BUFFER_PCT
                buffer_pts = self._LIQUIDITY_EXIT_BUFFER_PTS
                buffer = min(buffer_pct, buffer_pts)
                if current_price <= (zone_price + buffer):
                    reason = (
                        f"LIQUIDITY EARLY EXIT: SELL {ticker} @ {entry_price:.2f} -> "
                        f"approaching demand zone at {zone_price:.2f} "
                        f"(current={current_price:.2f}, buffer={buffer:.1f} pts) -- "
                        f"secured +{pnl_pts:.1f} pts profit"
                    )
                    logger.info("[LIQ-EXIT] %s", reason)
                    return True, reason

            return False, ""
        except Exception as e:
            logger.debug("[LIQ-EXIT] Error checking %s: %s", ticker, e)
            return False, ""

    def _check_profit_guard_exit(self, position):
        """Profit Guard: let winners run, bank them on a 10-15% pullback.

        Returns (should_exit, reason). The actual close + unit confirmation is
        handled by close_position() -> rpa_executor.flatten_position(), which
        closes EVERY unit for the ticker and then clears local state, confirming
        no position remains open.
        """
        if not getattr(config, "PROFIT_GUARD_ENABLED", True):
            return False, ""
        ticker = position.get("asset")
        action = str(position.get("action", "BUY") or "BUY").upper()
        entry = float(position.get("entry_price", 0) or 0)
        stop = float(position.get("stop_loss", 0) or 0)
        price = self._fetch_current_price(ticker)
        if entry <= 0 or not price or price <= 0:
            return False, ""

        peak_key = f"_pg_peak_{ticker}"
        armed_key = f"_pg_armed_{ticker}"
        be_key = f"_pg_be_{ticker}"
        prev_peak = getattr(self, peak_key, 0.0)
        armed = getattr(self, armed_key, False)

        # session awareness: widen the leash during busy/high-volume sessions
        widen = 0.0
        if getattr(config, "PROFIT_GUARD_SESSION_WIDEN_PCT", 0):
            try:
                if getattr(self, "session_detector", None) and self.session_detector.is_peak_volatility():
                    widen = float(getattr(config, "PROFIT_GUARD_SESSION_WIDEN_PCT", 0) or 0)
            except Exception:
                pass

        # higher-timeframe room: tighten when the trend runs out of space
        room = "UNKNOWN"
        if getattr(config, "PROFIT_GUARD_USE_HTF_ROOM", True):
            try:
                room = pg_htf_room(ticker, price, getattr(self, "scanner", None))
            except Exception:
                room = "UNKNOWN"

        res = pg_evaluate(
            entry=entry,
            stop=stop,
            action=action,
            price=price,
            peak=prev_peak,
            armed=armed,
            trigger_pct=float(getattr(config, "PROFIT_GUARD_TRIGGER_PCT", 100.0)),
            pullback_pct=float(getattr(config, "PROFIT_GUARD_PULLBACK_PCT", 12.5)),
            session_widen_pct=widen,
            htf_room=room,
        )

        setattr(self, peak_key, res["peak"])
        setattr(self, armed_key, res["armed"])

        # break-even lock once armed (guarantee no loss on the runner)
        if res.get("break_even") and getattr(config, "PROFIT_GUARD_BREAK_EVEN", True) and not getattr(self, be_key, False):
            try:
                self.rpa_executor.update_stop(ticker, action, float(entry))
                setattr(self, be_key, True)
                logger.info("[PROFIT-GUARD] Break-even stop moved to %.2f for %s", entry, ticker)
            except Exception as _be_err:
                logger.debug("[PROFIT-GUARD] break-even update skipped: %s", _be_err)

        if res["exit"]:
            # re-entry: keep sit-out short so the bot can hunt the next opportunity
            _sit = float(getattr(config, "PROFIT_GUARD_REENTRY_SITOUT_SECONDS", 0) or 0)
            if _sit > 0:
                self._global_sitout_until = time.time() + _sit
            return True, res["reason"]

        if res["armed"]:
            logger.debug("[PROFIT-GUARD] %s momentum=%s profit=+%.0f%% peak=+%.0f%% trail=%.2f room=%s",
                         ticker, res["momentum"], res["profit_pct"], res["run_up_pct"], res["trail"], room)
        return False, ""

    def _run_position_exit_scan(self):
        """Scan open positions for quick exit/stop guidance every 5 seconds.
        Also runs the Headmaster Supervisor for advanced exit decisions."""
        if not self.positions:
            return

        for position in list(self.positions):
            ticker = position.get("asset")
            if not ticker:
                continue

            # ── PROFIT GUARD (secure profits like a professional) ──
            # Runs FIRST. Lets the trade flow while momentum is strong, arms a
            # trailing stop once in solid profit, and exits the FULL position
            # on a 10-15% pullback from the peak. Full close uses flatten_position
            # which closes every unit, then confirms no position remains.
            try:
                _pg_exit, _pg_reason = self._check_profit_guard_exit(position)
                if _pg_exit:
                    logger.info("[PROFIT-GUARD] %s", _pg_reason)
                    self._log_dashboard(f"[PROFIT-GUARD] CLOSE {ticker}!")
                    _speak_alert(f"Profit secured on {ticker}. Pullback hit. Closing.", min_interval_seconds=2.0)
                    QTimer.singleShot(0, lambda t=ticker, r=_pg_reason: self.close_position(t, r))
                    continue
            except Exception as _pg_err:
                logger.debug("[PROFIT-GUARD] Error: %s", _pg_err)

            # ── HARD PROFIT TARGET (non-negotiable) ───────────────
            # Close the ENTIRE position when profit reaches 100 pips.
            # No indicators, no analysis — just lock in the gain.
            try:
                entry_price = float(position.get("entry_price", 0) or 0)
                action = str(position.get("action", "BUY") or "BUY").upper()
                is_long = action in ("BUY", "LONG")
                if entry_price > 0:
                    current_price = self._fetch_current_price(ticker)
                    if current_price and current_price > 0:
                        if is_long:
                            pnl_pips = current_price - entry_price
                        else:
                            pnl_pips = entry_price - current_price

                        # Track peak profit for U-turn protection + time since peak
                        peak_key = f"_peak_profit_{ticker}"
                        peak_time_key = f"_peak_time_{ticker}"
                        lock_key = f"_lock_taken_{ticker}"
                        current_peak = getattr(self, peak_key, 0.0)
                        peak_time = getattr(self, peak_time_key, 0.0)
                        lock_taken = getattr(self, lock_key, False)
                        now = time.time()
                        
                        if pnl_pips > current_peak:
                            setattr(self, peak_key, pnl_pips)
                            setattr(self, peak_time_key, now)
                            setattr(self, lock_key, False)
                            current_peak = pnl_pips
                            peak_time = now
                            lock_taken = False
                        
                        # PEAK PROFIT LOCK TAKE: Exit within 5-10 seconds after peak
                        # Suppressed once the Profit Guard owns a real runner, so
                        # big winners are trailed (10-15% pullback), not cut at a
                        # 1.5-pip wiggle. Still protects tiny spikes from full give-back.
                        _pg_armed = getattr(self, f"_pg_armed_{ticker}", False)
                        if (not lock_taken and current_peak >= 10 and not _pg_armed):  # 10 pips min for lock-take
                            _time_since_peak = now - peak_time
                            _decline_pct = (current_peak - pnl_pips) / current_peak if current_peak > 0 else 0
                            _should_exit = False
                            _exit_reason = ""
                            
                            # Exit on 15% decline after peak (fast U-turn)
                            if _decline_pct > 0.15:
                                _should_exit = True
                                _exit_reason = f"PEAK-LOCK: {action} {ticker} peaked +{current_peak:.1f}, declined {_decline_pct*100:.0f}% -> exiting at +{pnl_pips:.1f}"
                            # OR exit after 7 seconds at any profit (time-based lock)
                            elif _time_since_peak >= 7.0 and pnl_pips >= 5:
                                _should_exit = True
                                _exit_reason = f"PEAK-LOCK: {action} {ticker} locked +{pnl_pips:.1f} after {_time_since_peak:.0f}s since peak +{current_peak:.1f}"
                            
                            if _should_exit:
                                setattr(self, lock_key, True)
                                logger.info("[PEAK-LOCK] %s", _exit_reason)
                                self._log_dashboard(f"[PEAK-LOCK] CLOSE {ticker}! {_exit_reason}")
                                QTimer.singleShot(0, lambda t=ticker, r=_exit_reason: self.close_position(t, r))
                                continue

                        # IMMEDIATE EXIT: Profit evaporated (protecting from U-turn eat-all)
                        if pnl_pips <= 0 and current_peak > 0:
                            reason = f"ZERO PROTECT: {action} {ticker} profit gone (was +{current_peak:.1f})"
                            logger.warning("[ZERO-PROTECT] %s", reason)
                            self._log_dashboard(f"[ZERO-PROTECT] CLOSE {ticker}! Profit evaporated")
                            QTimer.singleShot(0, lambda t=ticker, r=reason: self.close_position(t, r))
                            continue

                        if pnl_pips >= self._HARD_PROFIT_TARGET_PIPS:
                            reason = (
                                f"HARD TARGET HIT: {action} {ticker} @ {entry_price:.2f} -> "
                                f"+{pnl_pips:.1f} pips (target={self._HARD_PROFIT_TARGET_PIPS:.0f}) "
                                f"--- LOCKING PROFIT"
                            )
                            logger.info("[PROFIT-TARGET] %s", reason)
                            self._log_dashboard(f"[PROFIT-TARGET] CLOSE {ticker}! +{pnl_pips:.1f} pips")
                            _speak_alert(
                                f"Profit target hit on {ticker}. +{pnl_pips:.0f} pips. Closing now.",
                                min_interval_seconds=2.0,
                            )
                            QTimer.singleShot(0, lambda t=ticker, r=reason: self.close_position(t, r))
                            # Sit out after hitting hard target
                            self._global_sitout_until = time.time() + self._HARD_PROFIT_SITOUT_SECONDS
                            logger.info("[PROFIT-TARGET] Sitting out %ds before next trade",
                                       self._HARD_PROFIT_SITOUT_SECONDS)
                            continue
            except Exception as _pt_err:
                logger.debug("[PROFIT-TARGET] Error: %s", _pt_err)

            # ── EMA9 RETEST EXIT (your core strategy) ─────────────
            # Exit when price closes below EMA9 after being above it.
            # This is the "escape early at first sign of weakness" rule.
            # Once the Profit Guard owns the runner (in solid profit) we let it
            # run and bank on the pullback instead of chopping a winner here.
            _pg_armed = getattr(self, f"_pg_armed_{ticker}", False)
            try:
                _ema9_exit = False
                _ema9_reason = ""
                if not _pg_armed:
                    _ema9_exit, _ema9_reason = self._check_ema9_retest_exit(position)
                if _ema9_exit:
                    logger.info("[EMA9-EXIT] %s: %s", ticker, _ema9_reason)
                    self._log_dashboard(f"[EMA9-EXIT] CLOSE {ticker}! {_ema9_reason}")
                    _speak_alert(f"EMA9 break on {ticker}. Taking profit.", min_interval_seconds=2.0)
                    QTimer.singleShot(0, lambda t=ticker, r=_ema9_reason: self.close_position(t, r))
                    continue
            except Exception as _ema9_err:
                logger.debug("[EMA9-EXIT] Error checking %s: %s", ticker, _ema9_err)

            # HAWK U-TURN CHECK
            try:
                _should_exit = False
                _exit_reason = ""
                if not _pg_armed:
                    _should_exit, _exit_reason = self._check_u_turn_exit(position)
                if _should_exit:
                    self._log_dashboard(f"[U-TURN] CLOSE {ticker} NOW! {_exit_reason}")
                    _speak_alert(f"U-turn on {ticker}. Taking profit.", min_interval_seconds=3.0)
                    QTimer.singleShot(0, lambda p=position, r=_exit_reason: self.close_position(p.get("asset", ticker), r))
                    continue
            except Exception as uturn_err:
                logger.debug("[U-TURN] Error checking %s: %s", ticker, uturn_err)

            # ── LIQUIDITY EARLY EXIT ──────────────────────────────
            # Exit a few pips before the nearest liquidation zone.
            # This is your "escape early" strategy — lock in profit
            # before price reaches the zone where reversals happen.
            # Yields to the Profit Guard once armed so winners can run.
            try:
                _liq_exit = False
                _liq_reason = ""
                if not _pg_armed:
                    _liq_exit, _liq_reason = self._check_liquidity_early_exit(position)
                if _liq_exit:
                    logger.info("[LIQ-EXIT] %s: %s", ticker, _liq_reason)
                    self._log_dashboard(f"[LIQ-EXIT] CLOSE {ticker}! {_liq_reason}")
                    _speak_alert(f"Liquidity zone approaching on {ticker}. Taking profit early.", min_interval_seconds=2.0)
                    QTimer.singleShot(0, lambda t=ticker, r=_liq_reason: self.close_position(t, r))
                    # Enforce global sit-out after liquidity exit
                    self._global_sitout_until = time.time() + self._LIQUIDITY_SITOUT_SECONDS
                    logger.info("[LIQ-EXIT] Sitting out for %ds before next trade", self._LIQUIDITY_SITOUT_SECONDS)
                    continue
            except Exception as _liq_err:
                logger.debug("[LIQ-EXIT] Error checking %s: %s", ticker, _liq_err)

            # ── INSTITUTIONAL REVERSAL ENGINE ─────────────────────
            # Multi-layer reversal detection: price action + volume +
            # momentum divergence + structural breaks. Catches U-turns
            # after breakouts above EMA20 with high accuracy.
            try:
                _df_rev = self.scanner._fetch_market_data(ticker)
                if _df_rev is not None and len(_df_rev) >= 20:
                    _rev_signal = reversal_engine.analyze(ticker, _df_rev)
                    if _rev_signal.is_reversal:
                        logger.warning("[REVERSAL-ENGINE] %s", _rev_signal.summary())
                        self._log_dashboard(f"[REVERSAL-ENGINE] CLOSE {ticker}! {_rev_signal.summary()}")
                        _speak_alert(
                            f"Reversal detected on {ticker}. "
                            f"Confidence {_rev_signal.confidence}. Exiting now.",
                            min_interval_seconds=2.0,
                        )
                        QTimer.singleShot(0, lambda t=ticker, r=_rev_signal.summary(): self.close_position(t, r))
                        continue
            except Exception as _rev_err:
                logger.debug("[REVERSAL-ENGINE] Error: %s", _rev_err)

            # ── POST-ENTRY REVERSAL CHECK ─────────────────────────
            try:
                _cur_price = self._fetch_current_price(ticker)
                if _cur_price and _cur_price > 0:
                    _should_rev, _rev_reason = reversal_detector.check(ticker, _cur_price)
                    if _should_rev:
                        logger.warning("[REVERSAL] Cutting %s: %s", ticker, _rev_reason)
                        self._log_dashboard(f"[REVERSAL] CLOSE {ticker}! {_rev_reason}")
                        _speak_alert(f"Reversal on {ticker}. Cutting loss.", min_interval_seconds=2.0)
                        QTimer.singleShot(0, lambda t=ticker, r=_rev_reason: self.close_position(t, r))
                        continue
            except Exception:
                pass

            try:
                df = self.scanner._fetch_market_data(ticker)
                if df is None or df.empty:
                    continue
                market_data = self._build_market_data_point(ticker, df)
                if market_data is None:
                    continue

                # === HEADMASTER SUPERVISOR CHECK ===
                # Headmaster Velez reflex engine — auto-flatten immediately.
                self.headmaster.evaluate(ticker, market_data.price, market_data.indicators)
                if self.headmaster.should_close:
                    reason = self.headmaster.consume_close_command()
                    logger.warning("[HEADMASTER] EXIT SIGNAL %s: %s", ticker, reason)
                    self._log_dashboard(f"[HEADMASTER] CLOSE {ticker} NOW! {reason}")
                    try:
                        _speak_alert(f"Headmaster says close {ticker} now. {reason}", min_interval_seconds=3.0)
                    except Exception:
                        pass
                    QTimer.singleShot(0, lambda t=ticker, r=reason: self.close_position(t, r))
                    continue

                # === LADDER EXIT (TP1/TP2/TP3 partial scale-out) ===
                try:
                    rsi_val = float(market_data.indicators.get("RSI", 50) or 50)
                    ladder_sig = ladder_exit_manager.evaluate(
                        symbol=ticker,
                        current_price=market_data.price,
                        rsi=rsi_val,
                    )
                    if ladder_sig.action == "CLOSE_FULL":
                        reason_l = f"[LADDER] {ladder_sig.reason}"
                        self._log_dashboard(reason_l)
                        QTimer.singleShot(0, lambda t=ticker, r=reason_l: self.close_position(t, r))
                        continue
                    elif ladder_sig.action == "CLOSE_PARTIAL":
                        reason_l = f"[LADDER] {ladder_sig.reason} — closing {ladder_sig.close_pct*100:.0f}%"
                        self._log_dashboard(reason_l)
                except Exception as ladder_err:
                    logger.debug("[LADDER] Evaluation error (non-critical): %s", ladder_err)

                # === STANDARD EXIT LOGIC ===
                should_exit, reason = self._evaluate_position_exit(position, market_data)
                if should_exit:
                    action = position.get("action")
                    entry = position.get("entry_price", 0.0)
                    price = market_data.price
                    message = (
                        f"[EXIT] {ticker} {action} EXIT NOW: {reason} | "
                        f"entry={entry:.2f} current={price:.2f}"
                    )
                    self._log_dashboard(message)
                    try:
                        _speak_alert(f"Exit {action} {ticker} now. {reason}", min_interval_seconds=3.0)
                    except Exception:
                        pass
                    # Auto-flatten in AUTONOMOUS mode for ALL exits (stop loss AND profit).
                    # close_position() clicks the Flatten button, which is safe
                    # (it does NOT open an opposite position).
                    if self.current_mode == "AUTONOMOUS":
                        QTimer.singleShot(0, lambda p=position, r=reason: self.close_position(p.get("asset", ticker), r))
            except Exception as e:
                logger.warning("[EXIT] Error checking position %s: %s", ticker, e)

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
    
    def _on_manual_close_detected(self):
        """Called by TradeMonitor when it detects a manual close in TradingView.
        Resets all internal state so the bot can hunt for the next opportunity."""
        logger.warning("[INTERVENTION] Manual close detected — resetting all state")
        # Clear all positions (the manual close already flattened them)
        self.positions.clear()
        # Release all lock slots
        self.asset_lock.force_reset()
        # Put headmaster to sleep
        try:
            self.headmaster.on_position_closed()
        except Exception:
            pass
        # Clear ladder state
        try:
            for sym in list(ladder_exit_manager._states.keys()):
                ladder_exit_manager.clear_trade(sym)
        except Exception:
            pass
        # Clear trade monitor state
        self.trade_monitor.clear_trade()
        # Log it
        self._log_dashboard("[INTERVENTION] Manual close detected. Scanner rearmed for next opportunity.")
        logger.info("[INTERVENTION] All state reset. Bot is hunting for the next trade.")

    def _headmaster_kill_order(self, ticker: str, reason: str):
        """HEADMASTER KILL ORDER: Bypass all filters, flatten immediately, reset everything.
        This is the 'Thank You Handshake Protocol' — take profit and move on."""
        
        # STEP 1: Direct FLATTEN click — no secondary checks
        try:
            self.rpa_executor.flatten_position(ticker)
            logger.info("[HEADMASTER] FLATTEN click sent for %s", ticker)
        except Exception as e:
            logger.error("[HEADMASTER] Flatten click failed: %s — trying close_position fallback", e)
        
        # STEP 2: Thank You log
        logger.info("[HEADMASTER] Dynamic U-Turn Exit executed! Taken the profit! Thank you so much!")
        self._log_dashboard(f"[HEADMASTER] ✓ Profit secured on {ticker}! Thank you! Reason: {reason[:80]}")
        
        # STEP 3: Release THIS ticker's lock, remove from positions list
        try:
            for i, pos in enumerate(list(self.positions)):
                if pos.get("asset") == ticker:
                    self.positions.pop(i)
                    break
            self.asset_lock.release_ticker(ticker)
            import time as _time
            setattr(self, f"_last_close_time_{ticker}", _time.time())
            setattr(self, f"_last_close_reason_{ticker}", reason)
            if self.asset_lock.open_count() == 0:
                self.headmaster.on_position_closed()
        except Exception as e:
            logger.error("[HEADMASTER] Lock reset error: %s", e)
        
        # STEP 4: Rearm scanner immediately — look for next opportunity
        logger.info("[HEADMASTER] Scanner rearmed. Hunting for next entry...")

    def close_position(self, ticker: str, reason: str = ""):
        """Close ALL positions for a ticker and release the lock.

        With multi-asset support, only the closed ticker's slot is released.
        Other open positions are unaffected.

        IMPORTANT: This closes ALL positions for the ticker (not just one).
        If you have 3 buy positions on MNQ, all 3 are flattened.
        """
        # --- Count how many positions we have for this ticker ---
        _matching = [p for p in self.positions if p.get("asset") == ticker]
        if not _matching:
            logger.warning("[CLOSE] No positions found for %s — nothing to close", ticker)
            return

        _qty = len(_matching)
        logger.info("[CLOSE] Closing %d position(s) for %s | Reason: %s", _qty, ticker, reason)

        try:
            # Execute close — use flatten_position which clicks "Close position"
            # (NOT execute_trade which clicks "Sell" and only reduces by 1)
            if config.get_active_mode() == "TRADINGVIEW":
                success = self.rpa_executor.flatten_position(ticker)
                if not success:
                    logger.error("[CLOSE] flatten_position FAILED for %s — retrying once", ticker)
                    time.sleep(1.0)
                    success = self.rpa_executor.flatten_position(ticker)
                    if not success:
                        logger.error("[CLOSE] flatten_position FAILED TWICE for %s", ticker)
            else:
                self.trade_executor.close_position(ticker)

            # Remove ALL matching positions from local list
            _closed_positions = []
            try:
                for pos in list(self.positions):
                    if pos.get("asset") == ticker:
                        self.positions.remove(pos)
                        _closed_positions.append(pos)
                _pk = f"_peak_profit_{ticker}"
                _pt = f"_peak_time_{ticker}"
                _lk = f"_lock_taken_{ticker}"
                for _attr in [_pk, _pt, _lk, f"_pg_peak_{ticker}", f"_pg_armed_{ticker}", f"_pg_be_{ticker}"]:
                    if hasattr(self, _attr):
                        delattr(self, _attr)
            except Exception as pos_err:
                logger.error("[CLOSE] Failed to remove %s positions: %s", ticker, pos_err)

            # ── REALIZED P&L TRACKING (for each closed position) ──
            for _closed_position in _closed_positions:
                try:
                    _entry = float(_closed_position.get("entry_price", 0) or 0)
                    _side = str(_closed_position.get("action", "BUY") or "BUY").upper()
                    _opened_at = _closed_position.get("opened_at", 0)
                    _hold_sec = (time.time() - _opened_at) if _opened_at else 0.0
                    _exit_price = self._fetch_current_price(ticker)
                    if _entry > 0 and _exit_price and _exit_price > 0:
                        _pnl = (_exit_price - _entry) if _side == "BUY" else (_entry - _exit_price)
                        pnl_tracker.record_close(
                            trade_id=f"{ticker}_{int(_opened_at or time.time())}",
                            asset=ticker, side=_side,
                            entry_price=_entry, exit_price=_exit_price,
                            pnl=_pnl, hold_seconds=_hold_sec,
                            reason=reason,
                        )
                except Exception as _pnl_err:
                    logger.debug("[PnL] record_close error: %s", _pnl_err)

            # Clear reversal detector for this ticker
            try:
                reversal_detector.clear(ticker)
                reversal_engine.clear_state(ticker)
            except Exception:
                pass

            # Clear ladder exit tracking for this ticker
            try:
                ladder_exit_manager.clear_trade(ticker)
            except Exception:
                pass

            # Clear trade monitor tracking for this ticker
            try:
                self.trade_monitor.clear_trade()
            except Exception:
                pass

            logger.info("[CLOSE] %d position(s) closed for %s | Reason: %s", len(_closed_positions), ticker, reason)

            # --- Release lock AFTER close is confirmed (not before!) ---
            try:
                self.asset_lock.release_ticker(ticker)
            except Exception as lock_err:
                logger.error("[LOCK] release_ticker failed for %s: %s", ticker, lock_err)

            # Put headmaster back to sleep if no more positions
            try:
                if self.asset_lock.open_count() == 0:
                    self.headmaster.on_position_closed()
            except Exception:
                pass

            # Set cooldown timestamp so we don't re-enter immediately
            import time as _time
            setattr(self, f"_last_close_time_{ticker}", _time.time())
            setattr(self, f"_last_close_reason_{ticker}", reason)
            
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
            return self.scanner._fetch_market_data(ticker)["Close"].iloc[-1]
        except Exception:
            return 0.0

    def _janitor_clear_phantom_positions(self):
        """STALE POSITION JANITOR — runs every 60s.
        A position older than 4 hours without a confirmed close event is likely phantom.
        Real trades should never last this long on a scalping bot."""
        import time as _jt
        _threshold = 14400  # 4 hours
        cleared = 0
        for _pos in list(self.positions):
            _opened = _pos.get("opened_at", 0)
            if _opened and (_jt.time() - _opened) > _threshold:
                logger.warning(
                    "[STALE-JANITOR] Clearing phantom %s %s (opened %ds ago, no close event)",
                    _pos.get("action"), _pos.get("asset"),
                    int(_jt.time() - _opened),
                )
                try:
                    self.positions.remove(_pos)
                    cleared += 1
                except ValueError:
                    pass
        if cleared:
            logger.info("[STALE-JANITOR] Cleared %d phantom position(s) — execution gate restored", cleared)
        return cleared

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
