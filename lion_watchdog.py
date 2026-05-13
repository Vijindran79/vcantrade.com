"""
Lion Bot Watchdog - Self-Healing Wrapper for main.py
Monitors main.py and restarts on crash.

The legacy Rithmic/R|Trader window health check is retired because current
execution runs through MetaTrader 5 and TradingView/Chrome sockets.
"""

import subprocess
import time
import sys
import os
import signal
import logging
from datetime import datetime

# Configuration
MAIN_SCRIPT = os.path.join(os.path.dirname(__file__), "main.py")
RESTART_DELAY = 5  # Seconds to wait before restarting
MAX_RESTARTS_PER_HOUR = 10  # Prevent restart loops
HEALTH_CHECK_INTERVAL = 60  # Seconds between health checks

# Logging
logging.basicConfig(
    filename="lion_watchdog.log",
    level=logging.INFO,
    format="%(asctime)s [WATCHDOG] %(message)s"
)
logger = logging.getLogger(__name__)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logger.addHandler(console)

class Watchdog:
    def __init__(self):
        self.main_process = None
        self.restart_count = 0
        self.last_restart_hour = datetime.now().hour
        self.running = True

        # Handle shutdown signals
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    def signal_handler(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False
        if self.main_process and self.main_process.poll() is None:
            self.main_process.terminate()
            try:
                self.main_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.main_process.kill()
        sys.exit(0)

    def check_rithmic_connection(self) -> bool:
        """Legacy compatibility stub; Rithmic window checks are retired."""
        return True

    def start_main(self):
        """Start main.py subprocess"""
        try:
            logger.info(f"Starting {MAIN_SCRIPT}...")
            self.main_process = subprocess.Popen(
                [sys.executable, MAIN_SCRIPT],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            logger.info(f"main.py started with PID {self.main_process.pid}")
            return True
        except Exception as e:
            logger.error(f"Failed to start main.py: {e}")
            return False

    def monitor(self):
        """Main monitoring loop"""
        logger.info("=== Lion Bot Watchdog Started ===")
        while self.running:
            # Start main.py if not running
            if not self.main_process or self.main_process.poll() is not None:
                # Check restart rate limit
                current_hour = datetime.now().hour
                if current_hour != self.last_restart_hour:
                    self.restart_count = 0
                    self.last_restart_hour = current_hour

                if self.restart_count >= MAX_RESTARTS_PER_HOUR:
                    logger.error(f"Exceeded {MAX_RESTARTS_PER_HOUR} restarts per hour. Sleeping 1 hour.")
                    time.sleep(3600)
                    continue

                if not self.start_main():
                    time.sleep(RESTART_DELAY)
                    continue

                self.restart_count += 1
                time.sleep(RESTART_DELAY)  # Wait for startup

            # Legacy Rithmic/R|Trader window health checks are retired. The
            # watchdog now restarts main.py only when the process exits.

            # Log main.py output
            if self.main_process.stdout:
                output = self.main_process.stdout.readline()
                if output:
                    print(f"[MAIN] {output.strip()}")

            # Check if main.py has crashed
            if self.main_process.poll() is not None:
                exit_code = self.main_process.returncode
                logger.warning(f"main.py exited with code {exit_code}. Restarting...")
                self.restart_main()
                continue

            time.sleep(1)  # Short sleep to prevent CPU spin

    def restart_main(self):
        """Restart the main.py process"""
        if self.main_process and self.main_process.poll() is None:
            self.main_process.terminate()
            try:
                self.main_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.main_process.kill()
        self.main_process = None
        time.sleep(RESTART_DELAY)


if __name__ == "__main__":
    watchdog = Watchdog()
    try:
        watchdog.monitor()
    except KeyboardInterrupt:
        watchdog.signal_handler(signal.SIGINT, None)
