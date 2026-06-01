"""
VcaniTrade AI - Trade Engine
Manages trade execution with strict safety controls
"""

import logging
import sqlite3
import json
from datetime import datetime, timedelta
from typing import List, Optional

import config
from core.models import (
    LLMAnalysisOutput,
    MarketDataPoint,
    TradeRecord,
    SafetyState,
    SignalAction,
    ConfidenceLevel,
)
from execution.rpa_executor import RPAExecutor
from core.profit_lock import ProfitLock, WalkAwayProtocol
from core.atr_stops import LooseATRStops
from core.institutional_suite import suite

logger = logging.getLogger(__name__)


class TradeEngine:
    """Core trading engine with safety controls and trade ledger"""

    def __init__(self):
        self.safety_state = SafetyState()
        self.open_trades: List[TradeRecord] = []
        self.trade_history: List[TradeRecord] = []
        self.cooldown_until: Optional[datetime] = None
        
        # HAWK PROTOCOL: Single-Asset Lock
        self.target_lock_active = False
        self.locked_asset = None
        self.hawk_mode = True  # Enable single-asset focus
        self.scanner_thread = None  # Will be set by external scanner if exists
        
        # Initialize RPA executor with error handling
        self.rpa_executor = None
        try:
            self.rpa_executor = RPAExecutor()
            logger.info("[RPA] Executor initialized successfully")
        except Exception as e:
            logger.warning("[RPA] Executor initialization failed: %s - Running in teacher mode", e)
            self.rpa_executor = None
        
        self.last_indicators: dict = {}  # Populated by swarm before process_signal

        # Active trade management: ProfitLock + ATR stops
        self.profit_lock = ProfitLock()
        self.walk_away = WalkAwayProtocol()
        self.atr_stops = LooseATRStops()

        # Initialize SQLite ledger
        self._init_ledger()
        # Price cache for HAWK protocol
        self._last_prices = {}

    def _init_ledger(self):
        """Initialize SQLite trade ledger"""
        try:
            self.conn = sqlite3.connect("vcanitrade_ledger.db", check_same_thread=False)
            cursor = self.conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT,
                    asset TEXT,
                    action TEXT,
                    entry_price REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    exit_price REAL,
                    pnl REAL,
                    confidence TEXT,
                    ai_reason TEXT,
                    mode TEXT,
                    status TEXT,
                    closed_at TEXT
                )
                """
            )
            self.conn.commit()
            logger.info("Trade ledger initialized")
        except sqlite3.Error as e:
            logger.error("Failed to initialize trade ledger: %s", e)
            raise

    def process_signal(
        self, signal: LLMAnalysisOutput, mode: str = "TEACHER"
    ) -> Optional[TradeRecord]:
        """
        Process trading signal from LLM analyzer
        Returns TradeRecord if trade executed, None otherwise
        """
        # Check safety controls
        if not self._check_safety():
            logger.warning("Trade blocked by safety controls: %s", signal.asset)
            return None

        # Teacher mode: log signal but don't execute
        if mode == "TEACHER" or config.DRY_RUN:
            logger.info(
                "[TEACHER MODE] Signal: %s %s - %s",
                signal.action, signal.asset, signal.reason
            )
            return self._create_signal_record(signal)

        # TREND FILTER: Block trades against the trend in autonomous mode
        indicators = self.last_indicators
        if mode == "AUTO" and indicators:
            trend = str(indicators.get("TREND_DIRECTION", "N/A")).upper()
            mtf_alignment = str(indicators.get("MTF_ALIGNMENT", "N/A")).upper()
            if signal.action == SignalAction.BUY and trend == "BEARISH":
                logger.warning(
                    "[TREND FILTER] BLOCKED BUY %s — trend is BEARISH. Signal: %s",
                    signal.asset, signal.reason
                )
                return None
            if signal.action == SignalAction.SELL and trend == "BULLISH":
                logger.warning(
                    "[TREND FILTER] BLOCKED SELL %s — trend is BULLISH. Signal: %s",
                    signal.asset, signal.reason
                )
                return None
            if mtf_alignment == "AGAINST":
                logger.warning(
                    "[TREND FILTER] BLOCKED %s %s — MTF alignment is AGAINST trend.",
                    signal.action.value, signal.asset
                )
                return None

        # INSTITUTIONAL SUITE: Regime filter + drawdown/daily-loss gates
        prices_for_regime = []
        if hasattr(self, 'last_indicators') and isinstance(self.last_indicators, dict):
            cp = self.last_indicators.get("current_price")
            if cp:
                prices_for_regime = [cp]
        sig_dict = {
            "action": signal.action.value,
            "confidence": float(getattr(signal.confidence, "value", 0.5) or 0.5)
                if not isinstance(getattr(signal.confidence, "value", 0.5), (int, float))
                else getattr(signal.confidence, "value", 0.5),
        }
        inst_check = suite.on_signal(sig_dict, prices_for_regime)
        if not inst_check.get("allow"):
            logger.warning("[INST-FILTER] BLOCKED %s — %s", signal.asset, inst_check.get("reason"))
            return None

        # Auto mode: execute trade
        try:
            if signal.action == SignalAction.BUY:
                return self._execute_buy(signal)
            elif signal.action == SignalAction.SELL:
                return self._execute_sell(signal)
            elif signal.action == SignalAction.CLOSE:
                return self._execute_close(signal)
            else:
                logger.info("HOLD signal for %s", signal.asset)
                return None

        except Exception as e:
            logger.error("Trade execution failed: %s", e)
            return None

    def _check_safety(self) -> bool:
        """Verify all safety controls before trading"""
        import config

        # Kill switch
        if config.KILL_SWITCH or self.safety_state.kill_switch_active:
            logger.warning("KILL SWITCH ACTIVE - Trading halted")
            return False

        # Daily loss limit (0 = disabled, e.g. Apex Trader Funding has no daily limit)
        if config.MAX_DAILY_LOSS > 0 and abs(self.safety_state.daily_pnl) >= config.MAX_DAILY_LOSS:
            logger.warning(
                "Daily loss limit reached: $%.2f",
                self.safety_state.daily_pnl
            )
            self.safety_state.daily_loss_limit_hit = True
            return False

        # Cooldown period
        if self.cooldown_until and datetime.utcnow() < self.cooldown_until:
            remaining = (self.cooldown_until - datetime.utcnow()).seconds
            self.safety_state.cooldown_remaining_seconds = remaining
            logger.warning("Cooldown active: %ds remaining", remaining)
            return False
        else:
            self.safety_state.cooldown_remaining_seconds = 0
            self.cooldown_until = None

        # Max positions
        if len(self.open_trades) >= config.MAX_OPEN_POSITIONS:
            logger.warning(
                "Max positions reached: %d/%d",
                len(self.open_trades), config.MAX_OPEN_POSITIONS
            )
            return False

        self.safety_state.open_positions = len(self.open_trades)
        self.safety_state.update_trade_ability()
        return self.safety_state.can_trade

    def _get_confidence_risk_pct(self, confidence: ConfidenceLevel) -> float:
        """Scale risk per trade based on signal confidence. Elite traders size up on A+ setups."""
        risk_map = {
            ConfidenceLevel.VERY_HIGH: 2.0,
            ConfidenceLevel.HIGH: 1.5,
            ConfidenceLevel.MEDIUM: 1.0,
            ConfidenceLevel.LOW: 0.5,
        }
        return risk_map.get(confidence, 1.0)

    def manage_open_trades(self, current_prices: dict) -> list:
        """
        Active trade management loop — checks all open trades for:
        - Walk Away Protocol (daily loss breach -> 24h shutdown)
        - Break-even moves (ProfitLock)
        - Time-based exits (max 30min hold)
        - Stop/TP hit detection

        Call this on every tick/scan cycle. Returns list of closed trade IDs.
        """
        # Store current prices for HAWK protocol get_current_price()
        self._last_prices.update(current_prices)

        # INSTITUTIONAL SUITE: Update equity curve + check drawdown/target alerts
        try:
            current_eq = float(config.CURRENT_BALANCE) + float(self.safety_state.daily_pnl)
            suite.on_tick(current_eq)
        except Exception as _e:
            logger.debug("[INST] on_tick skipped: %s", _e)

        closed_ids = []
        now = datetime.utcnow()

        # Walk Away Protocol: check daily P&L
        if config.CURRENT_BALANCE > 0:
            daily_pnl_pct = (self.safety_state.daily_pnl / config.CURRENT_BALANCE) * 100
            if self.walk_away.check_violation(daily_pnl_pct):
                logger.critical(
                    "[WALK AWAY] Daily loss %.2f%% exceeds threshold. Shutting down for 24h.",
                    daily_pnl_pct
                )
                for trade in list(self.open_trades):
                    self._close_trade_at_price(trade, current_prices.get(trade.asset, trade.entry_price), "WALK_AWAY")
                    closed_ids.append(trade.id)
                return closed_ids

        # HAWK: Update trailing stops for locked asset
        if self.target_lock_active and self.locked_asset:
            self.update_trailing_stops()

        # Manage each open trade
        for trade in list(self.open_trades):
            current_price = current_prices.get(trade.asset)
            if current_price is None:
                continue

            is_long = trade.action == SignalAction.BUY

            # Optional time-based exit
            max_hold = int(getattr(config, "MAX_TRADE_HOLD_SECONDS", 0) or 0)
            if max_hold > 0 and trade.timestamp:
                hold_seconds = (now - trade.timestamp).total_seconds()
                if hold_seconds > max_hold:
                    logger.info(
                        "[TIME EXIT] %s held for %.0fs (>%ds). Closing.",
                        trade.asset, hold_seconds, max_hold
                    )
                    self._close_trade_at_price(trade, current_price, "TIME_EXIT")
                    closed_ids.append(trade.id)
                    continue

            # Build position dict for ProfitLock API
            position = {
                "asset": trade.asset,
                "entry": trade.entry_price,
                "entry_price": trade.entry_price,
                "sl_price": trade.stop_loss or 0,
                "current_stop": trade.stop_loss or 0,
                "side": "BUY" if is_long else "SELL",
                "quantity": 1,
            }

            # Break-even check via ProfitLock
            break_even_result = self.profit_lock.check_break_even(position, current_price)
            if break_even_result:
                new_stop = float(break_even_result.get("new_stop", 0) or 0)
                if new_stop > 0 and new_stop != trade.stop_loss:
                    logger.info(
                        "[BREAK EVEN] %s stop moved to break-even: %.2f -> %.2f",
                        trade.asset, trade.stop_loss, new_stop
                    )
                    trade.stop_loss = new_stop
                    self.profit_lock.update_position_stop(
                        trade.asset, new_stop, reason="break_even",
                        break_even_locked=True,
                    )

            # Check if stop was hit
            if trade.stop_loss and trade.stop_loss > 0:
                if is_long and current_price <= trade.stop_loss:
                    logger.info("[STOP HIT] %s price %.2f <= stop %.2f", trade.asset, current_price, trade.stop_loss)
                    self._close_trade_at_price(trade, current_price, "STOP_HIT")
                    closed_ids.append(trade.id)
                elif not is_long and current_price >= trade.stop_loss:
                    logger.info("[STOP HIT] %s price %.2f >= stop %.2f", trade.asset, current_price, trade.stop_loss)
                    self._close_trade_at_price(trade, current_price, "STOP_HIT")
                    closed_ids.append(trade.id)

            # Check if take profit was hit
            if trade.take_profit and trade.take_profit > 0:
                if is_long and current_price >= trade.take_profit:
                    logger.info("[TP HIT] %s price %.2f >= TP %.2f", trade.asset, current_price, trade.take_profit)
                    self._close_trade_at_price(trade, current_price, "TP_HIT")
                    closed_ids.append(trade.id)
                elif not is_long and current_price <= trade.take_profit:
                    logger.info("[TP HIT] %s price %.2f <= TP %.2f", trade.asset, current_price, trade.take_profit)
                    self._close_trade_at_price(trade, current_price, "TP_HIT")
                    closed_ids.append(trade.id)

        return closed_ids

    def _close_trade_at_price(self, trade: TradeRecord, price: float, reason: str):
        """Close a trade at a specific price with reason."""
        trade.exit_price = price
        trade.closed_at = datetime.utcnow()
        trade.status = "CLOSED_" + reason
        trade.pnl = (
            (trade.exit_price - trade.entry_price)
            if trade.action == SignalAction.BUY
            else (trade.entry_price - trade.exit_price)
        )
        self.safety_state.daily_pnl += trade.pnl or 0
        self.open_trades.remove(trade)
        self.trade_history.append(trade)
        self._update_trade(trade)
        logger.info(
            "[CLOSE] %s %s @ %.2f -> %.2f | PnL: %.2f | Reason: %s",
            trade.action.value, trade.asset, trade.entry_price,
            trade.exit_price, trade.pnl or 0, reason
        )

        # INSTITUTIONAL SUITE: Record closed trade for Sharpe/Sortino/Drawdown
        try:
            hold_sec = 0.0
            if trade.timestamp and trade.closed_at:
                try:
                    t_open = datetime.fromisoformat(str(trade.timestamp))
                    t_close = datetime.fromisoformat(str(trade.closed_at))
                    hold_sec = (t_close - t_open).total_seconds()
                except Exception:
                    pass
            size = int(getattr(trade, "quantity", 1) or 1)
            suite.on_trade_close(
                symbol=trade.asset, side=trade.action.value,
                entry=float(trade.entry_price or 0),
                exit=float(trade.exit_price or 0),
                size=size, pnl=float(trade.pnl or 0),
                hold_sec=hold_sec,
            )
        except Exception as _e:
            logger.debug("[INST] on_trade_close skipped: %s", _e)
        
        # HAWK: Resume scanners after position close
        if self.target_lock_active and trade.asset == self.locked_asset:
            self.resume_scanners()

    def _execute_buy(self, signal: LLMAnalysisOutput) -> TradeRecord:
        """Execute buy trade with confidence-based position sizing"""
        # HAWK LOCK ENGAGEMENT
        if self.target_lock_active:
            logger.warning("[HAWK] LOCK ACTIVE on %s. Ignoring entry for %s.", self.locked_asset, signal.asset)
            return None
        
        risk_pct = self._get_confidence_risk_pct(signal.confidence)
        logger.info("[SIZING] %s confidence -> %.1f%% risk per trade", signal.confidence.value, risk_pct)
        trade = TradeRecord(
            asset=signal.asset,
            action=SignalAction.BUY,
            entry_price=signal.entry_price or 0,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            confidence=signal.confidence,
            ai_reason=signal.reason,
            mode="AUTO",
            status="OPEN",
        )

        # Save to ledger
        self._save_trade(trade)
        self.open_trades.append(trade)

        # Execute via RPA if not dry run
        if not config.DRY_RUN:
            success = self.rpa_executor.execute_trade(trade)
            if not success:
                logger.error("RPA execution failed for BUY %s", trade.asset)
            # INSTITUTIONAL SUITE: Record execution for TCA
            try:
                fill_price = float(signal.entry_price or trade.entry_price or 0)
                suite.on_execution(
                    symbol=trade.asset, side="BUY",
                    intended_price=fill_price, fill_price=fill_price,
                    intended_size=1, filled_size=1 if success else 0,
                    latency_ms=0.0,
                )
            except Exception as _e:
                logger.debug("[INST] on_execution skipped: %s", _e)

        # HAWK LOCK ACTIVATION
        self.target_lock_active = True
        self.locked_asset = signal.asset
        logger.info("[HAWK] TARGET LOCKED: %s. Scanners suspended.", signal.asset)
        self.suspend_scanners()

        logger.info(
            "EXECUTED BUY: %s @ %.2f | SL: %.2f | TP: %.2f",
            trade.asset, trade.entry_price, trade.stop_loss, trade.take_profit
        )
        return trade

    def _execute_sell(self, signal: LLMAnalysisOutput) -> TradeRecord:
        """Execute sell trade with confidence-based position sizing"""
        # HAWK LOCK ENGAGEMENT
        if self.target_lock_active:
            logger.warning("[HAWK] LOCK ACTIVE on %s. Ignoring entry for %s.", self.locked_asset, signal.asset)
            return None
        
        risk_pct = self._get_confidence_risk_pct(signal.confidence)
        logger.info("[SIZING] %s confidence -> %.1f%% risk per trade", signal.confidence.value, risk_pct)
        trade = TradeRecord(
            asset=signal.asset,
            action=SignalAction.SELL,
            entry_price=signal.entry_price or 0,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            confidence=signal.confidence,
            ai_reason=signal.reason,
            mode="AUTO",
            status="OPEN",
        )

        self._save_trade(trade)
        self.open_trades.append(trade)

        # Execute via RPA if not dry run
        if not config.DRY_RUN:
            success = self.rpa_executor.execute_trade(trade)
            if not success:
                logger.error("RPA execution failed for SELL %s", trade.asset)
            # INSTITUTIONAL SUITE: Record execution for TCA
            try:
                fill_price = float(signal.entry_price or trade.entry_price or 0)
                suite.on_execution(
                    symbol=trade.asset, side="SELL",
                    intended_price=fill_price, fill_price=fill_price,
                    intended_size=1, filled_size=1 if success else 0,
                    latency_ms=0.0,
                )
            except Exception as _e:
                logger.debug("[INST] on_execution skipped: %s", _e)

        # HAWK LOCK ACTIVATION
        self.target_lock_active = True
        self.locked_asset = signal.asset
        logger.info("[HAWK] TARGET LOCKED: %s. Scanners suspended.", signal.asset)
        self.suspend_scanners()

        logger.info(
            "EXECUTED SELL: %s @ %.2f | SL: %.2f | TP: %.2f",
            trade.asset, trade.entry_price, trade.stop_loss, trade.take_profit
        )
        return trade

    def _execute_close(self, signal: LLMAnalysisOutput) -> Optional[TradeRecord]:
        """Close existing position"""
        # Find matching open trade
        for i, trade in enumerate(self.open_trades):
            if trade.asset == signal.asset:
                # Close the trade
                trade.status = "CLOSED"
                trade.closed_at = datetime.utcnow()
                # Mock exit price (would come from market data)
                trade.exit_price = signal.entry_price
                trade.pnl = (
                    (trade.exit_price - trade.entry_price)
                    if trade.action == SignalAction.BUY
                    else (trade.entry_price - trade.exit_price)
                )

                # Update daily PnL
                self.safety_state.daily_pnl += trade.pnl or 0

                # Remove from open trades
                self.open_trades.pop(i)
                self.trade_history.append(trade)

                # Update ledger
                self._update_trade(trade)

                logger.info("CLOSED: %s | PnL: $%.2f", trade.asset, trade.pnl or 0)

                # Check if hit stop loss - start cooldown
                if trade.pnl and trade.pnl < 0 and trade.stop_loss:
                    self.cooldown_until = datetime.utcnow() + timedelta(
                        seconds=config.COOLDOWN_AFTER_STOP
                    )
                    logger.warning(
                        "Stop loss hit - cooldown for %ds",
                        config.COOLDOWN_AFTER_STOP
                    )

                return trade

        logger.warning("No open position found for %s", signal.asset)
        return None

    def _create_signal_record(self, signal: LLMAnalysisOutput) -> TradeRecord:
        """Create record for teacher mode (no execution)"""
        return TradeRecord(
            asset=signal.asset,
            action=signal.action,
            entry_price=signal.entry_price or 0,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            confidence=signal.confidence,
            ai_reason=signal.reason,
            mode="TEACHER",
            status="OPEN",
        )

    def _save_trade(self, trade: TradeRecord):
        """Save trade to SQLite ledger"""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO trades (id, timestamp, asset, action, entry_price, stop_loss, take_profit, 
                               exit_price, pnl, confidence, ai_reason, mode, status, closed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade.id,
                trade.timestamp.isoformat(),
                trade.asset,
                trade.action.value,
                trade.entry_price,
                trade.stop_loss,
                trade.take_profit,
                trade.exit_price,
                trade.pnl,
                trade.confidence.value,
                trade.ai_reason,
                trade.mode,
                trade.status,
                trade.closed_at.isoformat() if trade.closed_at else None,
            ),
        )
        self.conn.commit()

    def _update_trade(self, trade: TradeRecord):
        """Update existing trade in ledger"""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE trades SET exit_price=?, pnl=?, status=?, closed_at=? WHERE id=?
            """,
            (
                trade.exit_price,
                trade.pnl,
                trade.status,
                trade.closed_at.isoformat() if trade.closed_at else None,
                trade.id,
            ),
        )
        self.conn.commit()

    # ==================== HAWK PROTOCOL METHODS ====================

    def suspend_scanners(self):
        """Halts all background scanning threads except for the locked asset."""
        if hasattr(self, 'scanner_thread') and self.scanner_thread:
            self.scanner_thread.paused = True
            logger.info("[SYSTEM] Multi-ticker scanners PAUSED.")

    def resume_scanners(self):
        """Resumes scanning after position close."""
        self.target_lock_active = False
        self.locked_asset = None
        if hasattr(self, 'scanner_thread') and self.scanner_thread:
            self.scanner_thread.paused = False
            logger.info("[SYSTEM] Multi-ticker scanners RESUMED.")

    def update_trailing_stops(self):
        """U-TURN RADAR & TRAILING STOP for locked asset."""
        if not self.target_lock_active or not self.locked_asset:
            return

        # Get current price for locked asset
        current_price = self.get_current_price(self.locked_asset)
        if not current_price:
            return

        # Calculate Dynamic ATR Buffer
        atr_value = self.calculate_atr(self.locked_asset, period=3)
        if atr_value is None:
            return
        buffer = atr_value * 1.5  # 1:1.5 Risk Symmetry
        
        position = self.get_open_position(self.locked_asset)
        if not position:
            self.resume_scanners()
            return

        # U-TURN LOGIC
        if position['type'] == 'BUY':
            trailing_stop = current_price - buffer
            if current_price < position['entry_price'] + (buffer * 0.5):  # Reversal detected
                logger.info("[HAWK U-TURN] Trend reversal detected on %s. Exiting.", self.locked_asset)
                self.execute_global_profit_harvest(symbol=self.locked_asset)
        
        elif position['type'] == 'SELL':
            trailing_stop = current_price + buffer
            if current_price > position['entry_price'] - (buffer * 0.5):  # Reversal detected
                logger.info("[HAWK U-TURN] Trend reversal detected on %s. Exiting.", self.locked_asset)
                self.execute_global_profit_harvest(symbol=self.locked_asset)

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current price for symbol from last known prices or market data."""
        # Check if we have the price in last_indicators (populated by swarm before process_signal)
        if hasattr(self, 'last_indicators') and symbol in self.last_indicators:
            # Try to extract current price from indicators
            price = (self.last_indicators.get("current_price") or 
                     self.last_indicators.get("close") or 
                     self.last_indicators.get("last_close"))
            if price:
                return float(price)
        
        # Check if we have it in stored prices dict (updated in manage_open_trades)
        if hasattr(self, '_last_prices') and symbol in self._last_prices:
            return self._last_prices[symbol]
        
        logger.warning("[HAWK] get_current_price not available for %s - no market data feed connected", symbol)
        return None

    def calculate_atr(self, symbol: str, period: int = 3) -> Optional[float]:
        """Calculate ATR for symbol using loose ATR stops or simple estimation."""
        try:
            # Try to use the imported LooseATRStops class
            if hasattr(self.atr_stops, 'calculate_atr'):
                atr_value = self.atr_stops.calculate_atr(symbol, period)
                if atr_value:
                    return float(atr_value)
            
            # Fallback: Simple ATR calculation if we have price history
            if hasattr(self, '_last_prices') and symbol in self._last_prices:
                # Simplified: use 1% of current price as proxy for ATR
                current_price = self._last_prices[symbol]
                estimated_atr = current_price * 0.01 * period
                logger.info("[HAWK] Using estimated ATR for %s: %.2f (based on %.2f price)", symbol, estimated_atr, current_price)
                return estimated_atr
            
            logger.warning("[HAWK] calculate_atr not available for %s - no ATR data source", symbol)
            return None
        except Exception as e:
            logger.error("[HAWK] Error calculating ATR for %s: %s", symbol, e)
            return None

    def get_open_position(self, symbol: str) -> Optional[dict]:
        """Get open position for symbol."""
        for trade in self.open_trades:
            if trade.asset == symbol:
                return {
                    'type': trade.action.value,
                    'entry_price': trade.entry_price,
                    'stop_loss': trade.stop_loss,
                    'take_profit': trade.take_profit,
                }
        return None

    def get_all_open_symbols(self) -> List[str]:
        """Get all symbols with open positions."""
        return [trade.asset for trade in self.open_trades]

    def close_position(self, symbol: str) -> bool:
        """Close position for symbol."""
        for i, trade in enumerate(self.open_trades):
            if trade.asset == symbol:
                current_price = self.get_current_price(symbol) or trade.entry_price
                self._close_trade_at_price(trade, current_price, "MANUAL_CLOSE")
                return True
        return False

    def speak(self, msg: str):
        """AI Narrator TTS. Override this with actual TTS implementation."""
        logger.info("[AI NARRATOR] %s", msg)

    def execute_global_profit_harvest(self, symbol=None):
        """
        Instantly closes positions. 
        If symbol is provided, closes only that symbol (U-Turn).
        If None, closes ALL (Manual Button Press).
        """
        symbols_to_close = [symbol] if symbol else self.get_all_open_symbols()
        
        total_profit = 0
        for sym in symbols_to_close:
            pos = self.get_open_position(sym)
            if pos:
                profit = pos.get('pnl', 0)
                self.close_position(sym)
                total_profit += profit
                logger.info("[EXECUTION] Closed %s. Profit: $%.2f", sym, profit)
        
        # Reset State
        self.resume_scanners()
        
        # Announce via AI Narrator
        msg = "Harvest complete. Total profit: $%.2f. Scanners re-engaged." % total_profit
        self.speak(msg)
        
        return total_profit

    # ==================== END HAWK PROTOCOL METHODS ====================

    def get_performance_summary(self) -> dict:
        """Get trading performance summary"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM trades WHERE status='CLOSED'")
        closed_count = cursor.fetchone()[0]

        cursor.execute("SELECT SUM(pnl) FROM trades WHERE status='CLOSED'")
        total_pnl = cursor.fetchone()[0] or 0

        cursor.execute("SELECT AVG(pnl) FROM trades WHERE status='CLOSED' AND pnl > 0")
        avg_win = cursor.fetchone()[0] or 0

        cursor.execute("SELECT AVG(pnl) FROM trades WHERE status='CLOSED' AND pnl < 0")
        avg_loss = cursor.fetchone()[0] or 0

        return {
            "total_trades": closed_count,
            "total_pnl": total_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "open_positions": len(self.open_trades),
            "daily_pnl": self.safety_state.daily_pnl,
        }

    def activate_kill_switch(self):
        """Emergency stop all trading"""
        self.safety_state.kill_switch_active = True
        config.KILL_SWITCH = True
        logger.critical("KILL SWITCH ACTIVATED")

    def deactivate_kill_switch(self):
        """Deactivate kill switch"""
        self.safety_state.kill_switch_active = False
        config.KILL_SWITCH = False
        logger.info("Kill switch deactivated")

    def reset_daily_limits(self):
        """Reset daily PnL counter (call at start of each trading day)"""
        self.safety_state.daily_pnl = 0.0
        self.safety_state.daily_loss_limit_hit = False
        logger.info("Daily limits reset")

    def cleanup(self):
        """Clean up resources"""
        if hasattr(self, "conn"):
            self.conn.close()
            logger.info("Trade engine cleanup complete")