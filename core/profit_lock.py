"""
VcanTrade AI - Profit Lock System (Stage 3)

Dynamic Equity Guard & Trailing Drawdown Protection.
Monitors actual account balance and adjusts stops in real-time.

Features:
1. Trailing Max Drawdown (tightens stops on profit)
2. Breakeven + 1% lock when daily target reached
3. Walk Away Protocol (24h shutdown on max loss)
4. Equity Curve Monitoring
"""

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
import json

import config

logger = logging.getLogger(__name__)


class WalkAwayProtocol:
    """
    Emergency shutdown system to prevent revenge trading.
    If daily loss exceeds threshold, bot shuts down for 24 hours.
    """
    
    def __init__(self, max_daily_loss_pct: float = 2.0, shutdown_hours: int = 24):
        self.max_daily_loss_pct = max_daily_loss_pct
        self.shutdown_hours = shutdown_hours
        self.is_active = False
        self.shutdown_time = None
        self.shutdown_reason = ""
        self.violations_logged = []
        
        logger.info(
            f"🚶 Walk Away Protocol initialized: "
            f"Max Daily Loss={max_daily_loss_pct}%, "
            f"Shutdown={shutdown_hours}h"
        )
    
    def check_violation(self, daily_pnl_pct: float) -> bool:
        """
        Check if daily loss exceeds threshold.
        
        Args:
            daily_pnl_pct: Daily P&L as percentage (negative = loss)
            
        Returns:
            True if violation detected
        """
        if self.is_active:
            return True  # Already shut down
        
        if daily_pnl_pct <= -self.max_daily_loss_pct:
            # TRIGGER SHUTDOWN
            self.is_active = True
            self.shutdown_time = datetime.now()
            self.shutdown_reason = (
                f"THRESHOLD BREACHED. Daily loss {daily_pnl_pct:.2f}% "
                f"exceeded max {self.max_daily_loss_pct}%. "
                f"Shutting down for {self.shutdown_hours} hours to protect capital."
            )
            
            self.violations_logged.append({
                "timestamp": datetime.now().isoformat(),
                "daily_pnl_pct": daily_pnl_pct,
                "reason": self.shutdown_reason
            })
            
            logger.critical(f"🛑 WALK AWAY TRIGGERED: {self.shutdown_reason}")
            return True
        
        return False
    
    def can_trade(self) -> bool:
        """Check if bot is allowed to trade."""
        if not self.is_active:
            return True
        
        # Check if shutdown period has elapsed
        elapsed = datetime.now() - self.shutdown_time
        elapsed_hours = elapsed.total_seconds() / 3600
        
        if elapsed_hours >= self.shutdown_hours:
            logger.info(
                f"✅ Walk Away Protocol cleared: "
                f"{elapsed_hours:.1f}h shutdown complete"
            )
            self.is_active = False
            self.shutdown_time = None
            self.shutdown_reason = ""
            return True
        else:
            remaining = self.shutdown_hours - elapsed_hours
            logger.warning(
                f"🚶 Walk Away ACTIVE: {remaining:.1f}h remaining"
            )
            return False
    
    def get_status(self) -> Dict:
        """Get Walk Away status."""
        if not self.is_active:
            return {
                "active": False,
                "can_trade": True,
                "reason": "No violations"
            }
        
        elapsed = datetime.now() - self.shutdown_time
        elapsed_hours = elapsed.total_seconds() / 3600
        remaining = max(0, self.shutdown_hours - elapsed_hours)
        
        return {
            "active": True,
            "can_trade": False,
            "shutdown_time": self.shutdown_time.isoformat(),
            "elapsed_hours": elapsed_hours,
            "remaining_hours": remaining,
            "reason": self.shutdown_reason
        }


class ProfitLock:
    """
    Dynamic Equity Guard System.
    
    Responsibilities:
    1. Monitor daily P&L and equity curve
    2. Auto-tighten stops when profit target reached
    3. Move stops to breakeven + 1% when daily target hit
    4. Trigger Walk Away Protocol on max loss
    """

    def __init__(
        self,
        daily_profit_target_pct: float = 3.0,
        daily_max_loss_pct: float = 2.0,
        breakeven_buffer_pct: float = 1.0,
        starting_balance: float = 10000.0,
    ):
        """
        Initialize Profit Lock.
        
        Args:
            daily_profit_target_pct: % profit to trigger lock (default 3%)
            daily_max_loss_pct: % loss to trigger shutdown (default 2%)
            breakeven_buffer_pct: Extra % above breakeven when locking (default 1%)
            starting_balance: Starting account balance
        """
        self.daily_profit_target_pct = daily_profit_target_pct
        self.daily_max_loss_pct = daily_max_loss_pct
        self.breakeven_buffer_pct = breakeven_buffer_pct
        self.starting_balance = starting_balance
        
        # Daily tracking
        self.daily_start_balance = starting_balance
        self.daily_start_time = datetime.now().replace(hour=0, minute=0, second=0)
        self.current_balance = starting_balance
        self.peak_balance_today = starting_balance
        
        # Position tracking
        self.open_positions: List[Dict] = []
        self.stops_adjusted = False
        self.locks_triggered = []
        
        # Walk Away Protocol
        self.walk_away = WalkAwayProtocol(
            max_daily_loss_pct=daily_max_loss_pct,
            shutdown_hours=24
        )
        
        logger.info(
            f"🔒 Profit Lock initialized: "
            f"Target={daily_profit_target_pct}%, "
            f"Max Loss={daily_max_loss_pct}%, "
            f"BE Buffer={breakeven_buffer_pct}%"
        )

    def update_balance(self, current_balance: float):
        """Update current account balance."""
        self.current_balance = current_balance
        
        # Track peak
        if current_balance > self.peak_balance_today:
            self.peak_balance_today = current_balance
    
    def add_position(
        self,
        asset: str,
        entry_price: float,
        stop_loss: float,
        take_profit: Optional[float],
        position_size: float,
    ):
        """Add a position to tracking."""
        initial_risk_amount = abs(entry_price - stop_loss) * max(position_size, 0.0)
        self.open_positions.append({
            "asset": asset,
            "entry_price": entry_price,
            "original_stop": stop_loss,
            "current_stop": stop_loss,
            "take_profit": take_profit,
            "position_size": position_size,
            "initial_risk_amount": initial_risk_amount,
            "stop_locked": False,
            "break_even_locked": False,
            "timestamp": datetime.now().isoformat()
        })
        
        logger.info(
            f"📊 Position added to Profit Lock: "
            f"{asset} @ ${entry_price:.2f}, SL=${stop_loss:.2f}"
        )

    def remove_position(self, asset: str):
        """Remove a position from tracking after it closes."""
        self.open_positions = [pos for pos in self.open_positions if pos.get("asset") != asset]

    def update_position_stop(
        self,
        asset: str,
        new_stop: float,
        reason: str = "",
        stop_locked: bool = False,
        break_even_locked: bool = False,
    ) -> bool:
        """Persist the latest managed stop for a tracked position."""
        for pos in self.open_positions:
            if pos.get("asset") != asset:
                continue
            pos["current_stop"] = new_stop
            pos["stop_locked"] = pos.get("stop_locked", False) or stop_locked
            pos["break_even_locked"] = pos.get("break_even_locked", False) or break_even_locked
            if reason:
                pos["last_update_reason"] = reason
                pos["last_update_time"] = datetime.now().isoformat()
            if stop_locked or break_even_locked:
                self.stops_adjusted = True
            return True
        return False

    def _current_profit_amount(self, position: Dict, current_price: float) -> float:
        """Return open profit in account currency for the given position."""
        entry_price = float(position.get("entry", position.get("entry_price", 0.0)) or 0.0)
        quantity = float(position.get("quantity", position.get("position_size", 0.0)) or 0.0)
        side = str(position.get("side", "") or "").upper()
        if entry_price <= 0 or quantity <= 0 or current_price <= 0:
            return 0.0
        if side == "SELL":
            return (entry_price - current_price) * quantity
        return (current_price - entry_price) * quantity

    def check_break_even(self, position: Dict, current_price: float) -> Optional[Dict]:
        """
        MICRO-CONTRACT SHIELD: Raise the stop to entry plus buffer once profit target is achieved.
        
        PREDATOR-CLASS MNQ/MES LOGIC:
        - Automatic Break-Even at +7.5 points profit
        - Buffer: 0.5% to prevent premature triggering
        
        FIXED: Now correctly uses position entry price, not account balance.
        """
        entry_price = float(position.get("entry", position.get("entry_price", 0.0)) or 0.0)
        current_stop = float(position.get("sl_price", position.get("current_stop", 0.0)) or 0.0)
        side = str(position.get("side", "") or "").upper()
        initial_risk_amount = float(position.get("initial_risk_amount", 0.0) or 0.0)
        break_even_locked = bool(position.get("break_even_locked"))
        
        # PREDATOR-CLASS: MNQ/MES Break-Even at +7.5 points ($2/pt = $15 profit threshold)
        MNQ_BREAK_EVEN_POINTS = 7.5
        MNQ_POINT_VALUE = 2.0  # $2 per point for Micro NQ
        
        if break_even_locked or entry_price <= 0 or initial_risk_amount <= 0 or side not in {"BUY", "SELL"}:
            return None

        current_profit = self._current_profit_amount(position, current_price)
        
        # PREDATOR-CLASS: Check if we've achieved +7.5 points profit
        profit_points = abs(current_price - entry_price)
        if profit_points < MNQ_BREAK_EVEN_POINTS:
            return None

        # Use configurable buffer percentage (default 0.5% for MNQ/MES)
        buffer_pct = max(0.0, float(getattr(config, 'AUTONOMOUS_BREAK_EVEN_BUFFER_PCT', 0.5)))
        
        if side == "SELL":
            # For shorts: new stop = entry + buffer (stop goes ABOVE entry)
            new_stop = entry_price * (1.0 + (buffer_pct / 100.0))
            # Only update if new stop is tighter (lower than current)
            if current_stop > 0 and new_stop >= current_stop:
                return None
        else:
            # For longs: new stop = entry - buffer (stop goes BELOW entry)
            new_stop = entry_price * (1.0 - (buffer_pct / 100.0))
            # Only update if new stop is tighter (higher than current)
            if current_stop > 0 and new_stop <= current_stop:
                return None

        return {
            "new_stop": float(new_stop),
            "reason": f"🛡️ MNQ Shield BE lock (+{MNQ_BREAK_EVEN_POINTS}pts/{buffer_pct:.2f}%)",
            "break_even_locked": True,
            "stop_locked": True,
        }

    def calculate_three_bar_trailing_stop(
        self,
        position: Dict,
        recent_candles: Any,
        current_price: float,
        lookback_bars: int = 3,
    ) -> Optional[Dict]:
        """
        PREDATOR-CLASS 3-BAR TRAILING STOP: Follow 3-MINUTE candle lows/highs.
        
        MICRO-CONTRACT SHIELD LOGIC - UPDATED FOR BREATHING ROOM:
        - Changed from 1-minute to 3-MINUTE candles to avoid premature exits
        - For LONG positions: Trail below the lowest low of last 3 candles (3-min)
        - For SHORT positions: Trail above the highest high of last 3 candles (3-min)
        - Only activates when trade is in profit
        - $2/point value for MNQ/MES contracts
        
        Args:
            position: Position dict with entry, side, current_stop
            recent_candles: DataFrame with 'Low' and 'High' columns (3-MIN candles)
            current_price: Current market price
            lookback_bars: Number of bars to trail (default 3)
            
        Returns:
            Dict with new_stop if tighter stop found, None otherwise
        """
        if recent_candles is None or lookback_bars <= 0:
            return None

        side = str(position.get("side", "") or "").upper()
        current_stop = float(position.get("sl_price", position.get("current_stop", 0.0)) or 0.0)
        current_profit = self._current_profit_amount(position, current_price)
        
        # Only trail when in profit (Predator-Class profit protection)
        if current_profit <= 0 or side not in {"BUY", "SELL"}:
            return None

        tail = recent_candles.tail(max(lookback_bars, 1))
        if tail.empty:
            return None

        if side == "SELL":
            # SHORT position: Trail ABOVE the highest high of last N candles (3-min)
            if "High" not in tail or tail["High"].dropna().empty:
                return None
            new_stop = float(tail["High"].dropna().max())
            # Stop must be above current price and tighter than existing stop
            if new_stop <= 0 or new_stop <= current_price:
                return None
            if current_stop > 0 and new_stop >= current_stop:
                return None
        else:
            # LONG position: Trail BELOW the lowest low of last N candles (3-min)
            if "Low" not in tail or tail["Low"].dropna().empty:
                return None
            new_stop = float(tail["Low"].dropna().min())
            # Stop must be below current price and tighter than existing stop
            if new_stop <= 0 or new_stop >= current_price:
                return None
            if current_stop > 0 and new_stop <= current_stop:
                return None

        return {
            "new_stop": new_stop,
            "reason": f"🛡️ {lookback_bars}-bar MNQ vacuum trail (3-min candles)",
            "break_even_locked": False,
            "stop_locked": True,
        }

    def check_profit_locks(self) -> Dict:
        """
        Main check: Should we tighten stops?
        
        Returns:
            Dict with actions taken
        """
        # Check if Walk Away triggered
        if not self.walk_away.can_trade():
            return {
                "action": "WALK_AWAY_TRIGGERED",
                "can_trade": False,
                "reason": self.walk_away.get_status()["reason"]
            }
        
        daily_pnl_pct = self.get_daily_pnl_pct()
        
        # Check Walk Away violation
        if self.walk_away.check_violation(daily_pnl_pct):
            return {
                "action": "WALK_AWAY_TRIGGERED",
                "can_trade": False,
                "reason": self.walk_away.get_status()["reason"]
            }
        
        actions = {
            "action": "NO_CHANGE",
            "can_trade": True,
            "daily_pnl_pct": daily_pnl_pct,
            "stops_adjusted": False
        }
        
        # Check if profit target reached
        if daily_pnl_pct >= self.daily_profit_target_pct:
            if not self.stops_adjusted:
                # MOVE ALL STOPS TO BREAKEVEN + 1%
                self._lock_all_stops_to_breakeven()
                actions["action"] = "STOPS_LOCKED_TO_BREAKEVEN"
                actions["stops_adjusted"] = True
                actions["breakeven_level"] = self._calculate_breakeven_level()
                
                logger.info(
                    f"🔒 PROFIT LOCK TRIGGERED: Daily P&L {daily_pnl_pct:.2f}% >= {self.daily_profit_target_pct}% "
                    f"All stops moved to breakeven + {self.breakeven_buffer_pct}%"
                )
        
        return actions

    def _lock_all_stops_to_breakeven(self):
        """Move all position stops to breakeven + buffer."""
        breakeven_level = self._calculate_breakeven_level()
        
        for pos in self.open_positions:
            if not pos["stop_locked"]:
                old_stop = pos["current_stop"]
                pos["current_stop"] = breakeven_level
                pos["stop_locked"] = True
                
                logger.info(
                    f"🔒 Stop locked for {pos['asset']}: "
                    f"${old_stop:.2f} → ${breakeven_level:.2f}"
                )
        
        self.stops_adjusted = True
        self.locks_triggered.append({
            "timestamp": datetime.now().isoformat(),
            "daily_pnl_pct": self.get_daily_pnl_pct(),
            "breakeven_level": breakeven_level,
            "positions_locked": len(self.open_positions)
        })

    def _calculate_breakeven_level(self) -> float:
        """Calculate breakeven balance level + buffer.
        
        NOTE: This is for ACCOUNT-LEVEL profit locking, not position-level.
        For individual position breakeven, use check_break_even() instead.
        """
        breakeven = self.daily_start_balance
        buffer = breakeven * (self.breakeven_buffer_pct / 100)
        logger.info(
            f"Account breakeven calculated: ${breakeven:.2f} + ${buffer:.2f} buffer = ${breakeven + buffer:.2f}"
        )
        return breakeven + buffer

    def get_daily_pnl_pct(self) -> float:
        """Get daily P&L as percentage."""
        pnl = self.current_balance - self.daily_start_balance
        if self.daily_start_balance == 0:
            logger.warning("ProfitLock daily_start_balance is 0; returning 0.0 daily P&L%%")
            return 0.0
        return (pnl / self.daily_start_balance) * 100

    def get_daily_pnl_dollars(self) -> float:
        """Get daily P&L in dollars."""
        return self.current_balance - self.daily_start_balance

    def get_equity_curve_data(self) -> Dict:
        """Get equity curve statistics."""
        daily_pnl_pct = self.get_daily_pnl_pct()
        daily_pnl_dollars = self.get_daily_pnl_dollars()
        drawdown = self.peak_balance_today - self.current_balance
        drawdown_pct = 0.0
        if self.peak_balance_today != 0:
            drawdown_pct = (drawdown / self.peak_balance_today) * 100
        progress_to_target = 0.0
        if self.daily_profit_target_pct != 0:
            progress_to_target = min(100, (daily_pnl_pct / self.daily_profit_target_pct) * 100)
        
        return {
            "current_balance": self.current_balance,
            "starting_balance": self.daily_start_balance,
            "peak_balance_today": self.peak_balance_today,
            "daily_pnl_pct": daily_pnl_pct,
            "daily_pnl_dollars": daily_pnl_dollars,
            "current_drawdown": drawdown,
            "current_drawdown_pct": drawdown_pct,
            "profit_target_pct": self.daily_profit_target_pct,
            "progress_to_target": progress_to_target,
            "max_loss_threshold": -self.daily_max_loss_pct,
            "distance_to_max_loss": daily_pnl_pct - (-self.daily_max_loss_pct),
            "stops_locked": self.stops_adjusted,
            "positions_tracked": len(self.open_positions)
        }

    def reset_daily(self):
        """Reset daily tracking (call at start of each trading day)."""
        self.daily_start_balance = self.current_balance
        self.daily_start_time = datetime.now().replace(hour=0, minute=0, second=0)
        self.peak_balance_today = self.current_balance
        self.stops_adjusted = False
        self.open_positions = []
        
        logger.info(
            f"🔄 Daily reset: Starting balance ${self.daily_start_balance:.2f}"
        )

    def get_dashboard_summary(self) -> Dict:
        """Get summary for dashboard display."""
        equity_data = self.get_equity_curve_data()
        walk_away_status = self.walk_away.get_status()
        
        return {
            **equity_data,
            "walk_away": walk_away_status,
            "can_trade": walk_away_status["can_trade"],
            "locks_triggered_today": len(self.locks_triggered)
        }

    def get_stop_for_position(self, asset: str) -> Optional[float]:
        """Get current stop-loss for a tracked position."""
        for pos in self.open_positions:
            if pos["asset"] == asset:
                return pos["current_stop"]
        return None
