"""
Hybrid low-latency execution gateway.

Priority:
1. Direct broker WebSocket when configured.
2. Local execution socket on port 5555.
3. MT5 direct order_send when available.
4. TradingView GhostExecutor JS click.

Legacy mouse RPA remains outside this gateway as the caller's final fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp

import config
from services.execution_socket_client import ExecutionSocketClient

logger = logging.getLogger(__name__)


@dataclass
class GatewayResult:
    success: bool
    route: str
    latency_ms: float
    message: str = ""


class BrokerWebSocketClient:
    """Minimal generic WebSocket broker adapter.

    The adapter sends a normalized JSON order packet. Real broker-specific
    signing and field names can be added behind this same interface once the
    endpoint is known.
    """

    def __init__(
        self,
        url: str,
        token: str = "",
        timeout_seconds: float = 0.75,
        dry_run: bool = True,
    ) -> None:
        self.url = (url or "").strip()
        self.token = (token or "").strip()
        self.timeout_seconds = float(timeout_seconds or 0.75)
        self.dry_run = bool(dry_run)

    @property
    def configured(self) -> bool:
        return bool(self.url)

    async def send_order(
        self,
        symbol: str,
        action: str,
        quantity: float,
        order_type: str = "MARKET",
    ) -> GatewayResult:
        started = time.perf_counter()
        if not self.configured:
            return GatewayResult(False, "broker_ws", 0.0, "BROKER_WS_URL not configured")

        payload = {
            "type": "order",
            "symbol": symbol,
            "side": action.upper(),
            "quantity": quantity,
            "order_type": order_type.upper(),
            "dry_run": self.dry_run,
            "client": "vcantrade",
            "sent_at": time.time(),
        }
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.ws_connect(self.url) as ws:
                    await ws.send_str(json.dumps(payload))
                    msg = await ws.receive(timeout=self.timeout_seconds)
                    latency_ms = (time.perf_counter() - started) * 1000.0
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        text = msg.data or ""
                        ok = "error" not in text.lower() and "reject" not in text.lower()
                        return GatewayResult(ok, "broker_ws", latency_ms, text[:300])
                    if msg.type == aiohttp.WSMsgType.CLOSED:
                        return GatewayResult(False, "broker_ws", latency_ms, "WebSocket closed")
                    if msg.type == aiohttp.WSMsgType.ERROR:
                        return GatewayResult(False, "broker_ws", latency_ms, str(ws.exception()))
                    return GatewayResult(True, "broker_ws", latency_ms, str(msg.type))
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000.0
            return GatewayResult(False, "broker_ws", latency_ms, str(exc))


class HybridExecutionGateway:
    def __init__(
        self,
        socket_client: Optional[ExecutionSocketClient] = None,
        mt5_executor=None,
        ghost_executor=None,
    ) -> None:
        self.socket_client = socket_client or ExecutionSocketClient()
        self.mt5_executor = mt5_executor
        self.ghost_executor = ghost_executor
        self.broker_ws = BrokerWebSocketClient(
            url=str(getattr(config, "BROKER_WS_URL", "")),
            token=str(getattr(config, "BROKER_WS_TOKEN", "")),
            timeout_seconds=float(getattr(config, "BROKER_WS_TIMEOUT_SECONDS", 0.75)),
            dry_run=bool(getattr(config, "BROKER_WS_DRY_RUN", True)),
        )

    async def execute(
        self,
        symbol: str,
        action: str,
        confidence: float,
        quantity: float = 0.0,
        browser_loop=None,
    ) -> GatewayResult:
        action = str(action or "").upper()
        if action not in {"BUY", "SELL", "CLOSE", "FLATTEN"}:
            return GatewayResult(False, "gateway", 0.0, f"Unsupported action {action}")

        if self.broker_ws.configured:
            result = await self.broker_ws.send_order(symbol, action, quantity or 0.0)
            if result.success:
                logger.info("[HYBRID] %s %s via broker_ws in %.2fms", action, symbol, result.latency_ms)
                return result
            logger.warning("[HYBRID] broker_ws failed for %s %s: %s", action, symbol, result.message)

        socket_result = self._execute_socket(action, confidence)
        if socket_result.success:
            return socket_result

        mt5_result = self._execute_mt5(symbol, action, quantity)
        if mt5_result.success:
            return mt5_result

        ghost_result = await self._execute_ghost(action, browser_loop)
        if ghost_result.success:
            return ghost_result

        return GatewayResult(
            False,
            "hybrid_gateway",
            0.0,
            "; ".join(
                part
                for part in [
                    socket_result.message,
                    mt5_result.message,
                    ghost_result.message,
                ]
                if part
            ),
        )

    def _execute_socket(self, action: str, confidence: float) -> GatewayResult:
        started = time.perf_counter()
        try:
            if action in {"CLOSE", "FLATTEN"}:
                ok = self.socket_client.send_flatten(confidence)
            else:
                ok = self.socket_client.send_trade_action(action, confidence)
            latency_ms = (time.perf_counter() - started) * 1000.0
            return GatewayResult(ok, "local_socket", latency_ms, "sent" if ok else "socket unavailable")
        except Exception as exc:
            return GatewayResult(False, "local_socket", (time.perf_counter() - started) * 1000.0, str(exc))

    def _execute_mt5(self, symbol: str, action: str, quantity: float) -> GatewayResult:
        started = time.perf_counter()
        executor = self.mt5_executor
        if executor is None:
            return GatewayResult(False, "mt5", 0.0, "MT5 executor unavailable")
        try:
            ok = executor.execute_trade(symbol, action, volume=quantity or None)
            latency_ms = (time.perf_counter() - started) * 1000.0
            return GatewayResult(ok, "mt5", latency_ms, "order_send" if ok else "MT5 rejected")
        except Exception as exc:
            return GatewayResult(False, "mt5", (time.perf_counter() - started) * 1000.0, str(exc))

    async def _execute_ghost(self, action: str, browser_loop=None) -> GatewayResult:
        started = time.perf_counter()
        ghost = self.ghost_executor
        if ghost is None:
            return GatewayResult(False, "tradingview_js", 0.0, "GhostExecutor unavailable")
        try:
            if browser_loop and not browser_loop.is_closed():
                future = asyncio.run_coroutine_threadsafe(ghost.execute_js(action), browser_loop)
                ok = future.result(timeout=5.0)
            else:
                ok = await ghost.execute_js(action)
            latency_ms = (time.perf_counter() - started) * 1000.0
            return GatewayResult(ok, "tradingview_js", latency_ms, "clicked" if ok else "JS click failed")
        except Exception as exc:
            return GatewayResult(False, "tradingview_js", (time.perf_counter() - started) * 1000.0, str(exc))
