"""
GHOST EXECUTOR v2 — TradingView Desktop CDP Automation (port 9222)

Connects to the standalone TradingView Desktop application via Chrome DevTools Protocol
on localhost:9222. Executes trades using synthesized JavaScript MouseEvent injection
directly on the paper-trading Buy/Sell/Close buttons.

No physical mouse movement, no pyautogui, no coordinate files.
Three execution modes:
  1. JS injection (execute_js) — dispatch MouseEvents on TV buttons (sub-100ms)
  2. Locator click fallback (execute_js) — Playwright get_by_role click
  3. Slam close (slam_close) — zero-delay JS execution or direct cursor teleport

Also provides position querying for intervention detection (0.2s polling).
"""

import time
import logging
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)


class GhostExecutor:
    """
    Connects to TradingView Desktop on port 9222 via CDP.
    Executes ghost trades via JS injection. Zero mouse movement.
    """

    def __init__(self):
        self._page = None
        self._browser = None
        self._playwright = None
        self._connected = False
        self._cdp_url = "http://127.0.0.1:9222"
        self._mt5_executor = None

    async def connect(self) -> bool:
        """
        Connect to TradingView Desktop via CDP on port 9222.
        Must be launched externally with: --remote-debugging-port=9222
        """
        if self._connected and self._page and not self._page.is_closed():
            return True

        try:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()

            # Try to connect with retries
            cdp_url = self._cdp_url
            logger.info("[GHOST] Connecting to TradingView Desktop at %s", cdp_url)

            try:
                self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
            except Exception as conn_err:
                logger.error(
                    "[GHOST] Cannot connect to TradingView Desktop at %s. "
                    "Ensure it is running with --remote-debugging-port=9222. "
                    "Launch via: .\\scripts\\launch_tv_debug_bat.bat | Error: %s",
                    cdp_url, conn_err
                )
                await self._playwright.stop()
                self._playwright = None
                return False

            # Find the TradingView page
            contexts = self._browser.contexts
            for ctx in contexts:
                for pg in ctx.pages:
                    url = (pg.url or "").lower()
                    if "tradingview" in url:
                        self._page = pg
                        logger.info("[GHOST] Connected to TradingView Desktop tab: %s", pg.url[:80])
                        self._connected = True
                        return True

            # If no TV tab found, use the first available page
            if contexts and contexts[0].pages:
                self._page = contexts[0].pages[0]
                logger.info("[GHOST] Connected to first available page: %s", self._page.url[:80])
                self._connected = True
                return True

            logger.error("[GHOST] No pages found in TradingView Desktop")
            await self._playwright.stop()
            self._playwright = None
            return False

        except Exception as e:
            logger.error("[GHOST] Connection error: %s", e)
            self._connected = False
            return False

    async def disconnect(self):
        """Clean up Playwright resources."""
        self._connected = False
        self._page = None
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._browser = None
        self._playwright = None

    def set_page(self, page):
        """Set an externally managed Playwright page (from main.py's browser agent)."""
        self._page = page
        self._connected = page is not None and not page.is_closed()

    def set_mt5_executor(self, executor) -> None:
        """Attach an externally managed MT5 executor for direct API routing."""
        self._mt5_executor = executor

    async def execute_trade(self, symbol: str, action: str, volume: float = 0.0) -> bool:
        """Primary entry point: execute via TradingView JS injection or MT5."""
        action = str(action).strip().upper()
        if action in ("BUY", "SELL", "CLOSE"):
            success = await self.execute_js(action)
            if success:
                return True
        return self.execute_mt5(symbol, action, volume)

    def execute_mt5(self, symbol: str, action: str, volume: float = 0.0) -> bool:
        """Execute through the attached MT5 executor if one is available."""
        if not self._mt5_executor:
            logger.debug("[GHOST-MT5] No MT5 executor attached")
            return False
        try:
            return bool(self._mt5_executor.execute_trade(symbol, action, volume=volume or None))
        except Exception as exc:
            logger.error("[GHOST-MT5] MT5 execution failed for %s %s: %s", action, symbol, exc)
            return False

    @property
    def is_connected(self) -> bool:
        try:
            return self._connected and self._page is not None and not self._page.is_closed()
        except Exception:
            return False

    # ------------------------------------------------------------------
    # JS INJECTION — dispatch MouseEvents on TradingView paper trading buttons
    # No physical mouse movement, sub-100ms
    # ------------------------------------------------------------------
    async def execute_js(self, action: str) -> bool:
        """
        Execute Buy/Sell/Close via JavaScript DOM injection in TradingView.
        Dispatches synthesized MouseEvent sequences directly on the button element.
        """
        if not self.is_connected:
            logger.warning("[GHOST-JS] Not connected to TradingView Desktop")
            return False

        action = str(action).strip().upper()
        action_lower = action.lower()

        try:
            await self._page.bring_to_front()

            # Build array of possible button text matches depending on action
            if action == "CLOSE":
                search_actions = ['close', 'close position', 'flatten', 'exit']
            elif action == "SELL":
                search_actions = ['sell', 'sell market', 'short', 'short market', 'sell mkt']
            else:
                search_actions = ['buy', 'buy market', 'long', 'long market', 'buy mkt']

            actions_js = '", "'.join(search_actions)

            clicked = await self._page.evaluate(f"""() => {{
                const actions = ["{actions_js}"];
                const buttons = Array.from(document.querySelectorAll('button, div[role="button"], span[role="button"]'));
                let best = null, bestScore = 0;
                for (const btn of buttons) {{
                    const text = (btn.textContent || btn.innerText || '').toLowerCase().trim();
                    const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
                    let score = 0;
                    for (const a of actions) {{
                        if (text.includes(a)) score += 15;
                        else if (text.startsWith(a)) score += 12;
                        if (aria.includes(a)) score += 8;
                    }}
                    const style = window.getComputedStyle(btn);
                    const bg = (style.backgroundColor || '').toLowerCase();
                    // For Buy: green-ish bg. For Sell: red-ish bg. For Close: any visible button
                    if ('{action_lower}' === 'buy' && (bg.includes('0,255') || bg.includes('green') || bg.includes('34,197') || bg.includes('16,185'))) score += 10;
                    if ('{action_lower}' === 'sell' && (bg.includes('255,0') || bg.includes('red') || bg.includes('239,68'))) score += 10;
                    // Prefer visible clickable buttons
                    if (btn.offsetParent !== null) score += 5;
                    if (score > bestScore) {{ bestScore = score; best = btn; }}
                }}
                if (best && bestScore >= 10) {{
                    const rect = best.getBoundingClientRect();
                    const x = rect.left + rect.width / 2;
                    const y = rect.top + rect.height / 2;
                    // Synthesized MouseEvent sequence — no physical mouse
                    best.dispatchEvent(new MouseEvent('mousedown', {{ bubbles: true, clientX: x, clientY: y }}));
                    best.dispatchEvent(new MouseEvent('mouseup', {{ bubbles: true, clientX: x, clientY: y }}));
                    best.dispatchEvent(new MouseEvent('click', {{ bubbles: true, clientX: x, clientY: y }}));
                    return true;
                }}
                // Fallback: try clicking first element whose class/id contains 'buy', 'sell', or 'close'
                const fallbackKeywords = ['{action_lower}', '{action_lower[:1]}'];
                const all = document.querySelectorAll(
                    'button, [class*="{' + action_lower + '}"], [id*="{' + action_lower + '}"], [data-*="{' + action_lower + '}"]'
                );
                for (const el of all) {{
                    const cls = (el.className || '').toLowerCase();
                    const elId = (el.id || '').toLowerCase();
                    for (const kw of fallbackKeywords) {{
                        if (cls.includes(kw) || elId.includes(kw)) {{
                            if (el.offsetParent !== null) {{
                                const rect = el.getBoundingClientRect();
                                const x = rect.left + rect.width / 2;
                                const y = rect.top + rect.height / 2;
                                el.dispatchEvent(new MouseEvent('mousedown', {{ bubbles: true, clientX: x, clientY: y }}));
                                el.dispatchEvent(new MouseEvent('mouseup', {{ bubbles: true, clientX: x, clientY: y }}));
                                el.dispatchEvent(new MouseEvent('click', {{ bubbles: true, clientX: x, clientY: y }}));
                                return true;
                            }}
                        }}
                    }}
                }}
                return false;
            }}""")

            if clicked:
                logger.info("[GHOST-JS] %s executed via JS injection on TradingView Desktop", action)
                return True

            # Fallback: Playwright locator click (still no physical mouse pointer movement)
            logger.info("[GHOST-JS] JS injection failed, trying Playwright locator for %s", action)
            try:
                for name in search_actions:
                    btn = self._page.get_by_role("button", name=name).first
                    if await btn.count() > 0:
                        await btn.click(timeout=2000)
                        logger.info("[GHOST-JS] %s executed via locator click", action)
                        return True
            except Exception:
                pass

            logger.error("[GHOST-JS] All click strategies failed for %s", action)
            return False

        except Exception as e:
            logger.error("[GHOST-JS] Execution error for %s: %s", action, e)
            return False

    # ------------------------------------------------------------------
    # SLAM CLOSE — zero-delay emergency flatten via JS or cursor teleport
    # ------------------------------------------------------------------
    async def slam_close_js(self, ticker_hint: str = None) -> bool:
        """
        Fast U-Turn / Slam Close: try JS injection first, then cursor teleport.
        No humanization delays.
        """
        # Try JS-injected close first (fastest, no cursor)
        try:
            success = await self.execute_js("CLOSE")
            if success:
                logger.info("[GHOST-SLAM] Fast U-Turn close executed via JS injection")
                return True
        except Exception as e:
            logger.debug("[GHOST-SLAM] JS close failed: %s", e)

        # Fallback: zero-delay pyautogui teleport to close button
        try:
            import pyautogui
            close_coords = None
            # Try to find close button via JS first to get its screen coords
            if self.is_connected:
                coords = await self._page.evaluate("""() => {
                    const buttons = Array.from(document.querySelectorAll('button'));
                    for (const btn of buttons) {
                        const text = (btn.textContent || '').toLowerCase().trim();
                        if (text.includes('close') || text.includes('flatten')) {
                            const rect = btn.getBoundingClientRect();
                            return {x: rect.left + rect.width/2, y: rect.top + rect.height/2};
                        }
                    }
                    return null;
                }""")
                if coords:
                    close_coords = (coords["x"], coords["y"])

            if close_coords:
                cx, cy = close_coords
                # Browser window offset (typically Chrome has 0,0 at window top-left)
                pyautogui.moveTo(cx, cy, duration=0)
                pyautogui.click()
                logger.info("[GHOST-SLAM] Emergency close at screen (%d, %d)", cx, cy)
                return True

            logger.warning("[GHOST-SLAM] No close button found via JS or pyautogui")
            return False
        except Exception as e:
            logger.error("[GHOST-SLAM] Slam close failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # POSITION QUERYING — for human intervention detection (0.2s polling)
    # ------------------------------------------------------------------
    async def has_open_positions(self) -> bool:
        """
        Check if TradingView shows any open positions in the DOM.
        Used by TradeMonitor for 0.2s intervention detection.
        """
        if not self.is_connected:
            return False
        try:
            result = await self._page.evaluate("""() => {
                // Check for position rows in TradingView panel
                const posElements = document.querySelectorAll(
                    '[class*="position"], [class*="open-order"], [data-name*="position"], '
                    '[class*="order-row"], tr[class*="order"], [class*="trade-row"]'
                );
                for (const el of posElements) {
                    if (el.offsetParent !== null && (el.textContent || '').trim().length > 0) {
                        const text = el.textContent.toLowerCase();
                        // Skip headers and empty rows
                        if (text.includes('buy') || text.includes('sell') || text.includes('long') || text.includes('short')) {
                            return true;
                        }
                    }
                }
                // Also check for position count badges
                const badges = document.querySelectorAll('[class*="badge"], [class*="count"]');
                for (const b of badges) {
                    const txt = (b.textContent || '').trim();
                    if (txt.length > 0 && /^[1-9]\\d*$/.test(txt)) {
                        return true;
                    }
                }
                return false;
            }""")
            return bool(result)
        except Exception as e:
            logger.debug("[GHOST] Position check error: %s", e)
            return False

    # ------------------------------------------------------------------
    # NAVIGATE TO SYMBOL
    # ------------------------------------------------------------------
    async def navigate_to_symbol(self, symbol: str) -> bool:
        """
        Navigate TradingView to a symbol by typing into the search bar via keyboard.
        """
        if not self.is_connected:
            return False
        try:
            await self._page.bring_to_front()
            # Click search bar via keyboard shortcut
            await self._page.keyboard.press("Control+o")
            await asyncio.sleep(0.3)
            # Clear and type symbol
            await self._page.keyboard.press("Control+a")
            await asyncio.sleep(0.1)
            await self._page.keyboard.type(symbol, delay=20)
            await asyncio.sleep(0.3)
            await self._page.keyboard.press("Enter")
            await asyncio.sleep(2)
            logger.info("[GHOST] Navigated TradingView to symbol: %s", symbol)
            return True
        except Exception as e:
            logger.error("[GHOST] Navigation to %s failed: %s", symbol, e)
            return False
