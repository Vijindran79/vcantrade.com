"""
VcanTrade AI - RPA Executor

Humanized trade execution via keyboard hotkeys and physical mouse clicks.
Uses Bezier curve mouse trajectories and randomized jitter delays
to appear indistinguishable from a human trader.

PHASE 2 UPDATE: Physical mouse clicks using calibrated coordinates.
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


def _human_click(pyautogui, x: int, y: int, click_type: str = "left"):
    """Move to target with Bezier curve, then click."""
    _human_move(pyautogui, x, y)
    # Tiny pause before click (human reaction time)
    time.sleep(random.uniform(0.05, 0.15))
    if click_type == "left":
        pyautogui.click()
    elif click_type == "right":
        pyautogui.rightClick()
    elif click_type == "double":
        pyautogui.doubleClick()


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
    Uses PHYSICAL MOUSE CLICKS with calibrated coordinates.
    
    Execution Flow for BUY signal:
    1. Move mouse to 'Buy Button' location (using calibrated coords)
    2. Click once
    3. If confirmation popup appears, move to 'Confirm' button and click again
    4. Fill SL/TP inputs if calibrated
    """

    def __init__(self):
        self.use_hotkeys = config.USE_HOTKEYS
        self.hotkey_buy = config.HOTKEY_BUY
        self.hotkey_sell = config.HOTKEY_SELL
        self.hotkey_close = config.HOTKEY_CLOSE

        # Load calibrated coordinates manager
        self.calibration_manager = CalibrationManager()

        # Initialize pyautogui for physical mouse clicks
        self.pyautogui = None
        try:
            import pyautogui

            self.pyautogui = pyautogui
            pyautogui.FAILSAFE = True  # Move mouse to corner to abort
            pyautogui.PAUSE = 0  # Disable built-in delay — we handle it manually
            logger.info("PyAutoGUI initialized — physical mouse clicks enabled")
        except ImportError:
            logger.warning("PyAutoGUI not installed — falling back to hotkeys")
            self.use_hotkeys = True

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
        """Execute BUY order using physical mouse clicks."""
        logger.info(f"Executing BUY for {trade.asset} via physical clicks")
        
        if self.use_hotkeys:
            return self._send_hotkey(self.hotkey_buy)
        
        # Step 1: Click Buy button at calibrated location
        if not self._physical_click("buy_button"):
            logger.error("Failed to click Buy button")
            return False
        
        # Small delay for order dialog to appear
        time.sleep(random.uniform(0.3, 0.6))
        
        # Step 2: Fill Stop Loss if calibrated
        self._fill_input_field("sl_input", str(trade.stop_loss)) if trade.stop_loss else None
        
        # Step 3: Fill Take Profit if calibrated
        self._fill_input_field("tp_input", str(trade.take_profit)) if trade.take_profit else None
        
        # Step 4: Fill Lot Size if calibrated
        self._fill_input_field("lot_size_input", "0.01")
        
        # Step 5: Click Confirm button (handles popup if present)
        time.sleep(random.uniform(0.2, 0.4))
        self._physical_click("confirm_button")
        
        logger.info(f"BUY trade executed: {trade.asset}")
        return True

    def _execute_sell(self, trade: TradeRecord) -> bool:
        """Execute SELL order using physical mouse clicks."""
        logger.info(f"Executing SELL for {trade.asset} via physical clicks")
        
        if self.use_hotkeys:
            return self._send_hotkey(self.hotkey_sell)
        
        # Step 1: Click Sell button at calibrated location
        if not self._physical_click("sell_button"):
            logger.error("Failed to click Sell button")
            return False
        
        # Small delay for order dialog to appear
        time.sleep(random.uniform(0.3, 0.6))
        
        # Step 2: Fill Stop Loss if calibrated
        self._fill_input_field("sl_input", str(trade.stop_loss)) if trade.stop_loss else None
        
        # Step 3: Fill Take Profit if calibrated
        self._fill_input_field("tp_input", str(trade.take_profit)) if trade.take_profit else None
        
        # Step 4: Fill Lot Size if calibrated
        self._fill_input_field("lot_size_input", "0.01")
        
        # Step 5: Click Confirm button
        time.sleep(random.uniform(0.2, 0.4))
        self._physical_click("confirm_button")
        
        logger.info(f"SELL trade executed: {trade.asset}")
        return True

    def _execute_close(self, trade: TradeRecord) -> bool:
        """Close position using physical mouse clicks."""
        logger.info(f"Executing CLOSE for {trade.asset}")
        
        if self.use_hotkeys:
            return self._send_hotkey(self.hotkey_close)
        
        # Click close button at calibrated location
        result = self._physical_click("close_button")
        if result:
            logger.info(f"CLOSE trade executed: {trade.asset}")
        return result

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

    def _physical_click(self, point_name: str) -> bool:
        """
        Execute a physical mouse click at a calibrated screen position.
        Uses Bezier curve mouse movement for human-like behavior.
        """
        if not self.pyautogui:
            logger.error("PyAutoGUI not available for physical clicks")
            return False

        # Get calibrated coordinates
        x, y = self.calibration_manager.get_coordinate(point_name)
        if (x, y) == (0, 0):
            logger.error(f"No calibration data for '{point_name}' — cannot click")
            return False

        # Perform humanized click
        _human_click(self.pyautogui, x, y)
        logger.info(f"Physical click at {point_name}: ({x}, {y})")
        
        # Post-click jitter (human pause)
        _jitter()
        
        return True

    def _fill_input_field(self, field_name: str, value: str) -> bool:
        """
        Click an input field and type a value using humanized movements.
        """
        if not self.pyautogui:
            return False

        x, y = self.calibration_manager.get_coordinate(field_name)
        if (x, y) == (0, 0):
            logger.debug(f"No calibration for input field '{field_name}' — skipping")
            return False

        # Click the input field
        _human_click(self.pyautogui, x, y)
        time.sleep(random.uniform(0.1, 0.25))
        
        # Select all existing text (Ctrl+A)
        self.pyautogui.hotkey("ctrl", "a")
        time.sleep(random.uniform(0.05, 0.12))
        
        # Type the new value with humanized delays
        _human_type(self.pyautogui, value)
        logger.info(f"Filled {field_name}: {value}")
        
        # Post-input jitter
        _jitter()
        
        return True
