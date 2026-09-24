"""Bracket-order construction for Tradovate.

Every entry is submitted with `bracket.stopLoss` (and optionally
`bracket.takeProfit`) in the SAME /order call. Tradovate attaches the
protective legs atomically at the broker — if the bracket is rejected, the
entry is rejected too. There is no window where a position exists naked.

Reference: Tradovate API /order schema, `bracket` field.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import Settings
from .symbols import ContractSpec


class OrderConstructionError(ValueError):
    pass


@dataclass
class BracketOrder:
    payload: dict
    stop_price: float
    take_profit_price: Optional[float]


def _round_to_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    return round(round(price / tick) * tick, 10)


def build_entry(
    *,
    settings: Settings,
    spec: ContractSpec,
    action: str,
    quantity: int,
    entry_price: Optional[float] = None,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
) -> BracketOrder:
    if action not in ("BUY", "SELL"):
        raise OrderConstructionError(f"unsupported entry action: {action}")
    if quantity <= 0:
        raise OrderConstructionError("quantity must be > 0")

    tick = spec.tick_size

    if stop_loss is None:
        if settings.require_explicit_stop:
            raise OrderConstructionError(
                "signal omitted stop_loss and REQUIRE_EXPLICIT_STOP=true"
            )
        if entry_price is None:
            raise OrderConstructionError(
                "cannot derive default stop without entry_price"
            )
        offset = settings.default_stop_ticks * tick
        stop_loss = entry_price - offset if action == "BUY" else entry_price + offset

    stop_price = _round_to_tick(float(stop_loss), tick)

    if action == "BUY" and stop_price >= (entry_price or float("inf")):
        raise OrderConstructionError(
            f"BUY stop {stop_price} must be below entry {entry_price}"
        )
    if action == "SELL" and entry_price is not None and stop_price <= entry_price:
        raise OrderConstructionError(
            f"SELL stop {stop_price} must be above entry {entry_price}"
        )

    tp_price: Optional[float] = None
    if take_profit is not None:
        tp_price = _round_to_tick(float(take_profit), tick)
        if action == "BUY" and entry_price is not None and tp_price <= entry_price:
            raise OrderConstructionError("BUY take_profit must be above entry")
        if action == "SELL" and entry_price is not None and tp_price >= entry_price:
            raise OrderConstructionError("SELL take_profit must be below entry")

    bracket: dict = {"stopLoss": {"orderQty": quantity, "stopPrice": stop_price}}
    if tp_price is not None:
        bracket["takeProfit"] = {"orderQty": quantity, "limitPrice": tp_price}

    payload = {
        "accountSpec": settings.tradovate_account_spec,
        "accountId": settings.tradovate_account_id,
        "action": "Buy" if action == "BUY" else "Sell",
        "symbol": spec.tradovate_symbol,
        "orderQty": quantity,
        "orderType": "Market",
        "isAutomated": True,
        "bracket": bracket,
    }
    return BracketOrder(payload=payload, stop_price=stop_price, take_profit_price=tp_price)


def build_flatten(
    *,
    settings: Settings,
    spec: ContractSpec,
    side: str,
    quantity: int,
) -> dict:
    if side not in ("Buy", "Sell"):
        raise OrderConstructionError(f"flatten side must be Buy or Sell, got {side}")
    if quantity <= 0:
        raise OrderConstructionError("flatten quantity must be > 0")
    return {
        "accountSpec": settings.tradovate_account_spec,
        "accountId": settings.tradovate_account_id,
        "action": side,
        "symbol": spec.tradovate_symbol,
        "orderQty": quantity,
        "orderType": "Market",
        "isAutomated": True,
    }
