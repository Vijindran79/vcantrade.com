"""
TradingView Live Price Reader — Real-time prices via Chrome CDP
================================================================

Reads live prices directly from TradingView charts running in Chrome
on port 9222. No MT5, no Yahoo Finance, no synthetic data.

Method: Playwright connects to existing Chrome via CDP, injects
JavaScript to read the price from TradingView's DOM elements.

Architecture:
    Chrome (port 9222) → Playwright CDP → JavaScript DOM read → Live price

TradingView DOM selectors tried (in order):
    1. [data-name="legend-price-item-value"] — main price display
    2. .price-axis__price — price axis
    3. .chart-markup-table .price — older layout
    4. span.price — generic fallback
"""

import asyncio
import logging
import time
import re
from typing import Dict, Optional, Tuple
from datetime import datetime

import config

logger = logging.getLogger(__name__)

# TradingView DOM selectors for live price
PRICE_SELECTORS = [
    '[data-name="legend-price-item-value"]',
    '.chart-markup-table [data-name="legend-price-item-value"]',
    '.price-axis__price',
    '.chart-markup-table .price',
    'span.price:not(.price-axis__price)',
    '[class*="price" i]',
]

# TradingView symbol URL format
TV_CHART_URL = "https://www.tradingview.com/chart/?symbol={symbol}"


class TVPriceReader:
    """Reads live prices from TradingView charts via Chrome CDP."""

    def __init__(self, cdp_url: str = "http://127.0.0.1:9222"):
        self._cdp_url = cdp_url
        self._playwright = None
        self._browser = None
        self._page = None
        self._connected = False
        self._last_prices: Dict[str, float] = {}
        self._last_read_time: Dict[str, float] = {}
        self._read_count = 0
        self._error_count = 0

    async def connect(self) -> bool:
        """Connect to existing Chrome instance on port 9222."""
        if self._connected and self._page and not self._page.is_closed():
            return True

        try:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.connect_over_cdp(self._cdp_url)

            # Use existing pages or create new
            pages = self._browser.contexts[0].pages if self._browser.contexts else []
            if pages:
                self._page = pages[0]
                logger.info("[TV-READER] Connected to existing Chrome page: %s", self._page.url[:80])
            else:
                self._page = await self._browser.contexts[0].new_page()
                logger.info("[TV-READER] Created new page")

            self._connected = True
            logger.info("[TV-READER] ✅ Connected to Chrome on %s", self._cdp_url)
            return True

        except Exception as e:
            logger.error("[TV-READER] ❌ Failed to connect to Chrome: %s", e)
            self._connected = False
            return False

    async def get_price(self, symbol: str, navigate: bool = True) -> Optional[float]:
        """
        Get the current live price for a TradingView symbol.

        Args:
            symbol: TradingView symbol (e.g., 'MES1!', 'MGC1!', 'CME_MINI:MNQ1!')
            navigate: If True, navigates to the symbol's chart first

        Returns:
            Current price as float, or None if price couldn't be read
        """
        if not self._connected:
            if not await self.connect():
                return None

        try:
            # Build TradingView URL
            tv_symbol = self._to_tv_symbol(symbol)

            if navigate:
                url = TV_CHART_URL.format(symbol=tv_symbol)
                await self._navigate_safe(url)

            # Wait for chart to load
            await asyncio.sleep(0.5)

            # Read price from DOM
            price = await self._read_price_from_dom()
            if price is not None and price > 0:
                self._last_prices[symbol] = price
                self._last_read_time[symbol] = time.time()
                self._read_count += 1
                logger.debug("[TV-READER] %s = %.2f", symbol, price)
                return price

            # Try alternative: read from the legend/header
            price = await self._read_price_alternative()
            if price is not None and price > 0:
                self._last_prices[symbol] = price
                self._last_read_time[symbol] = time.time()
                self._read_count += 1
                return price

            self._error_count += 1
            logger.warning("[TV-READER] Could not read price for %s", symbol)
            return None

        except Exception as e:
            self._error_count += 1
            logger.error("[TV-READER] Error reading price for %s: %s", symbol, e)
            return None

    async def get_all_prices(
        self, symbols: list[str], navigate: bool = False
    ) -> Dict[str, Optional[float]]:
        """
        Read prices for multiple symbols. If navigate=False, reads from
        the currently open chart tab (fast — no navigation).
        """
        results = {}
        for symbol in symbols:
            price = await self.get_price(symbol, navigate=navigate)
            results[symbol] = price
            if navigate:
                await asyncio.sleep(0.3)  # Brief pause between navigations
        return results

    async def get_current_chart_price(self) -> Optional[float]:
        """
        Read the price from whatever chart is currently open.
        Fastest method — no navigation needed.
        """
        if not self._connected:
            if not await self.connect():
                return None

        try:
            price = await self._read_price_from_dom()
            if price and price > 0:
                return price
            return await self._read_price_alternative()
        except Exception:
            return None

    def get_last_price(self, symbol: str) -> Optional[float]:
        """Get the last successfully read price for a symbol (cached)."""
        return self._last_prices.get(symbol)

    def is_stale(self, symbol: str, max_age_seconds: float = 5.0) -> bool:
        """Check if the cached price is too old."""
        last = self._last_read_time.get(symbol, 0)
        return (time.time() - last) > max_age_seconds

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def stats(self) -> Dict:
        return {
            "connected": self._connected,
            "reads": self._read_count,
            "errors": self._error_count,
            "last_prices": self._last_prices,
        }

    async def close(self):
        """Disconnect from Chrome."""
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._connected = False

    # --- Internal methods ---

    def _to_tv_symbol(self, symbol: str) -> str:
        """Map internal symbol to TradingView chart URL symbol."""
        s = symbol.upper().strip()
        # Already has exchange prefix?
        if ":" in s:
            return s
        # Map bare symbols to CME/NYMEX/COMEX
        if s in ("MES1!", "ES1!"):
            return "CME_MINI:MES1!"
        if s in ("MNQ1!", "NQ1!"):
            return "CME_MINI:MNQ1!"
        if s in ("MGC1!", "GC1!"):
            return "COMEX:MGC1!"
        if s in ("MCL1!", "CL1!"):
            return "NYMEX:MCL1!"
        if s.endswith("!"):
            # Guess: M at start = CME_MINI, otherwise NYMEX
            if s.startswith("M"):
                base = s[1:]  # Strip M prefix
                if "NQ" in base or "ES" in base:
                    return f"CME_MINI:{s}"
                elif "GC" in base:
                    return f"COMEX:{s}"
                return f"NYMEX:{s}"
            return f"CME_MINI:{s}"
        return s

    async def _navigate_safe(self, url: str, timeout: int = 15000):
        """Navigate to URL, handling errors gracefully."""
        try:
            # Check if already on the right page
            current_url = self._page.url if self._page else ""
            if url in current_url:
                return  # Already there

            await self._page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            # Wait for chart to render
            await asyncio.sleep(1.0)
        except Exception as e:
            logger.debug("[TV-READER] Navigation warning: %s", e)

    async def _read_price_from_dom(self) -> Optional[float]:
        """Read price from TradingView DOM using JavaScript injection."""
        if not self._page or self._page.is_closed():
            return None

        # JavaScript to find the price in TradingView's DOM
        js_code = """
        (() => {
            // Try all known selectors
            const selectors = [
                '[data-name="legend-price-item-value"]',
                '.chart-markup-table [data-name="legend-price-item-value"]',
                '.price-axis__price',
                '.chart-markup-table .price',
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el && el.textContent) {
                    const match = el.textContent.match(/[\\d,]+\\.?\\d*/);
                    if (match) return match[0].replace(/,/g, '');
                }
            }
            // Fallback: find any element with a price-like number
            const all = document.querySelectorAll('span, div');
            for (const el of all) {
                const text = el.textContent || '';
                // Match price patterns like "4,210.70" or "30224.70"
                const match = text.match(/^\\s*[\\d,]{1,6}\\.\\d{2}\\s*$/);
                if (match && el.offsetParent !== null) {
                    return match[0].trim().replace(/,/g, '');
                }
            }
            return null;
        })();
        """

        try:
            result = await self._page.evaluate(js_code)
            if result:
                price = float(str(result).replace(",", ""))
                if 0.01 < price < 1_000_000:  # Sanity check
                    return price
        except Exception as e:
            logger.debug("[TV-READER] DOM read failed: %s", e)

        return None

    async def _read_price_alternative(self) -> Optional[float]:
        """Alternative method: read price from page title or URL."""
        if not self._page or self._page.is_closed():
            return None

        js_code = """
        (() => {
            // Try reading from the chart header/label
            const headerPrice = document.querySelector('.chart-container .price, .pane-legend .price, [class*="last-price"]');
            if (headerPrice && headerPrice.textContent) {
                const m = headerPrice.textContent.match(/[\\d,]+\\.?\\d*/);
                if (m) return m[0].replace(/,/g, '');
            }
            // Try all text nodes for price patterns
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let node;
            const prices = [];
            while (node = walker.nextNode()) {
                const text = node.textContent.trim();
                if (/^[\\d,]{1,6}\\.\\d{2}$/.test(text)) {
                    const val = parseFloat(text.replace(/,/g, ''));
                    if (val > 1 && val < 100000) prices.push(val);
                }
            }
            if (prices.length > 0) {
                // Return the most common price (likely the current one)
                prices.sort((a,b) => a-b);
                const mid = prices[Math.floor(prices.length / 2)];
                return String(mid);
            }
            return null;
        })();
        """

        try:
            result = await self._page.evaluate(js_code)
            if result:
                price = float(str(result).replace(",", ""))
                if 0.01 < price < 1_000_000:
                    return price
        except Exception:
            pass

        return None


# ---------------------------------------------------------------------------
# Singleton for global access
# ---------------------------------------------------------------------------

_tv_reader: Optional[TVPriceReader] = None


async def get_tv_reader() -> TVPriceReader:
    """Get or create the global TVPriceReader singleton."""
    global _tv_reader
    if _tv_reader is None:
        _tv_reader = TVPriceReader()
        await _tv_reader.connect()
    elif not _tv_reader.is_connected:
        await _tv_reader.connect()
    return _tv_reader


async def read_live_price(symbol: str) -> Optional[float]:
    """Quick one-shot: read current price for a symbol."""
    reader = await get_tv_reader()
    return await reader.get_price(symbol, navigate=False)


async def read_all_live_prices(symbols: list[str]) -> Dict[str, Optional[float]]:
    """Read prices for all symbols from currently open chart."""
    reader = await get_tv_reader()
    return await reader.get_all_prices(symbols, navigate=False)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    async def test():
        reader = TVPriceReader()
        await reader.connect()

        symbols = ["MES1!", "MNQ1!", "MGC1!", "MCL1!"]
        for sym in symbols:
            price = await reader.get_price(sym, navigate=False)
            print(f"  {sym}: {'$' + str(price) if price else 'FAILED'}")

        print(f"\nStats: {reader.stats}")
        await reader.close()

    asyncio.run(test())