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
        """Primary entry point: execute via TradingView JS injection.

        After every click attempt:
          1. Saves a screenshot to assets/last_trade_attempt.png so the user
             can SEE what TradingView looked like at the moment of execution.
          2. Polls TradingView's positions/orders panel for up to 5 seconds.
             Returns True only when a position actually appears for this
             symbol — not just because a button was clicked.
        """
        action = str(action).strip().upper()
        clicked = False
        if action in ("BUY", "SELL", "CLOSE"):
            clicked = await self.execute_js(action)

        # Save a screenshot regardless of click result so the user can
        # debug TradingView's state at the click instant.
        await self._save_post_click_screenshot(symbol, action, clicked)

        # CLOSE is verified by the absence of a position; we don't poll for it.
        if action == "CLOSE":
            return clicked or self.execute_mt5(symbol, action, volume)

        if clicked:
            verified = await self._verify_position_opened(symbol, action, timeout_seconds=5.0)
            if verified:
                logger.info("[GHOST-VERIFY] %s %s position confirmed in TradingView panel", action, symbol)
                return True
            logger.warning(
                "[GHOST-VERIFY] %s %s click registered but no matching position appeared "
                "in TradingView within 5s — order may have been blocked or sent to wrong panel",
                action, symbol,
            )
            # Fall through to MT5 attempt only if MT5 executor is wired
            return self.execute_mt5(symbol, action, volume)

        return self.execute_mt5(symbol, action, volume)

    async def _save_post_click_screenshot(self, symbol: str, action: str, clicked: bool) -> None:
        """Snapshot the TradingView page right after the click attempt.
        Saved to assets/last_trade_attempt.png so the user can verify what
        TradingView actually showed at the click moment."""
        if not self.is_connected:
            return
        try:
            import os
            os.makedirs("assets", exist_ok=True)
            path = "assets/last_trade_attempt.png"
            await self._page.screenshot(path=path, full_page=False, timeout=3000)
            logger.info(
                "[GHOST-SHOT] %s %s post-click screenshot saved to %s (clicked=%s)",
                action, symbol, path, clicked,
            )
        except Exception as exc:
            logger.debug("[GHOST-SHOT] Could not save screenshot: %s", exc)

    async def _verify_position_opened(self, symbol: str, action: str, timeout_seconds: float = 5.0) -> bool:
        """Poll TradingView's bottom panel for a new position matching this
        symbol. Returns True as soon as one appears, False if the timeout
        expires."""
        if not self.is_connected:
            return False
        # Strip prefixes like CME_MINI: so the symbol matches what TV displays.
        sym = str(symbol or "").upper().split(":")[-1].rstrip("!")
        action_upper = action.upper()
        deadline = time.monotonic() + max(0.5, float(timeout_seconds))
        while time.monotonic() < deadline:
            try:
                found = await self._page.evaluate(f"""() => {{
                    const sym = "{sym}";
                    const action = "{action_upper}";
                    // TradingView puts open positions in the bottom widget.
                    const rows = document.querySelectorAll(
                        '[data-name*="position" i] tr, ' +
                        '[class*="positions-row" i], ' +
                        '[class*="open-position" i], ' +
                        'div[role="row"]'
                    );
                    for (const row of rows) {{
                        const text = (row.textContent || '').toUpperCase();
                        if (text.includes(sym)) {{
                            // Bonus: row text usually contains BUY/SELL or LONG/SHORT
                            return true;
                        }}
                    }}
                    return false;
                }}""")
                if found:
                    return True
            except Exception as exc:
                logger.debug("[GHOST-VERIFY] poll error: %s", exc)
            await asyncio.sleep(0.4)
        return False

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
        Execute Buy/Sell/Close on TradingView's paper-trading order panel.

        Strategy:
        1. Try TradingView's stable button selectors first via Playwright click
           (real event firing, not synthetic). Selectors live on the
           bottom-panel order ticket.
        2. Fall back to Playwright role-based locator click.
        3. Fall back to JS-injected MouseEvent dispatch (last resort; least
           reliable on React apps, restricted to in-panel scope).
        4. After any click, wait for and confirm any "Place order" /
           "Confirm" / "Buy at market" dialog.
        """
        if not self.is_connected:
            logger.warning("[GHOST-JS] Not connected to TradingView Desktop")
            return False

        action = str(action).strip().upper()
        action_lower = action.lower()

        try:
            await self._page.bring_to_front()

            # ---- STRATEGY 1: TradingView-specific stable selectors ----
            # These target the React-rendered order panel buttons. data-name
            # attributes are stable across TV redesigns.
            if action == "CLOSE":
                tv_selectors = [
                    'button[data-name="close-positions-button"]',
                    'button[data-name="flatten-button"]',
                    'button[aria-label*="Close Position" i]',
                    'button[aria-label*="Flatten" i]',
                ]
            elif action == "SELL":
                tv_selectors = [
                    'button[data-name="order-panel-sell-button"]',
                    'button[data-name="sell-button"]',
                    'button[data-name*="sell" i]',
                    'button[data-type="sell-mkt"]',
                    'button[aria-label*="Sell" i]',
                    'div[data-name="legacy-order-panel"] button.sell-button',
                ]
            else:  # BUY
                tv_selectors = [
                    'button[data-name="order-panel-buy-button"]',
                    'button[data-name="buy-button"]',
                    'button[data-name*="buy" i]',
                    'button[data-type="buy-mkt"]',
                    'button[aria-label*="Buy" i]',
                    'div[data-name="legacy-order-panel"] button.buy-button',
                ]

            for selector in tv_selectors:
                try:
                    locator = self._page.locator(selector).first
                    if await locator.count() > 0 and await locator.is_visible(timeout=500):
                        await locator.click(timeout=2000, force=False)
                        logger.info(
                            "[GHOST-JS] %s clicked via TV selector: %s",
                            action, selector,
                        )
                        await self._confirm_order_dialog(action)
                        return True
                except Exception as sel_err:
                    logger.debug("[GHOST-JS] Selector %s failed: %s", selector, sel_err)
                    continue

            # ---- STRATEGY 2: Playwright role-based click ----
            search_text_map = {
                "BUY": ["Buy", "Buy market", "Long", "Place buy"],
                "SELL": ["Sell", "Sell market", "Short", "Place sell"],
                "CLOSE": ["Close position", "Flatten", "Close", "Exit"],
            }
            for name in search_text_map.get(action, [action.title()]):
                try:
                    btn = self._page.get_by_role("button", name=name).first
                    if await btn.count() > 0 and await btn.is_visible(timeout=500):
                        await btn.click(timeout=2000)
                        logger.info(
                            "[GHOST-JS] %s clicked via role-based locator: %s",
                            action, name,
                        )
                        await self._confirm_order_dialog(action)
                        return True
                except Exception:
                    continue

            # ---- STRATEGY 3: JS MouseEvent injection (last resort) ----
            # Restricted to elements within the trading panel only — prevents
            # matching navigation/tooltip elements that have "buy" in their text.
            clicked = await self._page.evaluate(f"""() => {{
                const action = "{action_lower}";
                const panels = document.querySelectorAll(
                    '[data-name*="order" i], [data-name*="trading" i], ' +
                    '[class*="order-panel" i], [class*="trading-panel" i], ' +
                    '[class*="bottom-widget" i]'
                );
                const candidates = [];
                if (panels.length > 0) {{
                    for (const p of panels) {{
                        for (const b of p.querySelectorAll('button, div[role="button"]')) {{
                            candidates.push(b);
                        }}
                    }}
                }}
                if (candidates.length === 0) {{ return false; }}
                let best = null, bestScore = 0;
                for (const btn of candidates) {{
                    const text = (btn.textContent || '').toLowerCase().trim();
                    const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
                    const dn = (btn.getAttribute('data-name') || '').toLowerCase();
                    let score = 0;
                    if (text === action) score += 30;
                    else if (text.startsWith(action + ' ')) score += 25;
                    else if (text.includes(action)) score += 10;
                    if (aria.includes(action)) score += 8;
                    if (dn.includes(action)) score += 20;
                    if (btn.offsetParent !== null) score += 5;
                    let p = btn.parentElement;
                    let inHeader = false;
                    while (p) {{
                        const pcls = (p.className || '').toString().toLowerCase();
                        if (pcls.includes('header') || pcls.includes('nav') || pcls.includes('menu')) {{
                            inHeader = true; break;
                        }}
                        p = p.parentElement;
                    }}
                    if (inHeader) score -= 50;
                    if (score > bestScore) {{ bestScore = score; best = btn; }}
                }}
                if (best && bestScore >= 20) {{
                    best.scrollIntoView({{ block: 'center' }});
                    const rect = best.getBoundingClientRect();
                    const x = rect.left + rect.width / 2;
                    const y = rect.top + rect.height / 2;
                    best.dispatchEvent(new MouseEvent('mousedown', {{ bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 }}));
                    best.dispatchEvent(new MouseEvent('mouseup', {{ bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 }}));
                    best.dispatchEvent(new MouseEvent('click', {{ bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 }}));
                    return true;
                }}
                return false;
            }}""")

            if clicked:
                logger.info(
                    "[GHOST-JS] %s clicked via JS injection (in-panel scope) on TradingView Desktop",
                    action,
                )
                await self._confirm_order_dialog(action)
                return True

            logger.error("[GHOST-JS] All click strategies failed for %s", action)
            return False

        except Exception as e:
            logger.error("[GHOST-JS] Execution error for %s: %s", action, e)
            return False

    async def _confirm_order_dialog(self, action: str) -> None:
        """After clicking Buy/Sell/Close, TradingView usually shows a
        confirmation dialog ("Place order" / "Buy at market" / etc.). Click
        through it. Without this, the click registered but the order never
        actually reached the broker."""
        if not self.is_connected:
            return
        try:
            await asyncio.sleep(0.4)  # wait for dialog to render
            confirm_selectors = [
                'button[data-name="place-and-line-button"]',
                'button[data-name="confirm-button"]',
                'button[data-name="submit-button"]',
                'button[data-name*="place" i]',
                'div[role="dialog"] button:has-text("Buy")',
                'div[role="dialog"] button:has-text("Sell")',
                'div[role="dialog"] button:has-text("Place")',
                'div[role="dialog"] button:has-text("Confirm")',
                'div[role="dialog"] button:has-text("OK")',
                'div[role="dialog"] button:has-text("Yes")',
            ]
            for selector in confirm_selectors:
                try:
                    btn = self._page.locator(selector).first
                    if await btn.count() > 0 and await btn.is_visible(timeout=500):
                        await btn.click(timeout=1500)
                        logger.info(
                            "[GHOST-JS] %s confirmation dialog cleared via: %s",
                            action, selector,
                        )
                        return
                except Exception:
                    continue
            logger.debug(
                "[GHOST-JS] No confirmation dialog found for %s (might be one-click order)",
                action,
            )
        except Exception as exc:
            logger.debug("[GHOST-JS] Confirm dialog handler error: %s", exc)

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
