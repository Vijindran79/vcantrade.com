"""In-process broker simulator used when ``DRY_RUN=true``.

This is a duck-typed stand-in for :class:`app.client.TradovateClient`. It
implements only the methods ``app.main`` actually calls, so the full
webhook -> schema -> risk guard -> bracket construction -> order-submission
path runs unchanged while **zero** bytes go to Tradovate.

Two things make it more than a stub:

1. **Orders are recorded.** Every accepted order is appended to
   ``sim_orders.json`` next to the risk-state file, including the bracket legs
   (``stop_price`` / ``take_profit_price``) that were attached. The e2e harness
   asserts on this, which is how "the stop is attached" is proven without a
   broker.
2. **Naked entries are counted.** An entry that reaches the broker without a
   ``bracket.stopLoss`` increments ``naked_orders`` and logs at ERROR. The sim
   never rejects it (a real broker would accept it too) — the point is that the
   counter must stay at zero, which is the one invariant Apex cares about.

Never let this stand in for the real client on a funded account: ``DRY_RUN`` is
checked at startup and the process logs a loud warning.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

from .config import Settings
from .logging_setup import get_logger

log = get_logger(__name__)

MAX_RECORDED_ORDERS = 500


class SimBroker:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._lock = threading.RLock()
        self._next_id = 1
        self._orders: list[dict] = []
        self._positions: dict[str, int] = {}
        self._naked = 0
        self._path = Path(settings.state_file).parent / "sim_orders.json"
        self._load()

    # ------------------------------------------------------------- persistence
    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._orders = list(raw.get("orders", []))
            self._positions = {k: int(v) for k, v in (raw.get("positions") or {}).items()}
            self._next_id = int(raw.get("next_id", len(self._orders) + 1))
            self._naked = int(raw.get("naked_orders", 0))
            log.info(
                "sim broker restored %d order(s) from %s", len(self._orders), self._path
            )
        except Exception as exc:  # noqa: BLE001 - the sim must never break the app
            log.warning("could not read sim state %s (%s); starting empty", self._path, exc)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "orders": self._orders[-MAX_RECORDED_ORDERS:],
                "positions": self._positions,
                "next_id": self._next_id,
                "naked_orders": self._naked,
                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not persist sim state: %s", exc)

    # ------------------------------------------------- TradovateClient surface
    async def list_accounts(self) -> list[dict]:
        return [{"id": self._s.tradovate_account_id or 1, "name": "SIM-DRY-RUN"}]

    async def find_contract(self, name: str) -> Optional[dict]:
        return {"name": name, "id": 0}

    async def list_positions(self, account_id: int) -> list[dict]:
        with self._lock:
            return [
                {"symbol": sym, "pos": qty, "accountId": account_id}
                for sym, qty in sorted(self._positions.items())
                if qty != 0
            ]

    async def place_order(self, order: dict) -> dict:
        with self._lock:
            order_id = self._next_id
            self._next_id += 1

            action = str(order.get("action", ""))
            symbol = str(order.get("symbol", ""))
            qty = int(order.get("orderQty") or 0)
            bracket = order.get("bracket") or {}
            stop = (bracket.get("stopLoss") or {}).get("stopPrice")
            tp = (bracket.get("takeProfit") or {}).get("limitPrice")
            is_flatten = not bracket

            if is_flatten:
                self._positions[symbol] = 0
            else:
                signed = qty if action == "Buy" else -qty
                self._positions[symbol] = self._positions.get(symbol, 0) + signed
                if stop is None:
                    self._naked += 1
                    log.error(
                        "SIM NAKED ENTRY order_id=%d %s %s qty=%d — no stop attached "
                        "(this must never happen)",
                        order_id, action, symbol, qty,
                    )

            record = {
                "order_id": str(order_id),
                "ts_ms": int(time.time() * 1000),
                "action": action,
                "symbol": symbol,
                "quantity": qty,
                "order_type": order.get("orderType"),
                "is_automated": order.get("isAutomated"),
                "account_spec": order.get("accountSpec"),
                "account_id": order.get("accountId"),
                "is_flatten": is_flatten,
                "bracket_attached": stop is not None,
                "stop_price": stop,
                "take_profit_price": tp,
                "payload": order,
            }
            self._orders.append(record)
            self._save()

            log.info(
                "SIM ORDER ACCEPTED order_id=%d %s %s qty=%d stop=%s tp=%s bracket=%s",
                order_id, action, symbol, qty, stop, tp, stop is not None,
            )
            return {"orderId": order_id, "orderStatus": "Working"}

    async def cancel_order(self, order_id: str) -> dict:
        return {"orderId": int(order_id), "orderStatus": "Canceled"}

    async def close_position(self, account_id: int, symbol: str, side: str, qty: int) -> dict:
        return await self.place_order(
            {
                "accountSpec": self._s.tradovate_account_spec,
                "accountId": account_id,
                "action": side,
                "symbol": symbol,
                "orderQty": qty,
                "orderType": "Market",
                "isAutomated": True,
            }
        )

    # ------------------------------------------------------------ introspection
    def orders(self) -> list[dict]:
        with self._lock:
            return list(self._orders)

    def last_order(self) -> Optional[dict]:
        with self._lock:
            return self._orders[-1] if self._orders else None

    def naked_orders(self) -> int:
        with self._lock:
            return self._naked

    def positions(self) -> dict[str, int]:
        with self._lock:
            return {s: q for s, q in self._positions.items() if q != 0}

    def reset(self) -> None:
        with self._lock:
            self._orders.clear()
            self._positions.clear()
            self._naked = 0
            self._next_id = 1
            self._save()
        log.warning("sim broker state reset")

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "mode": "DRY_RUN",
                "orders": len(self._orders),
                "bracketed_entries": sum(
                    1 for o in self._orders if o.get("bracket_attached")
                ),
                "naked_orders": self._naked,
                "positions": {s: q for s, q in self._positions.items() if q != 0},
                "state_file": str(self._path),
                "recent": self._orders[-5:],
            }
