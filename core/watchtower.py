"""
VcaniTrade AI - Watchtower Scanner (Global Scout)

Lightweight background daemon that continuously scans a predefined watchlist
across multiple markets. Detects volume/volatility anomalies and triggers
the Swarm for full debate when thresholds are breached.

Rate-limit strategy:
- Forex pairs: polled every 30s via yfinance (free, no key needed)
- Crypto: polled every 15s via ccxt public endpoints (no key needed)
- Assets are staggered [DASH] only 1-2 requests per second at peak
- Baseline volumes computed with exponential moving average (EMA)
  so we adapt to session changes (London open, NY open, etc.)
"""

import logging
import time
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

from PyQt6.QtCore import QThread, pyqtSignal

import config
from core.models import MarketDataPoint, WatchlistAlert

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Watchlist Configuration
# ---------------------------------------------------------------------------

# yfinance ticker symbols (Forex + Stocks + Gold)
YFINANCE_WATCHLIST = {
    "MNQ": "MNQ=F",   # Micro E-mini Nasdaq-100 futures
    "MES": "MES=F",   # Micro E-mini S&P 500 futures
    "XAUUSD": "GC=F",  # Gold futures
    "EURUSD": "EURUSD=X",  # EUR/USD
    "GBPUSD": "GBPUSD=X",  # GBP/USD
    "USDJPY": "JPY=X",  # USD/JPY (inverted)
    "DXY": "DX-Y.NYB",  # US Dollar Index
    "SPX": "^GSPC",  # S&P 500
    "AAPL": "AAPL",
    "TSLA": "TSLA",
}

# ccxt crypto symbols
CRYPTO_WATCHLIST = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
]

# Anomaly thresholds
VOLUME_SPIKE_RATIO = 3.0  # 300% of baseline triggers alert
VOLATILITY_BREAKOUT_PCT = 1.5  # 1.5% price move in scan window
MIN_DATA_POINTS = 5  # Need this many samples before alerting

# Scan intervals (seconds)
FOREX_SCAN_INTERVAL = 30
CRYPTO_SCAN_INTERVAL = 15


# ---------------------------------------------------------------------------
# Volume Baseline Tracker
# ---------------------------------------------------------------------------


class VolumeBaseline:
    """
    Exponential Moving Average (EMA) baseline for volume.
    Adapts to normal volume changes across trading sessions.
    """

    def __init__(self, alpha: float = 0.15):
        self.alpha = alpha  # EMA smoothing factor
        self.ema: Optional[float] = None
        self.samples: List[float] = []

    def update(self, volume: float):
        """Update EMA with new volume sample"""
        self.samples.append(volume)
        if self.ema is None:
            self.ema = volume
        else:
            self.ema = self.alpha * volume + (1 - self.alpha) * self.ema

    @property
    def ratio(self) -> float:
        """Current volume / EMA baseline. Returns 1.0 if no baseline yet."""
        if not self.samples or self.ema is None or self.ema == 0:
            return 1.0
        return self.samples[-1] / self.ema

    @property
    def ready(self) -> bool:
        return len(self.samples) >= MIN_DATA_POINTS


# ---------------------------------------------------------------------------
# Price History Tracker
# ---------------------------------------------------------------------------


class PriceHistory:
    """Rolling window of recent prices for volatility calculation"""

    def __init__(self, window: int = 20):
        self.window = window
        self.prices: List[float] = []

    def add(self, price: float):
        self.prices.append(price)
        if len(self.prices) > self.window:
            self.prices = self.prices[-self.window :]

    @property
    def volatility_pct(self) -> float:
        """Return price range as % of current price"""
        if len(self.prices) < 2:
            return 0.0
        high = max(self.prices)
        low = min(self.prices)
        current = self.prices[-1]
        if current == 0:
            return 0.0
        return ((high - low) / current) * 100

    @property
    def change_pct(self) -> float:
        if len(self.prices) < 2:
            return 0.0
        return ((self.prices[-1] - self.prices[0]) / self.prices[0]) * 100

    @property
    def ready(self) -> bool:
        return len(self.prices) >= MIN_DATA_POINTS


# ---------------------------------------------------------------------------
# Watchtower Scanner
# ---------------------------------------------------------------------------


class WatchtowerScanner(QThread):
    """
    Background daemon that scans watchlist for anomalies.
    Emits WatchlistAlert when thresholds are breached.
    Also emits MarketDataPoint for assets that should go to Swarm.
    """

    alert_detected = pyqtSignal(object)  # WatchlistAlert
    market_data_ready = pyqtSignal(object)  # MarketDataPoint (for Swarm handoff)
    scan_status = pyqtSignal(str)  # Status text for UI

    def __init__(self):
        super().__init__()
        self.running = True
        self.volume_baselines: Dict[str, VolumeBaseline] = defaultdict(VolumeBaseline)
        self.price_histories: Dict[str, PriceHistory] = defaultdict(PriceHistory)
        self.alert_cooldowns: Dict[str, datetime] = {}
        self.alert_cooldown_seconds = 300  # 5 min between alerts for same asset

    def run(self):
        """Main scan loop"""
        logger.info("Watchtower Scanner started")
        self.scan_status.emit("Watchtower: Scanning watchlist...")

        while self.running:
            try:
                self._scan_cycle()
            except Exception as e:
                logger.error(f"Watchtower scan error: {e}")

            # Sleep before next cycle
            for _ in range(FOREX_SCAN_INTERVAL):
                if not self.running:
                    break
                time.sleep(1)

    def _scan_cycle(self):
        """Single pass through all watchlist assets"""
        cycle_start = time.time()

        # Scan Forex/Stocks via yfinance
        self._scan_yfinance()

        # Scan Crypto via ccxt
        self._scan_crypto()

        elapsed = time.time() - cycle_start
        self.scan_status.emit(f"Watchtower: Scan complete ({elapsed:.1f}s)")

    def _scan_yfinance(self):
        """Scan Forex and Stock watchlist via yfinance"""
        try:
            import yfinance as yf
        except ImportError:
            logger.debug("yfinance not installed [DASH] skipping Forex/Stock scan")
            return

        for asset_name, ticker_symbol in YFINANCE_WATCHLIST.items():
            if not self.running:
                return

            try:
                ticker = yf.Ticker(ticker_symbol)
                hist = ticker.history(period="1d", interval="1m")

                if hist.empty or len(hist) < 2:
                    continue

                latest = hist.iloc[-1]
                prev = hist.iloc[-2]

                price = latest["Close"]
                volume = latest["Volume"]
                prev_volume = prev["Volume"]

                # Update trackers
                self.volume_baselines[asset_name].update(volume)
                self.price_histories[asset_name].add(price)

                # Check for anomalies
                self._check_anomalies(asset_name, price, volume, prev_volume)

                # Small delay to respect yfinance rate limits
                time.sleep(0.5)

            except Exception as e:
                logger.debug(f"yfinance scan failed for {asset_name}: {e}")
                continue

    def _scan_crypto(self):
        """Scan crypto watchlist via ccxt"""
        try:
            import ccxt
        except ImportError:
            logger.debug("ccxt not installed [DASH] skipping crypto scan")
            return

        try:
            exchange = ccxt.binance(
                {
                    "enableRateLimit": True,
                    "options": {"recvWindow": 5000},
                }
            )
        except Exception as e:
            logger.error(f"ccxt exchange init failed: {e}")
            return

        for symbol in CRYPTO_WATCHLIST:
            if not self.running:
                return

            try:
                ticker = exchange.fetch_ticker(symbol)

                asset_name = symbol.replace("/", "").replace("USDT", "USD")
                price = ticker["last"]
                volume = ticker["quoteVolume"] or ticker["baseVolume"] or 0

                # Update trackers
                self.volume_baselines[asset_name].update(volume)
                self.price_histories[asset_name].add(price)

                # Check anomalies (crypto has no prev_volume from ticker,
                # so we use volume ratio against baseline only)
                baseline = self.volume_baselines[asset_name]
                if baseline.ready:
                    vol_ratio = baseline.ratio
                    if vol_ratio >= VOLUME_SPIKE_RATIO:
                        alert = WatchlistAlert(
                            asset=asset_name,
                            alert_type="VOLUME_SPIKE",
                            severity="HIGH" if vol_ratio >= 5.0 else "MEDIUM",
                            current_price=price,
                            volume_ratio=vol_ratio,
                            price_change_pct=self.price_histories[
                                asset_name
                            ].change_pct,
                            reason=f"Volume spike {vol_ratio:.1f}x baseline on {asset_name}",
                        )
                        self._emit_alert(alert)

                time.sleep(0.3)  # Respect ccxt rate limit

            except Exception as e:
                logger.debug(f"ccxt scan failed for {symbol}: {e}")
                continue

    def _check_anomalies(
        self,
        asset: str,
        price: float,
        volume: float,
        prev_volume: float,
    ):
        """Check if current data breaches anomaly thresholds"""
        baseline = self.volume_baselines[asset]
        history = self.price_histories[asset]

        if not baseline.ready or not history.ready:
            return

        vol_ratio = baseline.ratio
        volatility = history.volatility_pct
        change_pct = history.change_pct

        # Volume spike detection
        if vol_ratio >= VOLUME_SPIKE_RATIO:
            severity = (
                "CRITICAL"
                if vol_ratio >= 5.0
                else "HIGH"
                if vol_ratio >= 4.0
                else "MEDIUM"
            )
            alert = WatchlistAlert(
                asset=asset,
                alert_type="VOLUME_SPIKE",
                severity=severity,
                current_price=price,
                volume_ratio=vol_ratio,
                price_change_pct=change_pct,
                reason=f"Volume {vol_ratio:.1f}x baseline. Price: {price:.5f}. Change: {change_pct:.2f}%",
            )
            self._emit_alert(alert)

        # Volatility breakout detection
        elif volatility >= VOLATILITY_BREAKOUT_PCT:
            alert = WatchlistAlert(
                asset=asset,
                alert_type="VOLATILITY_BREAKOUT",
                severity="HIGH" if volatility >= 2.5 else "MEDIUM",
                current_price=price,
                volume_ratio=vol_ratio,
                price_change_pct=change_pct,
                reason=f"Volatility breakout: {volatility:.2f}% range. Price: {price:.5f}",
            )
            self._emit_alert(alert)

    def _emit_alert(self, alert: WatchlistAlert):
        """Emit alert with cooldown protection.
        VOLUME SPIKE FILTER: SPX alerts are informational only - never trigger trades.
        """
        # Check cooldown
        last_alert = self.alert_cooldowns.get(alert.asset)
        if last_alert:
            elapsed = (datetime.utcnow() - last_alert).total_seconds()
            if elapsed < self.alert_cooldown_seconds:
                return  # Still in cooldown

        self.alert_cooldowns[alert.asset] = datetime.utcnow()
        self.alert_detected.emit(alert)
        logger.warning(
            f"WATCHTOWER ALERT: [{alert.severity}] {alert.alert_type} on "
            f"{alert.asset} [DASH] {alert.reason}"
        )

        # VOLUME SPIKE FILTER: SPX is informational only - NEVER trigger trades
        if alert.asset.upper() in ["SPX", "^GSPC", "S&P 500"]:
            logger.info("[WATCHTOWER] SPX alert is informational only - not triggering trade")
            return

        # Build MarketDataPoint for Swarm handoff (non-SPX only)
        market_data = MarketDataPoint(
            asset=alert.asset,
            price=alert.current_price,
            volume=alert.volume_ratio * 50000,  # Estimated
            price_change_1h=alert.price_change_pct,
            price_change_24h=alert.price_change_pct * 3,
        )
        self.market_data_ready.emit(market_data)

    def stop(self):
        """Stop the scanner"""
        self.running = False
        logger.info("Watchtower Scanner stopping")
