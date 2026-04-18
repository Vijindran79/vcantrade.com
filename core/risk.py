"""Professor Mode risk utilities.

Implements fixed-percent account risk sizing and support/resistance hard stops.
"""

from __future__ import annotations

from typing import Dict, List


def calculate_position_size(balance: float, risk_percent: float = 1) -> float:
    """Return dollar risk budget for a trade.

    Example: balance=5000, risk_percent=1 -> 50.0
    """
    safe_balance = max(0.0, float(balance or 0.0))
    safe_risk_percent = max(0.0, float(risk_percent or 0.0))
    return safe_balance * (safe_risk_percent / 100.0)


def _fallback_stop(entry_price: float, side: str, fallback_pct: float = 1.0) -> float:
    if side.upper() == "SELL":
        return entry_price * (1 + fallback_pct / 100.0)
    return entry_price * (1 - fallback_pct / 100.0)


def derive_hard_stop_loss(
    entry_price: float,
    side: str,
    levels: Dict[str, List[float]] | None,
) -> float:
    """Derive hard stop from S/R levels.

    BUY: nearest support below entry with a small buffer.
    SELL: nearest resistance above entry with a small buffer.
    """
    if entry_price <= 0:
        return 0.0

    levels = levels or {}
    supports = sorted(float(x) for x in levels.get("supports", []) if float(x) > 0)
    resistances = sorted(float(x) for x in levels.get("resistances", []) if float(x) > 0)

    side_up = side.upper()
    if side_up == "BUY":
        below = [price for price in supports if price < entry_price]
        if below:
            return max(below) * 0.998
        return _fallback_stop(entry_price, side_up)

    if side_up == "SELL":
        above = [price for price in resistances if price > entry_price]
        if above:
            return min(above) * 1.002
        return _fallback_stop(entry_price, side_up)

    return _fallback_stop(entry_price, side_up)


def build_hard_stop_plan(
    entry_price: float,
    side: str,
    levels: Dict[str, List[float]] | None,
) -> Dict[str, float]:
    """Return plan payload used by Professor execution flow."""
    stop_loss = derive_hard_stop_loss(entry_price=entry_price, side=side, levels=levels)
    return {
        "entry": float(entry_price or 0.0),
        "stop_loss": float(stop_loss or 0.0),
    }
