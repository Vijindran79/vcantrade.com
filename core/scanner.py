"""
VcaniTrade AI - Cloud Market Scanner
Monitors chosen tickers using MetaTrader 5 or yfinance data.
Detects technical signals (RSI Cross, Volume Spike, SMA Cross) and triggers Swarm Debate.

Architecture:
- Runs on Vast.ai server (headless) or local Windows machine
- Uses MetaTrader5 (mt5.copy_rates_from_pos) when EXECUTION_MODE == "MT5"
- Falls back to cached data when MT5 is unavailable (UI/Tradovate mode)
- Calculates technical indicators (RSI, SMA, Volume)
- Triggers Swarm Consensus when signal detected
- Dispatches high-confidence signals (>0.70) to Local Executor

NOW WITH SINGLE-ASSET LOCK RESPECT:
- Respects VcaniTradeEngine.asset_lock
- Skips scanning other tickers while locked
- Only processes the locked ticker
"""

import logging
import time
import asyncio
import threading
from numbers import Number
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
from ta import momentum, trend, volatility
import aiohttp

# Lazy import MetaTrader5 — only loaded when EXECUTION_MODE == "MT5"
mt5 = None

def _lazy_mt5():
    global mt5
    if mt5 is None:
        try:
            import MetaTrader5 as _mt5
            mt5 = _mt5
        except ImportError:
            mt5 = False  # Mark as unavailable
    return mt5

import config
from core.brain import GeminiBrain
from core.code_architect import CodeArchitect
from core.settings import settings_manager
from core.models import MarketDataPoint, SignalAction, ConfidenceLevel
from core.brain_swarm import OllamaSwarmConsensus as SwarmConsensus
from core.market_sessions import MarketSessionDetector, is_crypto_ticker, is_weekend_closed
from core.liquidity_engine import LiquidityEngine
from core.multi_timeframe_structure import MultiTimeframeStructureAnalyzer
from core.symbol_mapper import normalize_yfinance_symbol, translate_chart_symbol
from services.signal_dispatcher import AsyncSignalDispatcher

logger = logging.getLogger(__name__)


class TechnicalSignal:
    """Represents a detected technical signal."""
    
    def __init__(
        self, ticker: str, signal_type: str, strength: float, metadata: dict = None
    ):
        self.ticker = ticker
        self.signal_type = (
            signal_type  # "VOLUME_SPIKE", "RSI_OVERSOLD", "RSI_OVERBOUGHT", "SMA_CROSS"
        )
        self.strength = strength  # 0.0-1.0
        self.metadata = metadata or {}
        self.timestamp = datetime.utcnow()


class Scanner:
    """
    Cloud-based market scanner that monitors tickers using MetaTrader 5 data.
    Detects technical signals and triggers Swarm Debate when conditions met.
    
    NOW WITH SINGLE-ASSET LOCK RESPECT:
    - Only scans the locked ticker when lock is active
    - Skips all other tickers to prevent double-buys
    """
    
    def __init__(self):
        self.tickers = config.ACTIVE_WATCHLIST  # Dynamically updated from dashboard
        self.priority_scan_list = []
        self.consensus = SwarmConsensus()
        self.brain = GeminiBrain()
        self.code_architect = CodeArchitect()
        self.liquidity_engine = LiquidityEngine()
        self.recent_signals: Dict[str, datetime] = {}  # Legacy signal-type cooldown state
        self.last_signal_timestamps: Dict[str, datetime] = {}
        self.signal_cooldown = int(getattr(config, "SIGNAL_COOLDOWN_SECONDS", 300)) or 300
        self._lockdown_lock = threading.RLock()
        self.is_currently_holding = False
        self.active_locked_ticker = None
        self.status_callback = None
        self.market_data_cache: Dict[Tuple[str, str, str], pd.DataFrame] = {}
        self.last_close_cache: Dict[str, float] = {}
        
        # Reference to engine lock (set externally)
        self._engine_lock = None
        
        logger.info("[SCANNER] Initialized with single-asset lock respect")
    
    def set_engine_lock(self, lock):
        """Set reference to VcaniTradeEngine.asset_lock."""
        self._engine_lock = lock
        logger.info("[SCANNER] Engine lock reference set")
    
    def _is_locked_for_different_ticker(self, ticker: str) -> bool:
        """Check if engine is locked for a different ticker."""
        if self._engine_lock is None:
            return False
        return self._engine_lock.is_locked_for(ticker)
    
    def scan(self) -> List[TechnicalSignal]:
        """Main scanning loop with lock respect."""
        signals = []
        
        # Determine which tickers to scan
        if self._engine_lock and self._engine_lock.is_currently_holding:
            # Only scan the locked ticker
            tickers_to_scan = [self._engine_lock.active_locked_ticker]
            logger.debug("[SCANNER] Locked mode: scanning only %s", tickers_to_scan[0])
        else:
            # Scan all tickers in watchlist
            tickers_to_scan = self.tickers
        
        for ticker in tickers_to_scan:
            # Skip if locked for different ticker
            if self._is_locked_for_different_ticker(ticker):
                logger.debug("[SCANNER] Skipping %s — locked for different ticker", ticker)
                continue
            
            try:
                signal = self._scan_ticker(ticker)
                if signal:
                    signals.append(signal)
            except Exception as e:
                logger.warning("[SCANNER] Error scanning %s: %s", ticker, e)
        
        return signals
    
    def _scan_ticker(self, ticker: str) -> Optional[TechnicalSignal]:
        """Scan a single ticker for technical signals."""
        try:
            # Fetch market data
            df = self._fetch_market_data(ticker)
            if df is None or df.empty:
                return None
            
            # Calculate indicators
            close = df["Close"]
            if len(close) < 20:
                return None
            
            # RSI
            rsi = momentum.RSIIndicator(close=close, window=14).rsi()
            current_rsi = rsi.iloc[-1]
            
            # SMA
            sma_fast = trend.SMAIndicator(close=close, window=9).sma_indicator()
            sma_slow = trend.SMAIndicator(close=close, window=21).sma_indicator()
            
            # Volume
            volume = df["Volume"]
            avg_volume = volume.rolling(window=20).mean()
            current_volume = volume.iloc[-1]
            avg_volume_current = avg_volume.iloc[-1]
            
            # Detect signals
            signal_type = None
            strength = 0.0
            
            # RSI Overbought/Oversold
            if current_rsi > 85:
                signal_type = "RSI_OVERBOUGHT"
                strength = (current_rsi - 85) / 15.0  # Normalize
            elif current_rsi < 15:
                signal_type = "RSI_OVERSOLD"
                strength = (15 - current_rsi) / 15.0
            
            # SMA Cross
            elif sma_fast.iloc[-1] > sma_slow.iloc[-1] and sma_fast.iloc[-2] <= sma_slow.iloc[-2]:
                signal_type = "SMA_CROSS_BULL"
                strength = 0.8
            elif sma_fast.iloc[-1] < sma_slow.iloc[-1] and sma_fast.iloc[-2] >= sma_slow.iloc[-2]:
                signal_type = "SMA_CROSS_BEAR"
                strength = 0.8
            
            # Volume Spike
            elif current_volume > avg_volume_current * 3:
                signal_type = "VOLUME_SPIKE"
                strength = min(1.0, (current_volume / avg_volume_current) / 3.0)
            
            if signal_type:
                return TechnicalSignal(
                    ticker=ticker,
                    signal_type=signal_type,
                    strength=strength,
                    metadata={
                        "rsi": current_rsi,
                        "sma_fast": sma_fast.iloc[-1],
                        "sma_slow": sma_slow.iloc[-1],
                        "volume_ratio": current_volume / max(1, avg_volume_current),
                    }
                )
            
            return None
        
        except Exception as e:
            logger.warning("[SCANNER] Error in _scan_ticker for %s: %s", ticker, e)
            return None
    
    def _fetch_market_data(
        self,
        ticker: str,
        period: str = "1d",
        interval: str = "1m",
    ) -> Optional[pd.DataFrame]:
        """Fetch OHLCV market data from MetaTrader 5 using copy_rates_from_pos."""
        import concurrent.futures
        
        ticker_str = str(ticker).strip() if ticker is not None else ""
        allowed_symbol_chars = set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "abcdefghijklmnopqrstuvwxyz"
            "0123456789"
            "-_.=^:/!_"
        )
        if (
            not ticker_str
            or ticker_str.lower() in {"nan", "none", "null"}
            or isinstance(ticker, Number)
            or ticker_str.replace(".", "", 1).isdigit()
            or not any(ch.isalpha() for ch in ticker_str)
            or any(ch not in allowed_symbol_chars for ch in ticker_str)
        ):
            logger.debug("[SCANNER] Aborting fetch: '%s' is not a valid symbol string", ticker)
            return None
        
        ticker = ticker_str
        market_ticker = self._canonical_market_ticker(ticker_str)
        cache_key = (market_ticker, period, interval)
        
        max_retries = 3
        retry_delay = 2  # seconds
        
        # Map period string to count of bars to fetch
        bars_map = {
            "1d": 1440,   # 1 day of 1m bars
            "2d": 2880,
            "5d": 7200,
            "7d": 10080,
        }
        count = bars_map.get(period, 1440)
        
        # If interval is 3m/5m/15m etc, adjust count proportionally
        interval_minutes = 1
        if interval.endswith("m"):
            try:
                interval_minutes = int(interval[:-1])
            except ValueError:
                interval_minutes = 1
        elif interval.endswith("h"):
            try:
                interval_minutes = int(interval[:-1]) * 60
            except ValueError:
                interval_minutes = 60
        count = max(80, count // max(1, interval_minutes))
        
        mt5_tf = self._mt5_timeframe(interval)
        
        # Crypto fast path: MT5 does not carry crypto — use Yahoo Finance directly
        if is_crypto_ticker(ticker):
            yf_df = self._fetch_crypto_yfinance(ticker, market_ticker, period, interval, cache_key)
            if yf_df is not None and not yf_df.empty:
                return yf_df
            fallback = self._fallback_market_data(ticker, market_ticker, period, interval, cache_key)
            if fallback is not None:
                return fallback
            logger.warning("[WARN] Crypto data unavailable for %s - Skipping Cycle", ticker)
            return None
        
        # Fast path: MT5 unavailable (intentional in pure TradingView / browser mode)
        if mt5_tf is None:
            fallback = self._fallback_market_data(ticker, market_ticker, period, interval, cache_key)
            if fallback is not None:
                return fallback
            return None
        
        # Futures/Forex on weekends: markets are closed, skip MT5 noise entirely
        if self.session_detector.is_weekend() and is_weekend_closed(ticker):
            fallback = self._fallback_market_data(ticker, market_ticker, period, interval, cache_key)
            if fallback is not None:
                return fallback
            logger.info("[CLOCK] [WEEKEND SKIP] %s market closed - Skipping Cycle", ticker)
            return None
        
        for attempt in range(max_retries):
            try:
                if not self._ensure_mt5():
                    if attempt < 1:
                        time.sleep(retry_delay)
                        continue
                    fallback = self._fallback_market_data(ticker, market_ticker, period, interval, cache_key)
                    if fallback is not None:
                        return fallback
                    return None
                
                _mt5 = _lazy_mt5()
                selected_symbol = self._select_mt5_symbol(market_ticker)
                if not selected_symbol:
                    fallback = self._fallback_market_data(ticker, market_ticker, period, interval, cache_key)
                    if fallback is not None:
                        return fallback
                    return None
                
                def _fetch():
                    rates = _mt5.copy_rates_from_pos(selected_symbol, mt5_tf, 0, count)
                    return rates
                
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(_fetch)
                    rates = future.result(timeout=15)
                
                if rates is None or len(rates) == 0:
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
                    fallback = self._fallback_market_data(ticker, market_ticker, period, interval, cache_key)
                    if fallback is not None:
                        return fallback
                    return None
                
                df = pd.DataFrame(rates)
                df["time"] = pd.to_datetime(df["time"], unit="s")
                df = df.rename(columns={
                    "time": "Time",
                    "open": "Open",
                    "high": "High",
                    "low": "Low",
                    "close": "Close",
                    "tick_volume": "Volume",
                    "real_volume": "RealVolume",
                })
                df = df.set_index("Time")
                
                self.market_data_cache[cache_key] = df.copy()
                last_close = df["Close"].dropna().iloc[-1]
                self.last_close_cache[market_ticker] = float(last_close)
                
                return df
            
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                fallback = self._fallback_market_data(ticker, market_ticker, period, interval, cache_key)
                if fallback is not None:
                    return fallback
                return None
        
        return None
    
    def _canonical_market_ticker(self, ticker: str) -> str:
        """Convert ticker to canonical market ticker."""
        raw = str(ticker or "").strip()
        yf_map = getattr(config, "YFINANCE_SYMBOL_MAP", {})
        normalized = raw.upper()
        if normalized in yf_map:
            return yf_map[normalized]
        return normalized
    
    def _mt5_timeframe(self, interval: str):
        """Map interval string to MT5 timeframe constant."""
        if mt5 is None or mt5 is False:
            return None
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
        return mapping.get(interval)
    
    def _ensure_mt5(self):
        """Ensure MT5 is initialized."""
        _mt5 = _lazy_mt5()
        if _mt5 is None or _mt5 is False:
            return False
        if not _mt5.initialize():
            return False
        return True
    
    def _select_mt5_symbol(self, market_ticker: str) -> Optional[str]:
        """Select symbol in MT5 MarketWatch."""
        mt5_map = getattr(config, "MT5_SYMBOL_MAP", {})
        mt5_symbol = mt5_map.get(market_ticker, market_ticker)
        
        _mt5 = _lazy_mt5()
        if not _mt5.symbol_select(mt5_symbol, True):
            # Try candidates
            candidates = config.SYMBOL_BRIDGE_CANDIDATES.get(market_ticker, [])
            for candidate in candidates:
                if _mt5.symbol_select(candidate, True):
                    return candidate
            return None
        
        return mt5_symbol
    
    def _fallback_market_data(self, ticker, market_ticker, period, interval, cache_key):
        """Return cached bars if available."""
        cached = self.market_data_cache.get(cache_key)
        if cached is not None and not cached.empty:
            return cached.copy()
        return None
    
    def _fetch_crypto_yfinance(self, ticker, market_ticker, period, interval, cache_key):
        """Fetch crypto data from Yahoo Finance."""
        try:
            import yfinance as yf
            
            yf_symbol = normalize_yfinance_symbol(market_ticker)
            
            def _download():
                return yf.download(
                    yf_symbol,
                    period=period,
                    interval=interval,
                    progress=False,
                    threads=False,
                )
            
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(_download)
                raw_df = future.result(timeout=20)
            
            if raw_df is None or raw_df.empty:
                return None
            
            # Normalize columns
            if isinstance(raw_df.columns, pd.MultiIndex):
                raw_df.columns = raw_df.columns.get_level_values(0)
            
            raw_df = raw_df.rename(columns={
                "Open": "Open",
                "High": "High",
                "Low": "Low",
                "Close": "Close",
                "Volume": "Volume",
            })
            
            self.market_data_cache[cache_key] = raw_df.copy()
            return raw_df
            
        except Exception as e:
            logger.warning("[CRYPTO] Yahoo Finance fetch failed for %s: %s", ticker, e)
            return None


# Legacy alias
CloudScannerThread = Scanner
