import socket
import json
import pyautogui
import time
import random
import math
import threading
import os
import sys
import pygetwindow as gw
from datetime import datetime

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

pyautogui.FAILSAFE = False

HOST = "0.0.0.0"
PORT = 5555
CONFIG_FILE = "config_coordinates.json"

# R|Trader Pro window hints
RTRADER_WINDOW_HINTS = ["Rithmic Trader Pro", "Rithmic Trader", "R|Trader Pro", "R|Trader", "RTrader Pro", "RTrader"]
CLICK_JITTER = 2
COLOR_TOLERANCE = 30


def find_rtrader_window():
    """Find R|Trader Pro window across all monitors, handle minimized/hidden windows."""
    try:
        # Use module-level gw (imported at top of file)
        all_windows = gw.getAllWindows()
        found_windows = []
        
        # Print all window titles for debugging
        all_titles = []
        for w in all_windows:
            title = getattr(w, "title", "").strip()
            if title:
                all_titles.append(title)
                # Check if any hint matches
                for hint in RTRADER_WINDOW_HINTS:
                    if hint.lower() in title.lower():
                        found_windows.append((title, w))
        
        log(f"[WINDOW] All window titles: {all_titles[:20]}")  # Print first 20
        log(f"[WINDOW] Found {len(found_windows)} windows matching hints: {[w[0] for w in found_windows]}")
        
        if found_windows:
            # Use the first matching window
            title, window = found_windows[0]
            log(f"[WINDOW] Found R|Trader window: '{title}'")
            
            # Restore minimized window
            try:
                if getattr(window, "isMinimized", False):
                    log(f"[WINDOW] Restoring minimized window: '{title}'")
                    window.restore()
                    time.sleep(0.5)
                if getattr(window, "visible", True):
                    return window
            except Exception as e:
                log(f"[WINDOW] Error restoring window: {e}")
        
        # Fallback to getWindowsWithTitle
        log("[WINDOW] Trying fallback with getWindowsWithTitle...")
        for hint in RTRADER_WINDOW_HINTS:
            windows = gw.getWindowsWithTitle(hint)
            for w in windows:
                if getattr(w, "visible", True):
                    log(f"[WINDOW] Fallback found: '{getattr(w, 'title', '')}'")
                    return w
        
        log("[WINDOW] No R|Trader Pro window found")
        return None
    except Exception as e:
        log(f"[WINDOW] Error finding R|Trader window: {e}")
        return None


def bring_window_to_foreground(window):
    """Bring window to foreground, handle multi-monitor setups."""
    if not window:
        return False
    try:
        # Restore if minimized
        if getattr(window, "isMinimized", False):
            window.restore()
            time.sleep(0.3)
        # Activate window
        window.activate()
        time.sleep(0.5)
        # Verify it's active
        active = gw.getActiveWindow()
        if active and getattr(active, "title", "") == getattr(window, "title", ""):
            return True
        # Fallback: ALT-TAB to window
        return _alt_tab_to_window(window)
    except Exception as e:
        log(f"[WINDOW] Error bringing window to foreground: {e}")
        return False


def _alt_tab_to_window(target_window):
    """Use ALT-TAB to cycle to target window."""
    target_title = getattr(target_window, "title", "").lower()
    for _ in range(20):
        pyautogui.hotkey("alt", "tab")
        time.sleep(0.3)
        try:
            active = gw.getActiveWindow()
            if active and target_title in active.title.lower():
                return True
        except Exception:
            pass
    return False


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}] {msg}")


def load_coordinates():
    if not os.path.exists(CONFIG_FILE):
        log(f"[ERROR] {CONFIG_FILE} not found. Run coordinate_calibration.py first.")
        sys.exit(1)
    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)
    required = ["BUTTON_BUY", "BUTTON_SELL", "BUTTON_FLATTEN",
                "ACCOUNT_DROPDOWN", "SIM_ACCOUNT_SLOT", "APEX_ACCOUNT_SLOT"]
    for key in required:
        if key not in config:
            log(f"[ERROR] Missing coordinate: {key}")
            sys.exit(1)
    for key in required:
        log(f"[CONFIG] {key}: ({config[key]['x']}, {config[key]['y']}) color={config[key]['color']}")
    return config


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


def execute_strike(config, action):
    log(f"[STRIKE] Lion executing: {action}")

    # Step0: Bring R|Trader window to foreground
    log("[STEP 0] Bringing R|Trader window to foreground...")
    rtrader_window = find_rtrader_window()
    
    if not rtrader_window:
        log("[WINDOW HIDDEN] R|Trader Pro window not found - Window not found")
        log("[DEBUG] Make sure R|Trader Pro is running on this machine")
        log(f"[DEBUG] RTRADER_WINDOW_HINTS: {RTRADER_WINDOW_HINTS}")
        return False
    
    if not bring_window_to_foreground(rtrader_window):
        log("[WINDOW ERROR] Failed to bring R|Trader window to foreground")
        return False
    
    log(f"[WINDOW] Successfully activated R|Trader window: {rtrader_window.title}")

    if action != "FLATTEN":
        dd = config["ACCOUNT_DROPDOWN"]
        log(f"[ACCOUNT] Moving to dropdown ({dd['x']}, {dd['y']})")
        move_human(dd["x"], dd["y"])
        time.sleep(random.uniform(0.1, 0.25))
        human_click(dd["x"], dd["y"])
        time.sleep(random.uniform(0.2, 0.4))

        slot_key = "SIM_ACCOUNT_SLOT" if "SIM" in action else "APEX_ACCOUNT_SLOT"
        slot = config[slot_key]
        log(f"[ACCOUNT] Selecting {slot_key} ({slot['x']}, {slot['y']})")
        move_human(slot["x"], slot["y"])
        time.sleep(random.uniform(0.1, 0.25))
        human_click(slot["x"], slot["y"])
        time.sleep(random.uniform(0.3, 0.5))

    btn_key = "BUTTON_BUY" if "BUY" in action else "BUTTON_SELL" if "SELL" in action else "BUTTON_FLATTEN"
    btn = config[btn_key]
    log(f"[BUTTON] Moving to {btn_key} ({btn['x']}, {btn['y']})")
    move_human(btn["x"], btn["y"])
    time.sleep(random.uniform(0.05, 0.15))
    human_click(btn["x"], btn["y"])
    log(f"[STRIKE] {action} execution complete.")
    return True


def handle_client(config, conn, addr):
    log(f"[CONN] {addr} connected")
    try:
        data = conn.recv(4096).decode("utf-8")
        if data:
            cmd = json.loads(data)
            action = cmd.get("action", "UNKNOWN")
            success = execute_strike(config, action)
            if success:
                response = json.dumps({"status": "SUCCESS", "action": action})
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
    log("  GHOST-HAND SERVER (Master Stealth) - TRADING DESKTOP")
    log("=" * 60)
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
