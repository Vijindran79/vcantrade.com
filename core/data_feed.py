"""
VcanTrade AI - Unified Market Data Feed
=========================================
Smart hybrid: MetaTrader 5 (primary, real-time) + Yahoo Finance (fallback, always works).
Automatically picks the best available source. Caches successful fetches.
"""
import logging
import time
import threading
from typing import Optional, List, Dict
from datetime import datetime
from pathlib import Path
import json

logger = logging.getLogger(__name__)

DATA_FEED_CACHE = Path("data_feed_cache.json")
CACHE_TTL_SECONDS = 60  # 1 minute freshness window


class MarketDataFeed:
    """
    Unified data feed. Best-of-both-worlds:
    1. MetaTrader 5 (real-time tick data, zero delay, futures-accurate)
    2. Yahoo Finance (free, no install, works anywhere, ~15min delay for some)
    3. Cache (last successful fetch, survives network blips)

    Auto-selects the best available source. Falls back gracefully.
    """

    def __init__(self, prefer_mt5: bool = True, cache_ttl: int = CACHE_TTL_SECONDS):
        self.prefer_mt5 = prefer_mt5
        self.cache_ttl = cache_ttl
        self._cache: Dict[str, Dict] = {}
        self._lock = threading.RLock()
        self._mt5_available = None
        self._last_mt5_check = 0
        self._last_yf_call = 0
        self._yf_min_interval = 2.0  # Rate limit: max 1 call per 2s
        self._stats = {"mt5_hits": 0, "yf_hits": 0, "cache_hits": 0, "errors": 0}
        self._load_cache()

    # ------------------------------------------------------------------
    # Source availability
    # ------------------------------------------------------------------
    def is_mt5_available(self) -> bool:
        """Check if MetaTrader 5 is available (cached for 30s)."""
        now = time.time()
        if self._mt5_available is not None and (now - self._last_mt5_check) < 30:
            return self._mt5_available
        try:
            import MetaTrader5 as mt5
            if not mt5.initialize():
                self._mt5_available = False
            else:
                self._mt5_available = True
                mt5.shutdown()
        except Exception:
            self._mt5_available = False
        self._last_mt5_check = now
        return self._mt5_available

    def is_yfinance_available(self) -> bool:
        """yfinance is always available if installed."""
        try:
            import yfinance  # noqa
            return True
        except ImportError:
            return False

    # ------------------------------------------------------------------
    # Symbol mapping
    # ------------------------------------------------------------------
    @staticmethod
    def _to_mt5_symbol(ticker: str) -> str:
        """Map internal ticker to MT5 broker symbol."""
        m = {
            "MNQ1!": "MNQ", "MNQ": "MNQ", "NQM6": "MNQ",
            "MES1!": "MES", "MES": "MES", "ESM6": "MES",
            "MCL1!": "MCL", "MCL": "MCL", "MCLM6": "MCL",
            "MGC1!": "MGC", "MGC": "MGC", "MGC=F": "MGC", "GC=F": "GC", "GC": "GC",
            "XAUUSD": "XAUUSD", "XAU": "XAUUSD", "GOLD": "Gold_SB",
            "NQ=F": "NQ", "ES=F": "ES", "CL=F": "CL", "GC=F": "GC",
            "NQ": "NQ", "ES": "ES", "CL": "CL", "GC": "GC",
        }
        return m.get(ticker, ticker)

    @staticmethod
    def _to_yfinance_symbol(ticker: str) -> str:
        """Map internal ticker to Yahoo Finance symbol."""
        m = {
            "MNQ1!": "NQ=F", "MNQ": "NQ=F", "NQM6": "NQ=F",
            "MES1!": "ES=F", "MES": "ES=F", "ESM6": "ES=F",
            "MCL1!": "CL=F", "MCL": "CL=F", "MCLM6": "CL=F",
            "MGC1!": "GC=F", "MGC": "GC=F", "MGC=F": "GC=F",
            "GC=F": "GC=F", "GC": "GC=F", "XAUUSD": "GC=F", "XAU": "GC=F", "GOLD": "GC=F",
            "NQ=F": "NQ=F", "ES=F": "ES=F", "CL=F": "CL=F", "GC=F": "GC=F",
            "NQ": "NQ=F", "ES": "ES=F", "CL": "CL=F", "GC": "GC=F",
            "BTC-USD": "BTC-USD", "ETH-USD": "ETH-USD",
        }
        return m.get(ticker, ticker)

    # ------------------------------------------------------------------
    # Fetchers
    # ------------------------------------------------------------------
    def _fetch_mt5(self, ticker: str, count: int = 200) -> Optional[List[Dict]]:
        """Fetch OHLCV bars from MetaTrader 5. Real-time, zero delay."""
        if not self.is_mt5_available():
            return None
        try:
            import MetaTrader5 as mt5
            if not mt5.initialize():
                return None
            symbol = self._to_mt5_symbol(ticker)
            # Try M1 timeframe
            rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, count)
            mt5.shutdown()
            if rates is None or len(rates) == 0:
                return None
            bars = []
            for r in rates:
                bars.append({
                    "timestamp": datetime.fromtimestamp(r["time"]).isoformat(),
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": int(r["tick_volume"]),
                    "source": "MT5",
                })
            return bars
        except Exception as e:
            logger.debug("[FEED] MT5 fetch failed for %s: %s", ticker, e)
            self._stats["errors"] += 1
            return None

    def _fetch_yfinance(self, ticker: str, count: int = 200) -> Optional[List[Dict]]:
        """Fetch OHLCV bars from Yahoo Finance. Free, always works."""
        # Rate limit protection
        elapsed = time.time() - self._last_yf_call
        if elapsed < self._yf_min_interval:
            time.sleep(self._yf_min_interval - elapsed)
        self._last_yf_call = time.time()

        try:
            import yfinance as yf
            import pandas as pd
            yf_sym = self._to_yfinance_symbol(ticker)

            # Use 5d period at 1m interval for recent intraday data
            df = yf.download(
                yf_sym, period="5d", interval="1m",
                progress=False, threads=False, auto_adjust=True,
            )
            if df is None or df.empty:
                # Fallback to daily
                df = yf.download(
                    yf_sym, period="1mo", interval="1d",
                    progress=False, threads=False, auto_adjust=True,
                )
            if df is None or df.empty:
                return None
            # Handle MultiIndex columns from yfinance
            if hasattr(df.columns, 'levels'):
                df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            bars = []
            for idx, row in df.tail(count).iterrows():
                bars.append({
                    "timestamp": idx.isoformat() if hasattr(idx, "isoformat") else str(idx),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": int(row.get("Volume", 0) or 0),
                    "source": "YFINANCE",
                })
            return bars
        except Exception as e:
            logger.debug("[FEED] YFinance fetch failed for %s: %s", ticker, e)
            self._stats["errors"] += 1
            return None

    # ------------------------------------------------------------------
    # Smart fetch with automatic fallback
    # ------------------------------------------------------------------
    def get_bars_multi(self, ticker: str, interval: str = "1m",
                       count: int = 100) -> Optional[List[Dict]]:
        """
        Get OHLCV bars for a ticker at a SPECIFIC timeframe interval.
        Supported intervals: 1m, 5m, 15m, 1h, 1d
        Used for multi-timeframe analysis (confluence engine).
        """
        cache_key = f"{ticker}_{interval}_{count}"
        now = time.time()

        # Check cache (shorter TTL for higher TFs is fine)
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and (now - cached["timestamp"]) < self.cache_ttl:
                self._stats["cache_hits"] += 1
                return cached["bars"]

        bars = None
        source_used = None

        # Try Yahoo Finance (best for multi-TF — MT5 only has M1, M5, M15, M30, H1)
        if self.is_yfinance_available():
            bars = self._fetch_yfinance_multi(ticker, interval, count)
            if bars:
                source_used = "YFINANCE"
                self._stats["yf_hits"] += 1

        # Try MT5 as fallback for supported timeframes
        if not bars and self.is_mt5_available():
            bars = self._fetch_mt5_multi(ticker, interval, count)
            if bars:
                source_used = "MT5"
                self._stats["mt5_hits"] += 1

        # Last resort: serve stale cache
        if not bars:
            with self._lock:
                cached = self._cache.get(cache_key)
                if cached:
                    logger.warning("[FEED] Serving stale %s cache for %s", interval, ticker)
                    return cached["bars"]

        # Update cache on success
        if bars:
            with self._lock:
                self._cache[cache_key] = {
                    "timestamp": now,
                    "bars": bars,
                    "source": source_used,
                }

        return bars

    def _fetch_yfinance_multi(self, ticker: str, interval: str, count: int) -> Optional[List[Dict]]:
        """Fetch OHLCV bars from Yahoo Finance at a specific interval."""
        # Rate limit protection
        elapsed = time.time() - self._last_yf_call
        if elapsed < self._yf_min_interval:
            time.sleep(self._yf_min_interval - elapsed)
        self._last_yf_call = time.time()

        # yfinance interval → period mapping (yfinance limits vary by interval)
        interval_config = {
            "1m":  ("5d",  "1m"),
            "5m":  ("1mo", "5m"),
            "15m": ("1mo", "15m"),
            "30m": ("1mo", "30m"),
            "1h":  ("3mo", "1h"),
            "1d":  ("2y",  "1d"),
        }
        cfg = interval_config.get(interval)
        if not cfg:
            return None
        period, yf_interval = cfg

        try:
            import yfinance as yf
            yf_sym = self._to_yfinance_symbol(ticker)
            df = yf.download(
                yf_sym, period=period, interval=yf_interval,
                progress=False, threads=False, auto_adjust=True,
            )
            if df is None or df.empty:
                return None
            # Flatten multi-index columns if present
            if hasattr(df.columns, 'get_level_values'):
                df.columns = df.columns.get_level_values(0)
            df = df.tail(count)
            bars = []
            for idx, row in df.iterrows():
                bars.append({
                    "timestamp": idx.isoformat() if hasattr(idx, "isoformat") else str(idx),
                    "open": float(row.get("Open", 0.0) or 0.0),
                    "high": float(row.get("High", 0.0) or 0.0),
                    "low": float(row.get("Low", 0.0) or 0.0),
                    "close": float(row.get("Close", 0.0) or 0.0),
                    "volume": int(row.get("Volume", 0.0) or 0.0),
                    "source": "YFINANCE",
                })
            return bars
        except Exception as e:
            logger.debug("[FEED] YF multi fetch failed for %s @ %s: %s", ticker, interval, e)
            self._stats["errors"] += 1
            return None

    def _fetch_mt5_multi(self, ticker: str, interval: str, count: int) -> Optional[List[Dict]]:
        """Fetch OHLCV bars from MT5 at a specific timeframe."""
        try:
            import MetaTrader5 as mt5
            if not mt5.initialize():
                return None
            symbol = self._to_mt5_symbol(ticker)
            tf_map = {
                "1m":  mt5.TIMEFRAME_M1,
                "5m":  mt5.TIMEFRAME_M5,
                "15m": mt5.TIMEFRAME_M15,
                "30m": mt5.TIMEFRAME_M30,
                "1h":  mt5.TIMEFRAME_H1,
                "1d":  mt5.TIMEFRAME_D1,
            }
            tf = tf_map.get(interval)
            if not tf:
                mt5.shutdown()
                return None
            rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
            mt5.shutdown()
            if rates is None or len(rates) == 0:
                return None
            bars = []
            for r in rates:
                bars.append({
                    "timestamp": datetime.fromtimestamp(r["time"]).isoformat(),
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": int(r["tick_volume"]),
                    "source": "MT5",
                })
            return bars
        except Exception as e:
            logger.debug("[FEED] MT5 multi fetch failed for %s @ %s: %s", ticker, interval, e)
            self._stats["errors"] += 1
            return None

    def get_bars(self, ticker: str, count: int = 200,
                 use_cache: bool = True) -> Optional[List[Dict]]:
        """
        Get OHLCV bars for a ticker. Best-of-both-worlds:

        1. If prefer_mt5 AND MT5 available → try MT5 first
        2. Otherwise or on failure → try Yahoo Finance
        3. On any failure → return cached data if fresh
        4. On cache miss → return None

        Returns: list of bar dicts or None
        """
        cache_key = f"{ticker}_{count}"
        now = time.time()

        # Check cache first
        if use_cache:
            with self._lock:
                cached = self._cache.get(cache_key)
                if cached and (now - cached["timestamp"]) < self.cache_ttl:
                    self._stats["cache_hits"] += 1
                    return cached["bars"]

        bars = None
        source_used = None

        # Try MT5 first (if preferred and available)
        if self.prefer_mt5 and self.is_mt5_available():
            bars = self._fetch_mt5(ticker, count)
            if bars:
                source_used = "MT5"
                self._stats["mt5_hits"] += 1

        # Fall back to Yahoo Finance
        if not bars and self.is_yfinance_available():
            bars = self._fetch_yfinance(ticker, count)
            if bars:
                source_used = "YFINANCE"
                self._stats["yf_hits"] += 1

        # Last resort: serve stale cache
        if not bars and use_cache:
            with self._lock:
                cached = self._cache.get(cache_key)
                if cached:
                    logger.warning("[FEED] Serving stale cache for %s (%ds old)",
                                   ticker, int(now - cached["timestamp"]))
                    return cached["bars"]

        # Update cache on success
        if bars:
            with self._lock:
                self._cache[cache_key] = {
                    "timestamp": now,
                    "bars": bars,
                    "source": source_used,
                }
            self._save_cache()
            logger.debug("[FEED] %s: %d bars from %s", ticker, len(bars), source_used)

        return bars

    def get_latest_price(self, ticker: str) -> Optional[float]:
        """Get the most recent price for a ticker."""
        bars = self.get_bars(ticker, count=5)
        if bars and len(bars) > 0:
            return float(bars[-1]["close"])
        return None

    def get_data_source(self, ticker: str) -> str:
        """Which source was last used for this ticker."""
        cache_key = f"{ticker}_200"
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached:
                return cached.get("source", "UNKNOWN")
        return "NONE"

    def get_stats(self) -> Dict:
        """Get feed usage statistics."""
        return {
            **self._stats,
            "mt5_available": self.is_mt5_available(),
            "yfinance_available": self.is_yfinance_available(),
            "cache_size": len(self._cache),
            "prefer_mt5": self.prefer_mt5,
        }

    def clear_cache(self):
        with self._lock:
            self._cache.clear()
        try:
            if DATA_FEED_CACHE.exists():
                DATA_FEED_CACHE.unlink()
        except Exception:
            pass

    def _save_cache(self):
        try:
            # Only persist tiny amount to avoid huge file
            data = {}
            for k, v in list(self._cache.items())[-20:]:
                data[k] = {"timestamp": v["timestamp"], "source": v["source"],
                           "bars": v["bars"][-50:]}  # Keep last 50 bars
            DATA_FEED_CACHE.write_text(json.dumps(data, default=str))
        except Exception as e:
            logger.debug("[FEED] Cache save error: %s", e)

    def _load_cache(self):
        try:
            if DATA_FEED_CACHE.exists():
                data = json.loads(DATA_FEED_CACHE.read_text())
                with self._lock:
                    self._cache = data
        except Exception as e:
            logger.debug("[FEED] Cache load error: %s", e)


# Global singleton
data_feed = MarketDataFeed(prefer_mt5=True)
