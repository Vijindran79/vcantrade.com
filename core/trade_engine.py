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

logger = logging.getLogger(__name__)


class TradeEngine:
    """Core trading engine with safety controls and trade ledger"""

    def __init__(self):
        self.safety_state = SafetyState()
        self.open_trades: List[TradeRecord] = []
        self.trade_history: List[TradeRecord] = []
        self.cooldown_until: Optional[datetime] = None
        self.rpa_executor = RPAExecutor()
        
        # THE HAWK PROTOCOL - Single-Asset Target Lock
        self.target_lock_active = False
        self.locked_asset: Optional[str] = None
        self.atr_calculator = LooseATRStops(atr_period=14, multiplier=1.5)
        self.trailing_stop_buffer: Dict[str, float] = {}  # asset -> trailing stop price
        
        # U-Turn Radar - Volatility-aware exit detection
        self.uturn_threshold_multiplier = 1.0  # ATR multiplier for U-turn detection
        self.last_tracked_price: Dict[str, float] = {}
        
        # Profit Harvest state
        self.profit_harvest_triggered = False

        # Initialize SQLite ledger
        self._init_ledger()

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
            logger.error(f"Failed to initialize trade ledger: {e}")
            raise

    def process_signal(
        self, signal: LLMAnalysisOutput, mode: str = "TEACHER"
    ) -> Optional[TradeRecord]:
        """
        Process trading signal from LLM analyzer
        Returns TradeRecord if trade executed, None otherwise
        """
        # Check market regime filter (CHOPPY market protection)
        if signal.market_regime and signal.market_regime.upper() == "CHOP":
            if not config.ALLOW_TRADES_IN_CHOP:
                logger.warning(
                    f"🚫 CHOPPY MARKET BLOCKED: {signal.asset} | Regime={signal.market_regime} | Volatility={signal.volatility_state or 'N/A'}"
                )
                return None
        
        # Check safety controls
        if not self._check_safety():
            logger.warning(f"Trade blocked by safety controls: {signal.asset}")
            return None

        # Teacher mode: log signal but don't execute
        if mode == "TEACHER" or config.DRY_RUN:
            logger.info(
                f"[TEACHER MODE] Signal: {signal.action} {signal.asset} - {signal.reason}"
            )
            return self._create_signal_record(signal)

        # Auto mode: execute trade
        try:
            if signal.action == SignalAction.BUY:
                return self._execute_buy(signal)
            elif signal.action == SignalAction.SELL:
                return self._execute_sell(signal)
            elif signal.action == SignalAction.CLOSE:
                return self._execute_close(signal)
            else:
                logger.info(f"HOLD signal for {signal.asset}")
                return None

        except Exception as e:
            logger.error(f"Trade execution failed: {e}")
            return None

    def _check_safety(self) -> bool:
        """Verify all safety controls before trading"""
        import config

        # Kill switch
        if config.KILL_SWITCH or self.safety_state.kill_switch_active:
            logger.warning("KILL SWITCH ACTIVE - Trading halted")
            return False

        # Daily loss limit
        if abs(self.safety_state.daily_pnl) >= config.MAX_DAILY_LOSS:
            logger.warning(
                f"Daily loss limit reached: ${self.safety_state.daily_pnl:.2f}"
            )
            self.safety_state.daily_loss_limit_hit = True
            return False

        # Cooldown period
        if self.cooldown_until and datetime.utcnow() < self.cooldown_until:
            remaining = (self.cooldown_until - datetime.utcnow()).seconds
            self.safety_state.cooldown_remaining_seconds = remaining
            logger.warning(f"Cooldown active: {remaining}s remaining")
            return False
        else:
            self.safety_state.cooldown_remaining_seconds = 0
            self.cooldown_until = None

        # Max positions
        if len(self.open_trades) >= config.MAX_OPEN_POSITIONS:
            logger.warning(
                f"Max positions reached: {len(self.open_trades)}/{config.MAX_OPEN_POSITIONS}"
            )
            return False

        self.safety_state.open_positions = len(self.open_trades)
        self.safety_state.update_trade_ability()
        return self.safety_state.can_trade

    def _execute_buy(self, signal: LLMAnalysisOutput) -> TradeRecord:
        """Execute buy trade with HAWK PROTOCOL target lock"""
        # HAWK PROTOCOL: Activate single-asset target lock
        self.target_lock_active = True
        self.locked_asset = signal.asset
        logger.info(f"🦅 HAWK PROTOCOL ACTIVATED: Target locked on {signal.asset}")
        
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
        
        # Initialize ATR-based trailing stop
        if signal.entry_price and signal.atr:
            initial_stop = self.atr_calculator.calculate_stop_loss(
                entry_price=signal.entry_price,
                atr=signal.atr,
                direction="LONG"
            )
            self.trailing_stop_buffer[signal.asset] = initial_stop
            self.last_tracked_price[signal.asset] = signal.entry_price
            logger.info(f"📏 Initial ATR trailing stop set at ${initial_stop:.2f} for {signal.asset}")

        # Execute via RPA if not dry run
        if not config.DRY_RUN:
            success = self.rpa_executor.execute_trade(trade)
            if not success:
                logger.error(f"RPA execution failed for BUY {trade.asset}")

        logger.info(
            f"EXECUTED BUY: {trade.asset} @ {trade.entry_price} | SL: {trade.stop_loss} | TP: {trade.take_profit}"
        )
        return trade

    def _execute_sell(self, signal: LLMAnalysisOutput) -> TradeRecord:
        """Execute sell trade with HAWK PROTOCOL target lock"""
        # HAWK PROTOCOL: Activate single-asset target lock
        self.target_lock_active = True
        self.locked_asset = signal.asset
        logger.info(f"🦅 HAWK PROTOCOL ACTIVATED: Target locked on {signal.asset} (SHORT)")
        
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
        
        # Initialize ATR-based trailing stop for SHORT
        if signal.entry_price and signal.atr:
            initial_stop = self.atr_calculator.calculate_stop_loss(
                entry_price=signal.entry_price,
                atr=signal.atr,
                direction="SHORT"
            )
            self.trailing_stop_buffer[signal.asset] = initial_stop
            self.last_tracked_price[signal.asset] = signal.entry_price
            logger.info(f"📏 Initial ATR trailing stop set at ${initial_stop:.2f} for {signal.asset} (SHORT)")

        # Execute via RPA if not dry run
        if not config.DRY_RUN:
            success = self.rpa_executor.execute_trade(trade)
            if not success:
                logger.error(f"RPA execution failed for SELL {trade.asset}")

        logger.info(
            f"EXECUTED SELL: {trade.asset} @ {trade.entry_price} | SL: {trade.stop_loss} | TP: {trade.take_profit}"
        )
        return trade

    def _execute_close(self, signal: LLMAnalysisOutput) -> Optional[TradeRecord]:
        """Close existing position and release HAWK PROTOCOL target lock"""
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

                logger.info(f"CLOSED: {trade.asset} | PnL: ${trade.pnl:.2f}")

                # HAWK PROTOCOL: Release target lock after close
                if self.locked_asset == signal.asset:
                    self.target_lock_active = False
                    self.locked_asset = None
                    # Clear trailing stop buffer
                    if signal.asset in self.trailing_stop_buffer:
                        del self.trailing_stop_buffer[signal.asset]
                    if signal.asset in self.last_tracked_price:
                        del self.last_tracked_price[signal.asset]
                    logger.info("🦅 HAWK PROTOCOL RELEASED: Target lock cleared, resuming multi-asset scan")

                # Check if hit stop loss - start cooldown
                if trade.pnl and trade.pnl < 0 and trade.stop_loss:
                    self.cooldown_until = datetime.utcnow() + timedelta(
                        seconds=config.COOLDOWN_AFTER_STOP
                    )
                    logger.warning(
                        f"Stop loss hit - cooldown for {config.COOLDOWN_AFTER_STOP}s"
                    )

                return trade

        logger.warning(f"No open position found for {signal.asset}")
        return None

    def update_trailing_stops(self, current_prices: Dict[str, float]):
        """
        U-TURN RADAR: Update trailing stops based on current market prices.
        Detects volatility-aware U-turn reversals and triggers immediate exits.
        
        Args:
            current_prices: Dict mapping asset symbols to current market prices
        """
        if not self.target_lock_active or not self.locked_asset:
            return  # No active target, skip trailing stop updates
        
        if self.locked_asset not in current_prices:
            return  # Price data not available
        
        current_price = current_prices[self.locked_asset]
        
        # Find the open trade for locked asset
        active_trade = None
        for trade in self.open_trades:
            if trade.asset == self.locked_asset:
                active_trade = trade
                break
        
        if not active_trade:
            # No open trade, release lock
            self.target_lock_active = False
            self.locked_asset = None
            return
        
        # Calculate ATR-based trailing stop adjustment
        atr_data = self.atr_calculator.calculate_3day_volatility()
        current_atr = atr_data.get('avg_atr_3d', 0)
        
        if current_atr <= 0:
            current_atr = abs(current_price * 0.01)  # Fallback: 1% of price
        
        direction = "LONG" if active_trade.action == SignalAction.BUY else "SHORT"
        
        # Update trailing stop buffer based on price movement
        if self.locked_asset in self.trailing_stop_buffer:
            current_trailing_stop = self.trailing_stop_buffer[self.locked_asset]
            
            if direction == "LONG":
                # For LONG: trail stop upward as price increases
                new_trailing_stop = current_price - (current_atr * self.uturn_threshold_multiplier)
                if new_trailing_stop > current_trailing_stop:
                    self.trailing_stop_buffer[self.locked_asset] = new_trailing_stop
                    logger.debug(f"📈 Trailing stop raised to ${new_trailing_stop:.2f} for {self.locked_asset}")
                
                # U-TURN DETECTION: Price reversed below trailing stop
                if current_price < current_trailing_stop:
                    logger.warning(
                        f"🚨 U-TURN DETECTED: {self.locked_asset} dropped below trailing stop "
                        f"${current_trailing_stop:.2f} (current: ${current_price:.2f})"
                    )
                    # Trigger immediate close via U-turn enforcement
                    self._enforce_uturn_exit(active_trade, current_price, "Trailing Stop Breach")
                    
            else:  # SHORT
                # For SHORT: trail stop downward as price decreases
                new_trailing_stop = current_price + (current_atr * self.uturn_threshold_multiplier)
                if new_trailing_stop < current_trailing_stop:
                    self.trailing_stop_buffer[self.locked_asset] = new_trailing_stop
                    logger.debug(f"📉 Trailing stop lowered to ${new_trailing_stop:.2f} for {self.locked_asset}")
                
                # U-TURN DETECTION: Price reversed above trailing stop
                if current_price > current_trailing_stop:
                    logger.warning(
                        f"🚨 U-TURN DETECTED: {self.locked_asset} rose above trailing stop "
                        f"${current_trailing_stop:.2f} (current: ${current_price:.2f})"
                    )
                    # Trigger immediate close via U-turn enforcement
                    self._enforce_uturn_exit(active_trade, current_price, "Trailing Stop Breach")
        
        # Track last price for next iteration
        self.last_tracked_price[self.locked_asset] = current_price

    def _enforce_uturn_exit(self, trade: TradeRecord, exit_price: float, reason: str):
        """
        U-TURN ENFORCEMENT: Immediately close position when trailing stop is breached.
        Bypasses all delay channels and logs to trade journal.
        
        Args:
            trade: The TradeRecord to close
            exit_price: Current market exit price
            reason: Reason for exit (e.g., "Trailing Stop Breach", "U-Turn Reversal")
        """
        logger.critical(f"🚨 U-TURN ENFORCEMENT: Closing {trade.asset} - {reason}")
        
        # Calculate PnL
        trade.exit_price = exit_price
        trade.pnl = (
            (exit_price - trade.entry_price)
            if trade.action == SignalAction.BUY
            else (trade.entry_price - exit_price)
        )
        
        # Update daily PnL
        self.safety_state.daily_pnl += trade.pnl or 0
        
        # Execute market close via RPA
        if not config.DRY_RUN:
            try:
                # Create close signal
                close_signal = LLMAnalysisOutput(
                    asset=trade.asset,
                    action=SignalAction.CLOSE,
                    entry_price=exit_price,
                    confidence=ConfidenceLevel.HIGH,
                    reason=f"U-TURN EXIT: {reason}"
                )
                self.rpa_executor.execute_close(trade)
                logger.info(f"✅ Market close order executed for {trade.asset} @ ${exit_price:.2f}")
            except Exception as e:
                logger.error(f"❌ RPA close execution failed: {e}")
        
        # Update trade record
        trade.status = "CLOSED"
        trade.closed_at = datetime.utcnow()
        trade.ai_reason = f"{trade.ai_reason or ''} | EXIT: {reason}"
        
        # Remove from open trades
        if trade in self.open_trades:
            self.open_trades.remove(trade)
        self.trade_history.append(trade)
        
        # Update ledger
        self._update_trade(trade)
        
        # Log to trade journal
        from core.journal import TradeJournalDB
        try:
            journal = TradeJournalDB()
            journal.log_exit(trade.id, exit_price, trade.pnl, reason)
            logger.info(f"📖 U-turn exit logged to trade journal: {trade.asset} | PnL: ${trade.pnl:.2f}")
        except Exception as e:
            logger.error(f"Failed to log U-turn exit to journal: {e}")
        
        # HAWK PROTOCOL: Release target lock
        self.target_lock_active = False
        self.locked_asset = None
        if trade.asset in self.trailing_stop_buffer:
            del self.trailing_stop_buffer[trade.asset]
        if trade.asset in self.last_tracked_price:
            del self.last_tracked_price[trade.asset]
        
        logger.info("🦅 HAWK PROTOCOL RELEASED: Ready for next opportunity")

    def execute_global_profit_harvest(self) -> Dict:
        """
        ONE-CLICK PROFIT HARVEST: Master execution method to flatten all positions.
        Bypasses all AI confirmations and trailing calculations.
        Immediately transmits market-flatten orders to MetaTrader 5.
        
        Returns:
            Dict with harvest results (positions_closed, total_pnl, assets_closed)
        """
        logger.critical("🔴 ONE-CLICK PROFIT HARVEST TRIGGERED")
        
        results = {
            "positions_closed": 0,
            "total_pnl": 0.0,
            "assets_closed": [],
            "timestamp": datetime.utcnow().isoformat()
        }
        
        # Copy list to avoid modification during iteration
        trades_to_close = list(self.open_trades)
        
        for trade in trades_to_close:
            logger.info(f"🎯 Harvesting profit: {trade.asset} ({trade.action.value})")
            
            # Get current market price (mock - would come from live feed)
            exit_price = trade.entry_price  # Placeholder
            if trade.asset in self.last_tracked_price:
                exit_price = self.last_tracked_price[trade.asset]
            
            # Calculate PnL
            pnl = (
                (exit_price - trade.entry_price)
                if trade.action == SignalAction.BUY
                else (trade.entry_price - exit_price)
            )
            
            # Execute market close via RPA (bypass all checks)
            if not config.DRY_RUN:
                try:
                    self.rpa_executor.execute_close(trade)
                    logger.info(f"✅ Market close executed for {trade.asset} @ ${exit_price:.2f}")
                except Exception as e:
                    logger.error(f"❌ Failed to close {trade.asset}: {e}")
                    continue
            
            # Update trade record
            trade.exit_price = exit_price
            trade.pnl = pnl
            trade.status = "CLOSED"
            trade.closed_at = datetime.utcnow()
            trade.ai_reason = f"{trade.ai_reason or ''} | EXIT: ONE-CLICK PROFIT HARVEST"
            
            # Update daily PnL
            self.safety_state.daily_pnl += pnl or 0
            
            # Remove from open trades
            if trade in self.open_trades:
                self.open_trades.remove(trade)
            self.trade_history.append(trade)
            
            # Update ledger
            self._update_trade(trade)
            
            # Log to journal
            from core.journal import TradeJournalDB
            try:
                journal = TradeJournalDB()
                journal.log_exit(trade.id, exit_price, pnl, "One-Click Profit Harvest")
            except Exception as e:
                logger.error(f"Failed to log harvest exit: {e}")
            
            # Update results
            results["positions_closed"] += 1
            results["total_pnl"] += pnl or 0
            results["assets_closed"].append(trade.asset)
        
        # HAWK PROTOCOL: Release all target locks
        self.target_lock_active = False
        self.locked_asset = None
        self.trailing_stop_buffer.clear()
        self.last_tracked_price.clear()
        
        logger.info(
            f"✅ PROFIT HARVEST COMPLETE: Closed {results['positions_closed']} positions | "
            f"Total PnL: ${results['total_pnl']:.2f} | Assets: {', '.join(results['assets_closed'])}"
        )
        
        return results

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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
