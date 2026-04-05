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
        """Execute buy trade"""
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
                logger.error(f"RPA execution failed for BUY {trade.asset}")

        logger.info(
            f"EXECUTED BUY: {trade.asset} @ {trade.entry_price} | SL: {trade.stop_loss} | TP: {trade.take_profit}"
        )
        return trade

    def _execute_sell(self, signal: LLMAnalysisOutput) -> TradeRecord:
        """Execute sell trade"""
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
                logger.error(f"RPA execution failed for SELL {trade.asset}")

        logger.info(
            f"EXECUTED SELL: {trade.asset} @ {trade.entry_price} | SL: {trade.stop_loss} | TP: {trade.take_profit}"
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

                logger.info(f"CLOSED: {trade.asset} | PnL: ${trade.pnl:.2f}")

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
