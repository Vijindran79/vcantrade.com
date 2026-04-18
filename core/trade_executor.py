"""Smart order execution for Professor Mode.

Uses exchange API when available, otherwise runs in simulated mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class OrderResult:
    status: str
    symbol: str
    side: str
    order_type: str
    price: float
    quantity: float
    order_id: str
    simulated: bool
    message: str


class TradeExecutor:
    """Exchange-facing smart executor with nearest S/R limit entries."""

    def __init__(self, exchange_client=None):
        self.exchange_client = exchange_client

    @staticmethod
    def _nearest_level(side: str, fallback_price: float, levels: Optional[Dict[str, List[float]]]) -> float:
        levels = levels or {}
        supports = sorted(levels.get("supports", []))
        resistances = sorted(levels.get("resistances", []))

        if side.upper() == "BUY":
            below = [x for x in supports if x <= fallback_price]
            if below:
                return max(below)
            return fallback_price

        if side.upper() == "SELL":
            above = [x for x in resistances if x >= fallback_price]
            if above:
                return min(above)
            return fallback_price

        return fallback_price

    def place_smart_entry(
        self,
        symbol: str,
        side: str,
        quantity: float,
        levels: Optional[Dict[str, List[float]]],
        fallback_price: float,
    ) -> OrderResult:
        """Place a LIMIT order at nearest S/R (support for buy, resistance for sell)."""
        limit_price = self._nearest_level(side, fallback_price, levels)
        order_id = f"sim_{symbol}_{int(datetime.utcnow().timestamp())}"

        if self.exchange_client and hasattr(self.exchange_client, "create_order"):
            response = self.exchange_client.create_order(
                symbol=symbol,
                side=side.upper(),
                order_type="LIMIT",
                quantity=quantity,
                price=limit_price,
            )
            order_id = str(response.get("id", order_id))
            return OrderResult(
                status="submitted",
                symbol=symbol,
                side=side.upper(),
                order_type="LIMIT",
                price=limit_price,
                quantity=quantity,
                order_id=order_id,
                simulated=False,
                message="Order sent to exchange API",
            )

        return OrderResult(
            status="simulated",
            symbol=symbol,
            side=side.upper(),
            order_type="LIMIT",
            price=limit_price,
            quantity=quantity,
            order_id=order_id,
            simulated=True,
            message="No exchange client configured; simulated limit order",
        )
