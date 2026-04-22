"""
VcanTrade AI - Loose ATR Stop Loss Calculator

Calculates stop-loss distance based on 3-day volatility to prevent
premature stop-outs from market noise.
"""

import logging
from typing import Dict, List, Optional
import numpy as np

logger = logging.getLogger(__name__)


class LooseATRStops:
    """
    Loose ATR-Based Stop Loss Calculator.
    
    Philosophy: "Long Trip shouldn't be cut short by noise."
    Uses 3-day Average True Range to set stops that breathe with volatility.
    """

    def __init__(self, atr_period: int = 14, multiplier: float = 1.5):
        """
        Initialize ATR calculator.
        
        Args:
            atr_period: ATR calculation period (default 14)
            multiplier: ATR multiplier for stop distance (1.5 = loose)
        """
        self.atr_period = atr_period
        self.multiplier = multiplier
        self.atr_history = []  # Store recent ATR values
        
        logger.info(f"[RULER] Loose ATR Stops initialized: Period={atr_period}, Multiplier={multiplier}x")

    def calculate_atr(self, highs: List[float], lows: List[float], closes: List[float]) -> float:
        """
        Calculate Average True Range from price data.
        
        Args:
            highs: List of high prices (last N candles)
            lows: List of low prices
            closes: List of close prices
            
        Returns:
            Current ATR value
        """
        if len(highs) < 2:
            logger.warning("Not enough data for ATR calculation")
            return 0.0
        
        true_ranges = []
        for i in range(1, len(highs)):
            high_low = highs[i] - lows[i]
            high_close = abs(highs[i] - closes[i-1])
            low_close = abs(lows[i] - closes[i-1])
            
            true_range = max(high_low, high_close, low_close)
            true_ranges.append(true_range)
        
        # Calculate ATR (simple average of last N true ranges)
        recent_tr = true_ranges[-self.atr_period:]
        atr = np.mean(recent_tr)
        
        self.atr_history.append(atr)
        
        # Keep only last 3 days (for daily ATR tracking)
        if len(self.atr_history) > 3:
            self.atr_history = self.atr_history[-3:]
        
        return float(atr)

    def calculate_stop_loss(
        self,
        entry_price: float,
        atr: float,
        direction: str = "LONG",
        custom_multiplier: float = None,
    ) -> float:
        """
        Calculate loose stop-loss based on ATR.
        
        Args:
            entry_price: Entry price
            atr: Current ATR value
            direction: "LONG" or "SHORT"
            custom_multiplier: Override default multiplier
            
        Returns:
            Stop loss price
        """
        mult = custom_multiplier or self.multiplier
        stop_distance = atr * mult
        
        if direction == "LONG":
            stop_loss = entry_price - stop_distance
        else:  # SHORT
            stop_loss = entry_price + stop_distance
        
        logger.info(
            f"[RULER] Loose ATR Stop: Entry=${entry_price:.2f}, ATR={atr:.2f}, "
            f"Multiplier={mult}x, Stop=${stop_loss:.2f} (Distance: ${stop_distance:.2f})"
        )
        
        return stop_loss

    def calculate_take_profit(
        self,
        entry_price: float,
        atr: float,
        direction: str = "LONG",
        risk_reward_ratio: float = 2.0,
    ) -> float:
        """
        Calculate take-profit based on ATR with risk:reward ratio.
        
        Args:
            entry_price: Entry price
            atr: Current ATR value
            direction: "LONG" or "SHORT"
            risk_reward_ratio: Target R:R (default 2:1)
            
        Returns:
            Take profit price
        """
        stop_distance = atr * self.multiplier
        tp_distance = stop_distance * risk_reward_ratio
        
        if direction == "LONG":
            take_profit = entry_price + tp_distance
        else:  # SHORT
            take_profit = entry_price - tp_distance
        
        logger.info(
            f"[TARGET] ATR Take Profit: Entry=${entry_price:.2f}, "
            f"R:R={risk_reward_ratio}:1, TP=${take_profit:.2f}"
        )
        
        return take_profit

    def calculate_volatility_regime(self, atr: float, price: float) -> Dict:
        """
        Determine current volatility regime (Low/Medium/High).
        
        Args:
            atr: Current ATR
            price: Current price
            
        Returns:
            Dict with regime info
        """
        atr_pct = (atr / price) * 100
        
        if atr_pct < 1.0:
            regime = "LOW"
            note = "Quiet market - tight stops OK"
        elif atr_pct < 2.5:
            regime = "MEDIUM"
            note = "Normal volatility - standard stops"
        else:
            regime = "HIGH"
            note = "High volatility - use loose stops!"
        
        return {
            "atr": atr,
            "atr_percent": atr_pct,
            "regime": regime,
            "note": note,
            "recommended_multiplier": 2.0 if regime == "HIGH" else 1.5
        }

    def calculate_3day_volatility(self) -> Dict:
        """
        Calculate 3-day average volatility for stop calibration.
        
        Returns:
            Dict with 3-day volatility stats
        """
        if len(self.atr_history) < 3:
            return {
                "status": "INSUFFICIENT_DATA",
                "note": "Need 3 days of ATR history",
                "avg_atr": self.atr_history[-1] if self.atr_history else 0.0
            }
        
        recent_3d = self.atr_history[-3:]
        avg_atr = np.mean(recent_3d)
        max_atr = np.max(recent_3d)
        min_atr = np.min(recent_3d)
        
        return {
            "status": "CALIBRATED",
            "avg_atr_3d": float(avg_atr),
            "max_atr_3d": float(max_atr),
            "min_atr_3d": float(min_atr),
            "volatility_trend": "INCREASING" if recent_3d[-1] > recent_3d[0] else "DECREASING",
            "recommended_stop_multiplier": 2.0 if avg_atr > 1.5 else 1.5
        }
