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
from PyQt6.QtCore import QObject, pyqtSignal

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
# Import UI components after QApplication is available
from ui.dashboard import CommandCenter as Dashboard
from ui.ai_narrator import AINarratorOverlay as AINarrator
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
        
        # UI components
        self.dashboard = Dashboard()
        self.ai_narrator = AINarrator()
        self.lion_switchboard = LionSwitchboard()
        
        # Signal dispatcher
        self.signal_dispatcher = SignalDispatcher()
        
        # Threads
        self.cloud_scanner = CloudScannerThread()
        self.signal_listener = SignalListenerThread()
        self.data_scout_listener = DataScoutListenerThread()
        
        # State
        self.current_mode = "AUTONOMOUS" if not config.TEACHER_MODE else "TEACHER"
        self.is_running = False
        self.current_watchlist = self._normalize_watchlist(config.ACTIVE_WATCHLIST)
        
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
        self.scanner.start()
        self.trade_monitor.start()
        
        if self.cloud_scanner:
            self.cloud_scanner.start()
        if self.signal_listener:
            self.signal_listener.start()
        if self.data_scout_listener:
            self.data_scout_listener.start()
        
        logger.info("[ENGINE] VcaniTrade Engine STARTED")
    
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

# =========================================================================
# MAIN ENTRY POINT
# =========================================================================

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
        
        # STEP 3: START THE ENGINE (starts threads, monitoring, etc.)
        engine.start()
        
        # STEP 4: RELEASE CONTROL TO THE PYQT GRAPHICAL EVENT LOOP
        # This blocks until the application exits (e.g., user closes the window)
        exit_code = app.exec()
        
        # STEP 5: CLEANUP WHEN EVENT LOOP ENDS
        logger.info("[SHUTDOWN] Event loop ended, stopping engine...")
        engine.stop()
        
        sys.exit(exit_code)
        
    except Exception as e:
        logger.critical(f"[BOOT-CRASH] Critical failure during unified system startup loop: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
