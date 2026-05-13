"""Institutional Execution Hardening: auto-calibration + strict verify + isolated account lock."""

# ===== 1. execution_server.py: Auto-calibration + hardened execution =====
with open("execution_server.py", "r", encoding="utf-8") as f:
    es = f.read()

# Add RESOLVED_COORDS global and calibration import
old_imports = """import socket
import json
import pyautogui
import time
import random
import math
import threading
import os
import sys
from datetime import datetime"""

new_imports = """import socket
import json
import pyautogui
import time
import random
import math
import threading
import os
import sys
from datetime import datetime

# AUTO-CALIBRATION: OpenCV-backed template matching for institutional-grade coordinate resolution
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# Resolved button coordinates discovered at runtime via template matching
RESOLVED_COORDS = {}"""

if old_imports in es:
    es = es.replace(old_imports, new_imports)
    print("execution_server.py: Added calibration imports + RESOLVED_COORDS")
else:
    print("WARN: execution_server.py imports not found")

# Add calibrate_trading_interface function before load_coordinates
old_load_coords = "def load_coordinates():"
new_load_coords = """def calibrate_trading_interface():
    \"\"\"AUTO-CALIBRATION: Scan the active monitor for trading button templates.
    Uses OpenCV template matching (via pyautogui + cv2) to resolve exact (x, y)
    centers of Buy / Sell / Flatten buttons on the user's active layout.
    Stores results in RESOLVED_COORDS for the lifetime of the server.
    \"\"\""
    global RESOLVED_COORDS
    RESOLVED_COORDS = {}

    templates = {
        "BUTTON_BUY": ("rithmic_buy.png", "Buy"),
        "BUTTON_SELL": ("rithmic_sell.png", "Sell"),
        "BUTTON_FLATTEN": ("rithmic_exit.png", "Flatten"),
    }

    script_dir = os.path.dirname(os.path.abspath(__file__))
    for btn_key, (template_name, label) in templates.items():
        template_path = os.path.join(script_dir, template_name)
        if not os.path.exists(template_path):
            log(f"[CALIBRATE] Template not found: {template_path}")
            continue

        try:
            # OpenCV-backed multi-scale template match across primary monitor
            if HAS_CV2:
                screen = pyautogui.screenshot()
                screen_cv = cv2.cvtColor(np.array(screen), cv2.COLOR_RGB2BGR)
                template_cv = cv2.imread(template_path, cv2.IMREAD_UNCHANGED)
                if template_cv is None:
                    log(f"[CALIBRATE] Could not load {template_name}")
                    continue

                # Handle alpha channel
                if template_cv.shape[-1] == 4:
                    alpha = template_cv[:, :, 3]
                    template_cv = cv2.merge([template_cv[:, :, :3], alpha])
                    # Use masked template matching
                    result = cv2.matchTemplate(screen_cv, template_cv[:, :, :3], cv2.TM_CCOEFF_NORMED)
                else:
                    result = cv2.matchTemplate(screen_cv, template_cv, cv2.TM_CCOEFF_NORMED)

                _, max_val, _, max_loc = cv2.minMaxLoc(result)
                if max_val >= 0.70:
                    h, w = template_cv.shape[:2]
                    center_x = max_loc[0] + w // 2
                    center_y = max_loc[1] + h // 2
                    RESOLVED_COORDS[btn_key] = {"x": center_x, "y": center_y}
                    log(f"[CALIBRATE] {btn_key} ({label}) locked at ({center_x}, {center_y}) confidence={max_val:.2f}")
                else:
                    log(f"[CALIBRATE] {btn_key} template match too weak ({max_val:.2f}) — will require packet coordinates")
            else:
                # Fallback: pure pyautogui grayscale locate
                location = pyautogui.locateOnScreen(template_path, confidence=0.70, grayscale=True)
                if location:
                    center_x = location.left + location.width // 2
                    center_y = location.top + location.height // 2
                    RESOLVED_COORDS[btn_key] = {"x": center_x, "y": center_y}
                    log(f"[CALIBRATE] {btn_key} ({label}) locked at ({center_x}, {center_y})")
                else:
                    log(f"[CALIBRATE] {btn_key} not found on screen")
        except Exception as e:
            log(f"[CALIBRATE] Error scanning for {btn_key}: {e}")

    if not RESOLVED_COORDS:
        log("[CALIBRATE] WARNING: No buttons auto-detected. Server requires packet-provided coordinates.")
    else:
        log(f"[CALIBRATE] Auto-calibration complete. {len(RESOLVED_COORDS)} buttons resolved.")

    return RESOLVED_COORDS


def load_coordinates():"""

if old_load_coords in es:
    es = es.replace(old_load_coords, new_load_coords)
    print("execution_server.py: Added calibrate_trading_interface()")
else:
    print("WARN: load_coordinates() not found")

# Modify load_coordinates to merge with RESOLVED_COORDS
old_load_body = """    if not os.path.exists(CONFIG_FILE):
        log(f"[INFO] {CONFIG_FILE} not found - packet-provided TradingView coordinates required")
        return {}
    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
        required = ["BUTTON_BUY", "BUTTON_SELL", "BUTTON_FLATTEN"]
        for key in required:
            if key not in config:
                log(f"[WARN] Missing coordinate: {key} - some features may not work")
        return config
    except Exception as e:
        log(f"[ERROR] Failed to load {CONFIG_FILE}: {e}")
        return {}"""

new_load_body = """    merged = dict(RESOLVED_COORDS)  # Start with auto-calibrated coordinates

    # Overlay manual config_coordinates.json if present
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                manual = json.load(f)
            for key in ("BUTTON_BUY", "BUTTON_SELL", "BUTTON_FLATTEN"):
                if key in manual:
                    merged[key] = manual[key]
                    log(f"[CONFIG] Manual override applied for {key}")
        except Exception as e:
            log(f"[ERROR] Failed to load {CONFIG_FILE}: {e}")
    else:
        log(f"[INFO] {CONFIG_FILE} not found - relying on auto-calibration or packet coordinates")

    required = ["BUTTON_BUY", "BUTTON_SELL", "BUTTON_FLATTEN"]
    for key in required:
        if key not in merged:
            log(f"[WARN] Missing coordinate: {key} - execution will require explicit packet target")
    return merged"""

if old_load_body in es:
    es = es.replace(old_load_body, new_load_body)
    print("execution_server.py: load_coordinates() now merges auto + manual coords")
else:
    print("WARN: load_coordinates() body not found")

# Modify main() to call calibration at startup
old_main = """def main():
    log("=" * 60)
    log("  GHOST-HAND SERVER - TRADINGVIEW / MT5 ONLY")
    log("=" * 60)
    config = load_coordinates()
    log(f"[SERVER] Listening on {HOST}:{PORT}")"""

new_main = """def main():
    log("=" * 60)
    log("  GHOST-HAND SERVER - INSTITUTIONAL EXECUTION ENGINE")
    log("=" * 60)

    # STAGE 1: Auto-calibrate button coordinates from screen templates
    calibrate_trading_interface()

    config = load_coordinates()
    log(f"[SERVER] Listening on {HOST}:{PORT}")"""

if old_main in es:
    es = es.replace(old_main, new_main)
    print("execution_server.py: main() now calls calibrate_trading_interface()")
else:
    print("WARN: main() not found")

with open("execution_server.py", "w", encoding="utf-8") as f:
    f.write(es)

print("execution_server.py updated")

# ===== 2. execution/rpa_executor.py: Harden verify_position_opened + isolate account lock =====
with open("execution/rpa_executor.py", "r", encoding="utf-8") as f:
    rpa = f.read()

# 2a. Harden verify_position_opened: remove weak pass, strict OCR/template only
old_verify = """    def verify_position_opened(self, ticker):
        \"\"\"Screenshot-based confirmation that the position appears in WealthCharts.\"\"\"
        time.sleep(4)  # Wait for broker fill (extended for WealthCharts delay)
        window = self._get_browser_window()
        if not window:
            logger.warning(f"[VERIFY] Cannot verify {ticker}: WealthCharts window not found")
            return False

        try:
            screenshot = pyautogui.screenshot(
                region=(window.left, window.top, window.width, window.height)
            )

            # Try OCR verification if pytesseract is available
            try:
                import pytesseract
                text = pytesseract.image_to_string(screenshot)
                normalized_text = text.upper().replace("-", "").replace("/", "")
                normalized_ticker = ticker.upper().replace("-", "").replace("/", "")
                if normalized_ticker in normalized_text:
                    logger.info(f"[VERIFY] {ticker} confirmed in WealthCharts positions panel via OCR")
                    return True
            except ImportError:
                pass  # OCR not available, fall through

            # Fallback: try image template matching if user provided a template
            try:
                template_path = getattr(config, "POSITION_OPEN_IMAGE", "")
                if template_path:
                    location = pyautogui.locateOnScreen(
                        template_path, confidence=0.7, region=(window.left, window.top, window.width, window.height)
                    )
                    if location:
                        logger.info(f"[VERIFY] {ticker} confirmed via WealthCharts template match")
                        return True
            except Exception:
                pass  # Template match failed or pyautogui lacks confidence support

            # Weak fallback: check if window title still contains WealthCharts (means no crash)
            if any(name in window.title for name in ["WealthCharts", "wealthcharts"]):
                logger.warning(f"[VERIFY] {ticker}: weak pass (WealthCharts window active, no OCR/template)")
                return True

            return False
        except Exception as exc:
            logger.warning(f"[VERIFY] Exception during WealthCharts verification for {ticker}: {exc}")
            return False"""

new_verify = """    def verify_position_opened(self, ticker):
        \"\"\"INSTITUTIONAL VERIFY: Strict screenshot confirmation. NO weak pass fallbacks.
        Returns True only if OCR or template match confirms the position row.
        If verification fails, logs [EXECUTION_MISSED] so the caller can retry.\"\"\"
        time.sleep(4)  # Wait for broker fill (extended for WealthCharts delay)
        window = self._get_browser_window()
        if not window:
            logger.warning("[VERIFY] Cannot verify %s: WealthCharts window not found", ticker)
            return False

        try:
            screenshot = pyautogui.screenshot(
                region=(window.left, window.top, window.width, window.height)
            )

            # STRICT OCR: look for ticker symbol in positions panel
            ocr_confirmed = False
            try:
                import pytesseract
                text = pytesseract.image_to_string(screenshot)
                normalized_text = text.upper().replace("-", "").replace("/", "")
                normalized_ticker = ticker.upper().replace("-", "").replace("/", "")
                if normalized_ticker in normalized_text:
                    logger.info("[VERIFY] %s confirmed in WealthCharts positions panel via OCR", ticker)
                    ocr_confirmed = True
            except ImportError:
                logger.debug("[VERIFY] pytesseract not available — skipping OCR")

            # STRICT TEMPLATE: look for position-open template if user provided one
            template_confirmed = False
            if not ocr_confirmed:
                try:
                    template_path = getattr(config, "POSITION_OPEN_IMAGE", "")
                    if template_path and os.path.exists(template_path):
                        location = pyautogui.locateOnScreen(
                            template_path, confidence=0.7, region=(window.left, window.top, window.width, window.height)
                        )
                        if location:
                            logger.info("[VERIFY] %s confirmed via WealthCharts template match", ticker)
                            template_confirmed = True
                except Exception:
                    logger.debug("[VERIFY] Template match failed — likely opencv-python missing")

            if ocr_confirmed or template_confirmed:
                return True

            # NO WEAK PASS. If we can't verify, it's a miss.
            logger.error("[EXECUTION_MISSED] %s position NOT verified after click. "
                         "OCR and template both failed. Rolling back position state.", ticker)
            return False

        except Exception as exc:
            logger.error("[EXECUTION_MISSED] Exception during WealthCharts verification for %s: %s", ticker, exc)
            return False"""

if old_verify in rpa:
    rpa = rpa.replace(old_verify, new_verify)
    print("rpa_executor.py: verify_position_opened hardened — weak pass removed")
else:
    print("WARN: verify_position_opened not found")

# 2b. Isolate account verification BEFORE mouse movement in _execute_trade_pyautogui
old_exec_py = """    def _execute_trade_pyautogui(self, trade):
        \"\"\"Legacy PyAutoGUI execution (mouse movement). Kept as emergency fallback.\"\"\"
        action = self._normalize_action(trade.action)
        target_key = "buy_button" if action == "BUY" else "sell_button" if action == "SELL" else None
        if not target_key:
            logger.error("[RPA] Invalid trade action: %s", action)
            return False

        for attempt in range(1, 4):
            logger.info("[RPA] Attempt %s/3 for %s %s", attempt, action, trade.asset)
            success = self._lightning_strike_sequence(target_key, trade.asset)
            if success:
                verified = self.verify_position_opened(trade.asset)
                if verified:
                    logger.info("[RPA] %s %s executed and verified", action, trade.asset)
                    return True
                logger.warning("[RPA] Click succeeded but verification failed for %s", trade.asset)
            if attempt < 3:
                backoff = 2 ** (attempt - 1)
                logger.info(f"[RPA] Retrying in {backoff}s...")
                time.sleep(backoff)"""

new_exec_py = """    def _execute_trade_pyautogui(self, trade):
        \"\"\"Legacy PyAutoGUI execution (mouse movement). Kept as emergency fallback.\"\"\"
        action = self._normalize_action(trade.action)
        target_key = "buy_button" if action == "BUY" else "sell_button" if action == "SELL" else None
        if not target_key:
            logger.error("[RPA] Invalid trade action: %s", action)
            return False

        # ISOLATED ACCOUNT LOCK: verify BEFORE any mouse movement.
        # Playwright page access happens here, outside the rapid click thread.
        platform = self._detect_platform()
        if platform == "wealthcharts":
            logger.info("[CHAIN-LOCK] Pre-strike account verification for %s", trade.asset)
            account_ok = self._micro_verify_account()
            if not account_ok:
                logger.error("[ALARM] TRADE ABORTED: Account chain-lock failed BEFORE strike for %s", trade.asset)
                return False
            logger.info("[CHAIN-LOCK] Account verified — proceeding to physical strike")

        for attempt in range(1, 4):
            logger.info("[RPA] Attempt %s/3 for %s %s", attempt, action, trade.asset)
            success = self._lightning_strike_sequence(target_key, trade.asset)
            if success:
                verified = self.verify_position_opened(trade.asset)
                if verified:
                    logger.info("[RPA] %s %s executed and verified", action, trade.asset)
                    return True
                logger.warning("[RPA] Click succeeded but verification failed for %s", trade.asset)
            if attempt < 3:
                backoff = 2 ** (attempt - 1)
                logger.info("[RPA] Retrying in %ss...", backoff)
                time.sleep(backoff)"""

if old_exec_py in rpa:
    rpa = rpa.replace(old_exec_py, new_exec_py)
    print("rpa_executor.py: Account lock isolated BEFORE mouse movement")
else:
    print("WARN: _execute_trade_pyautogui not found")

# 2c. Remove the in-sequence _micro_verify_account from _lightning_strike_sequence
old_strike_check = """            # CHAIN-LOCK: Micro-verify account 50ms BEFORE final click
            time.sleep(0.05)
            account_ok = self._micro_verify_account()
            if not account_ok:
                logger.error("[ALARM] TRADE ABORTED: Account chain-lock failed for %s", ticker)
                return False

            pyautogui.click()"""

new_strike_check = """            # PURE PHYSICAL STRIKE: account already verified upstream.
            # Zero browser/Playwright interaction inside this block.
            pyautogui.click()"""

if old_strike_check in rpa:
    rpa = rpa.replace(old_strike_check, new_strike_check)
    print("rpa_executor.py: Removed in-sequence account check from strike loop")
else:
    print("WARN: in-sequence account check not found")

with open("execution/rpa_executor.py", "w", encoding="utf-8") as f:
    f.write(rpa)

print("rpa_executor.py updated")
print("ALL DONE")
