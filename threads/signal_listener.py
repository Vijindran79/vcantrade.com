"""
VcanTrade AI — Signal Listener QThread
Background thread that listens for incoming signals from the Cloud Scanner.
Runs an HTTP server on the local laptop to receive trade signals.
"""

import asyncio
import logging

from PyQt6.QtCore import QThread, pyqtSignal

import config
from services.signal_dispatcher import SignalDispatcher

logger = logging.getLogger(__name__)


class SignalListenerThread(QThread):
    """
    Background thread that listens for incoming signals from Cloud Scanner.
    Runs HTTP server on local laptop to receive trade signals.
    """

    signal_received = pyqtSignal(object)     # Emits received signal data
    handshake_received = pyqtSignal(object)  # Emits handshake metadata
    listener_error = pyqtSignal(str)         # Emits error message

    def __init__(self):
        super().__init__()
        self.running = True
        self.dispatcher = SignalDispatcher()

    def run(self):
        logger.info(
            "Signal Listener started on %s:%s",
            config.LOCAL_LISTENER_HOST,
            config.LOCAL_LISTENER_PORT,
        )
        try:
            self.dispatcher.set_signal_callback(self._on_signal_received)
            self.dispatcher.set_handshake_callback(self._on_handshake_received)
            asyncio.run(self._run_server())
        except Exception as e:
            self.listener_error.emit(f"Signal Listener error: {e}")
            logger.error("Signal Listener thread error: %s", e)

    async def _run_server(self):
        """Run the HTTP server loop."""
        try:
            runner = await self.dispatcher.start_server()
            logger.info(
                "[OK] Signal Dispatcher listening on %s:%s",
                config.LOCAL_LISTENER_HOST,
                config.LOCAL_LISTENER_PORT,
            )
            if config.PUBLIC_SIGNAL_URL:
                logger.info("[GLOBE] Public signal URL armed: %s", config.PUBLIC_SIGNAL_URL)
            logger.info(
                "[LOCK] Signal listener auth: %s",
                "ENABLED" if config.SIGNAL_API_KEY else "DISABLED",
            )

            # Verify server is accessible
            from aiohttp import ClientSession
            try:
                health_url = (
                    f"http://{config.LOCAL_LISTENER_HEALTH_HOST}"
                    f":{config.LOCAL_LISTENER_PORT}/api/health"
                )
                async with ClientSession() as session:
                    async with session.get(health_url, timeout=3) as response:
                        if response.status == 200:
                            logger.info("[OK] Signal Dispatcher health check passed")
                        else:
                            logger.warning(
                                "[WARN] Signal Dispatcher health check returned %s",
                                response.status,
                            )
            except Exception as e:
                logger.error("[WARN] Signal Dispatcher health check failed: %s", e)

            try:
                while self.running:
                    await asyncio.sleep(1)
            finally:
                await runner.cleanup()
                logger.info("Signal Dispatcher server stopped")
        except Exception as e:
            error_msg = f"Signal Dispatcher failed to start: {e}"
            self.listener_error.emit(error_msg)
            logger.error(error_msg)
            raise

    def _on_signal_received(self, signal_data: dict):
        """Handle incoming signal from cloud."""
        self.signal_received.emit(signal_data)
        logger.info("Signal received from cloud: %s", signal_data)

    def _on_handshake_received(self, handshake_data: dict):
        """Handle authenticated bridge handshake."""
        self.handshake_received.emit(handshake_data)
        logger.info("Handshake received from external brain: %s", handshake_data)

    def stop(self):
        self.running = False
