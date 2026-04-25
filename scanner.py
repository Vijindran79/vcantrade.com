"""
VcaniTrade AI - Data Scout (Remote Scanner)
Monitors assets via MetaTrader 5 and triggers Swarm Consensus debates.
Dispatches high-confidence signals to local laptop via ngrok tunnel.

Architecture:
- Scan every 15 seconds
- Move > 0.2% -> Call OllamaSwarmConsensus
- Confidence > 0.7 -> POST to ngrok tunnel
"""

import os
import sys
import time
import json
import logging
import asyncio
import requests
import MetaTrader5 as mt5

# Add project root to path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
from core.brain_swarm import OllamaSwarmConsensus
from core.models import MarketDataPoint

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] DataScout: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("DataScout")

# Configuration
TICKERS = [
    "GC=F",  # Gold Futures (mapped to MT5 GC1!)
    "EURUSD=X",  # Euro/USD
    "GBPUSD=X",  # GBP/USD
    "BTC-USD",  # Bitcoin
    "ETH-USD",  # Ethereum
    "TSLA",  # Tesla
    "NVDA",  # Nvidia
    "AAPL",  # Apple
    "CL=F",  # Crude Oil
    "^GSPC",  # S&P 500 Index
]
TUNNEL_URL = "https://89ad-82-18-221-251.ngrok-free.app/signal"
SCAN_INTERVAL = 15  # seconds
VOLATILITY_THRESHOLD = 0.002  # 0.2%
CONFIDENCE_THRESHOLD = 0.7


def _canonical_market_ticker(ticker: str) -> str:
    """Map ticker aliases to MT5 broker symbols."""
    if hasattr(config, "MT5_SYMBOL_MAP") and ticker in config.MT5_SYMBOL_MAP:
        return config.MT5_SYMBOL_MAP[ticker]
    return ticker


def _mt5_timeframe(interval: str) -> int:
    """Map string interval to MT5 timeframe constant."""
    mapping = {
        "1m": mt5.TIMEFRAME_M1,
        "3m": mt5.TIMEFRAME_M3,
        "5m": mt5.TIMEFRAME_M5,
        "15m": mt5.TIMEFRAME_M15,
        "30m": mt5.TIMEFRAME_M30,
        "1h": mt5.TIMEFRAME_H1,
        "4h": mt5.TIMEFRAME_H4,
        "1d": mt5.TIMEFRAME_D1,
    }
    return mapping.get(interval, mt5.TIMEFRAME_M1)


class DataScout:
    def __init__(self):
        self.swarm = OllamaSwarmConsensus()
        self.active_debate = False
        self.last_prices = {}
        self._mt5_initialized = False
        self._ensure_mt5()

    def _ensure_mt5(self) -> bool:
        if self._mt5_initialized:
            return True
        try:
            if not mt5.initialize():
                err = mt5.last_error()
                logger.error("[MT5] initialize() failed: %s", err)
                return False
            self._mt5_initialized = True
            account = mt5.account_info()
            if account:
                logger.info(
                    "[MT5] Connected | Account: %s | Balance: %.2f %s",
                    account.login,
                    account.balance,
                    account.currency,
                )
            return True
        except Exception as e:
            logger.error("[MT5] Initialization error: %s", e)
            return False

    def get_gpu_usage(self):
        """Monitor GPU VRAM to prevent crashes."""
        try:
            import pynvml

            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            return info.used / info.total
        except:
            return 0.0

    def _fetch_latest_price(self, ticker: str) -> float:
        """Fetch latest close price from MT5."""
        if not self._ensure_mt5():
            return 0.0
        mt5_symbol = _canonical_market_ticker(ticker)
        if not mt5.symbol_select(mt5_symbol, True):
            logger.warning("Symbol %s not available in MT5 MarketWatch", mt5_symbol)
            return 0.0
        rates = mt5.copy_rates_from_pos(mt5_symbol, mt5.TIMEFRAME_M1, 0, 2)
        if rates is None or len(rates) == 0:
            return 0.0
        return float(rates[-1]["close"])

    async def run_debate(self, ticker, current_price, change):
        """Trigger the multi-agent swarm debate."""
        if self.active_debate:
            logger.warning(f"Debate already in progress. Skipping {ticker}.")
            return

        self.active_debate = True
        logger.info(
            f"[SUCCESS] VOLATILITY DETECTED ({change * 100:.2f}%): Starting Swarm for {ticker} @ {current_price}"
        )

        try:
            # Prepare market data for swarm
            market_data = MarketDataPoint(
                asset=ticker,
                price=current_price,
                price_change_1h=change * 100,  # Approximate for 1m move
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                source="CLOUD_SCOUT",
            )

            # Run the debate (async)
            analysis, transcript = await self.swarm.run(market_data)

            # Check confidence
            # Map ConfidenceLevel enum to float for comparison
            conf_map = {"LOW": 0.3, "MEDIUM": 0.5, "HIGH": 0.8, "VERY_HIGH": 0.95}
            confidence = conf_map.get(analysis.confidence.value, 0.5)

            if confidence >= CONFIDENCE_THRESHOLD and analysis.action.value in [
                "BUY",
                "SELL",
            ]:
                logger.info(
                    f"[FIRE] HIGH CONVICTION SIGNAL: {analysis.action.value} {ticker} ({confidence})"
                )
                self.dispatch_signal(
                    ticker, analysis.action.value, confidence, analysis.reason
                )
            else:
                logger.info(
                    f"[EMOJI] SWARM VERDICT: {analysis.action.value} {ticker} (Confidence: {confidence}) - No trade."
                )

        except Exception as e:
            logger.error(f"Error during swarm debate: {e}")
        finally:
            self.active_debate = False

    def dispatch_signal(self, ticker, action, confidence, reason):
        """Send signal to laptop via ngrok tunnel."""
        payload = {
            "symbol": ticker,
            "action": action,
            "confidence": confidence,
            "reason": reason,
            "timestamp": time.time(),
        }
        try:
            logger.info(f"[SAT] Dispatching signal to laptop: {ticker} {action}")
            response = requests.post(TUNNEL_URL, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info("[OK] Signal received by laptop.")
            else:
                logger.error(f"[FAIL] Laptop returned status {response.status_code}")
        except Exception as e:
            logger.error(f"[FAIL] Failed to dispatch signal: {e}")

    async def scan_loop(self):
        """Main market scanning loop."""
        logger.info(
            f"[SAT] Data Scout Online. Monitoring {len(TICKERS)} assets every {SCAN_INTERVAL}s."
        )
        logger.info(f"Target Tunnel: {TUNNEL_URL}")

        while True:
            try:
                # 1. Protection: Check GPU usage
                vram_usage = self.get_gpu_usage()
                if vram_usage > 0.90:
                    logger.warning(
                        f"[WARN] GPU VRAM CRITICAL ({vram_usage * 100:.1f}%). Throttling 30s..."
                    )
                    await asyncio.sleep(30)
                    continue

                # 2. Fetch prices from MT5
                for ticker in TICKERS:
                    try:
                        current_price = self._fetch_latest_price(ticker)
                        if current_price <= 0:
                            continue

                        if ticker not in self.last_prices:
                            self.last_prices[ticker] = current_price
                            continue

                        prev_price = self.last_prices[ticker]
                        change = (current_price - prev_price) / prev_price
                        self.last_prices[ticker] = current_price

                        # 3. Check Volatility Trigger
                        if abs(change) >= VOLATILITY_THRESHOLD:
                            # Start debate in background if not already running
                            asyncio.create_task(
                                self.run_debate(ticker, current_price, change)
                            )

                    except Exception as e:
                        logger.error(f"Error processing {ticker}: {e}")

            except Exception as e:
                logger.error(f"Main loop error: {e}")

            await asyncio.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    scout = DataScout()
    try:
        asyncio.run(scout.scan_loop())
    except KeyboardInterrupt:
        logger.info("Scanner stopped by user.")
