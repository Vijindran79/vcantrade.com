"""
VcaniTrade AI - Main Application
Trading assistant with transparent overlay HUD and teacher/auto modes
"""

import sys
import logging
import time
import random
import signal
from datetime import datetime
from threading import Thread

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, QThread, pyqtSignal

import config
from core.models import (
    MarketDataPoint,
    OverlaySignal,
    SignalAction,
    ConfidenceLevel,
    DebateTranscript,
)
from core.llm_analyzer import LLMAnalyzer
from core.trade_engine import TradeEngine
from core.grader import Grader
from ui.dashboard import TradingOverlay, ControlWindow

# Setup logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(config.LOG_FILE), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


class MarketScanner(QThread):
    """
    Background thread that scans market data
    Emits MarketDataPoint for each asset
    """

    data_ready = pyqtSignal(MarketDataPoint)

    def __init__(self):
        super().__init__()
        self.running = True

    def run(self):
        """Simulate market data feed (replace with real WebSocket later)"""
        logger.info("Market scanner started")

        # Mock prices for demo
        base_prices = {
            "EURUSD": 1.08750,
            "GBPUSD": 1.26500,
            "USDJPY": 151.250,
            "BTCUSD": 68500.00,
            "ETHUSD": 3450.00,
        }

        while self.running:
            for asset in config.ASSETS:
                # Simulate price movement
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
                time.sleep(config.SCAN_INTERVAL / len(config.ASSETS))

    def stop(self):
        self.running = False


class AnalysisWorker(QThread):
    """
    Analyzes market data using Swarm Consensus multi-agent debate
    Runs in separate thread to keep UI responsive
    """

    analysis_complete = pyqtSignal(
        object, object
    )  # Emits (LLMAnalysisOutput, DebateTranscript)

    def __init__(self):
        super().__init__()
        self.analyzer = LLMAnalyzer()
        self.market_data_queue = []

    def add_to_queue(self, market_data: MarketDataPoint):
        """Add market data to analysis queue"""
        self.market_data_queue.append(market_data)

    def run(self):
        """Process queue continuously"""
        logger.info("Analysis worker started (Swarm Consensus mode)")

        while True:
            if self.market_data_queue:
                market_data = self.market_data_queue.pop(0)

                # Run swarm debate
                output, transcript = self.analyzer.analyze_market(market_data)
                self.analysis_complete.emit(output, transcript)
            else:
                time.sleep(0.1)


class VcaniTradeApp:
    """
    Main application controller
    Connects all modules together
    """

    def __init__(self):
        self.app = QApplication(sys.argv)

        # Initialize components
        self.control_window = ControlWindow()
        self.overlay = TradingOverlay()
        self.trade_engine = TradeEngine()
        self.grader = Grader()
        self.market_scanner = MarketScanner()
        self.analysis_worker = AnalysisWorker()

        # Current state
        self.current_mode = "TEACHER"
        self.latest_signals = {}

        self._connect_signals()
        logger.info("VcaniTrade AI initialized")

    def _connect_signals(self):
        """Connect all Qt signals"""
        # Control window
        self.control_window.mode_changed.connect(self._on_mode_changed)
        self.control_window.kill_switch_triggered.connect(self._on_kill_switch)

        # Market scanner -> Analysis worker
        self.market_scanner.data_ready.connect(self._on_market_data)

        # Analysis worker -> Trade engine & Overlay
        self.analysis_worker.analysis_complete.connect(self._on_analysis_complete)

    def _on_market_data(self, market_data: MarketDataPoint):
        """Handle new market data"""
        self.analysis_worker.add_to_queue(market_data)

    def _on_analysis_complete(self, analysis, transcript: DebateTranscript = None):
        """Handle Swarm Consensus analysis result"""
        # Log CEO verdict
        self.control_window.add_log(
            f"{analysis.action.value} {analysis.asset} - {analysis.confidence.value}"
        )

        # Log debate transcript if available
        if transcript:
            self.control_window.add_log(
                f"  [Sniper] {transcript.technical_sniper.brief[:80]}"
            )
            self.control_window.add_log(
                f"  [Macro]  {transcript.macro_analyst.brief[:80]}"
            )
            self.control_window.add_log(
                f"  [Risk]   {transcript.risk_manager.brief[:80]}"
            )
            self.control_window.add_log(f"  [CEO]    {transcript.ceo_verdict[:100]}")

        # Process through trade engine
        trade = self.trade_engine.process_signal(analysis, self.current_mode)

        # Update overlay
        if trade:
            overlay_signal = OverlaySignal(
                asset=trade.asset,
                action=trade.action,
                confidence=trade.confidence,
                entry_price=trade.entry_price if trade.entry_price > 0 else None,
                stop_loss=trade.stop_loss,
                take_profit=trade.take_profit,
                reason=trade.ai_reason,
            )
            self.overlay.update_signal_handler(overlay_signal)
            self.latest_signals[trade.asset] = overlay_signal

            # Pass transcript to overlay for display
            if transcript:
                self.overlay.update_debate_transcript(transcript)

    def _on_mode_changed(self, mode: str):
        """Handle mode change from control window"""
        self.current_mode = mode
        logger.info(f"Mode changed to {mode}")

    def _on_kill_switch(self):
        """Handle kill switch activation"""
        self.trade_engine.activate_kill_switch()
        self.market_scanner.stop()
        logger.critical("Kill switch activated - all systems halted")

    def run(self):
        """Start the application"""
        logger.info("Starting VcaniTrade AI...")

        # Show windows
        self.control_window.show()
        self.overlay.show()

        # Start background threads
        self.market_scanner.start()
        self.analysis_worker.start()

        # Start UI update timer
        self.ui_timer = QTimer()
        self.ui_timer.timeout.connect(self._update_ui_periodically)
        self.ui_timer.start(config.OVERLAY_UPDATE_MS)

        self.control_window.add_log("✅ VcaniTrade AI started - Paper mode active")
        self.control_window.add_log("📊 Scanning markets...")
        self.control_window.add_log("🎯 Switch to AUTO mode when ready")

        logger.info("Application running")

        # Run Qt event loop
        sys.exit(self.app.exec())

    def _update_ui_periodically(self):
        """Periodic UI updates"""
        # Update grader report if available
        if len(self.trade_engine.trade_history) > 0:
            report = self.grader.generate_report_card(days=1)
            if report.get("overall_grade"):
                self.control_window.add_log(
                    f"📊 Performance Grade: {report['overall_grade']} "
                    f"(Win Rate: {report.get('win_rate', 'N/A')})"
                )

    def cleanup(self):
        """Clean shutdown"""
        self.market_scanner.stop()
        self.trade_engine.cleanup()
        logger.info("Application shutdown complete")


def main():
    """Entry point"""
    print("=" * 60)
    print("🎯 VcaniTrade AI - Trading Assistant")
    print("=" * 60)
    print("Starting application...")
    print(f"Mode: TEACHER (safe)")
    print(f"Trading: PAPER (dry run)")
    print(f"Kill Switch: OFF")
    print("=" * 60)
    print()

    app = VcaniTradeApp()

    # Register signal handlers for graceful shutdown
    def signal_handler(sig, frame):
        print("\n\nReceived shutdown signal...")
        app.cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        app.run()
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        app.cleanup()
    except Exception as e:
        logger.error(f"Application error: {e}")
        app.cleanup()
        raise


if __name__ == "__main__":
    main()
