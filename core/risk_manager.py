"""Risk management utilities for Professor Mode autonomous trading."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class RiskPlan:
    action: str
    entry_price: float
    stop_loss: float
    risk_amount: float
    position_size: float
    source_level: Optional[float]


class RiskManager:
    """Calculate risk-based position sizes and stop-loss from S/R levels."""

    def __init__(self, risk_per_trade_pct: float = 1.0, fallback_sl_pct: float = 1.0):
        self.risk_per_trade_pct = max(0.1, risk_per_trade_pct)
        self.fallback_sl_pct = max(0.1, fallback_sl_pct)

    def derive_stop_loss(
        self,
        action: str,
        entry_price: float,
        levels: Optional[Dict[str, List[float]]] = None,
    ) -> tuple[float, Optional[float]]:
        """Derive stop loss using nearest support/resistance. Returns (sl, source_level)."""
        if entry_price <= 0:
            return 0.0, None

        levels = levels or {}
        supports = sorted(levels.get("supports", []))
        resistances = sorted(levels.get("resistances", []))

        side = action.upper()
        if side == "BUY":
            candidates = [x for x in supports if x < entry_price]
            if candidates:
                src = max(candidates)
                sl = src * 0.998
                return sl, src
            sl = entry_price * (1 - self.fallback_sl_pct / 100)
            return sl, None

        if side == "SELL":
            candidates = [x for x in resistances if x > entry_price]
            if candidates:
                src = min(candidates)
                sl = src * 1.002
                return sl, src
            sl = entry_price * (1 + self.fallback_sl_pct / 100)
            return sl, None

        sl = entry_price * (1 - self.fallback_sl_pct / 100)
        return sl, None

    def calculate_position_size(self, balance: float, entry_price: float, stop_loss: float) -> tuple[float, float]:
        """Return (risk_amount, quantity) based on 1% balance risk model."""
        if balance <= 0 or entry_price <= 0 or stop_loss <= 0:
            return 0.0, 0.0

        risk_amount = balance * (self.risk_per_trade_pct / 100)
        risk_per_unit = abs(entry_price - stop_loss)
        if risk_per_unit <= 0:
            return risk_amount, 0.0

        quantity = risk_amount / risk_per_unit
        return risk_amount, max(0.0, quantity)

    def build_plan(
        self,
        action: str,
        balance: float,
        entry_price: float,
        levels: Optional[Dict[str, List[float]]] = None,
    ) -> RiskPlan:
        stop_loss, src = self.derive_stop_loss(action, entry_price, levels)
        risk_amount, quantity = self.calculate_position_size(balance, entry_price, stop_loss)
        return RiskPlan(
            action=action.upper(),
            entry_price=entry_price,
            stop_loss=stop_loss,
            risk_amount=risk_amount,
            position_size=quantity,
            source_level=src,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Professor Mode — PositionSizer
# ─────────────────────────────────────────────────────────────────────────────

class PositionSizer:
    """Autonomous Professor position sizer.

    Rules:
    - Risk exactly 1% of ``balance`` per trade.
    - Stop-loss anchored to the nearest support (BUY) or resistance (SELL).
    - If the stop distance exceeds ``MAX_STOP_DISTANCE_PCT`` the trade is
      flagged *Too Risky* and ``evaluate()`` returns ``ok=False``.
    """

    MAX_STOP_DISTANCE_PCT: float = 5.0  # Hard cap; above this → rejected
    SL_BUFFER: float = 0.002            # 0.2% beyond S/R level

    def __init__(self, balance: float, risk_pct: float = 1.0):
        self.balance = max(0.0, balance)
        self.risk_pct = max(0.1, risk_pct)

    def evaluate(
        self,
        entry_price: float,
        side: str,
        levels: Optional[Dict[str, List[float]]] = None,
    ) -> dict:
        """Evaluate whether the trade meets the Professor's risk criteria.

        Returns a dict with keys:
          ok (bool), stop_loss (float), stop_distance_pct (float),
          risk_score ('Low'|'High'), risk_amount (float),
          quantity (float), reason (str)
        """
        if entry_price <= 0 or self.balance <= 0:
            return {
                "ok": False,
                "stop_loss": 0.0,
                "stop_distance_pct": 0.0,
                "risk_score": "High",
                "risk_amount": 0.0,
                "quantity": 0.0,
                "reason": "Invalid entry price or zero balance",
            }

        levels = levels or {}
        supports = sorted(levels.get("supports", []))
        resistances = sorted(levels.get("resistances", []))
        side_up = side.upper()

        # --- Derive stop-loss from nearest S/R ---
        stop_loss: Optional[float] = None
        if side_up == "BUY":
            candidates = [x for x in supports if x < entry_price]
            if candidates:
                stop_loss = max(candidates) * (1.0 - self.SL_BUFFER)
        elif side_up == "SELL":
            candidates = [x for x in resistances if x > entry_price]
            if candidates:
                stop_loss = min(candidates) * (1.0 + self.SL_BUFFER)

        # Fallback: 1% from entry
        if stop_loss is None:
            stop_loss = entry_price * (0.99 if side_up == "BUY" else 1.01)

        stop_distance_pct = abs(entry_price - stop_loss) / entry_price * 100.0

        # --- Too-Risky gate ---
        if stop_distance_pct > self.MAX_STOP_DISTANCE_PCT:
            return {
                "ok": False,
                "stop_loss": stop_loss,
                "stop_distance_pct": stop_distance_pct,
                "risk_score": "High",
                "risk_amount": 0.0,
                "quantity": 0.0,
                "reason": (
                    f"Too Risky: stop distance {stop_distance_pct:.1f}% "
                    f"exceeds maximum {self.MAX_STOP_DISTANCE_PCT:.0f}%"
                ),
            }

        # --- Size the position at exactly 1% risk ---
        risk_amount = self.balance * (self.risk_pct / 100.0)
        per_unit_risk = abs(entry_price - stop_loss)
        quantity = risk_amount / per_unit_risk if per_unit_risk > 0 else 0.0

        return {
            "ok": True,
            "stop_loss": stop_loss,
            "stop_distance_pct": stop_distance_pct,
            "risk_score": "Low",
            "risk_amount": risk_amount,
            "quantity": quantity,
            "reason": (
                f"Stop @ ${stop_loss:.4f} ({stop_distance_pct:.2f}% distance) "
                f"| Risk ${risk_amount:.2f}"
            ),
        }
