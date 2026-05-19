"""
VcanTrade AI - Market Regime Detector

Does the MATH before the LLM gets asked. No guessing.

This agent answers three questions with hard numbers:
1. Is the market TRENDING or CHOPPY? (ADX + directional movement)
2. What percentage of recent candles are GREEN vs RED? (momentum bias)
3. Is volatility EXPANDING or CONTRACTING? (ATR slope)

The output is a structured verdict that gets injected into every swarm
prompt so the LLM makes decisions based on facts, not vibes.

Logic:
- ADX > 25 = trending. ADX < 20 = choppy. Between = transitioning.
- Green candle % > 65% of last 20 bars = bullish bias.
- Red candle % > 65% of last 20 bars = bearish bias.
- ATR expanding (current > 20-bar avg) = volatility rising = wider stops needed.
- EMA20 > EMA50 > EMA200 = confirmed uptrend. Reverse = confirmed downtrend.

The regime verdict is: STRONG_BULL, LEAN_BULL, CHOPPY, LEAN_BEAR, STRONG_BEAR
Plus a numeric score from -100 (max bearish) to +100 (max bullish).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class RegimeVerdict:
    """Hard-number market regime assessment. No LLM involved."""

    regime: str  # STRONG_BULL, LEAN_BULL, CHOPPY, LEAN_BEAR, STRONG_BEAR
    score: float  # -100 to +100
    adx: float  # 0-100 (trend strength)
    green_pct: float  # 0-100 (% of green candles in lookback)
    red_pct: float  # 0-100
    ema_alignment: str  # BULL_STACK, BEAR_STACK, MIXED
    volatility_state: str  # EXPANDING, CONTRACTING, NORMAL
    atr_current: float
    atr_average: float
    trend_direction: str  # UP, DOWN, FLAT
    recommendation: str  # Human-readable one-liner

    def as_prompt_context(self) -> str:
        """Format for injection into LLM swarm prompts."""
        return (
            f"MARKET REGIME (calculated, not guessed):\n"
            f"  Regime: {self.regime} (score: {self.score:+.0f}/100)\n"
            f"  Trend Strength (ADX): {self.adx:.1f} ({'TRENDING' if self.adx > 25 else 'CHOPPY' if self.adx < 20 else 'TRANSITIONING'})\n"
            f"  Last 20 candles: {self.green_pct:.0f}% GREEN, {self.red_pct:.0f}% RED\n"
            f"  EMA Stack: {self.ema_alignment}\n"
            f"  Volatility: {self.volatility_state} (ATR {self.atr_current:.4f} vs avg {self.atr_average:.4f})\n"
            f"  Direction: {self.trend_direction}\n"
            f"  VERDICT: {self.recommendation}\n"
        )

    def as_dict(self) -> dict:
        return {
            "regime": self.regime,
            "score": self.score,
            "adx": self.adx,
            "green_pct": self.green_pct,
            "red_pct": self.red_pct,
            "ema_alignment": self.ema_alignment,
            "volatility_state": self.volatility_state,
            "atr_current": self.atr_current,
            "atr_average": self.atr_average,
            "trend_direction": self.trend_direction,
            "recommendation": self.recommendation,
        }


class RegimeDetector:
    """
    Pure-math market regime classifier. No LLM, no guessing.

    Feed it OHLCV data and it returns a hard verdict with numbers.
    """

    def __init__(self, adx_period: int = 14, ema_periods: tuple = (20, 50, 200), lookback: int = 20):
        self.adx_period = adx_period
        self.ema_periods = ema_periods
        self.lookback = lookback

    def analyze(self, df: pd.DataFrame) -> Optional[RegimeVerdict]:
        """Analyze OHLCV DataFrame and return regime verdict.

        Expects columns: Open, High, Low, Close, Volume (optional).
        Needs at least 200 rows for EMA200.
        """
        if df is None or len(df) < max(self.ema_periods):
            return None

        try:
            close = df["Close"].values.astype(float)
            high = df["High"].values.astype(float)
            low = df["Low"].values.astype(float)
            open_ = df["Open"].values.astype(float)

            # --- 1. ADX (trend strength) ---
            adx = self._calculate_adx(high, low, close)

            # --- 2. Green/Red candle percentage ---
            recent = min(self.lookback, len(df))
            recent_close = close[-recent:]
            recent_open = open_[-recent:]
            green_candles = np.sum(recent_close > recent_open)
            red_candles = np.sum(recent_close < recent_open)
            total_candles = max(1, recent)
            green_pct = (green_candles / total_candles) * 100.0
            red_pct = (red_candles / total_candles) * 100.0

            # --- 3. EMA alignment ---
            ema20 = self._ema(close, self.ema_periods[0])
            ema50 = self._ema(close, self.ema_periods[1])
            ema200 = self._ema(close, self.ema_periods[2])
            current_price = close[-1]

            if current_price > ema20 > ema50 > ema200:
                ema_alignment = "BULL_STACK"
            elif current_price < ema20 < ema50 < ema200:
                ema_alignment = "BEAR_STACK"
            elif current_price > ema50:
                ema_alignment = "LEAN_BULL"
            elif current_price < ema50:
                ema_alignment = "LEAN_BEAR"
            else:
                ema_alignment = "MIXED"

            # --- 4. ATR and volatility state ---
            atr_values = self._calculate_atr(high, low, close, period=14)
            atr_current = atr_values[-1] if len(atr_values) > 0 else 0.0
            atr_average = np.mean(atr_values[-20:]) if len(atr_values) >= 20 else atr_current
            if atr_current > atr_average * 1.3:
                volatility_state = "EXPANDING"
            elif atr_current < atr_average * 0.7:
                volatility_state = "CONTRACTING"
            else:
                volatility_state = "NORMAL"

            # --- 5. Trend direction (simple: price vs EMA20 slope) ---
            if len(close) >= 5:
                ema20_slope = ema20 - self._ema(close[:-3], self.ema_periods[0]) if len(close) > self.ema_periods[0] + 3 else 0
                price_vs_ema = current_price - ema20
                if price_vs_ema > 0 and ema20_slope > 0:
                    trend_direction = "UP"
                elif price_vs_ema < 0 and ema20_slope < 0:
                    trend_direction = "DOWN"
                else:
                    trend_direction = "FLAT"
            else:
                trend_direction = "FLAT"

            # --- 6. Composite score (-100 to +100) ---
            score = 0.0
            # ADX contribution (trending = stronger signal)
            if adx > 25:
                score += 15 if trend_direction == "UP" else -15 if trend_direction == "DOWN" else 0
            # Green/Red bias
            score += (green_pct - 50) * 0.8  # +40 max if 100% green, -40 if 100% red
            # EMA alignment
            ema_scores = {"BULL_STACK": 30, "LEAN_BULL": 15, "MIXED": 0, "LEAN_BEAR": -15, "BEAR_STACK": -30}
            score += ema_scores.get(ema_alignment, 0)
            # Clamp
            score = max(-100.0, min(100.0, score))

            # --- 7. Regime classification ---
            if score >= 50:
                regime = "STRONG_BULL"
            elif score >= 20:
                regime = "LEAN_BULL"
            elif score <= -50:
                regime = "STRONG_BEAR"
            elif score <= -20:
                regime = "LEAN_BEAR"
            else:
                regime = "CHOPPY"

            # --- 8. Recommendation ---
            if regime == "STRONG_BULL":
                recommendation = "STRONG BUY bias. All indicators aligned bullish. Look for pullback entries to go LONG."
            elif regime == "LEAN_BULL":
                recommendation = "Lean LONG. Trend is up but not fully confirmed. Smaller size, tighter stops."
            elif regime == "STRONG_BEAR":
                recommendation = "STRONG SELL bias. All indicators aligned bearish. Look for rally entries to go SHORT."
            elif regime == "LEAN_BEAR":
                recommendation = "Lean SHORT. Trend is down but not fully confirmed. Smaller size, tighter stops."
            else:
                if adx < 20:
                    recommendation = "CHOPPY — NO TRADE. ADX below 20, no clear trend. Wait for breakout."
                else:
                    recommendation = "MIXED signals. Reduce size or stand aside until regime clarifies."

            verdict = RegimeVerdict(
                regime=regime,
                score=score,
                adx=adx,
                green_pct=green_pct,
                red_pct=red_pct,
                ema_alignment=ema_alignment,
                volatility_state=volatility_state,
                atr_current=float(atr_current),
                atr_average=float(atr_average),
                trend_direction=trend_direction,
                recommendation=recommendation,
            )

            logger.info(
                "[REGIME] %s | Score: %+.0f | ADX: %.1f | Green: %.0f%% | EMAs: %s | Vol: %s | %s",
                regime, score, adx, green_pct, ema_alignment, volatility_state, recommendation[:60],
            )

            return verdict

        except Exception as e:
            logger.error("[REGIME] Analysis failed: %s", e)
            return None

    def _ema(self, data: np.ndarray, period: int) -> float:
        """Calculate EMA and return the last value."""
        if len(data) < period:
            return float(data[-1]) if len(data) > 0 else 0.0
        multiplier = 2.0 / (period + 1)
        ema = float(data[0])
        for price in data[1:]:
            ema = (float(price) - ema) * multiplier + ema
        return ema

    def _calculate_atr(self, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
        """Calculate ATR series."""
        if len(high) < 2:
            return np.array([0.0])
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:] - close[:-1])
            )
        )
        if len(tr) < period:
            return tr
        atr = np.zeros(len(tr))
        atr[period - 1] = np.mean(tr[:period])
        for i in range(period, len(tr)):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
        return atr[period - 1:]

    def _calculate_adx(self, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> float:
        """Calculate ADX (Average Directional Index)."""
        period = self.adx_period
        if len(high) < period * 2:
            return 0.0

        # Directional Movement
        up_move = high[1:] - high[:-1]
        down_move = low[:-1] - low[1:]

        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        # True Range
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1]))
        )

        # Smoothed averages
        def smooth(data, period):
            result = np.zeros(len(data))
            result[period - 1] = np.sum(data[:period])
            for i in range(period, len(data)):
                result[i] = result[i - 1] - (result[i - 1] / period) + data[i]
            return result

        smoothed_tr = smooth(tr, period)
        smoothed_plus = smooth(plus_dm, period)
        smoothed_minus = smooth(minus_dm, period)

        # Avoid division by zero
        smoothed_tr = np.where(smoothed_tr == 0, 1e-10, smoothed_tr)

        plus_di = 100.0 * smoothed_plus / smoothed_tr
        minus_di = 100.0 * smoothed_minus / smoothed_tr

        # DX
        di_sum = plus_di + minus_di
        di_sum = np.where(di_sum == 0, 1e-10, di_sum)
        dx = 100.0 * np.abs(plus_di - minus_di) / di_sum

        # ADX (smoothed DX)
        valid_dx = dx[period - 1:]
        if len(valid_dx) < period:
            return float(np.mean(valid_dx)) if len(valid_dx) > 0 else 0.0

        adx = np.zeros(len(valid_dx))
        adx[period - 1] = np.mean(valid_dx[:period])
        for i in range(period, len(valid_dx)):
            adx[i] = (adx[i - 1] * (period - 1) + valid_dx[i]) / period

        return float(adx[-1])
