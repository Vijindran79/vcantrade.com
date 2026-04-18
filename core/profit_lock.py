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
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import json

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
        take_profit: float,
        position_size: float,
    ):
        """Add a position to tracking."""
        self.open_positions.append({
            "asset": asset,
            "entry_price": entry_price,
            "original_stop": stop_loss,
            "current_stop": stop_loss,
            "take_profit": take_profit,
            "position_size": position_size,
            "stop_locked": False,
            "timestamp": datetime.now().isoformat()
        })
        
        logger.info(
            f"📊 Position added to Profit Lock: "
            f"{asset} @ ${entry_price:.2f}, SL=${stop_loss:.2f}"
        )

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
        """Calculate breakeven balance level + buffer."""
        breakeven = self.daily_start_balance
        buffer = breakeven * (self.breakeven_buffer_pct / 100)
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
