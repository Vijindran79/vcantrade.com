"""
Hybrid Execution Gateway — thin compatibility shim over `core.surface_router.SurfaceRouter`.

The legacy "hybrid" gateway tried four routes (broker WebSocket, local socket,
MT5, TradingView JS) and silently fell back. That was a footgun: a single
ambiguous response could fire the same logical order on two surfaces.

The new design is one armed surface per click, controlled by
`config.ACTIVE_EXECUTION_SURFACE`:
  * "TRADINGVIEW" -> GhostExecutor JS click on TradingView Desktop
  * "MT5"         -> Native MT5 order_send

Existing call sites (`HybridExecutionGateway(...)`, `gateway.execute(...)`,
`GatewayResult`) keep working — the gateway just forwards to `SurfaceRouter`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from core.surface_router import SurfaceRouter, get_active_surface

logger = logging.getLogger(__name__)


@dataclass
class GatewayResult:
    success: bool
    route: str
    latency_ms: float
    message: str = ""


class HybridExecutionGateway:
    """Routes orders to whichever surface (TradingView or MT5) is currently armed."""

    def __init__(
        self,
        socket_client=None,  # accepted but ignored (Rithmic socket retired)
        mt5_executor=None,
        ghost_executor=None,
    ) -> None:
        self.router = SurfaceRouter(mt5_executor=mt5_executor, ghost_executor=ghost_executor)
        self._mt5_executor = mt5_executor
        self._ghost_executor = ghost_executor
        # Kept as attributes so legacy callers that read them keep working.
        self.socket_client = None
        self.mt5_executor = mt5_executor
        self.ghost_executor = ghost_executor

    # ------------------------------------------------------------------
    # Setters used by main.py when MT5/Ghost executors come online late
    # ------------------------------------------------------------------
    def set_mt5_executor(self, executor) -> None:
        self._mt5_executor = executor
        self.mt5_executor = executor
        self.router.update_executors(mt5_executor=executor)

    def set_ghost_executor(self, executor) -> None:
        self._ghost_executor = executor
        self.ghost_executor = executor
        self.router.update_executors(ghost_executor=executor)

    # ------------------------------------------------------------------
    # PUBLIC API (matches the legacy gateway signature)
    # ------------------------------------------------------------------
    async def execute(
        self,
        symbol: str,
        action: str,
        confidence: float = 0.0,
        quantity: float = 0.0,
        entry_price: float = 0.0,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        target: Optional[dict] = None,
        selectors: Optional[dict] = None,
        browser_loop=None,
    ) -> GatewayResult:
        """Send a single order to the armed surface."""
        result = self.router.execute(
            symbol=symbol,
            action=action,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
            browser_loop=browser_loop,
        )

        surface = result.surface
        if result.success:
            logger.info(
                "[GATEWAY] %s %s via %s in %.2fms — %s",
                action,
                symbol,
                surface,
                result.latency_ms,
                result.message,
            )
        else:
            logger.warning(
                "[GATEWAY] %s %s on %s failed in %.2fms — %s",
                action,
                symbol,
                surface,
                result.latency_ms,
                result.message,
            )

        return GatewayResult(
            success=result.success,
            route=surface,
            latency_ms=result.latency_ms,
            message=result.message,
        )

    # ------------------------------------------------------------------
    # Helpers retained for compatibility
    # ------------------------------------------------------------------
    def active_surface(self) -> str:
        return get_active_surface()
