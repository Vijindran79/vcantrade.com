"""
Coordinate Finder - Run this on the DESKTOP
Helps you find the exact x,y coordinates of buttons on screen.

Usage:
    python get_coordinates.py
    python get_coordinates.py --delay 5  # Wait 5 seconds before capturing
"""
import time
import sys
import argparse

try:
    import pyautogui
    PYAUTOGUI_OK = True
except ImportError:
    PYAUTOGUI_OK = False
    print("[ERROR] pyautogui not installed!")
    print("Install with: pip install pyautogui")
    sys.exit(1)


def get_mouse_position(delay: float = 3.0):
    """Get mouse position after a delay (gives user time to position mouse)."""
    print(f"\n[INFO] Move your mouse to the target button in {delay} seconds...")
    print("[INFO] The coordinates will be captured when the timer expires.\n")

    for i in range(int(delay), 0, -1):
        print(f"   Capturing in {i}...", end="\r")
        time.sleep(1)

    print(" " * 50, end="\r")  # Clear line
    x, y = pyautogui.position()
    return x, y


def main():
    parser = argparse.ArgumentParser(description="Get screen coordinates for button clicks")
    parser.add_argument("--delay", type=float, default=3.0, help="Delay before capture (default: 3s)")
    args = parser.parse_args()

    print("=" * 60)
    print("COORDINATE FINDER - DESKTOP")
    print("=" * 60)
    print("\nThis tool helps you find the exact coordinates of buttons.")
    print("You will have time to position your mouse over each button.\n")

    # Get screen info
    width, height = pyautogui.size()
    print(f"[SCREEN] Resolution: {width}x{height}\n")

    # Get coordinates for each button
    buttons = ["BUY_SIM", "SELL_SIM", "FLATTEN_SIM", "BUY_REAL", "SELL_REAL", "FLATTEN_REAL"]

    results = {}

    for btn in buttons:
        input(f"[ACTION] Press ENTER when ready to capture '{btn}' button...")
        x, y = get_mouse_position(args.delay)
        results[btn.lower()] = {"x": x, "y": y}
        print(f"[RESULT] {btn} = (x: {x}, y: {y})\n")

    # Print JSON output
    print("=" * 60)
    print("COPY THIS TO config_coordinates.json:")
    print("=" * 60)
    import json
    output = {
        "screen_resolution": {"width": width, "height": height},
        "buttons": results
    }
    print(json.dumps(output, indent=4))
    print("=" * 60)

    # Save to file
    with open("config_coordinates.json", "w") as f:
        json.dump(output, f, indent=4)
    print("\n[SAVED] Coordinates saved to config_coordinates.json")


if __name__ == "__main__":
    main()
