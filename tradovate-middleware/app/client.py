"""Thin async REST client for Tradovate.

Only the endpoints the middleware needs:
  GET  /account/list
  GET  /contract/find?name=
  GET  /position/list?accountId=
  POST /order
  POST /order/cancelOrder

Every call attaches the current access token, retries once on 401 (token
refresh), and surfaces non-2xx as TradovateAPIError with the broker's message.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from .auth import TokenManager
from .config import Settings
from .logging_setup import get_logger

log = get_logger(__name__)


class TradovateAPIError(RuntimeError):
    def __init__(self, status: int, body: str, endpoint: str):
        super().__init__(f"{endpoint} -> HTTP {status}: {body[:400]}")
        self.status = status
        self.body = body
        self.endpoint = endpoint


class TradovateClient:
    def __init__(self, settings: Settings, token_manager: TokenManager, client: httpx.AsyncClient):
        self._s = settings
        self._tm = token_manager
        self._client = client
        self.base = settings.tradovate_base_url

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        timeout: float = 10.0,
    ) -> Any:
        url = f"{self.base}{path}"
        for attempt in (0, 1):
            token = await self._tm.get_token()
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            t0 = time.perf_counter()
            try:
                r = await self._client.request(
                    method, url, json=json, params=params, headers=headers, timeout=timeout
                )
            except httpx.HTTPError as exc:
                raise TradovateAPIError(0, f"network error: {exc}", path) from exc
            dt_ms = (time.perf_counter() - t0) * 1000.0

            if r.status_code == 401 and attempt == 0:
                log.warning("401 from %s — forcing token refresh", path)
                await self._tm._refresh()
                continue

            if r.status_code >= 400:
                raise TradovateAPIError(r.status_code, r.text, path)

            log.info(
                "tradovate %s %s -> %d in %.1fms",
                method, path, r.status_code, dt_ms,
            )
            if not r.content:
                return None
            ctype = r.headers.get("content-type", "")
            return r.json() if "json" in ctype else r.text
        raise TradovateAPIError(401, "auth failed after refresh", path)

    async def list_accounts(self) -> list[dict]:
        data = await self._request("GET", "/account/list")
        return data or []

    async def find_contract(self, name: str) -> Optional[dict]:
        return await self._request("GET", "/contract/find", params={"name": name})

    async def list_positions(self, account_id: int) -> list[dict]:
        data = await self._request("GET", "/position/list", params={"accountId": account_id})
        return data or []

    async def place_order(self, order: dict) -> dict:
        return await self._request("POST", "/order", json=order, timeout=8.0)

    async def cancel_order(self, order_id: str) -> dict:
        return await self._request("POST", "/order/cancelOrder", json={"orderId": int(order_id)})

    async def close_position(self, account_id: int, symbol: str, side: str, qty: int) -> dict:
        order = {
            "accountSpec": self._s.tradovate_account_spec,
            "accountId": account_id,
            "action": side,
            "symbol": symbol,
            "orderQty": qty,
            "orderType": "Market",
            "isAutomated": True,
        }
        return await self.place_order(order)
