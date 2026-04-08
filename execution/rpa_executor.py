"""
VcanTrade AI - RPA Executor

Humanized trade execution via keyboard hotkeys and mouse clicks.
Uses Bezier curve mouse trajectories and randomized jitter delays
to appear indistinguishable from a human trader.
"""

import logging
import random
import time
from typing import List, Optional, Tuple

import config
from core.models import TradeRecord, SignalAction
from core.calibration import CalibrationManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Humanization constants
# ---------------------------------------------------------------------------

# Mouse movement duration range (seconds) — humans don't move instantly
MOUSE_MOVE_MIN = 0.25
MOUSE_MOVE_MAX = 0.65

# Jitter delays between actions (seconds)
ACTION_JITTER_MIN = 0.3
ACTION_JITTER_MAX = 0.8

# Keystroke typing delay per character (seconds)
TYPE_DELAY_MIN = 0.02
TYPE_DELAY_MAX = 0.08

# Bezier curve control point offset — adds natural arc to mouse path
BEZIER_CONTROL_OFFSET = 80


# ---------------------------------------------------------------------------
# Bezier curve mouse movement
# ---------------------------------------------------------------------------


def _cubic_bezier(t: float, p0: float, p1: float, p2: float, p3: float) -> float:
    """Single-axis cubic Bezier interpolation."""
    mt = 1 - t
    return mt**3 * p0 + 3 * mt**2 * t * p1 + 3 * mt * t**2 * p2 + t**3 * p3


def _generate_bezier_path(
    start: Tuple[int, int],
    end: Tuple[int, int],
    num_steps: int = 12,
) -> List[Tuple[int, int]]:
    """
    Generate a list of cursor positions along a cubic Bezier curve.
    The control points are offset perpendicular to the direct line,
    creating a natural sweeping arc rather than a straight line.
    """
    x0, y0 = start
    x3, y3 = end

    # Calculate midpoint
    mx = (x0 + x3) / 2
    my = (y0 + y3) / 2

    # Perpendicular offset for control points (randomized direction)
    dx = x3 - x0
    dy = y3 - y0
    length = max((dx**2 + dy**2) ** 0.5, 1)
    perp_x = -dy / length
    perp_y = dx / length

    # Randomize control point offset (creates varied arcs)
    offset = random.uniform(0.3, 1.0) * BEZIER_CONTROL_OFFSET
    direction = random.choice([-1, 1])

    cx1 = mx + perp_x * offset * direction
    cy1 = my + perp_y * offset * direction
    cx2 = mx - perp_x * offset * direction * 0.5
    cy2 = my - perp_y * offset * direction * 0.5

    # Generate points along curve
    points = []
    for i in range(num_steps + 1):
        t = i / num_steps
        # Ease-in-out: slow start, fast middle, slow end
        t_eased = t**2 * (3 - 2 * t)
        x = int(_cubic_bezier(t_eased, x0, cx1, cx2, x3))
        y = int(_cubic_bezier(t_eased, y0, cy1, cy2, y3))
        points.append((x, y))

    return points


def _human_move(pyautogui, x: int, y: int):
    """
    Move mouse to (x, y) using a Bezier curve with randomized speed.
    Looks indistinguishable from a human moving the mouse.
    """
    start = pyautogui.position()
    end = (x, y)

    # Distance-based step count (farther = more steps)
    dist = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
    num_steps = max(8, min(20, int(dist / 50)))

    path = _generate_bezier_path(start, end, num_steps)

    # Total movement time (randomized)
    total_duration = random.uniform(MOUSE_MOVE_MIN, MOUSE_MOVE_MAX)
    step_delay = total_duration / num_steps

    for px, py in path:
        pyautogui.moveTo(px, py)
        time.sleep(step_delay)


def _human_click(pyautogui, x: int, y: int):
    """Move to target with Bezier curve, then click."""
    _human_move(pyautogui, x, y)
    # Tiny pause before click (human reaction time)
    time.sleep(random.uniform(0.05, 0.15))
    pyautogui.click()


def _human_type(pyautogui, text: str):
    """Type text with randomized per-character delays."""
    for char in str(text):
        pyautogui.write(char)
        time.sleep(random.uniform(TYPE_DELAY_MIN, TYPE_DELAY_MAX))


def _jitter():
    """Randomized pause between actions — mimics human thinking time."""
    time.sleep(random.uniform(ACTION_JITTER_MIN, ACTION_JITTER_MAX))


# ---------------------------------------------------------------------------
# RPAExecutor
# ---------------------------------------------------------------------------


class RPAExecutor:
    """
    Robotic Process Automation executor for trade execution.
    Uses keyboard hotkeys as primary method, humanized mouse clicks as fallback.
    """

    def __init__(self):
        self.use_hotkeys = config.USE_HOTKEYS
        self.hotkey_buy = config.HOTKEY_BUY
        self.hotkey_sell = config.HOTKEY_SELL
        self.hotkey_close = config.HOTKEY_CLOSE

        # Load calibrated coordinates
        self.calibration_manager = CalibrationManager()

        # Only import pyautogui if needed
        self.pyautogui = None
        if not self.use_hotkeys:
            try:
                import pyautogui

                self.pyautogui = pyautogui
                pyautogui.FAILSAFE = True
                pyautogui.PAUSE = 0  # Disable built-in delay — we handle it
                logger.info("PyAutoGUI initialized — humanized mouse execution enabled")
            except ImportError:
                logger.warning("PyAutoGUI not installed — mouse execution disabled")

    def execute_trade(self, trade: TradeRecord) -> bool:
        """Execute a trade via RPA. Returns True if successful."""
        if config.DRY_RUN:
            logger.info(f"[DRY RUN] Would execute {trade.action.value} {trade.asset}")
            return True

        try:
            if trade.action == SignalAction.BUY:
                return self._execute_buy(trade)
            elif trade.action == SignalAction.SELL:
                return self._execute_sell(trade)
            elif trade.action == SignalAction.CLOSE:
                return self._execute_close(trade)
            else:
                logger.warning(f"Unknown action: {trade.action}")
                return False
        except Exception as e:
            logger.error(f"RPA execution failed: {e}")
            return False

    def _execute_buy(self, trade: TradeRecord) -> bool:
        logger.info(f"Executing BUY for {trade.asset}")
        if self.use_hotkeys:
            return self._send_hotkey(self.hotkey_buy)
        return self._mouse_click_with_input(
            "buy_button", trade, fill_sl=True, fill_tp=True
        )

    def _execute_sell(self, trade: TradeRecord) -> bool:
        logger.info(f"Executing SELL for {trade.asset}")
        if self.use_hotkeys:
            return self._send_hotkey(self.hotkey_sell)
        return self._mouse_click_with_input(
            "sell_button", trade, fill_sl=True, fill_tp=True
        )

    def _execute_close(self, trade: TradeRecord) -> bool:
        logger.info(f"Executing CLOSE for {trade.asset}")
        if self.use_hotkeys:
            return self._send_hotkey(self.hotkey_close)
        return self._mouse_click("close_button")

    def _send_hotkey(self, hotkey: str) -> bool:
        """Send keyboard hotkey combination. Format: '<ctrl>+b'."""
        try:
            parts = hotkey.lower().replace("<", "").replace(">", "").split("+")
            if len(parts) == 2:
                modifier, key = parts
                from pyautogui import hotkey

                hotkey(modifier, key)
                logger.info(f"Hotkey sent: {hotkey}")
                time.sleep(random.uniform(0.3, 0.7))
                return True
            else:
                logger.error(f"Invalid hotkey format: {hotkey}")
                return False
        except ImportError:
            logger.error("PyAutoGUI not installed — cannot send hotkeys")
            return False
        except Exception as e:
            logger.error(f"Failed to send hotkey {hotkey}: {e}")
            return False

    def _mouse_click(self, point_name: str) -> bool:
        """Humanized click at a calibrated screen position."""
        if not self.pyautogui:
            logger.error("Mouse execution not available")
            return False

        x, y = self.calibration.get_coordinate(point_name)
        if (x, y) == (0, 0):
            logger.error(f"No calibration for '{point_name}' — cannot click")
            return False

        _human_click(self.pyautogui, x, y)
        logger.info(f"Clicked {point_name} at ({x}, {y})")
        _jitter()
        return True

    def _mouse_click_with_input(
        self,
        button_point: str,
        trade: TradeRecord,
        fill_sl: bool = True,
        fill_tp: bool = True,
    ) -> bool:
        """
        Humanized click + input sequence.
        Bezier mouse movements, randomized delays, natural typing.
        """
        if not self.pyautogui:
            logger.error("Mouse execution not available")
            return False

        # Step 1: Click the buy/sell button with Bezier curve
        if not self._mouse_click(button_point):
            return False

        # Human pause — waiting for order dialog to open
        time.sleep(random.uniform(0.4, 0.9))

        # Step 2: Fill Stop Loss
        if fill_sl and trade.stop_loss:
            sl_x, sl_y = self.calibration_manager.get_coordinate("sl_input")
            if (sl_x, sl_y) != (0, 0):
                _human_click(self.pyautogui, sl_x, sl_y)
                time.sleep(random.uniform(0.15, 0.35))
                # Select all existing text
                self.pyautogui.hotkey("ctrl", "a")
                time.sleep(random.uniform(0.05, 0.15))
                # Type with humanized delays
                _human_type(self.pyautogui, str(trade.stop_loss))
                logger.info(f"Filled Stop Loss: {trade.stop_loss}")
                _jitter()

        # Step 3: Fill Take Profit
        if fill_tp and trade.take_profit:
            tp_x, tp_y = self.calibration_manager.get_coordinate("tp_input")
            if (tp_x, tp_y) != (0, 0):
                _human_click(self.pyautogui, tp_x, tp_y)
                time.sleep(random.uniform(0.15, 0.35))
                self.pyautogui.hotkey("ctrl", "a")
                time.sleep(random.uniform(0.05, 0.15))
                _human_type(self.pyautogui, str(trade.take_profit))
                logger.info(f"Filled Take Profit: {trade.take_profit}")
                _jitter()

        # Step 4: Fill Lot Size if calibrated
        lot_x, lot_y = self.calibration_manager.get_coordinate("lot_size_input")
        if (lot_x, lot_y) != (0, 0):
            _human_click(self.pyautogui, lot_x, lot_y)
            time.sleep(random.uniform(0.15, 0.35))
            self.pyautogui.hotkey("ctrl", "a")
            time.sleep(random.uniform(0.05, 0.15))
            _human_type(self.pyautogui, "0.01")
            logger.info("Filled Lot Size: 0.01")
            _jitter()

        # Step 5: Click Confirm
        self._mouse_click("confirm_button")
        logger.info(f"Trade executed: {trade.action.value} {trade.asset}")
        return True
