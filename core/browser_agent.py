"""
VcaniTrade AI - Autonomous Browser Agent (Playwright)

The bot's "eyes and hands" - opens browser, checks prices,
and executes autonomous agentic work while Qwen analyzes.

Features:
- Opens WealthCharts/other sites to verify prices
- Scrapes real-time market data
- Takes screenshots for vision analysis
- Works autonomously in background
- Full async support
"""

import asyncio
import logging
import base64
import threading
import time
from typing import Optional, Dict, Any, Tuple
from datetime import datetime
from playwright.async_api import async_playwright, Page, Browser, BrowserContext

import config

logger = logging.getLogger(__name__)


class BrowserAgent:
    """
    Autonomous browser agent with persistent event loop and background thread.
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.playwright = None  # initialized lazily in _connect_via_cdp
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.is_running = False
        self.lock = threading.RLock()
        self.pause_cdp_listener = False
        self.target_locked = False
        self.target_locked_ticker: Optional[str] = None

        # Threading/Asyncio state
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread: Optional[threading.Thread] = None
        self._loop_ready = threading.Event()
        self._stop_event = threading.Event()

        # STAGE 4: Self-Healing Logic
        self.error_count = 0
        self.error_threshold = 3
        self.last_error: Optional[str] = None
        self.restart_count = 0
        self.max_restarts = 5
        self._dialog_handler_page: Optional[Page] = None
        self._navigating: bool = False
        self._last_cache_clear_date: Optional[str] = None
        self._failed_symbols: Dict[str, float] = {}
        self._on_page_switched = None
        # --- STALE-DATA GUARD (prevents trading on a frozen TradingView tab) ---
        self._last_price_value: float = 0.0
        self._last_price_change_time: float = 0.0
        self._feed_stale: bool = False
        self._last_heal_time: float = 0.0
        self._stale_threshold: float = float(getattr(config, "STALE_PRICE_SECONDS", 30))
        self._heal_cooldown: float = 20.0

        logger.info(f"[GLOBE] Browser Agent initialized (headless={headless})")

    def run_in_loop(self, coro):
        """Run a coroutine and return the result.

        Guarantees the coroutine executes (never silently returns None,
        which broke chart-symbol checks, bracket fills, CDP reconnects).
        After a successful run on a fresh loop, capture that loop into
        self.loop so subsequent calls (from the sync bot thread)
        can submit to it via run_coroutine_threadsafe — this is what
        makes the page's async ops (title read, evaluate) actually run.
        """
        if self.loop and self.loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, self.loop)
            try:
                return future.result(timeout=300)
            except Exception as e:
                logger.error(f"[GLOBE] Coroutine execution failed: {e!r}")
                return None
        # No active persistent loop: run on a one-off loop, then OWN it.
        try:
            _lp = asyncio.new_event_loop()
            _res = _lp.run_until_complete(coro)
            self.loop = _lp
            return _res
        except RuntimeError:
            try:
                _lp = asyncio.get_event_loop()
                _res = _lp.run_until_complete(coro)
                self.loop = _lp
                return _res
            except Exception:
                return None
        except Exception:
            return None

    def start_background(self):
        """Start the browser agent in a persistent background thread."""
        if self.thread and self.thread.is_alive():
            logger.warning("[GLOBE] Background thread already running")
            return True

        self._stop_event.clear()
        self.thread = threading.Thread(target=self._thread_entry, daemon=True, name="BrowserAgentLoop")
        self.thread.start()

        # Wait for loop to be ready
        if not self._loop_ready.wait(timeout=10):
            logger.error("[GLOBE] Background loop failed to initialize in time")
            return False

        # Now connect to the browser
        logger.info("[GLOBE] Background loop ready - connecting to browser...")
        success = self.run_in_loop(self.start())
        return success

    def _thread_entry(self):
        """Entry point for the background thread."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._loop_ready.set()

        try:
            # Keep loop alive until stop event is set
            self.loop.run_until_complete(self._loop_heartbeat())
        except Exception as e:
            logger.error(f"[GLOBE] Background loop crash: {e}")
        finally:
            self.is_running = False
            if self.loop and self.loop.is_running():
                self.loop.close()
            logger.info("[GLOBE] Background loop closed")

    async def _loop_heartbeat(self):
        """Simple periodic task to keep the loop active."""
        while not self._stop_event.is_set():
            await asyncio.sleep(0.5)

    def stop_background(self):
        """Stop the background thread and close the browser."""
        self._stop_event.set()
        if self.loop:
            self.run_in_loop(self.close())
        if self.thread:
            self.thread.join(timeout=5)

    async def close(self):
        """Close browser and clean up resources."""
        try:
            if self.browser:
                await self.browser.close()
            if hasattr(self, 'playwright'):
                await self.playwright.stop()
            self.is_running = False
            logger.info("[GLOBE] Browser Agent closed")
        except Exception as e:
            logger.error(f"[GLOBE] Close error: {e}")

    @staticmethod
    def _is_passive_observer_mode() -> bool:
        surface = str(getattr(config, "ACTIVE_EXECUTION_SURFACE", "") or "").upper().strip()
        return surface != "TRADINGVIEW"

    def _observer_log(self, symbol: str = "") -> None:
        label = str(symbol or "active tab").strip()
        logger.info("[OBSERVER] Monitoring active tab natively for symbol: %s", label)

    async def get_current_url(self) -> str:
        if not self.is_running or not self.page:
            return ""
        try:
            return self.page.url
        except Exception as e:
            logger.error(f"Failed to get current URL: {e}")
            return ""

    async def get_current_price(self, ticker: str) -> float:
        """Return the latest known price for the active ticker.

        STALE-DATA GUARD: tracks when the price last *changed*. If the
        value freezes for > STALE_PRICE_SECONDS (TradingView tab minimized
        / backgrounded by Chrome throttling), flag the feed stale so the
        engine refuses to trade on dead data, and auto-heal by reloading
        the chart tab.
        """
        now = time.time()
        price = 0.0
        try:
            from core.data_feed import data_feed
            bars = data_feed.get_bars(str(ticker), count=5)
            if bars:
                price = float(bars[-1].get("close") or 0.0)
        except Exception as e:
            logger.debug("[BROWSER] Price fetch failed: %s", e)

        if not price or price <= 0.0:
            try:
                if self.page:
                    price_text = await self.page.evaluate("""() => {
                        const nodes = Array.from(document.querySelectorAll('div, span, button, a'));
                        for (const node of nodes) {
                            const text = (node.textContent || '').replace(/,/g, '').trim();
                            if (/^\d+(\.\d+)?$/.test(text) && text.length > 0 && text.length < 24) {
                                const value = parseFloat(text);
                                if (value > 0) return value;
                            }
                        }
                        return 0;
                    }""")
                    if price_text and price_text > 0:
                        price = float(price_text)
            except Exception as e:
                logger.debug("[BROWSER] DOM price fetch failed: %s", e)

        # --- stale detection ---
        if price and price > 0:
            if self._last_price_value and abs(price - self._last_price_value) > 1e-6:
                # price actually moved -> feed is live
                self._last_price_change_time = now
                self._last_price_value = price
                if self._feed_stale:
                    logger.info("[STALE] Price feed recovered (last change %.1fs ago)", now - self._last_price_change_time)
                self._feed_stale = False
            else:
                # price present but unchanged -> check how long
                if self._last_price_change_time and (now - self._last_price_change_time) > self._stale_threshold:
                    if not self._feed_stale:
                        logger.warning("[STALE] Price frozen for %.0fs (%.2f) — feed considered stale",
                                     now - self._last_price_change_time, price)
                    self._feed_stale = True
                    # auto-heal: reload the chart tab to unfreeze it (respect cooldown)
                    if (now - self._last_heal_time) > self._heal_cooldown:
                        self._last_heal_time = now
                        await self._heal_frozen_tab()
        return price

    async def _heal_frozen_tab(self) -> None:
        """Reload + refocus the TradingView tab to unfreeze a throttled feed."""
        try:
            if self.page and not self.page.is_closed():
                logger.info("[STALE-HEAL] Reloading TradingView chart tab to unfreeze feed...")
                try:
                    await self.page.bring_to_front()
                except Exception:
                    pass
                await self.page.reload(wait_until="domcontentloaded", timeout=15000)
                logger.info("[STALE-HEAL] Tab reloaded; feed should resume shortly")
        except Exception as e:
            logger.warning("[STALE-HEAL] Failed: %s", e)

    def is_feed_stale(self) -> bool:
        """Engine calls this before executing — refuse to trade on stale data."""
        return bool(self._feed_stale)

    async def start(self):
        """Connect to existing Chrome via CDP.

        Hardened: retries FOREVER with exponential backoff instead of
        giving up after 3 tries (which left the whole bot dead when
        TradingView's CDP endpoint was flaky). Never fatal-exits.
        """
        if self.is_running:
            return True

        cdp_url = getattr(config, "BROWSER_CDP_URL", "http://127.0.0.1:9222")
        attempt = 0
        delay = 2
        while True:
            attempt += 1
            try:
                logger.info("[CDP] Connecting to %s (attempt %d)", cdp_url, attempt)
                # Ensure no stale playwright handle from a previous failed attempt
                if self.playwright is None:
                    self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.connect_over_cdp(
                    cdp_url, timeout=min(getattr(config, "CDP_CONNECT_TIMEOUT_MS", 240000), 20000)
                )
                contexts = self.browser.contexts or []
                if not contexts:
                    await self.browser.close()
                    await self.playwright.stop()
                    self.browser = None
                    self.playwright = None
                    raise RuntimeError("No browser contexts found after CDP connect")
                selected = None
                for ctx in reversed(contexts):
                    for pg in ctx.pages or []:
                        if "tradingview" in (pg.url or "").lower():
                            selected = pg
                            break
                    if selected:
                        break
                if not selected and contexts[0].pages:
                    selected = contexts[0].pages[0]
                if not selected:
                    await self.browser.close()
                    await self.playwright.stop()
                    self.browser = None
                    self.playwright = None
                    raise RuntimeError("No usable browser page found after CDP connect")

                self.context = contexts[0]
                self.page = selected
                self.simple_browser = None
                self.is_running = True
                self._tab_url = self.page.url
                logger.info("[OK] Connected to Chrome tab: %s", self._tab_url[:80])
                logger.info("[CDP] Playwright CDP connection successful")
                return True

            except Exception as e:
                err_text = str(e).lower()
                # Transient connection errors: retry forever with backoff.
                logger.warning("[CDP] Connect attempt %d failed: %s — retrying in %ds", attempt, e, delay)
                # Clean up any half-open handles so the next attempt starts fresh.
                try:
                    if self.browser is not None:
                        await self.browser.close()
                except Exception:
                    pass
                try:
                    if self.playwright is not None:
                        await self.playwright.stop()
                except Exception:
                    pass
                self.browser = None
                self.playwright = None
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)  # cap backoff at 60s

    # ------------------------------------------------------------------
    # Public helpers expected by executor / hunter / rpa_executor (BUG A1-A5)
    # ------------------------------------------------------------------
    async def _async_verify_chart_symbol(self, ticker: str) -> Optional[str]:
        """Return the symbol TradingView is ACTUALLY showing right now.

        TradingView keeps the symbol in the page TITLE (e.g. "MGC1! 4,058.7 ...")
        and/or in the chart-header DOM. The URL query param is often empty
        for saved-layout charts, so we rely on title + DOM, never just the URL.
        Returns the normalized symbol string, or None if undeterminable.
        """
        if not self.page or self.page.is_closed():
            return None
        candidates = []
        # 1) DOM header (best when present)
        try:
            dom = await self.page.evaluate(
                "() => {"
                "  const sels = ['.chart-header-symbol', '[data-symbol]',"
                "    '.symbol-info-title', '.tv-symbol-header',"
                "    '.js-button-text.apply-common-tooltip'];"
                "  for (const s of sels) {"
                "    const el = document.querySelector(s);"
                "    if (el && el.textContent) {"
                "      const t = el.textContent.trim().toUpperCase();"
                "      if (t) return t;"
                "    }"
                "  }"
                "  return '';"
                "}"
            )
            if dom:
                candidates.append(str(dom).upper().strip())
        except Exception:
            pass
        # 2) Page TITLE — TradingView always puts the symbol here.
        try:
            raw_title = await self.page.title()  # Playwright title() is a coroutine
            title = (raw_title or "").upper()
            # Extract a ticker-like token: MGC1!, MES1!, MNQ1!, BTC-USD, etc.
            import re
            m = re.search(r"\b([A-Z]{1,5}[0-9]?(?:!|=F|-USD)?)\b", title)
            if m:
                candidates.append(m.group(1))
        except Exception:
            pass
        # 3) URL query param fallback (may be empty for saved layouts)
        try:
            from urllib.parse import urlparse, parse_qs
            u = urlparse(self.page.url or "")
            url_sym = (parse_qs(u.query).get("symbol", [""])[0] or "").upper().strip()
            if url_sym:
                candidates.append(url_sym)
        except Exception:
            pass
        # Prefer a candidate that actually matches the target ticker.
        def _norm(s):
            s = str(s or "").upper()
            for ch in ("1!", "=F", "!", "-", "1", "2", "3", "4",
                        "5", "6", "7", "8", "9", "=", "F"):
                s = s.replace(ch, "")
            return s.strip()
        target = _norm(ticker)
        for c in candidates:
            if _norm(c) == target:
                return c
        # Otherwise return the first non-empty candidate if any.
        for c in candidates:
            if c:
                return c
        return None

    def verify_chart_symbol(self, ticker: str) -> Optional[str]:
        """Sync wrapper around the async chart-symbol reader."""
        if not ticker:
            return None
        coro = self._async_verify_chart_symbol(str(ticker))
        # Prefer the agent's persistent loop; fall back to a one-off loop
        # so this guard works even outside the bot's running loop (e.g.
        # standalone tests, or if the background loop isn't active).
        if getattr(self, "loop", None) and self.loop.is_running():
            return self.run_in_loop(coro)
        try:
            return asyncio.new_event_loop().run_until_complete(coro)
        except RuntimeError:
            return asyncio.get_event_loop().run_until_complete(coro)

    def navigate_to_chart(self, ticker: str) -> bool:
        """Navigate the controlled page to the chart for `ticker`."""
        if not ticker:
            return False
        coro = self._async_navigate_to_chart(str(ticker))
        result = self.run_in_loop(coro)
        return bool(result)

    async def _async_navigate_to_chart(self, ticker: str) -> bool:
        """Async worker for navigate_to_chart."""
        with self.lock:
            self._navigating = True
        try:
            if not self.page or self.page.is_closed():
                logger.warning("[GLOBE] navigate_to_chart: no live page")
                return False
            url = f"https://www.tradingview.com/chart/?symbol={ticker}"
            await self.page.goto(url, wait_until="domcontentloaded", timeout=20000)
            self._tab_url = self.page.url
            self.target_locked_ticker = str(ticker)
            logger.info("[GLOBE] Navigated to chart for %s", ticker)
            return True
        except Exception as e:
            logger.error("[GLOBE] navigate_to_chart(%s) failed: %s", ticker, e)
            return False
        finally:
            with self.lock:
                self._navigating = False

    def set_target_lock(self, ticker: str) -> None:
        """Mark a ticker as the locked execution target."""
        with self.lock:
            self.target_locked = True
            self.target_locked_ticker = str(ticker) if ticker else None
            logger.debug("[GLOBE] Target lock set: %s", self.target_locked_ticker)

    def clear_target_lock(self) -> None:
        """Release the current target lock."""
        with self.lock:
            self.target_locked = False
            self.target_locked_ticker = None
            logger.debug("[GLOBE] Target lock cleared")

    def record_error(self, error: Any = None) -> None:
        """Increment the error counter; self-heal when threshold is crossed."""
        with self.lock:
            self.error_count = int(getattr(self, "error_count", 0) or 0) + 1
            self.last_error = str(error) if error else None
            if self.error_count >= self.error_threshold:
                logger.warning(
                    "[GLOBE] error_count=%d reached threshold=%d - self-heal triggered",
                    self.error_count,
                    self.error_threshold,
                )
                # Call internal method under lock to avoid dead-lock
                self._trigger_self_heal_locked()

    def _trigger_self_heal_locked(self) -> None:
        """Reset internal counters (must be called with lock held)."""
        self.error_count = 0

    def _trigger_self_heal(self) -> None:
        """Reset internal counters and request a browser restart."""
        with self.lock:
            self.error_count = 0
            current_restarts = self.restart_count
        new_restarts = current_restarts + 1
        if new_restarts > self.max_restarts:
            logger.error("[GLOBE] max_restarts=%d exceeded - manual intervention required", self.max_restarts)
            return
        with self.lock:
            self.restart_count = new_restarts
        try:
            if self.loop and self.loop.is_running():
                self.run_in_loop(self.close())
                self.start_background()
        except Exception as e:
            logger.error("[GLOBE] self-heal restart failed: %s", e)

    def self_heal_restart(self) -> bool:
        """Public entry point for forced self-heal restart."""
        self._trigger_self_heal()
        return True

    def is_browser_busy(self) -> bool:
        """Return True if the browser is currently navigating / locked / erroring."""
        with self.lock:
            if self._navigating:
                return True
            if self.target_locked and self.target_locked_ticker:
                return True
            if int(getattr(self, "error_count", 0) or 0) > 0:
                return True
        return False
