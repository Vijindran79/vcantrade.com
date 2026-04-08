"""
VcanTrade AI - Main Application

Trading assistant with Swarm Consensus multi-agent analysis,
Vision Engine chart reading, Watchtower scanning, and RPA execution.

Architecture:
- All heavy work runs in QThreads (never blocks the GUI)
- Backend threads emit signals → CommandCenter updates on main thread
- Vision Engine captures screenshots in AnalysisWorker thread
- Watchtower runs independently, feeds anomalies to Swarm
"""

import signal
import sys
import time
import random
import logging
from datetime import datetime

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, QThread, pyqtSignal

import config
from core.models import (
    MarketDataPoint,
    OverlaySignal,
    SignalAction,
    ConfidenceLevel,
    DebateTranscript,
    WatchlistAlert,
)
from core.llm_analyzer import LLMAnalyzer
from core.trade_engine import TradeEngine
from core.grader import Grader
from core.watchtower import WatchtowerScanner
from core.vision_engine import VisionCapture
from ui.dashboard import (
    CommandCenter,
    TradingOverlay,
    CalibrationWizardDialog,
    VisionTestDialog,
)

# Setup logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(config.LOG_FILE), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


class MarketScanner(QThread):
    """
    Background thread that generates mock market data.
    Replaced by real WebSocket feeds in Phase 2.
    
    Only scans assets that are selected in the Prop Firm Selection Board.
    """

    data_ready = pyqtSignal(MarketDataPoint)

    def __init__(self):
        super().__init__()
        self.running = True
        self.selected_assets = config.SELECTED_ASSETS.copy()

    def set_selected_assets(self, assets: list):
        """Update the list of assets to scan."""
        self.selected_assets = assets.copy()
        logger.info(f"Market scanner updated to monitor: {self.selected_assets}")

    def run(self):
        logger.info(f"Market scanner started with assets: {self.selected_assets}")
        base_prices = {
            "EURUSD": 1.08750,
            "GBPUSD": 1.26500,
            "USDJPY": 151.250,
            "BTCUSD": 68500.00,
            "ETHUSD": 3450.00,
            "XAUUSD": 2035.00,
            "XAGUSD": 23.50,
            "WTI": 78.50,
            "BRENT": 82.30,
            "SPX500": 5200.00,
            "NAS100": 18000.00,
            "US30": 39000.00,
            "GER40": 17500.00,
            "UK100": 7800.00,
            "AUDUSD": 0.65500,
            "USDCAD": 1.35500,
            "NZDUSD": 0.61000,
            "USDCHF": 0.88500,
            "SOLUSD": 145.00,
            "NATGAS": 2.15,
        }

        while self.running:
            # Only iterate over SELECTED assets, not all assets
            for asset in self.selected_assets:
                if not self.running:
                    break
                    
                base = base_prices.get(asset, 100.0)
                change = random.uniform(-2.0, 2.0)
                price = base * (1 + change / 100)
                volume = random.uniform(10000, 100000)

                market_data = MarketDataPoint(
                    asset=asset,
                    price=price,
                    volume=volume,
                    price_change_1h=change,
                    price_change_24h=change * random.uniform(2, 5),
                    indicators={
                        "RSI": random.uniform(30, 70),
                        "MACD": random.uniform(-0.5, 0.5),
                    },
                )

                self.data_ready.emit(market_data)
                
                # Rate limit: sleep 2 seconds between each asset scan to avoid 429 errors
                time.sleep(2)

    def stop(self):
        self.running = False


class AnalysisWorker(QThread):
    """
    Analyzes market data using Swarm Consensus multi-agent debate.
    Runs in separate thread to keep GUI responsive.

    Dual-Vision: When config.USE_VISION is True, captures a chart
    screenshot and passes it to the Technical Sniper for visual analysis.
    """

    analysis_complete = pyqtSignal(object, object)

    def __init__(self):
        super().__init__()
        self.analyzer = LLMAnalyzer()
        self.market_data_queue = []
        self.vision = (
            VisionCapture(
                chart_region=(
                    config.CHART_REGION_X,
                    config.CHART_REGION_Y,
                    config.CHART_REGION_W,
                    config.CHART_REGION_H,
                ),
                save_debug=config.SAVE_DEBUG_SCREENSHOTS,
            )
            if config.USE_VISION
            else None
        )

    def add_to_queue(self, market_data: MarketDataPoint):
        self.market_data_queue.append(market_data)

    def run(self):
        logger.info("Analysis worker started (Swarm Consensus mode)")

        while True:
            if self.market_data_queue:
                market_data = self.market_data_queue.pop(0)

                # Capture chart screenshot if vision is enabled
                chart_base64 = None
                if self.vision:
                    screenshot = self.vision.capture_chart(asset=market_data.asset)
                    if screenshot:
                        chart_base64 = screenshot.to_base64()
                        logger.info(
                            f"Chart screenshot captured for {market_data.asset}"
                        )
                    else:
                        logger.warning(
                            f"Screenshot failed for {market_data.asset} — text-only"
                        )

                # Run swarm debate (with or without vision)
                output, transcript = self.analyzer.analyze_market(
                    market_data, chart_image_base64=chart_base64
                )
                self.analysis_complete.emit(output, transcript)
            else:
                time.sleep(0.1)


class VcaniTradeApp:
    """
    Main application controller.
    Connects all modules via Qt signals — zero blocking on the GUI thread.
    """

    def __init__(self):
        self.app = QApplication(sys.argv)

        # UI — Command Center (control) + TradingOverlay (HUD above charts)
        self.cmd = CommandCenter()
        self.overlay = TradingOverlay()

        # Core
        self.trade_engine = TradeEngine()
        self.grader = Grader()

        # Threads
        self.market_scanner = MarketScanner()
        self.watchtower = WatchtowerScanner()
        self.analysis_worker = AnalysisWorker()

        # State
        self.current_mode = "TEACHER"
        self.latest_signals = {}

        self._connect_signals()
        logger.info("VcaniTrade AI initialized")

    def _connect_signals(self):
        """Wire all backend threads to the CommandCenter UI."""
        # Command Center
        self.cmd.mode_changed.connect(self._on_mode_changed)
        self.cmd.kill_switch_triggered.connect(self._on_kill_switch)
        self.cmd.calibration_requested.connect(self._on_calibrate)
        self.cmd.vision_test_requested.connect(self._on_test_vision)
        self.cmd.calibration_reset_requested.connect(self._on_reset_calibration)
        self.cmd.eod_report_requested.connect(self._on_eod_report)
        
        # Prop Firm Selection Board → Market Scanner
        self.cmd.selection_changed.connect(self._on_selection_changed)

        # Market scanner → Analysis worker
        self.market_scanner.data_ready.connect(self._on_market_data)

        # Watchtower → UI alerts + Swarm handoff
        self.watchtower.alert_detected.connect(self._on_watchtower_alert)
        self.watchtower.market_data_ready.connect(self._on_market_data)

        # Analysis worker → Trade engine + UI
        self.analysis_worker.analysis_complete.connect(self._on_analysis_complete)

        # Update calibration status on startup
        self._refresh_calibration_status()

    def _on_selection_changed(self, selected_assets: list):
        """Update market scanner with new asset selection from UI."""
        self.market_scanner.set_selected_assets(selected_assets)
        self.cmd.log(
            f'<span style="color:#00D4FF">SELECTION BOARD</span>: '
            f"Now monitoring {len(selected_assets)} assets: {', '.join(selected_assets)}"
        )

    def _on_market_data(self, market_data: MarketDataPoint):
        """Queue market data for Swarm analysis."""
        self.analysis_worker.add_to_queue(market_data)

    def _on_watchtower_alert(self, alert: WatchlistAlert):
        """Handle Watchtower anomaly alert."""
        self.cmd.log(
            f'<span style="color:#F85149;font-weight:bold">WATCHTOWER</span>: '
            f"[{alert.severity}] {alert.alert_type} on {alert.asset} — {alert.reason}"
        )

    def _on_analysis_complete(self, analysis, transcript: DebateTranscript = None):
        """Handle Swarm Consensus result — all UI updates on main thread."""
        # Build overlay signal
        overlay_signal = OverlaySignal(
            asset=analysis.asset,
            action=analysis.action,
            confidence=analysis.confidence,
            entry_price=analysis.entry_price,
            stop_loss=analysis.stop_loss,
            take_profit=analysis.take_profit,
            reason=analysis.reason,
        )

        # Update both UI surfaces
        self.overlay.update_signal_handler(overlay_signal)
        self.cmd.display_signal(overlay_signal)

        # Log debate to terminal
        if transcript:
            self.cmd.log(
                f'<span style="color:#8B949E">Sniper: [{transcript.technical_sniper.action}] '
                f"{transcript.technical_sniper.conviction}</span>"
            )
            self.cmd.log(
                f'<span style="color:#8B949E">Macro:  [{transcript.macro_analyst.action}] '
                f"{transcript.macro_analyst.conviction}</span>"
            )
            self.cmd.log(
                f'<span style="color:#8B949E">Risk:   [{transcript.risk_manager.verdict}] '
                f"{transcript.risk_manager.conviction}</span>"
            )

            # Display CEO verdict prominently
            self.cmd.display_ceo_verdict(transcript)
            self.overlay.update_debate_transcript(transcript)
        else:
            self.cmd.log(
                f"{analysis.action.value} {analysis.asset} — {analysis.confidence.value}"
            )

        # Process through trade engine
        trade = self.trade_engine.process_signal(analysis, self.current_mode)

        # Run post-trade autopsy if trade was closed
        if trade and trade.status == "CLOSED":
            autopsy = self.grader.autopsy_trade(trade)
            self.cmd.log(
                f'<span style="color:#D29922">AUTOPSY</span>: '
                f"{trade.asset} Grade: {autopsy.grade} — {autopsy.explanation[:100]}"
            )

        self.latest_signals[analysis.asset] = analysis

    def _on_mode_changed(self, mode: str):
        self.current_mode = mode
        logger.info(f"Mode changed to {mode}")

    def _on_kill_switch(self):
        self.trade_engine.activate_kill_switch()
        self.market_scanner.stop()
        self.watchtower.stop()
        logger.critical("Kill switch activated — all systems halted")

    def _on_calibrate(self):
        """Open the RPA Coordinate Mapper wizard."""
        dialog = CalibrationWizardDialog(self.cmd)
        dialog.calibration_complete.connect(self._refresh_calibration_status)
        dialog.exec_()

    def _on_test_vision(self):
        """Capture a screenshot and display it for sanity check."""
        if not self.analysis_worker.vision:
            self.cmd.log("Vision Engine not available — cannot test")
            return

        screenshot = self.analysis_worker.vision.capture_chart(asset="TEST")
        if screenshot:
            self.cmd.log(
                f"Vision test: captured {screenshot.dimensions[0]}x{screenshot.dimensions[1]} "
                f"({screenshot.file_size_estimate_kb:.0f}KB)"
            )
            preview = VisionTestDialog(screenshot._resize_for_vlm(), self.cmd)
            preview.show()
        else:
            self.cmd.log("Vision test failed — screenshot capture error")

    def _on_reset_calibration(self):
        """Reset all RPA calibration data."""
        from core.calibration import CalibrationManager

        cal = CalibrationManager()
        cal.reset()
        self._refresh_calibration_status()
        self.cmd.log("Calibration reset — all coordinates cleared")

    def _on_eod_report(self):
        """Generate and display End-of-Day report."""
        from core.analytics_reporter import AnalyticsReporter

        reporter = AnalyticsReporter()
        report_text = reporter.generate_eod_report()
        filepath = reporter.save_report()

        # Display in terminal
        self.cmd.log("━" * 40)
        for line in report_text.split("\n"):
            self.cmd.log(line)
        self.cmd.log("━" * 40)
        self.cmd.log(f"Report saved: {filepath}")

        # Also generate HTML
        html_path = reporter.save_html_report()
        self.cmd.log(f"HTML report: {html_path}")

    def _refresh_calibration_status(self):
        """Update the calibration status label in the UI."""
        from core.calibration import CalibrationManager

        cal = CalibrationManager()
        status = cal.get_calibration_status()
        done = sum(1 for v in status.values() if v)
        total = len(status)
        self.cmd.update_calibration_status(cal.is_calibrated(), done, total)

    def run(self):
        """Start the application."""
        logger.info("Starting VcaniTrade AI...")

        # Show both UI surfaces
        self.cmd.show()
        self.overlay.show()

        # Start background threads
        self.market_scanner.start()
        self.watchtower.start()
        self.analysis_worker.start()

        # Status updates
        self.cmd.set_watchtower_status(True, "Scanning")
        if config.USE_VISION:
            self.cmd.set_vision_status(True, f"{config.VLM_MODEL}")
        else:
            self.cmd.set_vision_status(False, "Disabled")
        self.cmd.set_rpa_status(False, "Disarmed")

        # Startup messages
        self.cmd.log("VcaniTrade AI started — Paper mode active")
        self.cmd.log("Market scanner running")
        self.cmd.log("Watchtower monitoring watchlist")
        if config.USE_VISION:
            self.cmd.log(f"Vision Engine: {config.VLM_MODEL}")
        self.cmd.log("Mode: TEACHER — RPA disarmed")

        logger.info("Application running")
        sys.exit(self.app.exec())

    def cleanup(self):
        self.market_scanner.stop()
        self.watchtower.stop()
        self.trade_engine.cleanup()
        logger.info("Application shutdown complete")


def main():
    """Entry point."""
    print("=" * 60)
    print("VcaniTrade AI — Trading Assistant")
    print("=" * 60)
    print(f"Mode:      TEACHER (safe)")
    print(f"Trading:   PAPER (dry run)")
    print(
        f"Vision:    {config.VLM_MODEL}" if config.USE_VISION else "Vision:    Disabled"
    )
    print(f"Kill:      OFF")
    print("=" * 60)

    app = VcaniTradeApp()

    def signal_handler(sig, frame):
        print("\nShutdown signal received...")
        app.cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        app.run()
    except KeyboardInterrupt:
        print("\nShutting down...")
        app.cleanup()
    except Exception as e:
        logger.error(f"Application error: {e}")
        app.cleanup()
        raise


if __name__ == "__main__":
    main()
