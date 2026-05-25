"""
VcanTrade AI - Surface Router

Single source of truth for "where do orders go" with one runtime switch.

Two surfaces only:
  * TRADINGVIEW  -> GhostExecutor JS injection on TradingView Desktop (paper / live)
  * MT5          -> Native MetaTrader 5 order_send via MT5Executor

The switch is a plain string on `config.ACTIVE_EXECUTION_SURFACE`. The dashboard
buttons flip it; this router reads it on every order. There is no fallback
chain across surfaces — if the active surface is unavailable, the order is
rejected so the operator sees the failure instead of silently spraying orders
across two platforms.

Apex Trader Funding compatibility:
  * `surface == "MT5"` is for the brother's MT5 Apex account.
  * `surface == "TRADINGVIEW"` is for the user's TradingView paper / Apex bridge.

Latency design:
  * No yfinance call inside the order path.
  * No multi-route racing.
  * MT5 path uses `mt5.symbol_info_tick` (microseconds) for live price.
  * TradingView path skips the live price probe entirely because the JS click
    fires at the visible bid/ask shown in the trade panel.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import config

logger = logging.getLogger(__name__)


SURFACE_TRADINGVIEW = "TRADINGVIEW"
SURFACE_MT5 = "MT5"
VALID_SURFACES = {SURFACE_TRADINGVIEW, SURFACE_MT5}


@dataclass
class RouteResult:
    success: bool
    surface: str
    latency_ms: float
    message: str = ""
    order_id: str = ""


def get_active_surface() -> str:
    """Return the currently-armed execution surface."""
    raw = str(getattr(config, "ACTIVE_EXECUTION_SURFACE", "") or "").strip().upper()
    if raw in VALID_SURFACES:
        return raw
    # Back-compat: older configs used EXECUTION_MODE
    legacy = str(getattr(config, "EXECUTION_MODE", "") or "").strip().upper()
    if legacy == "MT5":
        return SURFACE_MT5
    return SURFACE_TRADINGVIEW


def set_active_surface(surface: str) -> str:
    """Flip the active surface. Returns the resolved surface name."""
    target = str(surface or "").strip().upper()
    if target not in VALID_SURFACES:
        target = SURFACE_TRADINGVIEW
    config.ACTIVE_EXECUTION_SURFACE = target
    config.EXECUTION_MODE = "MT5" if target == SURFACE_MT5 else "UI"
    config.TRADING_SURFACE = "METATRADER_5" if target == SURFACE_MT5 else "TRADINGVIEW_DESKTOP"
    logger.info("[ROUTER] Active execution surface set to %s", target)
    return target


class SurfaceRouter:
    """Routes every order to exactly one surface based on the switch."""

    def __init__(self, mt5_executor=None, ghost_executor=None) -> None:
        self.mt5_executor = mt5_executor
        self.ghost_executor = ghost_executor

    def update_executors(self, *, mt5_executor=None, ghost_executor=None) -> None:
        if mt5_executor is not None:
            self.mt5_executor = mt5_executor
        if ghost_executor is not None:
            self.ghost_executor = ghost_executor

    # ------------------------------------------------------------------
    # MAIN ORDER PATH
    # ------------------------------------------------------------------
    def execute(
        self,
        symbol: str,
        action: str,
        quantity: float = 0.0,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        browser_loop=None,
    ) -> RouteResult:
        """Send a single order to the armed surface. No fallback to the other surface."""
        action_up = str(action or "").upper()
        if action_up not in {"BUY", "SELL", "CLOSE", "FLATTEN"}:
            return RouteResult(False, "router", 0.0, f"Unsupported action: {action_up}")

        surface = get_active_surface()
        started = time.perf_counter()

        if surface == SURFACE_MT5:
            return self._execute_mt5(
                symbol=symbol,
                action=action_up,
                quantity=quantity,
                stop_loss=stop_loss,
                take_profit=take_profit,
                started=started,
            )

        return self._execute_tradingview(
            action=action_up,
            browser_loop=browser_loop,
            started=started,
        )

    # ------------------------------------------------------------------
    # MT5 ROUTE (brother's machine)
    # ------------------------------------------------------------------
    def _execute_mt5(
        self,
        symbol: str,
        action: str,
        quantity: float,
        stop_loss: float,
        take_profit: float,
        started: float,
    ) -> RouteResult:
        executor = self.mt5_executor
        if executor is None:
            latency = (time.perf_counter() - started) * 1000.0
            return RouteResult(
                False,
                SURFACE_MT5,
                latency,
                "MT5 executor not initialised. Open MetaTrader 5 and switch the dashboard to MT5 mode.",
            )

        action_for_executor = "SELL" if action == "FLATTEN" else action
        if action == "CLOSE":
            # CLOSE means close current position. MT5Executor doesn't have a
            # direct close; the position-management loop handles it. For an
            # explicit user-driven close, an opposite market order is sent at
            # the configured volume.
            action_for_executor = "SELL"

        try:
            ok = bool(
                executor.execute_trade(
                    symbol,
                    action_for_executor,
                    volume=float(quantity) if quantity and quantity > 0 else None,
                    stop_loss=float(stop_loss or 0.0),
                    take_profit=float(take_profit or 0.0),
                )
            )
            latency = (time.perf_counter() - started) * 1000.0
            if ok:
                return RouteResult(True, SURFACE_MT5, latency, "MT5 order_send accepted")
            return RouteResult(False, SURFACE_MT5, latency, "MT5 rejected the order; see MT5 log")
        except Exception as exc:  # noqa: BLE001
            latency = (time.perf_counter() - started) * 1000.0
            logger.exception("[ROUTER] MT5 order failed: %s", exc)
            return RouteResult(False, SURFACE_MT5, latency, f"MT5 exception: {exc}")

    # ------------------------------------------------------------------
    # TRADINGVIEW ROUTE (your machine)
    # ------------------------------------------------------------------
    def _execute_tradingview(
        self,
        action: str,
        browser_loop,
        started: float,
    ) -> RouteResult:
        ghost = self.ghost_executor
        if ghost is None:
            latency = (time.perf_counter() - started) * 1000.0
            return RouteResult(
                False,
                SURFACE_TRADINGVIEW,
                latency,
                "GhostExecutor not initialised. Launch TradingView Desktop with --remote-debugging-port=9222.",
            )

        if not getattr(ghost, "is_connected", False):
            latency = (time.perf_counter() - started) * 1000.0
            return RouteResult(
                False,
                SURFACE_TRADINGVIEW,
                latency,
                "TradingView Desktop CDP is not connected on port 9222.",
            )

        try:
            if browser_loop is not None and not browser_loop.is_closed():
                import asyncio

                future = asyncio.run_coroutine_threadsafe(ghost.execute_js(action), browser_loop)
                ok = bool(future.result(timeout=15.0))
            else:
                # Fallback if a loop is unavailable: drive the coroutine to
                # completion synchronously. This blocks the caller for at most
                # the JS click duration (sub-second).
                import asyncio

                ok = bool(asyncio.run(ghost.execute_js(action)))
            latency = (time.perf_counter() - started) * 1000.0
            if ok:
                return RouteResult(True, SURFACE_TRADINGVIEW, latency, "TradingView JS click landed")
            return RouteResult(False, SURFACE_TRADINGVIEW, latency, "TradingView JS click failed (button not found)")
        except Exception as exc:  # noqa: BLE001
            latency = (time.perf_counter() - started) * 1000.0
            logger.exception("[ROUTER] TradingView JS click failed: %s", exc)
            return RouteResult(False, SURFACE_TRADINGVIEW, latency, f"TradingView exception: {exc}")


__all__ = [
    "SURFACE_TRADINGVIEW",
    "SURFACE_MT5",
    "VALID_SURFACES",
    "RouteResult",
    "SurfaceRouter",
    "get_active_surface",
    "set_active_surface",
]
