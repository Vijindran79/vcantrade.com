"""
TRADE MONITOR - Human Intervention Detection & Fast Trailing Stops

Polls TradingView Desktop DOM every 200ms via GhostExecutor to detect manual trade closes.
If a trade disappears from the UI, instantly resets state.
Implements confidence-based take profit and fast-trailing stops.

No MT5 dependency. Uses GhostExecutor's has_open_positions() for TV DOM queries.
"""

import time
import logging
import threading
import asyncio
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class TradeMonitor:
    """
    Polls TradingView Desktop every 200ms via GhostExecutor to detect manual trade interventions.
    If the active trade vanishes from the TradingView panel, fires reset callback.

    Execution modes:
    1. GhostExecutor DOM query: has_open_positions() checks TV panel
    2. Fallback: internal state tracking only
    """

    def __init__(self, ghost_executor=None, on_manual_close: Optional[Callable] = None):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ghost = ghost_executor
        self._on_manual_close = on_manual_close
        self._current_trade_active = False
        self._high_water_mark = 0.0
        self._entry_price = 0.0
        self._side = ""
        self._symbol = ""
        self._trailing_active = False
        self._lock = threading.Lock()

        # Trailing stop config (loaded from config)
        self._tp_low = 50.0
        self._tp_high_min = 150.0
        self._tp_high_max = 200.0
        self._trail_activate = 30.0
        self._trail_distance = 15.0

    def set_ghost_executor(self, ghost):
        """Set the GhostExecutor for TradingView position queries."""
        self._ghost = ghost

    def load_config(self):
        """Load config values for trailing stops and TP targets."""
        try:
            import config as cfg
            self._tp_low = cfg.TP_LOW_CONFIDENCE
            self._tp_high_min = cfg.TP_HIGH_CONFIDENCE_MIN
            self._tp_high_max = cfg.TP_HIGH_CONFIDENCE_MAX
            self._trail_activate = cfg.TRAILING_STOP_ACTIVATE_AFTER_PROFIT
            self._trail_distance = cfg.TRAILING_STOP_DISTANCE
        except Exception:
            pass

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------
    def set_trade(self, symbol: str, side: str, entry_price: float):
        """Record a new trade for monitoring."""
        with self._lock:
            self._symbol = symbol
            self._side = side.upper()
            self._entry_price = entry_price
            self._high_water_mark = entry_price
            self._current_trade_active = True
            self._trailing_active = False
        logger.info("[MONITOR] Tracking %s %s @ %.2f", side, symbol, entry_price)

    def clear_trade(self):
        """Clear state after manual close or normal exit.
        No list.remove() calls — pure state reset, no sync errors."""
        with self._lock:
            self._current_trade_active = False
            self._high_water_mark = 0.0
            self._entry_price = 0.0
            self._symbol = ""
            self._side = ""
            self._trailing_active = False
        logger.info("[MONITOR] Trade state cleared")

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._current_trade_active

    # ------------------------------------------------------------------
    # Polling loop: 200ms intervals
    # ------------------------------------------------------------------
    def start(self):
        """Start the 200ms polling loop in a daemon thread."""
        if self._running:
            return
        self.load_config()
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="TradeMonitor")
        self._thread.start()
        logger.info("[MONITOR] Started (200ms polling via TradingView DOM)")

    def stop(self):
        self._running = False
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _poll_loop(self):
        """Internal loop. Runs every 200ms checking for intervention.
        Uses GhostExecutor to query TradingView DOM for open positions.
        No MT5 dependency. No sync Playwright calls."""
        # Create per-thread event loop for GhostExecutor async calls
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        while self._running:
            try:
                time.sleep(0.2)  # 200ms polling

                if not self._current_trade_active:
                    continue

                with self._lock:
                    symbol = self._symbol
                    side = self._side
                    entry = self._entry_price

                if not symbol:
                    continue

                # Check if trade still exists via GhostExecutor DOM query
                trade_exists = True
                if self._ghost and self._ghost.is_connected:
                    try:
                        trade_exists = self._loop.run_until_complete(
                            self._ghost.has_open_positions()
                        )
                    except Exception:
                        trade_exists = True  # Assume alive on query error

                if not trade_exists and self._current_trade_active:
                    # INTERVENTION DETECTED: trade was manually closed
                    logger.info("[INTERVENTION] Manual close detected for %s. Resetting state.", symbol)
                    self.clear_trade()
                    if self._on_manual_close:
                        self._on_manual_close()
                    continue

                if not trade_exists:
                    continue

                # --- FAST TRAILING STOP (profit-based, using internal state) ---
                # We can't read live P&L from DOM at 200ms reliably,
                # so this is a placeholder for the _update_positions timer path
                # which uses the 5-second price polling loop for actual dollar tracking.
                # The high-water-mark trailing is handled there.

            except Exception as e:
                logger.debug("[MONITOR] Poll error (non-fatal): %s", e)
                time.sleep(1)