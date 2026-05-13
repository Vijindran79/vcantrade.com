"""
TARGET-LOCKED SCANNER - Scans ONLY configured ACTIVE_SYMBOLS.
No weekday checks, no holiday schedules, no session detectors.
If one symbol: locks onto it directly and executes.

No asyncio conflicts. Pure synchronous polling with minimal overhead.
"""

import time
import logging
import threading
from typing import List, Optional, Callable

logger = logging.getLogger(__name__)


class TargetScanner:
    """
    Minimalist scanner that locks onto ACTIVE_SYMBOLS only.
    
    Philosophy:
    - If 1 symbol in list: scan it every cycle, no branching
    - No weekday/hour checks — you choose what to scan
    - Thread-safe: runs in its own daemon thread
    - Fires callbacks on signal detection
    """

    def __init__(self, symbols: Optional[List[str]] = None):
        # Determine active symbols
        if symbols:
            self.symbols = symbols
        else:
            try:
                import config
                self.symbols = list(getattr(config, "ACTIVE_SYMBOLS", ["BTCUSD"]))
            except Exception:
                self.symbols = ["BTCUSD"]

        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._scan_interval = 3.0  # seconds between scans

        # Callbacks
        self.on_signal: Optional[Callable] = None  # fn(ticker, action, confidence)
        self.on_error: Optional[Callable] = None   # fn(error_msg)
        self.on_status: Optional[Callable] = None  # fn(ticker, status)

        # Internal state
        self._last_prices: dict = {}
        self._consecutive_errors = 0

    def start(self):
        """Start the scanner thread."""
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._scan_loop, daemon=True, name="TargetScanner")
        self._thread.start()
        logger.info("[TARGET-SCAN] Started scanning: %s", self.symbols)

    def stop(self):
        self.running = False

    def _scan_loop(self):
        """Main loop: scans each symbol, fires callbacks."""
        while self.running:
            try:
                for symbol in self.symbols:
                    if not self.running:
                        break
                    self._scan_symbol(symbol)
                    # Brief pause between symbols to avoid flooding
                    time.sleep(0.5)
                time.sleep(self._scan_interval)
            except Exception as e:
                logger.error("[TARGET-SCAN] Loop error: %s", e)
                self._consecutive_errors += 1
                if self._consecutive_errors >= 5 and self.on_error:
                    self.on_error(f"Scanner: {self._consecutive_errors} consecutive errors")
                time.sleep(2)

    def _scan_symbol(self, symbol: str):
        """Scan a single symbol. Override in subclass for custom logic."""
        if self.on_status:
            self.on_status(symbol, "scanning")

    def get_active_symbol(self) -> str:
        """Return the primary active symbol (first in list)."""
        return self.symbols[0] if self.symbols else "BTCUSD"

    def get_symbol_count(self) -> int:
        return len(self.symbols)

    def is_single_target(self) -> bool:
        """True if only one symbol is configured."""
        return len(self.symbols) == 1


class MT5TargetScanner(TargetScanner):
    """
    Target scanner that reads prices from MT5.
    No yfinance, no Yahoo Finance dependency.
    """

    def __init__(self, symbols=None):
        super().__init__(symbols)
        self._mt5_initialized = False

    def _ensure_mt5(self):
        if self._mt5_initialized:
            return True
        try:
            import MetaTrader5 as mt5
            if mt5.initialize():
                self._mt5_initialized = True
                logger.info("[TARGET-SCAN] MT5 initialized")
                return True
            else:
                logger.error("[TARGET-SCAN] MT5 initialize() failed")
                return False
        except Exception as e:
            logger.error("[TARGET-SCAN] MT5 import failed: %s", e)
            return False

    def _scan_symbol(self, symbol: str):
        """Scan symbol via MT5 price data."""
        if not self._ensure_mt5():
            return

        import MetaTrader5 as mt5

        try:
            # Map symbol via config
            import config as cfg
            mapped = getattr(cfg, "MT5_SYMBOL_MAP", {}).get(symbol, symbol)

            if not mt5.symbol_select(mapped, True):
                logger.warning("[TARGET-SCAN] Symbol %s not in MarketWatch", mapped)
                if self.on_status:
                    self.on_status(symbol, "unavailable")
                return

            tick = mt5.symbol_info_tick(mapped)
            if tick is None:
                logger.debug("[TARGET-SCAN] No tick for %s", mapped)
                return

            current_price = (tick.bid + tick.ask) / 2.0
            self._last_prices[symbol] = current_price

            if self.on_status:
                self.on_status(symbol, f"live @ {current_price:.2f}")

            self._consecutive_errors = 0

        except Exception as e:
            logger.debug("[TARGET-SCAN] Scan error for %s: %s", symbol, e)
            self._consecutive_errors += 1

    def get_last_price(self, symbol: str) -> Optional[float]:
        """Get the last scanned price for a symbol."""
        return self._last_prices.get(symbol)