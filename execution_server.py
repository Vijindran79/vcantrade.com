import socket
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
RESOLVED_COORDS = {}

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

pyautogui.FAILSAFE = False

HOST = "0.0.0.0"
PORT = 5555
CONFIG_FILE = "config_coordinates.json"

CLICK_JITTER = 2
COLOR_TOLERANCE = 30
CLICK_LOCK = threading.RLock()


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}] {msg}")


def calibrate_trading_interface():
    """AUTO-CALIBRATION: Scan the active monitor for trading button templates.
    Uses OpenCV template matching (via pyautogui + cv2) to resolve exact (x, y)
    centers of Buy / Sell / Flatten buttons on the user's active layout.
    Stores results in RESOLVED_COORDS for the lifetime of the server.
    """
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


def load_coordinates():
    """Load optional TradingView coordinate overrides, return empty dict if file missing."""
    merged = dict(RESOLVED_COORDS)  # Start with auto-calibrated coordinates

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
    return merged


def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def color_distance(c1, c2):
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(c1, c2)))


def verify_color_at(x, y, expected_hex, tolerance=COLOR_TOLERANCE):
    try:
        screenshot = pyautogui.screenshot(region=(x - 2, y - 2, 5, 5))
        r, g, b = screenshot.getpixel((2, 2))
        actual = (r, g, b)
        expected = hex_to_rgb(expected_hex)
        dist = color_distance(actual, expected)
        match = dist < tolerance
        if not match:
            log(f"[COLOR] MISMATCH at ({x},{y}): expected {expected_hex}, got ({r},{g},{b}), dist={dist:.1f}")
        return match
    except Exception as e:
        log(f"[COLOR] Check failed: {e}")
        return True


def dist(x1, y1, x2, y2):
    if HAS_NUMPY:
        return float(np.hypot(x2 - x1, y2 - y1))
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def cubic_bezier(t, p0, p1, p2, p3):
    u = 1 - t
    return (u**3 * p0 + 3 * u**2 * t * p1 + 3 * u * t**2 * p2 + t**3 * p3)


def move_human(target_x, target_y):
    start_x, start_y = pyautogui.position()
    d = dist(start_x, start_y, target_x, target_y)
    if d < 5:
        return
    p1_x = start_x + random.uniform(-d * 0.3, d * 0.3)
    p1_y = start_y + random.uniform(-d * 0.3, d * 0.3)
    p2_x = target_x + random.uniform(-d * 0.3, d * 0.3)
    p2_y = target_y + random.uniform(-d * 0.3, d * 0.3)
    steps = random.randint(20, 40)
    duration = random.uniform(0.15, 0.4)
    for i in range(steps + 1):
        t = i / steps
        x = cubic_bezier(t, start_x, p1_x, p2_x, target_x)
        y = cubic_bezier(t, start_y, p1_y, p2_y, target_y)
        pyautogui.moveTo(x, y)
        time.sleep(duration / steps * random.uniform(0.5, 1.5))
    pyautogui.moveTo(target_x, target_y)


def human_click(btn_x, btn_y):
    jx = btn_x + random.randint(-CLICK_JITTER, CLICK_JITTER)
    jy = btn_y + random.randint(-CLICK_JITTER, CLICK_JITTER)
    pyautogui.click(clicks=1, interval=random.uniform(0.05, 0.15), button='left', x=jx, y=jy)
    return jx, jy


def _coords_from_packet(cmd):
    """Extract explicit TradingView/RPA coordinates sent by main.py."""
    target = cmd.get("target") or {}
    absolute = target.get("absolute") or target.get("coords") or target.get("coordinates")
    if isinstance(absolute, dict):
        absolute = (absolute.get("x"), absolute.get("y"))
    if isinstance(absolute, (list, tuple)) and len(absolute) >= 2:
        try:
            x = int(float(absolute[0]))
            y = int(float(absolute[1]))
            if x > 0 and y > 0:
                return x, y, str(target.get("point_name", "packet_target"))
        except (TypeError, ValueError):
            return None
    return None


def execute_packet_target(cmd):
    """Physically click packet-provided coordinates on the active screen frame."""
    resolved = _coords_from_packet(cmd)
    if not resolved:
        return None
    x, y, point_name = resolved
    packet_id = cmd.get("packet_id", "unknown")
    selectors = cmd.get("selectors") or {}
    log(
        f"[PACKET] packet_id={packet_id} action={cmd.get('action')} ticker={cmd.get('ticker')} "
        f"target={point_name} abs=({x},{y}) selectors={selectors}"
    )
    with CLICK_LOCK:
        move_human(x, y)
        time.sleep(random.uniform(0.05, 0.15))
        clicked_x, clicked_y = human_click(x, y)
    log(f"[CLICK_CONFIRM] packet_id={packet_id} physically clicked active frame at ({clicked_x},{clicked_y})")
    return {
        "point_name": point_name,
        "requested": [x, y],
        "clicked": [clicked_x, clicked_y],
        "selectors": selectors,
    }


def execute_strike(config, action, cmd=None):
    log(f"[STRIKE] Lion executing: {action}")
    cmd = cmd or {}

    packet_click = execute_packet_target(cmd)
    if packet_click:
        log(f"[STRIKE] {action} execution complete via packet target.")
        return True, packet_click

    btn_key = "BUTTON_BUY" if "BUY" in action else "BUTTON_SELL" if "SELL" in action else "BUTTON_FLATTEN"
    if btn_key not in config:
        log(
            f"[REJECTED] No TradingView packet target or {btn_key} coordinate override supplied. "
            "Legacy broker fallback has been removed."
        )
        return False, None

    btn = config[btn_key]
    log(f"[BUTTON] Moving to TradingView override {btn_key} ({btn['x']}, {btn['y']})")
    with CLICK_LOCK:
        move_human(btn["x"], btn["y"])
        time.sleep(random.uniform(0.05, 0.15))
        clicked_x, clicked_y = human_click(btn["x"], btn["y"])
    log(f"[CLICK_CONFIRM] action={action} physically clicked configured {btn_key} at ({clicked_x},{clicked_y})")
    log(f"[STRIKE] {action} execution complete.")
    return True, {"point_name": btn_key, "requested": [btn["x"], btn["y"]], "clicked": [clicked_x, clicked_y]}


def handle_client(config, conn, addr):
    log(f"[CONN] {addr} connected")
    try:
        data = conn.recv(4096).decode("utf-8")
        if data:
            cmd = json.loads(data)
            if str(cmd.get("type", "")).upper() == "HANDSHAKE":
                response = json.dumps({
                    "status": "ACK",
                    "type": "HANDSHAKE_ACK",
                    "port": PORT,
                    "server": "execution_server.py",
                    "received_at": datetime.now().isoformat(),
                    "packet_id": cmd.get("packet_id"),
                })
                log(
                    f"[HANDSHAKE] ACK sent to {addr} "
                    f"packet_id={cmd.get('packet_id', 'unknown')} source={cmd.get('source', 'unknown')}"
                )
                conn.send(response.encode("utf-8"))
                return

            action = cmd.get("action", "UNKNOWN")
            log(
                f"[PACKET_RECEIVED] packet_id={cmd.get('packet_id', 'unknown')} "
                f"source={cmd.get('source', 'unknown')} action={action} ticker={cmd.get('ticker', '')}"
            )
            success, click_receipt = execute_strike(config, action, cmd)
            if success:
                response = json.dumps({
                    "status": "SUCCESS",
                    "action": action,
                    "packet_id": cmd.get("packet_id"),
                    "click_receipt": click_receipt,
                })
                log(f"[RESPONSE] Sent: {response}")
            else:
                response = json.dumps({"status": "ERROR", "message": "Execution failed - check window status"})
                log(f"[ERROR] Execution failed for {action}")
            conn.send(response.encode("utf-8"))
    except json.JSONDecodeError:
        log(f"[ERROR] Invalid JSON from {addr}")
        conn.send(json.dumps({"status": "ERROR", "message": "Invalid JSON"}).encode("utf-8"))
    except Exception as e:
        log(f"[ERROR] {e}")
        conn.send(json.dumps({"status": "ERROR", "message": str(e)}).encode("utf-8"))
    finally:
        conn.close()
        log(f"[CONN] {addr} disconnected")


def main():
    log("=" * 60)
    log("  GHOST-HAND SERVER - INSTITUTIONAL EXECUTION ENGINE")
    log("=" * 60)

    # STAGE 1: Auto-calibrate button coordinates from screen templates
    calibrate_trading_interface()

    config = load_coordinates()
    log(f"[SERVER] Listening on {HOST}:{PORT}")
    log(f"[SERVER] Click jitter: ±{CLICK_JITTER}px | Color check: {'ON' if False else 'OFF'}")
    log(f"[SERVER] numpy: {'available' if HAS_NUMPY else 'not available'}")
    log("-" * 60)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen(5)
        while True:
            conn, addr = server.accept()
            t = threading.Thread(target=handle_client, args=(config, conn, addr), daemon=True)
            t.start()


if __name__ == "__main__":
    main()
