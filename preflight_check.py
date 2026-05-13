"""Pre-flight check for Lion trading system."""
import socket
import json
import time
import sys
import os

sys.path.insert(0, r"C:\Users\vijin\vcantrade.com-1")

print("=" * 60)
print("  LION PRE-FLIGHT CHECK")
print("=" * 60)

# Check 1: Config file exists
print("\n[CHECK 1] Config file...")
config_path = r"C:\Users\vijin\vcantrade.com-1\config_coordinates.json"
if os.path.exists(config_path):
    print("  [PASS] config_coordinates.json exists")
    with open(config_path) as f:
        config = json.load(f)
    required = ["NINJA_WEB_BUY", "NINJA_WEB_SELL", "NINJA_WEB_CLOSE",
                "RITHMIC_BUY", "RITHMIC_SELL", "RITHMIC_FLATTEN"]
    all_good = True
    for key in required:
        if key in config:
            print(f"  [PASS] {key}: ({config[key]['x']}, {config[key]['y']})")
        else:
            print(f"  [FAIL] Missing: {key}")
            all_good = False
    if all_good:
        print("  [PASS] All coordinates present")
    else:
        print("  [FAIL] Run: python coordinate_calibration.py")
else:
    print("  [FAIL] config_coordinates.json NOT FOUND")
    print("  Action: Run python coordinate_calibration.py")

# Check 2: pygetwindow available
print("\n[CHECK 2] Window focus (pygetwindow)...")
try:
    import pygetwindow
    print("  [PASS] pygetwindow available")
    print("  (Will auto-focus NinjaTrader Web browser before SIM clicks)")
except ImportError:
    print("  [WARN] pygetwindow not installed")
    print("  Action: pip install pygetwindow")
    print("  (Or manually focus browser before trades)")

# Check 3: Socket server (5555)
print("\n[CHECK 3] Socket server (port 5555)...")
try:
    # Quick connect test to localhost
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    # Don't actually connect - just verify port not in use by other app
    s.bind(("127.0.0.1", 5555))
    s.close()
    print("  [PASS] Port 5555 available")
except OSError:
    print("  [WARN] Port 5555 may be in use")
    print("  (This is OK if execution_server.py is already running)")

# Check 4: HTTP server (5556)
print("\n[CHECK 4] HTTP server (port 5556)...")
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    s.bind(("127.0.0.1", 5556))
    s.close()
    print("  [PASS] Port 5556 available")
except OSError:
    print("  [WARN] Port 5556 may be in use")

# Check 5: File syntax
print("\n[CHECK 5] Script syntax...")
try:
    import ast
    ast.parse(open(r"C:\Users\vijin\vcantrade.com-1\execution_server.py").read())
    print("  [PASS] execution_server.py syntax OK")
    ast.parse(open(r"C:\Users\vijin\vcantrade.com-1\coordinate_calibration.py").read())
    print("  [PASS] coordinate_calibration.py syntax OK")
except SyntaxError as e:
    print(f"  [FAIL] Syntax error: {e}")

print("\n" + "=" * 60)
print("  PRE-FLIGHT SUMMARY")
print("=" * 60)
print("\nIf all checks PASS, you're ready!")
print("\nQUICK START CHECKLIST:")
print("  1. Run: python coordinate_calibration.py")
print("     -> Capture NinjaTrader Web: Buy Mkt, Sell Mkt, Close")
print("     -> Capture Rithmic: Buy, Sell, Flatten")
print("  2. Run: python execution_server.py")
print("  3. Laptop: Connect Dashboard to 192.168.0.39:5556")
print("\nGOOD LUCK AT MARKET OPEN!  Lion is ready.  ")
print("=" * 60)
