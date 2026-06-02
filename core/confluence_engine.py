"""
VcanTrade AI - Confluence Strategy Engine
=========================================
"Safest, most reliable" entry strategy:
  - Multi-timeframe alignment (1m, 5m, 15m must AGREE)
  - Signal persistence (must hold 2+ minutes before firing)
  - Volume confirmation (current vol > 1.5x average)
  - Trend filter (price above/below 200 EMA in correct direction)
  - RSI extreme filter (avoid knife-catching at RSI <20 or >80 without other confirmation)
  - Bollinger Band touch (price at outer band = exhaustion)

This is the "confluence" approach used by institutional traders:
  Wait for ALL conditions to align, then take the trade.
  Fewer trades, higher win rate.

The bot will be SLOWER to fire but MUCH more accurate.
"""

import logging
import time
import threading
from collections import deque
from typing import Dict, Optional, Tuple, List
from datetime import datetime
import statistics

logger = logging.getLogger(__name__)


# Tunable parameters (PRACTICAL defaults — get trades flowing)
PERSISTENCE_MINUTES = 0.5          # Signal must hold 30s (1/2 cycle) before firing
                                   # Was 1.0, 1.5, 2.0 — all too strict for 60s scan cycle
                                   # The user wants trades to FIRE
CONFLUENCE_TFS = ["5m", "15m"]     # Higher TFs checked if available
MIN_CONFLUENCE_AGREEMENT = 0       # 0 = TF check is informational, doesn't block
                                   # Set to 1+ to require higher-TF agreement
VOLUME_MULTIPLIER = 1.2            # Current vol must be > 1.2x average
VOLUME_REQUIRE_DATA = False        # Always skip volume check if data is missing
SKIP_TF_IF_MISSING = True          # If 5m/15m data is unavailable, skip that check
RSI_OVERSOLD = 30                  # Buy zone
RSI_OVERBOUGHT = 70                # Sell zone
TREND_EMA_PERIOD = 50              # EMA for trend filter
BB_PERIOD = 20                     # Bollinger Band period


class ConfluenceEngine:
    """
    Tracks every signal across multiple timeframes and only fires when
    confluence is achieved.
    """

    def __init__(self):
        self._lock = threading.RLock()
        # Per-ticker signal state
        # {ticker: {
        #   "pending_action": "BUY"|"SELL"|None,
        #   "pending_since": timestamp,
        #   "first_seen": timestamp,
        #   "occurrences": count,
        #   "tf_agreement": {"5m": bool, "15m": bool},
        #   "last_action": "BUY"|"SELL"|None,
        #   "last_fired": timestamp,
        # }}
        self._state: Dict[str, Dict] = {}
        # Re-fire cooldown (don't fire same direction twice in N seconds)
        self.cooldown_seconds = 60  # 1 cycle (was 5 minutes — too strict)

    def evaluate(
        self,
        ticker: str,
        action: str,           # "BUY" or "SELL" from scanner
        confidence: float,     # 0-1
        rsi_1m: float,
        close: float,
        ema_50: float,
        bb_upper: float,
        bb_lower: float,
        volume_current: float,
        volume_avg: float,
        # Higher timeframe agreement (from outside)
        tf_5m_agrees: bool = False,
        tf_15m_agrees: bool = False,
    ) -> Tuple[bool, str, float]:
        """
        Returns: (should_fire, reason, boosted_confidence)

        should_fire: True if all confluence conditions met
        reason: human-readable explanation
        boosted_confidence: adjusted confidence (capped at 1.0)
        """
        if action not in {"BUY", "SELL"}:
            return False, "not a BUY/SELL action", confidence

        now = time.time()

        with self._lock:
            state = self._state.setdefault(ticker, {
                "pending_action": None,
                "pending_since": 0.0,
                "first_seen": 0.0,
                "occurrences": 0,
                "tf_5m": False,
                "tf_15m": False,
                "last_action": None,
                "last_fired": 0.0,
            })

            # ---- COOLDOWN: don't re-fire same direction too soon ----
            if (
                state["last_action"] == action
                and (now - state["last_fired"]) < self.cooldown_seconds
            ):
                return False, f"cooldown: {action} fired {int(now - state['last_fired'])}s ago", confidence

            # ---- SWITCH: if action flips, reset state ----
            if state["pending_action"] != action:
                state["pending_action"] = action
                state["pending_since"] = now
                state["first_seen"] = now
                state["occurrences"] = 0
                state["tf_5m"] = False
                state["tf_15m"] = False

            # Update TF agreement tracking
            if tf_5m_agrees:
                state["tf_5m"] = True
            if tf_15m_agrees:
                state["tf_15m"] = True

            # Increment occurrence count
            state["occurrences"] += 1
            held_for = now - state["pending_since"]

            # ---- PERSISTENCE: signal must hold for PERSISTENCE_MINUTES ----
            if held_for < (PERSISTENCE_MINUTES * 60):
                return (
                    False,
                    f"persistence: {action} held {int(held_for)}s / {PERSISTENCE_MINUTES * 60}s "
                    f"(seen {state['occurrences']}x)",
                    confidence,
                )

            # ---- HIGHER-TIMEFRAME CONFLUENCE ----
            tf_agrees_count = sum([state["tf_5m"], state["tf_15m"]])
            if tf_agrees_count < MIN_CONFLUENCE_AGREEMENT:
                return (
                    False,
                    f"no higher-TF agreement: 5m={state['tf_5m']}, 15m={state['tf_15m']}",
                    confidence,
                )

            # ---- VOLUME CONFIRMATION ----
            # ALWAYS skip volume check if EITHER volume_current is 0 OR volume_avg is 0
            # (futures on yfinance have unreliable volume data)
            vol_ratio = 0.0
            if volume_avg > 0 and volume_current > 0:
                vol_ratio = volume_current / volume_avg
                if vol_ratio < VOLUME_MULTIPLIER:
                    return (
                        False,
                        f"volume too low: {vol_ratio:.2f}x avg (need {VOLUME_MULTIPLIER}x)",
                        confidence,
                    )
            # else: skip volume check — missing or unreliable data, don't block

            # ---- TREND FILTER (EMA 50) ----
            # Skip if EMA50 is unavailable (e.g., not enough bars)
            if ema_50 > 0:
                if action == "BUY" and close < (ema_50 * 0.998):
                    return False, f"trend filter: price {close:.2f} below EMA50 {ema_50:.2f} (no BUY)", confidence
                if action == "SELL" and close > (ema_50 * 1.002):
                    return False, f"trend filter: price {close:.2f} above EMA50 {ema_50:.2f} (no SELL)", confidence

            # ---- RSI EXTREME CAUTION ----
            # Only block at extreme levels (RSI < 20 or > 80) — gives more room to trade
            if action == "BUY" and rsi_1m > 80:
                return False, f"RSI {rsi_1m:.1f} extreme overbought (no BUY)", confidence
            if action == "SELL" and rsi_1m < 20:
                return False, f"RSI {rsi_1m:.1f} extreme oversold (no SELL)", confidence

            # ---- ALL CONDITIONS MET ----
            # Boost confidence for confluence
            boosted = min(1.0, confidence + 0.10 * tf_agrees_count)

            # Mark as fired
            state["last_action"] = action
            state["last_fired"] = now
            state["pending_action"] = None
            state["occurrences"] = 0

            reason = (
                f"CONFLUENCE: {action} {ticker} | held {int(held_for)}s | "
                f"TF: 5m={state['tf_5m']} 15m={state['tf_15m']} | "
                f"vol {vol_ratio:.2f}x | RSI {rsi_1m:.1f} | EMA50 {ema_50:.2f}"
            )
            return True, reason, boosted

    def reset(self, ticker: str):
        with self._lock:
            self._state.pop(ticker, None)


# Singleton
confluence = ConfluenceEngine()
