import json
import time
import math
import random
import threading
from pynput import keyboard, mouse
import pyautogui

pyautogui.FAILSAFE = False

COORDINATE_LABELS = [
    "BUTTON_BUY",
    "BUTTON_SELL",
    "BUTTON_FLATTEN",
    "ACCOUNT_DROPDOWN",
    "SIM_ACCOUNT_SLOT",
    "APEX_ACCOUNT_SLOT"
]

captured_points = {}
current_label_index = 0
listener_active = True


def bezier_curve(p0, p1, p2, p3, t):
    u = 1 - t
    tt = t * t
    uu = u * u
    uuu = uu * u
    ttt = tt * t
    x = uuu * p0[0] + 3 * uu * t * p1[0] + 3 * u * tt * p2[0] + ttt * p3[0]
    y = uuu * p0[1] + 3 * uu * t * p1[1] + 3 * u * tt * p2[1] + ttt * p3[1]
    return (x, y)


def move_mouse_stealth(x, y):
    start_x, start_y = pyautogui.position()
    distance = math.sqrt((x - start_x) ** 2 + (y - start_y) ** 2)
    steps = max(int(distance / 2), 20)
    steps = min(steps, 100)

    mid_x = (start_x + x) / 2
    mid_y = (start_y + y) / 2
    offset = distance * 0.2
    angle = math.atan2(y - start_y, x - start_x)
    perp_angle = angle + math.pi / 2

    cp1_x = mid_x + math.cos(perp_angle) * offset * random.uniform(-1, 1)
    cp1_y = mid_y + math.sin(perp_angle) * offset * random.uniform(-1, 1)
    cp2_x = mid_x + math.cos(perp_angle) * offset * random.uniform(-1, 1)
    cp2_y = mid_y + math.sin(perp_angle) * offset * random.uniform(-1, 1)

    p0 = (start_x, start_y)
    p1 = (cp1_x, cp1_y)
    p2 = (cp2_x, cp2_y)
    p3 = (x, y)

    for i in range(steps + 1):
        t = i / steps
        px, py = bezier_curve(p0, p1, p2, p3, t)
        duration = random.uniform(0.001, 0.005)
        pyautogui.moveTo(px, py, duration=duration)

    pyautogui.moveTo(x, y)


def get_pixel_color(x, y):
    try:
        screenshot = pyautogui.screenshot(region=(x, y, 1, 1))
        r, g, b = screenshot.getpixel((0, 0))
        return "#{:02X}{:02X}{:02X}".format(r, g, b)
    except Exception:
        return "#000000"


def save_config():
    config = {}
    for label, data in captured_points.items():
        config[label] = {
            "x": data["x"],
            "y": data["y"],
            "color": data["color"]
        }
    with open("config_coordinates.json", "w") as f:
        json.dump(config, f, indent=4)
    print(f"\n[SAVED] config_coordinates.json updated.")


def on_press(key):
    global current_label_index, listener_active

    if not listener_active:
        return False

    try:
        if key.char.lower() == 'c':
            if current_label_index >= len(COORDINATE_LABELS):
                print("\n[DONE] All coordinates captured!")
                save_config()
                listener_active = False
                return False

            label = COORDINATE_LABELS[current_label_index]
            x, y = pyautogui.position()
            color = get_pixel_color(x, y)
            captured_points[label] = {"x": x, "y": y, "color": color}
            current_label_index += 1

            print(f"[CAPTURED] {label}: x={x}, y={y}, color={color}")

            if current_label_index < len(COORDINATE_LABELS):
                print(f"  -> Next: {COORDINATE_LABELS[current_label_index]}")
            else:
                print("  -> Press 'C' one more time to save and exit.")

        elif key.char.lower() == 'q':
            print("\n[EXIT] Calibration cancelled.")
            save_config()
            listener_active = False
            return False

    except AttributeError:
        pass


def main():
    print("=" * 60)
    print("  COORDINATE CALIBRATION SCRIPT - TRADING DESKTOP")
    print("=" * 60)
    print()
    print("Controls:")
    print("  [C] Capture current mouse position")
    print("  [Q] Quit and save progress")
    print()
    print("Capture sequence:")
    for i, label in enumerate(COORDINATE_LABELS):
        print(f"  {i + 1}. {label}")
    print()
    print("Press 'C' to begin capturing...")
    print("-" * 60)

    with keyboard.Listener(on_press=on_press) as listener:
        listener.join()

    print()
    print("Final configuration:")
    print(json.dumps(captured_points, indent=2))
    print()
    print("Ghost Hand test: Call move_mouse_stealth(x, y) to test stealth movement.")


if __name__ == "__main__":
    main()
