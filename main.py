"""
VcanTrade AI - Main Application (Hybrid Architecture)

Hybrid Data-Driven System:
- Cloud Scanner (Vast.ai): Monitors 10 tickers using yfinance, triggers Swarm Debate
- Signal Dispatch: High-confidence signals (>0.70) sent to Local Executor
- Local Executor (Laptop): Receives signals, performs Vision Confirmation, executes via RPA

Architecture:
- All heavy work runs in QThreads (never blocks the GUI)
- Backend threads emit signals → CommandCenter updates on main thread
- Signal Dispatcher runs as async HTTP server in background thread
- Vision Engine captures screenshots in AnalysisWorker thread
- Watchtower runs independently, feeds anomalies to Swarm
"""

# Fix Windows PowerShell emoji encoding issues
import sys
import os
import io

if sys.platform == 'win32':
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import signal
import socket
import time
import random
import asyncio
import logging
import threading
import re
from datetime import datetime, timezone
from typing import Optional

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, QThread, pyqtSignal, QObject

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
# STAGE 2: AI Strategist & Dynamic Architect
from core.code_architect import CodeArchitect
from core.atr_stops import LooseATRStops
from core.visual_confirmation import VisualChartConfirmation
# STAGE 3: Institutional Governor & Risk Architect
from core.risk_governor import RiskGovernor
from core.sentiment_pulse import SentimentPulse
from core.profit_lock import ProfitLock
# STAGE 4: Meta-Cognition & Alpha Hunter
from core.meta_analyzer import MetaAnalyzer, TradeJournal
from core.watchtower import WatchtowerScanner
from core.vision_engine import VisionCapture
from core.scanner import CloudScanner
from core.signal_dispatcher import SignalDispatcher
from core.browser_agent import BrowserAgent
from core.settings import settings_manager
from core.financial_safety import FinancialSafetyManager
from core.executor import UnifiedTradeExecutor, ExchangeLimitExecutor, ExchangeInterface
from core.risk import calculate_position_size, build_hard_stop_plan
from core.journal import TradeJournalDB
from core.risk_manager import RiskManager, PositionSizer
from core.trade_executor import TradeExecutor
from core.market_sessions import MarketSessionDetector
from ui.dashboard import CommandCenter
from ui.signal_dialog import SignalApprovalDialog
from ui.ai_narrator import AINarratorOverlay

# Setup logging with UTF-8 encoding to prevent emoji crashes
# Clear any existing handlers and set up fresh
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding='utf-8', mode='a'),
        logging.StreamHandler(sys.stdout)
    ],
    force=True  # Force reconfiguration
)
logger = logging.getLogger(__name__)
logger.info("Logging system initialized")


_instance_lock_socket = None


def _acquire_single_instance_lock() -> bool:
    """Prevent multiple dashboard instances from running simultaneously."""
    global _instance_lock_socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 47654))
        sock.listen(1)
        _instance_lock_socket = sock
        return True
    except OSError:
        return False


def _release_single_instance_lock() -> None:
    """Release single-instance lock socket."""
    global _instance_lock_socket
    if _instance_lock_socket:
        try:
            _instance_lock_socket.close()
        except Exception:
            pass
        _instance_lock_socket = None

# Ensure immediate flushing
for handler in logging.root.handlers:
    handler.flush = lambda: handler.stream.flush() if hasattr(handler, 'stream') else None


from core.signal_dispatcher import SignalDispatcher
import pyautogui

# ---------------------------------------------------------------------------
# Data Scout Listener — Listens for Vast.ai signals on port 5000
# ---------------------------------------------------------------------------

class DataScoutListenerThread(QThread):
    """
    Listens for POST requests on port 5000 from Vast.ai server.
    Triggers immediate TradingView symbol flip via keyboard.
    """
    signal_received = pyqtSignal(str)  # Emits the symbol

    def __init__(self):
        super().__init__()
        self.running = True
        self.loop = None  # Store event loop reference

    def run(self):
        logger.info("Data Scout Listener started on port 5000")
        try:
            # Create new event loop for this thread
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._run_server())
        except Exception as e:
            logger.error(f"Data Scout Listener error: {e}")
        finally:
            if self.loop and not self.loop.is_closed():
                self.loop.close()

    async def _run_server(self):
        from aiohttp import web

        async def handle_signal(request):
            try:
                data = await request.json()
                # Expecting: {"symbol": "TICKER", "action": "BUY/SELL", "confidence": 0.85}
                if data.get("symbol"):
                    symbol = data.get("symbol")
                    action = data.get("action", "SIGNAL")
                    confidence = data.get("confidence", 0.0)

                    print(f"🔥 CLOUD SIGNAL RECEIVED FOR {symbol}")
                    self.signal_received.emit(symbol)
                    return web.Response(text="Signal processed", status=200)
                return web.Response(text="Invalid data", status=400)
            except Exception as e:
                return web.Response(text=str(e), status=500)

        app = web.Application()
        app.router.add_post("/signal", handle_signal)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", 5000)
        await site.start()

        logger.info("Data Scout Listener HTTP server running")

        while self.running:
            await asyncio.sleep(1)

        logger.info("Data Scout Listener shutting down...")
        await runner.cleanup()

    def stop(self):
        self.running = False
        if self.loop and not self.loop.is_closed():
            # Cancel all tasks gracefully
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()


class CloudScannerThread(QThread):
    """
    Background thread that runs the Cloud Scanner on Vast.ai server.
    Monitors 10 tickers using yfinance and triggers Swarm Debate.
    """

    signal_detected = pyqtSignal(object)  # Emits trade signal data
    scanner_error = pyqtSignal(str)  # Emits error message

    def __init__(self):
        super().__init__()
        self.running = True
        self.scanner = CloudScanner()

    def run(self):
        logger.info("=" * 60)
        logger.info("☁️ CLOUD SCANNER THREAD STARTED")
        logger.info(f"   Monitoring {len(self.scanner.tickers)} tickers")
        logger.info(f"   Target: {config.CLOUD_SCANNER_URL}/api/signal")
        logger.info("=" * 60)
        
        try:
            # Run async scanner in event loop
            asyncio.run(self._run_scanner())
        except Exception as e:
            error_msg = f"Cloud Scanner error: {e}"
            self.scanner_error.emit(error_msg)
            logger.error(error_msg)

    async def _run_scanner(self):
        """Run the cloud scanner loop."""
        while self.running:
            try:
                # Scan all tickers
                signals = await self.scanner.scan_all_tickers()

                # Process through Swarm
                if signals:
                    trade_signal = await self.scanner.process_signals(signals)

                    if trade_signal:
                        # Dispatch to local executor
                        success = await self.scanner.dispatch_to_local(trade_signal)

                        if success:
                            self.signal_detected.emit(trade_signal)
                            logger.info(f"Signal dispatched: {trade_signal}")
                        else:
                            self.scanner_error.emit("Signal dispatch failed")

                # Wait before next scan
                await asyncio.sleep(config.SCAN_INTERVAL)

            except asyncio.CancelledError:
                logger.info("Cloud Scanner task cancelled")
                break
            except Exception as e:
                error_msg = f"Scan error: {type(e).__name__}: {e}"
                self.scanner_error.emit(error_msg)
                logger.error(f"☁️ SCANNER ERROR: {error_msg}")
                await asyncio.sleep(5)  # Wait before retry

    def stop(self):
        self.running = False


class SignalListenerThread(QThread):
    """
    Background thread that listens for incoming signals from Cloud Scanner.
    Runs HTTP server on local laptop to receive trade signals.
    """

    signal_received = pyqtSignal(object)  # Emits received signal data
    listener_error = pyqtSignal(str)  # Emits error message

    def __init__(self):
        super().__init__()
        self.running = True
        self.dispatcher = SignalDispatcher()

    def run(self):
        logger.info(f"Signal Listener started on port {config.LOCAL_LISTENER_PORT}")
        try:
            # Set callback
            self.dispatcher.set_signal_callback(self._on_signal_received)

            # Run async HTTP server
            asyncio.run(self._run_server())
        except Exception as e:
            self.listener_error.emit(f"Signal Listener error: {e}")
            logger.error(f"Signal Listener thread error: {e}")

    async def _run_server(self):
        """Run the HTTP server loop."""
        try:
            runner = await self.dispatcher.start_server()
            logger.info(f"✅ Signal Dispatcher listening on port {config.LOCAL_LISTENER_PORT}")
            
            # Verify server is accessible
            import requests
            try:
                health_url = f"http://localhost:{config.LOCAL_LISTENER_PORT}/api/health"
                response = requests.get(health_url, timeout=3)
                if response.status_code == 200:
                    logger.info(f"✅ Signal Dispatcher health check passed")
                else:
                    logger.warning(f"⚠️ Signal Dispatcher health check returned {response.status_code}")
            except Exception as e:
                logger.error(f"⚠️ Signal Dispatcher health check failed: {e}")
            
            try:
                # Keep running until stopped
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
        logger.info(f"Signal received from cloud: {signal_data}")

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

                try:
                    # Capture chart screenshot if vision is enabled
                    chart_base64 = None
                    if self.vision:
                        try:
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
                        except Exception as vision_error:
                            logger.error(f"Vision capture failed for {market_data.asset}: {vision_error}")
                            chart_base64 = None  # Fallback to text-only

                    # Run swarm debate (with or without vision)
                    try:
                        output, transcript = self.analyzer.analyze_market(
                            market_data, chart_image_base64=chart_base64
                        )
                        self.analysis_complete.emit(output, transcript)
                    except Exception as analysis_error:
                        logger.error(f"Swarm analysis failed for {market_data.asset}: {analysis_error}")
                        # Emit None to indicate failure - UI should handle gracefully
                        self.analysis_complete.emit(None, None)
                        
                except Exception as worker_error:
                    logger.error(f"Analysis worker critical error: {worker_error}")
                    # Continue loop - don't crash the thread
            else:
                time.sleep(0.1)


class VcaniTradeApp:
    """
    Main application controller - Hybrid Architecture.
    
    Cloud Scanner (Vast.ai):
    - Monitors 10 tickers using yfinance
    - Detects technical signals (RSI, Volume, SMA)
    - Triggers Swarm Debate when signals detected
    - Dispatches high-confidence signals (>0.70) to local
    
    Local Executor (Laptop):
    - Receives signals via HTTP listener
    - Switches TradingView to target ticker
    - Performs Vision Confirmation (screenshot)
    - Executes Trade via RPA locally
    """

    def __init__(self):
        self.app = QApplication(sys.argv)

        # Initialize account state early so downstream components can safely
        # reference balance during construction.
        self.balance = 10000.0
        self.equity = 10000.0
        self.daily_pnl = 0.0
        self.total_pnl = 0.0
        self.peak_balance = 10000.0
        self.max_drawdown = 0.0
        self.trades_today = 0
        self.positions = []

        # UI — Command Center (main dashboard)
        self.cmd = CommandCenter()
        
        # AI Narrator Overlay (glassmorphic assistant)
        self.ai_narrator = AINarratorOverlay()
        screens = self.app.screens()
        if len(screens) > 1:
            target_geo = screens[1].availableGeometry()
            self.ai_narrator.move(target_geo.left() + 20, target_geo.top() + 20)
        else:
            self.ai_narrator.move(20, 20)
        self.ai_narrator.show()

        # Core
        self.trade_engine = TradeEngine()
        self.grader = Grader()
        self.financial_safety = FinancialSafetyManager()
        self.executor = None  # Will be initialized when browser agent is ready
        self.risk_manager = RiskManager(risk_per_trade_pct=1.0)
        self.trade_executor = TradeExecutor(exchange_client=None)
        exchange_provider = os.getenv("EXCHANGE_PROVIDER", "binance")
        exchange_api_key = os.getenv("EXCHANGE_API_KEY") or os.getenv("BINANCE_API_KEY") or os.getenv("BYBIT_API_KEY")
        exchange_api_secret = os.getenv("EXCHANGE_API_SECRET") or os.getenv("BINANCE_API_SECRET") or os.getenv("BYBIT_API_SECRET")
        self.exchange_executor = ExchangeLimitExecutor(
            provider=exchange_provider,
            api_key=exchange_api_key,
            api_secret=exchange_api_secret,
        )
        self.sql_journal = TradeJournalDB(db_path="trades.db")
        self.session_detector = MarketSessionDetector()
        self.support_resistance_levels = {}
        self.latest_confidence_score = 0.0
        self.analysis_mode_status = "READY"
        
        # STAGE 2: AI Strategist & Dynamic Architect
        self.code_architect = CodeArchitect()  # Pine Script/MQL5 generator
        self.atr_stops = LooseATRStops(atr_period=14, multiplier=1.5)  # Loose ATR stops
        self.visual_confirmation = VisualChartConfirmation(check_interval=60)  # Chart reader
        
        # STAGE 3: Institutional Governor & Risk Architect
        self.risk_governor = RiskGovernor(
            max_risk_units=3,
            max_exposure_per_unit_pct=5.0,
            max_total_exposure_pct=15.0,
            correlation_threshold=0.85
        )  # Correlation-aware risk management
        self.sentiment_pulse = SentimentPulse(
            check_interval=300,  # 5 minutes
            red_folder_minutes_before=30,
            red_folder_minutes_after=15
        )  # News filter & RPA kill switch
        self.profit_lock = ProfitLock(
            daily_profit_target_pct=3.0,
            daily_max_loss_pct=2.0,
            breakeven_buffer_pct=1.0,
            starting_balance=self.balance
        )  # Dynamic equity guard

        # STAGE 4: Meta-Cognition & Alpha Hunter
        self.trade_journal = TradeJournal()  # Persistent trade journal
        self.meta_analyzer = MetaAnalyzer(
            journal=self.trade_journal,
            review_interval_hours=24,  # Self-review every 24 hours
            auto_apply=False  # Suggest only, don't auto-change config
        )  # Self-Correction Engine

        # Browser Agent (Autonomous price checking via Playwright)
        self.browser_agent = None  # Will be started asynchronously when needed
        self.browser_agent_status = "idle"  # idle, starting, ready, error
        self._browser_error_message = ""
        self._browser_executor_initialized = False
        self._browser_start_announced = False
        self._browser_ready_announced = False
        self._browser_error_announced = False
        
        # Load persistent trading settings (amount, lots, risk params)
        self.settings = settings_manager
        self.cmd.log(f"⚙️ Settings loaded: {self.settings.get('investment_mode')} mode")

        # Prop Firm Rule Engine (The "Professor")
        if config.PROP_FIRM_ENABLED:
            from core.prop_firm_rules import PropFirmRuleEngine, PropFirmName
            firm_map = {
                "TopStep": PropFirmName.TOPSTEP,
                "Apex": PropFirmName.APEX,
                "MyFundedFutures": PropFirmName.MYFUNDED,
                "FTMO": PropFirmName.FTMO,
            }
            firm = firm_map.get(config.PROP_FIRM_NAME, PropFirmName.TOPSTEP)
            self.prop_engine = PropFirmRuleEngine(firm)
            # Override account size from config
            self.prop_engine.rules.account_size = config.PROP_ACCOUNT_SIZE
            self.prop_engine.compliance.starting_balance = config.PROP_ACCOUNT_SIZE
            self.prop_engine.compliance.current_balance = config.PROP_ACCOUNT_SIZE
            self.prop_engine.compliance.peak_balance = config.PROP_ACCOUNT_SIZE
            logger.info(f"Prop Firm Rule Engine active: {config.PROP_FIRM_NAME}")
        else:
            self.prop_engine = None
            logger.info("Prop Firm Rule Engine disabled")

        # Hybrid Architecture Threads
        self.cloud_scanner = CloudScannerThread()  # Cloud-based scanning
        self.signal_listener = SignalListenerThread()  # Local HTTP listener
        self.data_scout_listener = DataScoutListenerThread()  # Vast.ai scout listener
        self.watchtower = WatchtowerScanner()  # Local fallback scanner
        self.analysis_worker = AnalysisWorker()  # Local analysis (vision + swarm)

        # State
        self.current_mode = "AUTONOMOUS"
        self.analysis_mode = False
        self.latest_signals = {}
        self.ticker_selector = config.CLOUD_TICKERS[0]  # Default ticker
        self.test_status_text = "Ready"  # Test execution status

        # Balance & P/L tracking
        self.balance = 10000.0  # Starting balance
        self.equity = 10000.0
        self.daily_pnl = 0.0
        self.total_pnl = 0.0
        self.peak_balance = 10000.0
        self.max_drawdown = 0.0
        self.trades_today = 0
        self.daily_wins = 0
        self.positions = []  # Live positions

        # Trading settings
        self.default_investment = 10.0
        self.take_profit_pct = 2.0
        self.stop_loss_pct = 1.0
        self.max_daily_loss = 500.0
        self.trade_ledger = []  # List of executed trades

        self._connect_signals()
        logger.info("VcaniTrade AI initialized (Hybrid Architecture)")
        
        # Auto-start browser agent for testing
        self._start_browser_agent_background()
    
    def _start_browser_agent_background(self):
        """Start browser agent in background thread with persistent event loop."""
        import asyncio
        import threading
        
        def run_browser_loop():
            """Run persistent event loop for browser agent."""
            try:
                # Create persistent event loop
                self._browser_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._browser_loop)
                
                # Run browser agent
                self._browser_loop.run_until_complete(self._init_browser_agent())
                
                # Keep loop alive for future tasks
                self._browser_loop.run_forever()
            except Exception as e:
                self.browser_agent_status = "error"
                self._browser_error_message = str(e)
                logger.error(f"Browser agent background loop failed: {e}")
        
        # Start in background thread
        self._browser_thread = threading.Thread(target=run_browser_loop, daemon=True)
        self._browser_thread.start()
        self.cmd.log("🌐 Browser agent launching in background...")
    
    async def _init_browser_agent(self):
        """Initialize browser agent and executor asynchronously."""
        try:
            self.browser_agent_status = "starting"
            self.browser_agent = BrowserAgent(headless=True)
            await self.browser_agent.start()
            self.browser_agent_status = "ready"
        except Exception as e:
            self.browser_agent_status = "error"
            self._browser_error_message = str(e)
            logger.error(f"Failed to start browser agent: {e}")

    def _sync_browser_agent_state(self):
        """Finalize browser-agent state transitions on the Qt main thread."""
        if self.browser_agent_status == "starting" and not self._browser_start_announced:
            self._browser_start_announced = True
            self.cmd.log("🌐 Browser agent starting...")

        if self.browser_agent_status == "ready" and not self._browser_executor_initialized:
            if self.browser_agent:
                self.executor = UnifiedTradeExecutor(
                    browser_agent=self.browser_agent,
                    cmd_logger=self.cmd.log,
                    ai_narrator=self.ai_narrator,
                )
                self._browser_executor_initialized = True
                if not self._browser_ready_announced:
                    self._browser_ready_announced = True
                    self.cmd.log("🌐 Browser agent ready - autonomous price checking ready")
                    self.cmd.log("🚀 Trade executor initialized with Slippage Guard")
                    self.ai_narrator.add_activity("🌐", "Browser agent ready for autonomous tasks")

        if self.browser_agent_status == "error" and not self._browser_error_announced:
            self._browser_error_announced = True
            msg = self._browser_error_message or "Unknown browser-agent startup error"
            self.cmd.log(f"⚠️ Browser agent failed: {msg}")
            self.ai_narrator.notify_error(f"Browser agent: {msg}")

    def _connect_signals(self):
        """Wire all backend threads to the CommandCenter UI."""
        # Command Center
        self.cmd.mode_changed.connect(self._on_mode_changed)
        self.cmd.kill_switch_triggered.connect(self._on_kill_switch)
        self.cmd.watchlist_updated.connect(self._on_watchlist_updated)
        self.cmd.settings_changed.connect(self._on_settings_changed)
        self.cmd.test_browser_requested.connect(self._on_test_browser)
        self.cmd.force_test_trade_requested.connect(self._on_force_test_trade)
        self.cmd.user_command_sent.connect(self._on_copilot_command)  # NEW: Co-Pilot Command Bridge

        # Cloud Scanner → UI + Narrator
        self.cloud_scanner.signal_detected.connect(self._on_cloud_signal)
        self.cloud_scanner.scanner_error.connect(self._on_scanner_error)

        # Signal Listener → UI + Narrator
        # self.signal_listener.signal_received.connect(self._on_signal_received)
        self.signal_listener.listener_error.connect(self._on_listener_error)

        # Data Scout Listener → UI + TV Flip + Narrator
        self.data_scout_listener.signal_received.connect(self._on_data_scout_signal)

        # Watchtower → UI alerts + Swarm handoff + Narrator
        self.watchtower.alert_detected.connect(self._on_watchtower_alert)
        self.watchtower.market_data_ready.connect(self._on_market_data)

        # Analysis worker → Trade engine + UI + Narrator
        self.analysis_worker.analysis_complete.connect(self._on_analysis_complete)

        # Position monitoring timer
        self.position_timer = QTimer()
        self.position_timer.timeout.connect(self._update_positions)
        self.position_timer.start(5000)  # Update every 5 seconds

        # News filter & safety timer
        self.safety_timer = QTimer()
        self.safety_timer.timeout.connect(self._update_safety_controls)
        self.safety_timer.start(60000)  # Check every 60 seconds

        # Heartbeat monitor - logs system health every 60 seconds
        self.heartbeat_timer = QTimer()
        self.heartbeat_timer.timeout.connect(self._heartbeat_check)
        self.heartbeat_timer.start(60000)  # 60 seconds
        
        # STAGE 3: Institutional Governor update timer (every 30 seconds)
        self.governor_timer = QTimer()
        self.governor_timer.timeout.connect(self._update_institutional_governor_ui)
        self.governor_timer.start(30000)  # 30 seconds

        # STAGE 4: Meta-Cognition Self-Review timer (every 24 hours = 86400000 ms)
        self.meta_review_timer = QTimer()
        self.meta_review_timer.timeout.connect(self._run_meta_cognition_review)
        self.meta_review_timer.start(86400000)  # 24 hours
        self.cmd.log("🧠 Meta-Cognition self-review scheduled (every 24 hours)")

        # Browser agent state sync timer (main-thread finalization)
        self.browser_state_timer = QTimer()
        self.browser_state_timer.timeout.connect(self._sync_browser_agent_state)
        self.browser_state_timer.start(1000)

        self.cmd.log("✅ All systems connected - Ready to trade")
        
        # Notify AI Narrator that system is ready
        self.ai_narrator.notify_system_ready()

    def _on_watchlist_updated(self, watchlist: list):
        """Handle watchlist update from dashboard."""
        config.CLOUD_TICKERS = watchlist
        self.cloud_scanner.scanner.tickers = watchlist
        self.cmd.log(f"📊 Watchlist updated: {len(watchlist)} tickers")
        self.ai_narrator.notify_scan_start(len(watchlist))

    def _on_settings_changed(self, settings: dict):
        """Handle trading settings update from dashboard."""
        # Update persistent settings
        self.settings.update(settings)

        # Update local variables
        self.default_investment = settings.get("investment", 1000.0)
        self.take_profit_pct = settings.get("take_profit_pct", 2.0)
        self.stop_loss_pct = settings.get("stop_loss_pct", 1.0)
        self.max_daily_loss = settings.get("max_daily_loss", 500.0)

        mode = self.settings.get("investment_mode", "dollar")
        if mode == "lots":
            lot_size = self.settings.get("lot_size", 2.0)
            self.cmd.log(f"⚙️ Settings updated: {lot_size} lots/trade, TP={self.take_profit_pct}%, SL={self.stop_loss_pct}%")
        else:
            self.cmd.log(f"⚙️ Settings updated: ${self.default_investment}/trade, TP={self.take_profit_pct}%, SL={self.stop_loss_pct}%")

        # Notify AI Narrator
        self.ai_narrator.add_activity(
            "⚙️",
            f"Settings updated - {mode} mode"
        )

    def _on_copilot_command(self, command: str):
        """Handle Co-Pilot Command Bridge - Process user suggestions"""
        import re
        
        self.cmd.log(f"🚀 CO-PILOT COMMAND: {command}")
        self.cmd.update_copilot_status("Processing...")
        
        # Parse command intent
        command_lower = command.lower()
        
        # Store current command for AI analysis
        self._current_copilot_command = command
        
        # Detect command type
        if any(keyword in command_lower for keyword in ["analysis mode", "focus mode", "chart mode", "professor mode"]):
            self._handle_analysis_mode_command(command)
        elif any(keyword in command_lower for keyword in ["mirror", "overlay", "assistant panel"]):
            self._handle_mirror_control_command(command)
        elif any(keyword in command_lower for keyword in ["support", "resistance", "trend", "break", "blink", "draw line"]):
            self._handle_chart_markup_command(command)
        elif any(keyword in command_lower for keyword in ["switch to", "change timeframe", "use timeframe"]):
            # Timeframe switching
            self._handle_timeframe_change(command)
        elif any(keyword in command_lower for keyword in ["buy now", "sell now", "force buy", "force sell", "force trade"]):
            # Force trade with safety checks
            self._handle_force_trade(command)
        elif any(keyword in command_lower for keyword in ["news", "sentiment", "analyze"]):
            # News analysis
            self._handle_news_analysis(command)
        elif any(keyword in command_lower for keyword in ["show levels", "plot zones", "draw zones", "generate script"]):
            # STAGE 2: Generate Pine Script zones
            self._handle_show_levels(command)
        else:
            # General analysis with user suggestion
            self._handle_general_command(command)

    def _handle_analysis_mode_command(self, command: str):
        """Enable/disable mirror analysis mode for chart-focused trading workflow."""
        cmd = command.lower()
        enable = not any(x in cmd for x in ["off", "disable", "stop", "exit"])
        self.analysis_mode = enable

        context = "Chart lock + visual commentary"
        if hasattr(self, "ai_narrator") and self.ai_narrator:
            self.ai_narrator.set_analysis_mode(enable, context)
            if enable:
                self.ai_narrator.snap_to("top-right", screen_index=1)

        if enable:
            self.cmd.copilot_mode.setText("Current Mode: ANALYSIS MODE")
            self.cmd.log("🧭 Analysis Mode enabled: mirror focused on chart workflow")
        else:
            self.cmd.copilot_mode.setText("Current Mode: SCANNING")
            self.cmd.log("🧭 Analysis Mode disabled")
        self.cmd.update_copilot_status("Ready")

    def _handle_mirror_control_command(self, command: str):
        """Process mirror panel controls through natural language commands."""
        cmd = command.lower()
        if not hasattr(self, "ai_narrator") or not self.ai_narrator:
            self.cmd.log("❌ Mirror not available")
            return

        if "pin" in cmd:
            should_pin = not any(x in cmd for x in ["unpin", "off", "disable"])
            self.ai_narrator.pin_btn.setChecked(should_pin)
            self.ai_narrator._toggle_pin()
            self.cmd.log(f"🧷 Mirror {'pinned' if should_pin else 'unpinned'}")

        if "opacity" in cmd:
            import re
            m = re.search(r"(\d{2,3})", cmd)
            if m:
                value = max(55, min(100, int(m.group(1))))
                self.ai_narrator.opacity_slider.setValue(value)
                self.cmd.log(f"👓 Mirror opacity set to {value}%")

        if "font" in cmd:
            if any(x in cmd for x in ["bigger", "larger", "increase", "up", "+"]):
                self.ai_narrator._change_font_scale(0.1)
                self.cmd.log("🔎 Mirror font increased")
            elif any(x in cmd for x in ["smaller", "decrease", "down", "-"]):
                self.ai_narrator._change_font_scale(-0.1)
                self.cmd.log("🔍 Mirror font decreased")

        if "snap" in cmd or "monitor" in cmd:
            if "bottom right" in cmd:
                self.ai_narrator.snap_to("bottom-right", 1)
                self.cmd.log("🧲 Mirror snapped to monitor 2 bottom-right")
            elif "bottom left" in cmd:
                self.ai_narrator.snap_to("bottom-left", 1)
                self.cmd.log("🧲 Mirror snapped to monitor 2 bottom-left")
            elif "center" in cmd:
                self.ai_narrator.snap_to("center", 1)
                self.cmd.log("🧲 Mirror centered on monitor 2")
            elif "left" in cmd:
                self.ai_narrator.snap_to("top-left", 1)
                self.cmd.log("🧲 Mirror snapped to monitor 2 top-left")
            else:
                self.ai_narrator.snap_to("top-right", 1)
                self.cmd.log("🧲 Mirror snapped to monitor 2 top-right")

        self.cmd.update_copilot_status("Ready")

    def _handle_chart_markup_command(self, command: str):
        """Generate chart markup scripts (S/R, trend breaks, blink lines) and inject to TradingView."""
        import re

        self.cmd.log(f"🧵 CHART MARKUP requested: {command}")

        asset_match = re.search(r'(BTC|ETH|SOL|XRP|BNB|ADA|EURUSD|GOLD|SPY|TSLA|AAPL|NVDA)', command, re.IGNORECASE)
        asset = asset_match.group(0).upper() + "-USD" if asset_match else "BTC-USD"

        tf_match = re.search(r'(\d+[mh])', command.lower())
        timeframe = tf_match.group(1).upper() if tf_match else "2H"

        # Base zones + markup overlay hooks.
        demand_zones = [
            {"low": 69000.0, "high": 69500.0, "strength": 0.82},
            {"low": 68200.0, "high": 68800.0, "strength": 0.70},
        ]
        supply_zones = [
            {"low": 71000.0, "high": 71500.0, "strength": 0.80},
            {"low": 72000.0, "high": 72800.0, "strength": 0.66},
        ]

        self.support_resistance_levels[asset] = {
            "supports": [z["low"] for z in demand_zones],
            "resistances": [z["high"] for z in supply_zones],
        }

        base_script = self.code_architect.generate_pine_script_zones(
            asset=asset,
            demand_zones=demand_zones,
            supply_zones=supply_zones,
            timeframe=timeframe
        )

        markup = self._build_markup_overlay_snippet(command)
        self._last_pine_script = f"{base_script}\n\n// --- MARKUP OVERLAY ---\n{markup}\n"

        self.cmd.add_copilot_response(
            thoughts=f"Generated chart markup overlay for {asset} on {timeframe}",
            verdict="MARKUP_READY",
            adjustment="✅ Support/resistance and trend-break overlays prepared. Injecting to TradingView now."
        )

        self._inject_pine_script_to_chart()
        self.cmd.update_copilot_status("Ready")

    def _build_markup_overlay_snippet(self, command: str) -> str:
        """Build Pine snippet for visual trade coaching overlays."""
        cmd = command.lower()
        lines = []
        lines.append("var line ai_sr_top = na")
        lines.append("var line ai_sr_bot = na")
        lines.append("if barstate.islast")
        lines.append("    ai_sr_top := line.new(bar_index-150, high * 1.01, bar_index, high * 1.01, color=color.new(color.red, 0), width=2)")
        lines.append("    ai_sr_bot := line.new(bar_index-150, low * 0.99, bar_index, low * 0.99, color=color.new(color.green, 0), width=2)")

        if any(x in cmd for x in ["trend", "break", "breakout"]):
            lines.append("var line ai_trend = na")
            lines.append("if barstate.islast")
            lines.append("    ai_trend := line.new(bar_index-80, close[80], bar_index, close, color=color.new(color.yellow, 0), width=2, style=line.style_dashed)")
            lines.append("break_up = ta.crossover(close, ta.sma(close, 20))")
            lines.append("break_dn = ta.crossunder(close, ta.sma(close, 20))")
            lines.append("plotshape(break_up, title='Trend Break Up', color=color.lime, style=shape.triangleup, size=size.small)")
            lines.append("plotshape(break_dn, title='Trend Break Down', color=color.red, style=shape.triangledown, size=size.small)")

        if "blink" in cmd:
            lines.append("blink = bar_index % 2 == 0")
            lines.append("bgcolor(blink ? color.new(color.orange, 92) : na)")

        if any(x in cmd for x in ["support", "resistance"]):
            lines.append("plot(ta.highest(high, 50), title='AI Resistance', color=color.new(color.red, 10), linewidth=2)")
            lines.append("plot(ta.lowest(low, 50), title='AI Support', color=color.new(color.green, 10), linewidth=2)")

        lines.append("label.new(bar_index, high, text='AI: Watch break + reaction', style=label.style_label_down, color=color.new(color.black, 75), textcolor=color.new(color.aqua, 0), size=size.tiny)")
        return "\n".join(lines)

    def _handle_timeframe_change(self, command: str):
        """Handle timeframe switching commands like 'Switch to 2H BTC longs'"""
        import re
        
        # Extract timeframe
        tf_match = re.search(r'(\d+[mh])', command.lower())
        if tf_match:
            new_timeframe = tf_match.group(1)
            self.cmd.log(f"📊 Timeframe change requested: {new_timeframe}")
            
            # Update config dynamically (no restart needed)
            if 'h' in new_timeframe:
                hours = int(new_timeframe.replace('h', ''))
                config.SCAN_INTERVAL = hours * 3600  # Convert to seconds
                self.cmd.copilot_mode.setText(f"Current Mode: {new_timeframe.upper()} SCANNING")
            elif 'm' in new_timeframe:
                minutes = int(new_timeframe.replace('m', ''))
                config.SCAN_INTERVAL = minutes * 60
                self.cmd.copilot_mode.setText(f"Current Mode: {new_timeframe.upper()} SCANNING")
            
            self.cmd.log(f"✅ SCAN_INTERVAL updated to {config.SCAN_INTERVAL}s")
        
        # Trigger AI analysis with user suggestion
        self._trigger_ai_analysis(command)

    def _handle_force_trade(self, command: str):
        """Handle force trade commands - RPA safety STILL APPLIES"""
        self.cmd.log(f"⚠️ FORCE TRADE requested: {command}")
        self.cmd.log(f"🛑 RPA Safety layer ACTIVE: Slippage Guard + Spread Check will run")
        
        # Trigger AI analysis with warning
        self._trigger_ai_analysis(command)

    def _handle_news_analysis(self, command: str):
        """Handle news-based analysis requests"""
        self.cmd.log(f"📰 NEWS ANALYSIS requested: {command}")
        
        # Trigger AI analysis
        self._trigger_ai_analysis(command)

    def _handle_show_levels(self, command: str):
        """STAGE 2: Generate Pine Script zones and optionally inject to TradingView"""
        self.cmd.log(f"🏗️ SHOW LEVELS requested: {command}")
        
        # Parse asset from command
        import re
        asset_match = re.search(r'(BTC|ETH|EURUSD|GOLD|SPY|TSLA|AAPL|NVDA)', command, re.IGNORECASE)
        asset = asset_match.group(0) + "-USD" if asset_match else "BTC-USD"
        
        # Parse timeframe
        tf_match = re.search(r'(\d+[mh])', command.lower())
        timeframe = tf_match.group(1).upper() if tf_match else "2H"
        
        self.cmd.log(f"📊 Generating zones for {asset} on {timeframe}")
        
        # Generate sample zones (would normally come from AI analysis)
        demand_zones = [
            {"low": 69000.0, "high": 69500.0, "strength": 0.85},
            {"low": 68200.0, "high": 68800.0, "strength": 0.72}
        ]
        supply_zones = [
            {"low": 71000.0, "high": 71500.0, "strength": 0.78},
            {"low": 72000.0, "high": 72800.0, "strength": 0.65}
        ]

        self.support_resistance_levels[asset] = {
            "supports": [z["low"] for z in demand_zones],
            "resistances": [z["high"] for z in supply_zones],
        }
        
        # Generate Pine Script
        pine_code = self.code_architect.generate_pine_script_zones(
            asset=asset,
            demand_zones=demand_zones,
            supply_zones=supply_zones,
            timeframe=timeframe
        )
        
        self.cmd.log(f"✅ Pine Script generated ({len(pine_code)} chars)")
        
        # Display in copilot chat
        self.cmd.add_copilot_response(
            thoughts=f"Generated Institutional Demand & Retail Supply zones for {asset}",
            verdict=f"SCRIPT_GENERATED",
            adjustment=f"✅ Pine Script v6 ready. {len(demand_zones)} demand zones, {len(supply_zones)} supply zones. Click 'Inject to Chart' to auto-add to TradingView."
        )
        
        # Store for later injection
        self._last_pine_script = pine_code
        
        # If user said "inject" or "add to chart", do it autonomously
        if any(keyword in command.lower() for keyword in ["inject", "add to chart", "auto"]):
            self._inject_pine_script_to_chart()
        
        self.cmd.update_copilot_status("Ready")

    def _inject_pine_script_to_chart(self):
        """STAGE 2: Auto-inject Pine Script to TradingView via BrowserAgent"""
        import asyncio
        
        if not hasattr(self, '_last_pine_script'):
            self.cmd.log("❌ No Pine Script to inject")
            return
        
        if not self.browser_agent or not self.browser_agent.is_running:
            self.cmd.log("❌ Browser agent not running")
            return
        
        self.cmd.log("🏗️ Injecting Pine Script to TradingView...")
        self.cmd.update_copilot_status("Injecting script...")
        
        # Submit to browser event loop
        asyncio.run_coroutine_threadsafe(
            self._do_inject_pine_script(),
            self._browser_loop
        )

    async def _do_inject_pine_script(self):
        """Actually inject the Pine Script via BrowserAgent"""
        try:
            success = await self.browser_agent.inject_pine_script_to_tradingview(
                self._last_pine_script
            )
            
            if success:
                self.cmd.log("✅ Pine Script injected successfully!")
                self.cmd.add_copilot_response(
                    thoughts="Pine Script added to your TradingView chart",
                    verdict="INJECTED",
                    adjustment="✅ Zones now visible on chart. AI's thoughts are now lines on your live chart!"
                )
                self.cmd.update_copilot_status("Ready")
            else:
                self.cmd.log("❌ Pine Script injection failed")
                self.cmd.add_copilot_response(
                    thoughts="Failed to inject script",
                    verdict="INJECTION_FAILED",
                    adjustment="⚠️ Manual steps needed: Open Pine Editor (Ctrl+P), paste code, click 'Add to Chart'"
                )
                self.cmd.update_copilot_status("Error")
                
        except Exception as e:
            self.cmd.log(f"❌ Injection error: {e}")
            self.cmd.update_copilot_status("Error")

    def _handle_general_command(self, command: str):
        """Handle general Co-Pilot commands"""
        self.cmd.log(f"💬 GENERAL COMMAND: {command}")
        
        # Trigger AI analysis
        self._trigger_ai_analysis(command)

    def _trigger_ai_analysis(self, user_suggestion: str):
        """Trigger Swarm Consensus analysis with user suggestion"""
        try:
            # Get current market data for first ticker
            from core.models import MarketDataPoint
            
            # Create sample market data (will be replaced with real data from scanner)
            market_data = MarketDataPoint(
                asset="BTC-USD",
                price=69500.0,
                timestamp=datetime.now(timezone.utc),
                indicators={
                    "RSI": 45.0,
                    "SIGNAL_TYPE": "VOLUME_SPIKE",
                    "SIGNAL_STRENGTH": 0.75
                }
            )
            
            # Run analysis with user suggestion
            # This will use the NEW swarm consensus with user_suggestion parameter
            import asyncio
            
            # Create task for async analysis
            asyncio.run_coroutine_threadsafe(
                self._run_copilot_analysis(market_data, user_suggestion),
                self._browser_loop if hasattr(self, '_browser_loop') else asyncio.get_event_loop()
            )
            
        except Exception as e:
            self.cmd.log(f"❌ Co-Pilot analysis failed: {e}")
            self.cmd.add_copilot_response(
                thoughts="System error occurred",
                verdict="ERROR",
                adjustment=f"Failed to process command: {e}"
            )
            self.cmd.update_copilot_status("Error")

    def _confidence_to_score(self, confidence_value) -> float:
        """Convert confidence value into a 0-100 score for Professor Mode gating."""
        if isinstance(confidence_value, (int, float)):
            if confidence_value <= 1:
                return float(confidence_value) * 100.0
            return float(confidence_value)

        if isinstance(confidence_value, str):
            mapping = {
                "LOW": 60.0,
                "MEDIUM": 80.0,
                "HIGH": 90.0,
                "VERY_HIGH": 96.0,
            }
            return mapping.get(confidence_value.strip().upper(), 0.0)

        return 0.0

    def _parse_confidence_line(self, text: str) -> Optional[float]:
        """Extract confidence percentage from a line like: Confidence: 92%."""
        if not text:
            return None
        match = re.search(r"(?im)^\s*confidence\s*:\s*(\d{1,3}(?:\.\d+)?)\s*%", str(text))
        if not match:
            return None
        try:
            return max(0.0, min(100.0, float(match.group(1))))
        except ValueError:
            return None

    def _resolve_analysis_confidence(self, output, transcript) -> float:
        """Resolve confidence from AI text first, then fallback to structured fields."""
        text_candidates = []
        text_candidates.append(getattr(output, "reason", ""))
        text_candidates.append(getattr(transcript, "ceo_full_statement", ""))
        text_candidates.append(getattr(transcript, "cto_full_statement", ""))
        text_candidates.append(getattr(transcript, "cfo_full_statement", ""))

        for text in text_candidates:
            parsed = self._parse_confidence_line(text)
            if parsed is not None:
                return parsed

        confidence_obj = getattr(output, "confidence", None)
        if confidence_obj is not None:
            raw_value = getattr(confidence_obj, "value", confidence_obj)
            return self._confidence_to_score(raw_value)

        return 0.0

    async def _run_copilot_analysis(self, market_data, user_suggestion: str):
        """Run AI analysis with user suggestion and display response"""
        try:
            # Import swarm consensus
            from core.swarm_consensus import OllamaSwarmConsensus
            
            # Initialize if not exists
            if not hasattr(self, 'copilot_swarm'):
                self.copilot_swarm = OllamaSwarmConsensus()
            
            # Run analysis with user suggestion
            output, transcript = await self.copilot_swarm.run(
                market_data=market_data,
                user_suggestion=user_suggestion
            )

            confidence_score = self._resolve_analysis_confidence(output, transcript)
            self.latest_confidence_score = confidence_score
            if confidence_score < 85:
                self.cmd.log("Trade Ignored: Low AI Confidence")
                self.analysis_mode_status = "REJECTED - Low Confidence"
                self.cmd.update_copilot_status(self.analysis_mode_status)
                self.ai_narrator.set_status("error", self.analysis_mode_status)
                self.cmd.add_copilot_response(
                    thoughts=output.reason,
                    verdict=f"IGNORED ({confidence_score:.0f}/100)",
                    adjustment="Trade blocked by Professor confidence gate (< 85)."
                )
                return

            self.analysis_mode_status = "APPROVED"
            
            # Parse AI response
            thoughts = output.reason
            verdict = f"{output.action.value} (Confidence: {output.confidence.value})"
            
            # Check if there's user_verdict in the response
            user_verdict = "AGREE"
            user_explanation = ""
            
            # Try to extract user-specific fields from transcript
            if hasattr(transcript, 'ceo_full_statement'):
                # The AI should have included user_verdict in JSON response
                # For now, use the reason field
                user_explanation = thoughts
            
            # Detect agreement/conflict based on analysis
            if output.confidence.value in ["HIGH", "VERY_HIGH"]:
                user_verdict = "AGREE"
            elif output.confidence.value == "LOW":
                user_verdict = "DISAGREE"
            else:
                user_verdict = "FORCE_WITH_WARNING"
            
            # Format response for UI
            adjustment = ""
            if user_verdict == "DISAGREE":
                adjustment = f"⚠️ Safer alternative: Wait for better entry zone. {user_explanation}"
            elif user_verdict == "FORCE_WITH_WARNING":
                adjustment = f"⚠️ Proceeding with caution. {user_explanation}"
            else:
                adjustment = f"✅ Your suggestion aligns with technical analysis. Entry: ${output.entry_price:.2f}"
            
            # Display in copilot chat
            self.cmd.add_copilot_response(
                thoughts=thoughts,
                verdict=verdict,
                adjustment=adjustment
            )
            
            self.cmd.update_copilot_status("Ready")
            
        except Exception as e:
            self.cmd.log(f"❌ Co-Pilot analysis error: {e}")
            self.cmd.add_copilot_response(
                thoughts="Analysis failed",
                verdict="ERROR",
                adjustment=f"Error: {str(e)}"
            )
            self.cmd.update_copilot_status("Error")


    def _on_test_browser(self):
        """Handle test browser click request."""
        import asyncio
        
        self.cmd.log("🧪 TEST: Testing browser agent...")
        
        if not self.browser_agent or not self.browser_agent.is_running:
            self.cmd.log("❌ TEST: Browser agent not running - launching now...")
            # Can't use ensure_future here since loop is in different thread
            # Just log and wait for auto-start
            return
        
        # Submit coroutine to browser's event loop
        asyncio.run_coroutine_threadsafe(
            self._test_browser_navigation(),
            self._browser_loop
        )

    async def _test_browser_navigation(self):
        """Test browser navigation and price fetching."""
        try:
            self.cmd.log("🧪 TEST: Navigating to BTC-USD chart...")
            await self.browser_agent.navigate_to_chart("BTC-USD")
            
            self.cmd.log("🧪 TEST: Fetching live price...")
            price = await self.browser_agent.get_live_price()
            
            if price > 0:
                self.cmd.log(f"✅ TEST SUCCESS: Browser fetched BTC-USD @ ${price:.2f}")
                self.test_status_text = f"Browser working - BTC @ ${price:.2f}"
            else:
                self.cmd.log("❌ TEST FAILED: Price fetch returned 0")
                self.test_status_text = "Browser test failed"
        except Exception as e:
            self.cmd.log(f"❌ TEST ERROR: {e}")
            self.test_status_text = f"Error: {e}"

    def _on_force_test_trade(self):
        """Handle force test trade request (bypasses session blocks and slippage guards)."""
        import asyncio
        import threading

        self.cmd.log("⚡ FORCE TEST: Creating test trade signal (bypassing all guards)...")

        # Get current BTC price for realistic test
        current_btc_price = 73000.0  # Approximate - will be verified by browser

        # Create synthetic test signal with REALISTIC price
        test_signal = {
            "ticker": "BTC-USD",
            "action": "BUY",
            "confidence": 0.85,
            "entry_price": current_btc_price,  # Realistic price
            "stop_loss": current_btc_price * 0.99,  # 1% SL
            "take_profit": current_btc_price * 1.02,  # 2% TP
            "reason": "⚡ FORCE TEST TRADE - Bypassing all guards to verify execution",
            "signal_type": "VOLUME_SPIKE",
            "investment_amount": 1000.0,
            "investment_mode": "dollar",
            "force_execute": True,  # Flag to bypass guards
        }

        # Run async executor in background thread with force_execute=True
        def run_force_test_in_thread():
            """Run force test executor in a new event loop."""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    self._execute_with_unified_executor(test_signal, force_execute=True)
                )
            except Exception as e:
                self.cmd.log(f'<span style="color:#F85149">❌ FORCE TEST ERROR</span>: {e}')
            finally:
                loop.close()

        executor_thread = threading.Thread(target=run_force_test_in_thread, daemon=True)
        executor_thread.start()
        self.cmd.log("⚡ Force test trade dispatched to executor")

    def _refresh_live_ledger(self):
        """Push current stats to the mirror's Live Ledger strip."""
        active = len(self.positions)
        unrealized = sum(p.get("pnl", 0.0) for p in self.positions)
        total_closed = self.trades_today
        rate = (self.daily_wins / total_closed * 100.0) if total_closed > 0 else 0.0
        self.ai_narrator.update_live_ledger(
            active_trades=active,
            unrealized_pnl=unrealized,
            daily_success_rate=rate,
        )

    def _update_positions(self):
        """Update live positions with current prices and check TP/SL."""
        import yfinance as yf
        import concurrent.futures

        updated_positions = []
        for pos in self.positions:
            try:
                # Get current price with timeout protection
                try:
                    ticker = yf.Ticker(pos["asset"])
                    
                    # Run yfinance in thread with timeout
                    def fetch_price():
                        return ticker.history(period="1d", interval="1m")
                    
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(fetch_price)
                        hist = future.result(timeout=10)  # 10 second timeout
                    
                    if hist.empty:
                        self.cmd.log(f"⚠️ No price data for {pos['asset']} - trying browser agent")
                        # Fallback to browser agent if yfinance fails
                        self._verify_price_with_browser(pos)
                        updated_positions.append(pos)
                        continue
                    
                    current_price = hist["Close"].iloc[-1]
                except concurrent.futures.TimeoutError:
                    self.cmd.log(f"⚠️ Timeout fetching {pos['asset']} price - network lag")
                    updated_positions.append(pos)
                    continue
                except Exception as e:
                    self.cmd.log(f"⚠️ Failed to fetch {pos['asset']} price: {e}")
                    updated_positions.append(pos)
                    continue
                
                pos["current"] = current_price

                # Calculate P&L
                if pos["side"] == "BUY":
                    pnl_pct = ((current_price - pos["entry"]) / pos["entry"]) * 100
                    pnl_usd = (current_price - pos["entry"]) * pos["quantity"]
                else:  # SELL
                    pnl_pct = ((pos["entry"] - current_price) / pos["entry"]) * 100
                    pnl_usd = (pos["entry"] - current_price) * pos["quantity"]

                pos["pnl"] = pnl_usd
                pos["pnl_pct"] = pnl_pct

                # Check Take Profit
                if pos["side"] == "BUY" and current_price >= pos["tp_price"]:
                    self.cmd.log(f"🎯 TAKE PROFIT HIT: {pos['asset']} @ ${current_price:.2f} | P&L: +${pnl_usd:.2f}")
                    self._close_position(pos, "Take Profit")
                    continue
                elif pos["side"] == "SELL" and current_price <= pos["tp_price"]:
                    self.cmd.log(f"🎯 TAKE PROFIT HIT: {pos['asset']} @ ${current_price:.2f} | P&L: +${pnl_usd:.2f}")
                    self._close_position(pos, "Take Profit")
                    continue

                # Check Stop Loss
                if pos["side"] == "BUY" and current_price <= pos["sl_price"]:
                    self.cmd.log(f"🛑 STOP LOSS HIT: {pos['asset']} @ ${current_price:.2f} | P&L: ${pnl_usd:.2f}")
                    self._close_position(pos, "Stop Loss")
                    continue
                elif pos["side"] == "SELL" and current_price >= pos["sl_price"]:
                    self.cmd.log(f"🛑 STOP LOSS HIT: {pos['asset']} @ ${current_price:.2f} | P&L: ${pnl_usd:.2f}")
                    self._close_position(pos, "Stop Loss")
                    continue

                updated_positions.append(pos)

            except Exception as e:
                self.cmd.log(f"⚠️ Error updating {pos['asset']}: {e}")
                updated_positions.append(pos)

        self.positions = updated_positions
        self.cmd.update_positions(self.positions)
        live_positions_pnl = sum(p.get("pnl", 0.0) for p in self.positions)
        self.ai_narrator.update_live_pnl(self.total_pnl + live_positions_pnl, len(self.positions))
        self._refresh_live_ledger()
        
        # Update AI Narrator with position status (only if there are positions)
        if self.positions:
            self.ai_narrator.notify_position_update(len(self.positions), self.daily_pnl)
        else:
            # No positions - set to idle/scanning
            self.ai_narrator.set_status("scanning", "No active positions")
    
    def _update_safety_controls(self):
        """Update financial safety controls and check news filter."""
        import asyncio
        
        # Check news filter
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.financial_safety.update_news_filter())
            loop.close()
        except Exception as e:
            logger.error(f"News filter update failed: {e}")
        
        # Check position size mode
        mode = self.financial_safety.current_mode
        paused = self.financial_safety.trading_paused
        
        if paused:
            self.cmd.log(f"🛑 Trading paused: {self.financial_safety.pause_reason}")
            self.ai_narrator.notify_error(f"Trading paused: {self.financial_safety.pause_reason}")
        elif mode.value != "normal":
            multiplier = self.financial_safety.get_position_size_multiplier()
            self.cmd.log(f"📏 Position size mode: {mode.value} ({multiplier:.0%} of normal)")
            self.ai_narrator.add_activity(
                "📏",
                f"Position size: {mode.value} ({multiplier:.0%})"
            )
        
        # Update dashboard with safety status
        safety_status = self.financial_safety.get_safety_status()
        self.cmd.update_safety_status(safety_status)

    def _update_institutional_governor_ui(self):
        """STAGE 3: Update Institutional Governor panel in dashboard."""
        # Collect risk governor data
        risk_summary = self.risk_governor.get_portfolio_summary()
        
        # Collect sentiment pulse data
        sentiment_summary = self.sentiment_pulse.get_dashboard_summary()
        
        # Collect profit lock data
        profit_lock_summary = self.profit_lock.get_dashboard_summary()
        
        # Merge data for dashboard
        governor_data = {
            # Risk Governor
            "total_exposure_pct": risk_summary["total_exposure_pct"],
            "max_total_exposure_pct": self.risk_governor.max_total_exposure_pct,
            "avg_correlation": risk_summary["avg_correlation"],
            
            # Sentiment Pulse
            "next_event": sentiment_summary["next_event"],
            "time_to_event": sentiment_summary["time_to_event"],
            "rpa_enabled": sentiment_summary["rpa_status"] == "ACTIVE",
            
            # Profit Lock
            "stops_locked": profit_lock_summary["stops_locked"],
            "daily_pnl_pct": profit_lock_summary["daily_pnl_pct"],
            "daily_pnl_dollars": profit_lock_summary["daily_pnl_dollars"],
            "progress_to_target": profit_lock_summary["progress_to_target"],
            
            # Walk Away Protocol
            "walk_away_can_trade": profit_lock_summary["can_trade"],
            "walk_away_remaining_hours": (
                profit_lock_summary["walk_away"]["remaining_hours"]
                if profit_lock_summary["walk_away"]["active"] else 0
            )
        }
        
        # Update dashboard UI
        self.cmd.update_institutional_governor(governor_data)

    def _run_meta_cognition_review(self):
        """
        STAGE 4: Meta-Cognition Self-Review.

        Every 24 hours, the AI reviews the trade_ledger.json and:
        1. Identifies worst performing asset
        2. Identifies best performing timeframe
        3. Suggests config adjustments
        4. Updates Alpha Score
        """
        self.cmd.log("=" * 60)
        self.cmd.log("🧠 META-COGNITION SELF-REVIEW INITIATED")
        self.cmd.log("=" * 60)

        try:
            # Run self-review
            review_result = self.meta_analyzer.perform_self_review()

            if review_result["status"] == "INSUFFICIENT_DATA":
                self.cmd.log(
                    f"🧠 Meta-Review skipped: Only {review_result.get('trades_analyzed', 0)} trades (need 5+)"
                )
                self.ai_narrator.add_activity("🧠", "Meta-review: Insufficient data")
                return

            # Log review results
            self.cmd.log(f"📊 Trades Analyzed: {review_result['trades_analyzed']}")
            self.cmd.log(f"🎯 Alpha Score: {review_result['alpha_score']:.1f}/100")

            if review_result.get("worst_asset"):
                worst = review_result["worst_asset"]
                self.cmd.log(
                    f"📉 Worst Performer: {worst.get('asset', 'N/A')} "
                    f"(${worst.get('total_pnl', 0):.2f} PnL, "
                    f"{worst.get('win_rate', 0):.0%} win rate)"
                )

            if review_result.get("best_asset"):
                best = review_result["best_asset"]
                self.cmd.log(
                    f"📈 Best Performer: {best.get('asset', 'N/A')} "
                    f"(${best.get('total_pnl', 0):.2f} PnL, "
                    f"{best.get('win_rate', 0):.0%} win rate)"
                )

            if review_result.get("best_timeframe"):
                best_tf = review_result["best_timeframe"]
                self.cmd.log(
                    f"⏱️  Best Timeframe: {best_tf.get('timeframe', 'N/A')} "
                    f"({best_tf.get('win_rate', 0):.0%} win rate)"
                )

            if review_result.get("adjustments_suggested", 0) > 0:
                self.cmd.log(
                    f"💡 Adjustments Suggested: {review_result['adjustments_suggested']}"
                )
                if review_result.get("adjustments_applied", 0) > 0:
                    self.cmd.log(
                        f"✅ Adjustments Applied: {review_result['adjustments_applied']}"
                    )

            # Update dashboard with Alpha Score
            learning_summary = self.meta_analyzer.get_learning_summary()
            self.cmd.update_meta_cognition(learning_summary)

            # Notify AI Narrator
            self.ai_narrator.add_activity(
                "🧠",
                f"Meta-review complete | Alpha: {review_result['alpha_score']:.1f}"
            )

            self.cmd.log("=" * 60)
            self.cmd.log("🧠 META-COGNITION REVIEW COMPLETE")
            self.cmd.log("=" * 60)

        except Exception as e:
            self.cmd.log(f"❌ Meta-Cognition review failed: {e}")
            logger.error(f"Meta-Cognition review error: {e}")
            self.ai_narrator.notify_error(f"Meta-review failed: {e}")

    def _heartbeat_check(self):
        """
        Heartbeat Monitor - Logs system health every 60 seconds.
        Now includes Market Session context.
        """
        # Get system stats
        active_trades = len(self.positions)
        daily_pnl = self.daily_pnl
        mode = self.current_mode
        
        # Get session status
        session_status = self.session_detector.get_session_status_log()
        
        # Check executor stats if available
        executor_status = ""
        if self.executor:
            stats = self.executor.get_execution_stats()
            if stats['consecutive_failures'] > 0:
                executor_status += f" | ⚠️ {stats['consecutive_failures']} consecutive failures"
            if stats['safety_stop_active']:
                executor_status += " | 🛑 Safety Stop Active"
        
        # Log heartbeat with session context
        self.cmd.log(
            f'[HEARTBEAT] System Healthy - {session_status} | {mode} mode | '
            f'Positions: {active_trades} | Daily P/L: ${daily_pnl:.2f} | '
            f'Trades Today: {self.trades_today}{executor_status}'
        )
        
        # Update AI Narrator status if idle
        if active_trades == 0:
            session_context = self.session_detector.get_session_context()
            if self.session_detector.is_weekend():
                self.ai_narrator.set_status("scanning", "Weekend - Crypto only")
            else:
                self.ai_narrator.set_status("scanning", f"{session_context['primary_session']} session active")

    def _verify_price_with_browser(self, position: dict):
        asset = position.get("asset", "UNKNOWN")
        self.cmd.log(f"🌐 Browser agent checking {asset} price autonomously...")
        self.ai_narrator.set_status("analyzing", f"Browser checking {asset}")
        
        # Run browser check in background thread
        def browser_check():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(
                    self._browser_price_check(asset)
                )
                loop.close()
                return result
            except Exception as e:
                logger.error(f"Browser price check failed: {e}")
                return None
        
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(browser_check)
            try:
                result = future.result(timeout=30)  # 30 second timeout
                if result and "price" in result:
                    self.cmd.log(f"✅ Browser found {asset} @ ${result['price']:.2f}")
                    position["current"] = result["price"]
                    if position.get("entry", 0) > 0:
                        qty = position.get("quantity", 0)
                        if position.get("side") == "BUY":
                            position["pnl"] = (position["current"] - position["entry"]) * qty
                        else:
                            position["pnl"] = (position["entry"] - position["current"]) * qty
                    self.ai_narrator.add_activity(
                        "🌐",
                        f"Browser verified {asset} @ ${result['price']:.2f}"
                    )
                else:
                    self.cmd.log(f"⚠️ Browser couldn't find price for {asset}")
            except concurrent.futures.TimeoutError:
                self.cmd.log(f"⚠️ Browser price check timeout for {asset}")
    
    async def _browser_price_check(self, asset: str):
        """Async browser price check using TradingView."""
        if not self.browser_agent:
            self.browser_agent = BrowserAgent(headless=True)
            await self.browser_agent.start()
        
        return await self.browser_agent.execute_autonomous_task(asset, "check_price")

    def _ensure_balance_state(self):
        """Defensive balance-state initialization for late/mocked call paths."""
        if not hasattr(self, "balance"):
            self.balance = 10000.0
        if not hasattr(self, "equity"):
            self.equity = float(self.balance)
        if not hasattr(self, "daily_pnl"):
            self.daily_pnl = 0.0
        if not hasattr(self, "total_pnl"):
            self.total_pnl = 0.0
        if not hasattr(self, "peak_balance"):
            self.peak_balance = float(self.balance)
        if not hasattr(self, "max_drawdown"):
            self.max_drawdown = 0.0

    def _close_position(self, position: dict, reason: str):
        """Close a position and update P&L."""
        self._ensure_balance_state()
        pnl = position.get("pnl", 0)
        self.balance += pnl
        self.daily_pnl += pnl
        self.total_pnl += pnl

        # Update peak balance and drawdown
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        self.max_drawdown = max(0, self.peak_balance - self.balance)

        # Record trade with Prop Firm Engine (The "Professor" tracks everything)
        if self.prop_engine:
            self.prop_engine.record_trade(pnl, position["asset"])
            compliance = self.prop_engine.get_dashboard_data()
            self.cmd.update_prop_firm_compliance(compliance)

        # Update UI
        self.cmd.update_balance(self.balance, self.equity, self.daily_pnl, self.total_pnl)
        self.cmd.add_trade_log(
            position["asset"],
            "CLOSE",
            position["amount"],
            pnl,
            f"Closed - {'Profit' if pnl > 0 else 'Loss'} ({reason})"
        )
        
        self.cmd.log(f"✅ Position closed: {position['asset']} | {reason} | P&L: ${pnl:.2f}")
        journal_id = position.get("journal_id")
        if journal_id:
            outcome = f"{reason} | PnL={pnl:.2f}"
            self.sql_journal.update_outcome(journal_id, outcome)
        if pnl > 0:
            self.daily_wins += 1
        self.ai_narrator.update_live_pnl(self.total_pnl, len(self.positions))
        self._refresh_live_ledger()

    def _on_market_data(self, market_data: MarketDataPoint):
        """Queue market data for Swarm analysis."""
        self.analysis_worker.add_to_queue(market_data)

    def _on_cloud_signal(self, signal_data: dict):
        """Handle signal detected by cloud scanner - executes IMMEDIATELY in AUTONOMOUS mode."""
        self.cmd.log(
            f'<span style="color:#00D4FF;font-weight:bold">☁️ CLOUD SCANNER</span>: '
            f'{signal_data["action"]} {signal_data["ticker"]} '
            f'(confidence: {signal_data["confidence"]:.2f})'
        )
        
        # Update AI Narrator
        self.ai_narrator.notify_signal_detected(
            signal_data["ticker"],
            signal_data.get("signal_type", "Signal"),
            signal_data["confidence"]
        )

        # Update trade ledger
        self._add_to_trade_ledger(signal_data)

        # MODE LOGIC:
        # - AUTONOMOUS: Execute immediately with new RPA hand logging
        if self.current_mode == "AUTONOMOUS":
            self.cmd.log(
                f'<span style="color:#3FB950;font-weight:bold">[AUTONOMOUS]</span>: '
                f'Executing {signal_data["action"]} on {signal_data["ticker"]} via RPA Hand'
            )

            # Execute on the main-thread-safe path to avoid Qt timer/thread issues.
            self._execute_cloud_signal(signal_data)
        else:
            # TEACHER MODE - Show dialog
            dialog = SignalApprovalDialog(signal_data, self.cmd)
            dialog.approved.connect(self._on_signal_approved)
            dialog.rejected.connect(self._on_signal_rejected)
            dialog.exec()

    def _on_signal_approved(self, signal_data: dict):
        """Handle user approval of trade signal."""
        amount = signal_data.get("investment_amount", 0)
        self.cmd.log(
            f'<span style="color:#3FB950;font-weight:bold">✅ APPROVED</span>: '
            f'{signal_data["action"]} {signal_data["ticker"]} '
            f'with ${amount:.2f}'
        )
        
        # Update AI Narrator
        self.ai_narrator.notify_trade_approved(
            signal_data["ticker"],
            signal_data["action"],
            amount
        )

        # Execute the trade
        self._execute_cloud_signal(signal_data)

    def _on_signal_rejected(self, signal_data: dict):
        """Handle user rejection of trade signal."""
        self.cmd.log(
            f'<span style="color:#F85149;font-weight:bold">❌ REJECTED</span>: '
            f'User declined {signal_data["action"]} {signal_data["ticker"]}'
        )
        
        # Update AI Narrator
        self.ai_narrator.notify_trade_rejected(signal_data["ticker"])

    def _on_signal_received(self, signal_data: dict):
        """Handle signal received from cloud via HTTP."""
        action = signal_data.get("action", "HOLD")
        ticker = signal_data.get("ticker", "UNKNOWN")
        confidence = signal_data.get("confidence", 0.0)
        entry_price = signal_data.get("entry_price", 0.0)

        confidence_score = self._confidence_to_score(confidence)
        if confidence_score < 85:
            self.analysis_mode_status = "REJECTED - Low Confidence"
            self.cmd.update_copilot_status(self.analysis_mode_status)
            self.ai_narrator.set_status("error", self.analysis_mode_status)
            self.cmd.log("Trade Ignored: Low AI Confidence")
            return
        
        # DEBUG: Log current mode and signal details
        self.cmd.log(
            f'<span style="color:#3FB950;font-weight:bold">📡 SIGNAL RECEIVED</span>: '
            f'{action} {ticker} (confidence: {confidence:.2f}, entry: ${entry_price:.2f})'
        )
        self.cmd.log(
            f'<span style="color:#8B949E">🔍 DEBUG</span>: '
            f'Mode={self.current_mode}, Action={action}, Confidence={confidence}, EntryPrice={entry_price}'
        )

        # Update trade ledger
        self._add_to_trade_ledger(signal_data)

        # AUTO-EXECUTE in autonomous mode for approved BUY/SELL signals
        if action in ["BUY", "SELL"] and entry_price > 0:
            self.cmd.log(
                f'<span style="color:#D29922;font-weight:bold">⚡ HIGH CONFIDENCE</span>: '
                f'Auto-executing {action} {ticker} (confidence: {confidence:.2f})'
            )
            self.ai_narrator.notify_trade_approved(ticker, action, 1000.0)
            self.cmd.log(f'<span style="color:#D29922">🔧 Calling _execute_cloud_signal...</span>')
            try:
                self._execute_cloud_signal(signal_data)
                self.cmd.log(f'<span style="color:#3FB950">✅ _execute_cloud_signal completed</span>')
            except Exception as e:
                self.cmd.log(f'<span style="color:#F85149">❌ Execution failed: {e}</span>')
                import traceback
                self.cmd.log(f'<span style="color:#F85149">📝 {traceback.format_exc()}</span>')
        elif self.current_mode == "AUTONOMOUS" and action in ["BUY", "SELL"]:
            # In autonomous mode, execute all BUY/SELL signals
            self.cmd.log(
                f'<span style="color:#D29922;font-weight:bold">🤖 AUTONOMOUS</span>: '
                f'Executing {action} {ticker} in autonomous mode'
            )
            try:
                self._execute_cloud_signal(signal_data)
            except Exception as e:
                self.cmd.log(f'<span style="color:#F85149">❌ Autonomous execution failed: {e}</span>')
        else:
            # TEACHER mode or low confidence - just log it
            self.cmd.log(
                f'<span style="color:#8B949E">📝 LOGGED</span>: '
                f'{action} {ticker} signal logged (waiting for approval or higher confidence)'
            )

    def _on_scanner_error(self, error: str):
        """Handle cloud scanner error."""
        self.cmd.log(
            f'<span style="color:#F85149;font-weight:bold">☁️ SCANNER ERROR</span>: '
            f'{error}'
        )
        self.ai_narrator.notify_error(f"Scanner: {error}")

    def _on_listener_error(self, error: str):
        """Handle signal listener error."""
        self.cmd.log(
            f'<span style="color:#F85149;font-weight:bold">📡 LISTENER ERROR</span>: '
            f'{error}'
        )
        self.ai_narrator.notify_error(f"Listener: {error}")

    def _execute_cloud_signal(self, signal_data: dict):
        """Execute a cloud-generated signal locally with Professor Mode controls."""
        action = signal_data.get("action", "HOLD").upper()
        ticker = signal_data.get("ticker", "UNKNOWN")
        entry_price = float(signal_data.get("entry_price", 0.0) or 0.0)
        confidence_score = self._confidence_to_score(
            signal_data.get("confidence", self.latest_confidence_score)
        )

        # ── Confidence Gate ───────────────────────────────────────────────
        if confidence_score < 85:
            self.analysis_mode_status = "REJECTED - Low Confidence"
            self.cmd.update_copilot_status("TRADE SKIPPED: Criteria not met")
            self.ai_narrator.set_status("error", "TRADE SKIPPED: Criteria not met")
            self.cmd.log(
                f'<span style="color:#D29922;font-weight:bold">⚠️ TRADE SKIPPED: Criteria not met</span> '
                f'— Confidence {confidence_score:.0f}% < 85% required'
            )
            return

        if action not in ["BUY", "SELL"]:
            self.cmd.log(f"⚠️ Unsupported action for execution: {action}")
            return

        if entry_price <= 0:
            self.cmd.log(f"⚠️ No valid entry price for {ticker} - cannot execute trade")
            self.ai_narrator.notify_error(f"No entry price for {ticker}")
            return

        # Check if trading is paused due to news
        if self.financial_safety.trading_paused:
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">🛑 NEWS FILTER BLOCKED</span>: '
                f'{ticker} - {self.financial_safety.pause_reason}'
            )
            self.ai_narrator.notify_error(f"News filter blocked: {ticker}")
            return

        levels = self.support_resistance_levels.get(ticker, {
            "supports": signal_data.get("support_levels", []),
            "resistances": signal_data.get("resistance_levels", []),
        })

        # ── PositionSizer Risk Gate ───────────────────────────────────────
        sizer = PositionSizer(balance=self.balance, risk_pct=1.0)
        risk_eval = sizer.evaluate(entry_price=entry_price, side=action, levels=levels)

        if not risk_eval["ok"] or risk_eval["risk_score"] != "Low":
            self.analysis_mode_status = "REJECTED - Too Risky"
            self.cmd.update_copilot_status("TRADE SKIPPED: Criteria not met")
            self.ai_narrator.set_status("error", "TRADE SKIPPED: Criteria not met")
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">🛑 TRADE SKIPPED: Criteria not met</span> '
                f'— RiskScore=High | {risk_eval["reason"]}'
            )
            self.ai_narrator.add_activity("🛑", f"TRADE SKIPPED — {risk_eval['reason'][:80]}")
            return

        sl_price = risk_eval["stop_loss"]
        risk_dollar = risk_eval["risk_amount"]
        quantity = risk_eval["quantity"]

        if quantity <= 0:
            self.cmd.log(f"⚠️ Invalid risk sizing for {ticker}; aborting trade")
            return

        amount = quantity * entry_price
        safe_amount, size_mode = self.financial_safety.calculate_safe_position_size(
            amount,
            self.daily_pnl,
            self.max_daily_loss,
        )
        if size_mode.value != "normal" and amount > 0:
            scale = safe_amount / amount
            quantity *= max(0.0, scale)
            amount = quantity * entry_price
            self.cmd.log(
                f'<span style="color:#D29922;font-weight:bold">📏 SAFETY MODE</span>: '
                f'Position reduced to ${amount:.2f} ({size_mode.value})'
            )

        if self.daily_pnl <= -self.max_daily_loss:
            self.cmd.log(f"🛑 MAX DAILY LOSS REACHED (${self.daily_pnl:.2f} / -${self.max_daily_loss:.2f}) - Trading halted")
            self.ai_narrator.notify_error("Max daily loss reached")
            return

        # Prop firm risk gate uses hard-stop implied loss.
        if self.prop_engine:
            potential_loss = abs(entry_price - sl_price) * quantity
            can_trade, violations = self.prop_engine.check_before_trade(ticker, potential_loss)
            if not can_trade:
                self.cmd.log(f'<span style="color:#F85149;font-weight:bold">🛑 PROP FIRM BLOCKED</span>: {ticker}')
                for v in violations:
                    self.cmd.log(f'<span style="color:#F85149">   {v}</span>')
                self.ai_narrator.notify_error(f"Prop firm blocked: {ticker}")
                return

        # ── Place Limit Order via ExchangeInterface ───────────────────────
        exchange_order = self.exchange_executor.place_limit_entry(
            symbol=ticker,
            side=action,
            quantity=quantity,
            entry_price=entry_price,
        )

        tp_price = (
            entry_price * (1 + self.take_profit_pct / 100)
            if action == "BUY"
            else entry_price * (1 - self.take_profit_pct / 100)
        )

        journal_id = self.sql_journal.save_trade(
            coin=ticker,
            entry=entry_price,
            stop_loss=sl_price,
            ai_confidence=confidence_score,
            ai_reasoning=signal_data.get("reason", "No reasoning provided"),
            outcome="OPEN",
        )

        position = {
            "asset": ticker,
            "side": action,
            "entry": entry_price,
            "current": entry_price,
            "amount": amount,
            "quantity": quantity,
            "tp_price": tp_price,
            "sl_price": sl_price,
            "pnl": 0.0,
            "pnl_pct": 0.0,
            "order_id": exchange_order.get("order_id", ""),
            "journal_id": journal_id,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }

        self.positions.append(position)
        self.trades_today += 1

        self.cmd.update_positions(self.positions)
        self.cmd.add_trade_log(ticker, action, amount, 0, "Open")

        self.cmd.log(
            f'<span style="color:#3FB950;font-weight:bold">✅ POSITION OPENED</span>: '
            f'{action} {ticker} @ ${entry_price:.2f} | Amount: ${amount:.2f} | '
            f'Qty: {quantity:.4f} | TP: ${tp_price:.2f} | SL: ${sl_price:.2f}'
        )
        self.cmd.log(
            f'<span style="color:#8B949E">🧾 JOURNAL</span>: SAVING TO DB... '
            f'Trade #{journal_id} | ORDER={exchange_order.get("status", "unknown")}'
        )
        reason_text = signal_data.get("reason", "n/a")
        self.cmd.log(
            f'<span style="color:#58A6FF">📊 Professor</span>: '
            f'Confidence[{confidence_score:.0f}%] | RISK: ${risk_dollar:.2f} | '
            f'REASON: {reason_text[:120]}'
        )
        # ── Mirror: Professor Dashboard Lines ────────────────────────────
        self.ai_narrator.add_activity("🎓", f"Confidence [{confidence_score:.0f}%] {'HIGH CONVICTION' if confidence_score >= 92 else 'APPROVED'}")
        self.ai_narrator.add_activity("🛡️", f"RISK: ${risk_dollar:.2f} | LEV: 5x | RiskScore: LOW")
        self.ai_narrator.add_activity("🧠", f"REASON: {reason_text[:80]}")
        self.ai_narrator.add_activity("🧾", "SAVING TO DB...")

        if self.browser_agent and action in ["BUY", "SELL"]:
            self.cmd.log(f"🌐 Browser agent verifying {ticker} price...")
            self._verify_price_with_browser(position)

        self.ai_narrator.notify_trade_executed(ticker, action, entry_price)
        live_positions_pnl = sum(p.get("pnl", 0.0) for p in self.positions)
        self.ai_narrator.update_live_pnl(self.total_pnl + live_positions_pnl, len(self.positions))
        self._refresh_live_ledger()

    async def _execute_with_unified_executor(self, signal_data: dict, force_execute: bool = False):
        """
        Execute trade using the Unified Trade Executor with Slippage Guard.
        This is the production-ready execution path with full safety checks.
        
        Args:
            signal_data: Trade signal data
            force_execute: If True, bypasses confidence and slippage guards (TEST MODE)
        """
        ticker = signal_data.get("ticker", "UNKNOWN")
        action = signal_data.get("action", "HOLD")

        mode_label = "⚡ FORCE TEST" if force_execute else "🚀 UNIFIED EXECUTOR"
        self.cmd.log(
            f'<span style="color:#00D4FF;font-weight:bold">{mode_label}</span>: '
            f'Processing {action} {ticker}'
        )

        # Check if executor is available
        if not self.executor:
            self.cmd.log("⚠️ Trade executor not available - falling back to standard execution")
            if self.browser_agent:
                # Initialize executor now
                self.executor = UnifiedTradeExecutor(
                    browser_agent=self.browser_agent,
                    cmd_logger=self.cmd.log,
                    ai_narrator=self.ai_narrator,
                )
            else:
                self.cmd.log("❌ Browser agent not available - cannot execute trade")
                return

        # Execute via unified executor (includes Slippage Guard)
        try:
            result = await self.executor.execute_signal(
                signal_data=signal_data,
                auto_execute=not config.TEACHER_MODE,  # Auto-execute in autonomous mode
                force_execute=force_execute,  # Bypass guards if requested
            )

            # Log result
            if result.status.value == "SUCCESS":
                self.cmd.log(
                    f'<span style="color:#3FB950;font-weight:bold">✅ EXECUTION SUCCESS</span>: '
                    f'{result.ticker} {result.action} @ ${result.execution_price:.2f} | '
                    f'Slippage: {result.slippage_pct:.3f}% | '
                    f'Spread: {result.spread_pct:.3f}%'
                )
                
                # Add to positions for monitoring
                if result.ticker in self.executor.active_trades:
                    trade_data = self.executor.active_trades[result.ticker]
                    position = {
                        "asset": result.ticker,
                        "side": result.action,
                        "entry": result.execution_price,
                        "current": result.execution_price,
                        "amount": signal_data.get("investment_amount", 0),
                        "quantity": result.quantity,
                        "tp_price": signal_data.get("take_profit", 0),
                        "sl_price": signal_data.get("stop_loss", 0),
                        "pnl": 0.0,
                        "pnl_pct": 0.0,
                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                        "order_id": result.order_id,
                    }
                    self.positions.append(position)
                    self.trades_today += 1
                    
                    self.cmd.update_positions(self.positions)
                    self.cmd.add_trade_log(
                        result.ticker,
                        result.action,
                        signal_data.get("investment_amount", 0),
                        0,
                        "Open"
                    )

            else:
                # Execution failed or skipped
                color = "#F85149" if "ABORT" in result.status.value or "FAIL" in result.status.value else "#D29922"
                self.cmd.log(
                    f'<span style="color:{color};font-weight:bold">⚠️ {result.status.value}</span>: '
                    f'{result.ticker} | {result.error_message}'
                )

        except Exception as e:
            self.cmd.log(f'<span style="color:#F85149;font-weight:bold">❌ EXECUTOR ERROR</span>: {e}')
            import traceback
            self.cmd.log(f'<span style="color:#F85149">📝 {traceback.format_exc()}</span>')

    def flip_chart(self, symbol: str):
        """
        Immediately flip TradingView to the target symbol via keyboard RPA.
        Triggers vision confirmation and updates UI status.
        """
        self.cmd.log(f'<span style="color:#00D4FF;font-weight:bold">📡 CLOUD SIGNAL</span>: Processing {symbol}...')
        # self.cmd.set_vision_status(True, "Confirming...")  # Removed - no status indicators in new dashboard

        # 1. TradingView Flip Logic
        try:
            # Type the symbol and press enter
            pyautogui.typewrite(symbol, interval=0.1)
            time.sleep(0.5)
            pyautogui.press("enter")
            self.cmd.log(f'<span style="color:#3FB950">✅ FLIPPED</span>: Chart switched to {symbol}')
        except Exception as e:
            self.cmd.log(f'<span style="color:#F85149">❌ FLIP FAILED</span>: {e}')
            return

        # 2. Trigger Vision Confirmation
        # We create a temporary MarketDataPoint to feed the analyzer
        data_point = MarketDataPoint(
            asset=symbol,
            price=0.0, # Will be filled by vision/analysis if possible
            timestamp=datetime.now(timezone.utc).isoformat(),
            source="CLOUD_SCOUT"
        )
        self.analysis_worker.add_to_queue(data_point)
        self.cmd.log(f'<span style="color:#D29922">👁️ VISION</span>: Final confirmation triggered for {symbol}')

    def _on_data_scout_signal(self, symbol: str):
        """Handler for signal from Data Scout (Vast.ai)."""
        self.flip_chart(symbol)

    def _on_watchtower_alert(self, alert: WatchlistAlert):
        """Handle Watchtower anomaly alert."""
        self.cmd.log(
            f'<span style="color:#F85149;font-weight:bold">WATCHTOWER</span>: '
            f"[{alert.severity}] {alert.alert_type} on {alert.asset} — {alert.reason}"
        )

    def _on_analysis_complete(self, analysis, transcript: DebateTranscript = None):
        """Handle Swarm Consensus result — all UI updates on main thread."""
        # Handle failed analysis
        if analysis is None:
            self.cmd.log(
                f'<span style="color:#F85149;font-weight:bold">⚠️ ANALYSIS FAILED</span>: '
                f'Swarm debate failed to complete. Skipping trade.'
            )
            return
            
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

        # Update dashboard
        # self.overlay.update_signal_handler(overlay_signal)  # Removed overlay
        self.cmd.log(f"📊 Signal: {overlay_signal.action} {overlay_signal.asset} @ ${overlay_signal.entry_price:.2f}")

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

            # Display CEO verdict (ceo_verdict is a string, not an object)
            self.cmd.log(f"🎯 CEO Verdict: {transcript.ceo_verdict}")
            # self.overlay.update_debate_transcript(transcript)  # Removed overlay
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
        self.cmd.log(f"🔄 Mode changed to: {mode}")
        self.ai_narrator.set_status("idle", f"Mode: {mode}")

    def _on_ticker_changed(self, ticker: str):
        """Handle ticker selection change."""
        self.ticker_selector = ticker
        self.cmd.log(f'Ticker changed to: {ticker}')

    def _add_to_trade_ledger(self, signal_data: dict):
        """Add signal to trade ledger for UI display."""
        trade_record = {
            "asset": signal_data.get("ticker", "N/A"),
            "action": signal_data.get("action", "HOLD"),
            "price": signal_data.get("entry_price", 0.0),
            "confidence": signal_data.get("confidence", 0.0),
            "result": "PENDING",
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S")
        }
        self.trade_ledger.append(trade_record)

        # Keep only last 50 trades
        if len(self.trade_ledger) > 50:
            self.trade_ledger = self.trade_ledger[-50:]

        # Update UI - add to trade log
        self.cmd.add_trade_log(
            trade_record.get("asset", ""),
            trade_record.get("action", ""),
            trade_record.get("amount", 0),
            trade_record.get("pnl", 0),
            trade_record.get("status", "Closed")
        )

    def _on_kill_switch(self):
        self.trade_engine.activate_kill_switch()
        self.cloud_scanner.stop()
        self.signal_listener.stop()
        self.data_scout_listener.stop()
        self.watchtower.stop()
        logger.critical("Kill switch activated — all systems halted")

    def _on_calibrate(self):
        """Open the RPA Coordinate Mapper wizard."""
        dialog = CalibrationWizardDialog(self.cmd)
        dialog.calibration_complete.connect(self._refresh_calibration_status)
        dialog.exec()

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
        logger.info("Starting VcaniTrade AI (Hybrid)...")
        self._ensure_balance_state()

        # Show dashboard
        self.cmd.show()

        # Initialize UI with balance and ledger
        self.cmd.update_balance(self.balance, self.equity, self.daily_pnl, self.total_pnl)

        # Start background threads
        if config.CLOUD_SCANNER_ENABLED:
            self.cloud_scanner.start()
            self.cmd.log("☁️ Cloud Scanner started")
        else:
            self.cmd.log("☁️ Cloud Scanner disabled - using local watchlist only")

        self.signal_listener.start()
        self.data_scout_listener.start()
        self.watchtower.start()
        self.analysis_worker.start()

        # Startup messages
        self.cmd.log("VcaniTrade AI started — Hybrid Architecture")
        self.cmd.log(f"Cloud Scanner: {'ENABLED' if config.CLOUD_SCANNER_ENABLED else 'DISABLED'}")
        self.cmd.log(f"Signal Listener: Port {config.LOCAL_LISTENER_PORT}")
        self.cmd.log(f"Data Scout: Port 5000")
        self.cmd.log(f"Monitoring: {len(config.CLOUD_TICKERS)} tickers")
        self.cmd.log("Mode: TEACHER — RPA disarmed")

        logger.info("Application running")
        sys.exit(self.app.exec())

    def cleanup(self):
        self.cloud_scanner.stop()
        self.signal_listener.stop()
        self.data_scout_listener.stop()
        self.watchtower.stop()
        self.trade_engine.cleanup()
        logger.info("Application shutdown complete")


def main():
    """Entry point."""
    if not _acquire_single_instance_lock():
        print("Another VcaniTrade dashboard instance is already running. Exiting.")
        return

    print("=" * 60)
    print("VcaniTrade AI — Hybrid Trading Assistant")
    print("=" * 60)
    print(f"Mode:      TEACHER (safe)")
    print(f"Trading:   PAPER (dry run)")
    print(
        f"Vision:    {config.VLM_MODEL}" if config.USE_VISION else "Vision:    Disabled"
    )
    print(f"Cloud:     {'ENABLED' if config.CLOUD_SCANNER_ENABLED else 'DISABLED'}")
    print(f"Listener:  Port {config.LOCAL_LISTENER_PORT}")
    print(f"Kill:      OFF")
    print("=" * 60)

    app = VcaniTradeApp()

    def signal_handler(sig, frame):
        print("\nShutdown signal received...")
        app.cleanup()
        _release_single_instance_lock()
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
    finally:
        _release_single_instance_lock()


if __name__ == "__main__":
    main()
