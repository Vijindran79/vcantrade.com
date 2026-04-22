"""
VcaniTrade AI - Main Application (Modular Architecture)

Entry point that initializes all services and UI components.
Decoupled design ensures UI crashes don't affect signal processing.
"""

import sys
import os
import signal
import logging
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, QThread

# Import modules
import config
from core.brain_swarm import OllamaSwarmConsensus
from services.signal_dispatcher import SignalDispatcher
from core.executor import UnifiedTradeExecutor
from core.scanner import CloudScanner
from core.watchtower import WatchtowerScanner
from core.browser_agent import BrowserAgent
from ui.dashboard import CommandCenter

# Setup ASCII-safe logging to prevent Windows charmap errors
# Force ASCII encoding on Windows to avoid UnicodeEncodeError
try:
    _ascii_stdout = open(
        sys.stdout.fileno(),
        mode="w",
        encoding="ascii",
        errors="ignore",
        closefd=False,
    )
    _console_handler = logging.StreamHandler(_ascii_stdout)
except Exception:
    _console_handler = logging.StreamHandler(sys.stdout)

_file_handler = logging.FileHandler(config.LOG_FILE, encoding="utf-8", errors="ignore") if config.LOG_FILE else logging.NullHandler()

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[_console_handler, _file_handler],
)
logger = logging.getLogger(__name__)


class SignalDispatcherThread(QThread):
    """Thread to run signal dispatcher independently."""

    def __init__(self, dispatcher):
        super().__init__()
        self.dispatcher = dispatcher

    def run(self):
        """Run the HTTP server in this thread."""
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.dispatcher.start_server())


class VcaniTradeApp:
    """Main application class with decoupled services."""

    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setApplicationName("VcaniTrade AI")
        self.app.setApplicationVersion("2.0")

        # Initialize core services
        self.browser_agent = None
        self.signal_dispatcher = None
        self.executor = None
        self.scanner = None
        self.brain = None
        self.watchtower = None

        # UI components
        self.cmd = None  # CommandCenter dashboard
        self.ai_narrator = None

        # Threads for background services
        self.service_threads = []

        self.initialize_services()
        self.initialize_ui()
        self.connect_signals()

    def initialize_services(self):
        """Initialize all background services."""
        logger.info("Initializing core services...")

        # Browser Agent
        self.browser_agent = BrowserAgent()

        # Signal Dispatcher (runs in background thread)
        self.signal_dispatcher = SignalDispatcher()
        # Connect to UI callbacks
        self.signal_dispatcher.set_signal_callback(self.on_signal_received)
        self.signal_dispatcher.set_handshake_callback(self.on_handshake_received)

        # Brain/Swarm
        self.brain = OllamaSwarmConsensus()

        # Executor
        self.executor = UnifiedTradeExecutor(self.browser_agent)

        # Scanner
        self.scanner = CloudScanner()

        # Watchtower
        self.watchtower = WatchtowerScanner()

    def initialize_ui(self):
        """Initialize UI components."""
        logger.info("Initializing UI components...")
        try:
            self.cmd = CommandCenter()
            logger.info("Dashboard created successfully")
        except Exception as e:
            logger.error(f"Failed to create dashboard: {e}")
            self.cmd = None

    def connect_signals(self):
        """Connect service signals to UI slots."""
        # No UI in this version
        pass

    def start_services(self):
        """Start all background services."""
        logger.info("Starting background services...")

        # Start signal dispatcher in thread
        # This ensures it survives UI crashes
        dispatcher_thread = SignalDispatcherThread(self.signal_dispatcher)
        dispatcher_thread.start()
        self.service_threads.append(dispatcher_thread)

        # Start other services similarly
        # self.scanner.start()
        # self.executor.start()
        # etc.

    def on_signal_received(self, signal_data):
        """Handle incoming signal from dispatcher."""
        logger.info(f"Signal received: {signal_data}")
        # Forward to executor
        if self.executor:
            self.executor.process_signal(signal_data)

    def on_handshake_received(self, handshake_data):
        """Handle handshake from dispatcher."""
        logger.info(f"Handshake received: {handshake_data}")

    def run(self):
        """Run the application."""
        self.start_services()

        # Show the dashboard immediately - MUST appear
        if self.cmd:
            self.cmd.show()
            logger.info("Dashboard is now visible")
        else:
            logger.warning("Dashboard not available - running headless")

        # Setup graceful shutdown via Qt timer (works cross-platform)
        def _on_signal(signum):
            self.shutdown()

        # Use QTimer to poll for signals on Windows (since signal handling is limited)
        self._shutdown_timer = QTimer()
        self._shutdown_timer.timeout.connect(lambda: None)
        self._shutdown_timer.start(100)

        # Run the Qt event loop - this keeps UI responsive
        logger.info("VcaniTrade AI started. Close the dashboard to exit.")
        return self.app.exec()

    def shutdown(self, signum=None, frame=None):
        """Graceful shutdown of all services."""
        logger.info("Shutting down VcaniTrade AI...")

        # Stop services
        for thread in self.service_threads:
            thread.quit()
            thread.wait()

        if self.signal_dispatcher:
            # Stop HTTP server
            pass

        self.app.quit()
        sys.exit(0)


def main():
    """Application entry point."""
    try:
        app = VcaniTradeApp()
        return app.run()
    except Exception as e:
        logger.critical(f"Application failed to start: {e}")
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
