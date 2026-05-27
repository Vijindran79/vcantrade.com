"""
VcanTrade AI — Cloud Scanner QThread
Background thread that runs the Cloud Scanner on the Vast.ai server,
monitors tickers using yfinance, triggers Swarm Debate, and emits
trade signals back to the main thread.
"""

import asyncio
import logging
import time

from PyQt6.QtCore import QThread, pyqtSignal

import config
from core.scanner import Scanner as CloudScanner

logger = logging.getLogger(__name__)


class CloudScannerThread(QThread):
    """
    PREDATOR-CLASS: Background thread that runs the Cloud Scanner.
    Monitors tickers and triggers Swarm Debate.

    WATCHDOG HEARTBEAT: If scanner stops for >heartbeat_timeout seconds,
    force-reinitialize the connection.
    """

    signal_detected = pyqtSignal(object)          # Emits trade signal data
    technical_signal_detected = pyqtSignal(object)  # Raw scanner opportunities
    scanner_error = pyqtSignal(str)               # Emits error message
    ticker_status = pyqtSignal(str, str)           # Emits per-ticker status updates
    heartbeat_pulse = pyqtSignal(bool)             # True when heartbeat is healthy

    def __init__(self):
        super().__init__()
        self.running = True
        self.scanner = CloudScanner()
        self.scanner.status_callback = self._emit_ticker_status

        # WATCHDOG HEARTBEAT TRACKING
        self.last_scan_time = time.time()
        self.heartbeat_timeout = max(
            float(getattr(config, "SCAN_INTERVAL", 10)) * 6.0, 60.0
        )
        self.consecutive_failures = 0
        self.max_failures_before_reinit = 3

    def _emit_ticker_status(self, ticker: str, status: str):
        self.ticker_status.emit(ticker, status)

    def _technical_signal_payload(self, signal) -> dict:
        """Convert a scanner TechnicalSignal into a UI-friendly opportunity payload."""
        signal_type = str(
            getattr(signal, "signal_type", "SIGNAL") or "SIGNAL"
        ).upper()
        strength = float(getattr(signal, "strength", 0.0) or 0.0)
        metadata = getattr(signal, "metadata", {}) or {}
        if "BUY" in signal_type or "BULLISH" in signal_type:
            action = "BUY"
        elif "SELL" in signal_type or "BEARISH" in signal_type:
            action = "SELL"
        elif "OVERSOLD" in signal_type:
            action = "BUY"
        elif "OVERBOUGHT" in signal_type:
            action = "SELL"
        else:
            action = str(
                metadata.get("action_hint")
                or metadata.get("liquidity_bias")
                or metadata.get("direction")
                or metadata.get("action")
                or "SIGNAL"
            ).upper()
            if action == "UP":
                action = "BUY"
            elif action == "DOWN":
                action = "SELL"
        return {
            "ticker": str(getattr(signal, "ticker", "UNKNOWN") or "UNKNOWN"),
            "action": action,
            "signal_type": signal_type,
            "confidence": max(0.0, min(1.0, strength)),
            "metadata": metadata,
        }

    def run(self):
        logger.info("=" * 60)
        logger.info("[CLOUD] CLOUD SCANNER THREAD STARTED")
        logger.info("   Monitoring %s tickers", len(self.scanner.tickers))
        logger.info("   Target: %s/api/signal", config.CLOUD_SCANNER_URL)
        logger.info("=" * 60)

        try:
            asyncio.run(self._run_scanner())
        except Exception as e:
            error_msg = f"Cloud Scanner error: {e}"
            self.scanner_error.emit(error_msg)
            logger.error(error_msg)

    async def _run_scanner(self):
        """
        Run the cloud scanner loop with WATCHDOG HEARTBEAT monitoring.
        """
        while self.running:
            try:
                # WATCHDOG: Check if we've exceeded heartbeat timeout
                elapsed = time.time() - self.last_scan_time
                if elapsed > self.heartbeat_timeout and self.last_scan_time > 0:
                    logger.warning(
                        "[DOG] WATCHDOG: Scanner idle for %.1fs "
                        "(%.0fs threshold). Forcing reinitialization...",
                        elapsed,
                        self.heartbeat_timeout,
                    )
                    self.consecutive_failures += 1

                    if self.consecutive_failures >= self.max_failures_before_reinit:
                        logger.critical(
                            "[DOG] WATCHDOG: %d consecutive failures. "
                            "Reinitializing scanner connection...",
                            self.consecutive_failures,
                        )
                        try:
                            self.scanner = CloudScanner()
                            self.scanner.status_callback = self._emit_ticker_status
                            self.consecutive_failures = 0
                            logger.info("[DOG] WATCHDOG: Scanner reinitialized successfully")
                        except Exception as reinit_err:
                            logger.error("[DOG] WATCHDOG: Reinitialization failed: %s", reinit_err)

                    self.heartbeat_pulse.emit(False)

                # Scan all tickers
                signals = await self.scanner.scan_all_tickers()

                for signal in signals or []:
                    self.technical_signal_detected.emit(
                        self._technical_signal_payload(signal)
                    )

                # Update heartbeat timestamp on successful market sweep
                self.last_scan_time = time.time()
                self.consecutive_failures = 0
                self.heartbeat_pulse.emit(True)

                # Process through Swarm
                if signals:
                    trade_signal = await self.scanner.process_signals(signals)
                    self.last_scan_time = time.time()

                    if trade_signal:
                        success = await self.scanner.dispatch_to_local(trade_signal)
                        self.last_scan_time = time.time()

                        if success:
                            self.signal_detected.emit(trade_signal)
                            logger.info("Signal dispatched: %s", trade_signal)
                        else:
                            streak = int(
                                getattr(self.scanner, "dispatch_failure_streak", 0) or 0
                            )
                            if streak >= 3 and not bool(
                                getattr(self.scanner, "dispatch_alert_emitted", False)
                            ):
                                dispatch_error = str(
                                    getattr(
                                        self.scanner,
                                        "last_dispatch_error_message",
                                        "",
                                    )
                                    or "Signal dispatch failed"
                                )
                                self.scanner_error.emit(
                                    f"Signal dispatch failed after {streak} "
                                    f"consecutive attempts: {dispatch_error}"
                                )
                                self.scanner.dispatch_alert_emitted = True

                # Mark the loop healthy before sleeping between scans.
                self.last_scan_time = time.time()

                await asyncio.sleep(config.SCAN_INTERVAL)

            except asyncio.CancelledError:
                logger.info("Cloud Scanner task cancelled")
                break
            except Exception as e:
                error_msg = f"Scan error: {type(e).__name__}: {e}"
                self.scanner_error.emit(error_msg)
                logger.error("[CLOUD] SCANNER ERROR: %s", error_msg)
                self.consecutive_failures += 1
                await asyncio.sleep(5)

    def stop(self):
        self.running = False
        try:
            self.scanner.close()
        except Exception:
            pass
