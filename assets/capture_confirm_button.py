"""
Capture Reference Image for TradingView Confirm Button
======================================================
Run this once while TradingView is open in paper trading mode:

    python assets/capture_confirm_button.py

It will:
1. Wait 3 seconds (time for you to open TradingView's order dialog).
2. Prompt you to draw a region around the "Confirm" button with your mouse.
3. Save the cropped screenshot to assets/tv_confirm_button.png.

The RPAExecutor will then use that image to visually locate the button
during every trade execution, instead of relying on hardcoded coordinates.
"""

import os
import sys
import time

try:
    import pyautogui
    from PIL import ImageGrab
except ImportError:
    print("Install requirements first: pip install pyautogui pillow")
    sys.exit(1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_PATH = os.path.join(BASE_DIR, "tv_confirm_button.png")
os.makedirs(BASE_DIR, exist_ok=True)

print("=== TradingView Confirm Button Capture ===")
print("1. Open TradingView in your browser.")
print("2. Open a paper trade order dialog so the Confirm button is visible.")
print("3. Come back here and press ENTER when ready.")
input("\nPress ENTER to start capture (you have 3 seconds) ...")
print("Capturing in 3 seconds [DASH] move your mouse to the TOP-LEFT of the Confirm button ...")
time.sleep(3)

x1, y1 = pyautogui.position()
print(f"Top-left captured: ({x1}, {y1})")
print("Now move your mouse to the BOTTOM-RIGHT of the Confirm button and press ENTER ...")
input()
x2, y2 = pyautogui.position()
print(f"Bottom-right captured: ({x2}, {y2})")

# Ensure correct ordering
left, top = min(x1, x2), min(y1, y2)
right, bottom = max(x1, x2), max(y1, y2)

if right - left < 5 or bottom - top < 5:
    print("ERROR: Region too small. Please re-run and select a larger area.")
    sys.exit(1)

# Add a small padding
PAD = 4
img = ImageGrab.grab(bbox=(left - PAD, top - PAD, right + PAD, bottom + PAD))
img.save(SAVE_PATH)
print(f"\n[OK] Saved: {SAVE_PATH}  ({img.width}x{img.height} px)")
print("The RPAExecutor will now use this image to visually find the Confirm button.")
