"""
Chrome Launcher for LionBot - Auto-starts Chrome with remote debugging.
Run this on the Desktop before starting the trading bot.

Usage:
    python launch_chrome.py
    python launch_chrome.py --port 9222
    python launch_chrome.py --no-wait  # Don't wait for user input
"""
import os
import sys
import time
import socket
import subprocess
import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("ChromeLauncher")


def is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """Check if a port is open."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def find_chrome_path() -> str:
    """Find Chrome executable path."""
    possible_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Users\{}\AppData\Local\Google\Chrome\Application\chrome.exe".format(os.getenv("USERNAME", "")),
    ]

    for path in possible_paths:
        if os.path.exists(path):
            return path

    # Try to find via where command
    try:
        result = subprocess.run(["where", "chrome"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip().split("\n")[0]
    except Exception:
        pass

    return ""


def launch_chrome(debug_port: int = 9222, wait: bool = True):
    """Launch Chrome with remote debugging enabled."""
    logger.info("=" * 60)
    logger.info("LION BOT - Chrome Launcher (Debug Mode)")
    logger.info("=" * 60)

    # Check if already running
    if is_port_open("127.0.0.1", debug_port):
        logger.info(f"[OK] Chrome already running with debug port {debug_port}")
        logger.info("[OK] CDP connection should work now!")
        return True

    # Find Chrome
    chrome_path = find_chrome_path()
    if not chrome_path:
        logger.error("[FAIL] Chrome not found! Please install Google Chrome.")
        return False

    logger.info(f"[INFO] Found Chrome: {chrome_path}")

    # Create debug directory
    debug_dir = os.path.join(os.path.expanduser("~"), "ChromeDebug_LionBot")
    os.makedirs(debug_dir, exist_ok=True)
    logger.info(f"[INFO] Debug directory: {debug_dir}")

    # Build command
    cmd = [
        chrome_path,
        f"--remote-debugging-port={debug_port}",
        f"--user-data-dir={debug_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-popup-blocking",
        "https://app.wealthcharts.com"
    ]

    logger.info(f"[LAUNCH] Starting Chrome with debug port {debug_port}...")
    logger.info(f"[LAUNCH] Command: {' '.join(cmd[:3])}...")

    try:
        # Launch Chrome (non-blocking)
        subprocess.Popen(cmd, shell=False)
        logger.info("[LAUNCH] Chrome process started")
    except Exception as e:
        logger.error(f"[FAIL] Failed to launch Chrome: {e}")
        return False

    # Wait for Chrome to start
    if wait:
        logger.info("[WAIT] Waiting for Chrome to become available...")
        for i in range(30):  # Wait up to 30 seconds
            time.sleep(1)
            if is_port_open("127.0.0.1", debug_port):
                logger.info(f"[SUCCESS] Chrome is ready on port {debug_port}!")
                logger.info("[SUCCESS] The 'blindfold' is removed - bot can now 'see'!")
                return True
            print(f"\r[WAIT] Waiting... ({i+1}s)", end="", flush=True)

        logger.error(f"[TIMEOUT] Chrome did not start within 30 seconds")
        return False

    return True


def main():
    parser = argparse.ArgumentParser(description="Launch Chrome with remote debugging for LionBot")
    parser.add_argument("--port", type=int, default=9222, help="Debug port (default: 9222)")
    parser.add_argument("--no-wait", action="store_true", help="Don't wait for Chrome to start")
    args = parser.parse_args()

    success = launch_chrome(debug_port=args.port, wait=not args.no_wait)

    if success:
        logger.info("=" * 60)
        logger.info("[READY] Next steps:")
        logger.info("  1. Log into WealthCharts in the Chrome window")
        logger.info("  2. Run: python execution_server.py")
        logger.info("  3. Let the Lion trade!")
        logger.info("=" * 60)
    else:
        logger.error("[FAIL] Could not launch Chrome")
        sys.exit(1)

    # Keep window open if running directly
    if not args.no_wait and sys.stdin.isatty():
        input("\nPress ENTER to exit...")


if __name__ == "__main__":
    main()
