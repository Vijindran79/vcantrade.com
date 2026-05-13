"""
Vision Helper - Find Rithmic buttons using template matching.
Run this on the Desktop where Rithmic is visible.

Usage:
    python vision_finder.py
    python vision_finder.py --save-targets  # Capture button images first
"""
import os
import sys
import time
import argparse
from pathlib import Path

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    print("[ERROR] OpenCV not installed. Run: pip install opencv-python")
    sys.exit(1)

try:
    import pyautogui
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False
    print("[ERROR] pyautogui not installed. Run: pip install pyautogui")
    sys.exit(1)


TARGET_DIR = Path("button_targets")
TARGET_DIR.mkdir(exist_ok=True)


def capture_button(name: str, duration: float = 5.0):
    """Capture a button image by having user hover over it."""
    print(f"\n[CAPTURE] Preparing to capture '{name}' button...")
    print(f"[CAPTURE] Hover your mouse over the {name} button in {duration} seconds...")
    
    for i in range(int(duration), 0, -1):
        print(f"   Capturing in {i}...", end="\r")
        time.sleep(1)
    
    print(" " * 50, end="\r")
    x, y = pyautogui.position()
    
    # Capture region around mouse (80x40 box)
    region = (x - 40, y - 20, 80, 40)
    screenshot = pyautogui.screenshot(region=region)
    filepath = TARGET_DIR / f"{name}.png"
    screenshot.save(filepath)
    
    print(f"[SUCCESS] Captured '{name}' at ({x}, {y}) -> {filepath}")
    return str(filepath), (x, y)


def find_button(template_path: str, threshold: float = 0.8):
    """Find button in current screen using template matching."""
    if not os.path.exists(template_path):
        print(f"[ERROR] Template not found: {template_path}")
        return None
    
    # Take screenshot of entire screen
    screenshot = pyautogui.screenshot()
    screen_np = np.array(screenshot)
    screen_bgr = cv2.cvtColor(screen_np, cv2.COLOR_RGB2BGR)
    
    # Load template
    template = cv2.imread(template_path)
    if template is None:
        print(f"[ERROR] Cannot load template: {template_path}")
        return None
    
    # Template matching
    result = cv2.matchTemplate(screen_bgr, template, cv2.TM_CCOEFF_NORMED)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
    
    if max_val >= threshold:
        h, w = template.shape[:2]
        center_x = max_loc[0] + w // 2
        center_y = max_loc[1] + h // 2
        print(f"[FOUND] Button at ({center_x}, {center_y}) with confidence {max_val:.2f}")
        return (center_x, center_y)
    else:
        print(f"[NOT FOUND] Confidence too low: {max_val:.2f} < {threshold}")
        return None


def save_coordinates(coords: dict):
    """Save found coordinates to config_coordinates.json."""
    import json
    
    config = {
        "screen_resolution": {
            "width": pyautogui.size()[0],
            "height": pyautogui.size()[1]
        },
        "RITHMIC_BUY": {"x": coords["buy"][0], "y": coords["buy"][1], "color": "#00FF41"},
        "RITHMIC_SELL": {"x": coords["sell"][0], "y": coords["sell"][1], "color": "#FF003C"},
        "RITHMIC_FLATTEN": {"x": coords["flatten"][0], "y": coords["flatten"][1], "color": "#FFD700"},
    }
    
    with open("config_coordinates.json", "w") as f:
        json.dump(config, f, indent=4)
    
    print(f"\n[SAVED] Coordinates saved to config_coordinates.json")


def main():
    parser = argparse.ArgumentParser(description="Vision-based button finder for Rithmic")
    parser.add_argument("--save-targets", action="store_true", help="Capture button images")
    parser.add_argument("--find-all", action="store_true", help="Find all buttons and save coordinates")
    args = parser.parse_args()
    
    print("=" * 60)
    print("VISION FINDER - Rithmic Button Detection")
    print("=" * 60)
    
    if args.save_targets:
        print("\n[STEP 1] Capturing button target images...")
        print("Make sure Rithmic is VISIBLE and logged in.\n")
        
        _, buy_coords = capture_button("buy")
        _, sell_coords = capture_button("sell")
        _, flatten_coords = capture_button("flatten")
        
        print(f"\n[SUCCESS] All target images saved to {TARGET_DIR}/")
        print("[NEXT] Run: python vision_finder.py --find-all")
    
    elif args.find_all:
        print("\n[STEP 2] Finding buttons on screen...")
        
        buttons = ["buy", "sell", "flatten"]
        coords = {}
        
        for btn in buttons:
            template = TARGET_DIR / f"{btn}.png"
            if not template.exists():
                print(f"[ERROR] {btn}.png not found. Run with --save-targets first.")
                sys.exit(1)
            
            print(f"\nSearching for {btn} button...")
            result = find_button(str(template))
            if result:
                coords[btn] = result
            else:
                print(f"[WARN] Could not find {btn} button")
                # Use fallback from pyautogui screenshot
                coords[btn] = (pyautogui.size()[0] // 2, pyautogui.size()[1] // 2)
        
        save_coordinates(coords)
    
    else:
        print("\nUsage:")
        print("  1. python vision_finder.py --save-targets  # Capture button images")
        print("  2. python vision_finder.py --find-all       # Find buttons and save coords")
        print("\nOr just use the simple method:")
        print("  python get_coordinates.py --delay 5")


if __name__ == "__main__":
    main()
