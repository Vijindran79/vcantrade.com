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
    
    DISABLED: The user wants to trade multiple symbols simultaneously.
    This lock was causing REJECTED_LOCK for every symbol whenever one
    trade was open. Now it always returns True (lock acquired) and
    never blocks.
    """
    
    def __init__(self):
        self._lock = threading.RLock()
        self.is_currently_holding = False
        self.active_locked_ticker = None
        self.lock_acquired_at = 0.0
        self.lock_timeout_seconds = 30  # Shortened from 300s
        
    def acquire(self, ticker: str) -> bool:
        """Always returns True — multi-asset trading enabled."""
        return True
    
    def release(self):
        """No-op — lock is disabled."""
        pass

    def force_reset(self):
        """Unconditionally clear ALL lock state back to the open gate.
        Called on every position close (SL/TP/Giveback) so the execution
        thread can never be left stuck holding a stale ticker lock."""
        try:
            with self._lock:
                self.is_currently_holding = False
                self.active_locked_ticker = None
                self.lock_acquired_at = 0.0
        except Exception:
            # Even if the RLock misbehaves, force the attributes clear.
            self.is_currently_holding = False
            self.active_locked_ticker = None
            self.lock_acquired_at = 0.0
    
    def is_locked_for(self, ticker: str) -> bool:
        """Always returns False — never blocks any ticker."""
        return False
    
    def check_timeout(self) -> bool:
        """Always returns False — no timeout needed."""
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
        self.trade_monitor = TradeMonitor()
        self.scanner = Scanner()
        self.headmaster = HeadmasterSupervisor()
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
            if self.current_mode == "AUTONOMOUS" and action in {"BUY", "SELL"}:
                logger.info("[AUTO] Dispatching autonomous execution for %s %s", action, ticker)
                QTimer.singleShot(0, lambda: self.process_validated_execution_path(payload))
        except Exception:
            pass

    def process_validated_execution_path(self, payload: dict):
        """Route validated bridge/swarm signals into teacher or autonomous execution."""
        ticker = str(payload.get("ticker") or payload.get("asset") or "UNKNOWN").strip().upper()
        action = str(payload.get("action") or payload.get("signal") or "WAIT").strip().upper()
        reason = str(payload.get("reason") or payload.get("reasoning") or "Bridge signal").strip()
        if action not in {"BUY", "SELL"} or not ticker or ticker == "UNKNOWN":
            logger.warning("[AUTO] Ignored non-executable signal: %s %s", action, ticker)
            self._log_dashboard(f"[BRIDGE] Ignored non-executable signal: {action} {ticker}")
            return

        # === DUPLICATE POSITION GUARD ===
        # Don't open another trade if we already have a position open on this ticker.
        # This prevents the 200+ duplicate trades per session problem.
        existing_positions = [p for p in self.positions if p.get("asset") == ticker]
        if existing_positions:
            logger.info("[GUARD] Already in position on %s (%d open) — skipping %s signal",
                       ticker, len(existing_positions), action)
            return

        # === COOLDOWN GUARD ===
        # Don't re-enter within 120 seconds of closing a position on the same ticker.
        # Gives the market time to settle after exit before re-entering.
        cooldown_key = f"_last_close_time_{ticker}"
        import time as _time
        last_close = getattr(self, cooldown_key, 0)
        if (_time.time() - last_close) < 120:
            logger.info("[GUARD] Cooldown active for %s — %d seconds since last close",
                       ticker, int(_time.time() - last_close))
            return

        # === CONFIDENCE FILTER ===
        # Only take trades with confidence >= 0.50. The brain gates
        # weak signals; the SOFT scanner tier produces ~0.55-0.65 confidence
        # signals when the trend is clear, so we don't want to filter them out
        # at the dispatcher too.
        confidence = float(payload.get("confidence") or payload.get("confidence_score") or 0.0)
        if confidence < 0.50:
            logger.info("[GUARD] Confidence too low for %s: %.1f%% (need 50%%) — skipping",
                       ticker, confidence * 100)
            return

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
                ticker_clean = ticker.replace("1!", "").replace("=F", "")
                if ticker_clean not in chart_title and ticker not in chart_title:
                    logger.warning("[GUARD] Chart shows different symbol (title: %s) — BLOCKED %s %s",
                                  tv_windows[0].title[:50], action, ticker)
                    return
        except Exception:
            # If we can't check, BLOCK for safety on prop firm
            logger.warning("[GUARD] Cannot verify chart symbol — BLOCKED %s %s for safety", action, ticker)
            return

        entry = float(payload.get("entry_price") or payload.get("price") or self._fetch_current_price(ticker) or 0.0)
        stop_loss = float(payload.get("stop_loss") or payload.get("sl") or 0.0)
        take_profit = float(payload.get("take_profit") or payload.get("tp") or 0.0)
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
    
    def _start_scanner_timer(self):
        """Start periodic scanner using QTimer."""
        self.scanner_timer = QTimer()
        self.scanner_timer.timeout.connect(self.execute_market_scan_sequence)
        interval_ms = int(float(config.SCAN_INTERVAL) * 1000)
        self.scanner_timer.start(interval_ms)
        self._scanner_timer = self.scanner_timer  # Backward-compatible alias.
        
        # FAST EXIT MONITOR: Check open positions every 3 seconds for instant profit-taking
        self._exit_timer = QTimer()
        self._exit_timer.timeout.connect(self._run_position_exit_scan)
        self._exit_timer.start(3000)  # 3 seconds
        
        logger.info("[RESTORE] Market scanning matrix loop aggressively restarted.")
        logger.info("[EXITS] Fast exit monitor armed: checking every 5 seconds")
        try:
            self.ai_narrator.notify_scan_start(len(self.current_watchlist))
        except Exception:
            pass
        self._log_dashboard(f"[SCAN] Scanner armed: {len(self.current_watchlist)} markets, {config.SCAN_INTERVAL:.0f}s structural cycle")

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
    
    def execute_trade(self, ticker: str, action: str, entry: float, sl: float, tp: float) -> TradeResult:
        """Execute a trade with single-asset lock enforcement.
        RULE: Only 1 position allowed at a time. Never stack."""
        
        # SAFETY: If positions list somehow has stale entries, don't block new trades forever.
        # Each position should be auto-cleaned by close_position(). If a position has been
        # "open" for more than 30 minutes without an exit, it's likely stale/phantom.
        import time as _time
        for pos in list(self.positions):
            opened_at = pos.get("opened_at", 0)
            if opened_at and (_time.time() - opened_at) > 1800:  # 30 min stale check
                logger.warning("[STALE] Removing stale phantom position: %s (opened %ds ago)",
                              pos.get("asset"), int(_time.time() - opened_at))
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
        """INTELLIGENT EXIT: Trailing stop + reversal detection + liquidity targets.
        Does NOT auto-click flatten (user closes on TradingView).
        Instead logs the EXIT signal clearly so user can act immediately."""
        action = str(position.get("action", "")).upper()
        entry = float(position.get("entry_price", market_data.price) or market_data.price)
        price = float(market_data.price or 0.0)
        if price <= 0:
            return False, ""

        rsi = float(market_data.indicators.get("RSI", 50.0) or 50.0)
        atr = float(market_data.indicators.get("ATR", 0.0) or 0.0)
        ema9 = float(market_data.indicators.get("EMA9", 0.0) or 0.0)
        ema21 = float(market_data.indicators.get("EMA21", 0.0) or 0.0)
        macd_hist = float(market_data.indicators.get("MACD_HIST", 0.0) or 0.0)
        macd_hist_prev = float(market_data.indicators.get("MACD_HIST_PREV", 0.0) or 0.0)
        pnl_points = price - entry if action == "BUY" else entry - price
        pnl_atr = pnl_points / max(atr, 1e-9)

        # --- STOP LOSS: 2x ATR (hard protection) ---
        if pnl_atr <= -2.0:
            return True, f"STOP LOSS: {pnl_atr:.1f} ATR ({pnl_points:.0f} pts)"

        # --- Track peak profit ---
        max_pnl_key = f"_max_pnl_{position.get('asset', '')}_{entry}"
        prev_max_pts = getattr(self, max_pnl_key, 0.0)
        if pnl_points > prev_max_pts:
            setattr(self, max_pnl_key, pnl_points)
            prev_max_pts = pnl_points
        prev_max_atr = prev_max_pts / max(atr, 1e-9)

        # --- TRAILING STOP: Once up 1 ATR, keep at least 50% of peak ---
        if prev_max_atr >= 1.0:
            trail_floor = prev_max_atr * 0.50
            if pnl_atr < trail_floor:
                return True, f"TRAILING STOP: peak +{prev_max_atr:.1f} ATR, now +{pnl_atr:.1f} ATR — take profit NOW"

        # --- TARGET: 2.5x ATR (nearest typical liquidity zone) ---
        if pnl_atr >= 2.5:
            return True, f"TARGET REACHED: +{pnl_atr:.1f} ATR ({pnl_points:.0f} pts) — liquidity zone hit"

        # --- REVERSAL CANDLE: color flip at 0.5+ ATR profit ---
        candle_open = float(market_data.indicators.get("CANDLE_OPEN", 0.0) or 0.0)
        candle_close = float(market_data.indicators.get("CANDLE_CLOSE", price) or price)
        prev_candle_open = float(market_data.indicators.get("PREV_CANDLE_OPEN", 0.0) or 0.0)
        prev_candle_close = float(market_data.indicators.get("PREV_CANDLE_CLOSE", 0.0) or 0.0)
        if candle_open > 0 and prev_candle_open > 0 and pnl_atr >= 0.5:
            current_red = candle_close < candle_open
            prev_green = prev_candle_close > prev_candle_open
            current_green = candle_close > candle_open
            prev_red = prev_candle_close < prev_candle_open
            if action == "BUY" and current_red and prev_green:
                return True, f"REVERSAL CANDLE at +{pnl_atr:.1f} ATR — close NOW"
            if action == "SELL" and current_green and prev_red:
                return True, f"REVERSAL CANDLE at +{pnl_atr:.1f} ATR — close NOW"

        # --- MOMENTUM FLIP at 0.8+ ATR profit ---
        if pnl_atr >= 0.8:
            if action == "BUY" and macd_hist < 0 <= macd_hist_prev:
                return True, f"MACD FLIPPED at +{pnl_atr:.1f} ATR — momentum dying"
            if action == "SELL" and macd_hist > 0 >= macd_hist_prev:
                return True, f"MACD FLIPPED at +{pnl_atr:.1f} ATR — momentum dying"

        # --- RSI EXTREME with profit ---
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
                if pullback_pct > 0.40 and profit > 0:
                    return True, f"U-TURN: Peak {current_peak:.2f}, now {profit:.2f} ({pullback_pct*100:.0f}% pullback)"
                if profit < 0 and current_peak > 0:
                    return True, f"U-TURN: Was +{current_peak:.2f}, now {profit:.2f} - protect capital"
            return False, ""
        except Exception as e:
            logger.debug("[U-TURN] Error checking %s: %s", ticker, e)
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

            # HAWK U-TURN CHECK
            try:
                _should_exit, _exit_reason = self._check_u_turn_exit(position)
                if _should_exit:
                    self._log_dashboard(f"[U-TURN] CLOSE {ticker} NOW! {_exit_reason}")
                    _speak_alert(f"U-turn on {ticker}. Taking profit.", min_interval_seconds=3.0)
                    QTimer.singleShot(0, lambda p=position, r=_exit_reason: self.close_position(p.get("asset", ticker), r))
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
                # Headmaster monitors but does NOT auto-flatten on paper trading
                # (clicking opposite direction opens new positions instead of closing)
                # It ALERTS the user to close manually.
                self.headmaster.evaluate(ticker, market_data.price, market_data.indicators)
                if self.headmaster.should_close:
                    reason = self.headmaster.consume_close_command()
                    logger.warning("[HEADMASTER] EXIT SIGNAL %s: %s", ticker, reason)
                    self._log_dashboard(f"[HEADMASTER] CLOSE {ticker} NOW! {reason}")
                    try:
                        _speak_alert(f"Headmaster says close {ticker} now. {reason}", min_interval_seconds=3.0)
                    except Exception:
                        pass
                    continue

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
                    # Only auto-flatten for STOP LOSS (losing money).
                    # For profit exits, ALERT the user — they close manually.
                    if self.current_mode == "AUTONOMOUS" and "STOP LOSS" in reason:
                        QTimer.singleShot(0, lambda p=position, r=reason: self.close_position(p.get("asset", ticker), r))
                    elif self.current_mode == "AUTONOMOUS":
                        # Profit exit — just log loudly, user closes manually
                        logger.info("[TAKE PROFIT SIGNAL] %s %s — CLOSE NOW! Reason: %s", action, ticker, reason)
                        self._log_dashboard(f"[TAKE PROFIT] CLOSE {ticker} NOW! {reason}")
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
        
        # STEP 3: Force reset ALL locks unconditionally
        try:
            # Remove from positions list
            for i, pos in enumerate(list(self.positions)):
                if pos.get("asset") == ticker:
                    self.positions.pop(i)
                    break
            # Force reset asset lock
            self.asset_lock.force_reset()
            # Set cooldown
            import time as _time
            setattr(self, f"_last_close_time_{ticker}", _time.time())
            # Put headmaster to sleep
            self.headmaster.on_position_closed()
        except Exception as e:
            logger.error("[HEADMASTER] Lock reset error: %s", e)
        
        # STEP 4: Rearm scanner immediately — look for next opportunity
        logger.info("[HEADMASTER] Scanner rearmed. Hunting for next entry...")

    def close_position(self, ticker: str, reason: str = ""):
        """Close a position and release the asset lock.

        TASK 1 — GLOBAL LOCK LEAK FIX: the execution gate is reset
        UNCONDITIONALLY the instant a position closes (Stop Loss, Take
        Profit, or Profit Giveback Shield). This happens BEFORE the broker
        flatten call so that even if the flatten throws, the gate is already
        open and ESM6/MCL1!/MGC1! can immediately claim the execution thread.
        """
        # --- UNCONDITIONAL GATE RESET (must happen first, no matter what) ---
        try:
            self.asset_lock.force_reset()
            logger.info("[LOCK] Execution gate force-reset to None on close of %s", ticker)
        except Exception as lock_err:
            logger.error("[LOCK] force_reset failed for %s: %s", ticker, lock_err)
        # Also clear any per-ticker churn lock dict if present
        try:
            if hasattr(self, "locked_tickers") and isinstance(self.locked_tickers, dict):
                self.locked_tickers.pop(ticker, None)
        except Exception:
            pass

        try:
            # Execute close
            if config.get_active_mode() == "TRADINGVIEW":
                self.rpa_executor.flatten_position(ticker)
            else:
                self.trade_executor.close_position(ticker)
            
            # Remove from local position list once close request has been issued.
            try:
                for i, position in enumerate(list(self.positions)):
                    if position.get("asset") == ticker:
                        self.positions.pop(i)
                        _pk = f"_peak_profit_{ticker}"
                        if hasattr(self, _pk):
                            delattr(self, _pk)
                        break
            except Exception:
                pass
            
            # Lock already force-reset above — this is a belt-and-suspenders release.
            try:
                self.asset_lock.release()
            except Exception:
                pass
            
            logger.info("[CLOSE] Position closed: %s | Reason: %s", ticker, reason)
            
            # Put headmaster back to sleep
            try:
                self.headmaster.on_position_closed()
            except Exception:
                pass
            
            # Set cooldown timestamp so we don't re-enter immediately
            import time as _time
            setattr(self, f"_last_close_time_{ticker}", _time.time())
            
        except Exception as e:
            logger.error("[CLOSE] Error closing position %s: %s", ticker, e)
            # Gate is already open from force_reset above; nothing to recover.
    
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
