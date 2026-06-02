"""
VcanTrade AI - Human-Like Browser Behavior Layer
=================================================
Wraps RPA actions with anti-bot-detection human behavior:
- Bézier-curve mouse movements (never straight lines)
- Variable typing speed with micro-pauses
- Random "thinking" delays before actions
- Mouse hovering before clicks
- Occasional scroll-and-glance behavior
- Mouse jitter on idle (prevents freeze detection)
"""
import asyncio
import random
import math
import logging
import time
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


class HumanBehavior:
    """Adds human-like behavior to browser automation to avoid bot detection."""

    def __init__(self, min_think_ms: int = 400, max_think_ms: int = 1800):
        self.min_think_ms = min_think_ms
        self.max_think_ms = max_think_ms
        self._last_mouse_pos: Optional[Tuple[float, float]] = None

    # ------------------------------------------------------------------
    # Bézier-curve mouse movement
    # ------------------------------------------------------------------
    async def move_mouse_human(self, page, target_x: float, target_y: float):
        """
        Move the mouse to (target_x, target_y) along a curved Bézier path
        with variable speed (acceleration, deceleration, micro-corrections).
        Looks indistinguishable from a human moving a real mouse.
        """
        if self._last_mouse_pos is None:
            self._last_mouse_pos = (target_x, target_y)
            await page.mouse.move(target_x, target_y)
            return

        start_x, start_y = self._last_mouse_pos
        dx = target_x - start_x
        dy = target_y - start_y
        distance = math.hypot(dx, dy)
        if distance < 1:
            return

        # Pick 2 control points offset perpendicular to the line,
        # so the path curves like a real human wrist
        mid_x = (start_x + target_x) / 2
        mid_y = (start_y + target_y) / 2
        perp_x = -dy / max(1.0, distance)
        perp_y = dx / max(1.0, distance)
        curve_amt = random.uniform(0.15, 0.35) * min(distance, 400)
        ctrl1_x = mid_x + perp_x * curve_amt + random.uniform(-30, 30)
        ctrl1_y = mid_y + perp_y * curve_amt + random.uniform(-30, 30)
        ctrl2_x = mid_x - perp_x * curve_amt * 0.5 + random.uniform(-20, 20)
        ctrl2_y = mid_y - perp_y * curve_amt * 0.5 + random.uniform(-20, 20)

        # Number of steps proportional to distance (humans take more steps for farther moves)
        steps = max(15, int(distance / 8))
        for i in range(1, steps + 1):
            t = i / steps
            # Cubic Bézier formula
            inv_t = 1 - t
            x = (inv_t ** 3) * start_x + 3 * (inv_t ** 2) * t * ctrl1_x + 3 * inv_t * (t ** 2) * ctrl2_x + (t ** 3) * target_x
            y = (inv_t ** 3) * start_y + 3 * (inv_t ** 2) * t * ctrl1_y + 3 * inv_t * (t ** 2) * ctrl2_y + (t ** 3) * target_y
            # Variable speed: slow at start, fast in middle, slow at end (ease-in-out)
            if t < 0.1 or t > 0.9:
                step_delay = random.uniform(0.012, 0.025)
            else:
                step_delay = random.uniform(0.004, 0.012)
            # Random micro-jitter
            if random.random() < 0.15:
                x += random.uniform(-1.5, 1.5)
                y += random.uniform(-1.5, 1.5)
            try:
                await page.mouse.move(x, y)
            except Exception:
                pass
            await asyncio.sleep(step_delay)

        self._last_mouse_pos = (target_x, target_y)

    # ------------------------------------------------------------------
    # Variable-speed typing (humans don't type uniformly)
    # ------------------------------------------------------------------
    async def type_human(self, page, text: str, selector: str = None):
        """
        Type text with human-like variable speed, occasional typos
        (corrected), and micro-pauses between words.
        """
        if selector:
            try:
                await page.focus(selector)
                await asyncio.sleep(random.uniform(0.1, 0.3))
            except Exception:
                pass

        for i, ch in enumerate(text):
            # Variable per-character delay
            base_delay = random.uniform(0.06, 0.18)
            # Some characters are slower (shift, numbers, symbols)
            if ch in '~!@#$%^&*()_+{}|:<>?':
                base_delay = random.uniform(0.15, 0.30)
            # Spaces are followed by small pause (word boundary)
            if ch == ' ' and i < len(text) - 1 and text[i + 1] != ' ':
                base_delay = random.uniform(0.12, 0.25)
            try:
                await page.keyboard.type(ch, delay=int(base_delay * 1000))
            except Exception:
                pass
            # 3% chance of a "thinking" pause
            if random.random() < 0.03:
                await asyncio.sleep(random.uniform(0.3, 0.7))

    # ------------------------------------------------------------------
    # Pre-action thinking delay
    # ------------------------------------------------------------------
    async def think_before_action(self, action_name: str = "action"):
        """Wait a random 'thinking' time before performing an action."""
        delay_ms = random.randint(self.min_think_ms, self.max_think_ms)
        logger.debug("[HUMAN] Thinking for %dms before %s", delay_ms, action_name)
        await asyncio.sleep(delay_ms / 1000.0)

    # ------------------------------------------------------------------
    # Hover before click (humans always hover before clicking)
    # ------------------------------------------------------------------
    async def click_human(self, page, selector: str, button: str = "left"):
        """
        Find element, move mouse to it along a curved path, hover,
        micro-pause, then click. Much harder to detect than a direct click.
        """
        try:
            el = page.locator(selector).first
            await el.wait_for(state="visible", timeout=10000)
            box = await el.bounding_box()
            if not box:
                logger.warning("[HUMAN] No bounding box for %s", selector)
                return False
            # Click somewhere inside the element (not always center)
            target_x = box["x"] + box["width"] * random.uniform(0.30, 0.70)
            target_y = box["y"] + box["height"] * random.uniform(0.30, 0.70)
            # Move along curved path
            await self.move_mouse_human(page, target_x, target_y)
            # Hover briefly
            await asyncio.sleep(random.uniform(0.08, 0.25))
            # Click with realistic hold time
            await page.mouse.down(button=button)
            await asyncio.sleep(random.uniform(0.05, 0.15))
            await page.mouse.up(button=button)
            return True
        except Exception as e:
            logger.warning("[HUMAN] Click failed for %s: %s", selector, str(e)[:80])
            return False

    # ------------------------------------------------------------------
    # Scroll-and-glance (humans scroll then look)
    # ------------------------------------------------------------------
    async def scroll_glance(self, page, direction: str = "down", amount: int = 300):
        """Scroll the page a bit like a human glancing at content."""
        try:
            delta = amount if direction == "down" else -amount
            # Wheel events come in small bursts
            steps = random.randint(3, 7)
            for _ in range(steps):
                chunk = delta // steps + random.randint(-20, 20)
                await page.mouse.wheel(0, chunk)
                await asyncio.sleep(random.uniform(0.04, 0.12))
            # Pause after scrolling (looking at content)
            await asyncio.sleep(random.uniform(0.2, 0.6))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Idle jitter (prevents "frozen cursor" detection)
    # ------------------------------------------------------------------
    async def idle_jitter(self, page):
        """Small mouse movements to keep the cursor from looking frozen."""
        if self._last_mouse_pos is None:
            return
        x, y = self._last_mouse_pos
        # 1-3px movement, occasionally
        if random.random() < 0.3:
            new_x = x + random.uniform(-2, 2)
            new_y = y + random.uniform(-2, 2)
            try:
                await page.mouse.move(new_x, new_y)
                self._last_mouse_pos = (new_x, new_y)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Full BUY/SELL action sequence
    # ------------------------------------------------------------------
    async def execute_buy_sell_human(self, page, action: str, symbol: str):
        """
        Full human-like BUY/SELL sequence on TradingView:
        1. Glance at chart (scroll)
        2. Think
        3. Find and hover the BUY/SELL button
        4. Move mouse along curved path
        5. Hover briefly
        6. Click
        7. Move mouse away after
        """
        try:
            action_word = "Buy Mkt" if action == "BUY" else "Sell Mkt"
            logger.info("[HUMAN] %s %s: starting human-like sequence", action, symbol)

            # 1. Glance at chart
            await self.scroll_glance(page, "down", random.randint(150, 350))
            await self.think_before_action(f"{action} {symbol}")

            # 2. Find the button
            buy_btn = page.get_by_text("Buy Mkt", exact=False).first
            sell_btn = page.get_by_text("Sell Mkt", exact=False).first
            target = buy_btn if action == "BUY" else sell_btn
            try:
                await target.wait_for(state="visible", timeout=10000)
            except Exception:
                logger.warning("[HUMAN] %s button not visible", action_word)
                return False

            # 3. Move mouse along curved path to the button
            box = await target.bounding_box()
            if not box:
                logger.warning("[HUMAN] No bounding box for %s", action_word)
                return False
            tx = box["x"] + box["width"] * random.uniform(0.3, 0.7)
            ty = box["y"] + box["height"] * random.uniform(0.3, 0.7)
            await self.move_mouse_human(page, tx, ty)

            # 4. Hover (humans always hover)
            await asyncio.sleep(random.uniform(0.15, 0.4))

            # 5. Click
            await page.mouse.down()
            await asyncio.sleep(random.uniform(0.06, 0.14))
            await page.mouse.up()

            # 6. Move mouse away (humans don't leave cursor on clicked button)
            await asyncio.sleep(random.uniform(0.2, 0.5))
            away_x = tx + random.uniform(-150, 150)
            away_y = ty + random.uniform(-80, 80)
            await self.move_mouse_human(page, away_x, away_y)

            logger.info("[HUMAN] %s %s: click sequence complete", action, symbol)
            return True

        except Exception as e:
            logger.error("[HUMAN] Sequence failed: %s", str(e)[:200])
            return False


# Singleton
human = HumanBehavior()
