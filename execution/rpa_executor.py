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
import ctypes
from typing import Callable, Dict, List, Optional, Tuple

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
MOUSE_MOVE_MIN = 0.15  # Reduced from 0.25 for faster execution
MOUSE_MOVE_MAX = 0.35  # Reduced from 0.65 for faster execution

# Jitter delays between actions (seconds)
ACTION_JITTER_MIN = 0.15  # Reduced from 0.3 for faster execution
ACTION_JITTER_MAX = 0.35  # Reduced from 0.8 for faster execution

# Keystroke typing delay per character (seconds)
TYPE_DELAY_MIN = 0.02
TYPE_DELAY_MAX = 0.08

# Bezier curve control point offset — adds natural arc to mouse path
BEZIER_CONTROL_OFFSET = 80

# Window focus settle delay — gives Windows time to foreground the browser
WINDOW_SETTLE_DELAY = 1.5

# Predator-Class Stealth: Extended reaction delay for Apex/TopStep stealth
REACTION_DELAY_MIN = 0.3  # Reduced from 0.8 for faster execution
REACTION_DELAY_MAX = 0.6  # Reduced from 1.6 for faster execution


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
    """
    Move to target with Bezier curve, then click.
    
    Predator-Class Stealth: Uses extended reaction delay (0.8s-1.6s) to mimic
    human cognitive processing time and evade prop firm detection algorithms.
    """
    _human_move(pyautogui, x, y)
    # Predator-Class reaction delay: 0.8s to 1.6s (human cognitive processing)
    time.sleep(random.uniform(REACTION_DELAY_MIN, REACTION_DELAY_MAX))
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
        self.last_failure_reason = ""

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

    def get_permission_status(self) -> dict:
        """Return a best-effort snapshot of OS privileges required for mouse control."""
        status = {
            "is_admin": False,
            "pyautogui_loaded": bool(self.pyautogui),
            "pygetwindow_loaded": bool(self._gw),
            "mouse_accessible": False,
            "mouse_control_ready": False,
            "mouse_error": "",
        }

        try:
            status["is_admin"] = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception as e:
            status["mouse_error"] = str(e)

        if self.pyautogui:
            try:
                self.pyautogui.position()
                status["mouse_accessible"] = True
            except Exception as e:
                status["mouse_error"] = str(e)

        status["mouse_control_ready"] = bool(
            status["pyautogui_loaded"] and status["pygetwindow_loaded"] and status["mouse_accessible"]
        )
        return status

    def assert_permissions_or_die(self):
        """Check all permissions at startup. Log FATAL and raise if the Hand cannot operate."""
        status = self.get_permission_status()
        problems = []

        if not status["is_admin"]:
            problems.append("NOT running as Administrator — mouse may be blocked by UAC-elevated windows")

        if not status["pyautogui_loaded"]:
            problems.append("PyAutoGUI failed to load — mouse movement impossible")

        if not status["pygetwindow_loaded"]:
            problems.append("PyGetWindow failed to load — window detection impossible")

        if not status["mouse_accessible"]:
            problems.append(f"Mouse inaccessible — {status['mouse_error'] or 'unknown error'}")

        if problems:
            banner = (
                "\n"
                "╔══════════════════════════════════════════════════════════════╗\n"
                "║          ⚠  RPA HAND PERMISSION CHECK FAILED  ⚠            ║\n"
                "╠══════════════════════════════════════════════════════════════╣\n"
            )
            for p in problems:
                banner += f"║  • {p:<57}║\n"
            banner += (
                "╠══════════════════════════════════════════════════════════════╣\n"
                "║  FIX: Right-click → 'Run as administrator'                  ║\n"
                "╚══════════════════════════════════════════════════════════════╝\n"
            )
            logger.fatal(banner)
            print(banner)

            # Fatal if mouse literally cannot move — no point continuing
            if not status["mouse_control_ready"]:
                raise RuntimeError(
                    f"RPA Hand cannot operate: {'; '.join(problems)}"
                )
        else:
            logger.info("RPA Hand permission check PASSED (admin=%s)", status["is_admin"])

    # Fuzzy fallback keywords — if TradingView/ticker not found, match any
    # browser window whose title contains one of these trading-related terms.
    _FUZZY_KEYWORDS = [
        "BTC", "ETH", "NQ", "ES", "CHART", "TRADE", "TRADING",
        "FOREX", "FUTURES", "CRYPTO", "BINANCE", "BYBIT", "COINBASE",
        "METATRADER", "MT4", "MT5", "OANDA", "EXNESS",
    ]
    # PREDATOR-CLASS BLACKLIST: Strict terminal/editor exclusion list
    # The RPA Hand will NEVER target these windows under any circumstances
    _WINDOW_TITLE_BLACKLIST = [
        "POWERSHELL",
        "PWSH",
        "CMD",
        "COMMAND PROMPT",
        "TERMINAL",
        "VISUAL STUDIO CODE",
        "VSCODE",
        "PYTHON",
        "CONSOLE",
        "GIT BASH",
        "WSL",
        "UBUNTU",
        "DEVELOPER",
        "DEBUG",
        "ADMINISTRATOR",
    ]
    _PREFERRED_BROWSER_HINTS = ["GOOGLE CHROME", "MICROSOFT EDGE", "BRAVE"]
    # Allowed browser titles when TradingView itself is not present
    _BROWSER_HINTS = ["GOOGLE CHROME", "MICROSOFT EDGE", "BRAVE"]

    def _is_blacklisted_window_title(self, title: str) -> bool:
        """Return True when a title belongs to a terminal/editor window we must ignore."""
        title_u = (title or "").strip().upper()
        return bool(title_u) and any(fragment in title_u for fragment in self._WINDOW_TITLE_BLACKLIST)

    def _is_browser_window_title(self, title: str) -> bool:
        """Return True when the title appears to belong to a supported browser."""
        title_u = (title or "").strip().upper()
        return any(fragment in title_u for fragment in self._BROWSER_HINTS)

    def _is_preferred_browser_title(self, title: str) -> bool:
        """Return True for the preferred browser engines when TradingView is absent."""
        title_u = (title or "").strip().upper()
        return any(fragment in title_u for fragment in self._PREFERRED_BROWSER_HINTS)

    def _get_browser_window(self, ticker_hint: Optional[str] = None):
        """Find chart window with aggressive fuzzy matching.

        Search priority:
          1. Window title contains 'TradingView'
          2. Allowed browser window with the ticker symbol
          3. Allowed browser window with a trading keyword
          4. Any allowed browser window (Chrome/Edge/Brave)

        If none of the allowed browser targets are present, abort by returning None.
        """
        if not self._gw:
            return None

        ticker_hint = (ticker_hint or "").replace("=F", "").split("-")[0].strip().upper()

        tier1 = []  # TradingView exact
        tier2 = []  # Allowed browser + ticker
        tier3 = []  # Allowed browser + fuzzy keyword
        tier4 = []  # Any allowed browser

        for w in self._gw.getAllWindows():
            title = (w.title or "").strip()
            if not title:
                continue
            title_u = title.upper()
            if self._is_blacklisted_window_title(title_u):
                logger.debug("Skipping blacklisted window title: '%s'", title)
                continue

            is_preferred_browser = self._is_preferred_browser_title(title_u)
            is_browser = self._is_browser_window_title(title_u)
            has_ticker = bool(ticker_hint and ticker_hint in title_u)
            has_fuzzy_keyword = any(kw in title_u for kw in self._FUZZY_KEYWORDS)

            if "TRADINGVIEW" in title_u:
                tier1.append(w)
            elif is_browser and has_ticker:
                tier2.append(w)
            elif is_browser and has_fuzzy_keyword:
                tier3.append(w)
            elif is_browser:
                tier4.append(w)

        # Pick first non-minimized window from the highest-priority tier
        for candidates in (tier1, tier2, tier3, tier4):
            if not candidates:
                continue
            for win in candidates:
                try:
                    if not win.isMinimized:
                        logger.debug("Window matched (tier): '%s'", win.title)
                        return win
                except AttributeError:
                    # Window object doesn't have isMinimized - assume it's valid
                    logger.debug("Window matched (no isMinimized attr): '%s'", win.title)
                    return win
                except Exception as e:
                    # Log the specific error and skip this window
                    logger.warning(f"Error checking window '{win.title}': {e}")
                    continue
            
            # All candidates minimized — return first anyway so we can restore it
            logger.debug("All candidates minimized, returning '%s'", candidates[0].title)
            return candidates[0]

        return None

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
        if self._is_blacklisted_window_title(title_u):
            return False
        if "TRADINGVIEW" in title_u:
            return True
        is_browser = self._is_browser_window_title(title_u)
        if ticker_hint_u and ticker_hint_u in title_u and is_browser:
            return True
        # Fuzzy: accept trading-related titles only when they are inside a browser window
        if is_browser and any(kw in title_u for kw in self._FUZZY_KEYWORDS):
            return True
        if self._is_preferred_browser_title(title_u):
            return True
        if is_browser:
            return True
        return False

    def _cycle_tabs_until_match(self, ticker_hint: Optional[str] = None, attempts: int = 8) -> bool:
        """Cycle browser tabs until TradingView or ticker title becomes active.
        
        IMPROVED: Now logs each failed attempt and final failure reason.
        """
        if not self.pyautogui:
            return False
        
        initial_title = self._active_window_title()
        logger.debug(f"Starting tab cycle from: '{initial_title}'")
        
        for i in range(max(1, attempts)):
            title = self._active_window_title()
            if self._title_matches_target(title, ticker_hint=ticker_hint):
                logger.info(f"Tab match found after {i+1} cycles: '{title}'")
                return True
            
            logger.debug(f"Tab cycle {i+1}/{attempts}: '{title}' → cycling...")
            self.pyautogui.hotkey("ctrl", "tab")
            time.sleep(0.3)
        
        final_title = self._active_window_title()
        logger.warning(
            f"Tab cycling exhausted ({attempts} attempts). "
            f"Started: '{initial_title}', Ended: '{final_title}'. "
            f"Target matcher: ticker='{ticker_hint}'"
        )
        return self._title_matches_target(final_title, ticker_hint=ticker_hint)

    def _alt_tab_into_view(self, ticker_hint: Optional[str] = None, attempts: int = 2) -> bool:
        """Physically cycle top-level windows until TradingView is foreground."""
        if not self.pyautogui:
            return False

        for _ in range(max(1, attempts)):
            if self._title_matches_target(self._active_window_title(), ticker_hint=ticker_hint):
                return True
            self.pyautogui.hotkey("alt", "tab")
            time.sleep(0.35)

        return self._title_matches_target(self._active_window_title(), ticker_hint=ticker_hint)

    def _resolve_window_title(self, ticker_hint: Optional[str] = None) -> str:
        win = self._get_browser_window(ticker_hint=ticker_hint)
        if win and getattr(win, "title", ""):
            return str(win.title)
        return self._active_window_title() or "UNKNOWN"

    def _log_move_attempt(self, x: int, y: int, ticker_hint: Optional[str] = None, window_title: Optional[str] = None) -> None:
        resolved_title = window_title or self._resolve_window_title(ticker_hint=ticker_hint)
        logger.info("[RPA] Moving to X: %s, Y: %s on Window: %s", x, y, resolved_title)

    def _ensure_window_frontmost(self, ticker_hint: Optional[str] = None) -> bool:
        """Force TradingView to the absolute front before any physical click."""
        if self._title_matches_target(self._active_window_title(), ticker_hint=ticker_hint):
            return True
        return self.bring_tradingview_to_front(ticker_hint=ticker_hint)

    def _move_cursor_logged(self, x: int, y: int, ticker_hint: Optional[str] = None, ensure_focus: bool = False) -> bool:
        if not self.pyautogui:
            logger.error("Mouse execution not available")
            return False
        if ensure_focus and not self._ensure_window_frontmost(ticker_hint=ticker_hint):
            return False
        self._log_move_attempt(int(x), int(y), ticker_hint=ticker_hint)
        _human_move(self.pyautogui, int(x), int(y))
        return True

    def _click_cursor_logged(self, x: int, y: int, ticker_hint: Optional[str] = None) -> bool:
        if not self.pyautogui:
            logger.error("Mouse execution not available")
            return False
        if not self._ensure_window_frontmost(ticker_hint=ticker_hint):
            return False
        self._log_move_attempt(int(x), int(y), ticker_hint=ticker_hint)
        _human_click(self.pyautogui, int(x), int(y))
        return True

    def _force_focus_tradingview(self, win, ticker_hint: Optional[str] = None) -> bool:
        """Hard-Focus routine: restore → maximize → activate → Alt-Tab fallback.

        Three escalation stages before giving up:
          Stage 1: restore + maximize + activate (standard pygetwindow)
          Stage 2: Alt-Tab cycling to bring window forward
          Stage 3: Win32 SetForegroundWindow via ctypes
        """
        candidate_title = getattr(win, "title", "") or ""
        if self._is_blacklisted_window_title(candidate_title):
            logger.warning("Rejected blacklisted focus target: '%s'", candidate_title)
            return False

        # ── Stage 1: standard pygetwindow focus ──────────────────────────
        try:
            if win.isMinimized:
                win.restore()
                time.sleep(0.15)
            try:
                win.maximize()
                time.sleep(WINDOW_SETTLE_DELAY)
            except Exception:
                pass
            try:
                win.activate()
                time.sleep(WINDOW_SETTLE_DELAY)
            except Exception:
                pass
            try:
                win.moveTo(config.TRADINGVIEW_WINDOW_X, config.TRADINGVIEW_WINDOW_Y)
            except Exception:
                pass
        except Exception as e:
            logger.warning("Stage-1 focus failed: %s — escalating", e)

        # Check after Stage 1
        if self._title_matches_target(self._active_window_title(), ticker_hint=ticker_hint):
            logger.info("Hard-Focus locked (stage 1) for %s", ticker_hint or "current symbol")
            return True

        # ── Stage 2: Alt-Tab + Ctrl-Tab cycling ──────────────────────────
        self._alt_tab_into_view(ticker_hint=ticker_hint, attempts=3)
        time.sleep(0.15)
        try:
            win.activate()
            time.sleep(WINDOW_SETTLE_DELAY)
        except Exception:
            pass
        if not self._cycle_tabs_until_match(ticker_hint=ticker_hint, attempts=10):
            logger.warning("Stage-2 tab cycling could not match — escalating")
        else:
            logger.info("Hard-Focus locked (stage 2) for %s", ticker_hint or "current symbol")
            return True

        # ── Stage 3: Win32 SetForegroundWindow ───────────────────────────
        try:
            hwnd = getattr(win, '_hWnd', None)
            if hwnd:
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                time.sleep(0.20)
                if self._title_matches_target(self._active_window_title(), ticker_hint=ticker_hint):
                    logger.info("Hard-Focus locked (stage 3 / SetForegroundWindow) for %s", ticker_hint or "current symbol")
                    return True
        except Exception as e:
            logger.debug("Stage-3 SetForegroundWindow failed: %s", e)

        # ── Final check — accept any fuzzy match ─────────────────────────
        fg_title = self._active_window_title()
        if self._title_matches_target(fg_title, ticker_hint=ticker_hint):
            logger.info("Hard-Focus locked (final fuzzy) for %s: '%s'", ticker_hint or "current symbol", fg_title)
            return True

        self._fire_blind_error(f"Hard-Focus FAILED after 3 stages for {ticker_hint or 'current symbol'} (fg='{fg_title}')")
        return False

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
        self.last_failure_reason = f"Visibility gate | {reason}"
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
        self.last_failure_reason = ""
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
                self.last_failure_reason = f"Unsupported action | {trade.action}"
                logger.warning(f"Unknown action: {trade.action}")
                return False
        except Exception as e:
            self.last_failure_reason = f"RPA execution error | {e}"
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

            if not self._click_cursor_logged(rect[0], rect[1], ticker_hint=ticker):
                return False
            time.sleep(0.2)
            self._log_move_attempt(start_x, top_y, ticker_hint=ticker)
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

        if not self._click_cursor_logged(x, y, ticker_hint=ticker_hint):
            return False
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
        if not self._move_cursor_logged(int(x), int(y)):
            return False
        logger.info("Human-move diagnostic completed to (%s, %s)", x, y)
        return True

    def _move_human_like(self, x: int, y: int) -> bool:
        """Backwards-compatible alias for terminal-based hand diagnostics."""
        return self.move_human_like(x, y)

    def _execute_entry_with_retry(self, button_point: str, trade: TradeRecord) -> bool:
        """Enhanced retry with window refocus between attempts."""
        max_attempts = 3
        
        for attempt in range(max_attempts):
            logger.info(f"Execution attempt {attempt + 1}/{max_attempts} for {trade.asset}")
            
            # Refocus window before each attempt (except first)
            if attempt > 0:
                logger.warning(f"Refocusing window after failed attempt {attempt}")
                time.sleep(0.3)
                self.bring_tradingview_to_front(ticker_hint=trade.asset)
            
            if not self._mouse_click_with_input(button_point, trade, fill_sl=True, fill_tp=True):
                logger.warning(f"Attempt {attempt + 1} failed for {trade.asset}")
                time.sleep(0.5)  # Brief pause before retry
                continue
                
            # Wait for position confirmation
            time.sleep(1.5)  # Increased from 1.0s
            
            if self._position_open_visible():
                logger.info(f"Position verified for {trade.asset} on attempt {attempt + 1}")
                return True
                
            logger.warning(f"Position verification failed for {trade.asset} on attempt {attempt + 1}")
        
        logger.error(f"All {max_attempts} attempts failed for {trade.asset}")
        self.last_failure_reason = f"Failed after {max_attempts} attempts"
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

    # Screen-percentage fallback offsets for common broker UI elements.
    # These are used ONLY when calibration.json is missing/empty.
    # Based on standard TradingView paper-trading panel layout.
    _FALLBACK_OFFSETS: Dict[str, Tuple[float, float]] = {
        "buy_button":     (0.85, 0.40),   # 85% width, 40% height
        "sell_button":    (0.85, 0.52),   # 85% width, 52% height
        "close_button":   (0.85, 0.64),   # 85% width, 64% height
        "sl_input":       (0.82, 0.46),   # 82% width, 46% height
        "tp_input":       (0.82, 0.54),   # 82% width, 54% height
        "lot_size_input": (0.82, 0.38),   # 82% width, 38% height
        "confirm_button": (0.50, 0.62),   # 50% width, 62% height (center dialog)
    }

    def _get_abs_coord(self, point_name: str, ticker_hint: Optional[str] = None) -> Tuple[int, int]:
        """Convert relative calibrated coordinate to absolute screen coordinate.

        Falls back to screen-percentage offsets when calibration is missing
        so the Hand never returns (0, 0) for essential buttons.
        """
        rel_x, rel_y = self.calibration_manager.get_coordinate(point_name)
        win = self._get_browser_window(ticker_hint=ticker_hint)

        if (rel_x, rel_y) != (0, 0):
            # Normal calibrated path
            if win:
                return (win.left + rel_x, win.top + rel_y)
            return (rel_x, rel_y)

        # ── Calibration bypass: screen-percentage fallback ────────────────
        if point_name in self._FALLBACK_OFFSETS:
            pct_x, pct_y = self._FALLBACK_OFFSETS[point_name]
            if win:
                abs_x = win.left + int(win.width * pct_x)
                abs_y = win.top + int(win.height * pct_y)
            else:
                # Last resort: use full screen dimensions
                try:
                    screen_w, screen_h = self.pyautogui.size()
                except Exception:
                    screen_w, screen_h = 1920, 1080
                abs_x = int(screen_w * pct_x)
                abs_y = int(screen_h * pct_y)
            logger.warning(
                "[CALIBRATION BYPASS] Using fallback %%offset for '%s' → (%s, %s)",
                point_name, abs_x, abs_y,
            )
            return (abs_x, abs_y)

        return (0, 0)

    def _mouse_click(self, point_name: str, ticker_hint: Optional[str] = None) -> bool:
        """Humanized click at a calibrated position relative to browser."""
        if not self.pyautogui:
            logger.error("Mouse execution not available")
            return False

        x, y = self._get_abs_coord(point_name, ticker_hint=ticker_hint)
        if (x, y) == (0, 0):
            logger.error(f"No calibration for '{point_name}' — cannot click")
            return False

        if not self._click_cursor_logged(x, y, ticker_hint=ticker_hint):
            return False
        logger.info(f"Clicked {point_name} at absolute ({x}, {y})")
        _jitter()
        return True

    def _format_price_input(self, price: float) -> str:
        """Format a price for TradingView input fields without trailing zero noise."""
        return f"{float(price):.4f}".rstrip("0").rstrip(".")

    def update_stop_loss(self, stop_loss: float, ticker_hint: Optional[str] = None) -> bool:
        """Edit the visible TradingView stop-loss field for an open position."""
        if not self.pyautogui:
            logger.error("Mouse execution not available")
            return False
        if stop_loss <= 0:
            logger.error("Invalid stop loss update requested: %s", stop_loss)
            return False
        if not self._verify_window_visible(ticker_hint=ticker_hint):
            return False

        sl_x, sl_y = self._get_abs_coord("sl_input", ticker_hint=ticker_hint)
        if (sl_x, sl_y) == (0, 0):
            logger.error("No calibration for 'sl_input' — cannot update stop loss")
            return False

        try:
            if not self._click_cursor_logged(sl_x, sl_y, ticker_hint=ticker_hint):
                return False
            time.sleep(random.uniform(0.15, 0.35))
            self.pyautogui.hotkey("ctrl", "a")
            time.sleep(random.uniform(0.05, 0.15))
            _human_type(self.pyautogui, self._format_price_input(stop_loss))
            time.sleep(random.uniform(0.05, 0.15))
            self.pyautogui.press("enter")
            logger.info("Updated Stop Loss to %s for %s", stop_loss, ticker_hint or "current chart")
            _jitter()
            return True
        except Exception as e:
            logger.error("Failed to update Stop Loss for %s: %s", ticker_hint or "current chart", e)
            return False

    def force_strike_test(self, action: str = "BUY", ticker_hint: Optional[str] = None) -> bool:
        """Immediate calibrated left-click for live RPA strike diagnostics."""
        action_upper = str(action or "BUY").upper()
        if action_upper not in {"BUY", "SELL"}:
            self.last_failure_reason = f"Force strike unsupported action | {action_upper}"
            logger.error("Force strike test aborted: unsupported action %s", action_upper)
            return False

        point_name = "buy_button" if action_upper == "BUY" else "sell_button"
        x, y = self._get_abs_coord(point_name, ticker_hint=ticker_hint)
        if (x, y) == (0, 0):
            self.last_failure_reason = f"Force strike missing calibration | {point_name}"
            logger.error("Force strike test aborted: no calibration for %s", point_name)
            return False

        if not self._click_cursor_logged(x, y, ticker_hint=ticker_hint):
            if not self.last_failure_reason:
                self.last_failure_reason = f"Force strike failed | unable to click {point_name}"
            return False

        logger.warning("FORCE STRIKE TEST: clicked %s at absolute (%s, %s) for %s", point_name, x, y, ticker_hint or "current chart")
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

        # FORCE window focus before ANY action - critical for reliability
        if not self._verify_window_visible(ticker_hint=trade.asset):
            logger.error(f"Cannot execute {button_point} - window not visible for {trade.asset}")
            return False

        if button_point == "sell_button":
            logger.info("Bearish execution path confirmed - using sell_button coordinates")

        # Step 1: Click the buy/sell button
        if not self._mouse_click(button_point, ticker_hint=trade.asset):
            return False

        # Human pause — waiting for order dialog to open (reduced from 0.4-0.9s)
        time.sleep(random.uniform(0.2, 0.5))

        # Step 2: Fill Stop Loss
        if fill_sl and trade.stop_loss:
            sl_x, sl_y = self._get_abs_coord("sl_input", ticker_hint=trade.asset)
            if (sl_x, sl_y) != (0, 0):
                if not self._click_cursor_logged(sl_x, sl_y, ticker_hint=trade.asset):
                    return False
                time.sleep(random.uniform(0.15, 0.35))
                self.pyautogui.hotkey("ctrl", "a")
                time.sleep(random.uniform(0.05, 0.15))
                _human_type(self.pyautogui, str(trade.stop_loss))
                logger.info(f"Filled Stop Loss: {trade.stop_loss}")
                # ── Human Hesitation ─────────────────────────────────────
                # Variable pause after entering Stop Loss (0.3s to 1.2s range)
                # Uses weighted random to favor realistic human delays:
                #   - 60% chance: quick review (0.3-0.6s)
                #   - 30% chance: medium pause (0.6-0.9s)  
                #   - 10% chance: deep thought (0.9-1.2s)
                hesitation_roll = random.random()
                if hesitation_roll < 0.6:
                    hesitation = random.uniform(0.3, 0.6)
                elif hesitation_roll < 0.9:
                    hesitation = random.uniform(0.6, 0.9)
                else:
                    hesitation = random.uniform(0.9, 1.2)

                logger.debug(f"Human hesitation: {hesitation:.2f}s after SL entry (roll={hesitation_roll:.2f})")
                time.sleep(hesitation)
                _jitter()

        # Step 3: Fill Take Profit
        if fill_tp and trade.take_profit:
            tp_x, tp_y = self._get_abs_coord("tp_input", ticker_hint=trade.asset)
            if (tp_x, tp_y) != (0, 0):
                if not self._click_cursor_logged(tp_x, tp_y, ticker_hint=trade.asset):
                    return False
                time.sleep(random.uniform(0.15, 0.35))
                self.pyautogui.hotkey("ctrl", "a")
                time.sleep(random.uniform(0.05, 0.15))
                _human_type(self.pyautogui, str(trade.take_profit))
                logger.info(f"Filled Take Profit: {trade.take_profit}")
                _jitter()

        # Step 4: Fill Lot Size if calibrated
        lot_x, lot_y = self._get_abs_coord("lot_size_input", ticker_hint=trade.asset)
        if (lot_x, lot_y) != (0, 0):
            if not self._click_cursor_logged(lot_x, lot_y, ticker_hint=trade.asset):
                return False
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
            if not self._click_cursor_logged(cx, cy, ticker_hint=trade.asset):
                return False
        else:
            logger.debug("No visual match for Confirm — using calibrated coordinate")
            if not self._mouse_click("confirm_button", ticker_hint=trade.asset):
                return False

        logger.info(f"Trade executed: {trade.action.value} {trade.asset}")
        return True
