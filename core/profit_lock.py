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
            f"[WALK] Walk Away Protocol initialized: "
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
            
            logger.critical(f"[STOP] WALK AWAY TRIGGERED: {self.shutdown_reason}")
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
                f"[OK] Walk Away Protocol cleared: "
                f"{elapsed_hours:.1f}h shutdown complete"
            )
            self.is_active = False
            self.shutdown_time = None
            self.shutdown_reason = ""
            return True
        else:
            remaining = self.shutdown_hours - elapsed_hours
            logger.warning(
                f"[WALK] Walk Away ACTIVE: {remaining:.1f}h remaining"
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

    POINT_VALUE_BY_ASSET = {
        "NQ": 20.0,
        "NQ=F": 20.0,
        "NQ1": 20.0,
        "MNQ": 2.0,
        "MNQ1": 2.0,
        "MNQ=F": 2.0,
        "CME_MINI:MNQ1": 2.0,
        "ES": 50.0,
        "ES=F": 50.0,
        "ES1": 50.0,
        "MES": 5.0,
        "MES=F": 5.0,
        "MES1": 5.0,
        "CME_MINI:MES1": 5.0,
        "CL": 100.0,
        "CL=F": 100.0,
        "CL1": 100.0,
        "NYMEX:CL1": 1000.0,
        "MCL": 100.0,
        "MCL=F": 100.0,
        "MCL1": 100.0,
        "NYMEX:MCL1": 100.0,
        "GC": 100.0,
        "GC=F": 100.0,
        "GC1": 100.0,
        "MGC": 10.0,
        "MGC=F": 10.0,
        "MGC1": 10.0,
        "COMEX:MGC1": 10.0,
        "XAUUSD": 10.0,
        "YM": 5.0,
        "YM=F": 5.0,
        "MYM": 0.5,
        "MYM=F": 0.5,
        "MYM1": 0.5,
        "CBOT_MINI:MYM1": 0.5,
        "RTY": 50.0,
        "RTY=F": 50.0,
        "M2K": 5.0,
        "M2K=F": 5.0,
        "M2K1": 5.0,
        "CME_MINI:M2K1": 5.0,
        "M6A": 10000.0,
        "6A=F": 10000.0,
        "M6A1": 10000.0,
        "CME:M6A1": 10000.0,
        "M6E": 12500.0,
        "6E=F": 12500.0,
        "M6E1": 12500.0,
        "CME:M6E1": 12500.0,
        "BTC-USD": 0.1,
        "MBT": 0.1,
        "MBT1": 0.1,
        "CME:MBT1": 0.1,
        "ETH-USD": 0.1,
        "MET": 0.1,
        "MET1": 0.1,
        "CME:MET1": 0.1,
    }

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
            f"[LOCK] Profit Lock initialized: "
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
        side: str = "BUY",
    ):
        """Add a position to tracking."""
        initial_risk_amount = abs(entry_price - stop_loss) * max(position_size, 0.0)
        self.open_positions.append({
            "asset": asset,
            "side": str(side or "BUY").upper(),
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
            f"[CHART] Position added to Profit Lock: "
            f"{asset} @ ${entry_price:.2f}, SL=${stop_loss:.2f}"
        )

    @classmethod
    def _point_value_for_asset(cls, asset: str) -> float:
        normalized_asset = str(asset or "").strip().upper().replace("!", "")
        return float(cls.POINT_VALUE_BY_ASSET.get(normalized_asset, 1.0))

    def _price_offset_for_dollars(self, position: Dict, dollars: float) -> float:
        """Convert a dollar objective into a price offset using contract value when known."""
        quantity = float(position.get("quantity", position.get("position_size", 0.0)) or 0.0)
        asset = str(position.get("asset", "") or "")
        point_value = float(position.get("point_value", self._point_value_for_asset(asset)) or 1.0)
        denominator = max(quantity * point_value, 0.0)
        if denominator <= 0:
            return 0.0
        return float(dollars) / denominator

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
        asset = str(position.get("asset", "") or "")
        point_value = float(position.get("point_value", self._point_value_for_asset(asset)) or 1.0)
        if entry_price <= 0 or quantity <= 0 or current_price <= 0:
            return 0.0
        if side == "SELL":
            return (entry_price - current_price) * quantity * point_value
        return (current_price - entry_price) * quantity * point_value

    def calculate_open_profit(self, position: Dict, current_price: float) -> float:
        """Public helper so the UI and stop engine share the same P&L math."""
        return self._current_profit_amount(position, current_price)

    def check_break_even(self, position: Dict, current_price: float) -> Optional[Dict]:
        """Raise the stop to entry plus the prop-firm lock amount once the trade is up enough."""
        entry_price = float(position.get("entry", position.get("entry_price", 0.0)) or 0.0)
        current_stop = float(position.get("sl_price", position.get("current_stop", 0.0)) or 0.0)
        side = str(position.get("side", "") or "").upper()
        break_even_locked = bool(position.get("break_even_locked"))

        if break_even_locked or entry_price <= 0 or side not in {"BUY", "SELL"}:
            return None

        current_profit = self._current_profit_amount(position, current_price)
        trigger_profit = max(0.0, float(getattr(config, "AUTONOMOUS_BREAK_EVEN_TRIGGER_USD", 15.0)))
        if current_profit < trigger_profit:
            return None

        lock_profit = max(0.0, float(getattr(config, "AUTONOMOUS_BREAK_EVEN_PLUS_USD", 2.0)))
        price_offset = self._price_offset_for_dollars(position, lock_profit)
        if price_offset <= 0:
            buffer_pct = max(0.0, float(config.AUTONOMOUS_BREAK_EVEN_BUFFER_PCT))
            price_offset = entry_price * (buffer_pct / 100.0)

        if side == "SELL":
            new_stop = entry_price - price_offset
            if current_stop > 0 and new_stop >= current_stop:
                return None
        else:
            new_stop = entry_price + price_offset
            if current_stop > 0 and new_stop <= current_stop:
                return None

        return {
            "new_stop": float(new_stop),
            "reason": "Prop-firm break-even lock",
            "break_even_locked": True,
            "stop_locked": True,
        }

    def calculate_structural_trailing_stop(
        self,
        position: Dict,
        recent_candles: Any,
        current_price: float,
    ) -> Optional[Dict]:
        """Trail behind fresh 1-minute structure once a new higher low or lower high prints."""
        if recent_candles is None:
            return None

        side = str(position.get("side", "") or "").upper()
        current_stop = float(position.get("sl_price", position.get("current_stop", 0.0)) or 0.0)
        if current_price <= 0 or side not in {"BUY", "SELL"}:
            return None

        candles = recent_candles
        try:
            if len(recent_candles.index) >= 3:
                candles = recent_candles.iloc[:-1]
        except Exception:
            candles = recent_candles

        if getattr(candles, "empty", True):
            return None

        if side == "SELL":
            if "High" not in candles:
                return None
            highs = candles["High"].dropna()
            if len(highs) < 2:
                return None
            previous_high = float(highs.iloc[-2])
            latest_high = float(highs.iloc[-1])
            if latest_high >= previous_high or latest_high <= current_price:
                return None
            if current_stop > 0 and latest_high >= current_stop:
                return None
            return {
                "new_stop": latest_high,
                "reason": "1m structural lower-high trail",
                "break_even_locked": False,
                "stop_locked": True,
            }

        if "Low" not in candles:
            return None
        lows = candles["Low"].dropna()
        if len(lows) < 2:
            return None

        previous_low = float(lows.iloc[-2])
        latest_low = float(lows.iloc[-1])
        if latest_low <= previous_low or latest_low >= current_price:
            return None
        if current_stop > 0 and latest_low <= current_stop:
            return None

        return {
            "new_stop": latest_low,
            "reason": "1m structural higher-low trail",
            "break_even_locked": False,
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
        PREDATOR-CLASS 3-BAR TRAILING STOP: Follow 1-minute candle lows/highs.
        
        MICRO-CONTRACT SHIELD LOGIC:
        - For LONG positions: Trail below the lowest low of last 3 candles
        - For SHORT positions: Trail above the highest high of last 3 candles
        - Only activates when trade is in profit
        - $2/point value for MNQ/MES contracts
        
        Args:
            position: Position dict with entry, side, current_stop
            recent_candles: DataFrame with 'Low' and 'High' columns (1-min candles)
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
            # SHORT position: Trail ABOVE the highest high of last N candles
            if "High" not in tail or tail["High"].dropna().empty:
                return None
            new_stop = float(tail["High"].dropna().max())
            # Stop must be above current price and tighter than existing stop
            if new_stop <= 0 or new_stop <= current_price:
                return None
            if current_stop > 0 and new_stop >= current_stop:
                return None
        else:
            # LONG position: Trail BELOW the lowest low of last N candles
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
            "reason": f"[SHIELD] {lookback_bars}-bar MNQ vacuum trail",
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
                    f"[LOCK] PROFIT LOCK TRIGGERED: Daily P&L {daily_pnl_pct:.2f}% >= {self.daily_profit_target_pct}% "
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
                    f"[LOCK] Stop locked for {pos['asset']}: "
                    f"${old_stop:.2f} -> ${breakeven_level:.2f}"
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
            f"[REFRESH] Daily reset: Starting balance ${self.daily_start_balance:.2f}"
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
