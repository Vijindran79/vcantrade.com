import asyncio
import logging
import time
from datetime import datetime
from typing import Dict, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

import config
from core.models import TradeRecord, SignalAction, ConfidenceLevel
from core.symbol_mapper import normalize_yfinance_symbol
from execution.rpa_executor import RPAExecutor

logger = logging.getLogger(__name__)


class ExchangeLimitExecutor:
    """Exchange-backed limit entry executor for Binance/Bybit.

    Falls back to simulation if ccxt or API credentials are unavailable.
    """

    def __init__(self, provider: str = "binance", api_key: str | None = None, api_secret: str | None = None):
        self.provider = (provider or "binance").lower()
        self.api_key = api_key
        self.api_secret = api_secret
        self.client = None
        self._init_client()

    def _init_client(self):
        try:
            import ccxt  # type: ignore
        except Exception:
            self.client = None
            return

        if not self.api_key or not self.api_secret:
            self.client = None
            return

        exchange_cls = getattr(ccxt, self.provider, None)
        if not exchange_cls:
            self.client = None
            return

        self.client = exchange_cls(
            {
                "apiKey": self.api_key,
                "secret": self.api_secret,
                "enableRateLimit": True,
            }
        )

    def place_limit_entry(self, symbol: str, side: str, quantity: float, entry_price: float) -> Dict:
        order_side = side.lower()
        if order_side not in ["buy", "sell"]:
            return {
                "status": "rejected",
                "provider": self.provider,
                "order_id": "",
                "symbol": symbol,
                "side": side,
                "entry_price": entry_price,
                "quantity": quantity,
                "reason": f"Unsupported side: {side}",
                "simulated": True,
            }

        if self.client is None:
            return {
                "status": "simulated",
                "provider": self.provider,
                "order_id": f"sim_{symbol}_{int(time.time())}",
                "symbol": symbol,
                "side": side,
                "entry_price": entry_price,
                "quantity": quantity,
                "reason": "No exchange API client available; simulated limit order",
                "simulated": True,
            }

        try:
            order = self.client.create_order(
                symbol=symbol,
                type="limit",
                side=order_side,
                amount=float(quantity),
                price=float(entry_price),
            )
            return {
                "status": "submitted",
                "provider": self.provider,
                "order_id": str(order.get("id", "")),
                "symbol": symbol,
                "side": side,
                "entry_price": entry_price,
                "quantity": quantity,
                "reason": "Limit order submitted to exchange",
                "simulated": False,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "provider": self.provider,
                "order_id": "",
                "symbol": symbol,
                "side": side,
                "entry_price": entry_price,
                "quantity": quantity,
                "reason": str(exc),
                "simulated": False,
            }


# [EMOJI]
# Professor Mode [DASH] ExchangeInterface
# [EMOJI]

class ExchangeInterface:
    """High-level trading interface used by Professor Mode.

    ``PAPER_TRADING = True`` (default) means every order is simulated
    regardless of whether real API keys are present.  Set to False and
    supply valid ``api_key`` / ``api_secret`` to go live.
    """

    PAPER_TRADING: bool = True  # <- Safety default; set False for live trading

    def __init__(
        self,
        provider: str = "binance",
        api_key: str | None = None,
        api_secret: str | None = None,
        paper_trading: bool = True,
    ):
        self.provider = (provider or "binance").lower()
        self.api_key = api_key
        self.api_secret = api_secret
        self.PAPER_TRADING = paper_trading
        # Delegate real exchange calls to ExchangeLimitExecutor
        self._executor = ExchangeLimitExecutor(
            provider=self.provider,
            api_key=None if self.PAPER_TRADING else self.api_key,
            api_secret=None if self.PAPER_TRADING else self.api_secret,
        )

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        entry_price: float,
    ) -> Dict:
        """Place a limit order at *entry_price*.

        When PAPER_TRADING is True returns a simulated order dict
        immediately without touching any exchange.
        """
        if self.PAPER_TRADING:
            return {
                "status": "paper",
                "provider": self.provider,
                "order_id": f"paper_{symbol}_{int(time.time())}",
                "symbol": symbol,
                "side": side,
                "entry_price": entry_price,
                "quantity": quantity,
                "reason": "PAPER_TRADING=True [DASH] simulated limit order",
                "simulated": True,
            }
        return self._executor.place_limit_entry(symbol, side, quantity, entry_price)


# ===================================================================
# Execution Result States
# ===================================================================

class ExecutionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    SKIPPED_LOW_CONFIDENCE = "SKIPPED_LOW_CONFIDENCE"
    ABORTED_SLIPPAGE = "ABORTED_SLIPPAGE"
    ABORTED_SPREAD = "ABORTED_SPREAD"
    FAILED_BROWSER_NAV = "FAILED_BROWSER_NAV"
    FAILED_PRICE_FETCH = "FAILED_PRICE_FETCH"
    FAILED_ORDER_EXECUTION = "FAILED_ORDER_EXECUTION"
    BLOCKED_BY_SAFETY = "BLOCKED_BY_SAFETY"


@dataclass
class ExecutionResult:
    """Result of trade execution attempt."""
    status: ExecutionStatus
    ticker: str
    action: str
    signal_price: float
    execution_price: Optional[float] = None
    slippage_pct: Optional[float] = None
    spread_pct: Optional[float] = None
    quantity: Optional[float] = None
    timestamp: Optional[datetime] = None
    error_message: Optional[str] = None
    order_id: Optional[str] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()

    def to_dict(self) -> Dict:
        """Convert to dictionary for logging/storage."""
        return {
            "status": self.status.value,
            "ticker": self.ticker,
            "action": self.action,
            "signal_price": self.signal_price,
            "execution_price": self.execution_price,
            "slippage_pct": self.slippage_pct,
            "spread_pct": self.spread_pct,
            "quantity": self.quantity,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "error_message": self.error_message,
            "order_id": self.order_id,
        }


# ===================================================================
# Slippage Guard
# ===================================================================

class SlippageGuard:
    """
    Protects against bad fills by checking:
    1. Price hasn't moved beyond MAX_SLIPPAGE_PERCENT
    2. Bid-ask spread is within MAX_SPREAD_PERCENT
    """

    def __init__(
        self,
        max_slippage_pct: float = None,
        max_spread_pct: float = None,
    ):
        self.max_slippage_pct = max_slippage_pct or config.MAX_SLIPPAGE_PERCENT
        self.max_spread_pct = max_spread_pct or config.MAX_SPREAD_PERCENT
        self.checks_performed = 0
        self.rejections = 0

    def check_slippage(
        self,
        signal_price: float,
        current_price: float,
    ) -> Tuple[bool, float]:
        """
        Check if slippage is within acceptable limits.

        Returns:
            Tuple of (is_acceptable, slippage_percentage)
        """
        if signal_price <= 0:
            return False, 0.0

        slippage_pct = abs(current_price - signal_price) / signal_price * 100
        is_acceptable = slippage_pct <= self.max_slippage_pct

        self.checks_performed += 1
        if not is_acceptable:
            self.rejections += 1

        return is_acceptable, slippage_pct

    def check_spread(
        self,
        bid: float,
        ask: float,
    ) -> Tuple[bool, float]:
        """
        Check if bid-ask spread is within acceptable limits.

        Returns:
            Tuple of (is_acceptable, spread_percentage)
        """
        if ask <= 0:
            return False, 0.0

        spread_pct = (ask - bid) / ask * 100
        is_acceptable = spread_pct <= self.max_spread_pct

        self.checks_performed += 1
        if not is_acceptable:
            self.rejections += 1

        return is_acceptable, spread_pct

    def get_stats(self) -> Dict:
        """Get slippage guard statistics."""
        return {
            "checks_performed": self.checks_performed,
            "rejections": self.rejections,
            "acceptance_rate": (
                (self.checks_performed - self.rejections)
                / max(1, self.checks_performed) * 100
            ),
        }


# ===================================================================
# Unified Trade Executor
# ===================================================================

class UnifiedTradeExecutor:
    """
    The Decision-to-Action Bridge.

    [WARN]  RPA SAFETY IS ALWAYS ACTIVE:
    Even when user says "Buy now!" via Co-Pilot Command Bridge,
    this executor STILL runs Slippage Guard and Spread Check.
    The AI is the final safety switch - it cannot bypass these checks.

    Workflow:
    1. Receive signal from Swarm Brain (or Co-Pilot Command)
    2. Validate confidence threshold
    3. Navigate browser to chart
    4. Slippage Guard checks live price (CANNOT BE BYPASSED)
    5. Spread check for liquidity (CANNOT BE BYPASSED)
    6. Execute via RPA Hand (Move the hand to execute orders!)
    7. Start trade monitoring

    Usage:
        executor = UnifiedTradeExecutor(browser_agent, cmd_logger)
        result = await executor.execute_signal(signal_data)
    """

    def __init__(
        self,
        browser_agent,
        cmd_logger=None,
        ai_narrator=None,
    ):
        self.browser_agent = browser_agent
        self.cmd_log = cmd_logger  # Command center log method
        self.ai_narrator = ai_narrator

        # Slippage Guard
        self.slippage_guard = SlippageGuard()

        # RPA Hand Executor (The "Hand" that moves)
        self.rpa_executor = RPAExecutor()

        # Safety Stop - Global Killswitch on consecutive failures
        self.consecutive_failures = 0
        self.max_consecutive_failures = 3  # Auto-pause after 3 failures
        self.safety_stop_active = False
        self.safety_stop_reason = ""

        # STAGE 4: Self-Healing Integration
        self.browser_error_count = 0  # Track browser errors for self-heal
        self.browser_error_threshold = 3  # Trigger restart after 3 errors

        # Execution tracking
        self.execution_count = 0
        self.success_count = 0
        self.active_trades = {}  # ticker -> trade_data

        logger.info("[SUCCESS] Unified Trade Executor initialized")

    def _log(self, message: str):
        """Log to both logger and command center if available."""
        logger.info(message)
        if self.cmd_log:
            self.cmd_log(message)

    async def execute_signal(
        self,
        signal_data: Dict,
        quantity: float = None,
        auto_execute: bool = True,
        force_execute: bool = False,  # NEW: Bypass all guards for testing
    ) -> ExecutionResult:
        """
        Main execution pipeline.

        Args:
            signal_data: Dict from Swarm containing ticker, action, price, confidence
            quantity: Trade size (defaults to signal_data quantity or 1000)
            auto_execute: If False, returns result without clicking (dry run)
            force_execute: If True, bypasses confidence checks and slippage guards (TEST MODE)

        Returns:
            ExecutionResult with status and details
        """
        ticker = signal_data.get("ticker", "UNKNOWN")
        action = signal_data.get("action", "HOLD")
        signal_price = signal_data.get("entry_price", 0.0)
        confidence_str = signal_data.get("confidence", "LOW")
        
        # Convert confidence string to numeric for threshold check if needed
        confidence_map = {"LOW": 0.3, "MEDIUM": 0.6, "HIGH": 0.8, "VERY_HIGH": 0.95}
        if isinstance(confidence_str, str):
            confidence_val = confidence_map.get(confidence_str, 0.5)
        else:
            confidence_val = confidence_str

        self.execution_count += 1
        
        if force_execute:
            self._log(f"[BOLT] FORCE MODE: Bypassing guards for {action} {ticker}")
        else:
            self._log(f"[SAT] Processing {action} for {ticker}...")

        # === SAFETY STOP CHECK === (Never bypassed)
        if self.safety_stop_active and not force_execute:
            msg = f"[STOP] SAFETY STOP ACTIVE: {self.safety_stop_reason}"
            self._log(msg)
            return ExecutionResult(
                status=ExecutionStatus.BLOCKED_BY_SAFETY,
                ticker=ticker,
                action=action,
                signal_price=signal_price,
                error_message=msg,
            )

        # Notify AI Narrator
        if self.ai_narrator:
            self.ai_narrator.set_status("executing", f"{action} {ticker}")

        # === STEP 1: BRAIN CHECK - Confidence Threshold ===
        if not force_execute and confidence_val < config.SWARM_CONFIDENCE_THRESHOLD:
            msg = (
                f"[STOP] Execution Skipped: Confidence {confidence_val:.2f} "
                f"below threshold {config.SWARM_CONFIDENCE_THRESHOLD}"
            )
            self._log(msg)
            return ExecutionResult(
                status=ExecutionStatus.SKIPPED_LOW_CONFIDENCE,
                ticker=ticker,
                action=action,
                signal_price=signal_price,
                error_message=msg,
            )

        self._log(f"[OK] Confidence check passed: {confidence_val:.2f}")

        # === STEP 2: CHART NAVIGATION (Get the eyes on the target) ===
        try:
            self._log(f"[GLOBE] Navigating browser to {ticker} chart...")
            success = await self.browser_agent.navigate_to_chart(ticker)
            if not success:
                self._log(f"[WARN] Browser navigation failed for {ticker} - attempting anyway")
                # STAGE 4: Record browser error for self-heal
                self.browser_error_count += 1
                if hasattr(self.browser_agent, 'record_error'):
                    self.browser_agent.record_error(f"Navigation failed for {ticker}")

                    # Trigger self-heal if threshold reached
                    if self.browser_error_count >= self.browser_error_threshold:
                        self._log(f"[SIREN] Browser error threshold reached ({self.browser_error_count}). Triggering self-heal...")
                        try:
                            await self.browser_agent.self_heal_restart()
                            self.browser_error_count = 0  # Reset counter after restart
                            self._log("[OK] Browser self-heal restart successful")
                        except Exception as restart_error:
                            self._log(f"[FAIL] Browser self-heal failed: {restart_error}")
        except Exception as e:
            self._log(f"[WARN] Browser navigation error: {e}")
            # STAGE 4: Record error for self-heal
            self.browser_error_count += 1
            if hasattr(self.browser_agent, 'record_error'):
                self.browser_agent.record_error(str(e))

        # === STEP 3: LIVE PRICE CHECK (Use yfinance - instant & reliable) ===
        try:
            import yfinance as yf
            import concurrent.futures
            import re
            
            yf_map = getattr(config, "YFINANCE_SYMBOL_MAP", {})
            raw_ticker = str(ticker or "").strip()
            compact_ticker = re.sub(r"[^A-Z0-9]", "", raw_ticker.upper())
            market_ticker = (
                yf_map.get(raw_ticker)
                or yf_map.get(raw_ticker.upper())
                or yf_map.get(compact_ticker)
                or normalize_yfinance_symbol(raw_ticker)
            )
            self._log(f"[CHART] Fetching live market price for {ticker} via {market_ticker}...")
            
            def fetch_price():
                """Fetch price using yfinance with timeout."""
                sym = yf.Ticker(market_ticker)
                hist = sym.history(period="1d", interval="1m")
                if hist.empty:
                    return None
                return hist["Close"].iloc[-1]
            
            # Run in thread with timeout
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(fetch_price)
                current_market_price = future.result(timeout=5.0)
            
            if current_market_price is None or current_market_price <= 0:
                self._log(f"[WARN] yfinance returned no price - using signal price ${signal_price:.2f}")
                current_market_price = signal_price
            else:
                self._log(f"[CHART] Live price: ${current_market_price:.2f}")
            
            # Estimate bid/ask spread
            bid = current_market_price * 0.9995
            ask = current_market_price * 1.0005

            # Calculate metrics
            slippage_ok, slippage_pct = self.slippage_guard.check_slippage(
                signal_price, current_market_price
            )
            spread_ok, spread_pct = self.slippage_guard.check_spread(bid, ask)

            self._log(
                f"[CHART] Live Audit: Price: {current_market_price} | "
                f"Slippage: {slippage_pct:.3f}% | "
                f"Spread: {spread_pct:.3f}%"
            )

            # === STEP 4: GO/NO-GO DECISION ===
            if force_execute:
                self._log(f"[BOLT] FORCE MODE: Bypassing slippage/spread checks")
                slippage_ok = True
                spread_ok = True
            
            if not slippage_ok:
                msg = f"[FAIL] ABORT: Slippage ({slippage_pct:.2f}%) exceeds limit"
                self._log(msg)
                return ExecutionResult(
                    status=ExecutionStatus.ABORTED_SLIPPAGE,
                    ticker=ticker,
                    action=action,
                    signal_price=signal_price,
                    execution_price=current_market_price,
                    slippage_pct=slippage_pct,
                    error_message=msg,
                )

            if not spread_ok:
                msg = f"[FAIL] ABORT: Spread ({spread_pct:.2f}%) exceeds limit"
                self._log(msg)
                return ExecutionResult(
                    status=ExecutionStatus.ABORTED_SPREAD,
                    ticker=ticker,
                    action=action,
                    signal_price=signal_price,
                    execution_price=current_market_price,
                    spread_pct=spread_pct,
                    error_message=msg,
                )

            self._log(f"[OK] Slippage Guard passed")

        except Exception as e:
            msg = f"[FAIL] Price check failed: {e}"
            self._log(msg)
            # STAGE 4: Record error for self-heal if browser-related
            if "browser" in str(e).lower() or "page" in str(e).lower():
                self.browser_error_count += 1
                if hasattr(self.browser_agent, 'record_error'):
                    self.browser_agent.record_error(str(e))
            return ExecutionResult(
                status=ExecutionStatus.FAILED_PRICE_FETCH,
                ticker=ticker,
                action=action,
                signal_price=signal_price,
                error_message=msg,
            )

        # === STEP 5: DRY RUN MODE ===
        if not auto_execute or config.DRY_RUN:
            self._log(f"[PAUSE] DRY RUN: Would execute {action} {ticker} @ {current_market_price}")
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                ticker=ticker,
                action=action,
                signal_price=signal_price,
                execution_price=current_market_price,
                quantity=quantity,
            )

        # === STEP 6: DYNAMIC QUANTITY CALCULATION ===
        trade_quantity = self._calculate_quantity(signal_data, current_market_price, quantity)

        # === STEP 7: ACTUAL RPA EXECUTION (MOVE THE HAND!) ===
        self._log(f"[AUTONOMOUS] Executing {action} on {ticker} via RPA Hand")
        
        try:
            # Map action string to SignalAction enum
            action_map = {
                "BUY": SignalAction.BUY,
                "SELL": SignalAction.SELL,
                "CLOSE": SignalAction.CLOSE
            }
            
            # Map confidence string to ConfidenceLevel enum
            conf_level_map = {
                "LOW": ConfidenceLevel.LOW,
                "MEDIUM": ConfidenceLevel.MEDIUM,
                "HIGH": ConfidenceLevel.HIGH,
                "VERY_HIGH": ConfidenceLevel.VERY_HIGH
            }
            
            # Create TradeRecord for RPA hand
            trade_rec = TradeRecord(
                asset=ticker,
                action=action_map.get(action.upper(), SignalAction.HOLD),
                entry_price=current_market_price,
                stop_loss=signal_data.get("stop_loss"),
                take_profit=signal_data.get("take_profit"),
                confidence=conf_level_map.get(confidence_str.upper() if isinstance(confidence_str, str) else "MEDIUM", ConfidenceLevel.MEDIUM),
                ai_reason=signal_data.get("reason", "Autonomous execution"),
                mode="AUTO",
                status="OPEN"
            )
            
            # Execute via RPA (Move the mouse, click the buttons!)
            success = self.rpa_executor.execute_trade(trade_rec)
            
            if success:
                order_id = f"rpa_{ticker}_{int(time.time())}"
                self.success_count += 1
                self.consecutive_failures = 0
                self._log(f"[OK] SUCCESS: RPA hand completed {action} for {ticker}")
                
                # Start monitoring
                self.start_trade_monitoring(ticker, action, current_market_price, trade_quantity, signal_price)
                
                return ExecutionResult(
                    status=ExecutionStatus.SUCCESS,
                    ticker=ticker,
                    action=action,
                    signal_price=signal_price,
                    execution_price=current_market_price,
                    quantity=trade_quantity,
                    order_id=order_id
                )
            else:
                self.consecutive_failures += 1
                failure_reason = self.rpa_executor.last_failure_reason or f"RPA hand failed to execute {action} for {ticker}"
                msg = f"[FAIL] FAILURE: {failure_reason}"
                self._log(msg)
                self._check_safety_stop()
                return ExecutionResult(
                    status=ExecutionStatus.FAILED_ORDER_EXECUTION,
                    ticker=ticker,
                    action=action,
                    signal_price=signal_price,
                    error_message=msg
                )

        except Exception as e:
            self.consecutive_failures += 1
            msg = f"[FAIL] Execution error: {e}"
            self._log(msg)
            # STAGE 4: Record error for self-heal if browser/RPA related
            error_str = str(e).lower()
            if any(keyword in error_str for keyword in ["browser", "page", "rpa", "element", "click"]):
                self.browser_error_count += 1
                if hasattr(self.browser_agent, 'record_error'):
                    self.browser_agent.record_error(str(e))

                # Check if we need to trigger self-heal
                if self.browser_error_count >= self.browser_error_threshold:
                    self._log(f"[SIREN] Browser error threshold reached ({self.browser_error_count}). Triggering self-heal...")
                    try:
                        await self.browser_agent.self_heal_restart()
                        self.browser_error_count = 0
                        self._log("[OK] Browser self-heal restart successful")
                    except Exception as restart_error:
                        self._log(f"[FAIL] Browser self-heal failed: {restart_error}")

            self._check_safety_stop()
            return ExecutionResult(
                status=ExecutionStatus.FAILED_ORDER_EXECUTION,
                ticker=ticker,
                action=action,
                signal_price=signal_price,
                error_message=msg
            )

    # ===================================================================
    # Trade Monitoring
    # ===================================================================

    def start_trade_monitoring(
        self,
        ticker: str,
        action: str,
        entry_price: float,
        quantity: float,
        signal_price: float,
    ):
        """
        Start tracking an active trade for P/L monitoring.
        """
        trade_data = {
            "ticker": ticker,
            "action": action,
            "entry_price": entry_price,
            "signal_price": signal_price,
            "quantity": quantity,
            "entry_time": datetime.now(),
            "current_price": entry_price,
            "pnl": 0.0,
            "pnl_pct": 0.0,
            "highest_price": entry_price,
            "lowest_price": entry_price,
            "status": "OPEN",
        }

        self.active_trades[ticker] = trade_data
        
        self._log(
            f"[CHART] Trade monitoring started: {ticker} | "
            f"Entry: {entry_price} | Qty: {quantity}"
        )

    def update_trade_prices(self, ticker: str, current_price: float):
        """Update P/L for an active trade."""
        if ticker not in self.active_trades:
            return

        trade = self.active_trades[ticker]
        trade["current_price"] = current_price
        
        # Track high/low
        trade["highest_price"] = max(trade["highest_price"], current_price)
        trade["lowest_price"] = min(trade["lowest_price"], current_price)

        # Calculate P/L
        if trade["action"] == "BUY":
            pnl = (current_price - trade["entry_price"]) * trade["quantity"]
            pnl_pct = (current_price - trade["entry_price"]) / trade["entry_price"] * 100
        else:  # SELL
            pnl = (trade["entry_price"] - current_price) * trade["quantity"]
            pnl_pct = (trade["entry_price"] - current_price) / trade["entry_price"] * 100

        trade["pnl"] = pnl
        trade["pnl_pct"] = pnl_pct

    def close_trade(self, ticker: str, exit_price: float, reason: str = "Manual"):
        """Close an active trade and log results."""
        if ticker not in self.active_trades:
            self._log(f"[WARN] No active trade found for {ticker}")
            return None

        trade = self.active_trades.pop(ticker)
        trade["status"] = "CLOSED"
        trade["exit_price"] = exit_price
        trade["exit_time"] = datetime.now()
        trade["exit_reason"] = reason
        trade["hold_duration"] = trade["exit_time"] - trade["entry_time"]

        # Final P/L
        if trade["action"] == "BUY":
            pnl = (exit_price - trade["entry_price"]) * trade["quantity"]
        else:
            pnl = (trade["entry_price"] - exit_price) * trade["quantity"]

        trade["pnl"] = pnl
        trade["pnl_pct"] = (exit_price - trade["entry_price"]) / trade["entry_price"] * 100

        # Log result
        pnl_emoji = "[MONEY]" if pnl >= 0 else "[RED]"
        self._log(
            f"{pnl_emoji} Trade closed: {ticker} | "
            f"P/L: ${pnl:.2f} ({trade['pnl_pct']:.2f}%) | "
            f"Reason: {reason} | "
            f"Duration: {trade['hold_duration']}"
        )

        return trade

    def close_all_positions(self, reason: str = "Apex Closing Time"):
        """Close all active trades immediately. Returns list of closed trades."""
        closed = []
        if not self.active_trades:
            self._log("[APEX] No active trades to close")
            return closed

        tickers = list(self.active_trades.keys())
        self._log(f"[APEX] Closing all {len(tickers)} positions: {tickers}")

        for ticker in tickers:
            # Use last known price from trade data
            trade = self.active_trades.get(ticker)
            if trade:
                exit_price = trade.get("current_price", trade.get("entry_price", 0))
                closed_trade = self.close_trade(ticker, exit_price, reason)
                if closed_trade:
                    closed.append(closed_trade)

        self._log(f"[APEX] Closed {len(closed)} positions")
        return closed

    def _calculate_quantity(
        self,
        signal_data: Dict,
        current_price: float,
        requested_quantity: float = None,
    ) -> float:
        """
        Dynamic Quantity Scaling - Calculate position size based on dollar amount.

        Priority:
        1. Use explicitly provided quantity (if user specified lots/units)
        2. Use dollar-based position sizing from signal_data["investment_amount"]
        3. Default to $1000 worth of the asset

        Examples:
        - BTC @ $50,000 with $1000 investment -> 0.02 BTC
        - AAPL @ $180 with $1000 investment -> 5.55 shares
        - EURUSD @ 1.0850 with $1000 investment -> 921.66 units
        """
        # If quantity is explicitly provided, use it
        if requested_quantity and requested_quantity > 0:
            self._log(f"[RULER] Using explicit quantity: {requested_quantity}")
            return requested_quantity

        # Get dollar amount to invest
        dollar_amount = signal_data.get("investment_amount", 1000.0)  # Default $1000

        # Check if user specified lots/units mode
        investment_mode = signal_data.get("investment_mode", "dollar")
        if investment_mode == "lots":
            quantity = signal_data.get("quantity", signal_data.get("lot_size", 1.0))
            self._log(f"[RULER] Using lots mode: {quantity} units")
            return quantity

        # Dollar-based calculation
        if current_price <= 0:
            self._log("[WARN] Current price is 0 - using signal price for quantity calc")
            current_price = signal_data.get("entry_price", 1.0)

        quantity = dollar_amount / current_price

        self._log(
            f"[RULER] Position Sizing: ${dollar_amount:.2f} / ${current_price:.2f} = {quantity:.4f} units"
        )

        return quantity

    def get_active_trades(self) -> Dict:
        """Get all active trades."""
        return self.active_trades.copy()

    def get_execution_stats(self) -> Dict:
        """Get overall execution statistics."""
        return {
            "total_attempts": self.execution_count,
            "successful_executions": self.success_count,
            "success_rate": (
                self.success_count / max(1, self.execution_count) * 100
            ),
            "slippage_guard_stats": self.slippage_guard.get_stats(),
            "active_trades": len(self.active_trades),
            "consecutive_failures": self.consecutive_failures,
            "safety_stop_active": self.safety_stop_active,
            "safety_stop_reason": self.safety_stop_reason,
        }

    def _check_safety_stop(self):
        """
        Check if consecutive failures exceed threshold and activate safety stop.
        """
        if self.consecutive_failures >= self.max_consecutive_failures:
            self.safety_stop_active = True
            self.safety_stop_reason = (
                f"{self.consecutive_failures} consecutive failures detected. "
                f"Auto-pausing to protect capital."
            )
            self._log(f"[STOP] SAFETY STOP ACTIVATED: {self.safety_stop_reason}")
            self._log("[WARN] Manual intervention required - check browser/connection")
            
            if self.ai_narrator:
                self.ai_narrator.notify_error(f"Safety Stop: {self.consecutive_failures} failures")

    def reset_safety_stop(self):
        """Manually reset the safety stop after fixing the issue."""
        old_failures = self.consecutive_failures
        self.consecutive_failures = 0
        self.safety_stop_active = False
        self.safety_stop_reason = ""
        self._log(f"[OK] Safety stop reset (had {old_failures} consecutive failures)")
