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
if sys.platform == 'win32':
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import signal
import time
import random
import asyncio
import logging
import threading
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
from core.watchtower import WatchtowerScanner
from core.vision_engine import VisionCapture
from core.scanner import CloudScanner
from core.signal_dispatcher import SignalDispatcher
from core.browser_agent import BrowserAgent
from core.settings import settings_manager
from ui.dashboard import CommandCenter
from ui.signal_dialog import SignalApprovalDialog
from ui.ai_narrator import AINarratorOverlay

# Setup logging with UTF-8 encoding to prevent emoji crashes
import sys
import io

# Force UTF-8 encoding for stdout/stderr on Windows
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

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

# Ensure immediate flushing
import sys
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

        # UI — Command Center (main dashboard)
        self.cmd = CommandCenter()
        
        # AI Narrator Overlay (glassmorphic assistant)
        self.ai_narrator = AINarratorOverlay()
        self.ai_narrator.move(20, 20)  # Top-left corner
        self.ai_narrator.show()

        # Core
        self.trade_engine = TradeEngine()
        self.grader = Grader()
        
        # Browser Agent (Autonomous price checking via Playwright)
        self.browser_agent = None  # Will be started asynchronously when needed
        
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
        self.current_mode = "TEACHER"
        self.latest_signals = {}
        self.ticker_selector = config.CLOUD_TICKERS[0]  # Default ticker

        # Balance & P/L tracking
        self.balance = 10000.0  # Starting balance
        self.equity = 10000.0
        self.daily_pnl = 0.0
        self.total_pnl = 0.0
        self.peak_balance = 10000.0
        self.max_drawdown = 0.0
        self.trades_today = 0
        self.positions = []  # Live positions

        # Trading settings
        self.default_investment = 10.0
        self.take_profit_pct = 2.0
        self.stop_loss_pct = 1.0
        self.max_daily_loss = 500.0
        self.trade_ledger = []  # List of executed trades

        self._connect_signals()
        logger.info("VcaniTrade AI initialized (Hybrid Architecture)")
    
    async def _init_browser_agent(self):
        """Initialize browser agent asynchronously."""
        try:
            self.browser_agent = BrowserAgent(headless=True)
            await self.browser_agent.start()
            self.cmd.log("🌐 Browser agent launched - autonomous price checking ready")
            self.ai_narrator.add_activity("🌐", "Browser agent ready for autonomous tasks")
        except Exception as e:
            logger.error(f"Failed to start browser agent: {e}")
            self.cmd.log(f"⚠️ Browser agent failed: {e}")

    def _connect_signals(self):
        """Wire all backend threads to the CommandCenter UI."""
        # Command Center
        self.cmd.mode_changed.connect(self._on_mode_changed)
        self.cmd.kill_switch_triggered.connect(self._on_kill_switch)
        self.cmd.watchlist_updated.connect(self._on_watchlist_updated)
        self.cmd.settings_changed.connect(self._on_settings_changed)

        # Cloud Scanner → UI + Narrator
        self.cloud_scanner.signal_detected.connect(self._on_cloud_signal)
        self.cloud_scanner.scanner_error.connect(self._on_scanner_error)

        # Signal Listener → UI + Narrator
        self.signal_listener.signal_received.connect(self._on_signal_received)
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
        
        # Update AI Narrator with position status (only if there are positions)
        if self.positions:
            self.ai_narrator.notify_position_update(len(self.positions), self.daily_pnl)
        else:
            # No positions - set to idle/scanning
            self.ai_narrator.set_status("scanning", "No active positions")
    
    def _verify_price_with_browser(self, position: dict):
        """Use browser agent to verify price when yfinance fails."""
        import asyncio
        
        asset = position["asset"]
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

    def _close_position(self, position: dict, reason: str):
        """Close a position and update P&L."""
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

    def _on_market_data(self, market_data: MarketDataPoint):
        """Queue market data for Swarm analysis."""
        self.analysis_worker.add_to_queue(market_data)

    def _on_cloud_signal(self, signal_data: dict):
        """Handle signal detected by cloud scanner - shows approval dialog ONLY in TEACHER mode."""
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
        # - AUTONOMOUS: Execute immediately with saved settings (NEVER shows dialog)
        # - TEACHER: Show approval dialog to ask user
        if self.current_mode == "AUTONOMOUS":
            # AUTO-EXECUTE with saved settings - NO DIALOG!
            self.cmd.log(
                f'<span style="color:#3FB950;font-weight:bold">🤖 AUTONOMOUS</span>: '
                f'Executing with saved settings'
            )
            self.ai_narrator.add_activity(
                "⚡",
                f"Auto-executing {signal_data['action']} {signal_data['ticker']}"
            )
            self._execute_cloud_signal(signal_data)
        else:
            # TEACHER MODE - Show dialog asking for approval and amount
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

        # AUTO-EXECUTE: If confidence >= 0.80 AND action is BUY/SELL, execute immediately
        if confidence >= 0.80 and action in ["BUY", "SELL"] and entry_price > 0:
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
        """Execute a cloud-generated signal locally - opens position with TP/SL."""
        from core.models import LLMAnalysisOutput

        action = signal_data.get("action", "HOLD")
        ticker = signal_data.get("ticker", "UNKNOWN")
        
        # ===== PROP FIRM RULE CHECK (The "Professor" Says No If Rules Violated) =====
        if self.prop_engine:
            # Estimate potential loss (using stop loss)
            entry_price = signal_data.get("entry_price", 0)
            if entry_price > 0:
                sl_price = entry_price * (1 - self.stop_loss_pct / 100) if action == "BUY" else entry_price * (1 + self.stop_loss_pct / 100)
                potential_loss = abs(entry_price - sl_price) * (self.default_investment / entry_price)
            else:
                potential_loss = self.default_investment * 0.01  # 1% estimate

            can_trade, violations = self.prop_engine.check_before_trade(
                ticker, potential_loss
            )

            if not can_trade:
                self.cmd.log(f'<span style="color:#F85149;font-weight:bold">🛑 PROP FIRM BLOCKED</span>: {ticker}')
                for v in violations:
                    self.cmd.log(f'<span style="color:#F85149">   {v}</span>')
                self.ai_narrator.notify_error(f"Prop firm blocked: {ticker}")
                return
            else:
                self.cmd.log(f'<span style="color:#3FB950">✅ PROP FIRM COMPLIANT</span>: {ticker} - All rules OK')

        # Get investment details (from signal, settings, or default)
        entry_price = signal_data.get("entry_price", 0)
        
        # Check if signal already has investment details (from approval dialog)
        if "investment_amount" in signal_data:
            # User approved with specific amount/lots
            amount = signal_data["investment_amount"]
            quantity = signal_data.get("quantity", amount / entry_price if entry_price > 0 else 0)
            investment_mode = signal_data.get("investment_mode", "dollar")
        else:
            # AUTONOMOUS mode - use saved settings
            amount, quantity = self.settings.get_investment_for_trade(entry_price)
            investment_mode = self.settings.get("investment_mode", "dollar")

        # Calculate TP/SL prices
        if entry_price == 0:
            self.cmd.log(f"⚠️ No entry price for {ticker} - cannot execute trade")
            self.ai_narrator.notify_error(f"No entry price for {ticker}")
            return

        tp_price = entry_price * (1 + self.take_profit_pct / 100) if action == "BUY" else entry_price * (1 - self.take_profit_pct / 100)
        sl_price = entry_price * (1 - self.stop_loss_pct / 100) if action == "BUY" else entry_price * (1 + self.stop_loss_pct / 100)

        # Check max daily loss
        if self.daily_pnl <= -self.max_daily_loss:
            self.cmd.log(f"🛑 MAX DAILY LOSS REACHED (${self.daily_pnl:.2f} / -${self.max_daily_loss:.2f}) - Trading halted")
            self.ai_narrator.notify_error("Max daily loss reached")
            return

        # Create position record
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
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }

        # Add to positions
        self.positions.append(position)
        self.trades_today += 1

        # Update UI
        self.cmd.update_positions(self.positions)
        self.cmd.add_trade_log(
            ticker,
            action,
            amount,
            0,
            "Open"
        )

        self.cmd.log(
            f'<span style="color:#3FB950;font-weight:bold">✅ POSITION OPENED</span>: '
            f'{action} {ticker} @ ${entry_price:.2f} | '
            f'Amount: ${amount:.2f} | Qty: {quantity:.4f} | TP: ${tp_price:.2f} | SL: ${sl_price:.2f}'
        )
        
        # Log investment mode used
        if investment_mode == "lots":
            self.cmd.log(
                f'<span style="color:#8B949E">💡 Mode</span>: '
                f'{quantity:.0f} lots/units @ ${entry_price:.2f} each'
            )
        else:
            self.cmd.log(
                f'<span style="color:#8B949E">💡 Mode</span>: '
                f'${amount:.2f} dollar investment'
            )
        
        # Use browser agent to verify entry price (in background)
        if self.browser_agent and action in ["BUY", "SELL"]:
            self.cmd.log(f"🌐 Browser agent verifying {ticker} price...")
            self._verify_price_with_browser(position)

        # Update AI Narrator
        self.ai_narrator.notify_trade_executed(
            ticker,
            action,
            entry_price
        )

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
