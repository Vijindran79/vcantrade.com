"""
VcanTrade AI - RPA Executor

Humanized trade execution via keyboard hotkeys and mouse clicks.
Uses Bezier curve mouse trajectories with non-uniform easing speed,
image-based visual button verification, window safety interlocks,
and micro-hesitation delays to appear indistinguishable from a human.
"""

import logging
import math
import os
import random
import time
from typing import Callable, List, Optional, Tuple

import config
from core.models import TradeRecord, SignalAction
from core.calibration import CalibrationManager

logger = logging.getLogger(__name__)

# Path to reference images for visual button matching
ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
CONFIRM_BUTTON_IMAGE = os.path.join(ASSETS_DIR, "tv_confirm_button.png")
POSITION_OPEN_IMAGE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    config.POSITION_OPEN_IMAGE.replace("/", os.sep),
)

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


def _eased_step_delays(num_steps: int, total_duration: float) -> List[float]:
    """
    Generate non-uniform per-step delays using a sine-based ease-in-out curve.
    The mouse accelerates at the start and decelerates at the end, mimicking
    natural wrist movement (slow → fast → slow).
    """
    # Raw weights: sine curve maps [0, pi] → [0, 1, 0], giving slow-fast-slow
    # We invert it so the DELAY is high at start/end (slow) and low in middle (fast)
    weights = []
    for i in range(num_steps):
        t = (i + 0.5) / num_steps  # midpoint of each step interval
        # Ease-in-out weight: high at edges (slow), low in middle (fast)
        w = 1.0 - math.sin(t * math.pi) * 0.75
        weights.append(max(w, 0.05))

    total_weight = sum(weights)
    return [total_duration * (w / total_weight) for w in weights]


def _human_move(pyautogui, x: int, y: int):
    """
    Move mouse to (x, y) using a Bezier curve with non-uniform speed easing.
    Accelerates mid-trajectory and decelerates near the target — indistinguishable
    from a natural human wrist movement.
    """
    start = pyautogui.position()
    end = (x, y)

    # Distance-based step count (farther = more intermediate points)
    dist = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
    num_steps = max(10, min(24, int(dist / 40)))

    path = _generate_bezier_path(start, end, num_steps)

    # Non-uniform per-step delays: slow at start, fast in middle, slow at end
    total_duration = random.uniform(MOUSE_MOVE_MIN, MOUSE_MOVE_MAX)
    step_delays = _eased_step_delays(num_steps, total_duration)

    for (px, py), delay in zip(path, step_delays):
        pyautogui.moveTo(px, py)
        time.sleep(delay)


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
    Coordinates are handled RELATIVE to the browser window.

    Args:
        on_blind_error: Optional callback invoked when the TradingView window is
                        not visible (minimized or covered). Receives no arguments.
                        Use this to display an alert in the UI.
    """

    def __init__(self, on_blind_error: Optional[Callable] = None):
        self.use_hotkeys = config.USE_HOTKEYS
        self.hotkey_buy = config.HOTKEY_BUY
        self.hotkey_sell = config.HOTKEY_SELL
        self.hotkey_close = config.HOTKEY_CLOSE
        self.on_blind_error = on_blind_error  # Safety interlock callback

        # Load calibrated coordinates
        self.calibration_manager = CalibrationManager()

        # Only import pyautogui if needed
        self.pyautogui = None
        self._gw = None
        
        try:
            import pyautogui
            import pygetwindow as gw

            self.pyautogui = pyautogui
            self._gw = gw
            pyautogui.FAILSAFE = True
            pyautogui.PAUSE = 0  # Disable built-in delay — we handle it
            logger.info("RPA Hand: PyAutoGUI & PyGetWindow initialized")
        except ImportError:
            logger.warning("RPA Hand: Required libraries missing - mouse execution disabled")

    def _get_browser_window(self, ticker_hint: Optional[str] = None):
        """Find chart window by partial title match (TradingView or ticker hint)."""
        if not self._gw:
            return None

        ticker_hint = (ticker_hint or "").replace("=F", "").split("-")[0].strip().upper()
        candidates = []
        for w in self._gw.getAllWindows():
            title = (w.title or "").strip()
            if not title:
                continue
            title_u = title.upper()
            if "TRADINGVIEW" in title_u:
                candidates.append(w)
                continue
            if ticker_hint and ticker_hint in title_u:
                candidates.append(w)

        windows = candidates
        if not windows:
            return None

        for win in windows:
            try:
                if not win.isMinimized:
                    return win
            except Exception:
                return win
        return windows[0]

    def _active_window_title(self) -> str:
        if not self._gw:
            return ""
        try:
            active = self._gw.getActiveWindow()
            return (active.title or "") if active else ""
        except Exception:
            return ""

    def _title_matches_target(self, title: str, ticker_hint: Optional[str] = None) -> bool:
        title_u = (title or "").upper()
        ticker_hint_u = (ticker_hint or "").replace("=F", "").split("-")[0].strip().upper()
        return "TRADINGVIEW" in title_u or (ticker_hint_u and ticker_hint_u in title_u)

    def _cycle_tabs_until_match(self, ticker_hint: Optional[str] = None, attempts: int = 8) -> bool:
        """Cycle browser tabs until TradingView or ticker title becomes active."""
        if not self.pyautogui:
            return False
        for _ in range(max(1, attempts)):
            title = self._active_window_title()
            if self._title_matches_target(title, ticker_hint=ticker_hint):
                return True
            self.pyautogui.hotkey("ctrl", "tab")
            time.sleep(0.3)
        return self._title_matches_target(self._active_window_title(), ticker_hint=ticker_hint)

    def _force_focus_tradingview(self, win, ticker_hint: Optional[str] = None) -> bool:
        """Restore, maximize, activate, move window, and verify chart is foreground."""
        try:
            if win.isMinimized:
                win.restore()
            time.sleep(0.08)
            try:
                win.maximize()
            except Exception:
                pass
            time.sleep(0.08)
            win.activate()
            time.sleep(0.08)
            try:
                win.moveTo(config.TRADINGVIEW_WINDOW_X, config.TRADINGVIEW_WINDOW_Y)
            except Exception:
                pass
            if not self._cycle_tabs_until_match(ticker_hint=ticker_hint):
                self._fire_blind_error(f"unable to find TradingView tab for {ticker_hint or 'current symbol'}")
                return False
            time.sleep(0.12)
        except Exception as e:
            self._fire_blind_error(f"failed to activate TradingView window: {e}")
            return False

        try:
            import ctypes
            fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
            fg_title = self._active_window_title()
            ticker_hint_u = (ticker_hint or "").replace("=F", "").split("-")[0].strip().upper()
            fg_title_u = fg_title.upper()
            if "TRADINGVIEW" not in fg_title_u and (not ticker_hint_u or ticker_hint_u not in fg_title_u):
                self._fire_blind_error(f"foreground window is '{fg_title}', not TradingView/{ticker_hint_u or 'ticker'}")
                return False
            try:
                if not win.isActive:
                    self._fire_blind_error(f"window not active after focus for {ticker_hint or 'current symbol'}")
                    return False
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"Foreground check skipped: {e}")

        return True

    def _verify_window_visible(self, ticker_hint: Optional[str] = None) -> bool:
        """
        Safety interlock: verify the TradingView window is visible and in the foreground.

        Checks three conditions:
          1. The window exists.
          2. The window is NOT minimized.
          3. After activation, the foreground window title matches TradingView
             (i.e. it is not covered/obscured by another app).

        If any check fails, fires `on_blind_error` callback and returns False.
        """
        if not self._gw:
            self._fire_blind_error("PyGetWindow not available")
            return False

        win = self._get_browser_window(ticker_hint=ticker_hint)
        if win is None:
            self._fire_blind_error(f"TradingView/ticker window not found for {ticker_hint or 'current symbol'}")
            return False

        if not self._force_focus_tradingview(win, ticker_hint=ticker_hint):
            return False

        # Check minimized state post-restore attempt
        try:
            if win.isMinimized:
                self._fire_blind_error("window still minimized after restore attempt")
                return False
        except Exception:
            pass  # Some window objects don't expose isMinimized — proceed

        return True

    def bring_tradingview_to_front(self, ticker_hint: Optional[str] = None) -> bool:
        """Public focus interlock used before an immediate strike."""
        visible = self._verify_window_visible(ticker_hint=ticker_hint)
        if visible:
            logger.info("TradingView focus locked for %s", ticker_hint or "current symbol")
        return visible

    def _fire_blind_error(self, reason: str):
        """Log and notify that the Professor cannot see the chart."""
        logger.error(f"[SAFETY INTERLOCK] Professor is blind: {reason}")
        if self.on_blind_error:
            try:
                self.on_blind_error(reason)
            except TypeError:
                self.on_blind_error()
            except Exception as cb_err:
                logger.error(f"on_blind_error callback failed: {cb_err}")

    def _find_button_by_image(
        self,
        image_path: str,
        confidence: float = 0.80,
    ) -> Optional[Tuple[int, int]]:
        """
        Locate a UI button on screen using pixel template matching.

        Returns the (x, y) center of the best match, or None if not found.
        Falls back gracefully when the reference image doesn't exist or
        opencv/pillow are unavailable.
        """
        if not self.pyautogui:
            return None
        if not os.path.exists(image_path):
            logger.debug(f"Reference image not found: {image_path} — skipping visual match")
            return None
        try:
            location = self.pyautogui.locateOnScreen(image_path, confidence=confidence)
            if location:
                cx = location.left + location.width // 2
                cy = location.top + location.height // 2
                logger.info(f"Visual match: found '{os.path.basename(image_path)}' at ({cx}, {cy})")
                return (cx, cy)
        except Exception as e:
            logger.debug(f"Image-based button search failed: {e}")
        return None

    def execute_trade(self, trade: TradeRecord) -> bool:
        """Execute a trade via RPA. Returns True if successful."""
        if config.DRY_RUN:
            logger.info(f"[DRY RUN] Would execute {trade.action.value} {trade.asset}")
            return True

        # ── Safety Interlock ─────────────────────────────────────────────
        # Do NOT click if TradingView is minimized or covered by another app
        if not self.bring_tradingview_to_front(ticker_hint=trade.asset):
            logger.error("[ABORT] Professor is blind — execution cancelled")
            return False

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

    def draw_liquidity_zone(self, ticker: str, liquidity_zone: Optional[dict]) -> bool:
        """Draw nearest liquidity zone using rectangle tool when optional calibration exists."""
        if not liquidity_zone or not self.pyautogui:
            return False
        if not self._verify_window_visible(ticker_hint=ticker):
            return False

        rect = self._get_abs_coord("rectangle_tool")
        chart_tl = self._get_abs_coord("chart_top_left")
        chart_br = self._get_abs_coord("chart_bottom_right")
        if rect == (0, 0) or chart_tl == (0, 0) or chart_br == (0, 0):
            logger.info("Liquidity draw skipped: optional rectangle/chart calibration not available")
            return False

        try:
            start_ratio = min(1.0, max(0.0, liquidity_zone.get("start_index", 0) / 50.0))
            end_ratio = min(1.0, max(0.05, liquidity_zone.get("end_index", 0) / 50.0))
            zone_level = float(liquidity_zone.get("level", 0.0))
            current_price = float(liquidity_zone.get("current_price", zone_level))
            price_range = max(abs(current_price) * 0.02, 0.01)
            top_price = max(zone_level, current_price) + price_range
            bottom_price = min(zone_level, current_price) - price_range
            chart_width = max(1, chart_br[0] - chart_tl[0])
            chart_height = max(1, chart_br[1] - chart_tl[1])

            start_x = chart_tl[0] + int(chart_width * start_ratio)
            end_x = chart_tl[0] + int(chart_width * end_ratio)

            max_price = top_price
            min_price = bottom_price
            if max_price == min_price:
                max_price += 1.0

            y = chart_tl[1] + int(chart_height * (1 - ((zone_level - min_price) / (max_price - min_price))))
            top_y = max(chart_tl[1], y - 16)
            bottom_y = min(chart_br[1], y + 16)

            _human_click(self.pyautogui, rect[0], rect[1])
            time.sleep(0.2)
            self.pyautogui.moveTo(start_x, top_y)
            self.pyautogui.dragTo(end_x, bottom_y, duration=0.5, button="left")
            time.sleep(0.2)
            self.pyautogui.write("LIQUIDITY TARGET")
            logger.info("Liquidity zone drawn for %s at %.4f", ticker, zone_level)
            return True
        except Exception as e:
            logger.error(f"Failed to draw liquidity zone for {ticker}: {e}")
            return False

    def _position_open_visible(self) -> bool:
        if not self.pyautogui:
            return False
        try:
            if os.path.exists(POSITION_OPEN_IMAGE):
                return self.pyautogui.locateOnScreen(POSITION_OPEN_IMAGE, confidence=0.75) is not None
            # Fallback heuristic: confirm button should disappear or move after successful entry.
            return self._find_button_by_image(CONFIRM_BUTTON_IMAGE, confidence=0.85) is None
        except Exception as e:
            logger.debug(f"Position-open verification failed: {e}")
            return False

    def get_chart_focus_target(self, ticker_hint: Optional[str] = None) -> Optional[Tuple[int, int]]:
        """Resolve a neutral chart point for visible hand diagnostics or keyboard focus."""
        if not self.pyautogui:
            return None

        win = self._get_browser_window(ticker_hint=ticker_hint)
        if not win:
            return None

        chart_tl = self._get_abs_coord("chart_top_left", ticker_hint=ticker_hint)
        chart_br = self._get_abs_coord("chart_bottom_right", ticker_hint=ticker_hint)
        if chart_tl != (0, 0) and chart_br != (0, 0):
            x = chart_tl[0] + max(40, (chart_br[0] - chart_tl[0]) // 3)
            y = chart_tl[1] + max(40, (chart_br[1] - chart_tl[1]) // 3)
        else:
            x = win.left + max(120, win.width // 2)
            y = win.top + max(120, win.height // 2)
        return (x, y)

    def describe_entry_target(self, action: str, ticker_hint: Optional[str] = None) -> Optional[dict]:
        """Return relative and absolute TradingView button coordinates for BUY/SELL execution."""
        action_upper = str(action or "").upper()
        if action_upper not in {"BUY", "SELL"}:
            return None

        point_name = "buy_button" if action_upper == "BUY" else "sell_button"
        relative = self.calibration_manager.get_coordinate(point_name)
        absolute = self._get_abs_coord(point_name, ticker_hint=ticker_hint)
        return {
            "action": action_upper,
            "point_name": point_name,
            "relative": relative,
            "absolute": absolute,
        }

    def _click_neutral_chart_area(self, ticker_hint: Optional[str] = None) -> bool:
        """Click a neutral chart area so TradingView reliably receives keyboard shortcuts."""
        if not self.pyautogui:
            return False
        target = self.get_chart_focus_target(ticker_hint=ticker_hint)
        if not target:
            return False

        x, y = target

        _human_click(self.pyautogui, x, y)
        logger.info("Neutral chart focus click at (%s, %s)", x, y)
        return True

    def switch_timeframe(self, timeframe: str, ticker_hint: Optional[str] = None) -> bool:
        """Focus chart, type TradingView timeframe shortcut, and wait for load."""
        tf_text = str(timeframe or "").strip().lower().replace("min", "m")
        tf_map = {
            "1m": "1",
            "3m": "3",
            "5m": "5",
            "15m": "15",
        }
        tf_input = tf_map.get(tf_text, tf_text)
        if not tf_input or not tf_input.replace("m", "").replace("h", "").replace("d", "").isdigit():
            logger.error("Unsupported timeframe for TradingView shortcut: %s", timeframe)
            return False

        if not self._verify_window_visible(ticker_hint=ticker_hint):
            return False
        if not self._click_neutral_chart_area(ticker_hint=ticker_hint):
            return False

        try:
            self.pyautogui.write(tf_input, interval=random.uniform(TYPE_DELAY_MIN, TYPE_DELAY_MAX))
            self.pyautogui.press("enter")
            logger.info("TradingView timeframe switched to %s using typed shortcut %s", timeframe, tf_input)
            time.sleep(1.0)
            return True
        except Exception as e:
            logger.error("Failed to switch timeframe to %s: %s", timeframe, e)
            return False

    def force_hand_test(self, ticker_hint: Optional[str] = None) -> bool:
        """Move the RPA hand to the active chart and perform one neutral click."""
        if not self.pyautogui:
            logger.error("Neutral hand test unavailable: PyAutoGUI not loaded")
            return False
        if not self._verify_window_visible(ticker_hint=ticker_hint):
            return False
        clicked = self._click_neutral_chart_area(ticker_hint=ticker_hint)
        if clicked:
            logger.info("Force hand test completed for %s", ticker_hint or "current chart")
        return clicked

    def force_hand_test_move(self, ticker_hint: Optional[str] = None) -> bool:
        """Visible hand diagnostic: move to the active chart focus point without clicking."""
        if not self.pyautogui:
            logger.error("Human-move diagnostic unavailable: PyAutoGUI not loaded")
            return False
        if not self._verify_window_visible(ticker_hint=ticker_hint):
            return False

        target = self.get_chart_focus_target(ticker_hint=ticker_hint)
        if not target:
            logger.error("Human-move diagnostic unavailable: no chart focus target for %s", ticker_hint or "current chart")
            return False

        x, y = target
        self.move_human_like(x, y)
        logger.info("Force hand move diagnostic completed for %s at (%s, %s)", ticker_hint or "current chart", x, y)
        return True

    def move_human_like(self, x: int, y: int) -> bool:
        """Direct hand diagnostic: move the cursor to a point without any chart logic."""
        if not self.pyautogui:
            logger.error("Human-move diagnostic unavailable: PyAutoGUI not loaded")
            return False
        _human_move(self.pyautogui, int(x), int(y))
        logger.info("Human-move diagnostic completed to (%s, %s)", x, y)
        return True

    def _move_human_like(self, x: int, y: int) -> bool:
        """Backwards-compatible alias for terminal-based hand diagnostics."""
        return self.move_human_like(x, y)

    def _execute_entry_with_retry(self, button_point: str, trade: TradeRecord) -> bool:
        for attempt in range(2):
            if not self._mouse_click_with_input(button_point, trade, fill_sl=True, fill_tp=True):
                continue
            time.sleep(1.0)
            if self._position_open_visible():
                logger.info("Position-open verification succeeded for %s on attempt %s", trade.asset, attempt + 1)
                return True
            logger.warning("Position-open verification failed for %s on attempt %s", trade.asset, attempt + 1)
        return False

    def _execute_buy(self, trade: TradeRecord) -> bool:
        logger.info(f"[AUTONOMOUS] Executing BUY on {trade.asset} via RPA Hand")
        return self._execute_entry_with_retry("buy_button", trade)

    def _execute_sell(self, trade: TradeRecord) -> bool:
        logger.info(f"[AUTONOMOUS] Executing SELL on {trade.asset} via RPA Hand (RED sell path)")
        logger.info("FORCED SELL STRIKE: moving to red sell_button coordinates for %s", trade.asset)
        return self._execute_entry_with_retry("sell_button", trade)

    def _execute_close(self, trade: TradeRecord) -> bool:
        logger.info(f"[AUTONOMOUS] Executing CLOSE on {trade.asset} via RPA Hand")
        if self.use_hotkeys:
            return self._send_hotkey(self.hotkey_close)
        return self._mouse_click("close_button", ticker_hint=trade.asset)

    def _send_hotkey(self, hotkey: str) -> bool:
        """Send keyboard hotkey combination. Format: '<ctrl>+b'."""
        try:
            parts = hotkey.lower().replace("<", "").replace(">", "").split("+")
            if len(parts) == 2:
                modifier, key = parts
                self.pyautogui.hotkey(modifier, key)
                logger.info(f"Hotkey sent: {hotkey}")
                time.sleep(random.uniform(0.3, 0.7))
                return True
            else:
                logger.error(f"Invalid hotkey format: {hotkey}")
                return False
        except Exception as e:
            logger.error(f"Failed to send hotkey {hotkey}: {e}")
            return False

    def _get_abs_coord(self, point_name: str, ticker_hint: Optional[str] = None) -> Tuple[int, int]:
        """Convert relative calibrated coordinate to absolute screen coordinate."""
        rel_x, rel_y = self.calibration_manager.get_coordinate(point_name)
        if (rel_x, rel_y) == (0, 0):
            return (0, 0)
        
        win = self._get_browser_window(ticker_hint=ticker_hint)
        if win:
            abs_x = win.left + rel_x
            abs_y = win.top + rel_y
            return (abs_x, abs_y)
        
        # Fallback to coordinate as absolute if window not found
        return (rel_x, rel_y)

    def _mouse_click(self, point_name: str, ticker_hint: Optional[str] = None) -> bool:
        """Humanized click at a calibrated position relative to browser."""
        if not self.pyautogui:
            logger.error("Mouse execution not available")
            return False

        x, y = self._get_abs_coord(point_name, ticker_hint=ticker_hint)
        if (x, y) == (0, 0):
            logger.error(f"No calibration for '{point_name}' — cannot click")
            return False

        _human_click(self.pyautogui, x, y)
        logger.info(f"Clicked {point_name} at absolute ({x}, {y})")
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
        Humanized click + input sequence with relative coordinates.
        """
        if not self.pyautogui:
            logger.error("Mouse execution not available")
            return False

        if button_point == "sell_button":
            logger.info("Bearish execution path confirmed - using sell_button coordinates")

        # Step 1: Click the buy/sell button
        if not self._mouse_click(button_point, ticker_hint=trade.asset):
            return False

        # Human pause — waiting for order dialog to open
        time.sleep(random.uniform(0.4, 0.9))

        # Step 2: Fill Stop Loss
        if fill_sl and trade.stop_loss:
            sl_x, sl_y = self._get_abs_coord("sl_input", ticker_hint=trade.asset)
            if (sl_x, sl_y) != (0, 0):
                _human_click(self.pyautogui, sl_x, sl_y)
                time.sleep(random.uniform(0.15, 0.35))
                self.pyautogui.hotkey("ctrl", "a")
                time.sleep(random.uniform(0.05, 0.15))
                _human_type(self.pyautogui, str(trade.stop_loss))
                logger.info(f"Filled Stop Loss: {trade.stop_loss}")
                # ── Human Hesitation ─────────────────────────────────────
                # 0.5 s pause after entering Stop Loss, before the next action.
                # Mimics the natural moment a human re-reads their own input.
                hesitation = 0.5 + random.uniform(0.0, 0.18)
                logger.debug(f"Human hesitation: {hesitation:.2f}s after SL entry")
                time.sleep(hesitation)
                _jitter()

        # Step 3: Fill Take Profit
        if fill_tp and trade.take_profit:
            tp_x, tp_y = self._get_abs_coord("tp_input", ticker_hint=trade.asset)
            if (tp_x, tp_y) != (0, 0):
                _human_click(self.pyautogui, tp_x, tp_y)
                time.sleep(random.uniform(0.15, 0.35))
                self.pyautogui.hotkey("ctrl", "a")
                time.sleep(random.uniform(0.05, 0.15))
                _human_type(self.pyautogui, str(trade.take_profit))
                logger.info(f"Filled Take Profit: {trade.take_profit}")
                _jitter()

        # Step 4: Fill Lot Size if calibrated
        lot_x, lot_y = self._get_abs_coord("lot_size_input", ticker_hint=trade.asset)
        if (lot_x, lot_y) != (0, 0):
            _human_click(self.pyautogui, lot_x, lot_y)
            time.sleep(random.uniform(0.15, 0.35))
            self.pyautogui.hotkey("ctrl", "a")
            time.sleep(random.uniform(0.05, 0.15))
            _human_type(self.pyautogui, "0.01")
            logger.info("Filled Lot Size: 0.01")
            _jitter()

        # Step 5: Click Confirm — try visual match first, fall back to calibration
        confirm_pos = self._find_button_by_image(CONFIRM_BUTTON_IMAGE, confidence=0.80)
        if confirm_pos:
            cx, cy = confirm_pos
            logger.info(f"Visual Confirm: clicking matched button at ({cx}, {cy})")
            _human_click(self.pyautogui, cx, cy)
        else:
            logger.debug("No visual match for Confirm — using calibrated coordinate")
            self._mouse_click("confirm_button", ticker_hint=trade.asset)

        logger.info(f"Trade executed: {trade.action.value} {trade.asset}")
        return True
