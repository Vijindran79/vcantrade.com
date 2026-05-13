"""
Confidence Escalator - Dual-Stage Trade Validation System

Strategy: Two-stage validation
- Paper/simulation probe validates the setup first
- TradingView or MetaTrader 5 handles the final execution route

Phase 1 (PROBING):  50% confidence -> track simulated/paper probe
Phase 2 (STRIKE):   85% confidence -> allow TradingView/MT5 real strike

Kill Switch: If SIM fails or S1 breaks, REAL trade is NEVER placed.
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class EscalatorState(Enum):
    IDLE = "IDLE"
    PROBING = "PROBING"      # SIM trade active, monitoring
    VALIDATING = "VALIDATING"  # Checking if SIM confirms the move
    READY_TO_STRIKE = "READY_TO_STRIKE"  # 85% confidence reached
    STRIKING = "STRIKING"    # REAL trade being placed
    COMPLETE = "COMPLETE"    # Trade cycle complete
    FAILED = "FAILED"        # SIM failed or S1 broken


@dataclass
class ConfidenceMetrics:
    """Tracks confidence-building metrics."""
    current_confidence: float = 0.0
    bars_held_s1: int = 0
    bars_required: int = 3  # Bars S1 must hold before escalation
    sim_in_profit: bool = False
    sim_entry_price: float = 0.0
    sim_current_pnl: float = 0.0
    price_holding_s1: bool = False
    last_s1_test_time: Optional[datetime] = None
    confidence_history: list = field(default_factory=list)

    def update_confidence(self):
        """Calculate confidence based on multiple factors."""
        conf = 0.0
        # Phase 1: Initial signal (50%)
        if self.bars_held_s1 >= 1:
            conf = 50.0
        # Phase 2: Price holding S1
        if self.bars_held_s1 >= self.bars_required:
            conf = max(conf, 70.0)
        # Phase 3: SIM in profit
        if self.sim_in_profit:
            conf = max(conf, 80.0)
        # Phase 4: Both conditions met
        if self.bars_held_s1 >= self.bars_required and self.sim_in_profit:
            conf = 85.0
        self.current_confidence = conf
        self.confidence_history.append((datetime.utcnow(), conf))

    def reset(self):
        self.current_confidence = 0.0
        self.bars_held_s1 = 0
        self.sim_in_profit = False
        self.sim_entry_price = 0.0
        self.sim_current_pnl = 0.0
        self.price_holding_s1 = False
        self.last_s1_test_time = None


@dataclass
class SimTradeState:
    """Tracks the SIM trade being used as a probe."""
    active: bool = False
    trade_id: Optional[str] = None
    entry_price: float = 0.0
    entry_time: Optional[datetime] = None
    current_pnl: float = 0.0
    is_profitable: bool = False
    bars_since_entry: int = 0
    stop_loss: float = 0.0
    take_profit: float = 0.0

    def update_pnl(self, current_price: float):
        if not self.active or not self.entry_price:
            return
        self.current_pnl = current_price - self.entry_price
        self.is_profitable = self.current_pnl > 0
        self.bars_since_entry += 1

    def close(self):
        self.active = False
        self.bars_since_entry = 0


class ConfidenceEscalator:
    """
    Dual-Stage Confidence Escalator for safe trade execution.

    Flow:
    IDLE -> PROBING (50% conf, BUY_SIM)
         -> VALIDATING (monitoring SIM)
         -> READY_TO_STRIKE (85% conf)
         -> STRIKING (BUY_REAL)
         -> COMPLETE

    Kill Switch Path:
    PROBING -> FAILED (SIM fails or S1 breaks)
    """

    def __init__(self, bars_required: int = 3, min_profit_pips: float = 1.0):
        self.state = EscalatorState.IDLE
        self.metrics = ConfidenceMetrics(bars_required=bars_required)
        self.sim_trade = SimTradeState()
        self.real_trade_placed = False
        self.kill_switch_triggered = False
        self.last_state_change = datetime.utcnow()
        self.min_profit_pips = min_profit_pips
        self._state_observers = []

    def add_observer(self, callback):
        """Add callback for state changes: callback(state, metrics, sim_trade)."""
        self._state_observers.append(callback)

    def _notify_observers(self):
        for cb in self._state_observers:
            try:
                cb(self.state, self.metrics, self.sim_trade)
            except Exception as e:
                logger.error(f"Observer notification failed: {e}")

    def _transition_to(self, new_state: EscalatorState, reason: str = ""):
        old_state = self.state
        self.state = new_state
        self.last_state_change = datetime.utcnow()
        logger.info(f"[ESCALATOR] {old_state.value} -> {new_state.value} | {reason}")
        self._notify_observers()

    def trigger_probe(self, entry_price: float, stop_loss: float, take_profit: float) -> bool:
        """
        Phase 1: Trigger SIM probe at 50% confidence.
        Returns True if probe was triggered successfully.
        """
        if self.state != EscalatorState.IDLE:
            logger.warning(f"[ESCALATOR] Cannot trigger probe from {self.state.value}")
            return False

        if self.kill_switch_triggered:
            logger.critical("[ESCALATOR] Kill switch active - probe blocked")
            return False

        self.sim_trade = SimTradeState(
            active=True,
            entry_price=entry_price,
            entry_time=datetime.utcnow(),
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        self.metrics.sim_entry_price = entry_price
        self._transition_to(EscalatorState.PROBING, f"SIM probe @ {entry_price}")
        return True

    def update_market_conditions(self, current_price: float, s1_level: float,
                                  bar_closed: bool = False) -> EscalatorState:
        """
        Update with latest market data. Call on each tick/bar close.
        Returns current state after update.
        """
        if self.state == EscalatorState.IDLE:
            return self.state

        if self.kill_switch_triggered:
            self._transition_to(EscalatorState.FAILED, "Kill switch active")
            return self.state

        # Check if price is holding S1
        holding_s1 = current_price >= s1_level
        self.metrics.price_holding_s1 = holding_s1

        if bar_closed:
            if holding_s1:
                self.metrics.bars_held_s1 += 1
                logger.info(f"[ESCALATOR] S1 held for {self.metrics.bars_held_s1} bars")
            else:
                logger.warning("[ESCALATOR] S1 broken - price below S1!")
                self._handle_s1_break()
                return self.state

        # Update SIM trade P&L
        if self.sim_trade.active:
            self.sim_trade.update_pnl(current_price)
            self.metrics.sim_current_pnl = self.sim_trade.current_pnl
            self.metrics.sim_in_profit = (
                self.sim_trade.is_profitable and
                self.sim_trade.current_pnl >= self.min_profit_pips
            )

            # Check for SIM failure (stopped out or deep loss)
            if current_price <= self.sim_trade.stop_loss:
                logger.warning("[ESCALATOR] SIM trade hit stop loss!")
                self._handle_sim_failure("SIM stop loss hit")
                return self.state

        # Update confidence
        self.metrics.update_confidence()

        # State machine transitions
        self._evaluate_state_transitions(current_price, s1_level)

        return self.state

    def _evaluate_state_transitions(self, current_price: float, s1_level: float):
        """Evaluate and execute state transitions based on current metrics."""
        if self.state == EscalatorState.PROBING:
            # Move to validating once we have at least 1 bar
            if self.metrics.bars_held_s1 >= 1:
                self._transition_to(EscalatorState.VALIDATING, "Started validating SIM performance")

        elif self.state == EscalatorState.VALIDATING:
            # Check if ready to strike (85% confidence)
            if (self.metrics.bars_held_s1 >= self.metrics.bars_required and
                self.metrics.sim_in_profit and
                self.metrics.current_confidence >= 85.0):
                self._transition_to(EscalatorState.READY_TO_STRIKE,
                                   f"85% confidence: {self.metrics.bars_held_s1} bars, SIM profitable")

        elif self.state == EscalatorState.READY_TO_STRIKE:
            # Waiting for execution signal
            pass

    def execute_real_trade(self) -> bool:
        """
        Phase 2: Execute REAL trade after SIM confirmation.
        Returns True if REAL trade was placed.
        """
        if self.state != EscalatorState.READY_TO_STRIKE:
            logger.warning(f"[ESCALATOR] Cannot strike from {self.state.value}")
            return False

        self._transition_to(EscalatorState.STRIKING, "Executing REAL trade")
        self.real_trade_placed = True
        return True

    def complete_cycle(self):
        """Mark the trade cycle as complete."""
        self._transition_to(EscalatorState.COMPLETE, "Trade cycle complete")
        # Reset for next cycle after brief delay
        time.sleep(1)
        self.reset()

    def _handle_s1_break(self):
        """Handle S1 support break - kill the cycle."""
        self.kill_switch_triggered = True
        self.sim_trade.close()
        self._transition_to(EscalatorState.FAILED, "S1 support broken")

    def _handle_sim_failure(self, reason: str):
        """Handle SIM trade failure - kill the cycle."""
        self.kill_switch_triggered = True
        self.sim_trade.close()
        self._transition_to(EscalatorState.FAILED, reason)

    def reset(self):
        """Reset the escalator for a new trade cycle."""
        self.state = EscalatorState.IDLE
        self.metrics.reset()
        self.sim_trade = SimTradeState()
        self.real_trade_placed = False
        self.kill_switch_triggered = False
        self.last_state_change = datetime.utcnow()
        logger.info("[ESCALATOR] Reset complete - ready for new cycle")

    def get_confidence_display(self) -> dict:
        """Get current state for GUI display."""
        return {
            "state": self.state.value,
            "confidence": self.metrics.current_confidence,
            "bars_held_s1": self.metrics.bars_held_s1,
            "bars_required": self.metrics.bars_required,
            "sim_active": self.sim_trade.active,
            "sim_profitable": self.metrics.sim_in_profit,
            "sim_pnl": self.metrics.sim_current_pnl,
            "price_holding_s1": self.metrics.price_holding_s1,
            "kill_switch": self.kill_switch_triggered,
            "real_trade_placed": self.real_trade_placed,
            "time_in_state": (datetime.utcnow() - self.last_state_change).seconds,
        }
