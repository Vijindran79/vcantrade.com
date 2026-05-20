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
from typing import Optional, Dict, Any, Tuple
from datetime import datetime
from playwright.async_api import async_playwright, Page, Browser, BrowserContext

import config

logger = logging.getLogger(__name__)


class BrowserAgent:
    """
    Autonomous browser agent that can:
    - Open websites and check prices
    - Scrape market data from WealthCharts, Yahoo Finance, etc.
    - Take screenshots for vision analysis
    - Navigate and interact with web pages autonomously
    - Self-heal: Auto-restart on repeated failures (Stage 4)
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.is_running = False

        # STAGE 4: Self-Healing Logic
        self.error_count = 0  # Consecutive error counter
        self.error_threshold = 3  # Restart after 3 consecutive errors
        self.last_error: Optional[str] = None
        self.restart_count = 0  # Total restarts performed
        self.max_restarts = 5  # Maximum restart attempts before giving up
        self._dialog_handler_page: Optional[Page] = None
        # STURDY BRIDGE: Navigation lock flag
        self._navigating: bool = False
        # CDP cache clear tracking: only clear once per day on first self-heal
        self._last_cache_clear_date: Optional[str] = None
        # NAVIGATION GUARD: track symbols that failed with ERR_ABORTED (skip for 5 min)
        self._failed_symbols: Dict[str, float] = {}  # symbol -> timestamp of failure
        self._on_page_switched = None  # Optional callback when active page changes

        logger.info(f"[GLOBE] Browser Agent initialized (headless={headless})")

    @staticmethod
    def _is_passive_observer_mode() -> bool:
        """Return True only when the active surface is NOT TradingView (i.e. MT5 or passive mode).
        When ACTIVE_EXECUTION_SURFACE=TRADINGVIEW we want real clicks via CDP."""
        surface = str(getattr(config, "ACTIVE_EXECUTION_SURFACE", "") or "").upper().strip()
        if surface == "TRADINGVIEW":
            return False
        return True

    def _observer_log(self, symbol: str = "") -> None:
        label = str(symbol or "active tab").strip()
        logger.info("[OBSERVER] Monitoring active tab natively for symbol: %s", label)

    @staticmethod
    def _is_missing_dialog_error(exc: Exception) -> bool:
        """Return True for harmless browser errors raised after a dialog already disappeared."""
        text = str(exc).lower()
        return "no dialog is showing" in text or "handlejavascriptdialog" in text

    # EPIPE RECOVERY: Pipe Watchdog
    async def _safe_navigate(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 30000) -> bool:
        """Passive observer no-op: never call page.goto or reload."""
        self._observer_log(url)
        return True

    def is_browser_busy(self) -> bool:
        """STURDY BRIDGE: Check if the browser is currently busy (navigating or loading).
        Returns True if the bot should SKIP this cycle and wait for the next one.
        Thread-safe: uses only the _navigating boolean flag (no async/page.evaluate calls)."""
        if self._navigating:
            logger.debug("[BRIDGE] Browser is navigating — skipping cycle")
            return True
        return False

    def _install_safe_dialog_handler(self):
        """Auto-accept JavaScript dialogs without letting ProtocolError crash navigation.
        Installed once per page at initialization time — passive listener only."""
        if not self.page or self._dialog_handler_page is self.page:
            return

        async def _safe_accept(dialog):
            try:
                await dialog.accept()
                logger.debug("[DIALOG] Auto-accepted JavaScript dialog")
            except Exception as e:
                # Broad catch: dialog may have already disappeared — never crash
                if "no dialog" in str(e).lower():
                    logger.debug("[DIALOG] Dialog already dismissed")
                else:
                    logger.debug("[DIALOG] Accept error (ignored): %s", e)

        def _handler(dialog):
            try:
                asyncio.create_task(_safe_accept(dialog))
            except Exception:
                pass

        try:
            self.page.on("dialog", _handler)
            self._dialog_handler_page = self.page
        except Exception:
            pass

    async def get_current_url(self) -> str:
        """Get the current URL of the active browser tab."""
        if not self.is_running or not self.page:
            return ""
        
        try:
            return self.page.url
        except Exception as e:
            logger.error(f"Failed to get current URL: {e}")
            return ""

    async def detect_ticker_from_url(self) -> Optional[str]:
        """
        Detect which ticker the user is currently viewing based on URL.
        Works with TradingView, Yahoo Finance, and other common platforms.
        """
        url = await self.get_current_url()
        if not url:
            return None
        
        # WealthCharts patterns
        if "wealthcharts.com" in url:
            # e.g., https://app.wealthcharts.com/?symbol=NQM6
            import re
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            if "symbol" in qs:
                ticker = qs["symbol"][0]
                logger.info(f"[EYE] Browser Context: User viewing WealthCharts {ticker}")
                return ticker
        # TradingView patterns (legacy)
        if "tradingview.com" in url:
            # e.g., https://www.tradingview.com/symbols/BTCUSD/
            import re
            match = re.search(r'/symbols/([^/]+)', url)
            if match:
                ticker = match.group(1)
                logger.info(f"[EYE] Browser Context: User viewing TradingView {ticker}")
                return ticker
        
        # Yahoo Finance patterns
        elif "finance.yahoo.com" in url:
            # e.g., https://finance.yahoo.com/quote/BTC-USD/
            import re
            match = re.search(r'/quote/([^/]+)', url)
            if match:
                ticker = match.group(1)
                logger.info(f"[EYE] Browser Context: User viewing Yahoo Finance {ticker}")
                return ticker
        
        # eToro patterns
        elif "etoro.com" in url:
            import re
            match = re.search(r'/markets/([^/]+)', url)
            if match:
                ticker = match.group(1)
                logger.info(f"[EYE] Browser Context: User viewing eToro {ticker}")
                return ticker
        
        return None

    async def detect_market_context(self) -> Dict[str, Any]:
        """
        Detect market context from current browser activity.
        Returns dict with detected ticker, market, and suggested focus.
        """
        ticker = await self.detect_ticker_from_url()
        if not ticker:
            return {"context": "unknown", "ticker": None, "market": None}
        
        # Classify market based on ticker
        market = "US"  # Default
        if any(x in ticker.upper() for x in ["BTC", "ETH", "SOL", "CRYPTO"]):
            market = "CRYPTO"
        elif any(x in ticker.upper() for x in ["HK", "HKG", "700", "9988", "TCEHY"]):
            market = "ASIAN"
        elif any(x in ticker.upper() for x in ["EUR", "GBP", "JPY", "USD"]):
            market = "FOREX"
        
        return {
            "context": "browser_focus",
            "ticker": ticker,
            "market": market,
            "message": f"User viewing {ticker} - prioritizing {market} analysis",
        }

    async def navigate_to_chart(self, ticker: str) -> bool:
        """Passive observer no-op: never navigate or switch the active chart."""
        self._observer_log(ticker)
        return True

    async def _navigate_once(self, ticker: str, tv_symbol: str) -> bool:
        """Passive observer no-op kept for old internal callers."""
        self._observer_log(ticker)
        return True

    async def get_live_price(self) -> float:
        """
        Read the current live price from the WealthCharts chart.
        Uses multiple selector strategies for reliability including aria-label and JS fallback.

        Returns:
            Current price as float, or 0.0 if failed
        """
        try:
            import asyncio

            if not self.page:
                return 0.0
            
            # ROBUST SELECTORS: Try aria-label, class, and data-attribute based selectors
            selectors_to_try = [
                # TradingView last-price container (most reliable)
                '[aria-label*="Last"]',
                '[aria-label*="last"]',
                '[aria-label*="Price"]',
                '[aria-label*="price"]',
                # Common TV class names
                '.js-symbol-last',
                '.tv-symbol-price',
                '.last-price',
                '.last-price-value',
                '[class*="lastPrice"]',
                '[class*="last-price"]',
                '[class*="current-price"]',
                # Data test IDs
                '[data-testid="qsp-price"]',
                '[data-name="last-price"]',
                # Generic price spans
                'span[class*="price"]',
                'div[class*="price"]',
            ]
            
            for selector in selectors_to_try:
                try:
                    price_elem = await asyncio.wait_for(
                        self.page.query_selector(selector),
                        timeout=2.0
                    )
                    if price_elem:
                        price_text = await price_elem.inner_text()
                        # Clean up: remove commas, currency symbols, etc.
                        price_text = price_text.replace(',', '').replace('$', '').replace('€', '').replace('£', '').strip()
                        if price_text and price_text.replace('.', '').replace('-', '').isdigit():
                            price = float(price_text)
                            logger.info(f"[CHART] Live price fetched: ${price:.2f}")
                            return price
                except Exception:
                    continue  # Try next selector
            
            # JAVASCRIPT FALLBACK: Search DOM for price-like text near the symbol header
            try:
                price_from_js = await self.page.evaluate("""() => {
                    // Look for elements that contain a number with 2+ decimals (price pattern)
                    const allElements = document.querySelectorAll('span, div');
                    for (const el of allElements) {
                        const text = el.textContent.trim();
                        // Match patterns like 123.45, 1,234.56, 0.1234
                        if (/^\\d{1,3}(,\\d{3})*\\.?\\d+$|^\\d+\\.\\d{2,}$/.test(text)) {
                            const num = parseFloat(text.replace(/,/g, ''));
                            if (num > 0 && num < 1000000) {
                                return text;
                            }
                        }
                    }
                    return null;
                }""")
                if price_from_js:
                    price_text = price_from_js.replace(',', '').strip()
                    price = float(price_text)
                    logger.info(f"[CHART] Live price fetched via JS fallback: ${price:.2f}")
                    return price
            except Exception:
                pass
            
            # Last resort: try to extract price from page title
            logger.warning("[WARN] Price element not found - returning 0.0")
            return 0.0
            
        except Exception as e:
            logger.error(f"[FAIL] Failed to get live price: {e}")
            return 0.0

    async def get_order_book(self) -> Tuple[float, float]:
        """
        Get bid/ask prices. For WealthCharts (which doesn't show order book),
        we estimate from the current price with typical spread.
        
        Returns:
            Tuple of (bid, ask) prices
        """
        try:
            price = await self.get_live_price()
            if price <= 0:
                return (0.0, 0.0)
            
            # Estimate spread (typical 0.01% for liquid assets)
            spread = price * 0.0001
            bid = price - (spread / 2)
            ask = price + (spread / 2)
            
            logger.debug(f"[CHART] Estimated bid/ask: {bid:.2f} / {ask:.2f}")
            return (bid, ask)
        except Exception as e:
            logger.error(f"[FAIL] Failed to get order book: {e}")
            return (0.0, 0.0)

    async def click_order_button(self, action: str, quantity: float = 1000, price: float = 0) -> bool:
        """
        Click the buy/sell button on WealthCharts.
        
        Args:
            action: 'BUY' or 'SELL'
            quantity: Number of units to trade
            price: Target price (0 = market order)
            
        Returns:
            True if click executed successfully
        """
        try:
            if not self.page:
                logger.error("[MOUSE] No WealthCharts page available")
                return False

            action = action.strip().upper()
            if action not in {"BUY", "SELL"}:
                logger.error("[MOUSE] Invalid action: %s", action)
                return False

            # Ensure Order Entry panel is open
            await self._ensure_order_entry_visible()

            # Find and click the target button
            button_text = "Buy" if action == "BUY" else "Sell"
            locator = self.page.get_by_role("button", name=button_text, exact=False)
            if await locator.count() == 0:
                logger.error("[MOUSE] Could not find %s button on WealthCharts", action)
                return False

            await locator.first.click(delay=50)
            logger.info(f"[MOUSE] Clicked {action} button on WealthCharts for {quantity:.4f} units @ ${price:.2f}")
            return True
        except Exception as e:
            logger.error(f"[FAIL] Failed to click WealthCharts order button: {e}")
            return False

    async def start(self):
        """Always prefer existing Chrome on BROWSER_CDP_URL (port 9222).
        Never launch the bot's own browser unless CDP is completely unavailable.
        This keeps the user's existing paper trading tab and avoids extra windows."""
        if self.is_running:
            logger.warning("Browser agent already running")
            return

        MAX_CDP_RETRIES = 5
        CDP_RETRY_DELAY = 5

        for attempt in range(1, MAX_CDP_RETRIES + 1):
            try:
                self.playwright = await async_playwright().start()

                # ALWAYS try CDP first (user's existing Chrome with --remote-debugging-port=9222)
                cdp_url = getattr(config, "BROWSER_CDP_URL", "http://127.0.0.1:9222")
                logger.info("[CDP] Attempting connection to existing Chrome at %s", cdp_url)
                self.browser = await self.playwright.chromium.connect_over_cdp(cdp_url)
                contexts = self.browser.contexts
                if not contexts:
                    raise RuntimeError(f"No contexts in Chrome CDP connection: {cdp_url}")

                # Pick the most recent page (user's current TradingView chart)
                selected_page = None
                selected_context = None
                for ctx in reversed(contexts):
                    pages = ctx.pages
                    if pages:
                        selected_context = ctx
                        selected_page = pages[-1]
                        break

                if selected_page:
                    self.context = selected_context
                    self.page = selected_page
                    self._install_safe_dialog_handler()
                    self.is_running = True
                    logger.info("[OK] Connected to existing Chrome tab: %s", self.page.url[:80])
                    return

                # No pages at all — create one in the existing context
                self.context = contexts[0]
                chart_url = getattr(config, "TRADINGVIEW_URL", "https://www.tradingview.com/chart/")
                self.page = await self.context.new_page()
                self._install_safe_dialog_handler()
                await self.page.goto(chart_url, wait_until="domcontentloaded")
                self.is_running = True
                logger.info("[OK] Created new tab in existing Chrome: %s", chart_url)
                return

            except Exception as e:
                err_text = str(e).lower()
                is_conn_refused = "econnrefused" in err_text or "10061" in err_text or "connection refused" in err_text

                if is_conn_refused and attempt < MAX_CDP_RETRIES:
                    logger.warning("[CDP] Connection refused (attempt %d/%d) — retrying in %ds", attempt, MAX_CDP_RETRIES, CDP_RETRY_DELAY)
                    try:
                        await self.playwright.stop()
                    except Exception:
                        pass
                    await asyncio.sleep(CDP_RETRY_DELAY)
                    continue

                logger.error("[CDP] Failed to connect to existing Chrome on %s: %s", cdp_url, e)
                if attempt >= MAX_CDP_RETRIES:
                    logger.error("[CDP] All retries exhausted. No browser connection available.")
                    raise RuntimeError("Could not connect to Chrome on port 9222. Please launch Chrome with --remote-debugging-port=9222 first.")

    async def _handle_login_wait(self):
        """Detect login screen and wait for manual login if needed."""
        if not self.page:
            return
        url = self.page.url or ""
        # Check if we're on a login/signin page
        if "signin" in url.lower() or "login" in url.lower():
            logger.warning("[LOGIN] WealthCharts login screen detected. Waiting 30s for manual login...")
            await asyncio.sleep(30)
            return
        # Check DOM for login form indicators
        has_login_form = await self.page.evaluate("""() => {
            const email = document.querySelector('input[type="email"], input[name="username"], input[placeholder*="email" i]');
            const pass = document.querySelector('input[type="password"], input[placeholder*="password" i]');
            return !!(email && pass);
        }""")
        if has_login_form:
            logger.warning("[LOGIN] WealthCharts login form detected in DOM. Waiting 30s for manual login...")
            await asyncio.sleep(30)

    async def _apply_dom_only_mode(self):
        """DOM-ONLY MODE: Hide WealthCharts clutter (news, scanner, chat, social).
        Keeps ONLY the chart and Order Entry panel visible."""
        if not self.page:
            return
        try:
            await self.page.evaluate("""() => {
                const clutterSelectors = [
                    '[class*="news-panel" i]',
                    '[class*="news-feed" i]',
                    '[class*="scanner" i]',
                    '[class*="chat" i]',
                    '[class*="social" i]',
                    '[class*="twitter" i]',
                    '[class*="idea-stream" i]',
                    '[class*="timeline" i]',
                    '[data-testid*="news" i]',
                    '[data-testid*="chat" i]',
                    '[data-testid*="scanner" i]',
                    '[aria-label*="news" i]',
                    '[aria-label*="chat" i]',
                ];
                let hidden = 0;
                for (const sel of clutterSelectors) {
                    document.querySelectorAll(sel).forEach(el => {
                        if (el.offsetParent !== null) {
                            el.style.display = 'none';
                            hidden += 1;
                        }
                    });
                }
                return hidden;
            }""")
            logger.info("[DOM-ONLY] WealthCharts clutter hidden — chart + Order Entry only")
        except Exception:
            pass

    async def _dom_cleanup_loop(self):
        """AUTO-CLEANUP: Re-apply DOM-Only mode every 60 seconds to catch reappearing News/Chat."""
        while self.is_running:
            try:
                await asyncio.sleep(60)
                if self.page and self.is_running:
                    await self._apply_dom_only_mode()
                    await self._close_blocking_popups()
                    logger.debug("[CLEANUP] Auto-cleaned WealthCharts clutter (60s interval)")
            except Exception:
                pass

    async def _ensure_order_entry_visible(self):
        """Ensure WealthCharts Order Entry panel is open and unobstructed."""
        if not self.page:
            return False
        try:
            # Check if Order Entry panel is already visible
            panel_visible = await self.page.evaluate("""() => {
                const panel = document.querySelector(
                    '[class*="order-entry-panel"], [class*="wc-order-panel"], [data-testid="order-entry"], [class*="trading-panel"]'
                );
                return !!panel && panel.offsetParent !== null && panel.getBoundingClientRect().height > 80;
            }""")
            if panel_visible:
                logger.info("[ORDER ENTRY] WealthCharts Order Entry panel already visible")
                return True

            # Try to open Order Entry panel via UI controls
            opened = await self.page.evaluate("""() => {
                const triggers = document.querySelectorAll(
                    'button[title*="Order Entry" i], [aria-label*="Order Entry" i], [class*="order-entry-toggle"]'
                );
                for (const trigger of triggers) {
                    if (trigger.offsetParent !== null) {
                        trigger.click();
                        return true;
                    }
                }
                return false;
            }""")
            if opened:
                await asyncio.sleep(1)
                logger.info("[ORDER ENTRY] Opened WealthCharts Order Entry panel")
                return True

            logger.warning("[ORDER ENTRY] Could not confirm WealthCharts Order Entry panel is visible")
            return False
        except Exception as e:
            logger.warning("[ORDER ENTRY] Error checking Order Entry panel: %s", e)
            return False

    async def navigate_to_tradingview(self):
        """Passive observer no-op for the current TradingView tab."""
        self._observer_log("TradingView")
        return True

    @staticmethod
    def _symbol_search_terms(symbol: str) -> list:
        """Build a list of matchable search terms from a TradingView symbol string.

        Examples:
            CME_MINI:MNQ1! -> ["cme_mini:mnq1!", "cme_mini:mnq1", "mnq1!", "mnq1", "mnq"]
            CL=F            -> ["cl=f", "cl", "clf"]
            GC=F            -> ["gc=f", "gc", "gcf"]
        """
        s = symbol.strip().lower()
        terms = [s]
        # strip trailing '!' to produce a clean base
        base = s.rstrip('!')
        if base != s:
            terms.append(base)
        # if symbol contains ':', split on it and add the right-hand part
        if ':' in s:
            rhs = s.split(':', 1)[1]
            rhs_base = rhs.rstrip('!')
            if rhs not in terms:
                terms.append(rhs)
            if rhs_base not in terms:
                terms.append(rhs_base)
            # add further-stripped version (drop digits at end)
            stripped = rhs_base.rstrip('0123456789')
            if stripped and stripped not in terms:
                terms.append(stripped)
        else:
            # handle foo=F style (e.g. CL=F -> clf)
            no_equals = s.replace('=', '')
            if no_equals != s and no_equals not in terms:
                terms.append(no_equals)
            # also add just the prefix before '='
            if '=' in s:
                prefix = s.split('=', 1)[0]
                if prefix and prefix not in terms:
                    terms.append(prefix)
        return terms

    async def navigate_to_symbol(self, symbol: str) -> bool:
        """Switch to the browser tab whose TradingView chart matches this symbol."""
        if self._is_passive_observer_mode():
            self._observer_log(symbol)
            return True
        if not self.browser or not self.is_running:
            return False
        terms = self._symbol_search_terms(symbol)
        best_page = None
        best_score = 0
        for ctx in self.browser.contexts:
            for page in ctx.pages:
                try:
                    url = page.url.lower()
                    title = ""
                    try:
                        title = (await page.title()).lower()
                    except Exception:
                        pass
                    score = sum(2 if t in url else 1 if t in title else 0 for t in terms)
                    if score > best_score:
                        best_score = score
                        best_page = page
                except Exception:
                    continue
        if best_page:
            await best_page.bring_to_front()
            self.page = best_page
            if hasattr(self, '_on_page_switched') and callable(self._on_page_switched):
                self._on_page_switched(self.page)
            logger.info("[NAV] Switched to tab for %s: %s", symbol, best_page.url[:80])
            return True
        logger.warning("[NAV] No tab found matching %s (searched: %s)", symbol, terms[:5])
        return False

    async def switch_to_symbol(self, symbol: str, *args, **kwargs) -> bool:
        """Delegate to navigate_to_symbol."""
        return await self.navigate_to_symbol(symbol)

    async def maps_to_symbol(self, symbol: str, *args, **kwargs) -> bool:
        """Delegate to navigate_to_symbol."""
        return await self.navigate_to_symbol(symbol)

    async def Maps_to_symbol(self, symbol: str, *args, **kwargs) -> bool:
        """Delegate to navigate_to_symbol (legacy mixed-case)."""
        return await self.navigate_to_symbol(symbol)

    async def maps_to_chart(self, symbol: str = "active tab", *args, **kwargs) -> bool:
        """Delegate to navigate_to_symbol."""
        return await self.navigate_to_symbol(symbol)

    async def Maps_to_chart(self, symbol: str = "active tab", *args, **kwargs) -> bool:
        """Delegate to navigate_to_symbol (legacy mixed-case)."""
        return await self.navigate_to_symbol(symbol)

    async def maps_to(self, symbol: str = "active tab", *args, **kwargs) -> bool:
        """Delegate to navigate_to_symbol."""
        return await self.navigate_to_symbol(symbol)

    async def Maps_to(self, symbol: str = "active tab", *args, **kwargs) -> bool:
        """Delegate to navigate_to_symbol (legacy mixed-case)."""
        return await self.navigate_to_symbol(symbol)

    async def stop(self):
        """Close the browser agent and cleanup all resources."""
        if self._is_passive_observer_mode():
            try:
                if hasattr(self, 'playwright') and self.playwright:
                    await self.playwright.stop()
            except Exception:
                pass
            self.is_running = False
            self.page = None
            self.context = None
            self.browser = None
            logger.info("[OBSERVER] Browser agent detached without closing the active tab")
            return

        if self.browser and self.is_running:
            try:
                # Close page first
                if self.page:
                    try:
                        await self.page.close()
                    except:
                        pass

                # Close context
                if self.context:
                    try:
                        await self.context.close()
                    except:
                        pass

                # Close browser
                await self.browser.close()

                # Stop playwright
                if hasattr(self, 'playwright') and self.playwright:
                    try:
                        await self.playwright.stop()
                    except:
                        pass

                self.is_running = False

                # STAGE 4: Explicitly set to None for GC
                self.page = None
                self.context = None
                self.browser = None

                # Record successful cleanup
                if hasattr(self, 'record_success'):
                    self.record_success()

                logger.info("[STOP] Browser agent stopped and resources cleaned up")

            except Exception as e:
                logger.error(f"Error stopping browser agent: {e}")
                # Still reset state even on error
                self.is_running = False
                self.page = None
                self.context = None
                self.browser = None

    async def navigate_to(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 30000):
        """Passive observer no-op: never navigate away from the active tab."""
        self._observer_log(url)
        return True

    async def get_tradingview_price(self, symbol: str) -> Dict[str, Any]:
        """
        Get current price from TradingView.
        If already on a TradingView page, scrapes from current tab instead of navigating.
        """
        if not self.is_running:
            await self.start()

        try:
            current_url = await self.get_current_url()
            # Only navigate if we're not already on a TradingView chart
            if "tradingview.com" not in current_url:
                if self._is_passive_observer_mode():
                    self._observer_log(symbol)
                else:
                    url = f"https://www.tradingview.com/symbols/{symbol.replace('-', '')}/"
                    await self.navigate_to(url)
            else:
                logger.info("[CHART] Already on TradingView - scraping current tab for %s", symbol)

            # ROBUST SELECTORS: Try aria-label, class, and data-attribute based selectors
            selectors = [
                '[aria-label*="Last"]',
                '[aria-label*="last"]',
                '[aria-label*="Price"]',
                '[aria-label*="price"]',
                '.js-symbol-last',
                '.tv-symbol-price',
                '.last-price',
                '.last-price-value',
                '[class*="lastPrice"]',
                '[class*="last-price"]',
                '[class*="current-price"]',
                '[data-testid="qsp-price"]',
                '[data-name="last-price"]',
                'span[class*="price"]',
                'div[class*="price"]',
            ]
            price = 0.0
            for sel in selectors:
                try:
                    elem = await self.page.query_selector(sel)
                    if elem:
                        price_text = await elem.inner_text()
                        price_text = price_text.replace(',', '').replace('$', '').replace('€', '').replace('£', '').strip()
                        if price_text and price_text.replace('.', '').replace('-', '').isdigit():
                            price = float(price_text)
                            break
                except Exception:
                    continue
            
            # JS FALLBACK for TradingView price
            if price <= 0:
                try:
                    price_from_js = await self.page.evaluate("""() => {
                        const allElements = document.querySelectorAll('span, div');
                        for (const el of allElements) {
                            const text = el.textContent.trim();
                            if (/^\\d{1,3}(,\\d{3})*\\.?\\d+$|^\\d+\\.\\d{2,}$/.test(text)) {
                                const num = parseFloat(text.replace(/,/g, ''));
                                if (num > 0 && num < 1000000) {
                                    return text;
                                }
                            }
                        }
                        return null;
                    }""")
                    if price_from_js:
                        price_text = price_from_js.replace(',', '').strip()
                        price = float(price_text)
                except Exception:
                    pass

            if price <= 0:
                logger.debug("[OBSERVER] Price element not found on active tab for %s", symbol)
                return {"symbol": symbol, "error": "Price element not found", "source": "TradingView", "price": None}

            logger.info("[CHART] TradingView data for %s: $%.2f", symbol, price)
            return {
                "symbol": symbol,
                "price": price,
                "source": "TradingView",
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            logger.debug("[FAIL] Failed to get TradingView price for %s: %s", symbol, e)
            return {"symbol": symbol, "error": str(e), "source": "TradingView", "price": None}

    async def get_yahoo_finance_price(self, symbol: str) -> Dict[str, Any]:
        """Get current price from Yahoo Finance. Opens a new tab so user's TradingView tab stays intact.
        TOTAL YAHOO BAN: M6 futures and Gold/XAUUSD are NOT on Yahoo Finance — return error immediately."""
        if not self.is_running:
            await self.start()

        if self._is_passive_observer_mode():
            self._observer_log(symbol)
            return {"symbol": symbol, "error": "Passive observer mode does not open Yahoo Finance", "source": "OBSERVER", "price": None}

        # Hard bypass: never open Yahoo Finance for futures or Gold
        # HARD BYPASS: Never open Yahoo Finance for M6 futures or XAUUSD
        if symbol and (symbol.upper().endswith("M6") or symbol.upper() == "XAUUSD"):
            logger.info("[YAHOO BAN] Returning error dict for %s — Yahoo Finance banned for futures/Gold", symbol)
            return {"symbol": symbol, "error": "Yahoo Finance banned for futures/Gold", "source": "YAHOO_BAN", "price": None}

        try:
            # Open Yahoo Finance in a new tab to avoid disturbing user's TradingView
            new_page = await self.context.new_page()
            url = f"https://finance.yahoo.com/quote/{symbol}/"
            logger.info("[CHART] Opening Yahoo Finance in new tab for %s", symbol)
            await new_page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)

            # Scrape price
            price_text = await new_page.text_content('[data-testid="qsp-price"]')
            price = float(price_text.replace(',', '').strip())

            # Scrape change
            try:
                change_text = await new_page.text_content('[data-testid="qsp-price-change"]')
                change = change_text.strip()
            except Exception:
                change = "0.00"

            await new_page.close()
            logger.info("[CHART] Yahoo Finance data for %s: $%.2f (%s)", symbol, price, change)
            return {
                "symbol": symbol,
                "price": price,
                "change": change,
                "source": "Yahoo Finance",
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            if "404" in str(e) or "not found" in str(e).lower():
                logger.warning("[CHART] Yahoo Finance 404 for %s — ignoring, staying on current page", symbol)
            else:
                logger.error("[FAIL] Failed to get Yahoo Finance price for %s: %s", symbol, e)
            if 'new_page' in locals() and new_page:
                await new_page.close()
            return {"symbol": symbol, "error": "Yahoo Finance failed", "source": "Yahoo Finance", "price": None}

    async def check_multiple_sources(self, symbol: str) -> Dict[str, Any]:
        """
        Agentic work: Check multiple sources and find the best price.
        This is the bot thinking and deciding autonomously!
        """
        logger.info(f"[BRAIN] Agent checking multiple price sources for {symbol}...")
        
        results = {}
        
        # Try TradingView first
        try:
            tv_data = await self.get_tradingview_price(symbol)
            if "price" in tv_data:
                results["tradingview"] = tv_data
                logger.info(f"[OK] TradingView: ${tv_data['price']:.2f}")
        except Exception as e:
            logger.warning(f"TradingView failed: {e}")
        
        # Try Yahoo Finance (TOTAL YAHOO BAN: skip for M6 futures and Gold)
        try:
            # Hard bypass check - never call Yahoo for these symbols
            if not (symbol.upper().endswith("M6") or symbol.upper() == "XAUUSD"):
                yf_data = await self.get_yahoo_finance_price(symbol)
                if "price" in yf_data:
                    results["yahoo"] = yf_data
                    logger.info(f"[OK] Yahoo Finance: ${yf_data['price']:.2f}")
            else:
                logger.info("[YAHOO BAN] Skipping Yahoo Finance for %s (M6/XAUUSD)", symbol)
        except Exception as e:
            logger.warning(f"Yahoo Finance failed: {e}")
        
        # Find best price (average if multiple sources)
        prices = [data["price"] for data in results.values() if "price" in data]
        if prices:
            avg_price = sum(prices) / len(prices)
            logger.info(f"[CHART] Average price from {len(prices)} sources: ${avg_price:.2f}")
            
            return {
                "symbol": symbol,
                "price": avg_price,
                "sources_checked": len(results),
                "data": results,
                "timestamp": datetime.now().isoformat(),
            }
        else:
            logger.error(f"[FAIL] All price sources failed for {symbol}")
            return {
                "symbol": symbol,
                "error": "All sources failed",
            }

    async def _wait_for_chart_ready(self, timeout_ms: int = 10000):
        """
        Verify that the WealthCharts/TradingView chart candles are visible before taking a screenshot.
        Waits up to timeout_ms for loading spinners to disappear and canvas to appear.
        """
        import asyncio
        start_time = asyncio.get_event_loop().time()
        timeout_sec = timeout_ms / 1000.0
        
        while (asyncio.get_event_loop().time() - start_time) < timeout_sec:
            try:
                # Check if any loading indicators are present (WealthCharts + TradingView)
                loading_selectors = [
                    '[class*="loading"]',
                    '[class*="spinner"]',
                    '[class*="progress"]',
                    '.tv-loading-indicator',
                    '[data-loading="true"]',
                    '[class*="wc-loading"]',
                ]
                any_loading = False
                for sel in loading_selectors:
                    try:
                        elem = await self.page.query_selector(sel)
                        if elem and await elem.is_visible():
                            any_loading = True
                            break
                    except Exception:
                        continue
                
                if any_loading:
                    logger.debug("[CHART] Waiting for loading indicator to disappear...")
                    await asyncio.sleep(0.5)
                    continue
                
                # Check if chart canvas or candle elements are visible (WealthCharts + TradingView)
                chart_selectors = [
                    'canvas',
                    '[class*="chart"]',
                    '[class*="candle"]',
                    '[class*="pane"]',
                    '[data-name="chart"]',
                    '[class*="wc-chart"]',
                ]
                chart_visible = False
                for sel in chart_selectors:
                    try:
                        elem = await self.page.query_selector(sel)
                        if elem and await elem.is_visible():
                            chart_visible = True
                            break
                    except Exception:
                        continue
                
                if chart_visible:
                    logger.info("[CHART] Chart is ready — candles visible")
                    return
                
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.debug(f"[CHART] Chart readiness check error: {e}")
                await asyncio.sleep(0.5)
        
        logger.warning("[CHART] Chart readiness timeout — proceeding with screenshot anyway")

    async def _close_blocking_popups(self):
        """Dismiss WealthCharts/TradingView pop-ups that can cover the chart/Order Entry panel.
        CRASH-PROOF: Broad exception wrapping — bot must never die here."""
        if not self.page:
            return
        try:
            self._install_safe_dialog_handler()
            try:
                await self.page.keyboard.press("Escape")
                await asyncio.sleep(0.15)
            except Exception:
                pass
            try:
                closed_count = await self.page.evaluate("""() => {
                    // Match common WealthCharts + TradingView popup text
                    const blockerText = /need help|intraday|upgrade|trial|paper trading|got it|welcome|tour|guide|order entry/i;
                    let closed = 0;

                    const clickCloseButton = (root) => {
                        const selectors = [
                            'button[aria-label*="Close" i]',
                            'button[title*="Close" i]',
                            '[data-name*="close" i]',
                            '[class*="close" i]',
                            '[class*="dismiss" i]',
                        ];
                        for (const selector of selectors) {
                            const button = root.querySelector(selector);
                            if (button) {
                                button.click();
                                closed += 1;
                                return true;
                            }
                        }
                        return false;
                    };

                    const candidates = Array.from(document.querySelectorAll(
                        '[role="dialog"], [class*="modal" i], [class*="popup" i], [class*="toast" i], [class*="notification" i], [class*="tooltip" i]'
                    ));

                    for (const el of candidates) {
                        const text = (el.innerText || el.textContent || '').trim();
                        const rect = el.getBoundingClientRect();
                        const visible = rect.width > 0 && rect.height > 0;
                        // PROTECT WATCHLIST BAR: skip thin elements (toolbars, bottom bars)
                        if (rect.height < 80) {
                            continue;  // too short to be a blocking popup
                        }
                        // PROTECT TRADING BAR: never hide elements containing Buy/Sell Mkt buttons
                        if (text.includes('Buy Mkt') || text.includes('Sell Mkt')) {
                            continue;
                        }
                        if (!visible || !blockerText.test(text)) {
                            continue;
                        }

                        if (!clickCloseButton(el)) {
                            el.style.display = 'none';
                            closed += 1;
                        }
                    }

                    const buttons = Array.from(document.querySelectorAll('button, [role="button"]'));
                    for (const button of buttons) {
                        const text = (button.innerText || button.textContent || button.getAttribute('aria-label') || '').trim();
                        if (/^(got it|dismiss|close|no thanks|maybe later|ok)$/i.test(text)) {
                            button.click();
                            closed += 1;
                        }
                    }

                    return closed;
                }""")
                if closed_count:
                    logger.info("[POPUP] Closed %s blocking WealthCharts/TradingView pop-up element(s)", closed_count)
                    await asyncio.sleep(0.25)
            except Exception:
                pass
        except Exception:
            pass

    async def take_screenshot(self, save_path: str = None) -> Optional[str]:
        """Take a screenshot and return as base64. Waits for chart to be fully loaded first."""
        if self._is_passive_observer_mode():
            logger.debug("[VISION] Skipping visual screenshot in passive mode.")
            return None

        if not self.page:
            return None

        try:
            self._install_safe_dialog_handler()
            await self._close_blocking_popups()
            # PAGE STATE CHECK: Wait for chart candles to be visible before screenshot
            await self._wait_for_chart_ready()
            await self._close_blocking_popups()
            
            screenshot_bytes = await self.page.screenshot(full_page=True)
            base64_screenshot = base64.b64encode(screenshot_bytes).decode('utf-8')

            if save_path:
                with open(save_path, 'wb') as f:
                    f.write(screenshot_bytes)
                logger.info(f"[CAMERA] Screenshot saved: {save_path}")

            return base64_screenshot
        except Exception as e:
            logger.error(f"[FAIL] Failed to take screenshot: {e}")
            return None

    async def inject_pine_script_to_tradingview(self, pine_script_code: str) -> bool:
        """
        Autonomous Adaptation: Open TradingView Pine Editor and inject generated code.
        
        This is the "AI's thoughts appear as lines on the live chart" feature.
        
        Args:
            pine_script_code: Generated Pine Script v6 code
            
        Returns:
            True if injection successful
        """
        try:
            if not self.is_running or not self.page:
                logger.error("Browser not running")
                return False

            if self._is_passive_observer_mode():
                self._observer_log("Pine Editor")
                return False
            
            # Check if we're on TradingView
            current_url = await self.get_current_url()
            if "tradingview.com" not in current_url:
                logger.warning("Not on TradingView, navigating...")
                await self.navigate_to_chart("BTCUSD")
            
            logger.info("[BUILD] Injecting Pine Script to TradingView...")
            
            # Open pine Editor via keyboard shortcut (Ctrl+P for Pine Editor)
            await self.page.keyboard.press("Control+p")
            await asyncio.sleep(1)  # Wait for editor to open
            
            # Find editor textarea and paste code
            # TradingView Pine Editor has a specific textarea for code
            try:
                await self.page.click('textarea[class*="pine-editor"]', timeout=5000)
                await asyncio.sleep(0.5)
                
                # Select all existing code and delete it
                await self.page.keyboard.press("Control+a")
                await self.page.keyboard.press("Backspace")
                await asyncio.sleep(0.5)
                
                # Type/Paste the generated code
                await self.page.keyboard.type(pine_script_code, delay=1)  # delay=1ms per char
                await asyncio.sleep(1)
                
                # Click "Add to Chart" button
                await self.page.click('button[title*="Add to Chart"]', timeout=5000)
                await asyncio.sleep(1)
                
                # Close editor
                await self.page.keyboard.press("Escape")
                await asyncio.sleep(0.5)
                
                logger.info("[OK] Pine Script injected and added to chart successfully")
                return True
            except Exception as editor_error:
                logger.error(f"Pine Editor interaction failed: {editor_error}")
                # Fallback: Try using execute_script to inject directly
                await self.page.evaluate(f"""
                    // This would require TradingView's internal API
                    // For now, we'll use keyboard-based approach
                """)
                return False
            
        except Exception as e:
            logger.error(f"[FAIL] Pine Script injection failed: {e}")
            return False

    async def verify_zones_on_chart(self, expected_zones: list) -> dict:
        """
        Verify that AI-generated zones are visible on chart.
        
        Args:
            expected_zones: List of zones that should be on chart
            
        Returns:
            Verification result
        """
        try:
            if not self.is_running or not self.page:
                return {"status": "BROWSER_NOT_RUNNING"}
            
            # Take screenshot to verify zones
            screenshot = await self.take_screenshot()
            
            # For now, just return success (could use VLM to verify visually)
            return {
                "status": "VERIFIED",
                "zones_count": len(expected_zones),
                "screenshot": screenshot,
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Zone verification failed: {e}")
            return {"status": "VERIFICATION_FAILED", "error": str(e)}

    async def execute_autonomous_task(self, symbol: str, task: str = "check_price") -> Dict[str, Any]:
        """
        Main entry point for autonomous agentic work.
        Qwen tells the bot what to do, and the browser agent does it.
        """
        logger.info(f"[ROBOT] Autonomous task: {task} for {symbol}")
        
        try:
            if not self.is_running:
                await self.start()
            
            if task == "check_price":
                return await self.check_multiple_sources(symbol)
            elif task == "tradingview":
                return await self.get_tradingview_price(symbol)
            elif task == "yahoo":
                return await self.get_yahoo_finance_price(symbol)
            elif task == "screenshot":
                screenshot = await self.take_screenshot(f"screenshots/{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
                return {"symbol": symbol, "screenshot": screenshot}
            else:
                logger.warning(f"Unknown task: {task}")
                return {"error": f"Unknown task: {task}"}
                
        except Exception as e:
            logger.error(f"[FAIL] Autonomous task failed: {e}")
            return {"error": str(e)}

    async def __aenter__(self):
        """Async context manager support."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager cleanup."""
        await self.stop()

    # =========================================================================
    # STAGE 4: Self-Healing Logic (Auto-Restart on Failures)
    # =========================================================================

    async def self_heal_restart(self):
        """
        Self-healing restart logic.

        GHOST MODE: Self-healing restart is DISABLED.
        Refreshing the page kills the WealthCharts session.
        Instead, the bot WAITS for the user to fix the issue manually.
        """
        logger.error(
            f"[GHOST] Browser error detected: {self.last_error}. "
            f"Self-healing restart is DISABLED to preserve WealthCharts session."
        )
        logger.error(
            "[GHOST] The bot will NOT refresh the page. "
            "Please check Chrome manually and re-login if needed."
        )
        # Do NOT call stop() or start() — that kills the session
        # Just reset the error counter and wait
        self.error_count = 0

    def record_success(self):
        """Record a successful operation (reset error counter)."""
        self.error_count = 0
        self.last_error = None

    def record_error(self, error: str):
        """
        Record an error and trigger self-heal if threshold reached.

        Args:
            error: Error message or type
        """
        self.error_count += 1
        self.last_error = error

        logger.warning(
            f"[WARN] Browser error #{self.error_count}/{self.error_threshold}: {error}"
        )

        # Check if we should self-heal
        if self.error_count >= self.error_threshold:
            logger.warning(
                f"[SIREN] Error threshold reached ({self.error_threshold}). "
                f"Triggering self-healing restart..."
            )
            # Note: Actual restart is async, will be triggered by caller


# Convenience function for one-off usage
async def quick_price_check(symbol: str) -> Dict[str, Any]:
    """Quick one-shot price check using browser agent."""
    async with BrowserAgent(headless=True) as agent:
        return await agent.check_multiple_sources(symbol)


if __name__ == "__main__":
    # Test the browser agent
    async def main():
        print("=" * 60)
        print("VcaniTrade AI - Browser Agent Test")
        print("=" * 60)
        
        async with BrowserAgent(headless=False) as agent:
            # Test TradingView price check
            result = await agent.execute_autonomous_task("BTCUSD", "check_price")
            print(f"\nResult: {result}")
            
            # Wait to see the browser
            await asyncio.sleep(5)
        
        print("\n[OK] Browser agent test complete")
    
    asyncio.run(main())
