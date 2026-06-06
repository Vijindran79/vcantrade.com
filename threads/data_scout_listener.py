"""
VcanTrade AI — Data Scout Listener QThread
Background thread that scouts for external data feeds and market intelligence.
"""

import logging
import time

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


class DataScoutListenerThread(QThread):
    """
    Background thread that scouts for external data feeds.
    Listens for market intelligence and data updates from external sources.
    """

    signal_received = pyqtSignal(object)     # Emits received data
    scout_error = pyqtSignal(str)          # Emits error message

    def __init__(self):
        super().__init__()
        self.running = True
        self.poll_interval = 5.0  # seconds between scout checks

    def run(self):
        """Main thread loop for data scouting."""
        logger.info("[DATA-SCOUT] Data Scout Listener thread started")
        try:
            while self.running:
                try:
                    # Scout loop placeholder - extend with actual data feed logic
                    time.sleep(self.poll_interval)
                except Exception as e:
                    logger.error("[DATA-SCOUT] Scout loop error: %s", e)
                    self.scout_error.emit(f"Data scout error: {e}")
                    time.sleep(self.poll_interval)
        except Exception as e:
            self.scout_error.emit(f"Data Scout Listener error: {e}")
            logger.error("[DATA-SCOUT] Thread error: %s", e)

    def stop(self):
        """Stop the scout listener thread."""
        self.running = False
        logger.info("[DATA-SCOUT] Data Scout Listener thread stopping")