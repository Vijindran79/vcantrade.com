"""
VcanTrade AI - Market Regime Detector
======================================
Detects bull, bear, sideways, high-volatility, and crisis regimes.
Adjusts strategy parameters based on current market conditions.
"""
import logging
from typing import Dict, List, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    STRONG_BULL = "STRONG_BULL"
    BULL = "BULL"
    SIDEWAYS = "SIDEWAYS"
    BEAR = "BEAR"
    STRONG_BEAR = "STRONG_BEAR"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    CRISIS = "CRISIS"
    UNKNOWN = "UNKNOWN"


class RegimeDetector:
    """
    Multi-factor regime classification.
    Uses trend (SMA), volatility (ATR), and momentum (RSI) to detect
    the current market regime. Adjusts strategy parameters accordingly.
    """

    def __init__(self):
        self.current_regime = MarketRegime.UNKNOWN
        self.regime_history: List[Dict] = []
        self.regime_params = {
            MarketRegime.STRONG_BULL: {
                "position_size_mult": 1.2, "stop_loss_mult": 1.5,
                "take_profit_mult": 2.0, "max_trades_per_day": 25,
                "preferred_action": "BUY", "confidence_boost": 1.1,
            },
            MarketRegime.BULL: {
                "position_size_mult": 1.0, "stop_loss_mult": 1.3,
                "take_profit_mult": 1.8, "max_trades_per_day": 20,
                "preferred_action": "BUY", "confidence_boost": 1.05,
            },
            MarketRegime.SIDEWAYS: {
                "position_size_mult": 0.6, "stop_loss_mult": 0.8,
                "take_profit_mult": 1.0, "max_trades_per_day": 10,
                "preferred_action": "BOTH", "confidence_boost": 1.0,
            },
            MarketRegime.BEAR: {
                "position_size_mult": 1.0, "stop_loss_mult": 1.3,
                "take_profit_mult": 1.8, "max_trades_per_day": 20,
                "preferred_action": "SELL", "confidence_boost": 1.05,
            },
            MarketRegime.STRONG_BEAR: {
                "position_size_mult": 1.2, "stop_loss_mult": 1.5,
                "take_profit_mult": 2.0, "max_trades_per_day": 25,
                "preferred_action": "SELL", "confidence_boost": 1.1,
            },
            MarketRegime.HIGH_VOLATILITY: {
                "position_size_mult": 0.5, "stop_loss_mult": 2.0,
                "take_profit_mult": 2.5, "max_trades_per_day": 8,
                "preferred_action": "BOTH", "confidence_boost": 0.9,
            },
            MarketRegime.CRISIS: {
                "position_size_mult": 0.0, "stop_loss_mult": 1.0,
                "take_profit_mult": 1.0, "max_trades_per_day": 0,
                "preferred_action": "NONE", "confidence_boost": 0.0,
            },
        }

    def detect(self, prices: List[float], volumes: Optional[List[float]] = None) -> MarketRegime:
        """
        Detect current market regime from price series.
        Returns regime + recommended parameters.
        """
        if len(prices) < 50:
            return MarketRegime.UNKNOWN

        try:
            import numpy as np
            arr = np.array(prices[-100:])

            # Trend: SMA20 vs SMA50 slope
            sma_20 = float(np.mean(arr[-20:]))
            sma_50 = float(np.mean(arr[-50:]))
            trend_pct = ((sma_20 - sma_50) / sma_50) * 100

            # Volatility: std dev of last 20 returns
            returns = np.diff(arr[-21:]) / arr[-21:-1]
            vol = float(np.std(returns)) * 100

            # Momentum: RSI(14)
            rsi = self._rsi(arr, 14)

            # Volume trend (if available)
            vol_trend = 0.0
            if volumes and len(volumes) >= 20:
                vol_arr = np.array(volumes[-20:])
                vol_trend = float((vol_arr[-1] - np.mean(vol_arr)) / (np.mean(vol_arr) + 1e-9))

            # Crisis detection: vol > 3% and big drop
            if vol > 3.0 and trend_pct < -2.0:
                regime = MarketRegime.CRISIS
            elif vol > 2.0:
                regime = MarketRegime.HIGH_VOLATILITY
            elif trend_pct > 3.0 and rsi > 65:
                regime = MarketRegime.STRONG_BULL
            elif trend_pct > 0.5 and rsi > 50:
                regime = MarketRegime.BULL
            elif trend_pct < -3.0 and rsi < 35:
                regime = MarketRegime.STRONG_BEAR
            elif trend_pct < -0.5 and rsi < 50:
                regime = MarketRegime.BEAR
            else:
                regime = MarketRegime.SIDEWAYS

            if regime != self.current_regime:
                logger.info("[REGIME] Shift: %s -> %s (trend=%.2f%%, vol=%.2f%%, rsi=%.0f)",
                            self.current_regime.value, regime.value, trend_pct, vol, rsi)
                self.current_regime = regime
                self.regime_history.append({
                    "regime": regime.value,
                    "trend_pct": trend_pct,
                    "vol_pct": vol,
                    "rsi": rsi,
                    "vol_trend": vol_trend,
                })
                if len(self.regime_history) > 200:
                    self.regime_history = self.regime_history[-200:]

            return regime
        except Exception as e:
            logger.error("[REGIME] Detection error: %s", e)
            return MarketRegime.UNKNOWN

    def get_params(self, regime: Optional[MarketRegime] = None) -> Dict:
        """Get recommended parameters for current regime."""
        r = regime or self.current_regime
        return self.regime_params.get(r, self.regime_params[MarketRegime.SIDEWAYS])

    def should_trade(self) -> bool:
        """Whether current regime allows trading."""
        params = self.get_params()
        return params["max_trades_per_day"] > 0 and params["position_size_mult"] > 0

    def filter_signal(self, action: str, confidence: float) -> Dict:
        """
        Filter and adjust signal based on regime.
        Returns: {allow, adjusted_confidence, size_mult, sl_mult, tp_mult}
        """
        params = self.get_params()
        action = action.upper()

        # Check preferred action
        preferred = params["preferred_action"]
        if preferred != "BOTH" and preferred != "NONE" and action != preferred:
            return {
                "allow": False,
                "reason": f"Regime {self.current_regime.value} prefers {preferred} signals",
                "adjusted_confidence": 0.0,
            }

        if preferred == "NONE":
            return {
                "allow": False,
                "reason": f"Regime {self.current_regime.value} — NO TRADING",
                "adjusted_confidence": 0.0,
            }

        adj_conf = min(1.0, confidence * params["confidence_boost"])
        return {
            "allow": True,
            "adjusted_confidence": adj_conf,
            "size_mult": params["position_size_mult"],
            "sl_mult": params["stop_loss_mult"],
            "tp_mult": params["take_profit_mult"],
        }

    @staticmethod
    def _rsi(prices, period: int = 14) -> float:
        try:
            import numpy as np
            if len(prices) < period + 1:
                return 50.0
            deltas = np.diff(prices[-(period + 1):])
            gains = np.where(deltas > 0, deltas, 0)
            losses = np.where(deltas < 0, -deltas, 0)
            avg_gain = np.mean(gains)
            avg_loss = np.mean(losses)
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            return float(100 - (100 / (1 + rs)))
        except Exception:
            return 50.0


# Singleton
regime_detector = RegimeDetector()
