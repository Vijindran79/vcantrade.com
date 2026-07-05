"""
VcaniTrade AI — Institutional U-Turn Reversal Engine
=====================================================

Detects imminent price reversals after strong upward surges,
particularly after breakouts above EMA20 driven by buyer
participation.

Designed for low-latency, high-accuracy detection with
minimal false positives.

DETECTION LAYERS (all must be evaluated on every tick):
  1. Price Action Reversal (candle patterns + structure)
  2. Volume Exhaustion / Distribution
  3. Momentum Divergence (RSI, MACD)
  4. Structural Break (swing low, EMA cross)
  5. Composite Score → weighted verdict

Each layer produces a score 0.0-1.0. Final verdict is
a weighted composite. Threshold adjustable.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

# How many recent candles to analyze
LOOKBACK_CANDLES = 20

# Minimum score to trigger reversal alert (0.0-1.0)
# Higher = fewer false positives, lower = catches more reversals
REVERSAL_THRESHOLD = 0.55

# Layer weights (must sum to 1.0)
WEIGHT_PRICE_ACTION = 0.30
WEIGHT_VOLUME = 0.20
WEIGHT_MOMENTUM = 0.25
WEIGHT_STRUCTURAL = 0.25

# RSI thresholds
RSI_OVERBOUGHT = 75
RSI_DIVERGENCE_LOOKBACK = 5

# Volume spike threshold (current vol / avg vol)
VOLUME_SPIKE_MULT = 1.8
VOLUME_DECLINE_BARS = 3

# EMA cross detection
EMA_FAST = 9
EMA_SLOW = 21


# ═══════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ReversalSignal:
    """Result of reversal analysis for one ticker."""
    ticker: str
    is_reversal: bool
    composite_score: float
    confidence: str  # "LOW", "MEDIUM", "HIGH", "EXTREME"
    price_action_score: float
    volume_score: float
    momentum_score: float
    structural_score: float
    reasons: List[str]
    timestamp: float = field(default_factory=time.time)

    def summary(self) -> str:
        if not self.is_reversal:
            return f"[REVERSAL] {self.ticker}: no reversal detected (score={self.composite_score:.2f})"
        return (
            f"[REVERSAL] {self.ticker}: {self.confidence} REVERSAL "
            f"(score={self.composite_score:.2f}) — {', '.join(self.reasons[:3])}"
        )


@dataclass
class SurgeState:
    """Tracks whether a ticker is in an active surge (pre-reversal context)."""
    ticker: str
    in_surge: bool = False
    surge_start_price: float = 0.0
    surge_peak_price: float = 0.0
    surge_start_time: float = 0.0
    bars_above_ema20: int = 0
    surge_high_volume: bool = False


# ═══════════════════════════════════════════════════════════════════
# REVERSAL ENGINE
# ═══════════════════════════════════════════════════════════════════

class ReversalEngine:
    """
    Institutional-grade U-turn reversal detector.

    Call ``analyze()`` on every tick with the ticker's OHLCV DataFrame.
    Returns a ReversalSignal with a composite score and detailed reasons.
    """

    def __init__(self, threshold: float = REVERSAL_THRESHOLD):
        self.threshold = threshold
        self._surge_states: Dict[str, SurgeState] = {}

    # ── Public API ────────────────────────────────────────────────

    def analyze(self, ticker: str, df: pd.DataFrame) -> ReversalSignal:
        """
        Run full reversal analysis on the given ticker's data.

        Args:
            ticker: Symbol name
            df: OHLCV DataFrame with columns [Open, High, Low, Close, Volume]

        Returns:
            ReversalSignal with composite score and reasons
        """
        if df is None or len(df) < LOOKBACK_CANDLES:
            return ReversalSignal(
                ticker=ticker, is_reversal=False, composite_score=0.0,
                confidence="LOW", price_action_score=0.0, volume_score=0.0,
                momentum_score=0.0, structural_score=0.0,
                reasons=["insufficient data"],
            )

        recent = df.tail(LOOKBACK_CANDLES).copy()
        close = recent["Close"].values
        high = recent["High"].values
        low = recent["Low"].values
        opn = recent["Open"].values
        vol = recent["Volume"].values if "Volume" in recent.columns else np.zeros(len(recent))

        current_price = float(close[-1])
        current_high = float(high[-1])
        current_low = float(low[-1])
        current_open = float(opn[-1])
        current_vol = float(vol[-1]) if len(vol) > 0 else 0.0

        # EMAs
        close_series = pd.Series(close)
        ema9 = close_series.ewm(span=EMA_FAST, adjust=False).mean().values
        ema20 = close_series.ewm(span=20, adjust=False).mean().values
        ema200 = close_series.ewm(span=min(200, len(close)), adjust=False).mean().values

        # RSI (14-period)
        rsi = self._calculate_rsi(close, 14)

        # MACD
        macd_line, signal_line, histogram = self._calculate_macd(close)

        # Average volume
        avg_vol = float(np.mean(vol[:-1])) if len(vol) > 1 else current_vol

        # Update surge state
        surge = self._update_surge_state(ticker, current_price, ema20, current_vol, avg_vol)

        reasons = []

        # ── Layer 1: Price Action Reversal ────────────────────────
        pa_score, pa_reasons = self._analyze_price_action(
            close, high, low, opn, ema9, ema20, current_price, current_open, current_high, current_low
        )
        reasons.extend(pa_reasons)

        # ── Layer 2: Volume Exhaustion / Distribution ─────────────
        vol_score, vol_reasons = self._analyze_volume(
            close, vol, high, low, current_price, current_vol, avg_vol
        )
        reasons.extend(vol_reasons)

        # ── Layer 3: Momentum Divergence ──────────────────────────
        mom_score, mom_reasons = self._analyze_momentum(
            close, rsi, histogram, ema9, current_price
        )
        reasons.extend(mom_reasons)

        # ── Layer 4: Structural Break ─────────────────────────────
        str_score, str_reasons = self._analyze_structure(
            close, high, low, ema9, ema20, ema200, current_price, rsi
        )
        reasons.extend(str_reasons)

        # ── Composite Score ───────────────────────────────────────
        composite = (
            WEIGHT_PRICE_ACTION * pa_score
            + WEIGHT_VOLUME * vol_score
            + WEIGHT_MOMENTUM * mom_score
            + WEIGHT_STRUCTURAL * str_score
        )

        # Boost score if we're in an active surge
        if surge.in_surge:
            surge_pts = surge.surge_peak_price - surge.surge_start_price
            if surge_pts > 0:
                giveback = (surge.surge_peak_price - current_price) / surge_pts
                if giveback > 0.3:
                    composite = min(1.0, composite + 0.15)
                    reasons.append(f"surge giveback {giveback*100:.0f}% from peak")
                if surge.surge_high_volume and vol_score > 0.3:
                    composite = min(1.0, composite + 0.10)
                    reasons.append("high-volume surge + volume exhaustion")

        # Determine confidence level
        if composite >= 0.80:
            confidence = "EXTREME"
        elif composite >= 0.65:
            confidence = "HIGH"
        elif composite >= 0.50:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        is_reversal = composite >= self.threshold

        signal = ReversalSignal(
            ticker=ticker,
            is_reversal=is_reversal,
            composite_score=round(composite, 3),
            confidence=confidence,
            price_action_score=round(pa_score, 3),
            volume_score=round(vol_score, 3),
            momentum_score=round(mom_score, 3),
            structural_score=round(str_score, 3),
            reasons=reasons,
        )

        if is_reversal:
            logger.warning("[REVERSAL] %s", signal.summary())

        return signal

    # ── Layer 1: Price Action ─────────────────────────────────────

    def _analyze_price_action(
        self, close, high, low, opn, ema9, ema20,
        price, cur_open, cur_high, cur_low
    ) -> Tuple[float, List[str]]:
        """Detect candle-based reversal patterns."""
        score = 0.0
        reasons = []

        body = abs(price - cur_open)
        candle_range = cur_high - cur_low if cur_high > cur_low else 0.001
        upper_wick = cur_high - max(price, cur_open)
        lower_wick = min(price, cur_open) - cur_low
        is_red = price < cur_open

        # 1. Bearish engulfing: red candle body > previous green body
        if len(close) >= 2:
            prev_body = abs(float(close[-2]) - float(opn[-2]))
            if is_red and body > prev_body * 1.2 and body > candle_range * 0.5:
                score += 0.35
                reasons.append("bearish engulfing candle")

        # 2. Shooting star / long upper wick rejection
        if upper_wick > body * 2.0 and upper_wick > lower_wick * 1.5:
            score += 0.25
            reasons.append(f"shooting star (wick={upper_wick:.1f}, body={body:.1f})")

        # 3. Three consecutive lower closes (micro-downtrend)
        if len(close) >= 4:
            if close[-1] < close[-2] < close[-3] < close[-4]:
                score += 0.20
                reasons.append("4 consecutive lower closes")

        # 4. Close below EMA9 after being above (momentum loss)
        if len(ema9) >= 2:
            if float(close[-2]) > float(ema9[-2]) and price < float(ema9[-1]):
                score += 0.20
                reasons.append("close below EMA9 (lost fast support)")

        # 5. Close below EMA20 after breakout above it
        if len(ema20) >= 2:
            if float(close[-2]) > float(ema20[-2]) and price < float(ema20[-1]):
                score += 0.30
                reasons.append("close below EMA20 after breakout — failed breakout")

        return min(1.0, score), reasons

    # ── Layer 2: Volume ───────────────────────────────────────────

    def _analyze_volume(
        self, close, vol, high, low, price, current_vol, avg_vol
    ) -> Tuple[float, List[str]]:
        """Detect volume exhaustion and distribution patterns."""
        score = 0.0
        reasons = []

        if avg_vol <= 0:
            return 0.0, []

        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0.0

        # 1. Volume spike on RED candle (distribution / smart money selling)
        is_red = price < float(close[-2]) if len(close) >= 2 else False
        if is_red and vol_ratio >= VOLUME_SPIKE_MULT:
            score += 0.40
            reasons.append(f"distribution: red candle with {vol_ratio:.1f}x avg volume")

        # 2. Declining volume on up moves (exhaustion)
        if len(vol) >= VOLUME_DECLINE_BARS + 1:
            recent_green_vols = []
            for i in range(-VOLUME_DECLINE_BARS, 0):
                if i < len(close) and float(close[i]) > float(close[i - 1]):
                    recent_green_vols.append(float(vol[i]))
            if len(recent_green_vols) >= 2:
                declining = all(
                    recent_green_vols[j] > recent_green_vols[j + 1]
                    for j in range(len(recent_green_vols) - 1)
                )
                if declining:
                    score += 0.30
                    reasons.append("volume declining on up moves (exhaustion)")

        # 3. High volume bar that failed to push price higher
        if len(close) >= 2 and len(vol) >= 2:
            if vol_ratio >= 1.5 and price <= float(close[-2]):
                score += 0.30
                reasons.append(f"high volume ({vol_ratio:.1f}x) but price didn't advance")

        # 4. Volume divergence: price at new high, volume at new low
        if len(close) >= 10 and len(vol) >= 10:
            price_high = float(np.max(close[-10:]))
            vol_low = float(np.min(vol[-10:]))
            if price >= price_high * 0.998 and current_vol <= vol_low * 1.2:
                score += 0.25
                reasons.append("volume divergence: price near high but volume collapsed")

        return min(1.0, score), reasons

    # ── Layer 3: Momentum ─────────────────────────────────────────

    def _analyze_momentum(
        self, close, rsi, histogram, ema9, price
    ) -> Tuple[float, List[str]]:
        """Detect momentum divergence and exhaustion."""
        score = 0.0
        reasons = []

        current_rsi = float(rsi[-1]) if len(rsi) > 0 else 50.0

        # 1. RSI overbought and turning down
        if len(rsi) >= 2:
            if current_rsi >= RSI_OVERBOUGHT and float(rsi[-1]) < float(rsi[-2]):
                score += 0.30
                reasons.append(f"RSI {current_rsi:.0f} turning down from overbought")

        # 2. RSI bearish divergence: price higher high, RSI lower high
        if len(close) >= RSI_DIVERGENCE_LOOKBACK + 1 and len(rsi) >= RSI_DIVERGENCE_LOOKBACK + 1:
            price_higher = float(close[-1]) > float(close[-(RSI_DIVERGENCE_LOOKBACK + 1)])
            rsi_lower = float(rsi[-1]) < float(rsi[-(RSI_DIVERGENCE_LOOKBACK + 1)])
            if price_higher and rsi_lower and current_rsi > 55:
                score += 0.35
                reasons.append(
                    f"RSI bearish divergence: price up but RSI down "
                    f"(RSI {float(rsi[-(RSI_DIVERGENCE_LOOKBACK+1)]):.0f} -> {current_rsi:.0f})"
                )

        # 3. MACD histogram turning negative after positive run
        if len(histogram) >= 3:
            h = [float(histogram[i]) for i in range(-3, 0)]
            if h[0] > 0 and h[1] > 0 and h[2] < 0:
                score += 0.25
                reasons.append("MACD histogram turned negative (bearish crossover)")

        # 4. MACD line crossing below signal line
        if len(close) >= 26:
            macd_line, signal_line, _ = self._calculate_macd(close)
            if len(macd_line) >= 2 and len(signal_line) >= 2:
                if (float(macd_line[-2]) > float(signal_line[-2]) and
                    float(macd_line[-1]) < float(signal_line[-1])):
                    score += 0.25
                    reasons.append("MACD bearish crossover (line below signal)")

        # 5. RSI extreme reading (>= 80) — very overextended
        if current_rsi >= 80:
            score += 0.15
            reasons.append(f"RSI {current_rsi:.0f} extremely overbought")

        return min(1.0, score), reasons

    # ── Layer 4: Structural ───────────────────────────────────────

    def _analyze_structure(
        self, close, high, low, ema9, ema20, ema200, price, rsi
    ) -> Tuple[float, List[str]]:
        """Detect structural breaks (swing lows, EMA crosses)."""
        score = 0.0
        reasons = []

        # 1. EMA9 crossing below EMA21 (bearish cross)
        if len(ema9) >= 2 and len(ema20) >= 2:
            if float(ema9[-2]) > float(ema20[-2]) and float(ema9[-1]) < float(ema20[-1]):
                score += 0.35
                reasons.append("EMA9 bearish cross below EMA20")

        # 2. Break below recent swing low
        if len(low) >= 6:
            swing_low = float(np.min(low[-6:-1]))  # lowest of previous 5 bars
            if price < swing_low:
                score += 0.30
                reasons.append(f"break below swing low {swing_low:.2f}")

        # 3. Price rejected from upper Bollinger Band
        if len(close) >= 20:
            bb_mid = float(np.mean(close[-20:]))
            bb_std = float(np.std(close[-20:]))
            bb_upper = bb_mid + 2 * bb_std
            if float(high[-1]) >= bb_upper and price < float(close[-2]):
                score += 0.20
                reasons.append(f"rejected from upper BB ({bb_upper:.2f})")

        # 4. Break below EMA200 after being above (major trend shift)
        if len(ema200) >= 2 and float(ema200[-1]) > 0:
            if float(close[-2]) > float(ema200[-2]) and price < float(ema200[-1]):
                score += 0.40
                reasons.append("break below EMA200 — major trend shift")

        # 5. Lower high pattern (failed to make new high)
        if len(high) >= 4:
            if float(high[-1]) < float(high[-2]) < float(high[-3]):
                score += 0.15
                reasons.append("lower highs pattern (3 bars)")

        return min(1.0, score), reasons

    # ── Surge State Tracking ──────────────────────────────────────

    def _update_surge_state(
        self, ticker: str, price: float, ema20, vol: float, avg_vol: float
    ) -> SurgeState:
        """Track whether this ticker is in an active price surge."""
        state = self._surge_states.get(ticker, SurgeState(ticker=ticker))
        ema20_val = float(ema20[-1]) if len(ema20) > 0 else 0.0

        if ema20_val <= 0:
            return state

        above_ema20 = price > ema20_val

        if above_ema20:
            if not state.in_surge:
                state.in_surge = True
                state.surge_start_price = price
                state.surge_peak_price = price
                state.surge_start_time = time.time()
                state.bars_above_ema20 = 1
            else:
                state.bars_above_ema20 += 1
                if price > state.surge_peak_price:
                    state.surge_peak_price = price
            if vol > avg_vol * 1.5:
                state.surge_high_volume = True
        else:
            # Price dropped below EMA20 — surge may be over
            if state.in_surge and state.bars_above_ema20 >= 3:
                # Was in surge, now below EMA20 — potential reversal confirmed
                pass
            elif not state.in_surge:
                pass
            # Reset if below EMA20 for 2+ bars
            if state.in_surge:
                state.bars_above_ema20 = 0

        self._surge_states[ticker] = state
        return state

    def clear_state(self, ticker: str):
        """Clear surge state for a ticker (call when position closes)."""
        self._surge_states.pop(ticker, None)

    # ── Technical Calculations ────────────────────────────────────

    @staticmethod
    def _calculate_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
        """Calculate RSI using exponential moving average method."""
        if len(close) < period + 1:
            return np.full(len(close), 50.0)

        deltas = np.diff(close)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        avg_gain = np.zeros(len(deltas))
        avg_loss = np.zeros(len(deltas))

        avg_gain[period - 1] = np.mean(gains[:period])
        avg_loss[period - 1] = np.mean(losses[:period])

        for i in range(period, len(deltas)):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i]) / period

        rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
        rsi = 100.0 - (100.0 / (1.0 + rs))

        # Pad the beginning with 50
        full_rsi = np.full(len(close), 50.0)
        full_rsi[1:] = rsi
        return full_rsi

    @staticmethod
    def _calculate_macd(
        close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Calculate MACD line, signal line, and histogram."""
        cs = pd.Series(close)
        ema_fast = cs.ewm(span=fast, adjust=False).mean().values
        ema_slow = cs.ewm(span=slow, adjust=False).mean().values
        macd_line = ema_fast - ema_slow
        signal_line = pd.Series(macd_line).ewm(span=signal, adjust=False).mean().values
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram


# ═══════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════

reversal_engine = ReversalEngine()
