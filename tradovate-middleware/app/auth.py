"""Tradovate access-token lifecycle.

Tradovate issues short-lived access tokens (~60 min). The token must be
refreshed proactively; a 401 mid-order is unacceptable. This module owns a
single token, refreshes it on a background timer, and exposes `get_token()`
that callers can await without worrying about expiry.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from .config import Settings
from .logging_setup import get_logger

log = get_logger(__name__)


class TradovateAuthError(RuntimeError):
    pass


class TokenManager:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self._s = settings
        self._client = client
        self._token: Optional[str] = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()
        self._refresh_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        await self._refresh()
        self._refresh_task = asyncio.create_task(self._refresh_loop(), name="tv-token-refresh")

    async def stop(self) -> None:
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except (asyncio.CancelledError, Exception):
                pass
            self._refresh_task = None

    async def get_token(self) -> str:
        async with self._lock:
            if not self._token or time.time() >= self._expires_at:
                await self._refresh_locked()
            return self._token  # type: ignore[return-value]

    async def _refresh_loop(self) -> None:
        while True:
            try:
                sleep_for = max(30.0, (self._expires_at - time.time()) - self._s.token_refresh_margin_sec)
                await asyncio.sleep(sleep_for)
                async with self._lock:
                    await self._refresh_locked()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("token refresh loop error: %s", exc)
                await asyncio.sleep(30)

    async def _refresh(self) -> None:
        async with self._lock:
            await self._refresh_locked()

    async def _refresh_locked(self) -> None:
        url = f"{self._s.tradovate_base_url}/auth/accessTokenRequest"
        payload = {
            "name": self._s.tradovate_cid,
            "appId": self._s.tradovate_app_id,
            "cid": self._s.tradovate_cid,
            "sec": self._s.tradovate_sec,
            "version": self._s.tradovate_app_version,
        }
        try:
            r = await self._client.post(url, json=payload, timeout=15.0)
        except httpx.HTTPError as exc:
            raise TradovateAuthError(f"network error contacting {url}: {exc}") from exc

        if r.status_code != 200:
            raise TradovateAuthError(
                f"accessTokenRequest failed: HTTP {r.status_code} {r.text[:300]}"
            )

        data = r.json()
        token = data.get("accessToken")
        if not token:
            raise TradovateAuthError(f"accessTokenRequest returned no token: {data}")

        expires_raw = data.get("expiresAt") or data.get("expiration")
        expires_at = self._parse_expiry(expires_raw)
        margin = self._s.token_refresh_margin_sec

        self._token = token
        self._expires_at = max(time.time() + 60.0, expires_at - margin)
        log.info(
            "tradovate token acquired env=%s expires_in_sec=%.0f",
            self._s.tradovate_env,
            self._expires_at - time.time(),
        )

    @staticmethod
    def _parse_expiry(raw) -> float:
        if raw is None:
            return time.time() + 3600.0
        if isinstance(raw, (int, float)):
            v = float(raw)
            return v / 1000.0 if v > 1e12 else v
        if isinstance(raw, str):
            try:
                s = raw.replace("Z", "+00:00")
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except ValueError:
                pass
        return time.time() + 3600.0
