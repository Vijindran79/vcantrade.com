"""
VcanTrade AI - Cloud Market Scanner
Monitors 10 chosen counters using yfinance without a screen.
Detects technical signals (RSI Cross, Volume Spike, SMA Cross) and triggers Swarm Debate.

Architecture:
- Runs on Vast.ai server (headless)
- Uses yfinance for market data
- Calculates technical indicators (RSI, SMA, Volume)
- Triggers Swarm Consensus when signal detected
- Dispatches high-confidence signals (>0.70) to Local Executor
"""

import logging
import time
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests

import config
from core.brain import GeminiBrain
from core.code_architect import CodeArchitect
from core.models import MarketDataPoint, SignalAction, ConfidenceLevel
from core.swarm_consensus import OllamaSwarmConsensus as SwarmConsensus
from core.market_sessions import MarketSessionDetector

logger = logging.getLogger(__name__)


class TechnicalSignal:
    """Represents a detected technical signal."""
    
    def __init__(self, ticker: str, signal_type: str, strength: float, metadata: dict = None):
        self.ticker = ticker
        self.signal_type = signal_type  # "VOLUME_SPIKE", "RSI_OVERSOLD", "RSI_OVERBOUGHT", "SMA_CROSS"
        self.strength = strength  # 0.0-1.0
        self.metadata = metadata or {}
        self.timestamp = datetime.utcnow()


class CloudScanner:
    """
    Cloud-based market scanner that monitors 10 tickers using yfinance.
    Detects technical signals and triggers Swarm Debate when conditions met.
    
    NOW WITH MARKET SESSION AWARENESS:
    - Auto-filters tickers based on day of week (Sunday = Crypto only)
    - Detects active trading sessions (Asian/London/US)
    - Adjusts scanning based on market hours
    """

    def __init__(self):
        self.tickers = config.CLOUD_TICKERS  # Dynamically updated from dashboard
        self.priority_scan_list = []
        self.consensus = SwarmConsensus()
        self.brain = GeminiBrain()
        self.code_architect = CodeArchitect()
        self.recent_signals: Dict[str, datetime] = {}  # Prevent duplicate signals
        self.signal_cooldown = 300  # 5 minutes between signals for same ticker
        self.status_callback = None
        self.market_data_cache: Dict[Tuple[str, str, str], pd.DataFrame] = {}
        self.last_close_cache: Dict[str, float] = {}
        
        # Market Session Awareness
        self.session_detector = MarketSessionDetector()
        
        logger.info("☁️ Cloud Scanner initialized with Market Session Awareness")

    def set_runtime_context(self, mode: str, dashboard_tickers: Optional[List[str]] = None):
        """Keep session awareness aligned with the operator's live dashboard state."""
        self.session_detector.set_runtime_context(mode, dashboard_tickers)

    def _get_active_scan_list(self) -> List[str]:
        """Return sniper-priority list when configured; otherwise session-filtered tickers."""
        if self.priority_scan_list:
            return list(self.priority_scan_list)
        return [ticker for ticker in self.tickers if str(ticker).strip()]

    def _emit_status(self, ticker: str, status: str):
        """Emit optional per-ticker status updates for dashboard/mirror feedback."""
        if self.status_callback:
            try:
                self.status_callback(ticker, status)
            except Exception as e:
                logger.debug(f"Status callback failed for {ticker}/{status}: {e}")

    def get_scan_interval(self) -> float:
        """Scale scan cadence based on active watchlist size."""
        count = max(0, len(self._get_active_scan_list()))
        if count <= 0:
            return 30.0
        if count <= 2:
            return 2.0
        if count >= 10:
            return 30.0
        return round(2.0 + ((count - 2) * (28.0 / 8.0)), 1)
        
    async def scan_all_tickers(self) -> List[TechnicalSignal]:
        """Scan all tickers and return detected signals."""
        signals = []

        # Apply sniper priority list first; otherwise normal session filtering.
        active_tickers = self._get_active_scan_list()
        session_context = self.session_detector.get_session_context()
        
        # Log session info
        if self.priority_scan_list:
            logger.info(
                f"🎯 [SNIPER MODE] Priority list active. "
                f"Scanning only: {', '.join(active_tickers)}"
            )
        elif session_context.get("dashboard_override_active"):
            logger.info(
                "🕐 [OVERRIDE MODE] Weekend silence bypassed. Scanning dashboard tickers: %s",
                ", ".join(active_tickers),
            )
        elif self.session_detector.is_weekend():
            logger.info(
                f"🕐 [WEEKEND MODE] Markets closed. "
                f"Scanning {len(active_tickers)} crypto tickers only."
            )
        else:
            logger.debug(
                f"🕐 [SESSION] {session_context['primary_session']} | "
                f"Scanning {len(active_tickers)} tickers"
            )

        for ticker in active_tickers:
            radar_symbol = ticker.replace("=F", "")
            logger.info(f"📡 Radar sweep: Scanning {radar_symbol}")
            self._emit_status(ticker, "scanning")
            try:
                detected = await self._scan_single_ticker(ticker)
                signals.extend(detected)
            except Exception as e:
                logger.error(f"Failed to scan {ticker}: {e}")

        return signals
    
    async def _scan_single_ticker(self, ticker: str) -> List[TechnicalSignal]:
        """Scan a single ticker for technical signals."""
        signals = []
        
        # Fetch 1-minute data for last day
        df = await self._fetch_market_data(ticker)
        if df is None or df.empty:
            return signals
        
        # Calculate technical indicators
        df = self._calculate_indicators(df)
        liquidity_zone = self._detect_liquidity_zone(df, ticker)
        brain_package = self._build_brain_package(df, liquidity_zone)
        if liquidity_zone:
            self._emit_status(ticker, "analyzing_liquidity")

        liquidity_signal = self._detect_liquidity_reversal(df, ticker, liquidity_zone)
        if liquidity_signal:
            liquidity_signal.metadata.update(brain_package)
            liquidity_signal.metadata["liquidity_zone"] = liquidity_zone
            signals.append(liquidity_signal)
        
        # Detect signals
        volume_spike = self._detect_volume_spike(df, ticker)
        if volume_spike:
            volume_spike.metadata.update(brain_package)
            volume_spike.metadata["liquidity_zone"] = liquidity_zone
            signals.append(volume_spike)
        
        rsi_signal = self._detect_rsi_signal(df, ticker)
        if rsi_signal:
            rsi_signal.metadata.update(brain_package)
            rsi_signal.metadata["liquidity_zone"] = liquidity_zone
            signals.append(rsi_signal)
        
        sma_signal = self._detect_sma_cross(df, ticker)
        if sma_signal:
            sma_signal.metadata.update(brain_package)
            sma_signal.metadata["liquidity_zone"] = liquidity_zone
            signals.append(sma_signal)
        
        return signals

    def _detect_liquidity_zone(self, df: pd.DataFrame, ticker: str) -> Optional[dict]:
        """Find nearest equal highs or swing lows from the last 50 candles."""
        if df is None or df.empty or len(df) < 10:
            return None

        recent = df.tail(50).copy()
        current_price = recent["Close"].iloc[-1]
        tolerance = max(abs(current_price) * 0.001, 0.01)
        candidates = []

        highs = recent["High"].tolist()
        lows = recent["Low"].tolist()
        for index in range(1, len(recent) - 1):
            high = highs[index]
            low = lows[index]
            if pd.isna(high) or pd.isna(low):
                continue

            if high >= highs[index - 1] and high >= highs[index + 1]:
                candidates.append(("equal_highs", index, float(high)))
            if low <= lows[index - 1] and low <= lows[index + 1]:
                candidates.append(("swing_lows", index, float(low)))

        best_zone = None
        for zone_type, index, level in candidates:
            for zone_type_2, index_2, level_2 in candidates:
                if zone_type != zone_type_2 or index == index_2:
                    continue
                if abs(level - level_2) > tolerance:
                    continue
                zone_level = round((level + level_2) / 2.0, 4)
                distance = abs(zone_level - current_price)
                zone = {
                    "type": zone_type,
                    "level": zone_level,
                    "start_index": min(index, index_2),
                    "end_index": max(index, index_2),
                    "current_price": float(current_price),
                    "label": "LIQUIDITY TARGET",
                    "distance": float(distance),
                }
                if best_zone is None or zone["distance"] < best_zone["distance"]:
                    best_zone = zone

        if best_zone:
            logger.info(
                "💧 Liquidity zone detected for %s: %s @ %.4f",
                ticker,
                best_zone["type"],
                best_zone["level"],
            )
        return best_zone

    def _format_liquidity_zone_label(self, liquidity_zone: Optional[dict]) -> str:
        """Build a compact label for prompts, logs, and AI context."""
        if not liquidity_zone:
            return "N/A"

        zone_type = str(liquidity_zone.get("type", "zone")).strip() or "zone"
        level = float(liquidity_zone.get("level", 0.0) or 0.0)
        return f"{zone_type} @ {level:.4f}"

    def _detect_liquidity_reversal(
        self,
        df: pd.DataFrame,
        ticker: str,
        liquidity_zone: Optional[dict],
    ) -> Optional[TechnicalSignal]:
        """Promote a nearby liquidity rejection into an actionable signal candidate."""
        if liquidity_zone is None or df is None or df.empty or len(df) < 3:
            return None

        zone_type = str(liquidity_zone.get("type", "")).strip().lower()
        zone_level = float(liquidity_zone.get("level", 0.0) or 0.0)
        if zone_type not in {"equal_highs", "swing_lows"} or zone_level <= 0:
            return None

        last = df.iloc[-1]
        candle = {
            "open": float(last.get("Open", 0.0) or 0.0),
            "high": float(last.get("High", 0.0) or 0.0),
            "low": float(last.get("Low", 0.0) or 0.0),
            "close": float(last.get("Close", 0.0) or 0.0),
            "volume": float(last.get("Volume", 0.0) or 0.0),
        }
        if candle["close"] <= 0:
            return None

        rsi_value = float(last.get("RSI", 50.0) or 50.0)
        atr_value = float(last.get("ATR", 0.0) or 0.0)
        distance_ratio = abs(candle["close"] - zone_level) / max(abs(zone_level), 1.0)
        zone_width = max(abs(zone_level) * 0.0015, atr_value * 0.25 if atr_value > 0 else 0.0, 0.5)

        demand_zones = []
        supply_zones = []
        if zone_type == "equal_highs":
            supply_zones.append({
                "low": zone_level - zone_width,
                "high": zone_level + zone_width,
                "strength": 0.7,
            })
        else:
            demand_zones.append({
                "low": zone_level - zone_width,
                "high": zone_level + zone_width,
                "strength": 0.7,
            })

        sweep = self.code_architect.detect_liquidity_sweep(
            candle=candle,
            demand_zones=demand_zones,
            supply_zones=supply_zones,
            rsi_value=rsi_value,
            rsi_divergence=False,
        )

        signal_type = None
        strength = 0.0
        bias = None

        if sweep:
            bias = "SELL" if sweep.get("direction") == "BEARISH" else "BUY"
            signal_type = f"LIQUIDITY_SWEEP_{bias}"
            strength = max(0.65, float(sweep.get("conviction", 0.65) or 0.65))
        else:
            near_supply = zone_type == "equal_highs" and candle["high"] >= (zone_level - zone_width) and candle["close"] < candle["open"]
            near_demand = zone_type == "swing_lows" and candle["low"] <= (zone_level + zone_width) and candle["close"] > candle["open"]
            if near_supply:
                bias = "SELL"
                signal_type = "LIQUIDITY_REJECTION_SELL"
            elif near_demand:
                bias = "BUY"
                signal_type = "LIQUIDITY_REJECTION_BUY"
            else:
                return None

            strength = max(0.60, min(0.85, 0.85 - (distance_ratio * 100)))

        logger.info(
            "🎯 Liquidity trigger armed for %s: %s near %s",
            ticker,
            signal_type,
            self._format_liquidity_zone_label(liquidity_zone),
        )

        return TechnicalSignal(
            ticker=ticker,
            signal_type=signal_type,
            strength=round(strength, 2),
            metadata={
                "price": candle["close"],
                "rsi": rsi_value,
                "atr": atr_value,
                "liquidity_bias": bias,
                "liquidity_sweep": sweep,
                "liquidity_zone_label": self._format_liquidity_zone_label(liquidity_zone),
            },
        )
    
    async def _fetch_market_data(
        self,
        ticker: str,
        period: str = "1d",
        interval: str = "1m",
    ) -> Optional[pd.DataFrame]:
        """Fetch market data using yfinance with 3-try retry loop."""
        import concurrent.futures
        import time

        cache_key = (ticker, period, interval)
        
        max_retries = 3
        retry_delay = 2  # seconds
        
        for attempt in range(max_retries):
            try:
                symbol = yf.Ticker(ticker)
                
                def fetch_data():
                    return symbol.history(period=period, interval=interval)

                # Run in thread with timeout
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(fetch_data)
                    df = future.result(timeout=15)

                # Validate data quality
                if df is None or df.empty or df['Close'].iloc[-1] is None:
                    if attempt < max_retries - 1:
                        logger.warning(f"Empty data for {ticker} (attempt {attempt+1}/{max_retries}) - retrying...")
                        time.sleep(retry_delay)
                        continue
                    else:
                        fallback = self._fallback_market_data(ticker, period, interval, cache_key)
                        if fallback is not None:
                            return fallback
                        logger.error(f"⚠️ Market Data Timeout - Skipping Cycle for {ticker}")
                        return None

                sanitized = df.copy()
                self.market_data_cache[cache_key] = sanitized
                last_close = sanitized['Close'].dropna().iloc[-1] if 'Close' in sanitized and not sanitized['Close'].dropna().empty else None
                if last_close is not None:
                    self.last_close_cache[ticker] = float(last_close)

                return sanitized
                
            except concurrent.futures.TimeoutError:
                if attempt < max_retries - 1:
                    logger.warning(f"Timeout fetching data for {ticker} (attempt {attempt+1}/{max_retries}) - retrying...")
                    time.sleep(retry_delay)
                    continue
                else:
                    fallback = self._fallback_market_data(ticker, period, interval, cache_key)
                    if fallback is not None:
                        return fallback
                    logger.error(f"⚠️ Market Data Timeout - Skipping Cycle for {ticker}")
                    return None
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Error fetching data for {ticker}: {e} (attempt {attempt+1}/{max_retries}) - retrying...")
                    time.sleep(retry_delay)
                    continue
                else:
                    fallback = self._fallback_market_data(ticker, period, interval, cache_key)
                    if fallback is not None:
                        logger.warning("Using fallback market data for %s after fetch failure: %s", ticker, e)
                        return fallback
                    logger.error(f"⚠️ Market Data Timeout - Skipping Cycle for {ticker}: {e}")
                    return None
        
        return None

    def _fallback_market_data(
        self,
        ticker: str,
        period: str,
        interval: str,
        cache_key: Tuple[str, str, str],
    ) -> Optional[pd.DataFrame]:
        """Return cached bars or synthesize intraday bars from last known close/daily history."""
        cached = self.market_data_cache.get(cache_key)
        if cached is not None and not cached.empty:
            logger.warning("Using cached market data for %s (%s/%s)", ticker, period, interval)
            return cached.copy()

        try:
            daily_history = yf.Ticker(ticker).history(period="5d", interval="1d")
        except Exception as e:
            daily_history = None
            logger.debug("Daily fallback fetch failed for %s: %s", ticker, e)

        close_values: List[float] = []
        if daily_history is not None and not daily_history.empty and "Close" in daily_history:
            close_values = [float(value) for value in daily_history["Close"].dropna().tolist()]

        if not close_values and ticker in self.last_close_cache:
            close_values = [self.last_close_cache[ticker]]

        if not close_values:
            return None

        fallback = self._build_synthetic_intraday_frame(close_values, interval)
        if fallback is None or fallback.empty:
            return None

        self.market_data_cache[cache_key] = fallback.copy()
        self.last_close_cache[ticker] = float(fallback["Close"].iloc[-1])
        logger.warning("Synthesized fallback bars for %s from last known close data", ticker)
        return fallback

    def _build_synthetic_intraday_frame(self, close_values: List[float], interval: str) -> Optional[pd.DataFrame]:
        """Build flat-to-gently-trended candles so weekend fetch failures don't abort analysis."""
        normalized = [float(value) for value in close_values if value is not None]
        if not normalized:
            return None

        freq_map = {
            "1m": "1min",
            "3m": "3min",
            "5m": "5min",
            "15m": "15min",
            "1h": "1h",
            "1d": "1d",
        }
        freq = freq_map.get(interval, "1min")
        bar_count = max(80, len(normalized) * 24)

        samples: List[float] = []
        if len(normalized) == 1:
            samples = [normalized[0]] * bar_count
        else:
            segments = len(normalized) - 1
            bars_per_segment = max(1, bar_count // segments)
            for index in range(segments):
                start = normalized[index]
                end = normalized[index + 1]
                for step in range(bars_per_segment):
                    ratio = step / max(1, bars_per_segment)
                    samples.append(start + ((end - start) * ratio))
            samples.append(normalized[-1])
            if len(samples) < bar_count:
                samples.extend([normalized[-1]] * (bar_count - len(samples)))
            samples = samples[-bar_count:]

        index = pd.date_range(end=datetime.utcnow(), periods=len(samples), freq=freq)
        rows = []
        previous_close = samples[0]
        for close_price in samples:
            wiggle = max(abs(close_price) * 0.0005, 0.01)
            row_open = previous_close
            row_high = max(row_open, close_price) + wiggle
            row_low = min(row_open, close_price) - wiggle
            rows.append((row_open, row_high, row_low, close_price, 0.0))
            previous_close = close_price

        return pd.DataFrame(rows, index=index, columns=["Open", "High", "Low", "Close", "Volume"])

    async def _evaluate_timeframe_alignment(self, ticker: str, action: str) -> Tuple[bool, Dict[str, str]]:
        """5m/3m/1m triple-check. All three timeframes must align with action."""
        action = str(action or "").upper()
        if action not in {"BUY", "SELL"}:
            return False, {}

        timeframe_config = [
            ("5m", "2d", "5m"),
            ("3m", "2d", "3m"),
            ("1m", "1d", "1m"),
        ]
        votes: Dict[str, str] = {}

        def _resample_to_3m(df_1m: pd.DataFrame) -> Optional[pd.DataFrame]:
            if df_1m is None or df_1m.empty:
                return None
            try:
                if not isinstance(df_1m.index, pd.DatetimeIndex):
                    return None
                out = pd.DataFrame()
                out["Open"] = df_1m["Open"].resample("3min").first()
                out["High"] = df_1m["High"].resample("3min").max()
                out["Low"] = df_1m["Low"].resample("3min").min()
                out["Close"] = df_1m["Close"].resample("3min").last()
                out["Volume"] = df_1m["Volume"].resample("3min").sum()
                out = out.dropna()
                return out
            except Exception:
                return None

        for label, period, interval in timeframe_config:
            if interval == "3m":
                one_min_df = await self._fetch_market_data(ticker, period=period, interval="1m")
                df = _resample_to_3m(one_min_df)
            else:
                df = await self._fetch_market_data(ticker, period=period, interval=interval)
            if df is None or len(df) < 30:
                votes[label] = "WAIT"
                continue

            required_cols = {"Open", "High", "Low", "Close", "Volume"}
            if not required_cols.issubset(df.columns):
                votes[label] = "WAIT"
                continue

            if df[list(required_cols)].tail(1).isnull().any(axis=1).iloc[0]:
                logger.info("⏳ MTF wait-mode: %s %s has partial candle data", ticker, label)
                votes[label] = "WAIT"
                continue

            fast = ta.sma(df["Close"], length=9)
            slow = ta.sma(df["Close"], length=21)
            rsi = ta.rsi(df["Close"], length=14)

            f = fast.iloc[-1]
            s = slow.iloc[-1]
            r = rsi.iloc[-1]
            if pd.isna(f) or pd.isna(s) or pd.isna(r):
                votes[label] = "WAIT"
            elif f > s and r >= 50:
                votes[label] = "BUY"
            elif f < s and r <= 50:
                votes[label] = "SELL"
            else:
                votes[label] = "WAIT"

        aligned = all(votes.get(tf) == action for tf in ["5m", "3m", "1m"])
        return aligned, votes
    
    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate technical indicators (RSI, SMA, etc.)."""
        # RSI (14-period)
        df['RSI'] = ta.rsi(df['Close'], length=14)
        
        # SMA (20 and 50 period)
        df['SMA_FAST'] = ta.sma(df['Close'], length=config.SMA_FAST)
        df['SMA_SLOW'] = ta.sma(df['Close'], length=config.SMA_SLOW)
        
        # Volume moving average
        df['VOL_MA'] = ta.sma(df['Volume'], length=20)

        # ATR (14-period) for Gemini data package
        df['ATR'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)
        
        return df

    def _build_brain_package(self, df: pd.DataFrame, liquidity_zone: Optional[dict]) -> dict:
        """Build the Gemini data package from the latest candles and liquidity geometry."""
        recent = df.tail(10).copy() if df is not None else pd.DataFrame()
        recent_ohlcv = []
        recent_lines = []
        for index, row in recent.iterrows():
            candle = {
                "timestamp": index.isoformat() if hasattr(index, "isoformat") else str(index),
                "open": float(row.get("Open", 0.0) or 0.0),
                "high": float(row.get("High", 0.0) or 0.0),
                "low": float(row.get("Low", 0.0) or 0.0),
                "close": float(row.get("Close", 0.0) or 0.0),
                "volume": float(row.get("Volume", 0.0) or 0.0),
            }
            recent_ohlcv.append(candle)
            recent_lines.append(
                f"{candle['timestamp']} O:{candle['open']:.4f} H:{candle['high']:.4f} "
                f"L:{candle['low']:.4f} C:{candle['close']:.4f} V:{candle['volume']:.2f}"
            )

        rsi_value = 50.0
        atr_value = 0.0
        if df is not None and not df.empty:
            if "RSI" in df and not df["RSI"].dropna().empty:
                rsi_value = float(df["RSI"].dropna().iloc[-1])
            if "ATR" in df and not df["ATR"].dropna().empty:
                atr_value = float(df["ATR"].dropna().iloc[-1])

        liquidity_zones = []
        if liquidity_zone:
            liquidity_zones.append(
                {
                    "type": liquidity_zone.get("type", "unknown"),
                    "coordinates": {
                        "start_index": int(liquidity_zone.get("start_index", 0) or 0),
                        "end_index": int(liquidity_zone.get("end_index", 0) or 0),
                        "level": float(liquidity_zone.get("level", 0.0) or 0.0),
                    },
                    "distance": float(liquidity_zone.get("distance", 0.0) or 0.0),
                    "current_price": float(liquidity_zone.get("current_price", 0.0) or 0.0),
                }
            )

        return {
            "recent_ohlcv": recent_ohlcv,
            "recent_candle_lines": recent_lines,
            "rsi": rsi_value,
            "atr": atr_value,
            "liquidity_zones": liquidity_zones,
        }
    
    def _detect_volume_spike(self, df: pd.DataFrame, ticker: str) -> Optional[TechnicalSignal]:
        """Detect volume spike (>3x average)."""
        if len(df) < 2:
            return None
        
        last_vol = df['Volume'].iloc[-1]
        avg_vol = df['VOL_MA'].iloc[-1]
        
        if pd.isna(avg_vol) or avg_vol == 0:
            return None
        
        volume_ratio = last_vol / avg_vol
        
        if volume_ratio > config.VOLUME_SPIKE_MULTIPLIER:
            strength = min(1.0, volume_ratio / 10.0)  # Normalize to 0-1
            return TechnicalSignal(
                ticker=ticker,
                signal_type="VOLUME_SPIKE",
                strength=strength,
                metadata={
                    "volume_ratio": volume_ratio,
                    "last_volume": last_vol,
                    "avg_volume": avg_vol,
                    "price": df['Close'].iloc[-1]
                }
            )
        
        return None
    
    def _detect_rsi_signal(self, df: pd.DataFrame, ticker: str) -> Optional[TechnicalSignal]:
        """Detect RSI overbought/oversold conditions."""
        if len(df) < 2:
            return None
        
        current_rsi = df['RSI'].iloc[-1]
        prev_rsi = df['RSI'].iloc[-2]
        
        if pd.isna(current_rsi) or pd.isna(prev_rsi):
            return None
        
        # RSI crosses below oversold (bullish)
        if prev_rsi >= config.RSI_OVERSOLD and current_rsi < config.RSI_OVERSOLD:
            return TechnicalSignal(
                ticker=ticker,
                signal_type="RSI_OVERSOLD",
                strength=0.75,
                metadata={
                    "rsi": current_rsi,
                    "price": df['Close'].iloc[-1]
                }
            )
        
        # RSI crosses above overbought (bearish)
        if prev_rsi <= config.RSI_OVERBOUGHT and current_rsi > config.RSI_OVERBOUGHT:
            return TechnicalSignal(
                ticker=ticker,
                signal_type="RSI_OVERBOUGHT",
                strength=0.75,
                metadata={
                    "rsi": current_rsi,
                    "price": df['Close'].iloc[-1]
                }
            )
        
        return None
    
    def _detect_sma_cross(self, df: pd.DataFrame, ticker: str) -> Optional[TechnicalSignal]:
        """Detect SMA fast/slow cross (Golden Cross / Death Cross)."""
        if len(df) < 2:
            return None
        
        current_fast = df['SMA_FAST'].iloc[-1]
        current_slow = df['SMA_SLOW'].iloc[-1]
        prev_fast = df['SMA_FAST'].iloc[-2]
        prev_slow = df['SMA_SLOW'].iloc[-2]
        
        if any(pd.isna([current_fast, current_slow, prev_fast, prev_slow])):
            return None
        
        # Golden Cross (bullish)
        if prev_fast <= prev_slow and current_fast > current_slow:
            return TechnicalSignal(
                ticker=ticker,
                signal_type="SMA_CROSS_BULLISH",
                strength=0.80,
                metadata={
                    "sma_fast": current_fast,
                    "sma_slow": current_slow,
                    "price": df['Close'].iloc[-1]
                }
            )
        
        # Death Cross (bearish)
        if prev_fast >= prev_slow and current_fast < current_slow:
            return TechnicalSignal(
                ticker=ticker,
                signal_type="SMA_CROSS_BEARISH",
                strength=0.80,
                metadata={
                    "sma_fast": current_fast,
                    "sma_slow": current_slow,
                    "price": df['Close'].iloc[-1]
                }
            )
        
        return None
    
    def _is_signal_cooldown(self, ticker: str, signal_type: str) -> bool:
        """Check if signal is within cooldown period."""
        key = f"{ticker}:{signal_type}"
        if key in self.recent_signals:
            elapsed = (datetime.utcnow() - self.recent_signals[key]).total_seconds()
            return elapsed < self.signal_cooldown
        return False
    
    def _record_signal(self, ticker: str, signal_type: str):
        """Record signal timestamp for cooldown tracking."""
        key = f"{ticker}:{signal_type}"
        self.recent_signals[key] = datetime.utcnow()
    
    async def process_signals(self, signals: List[TechnicalSignal]) -> Optional[dict]:
        """
        Process detected signals through Swarm Debate.
        Returns trade signal if confidence > threshold.
        FAST MODE: Single agent call with 15s timeout for local execution.
        """
        if not signals:
            return None

        for signal in signals:
            # Check cooldown
            if self._is_signal_cooldown(signal.ticker, signal.signal_type):
                logger.debug(f"Signal cooldown: {signal.ticker} - {signal.signal_type}")
                continue

            logger.info(
                f"🔥 Signal detected: {signal.signal_type} on {signal.ticker} "
                f"(strength: {signal.strength:.2f})"
            )

            # Build market data for Swarm
            market_data = self._build_market_data(signal)

            # Run Swarm Debate
            try:
                analysis, transcript = await self.consensus.run(market_data)

                # 🔥 NUCLEAR FIX: Override HOLD action if technical signal is strong
                # and agents are aligned (Technical Sniper + Risk Manager agree)
                if analysis.action.value == "HOLD" and signal.strength >= 0.60:
                    # Check if agents support a trade direction
                    tech_action = transcript.technical_sniper.action if transcript else "HOLD"
                    risk_verdict = transcript.risk_manager.verdict if transcript else "HOLD"
                    
                    if tech_action in ["BUY", "SELL"] and risk_verdict == "APPROVE":
                        logger.info(
                            f"🔥 NUCLEAR OVERRIDE: LLM returned HOLD but technical signal "
                            f"strong ({signal.strength:.2f}) and agents aligned "
                            f"(Tech: {tech_action}, Risk: {risk_verdict})"
                        )
                        # Override to technical signal direction
                        analysis.action = SignalAction(tech_action)
                        analysis.reason = f"Strong technical {signal.signal_type} signal (strength: {signal.strength:.2f}) with agent alignment"

                # Calculate confidence score (0.0-1.0)
                confidence_score = self._calculate_confidence(analysis, transcript, signal.strength)

                logger.info(f"Swarm consensus: {confidence_score:.2f} confidence")

                # ALWAYS return signal - let user decide, not the AI
                self._record_signal(signal.ticker, signal.signal_type)

                # FILTER: Only dispatch BUY/SELL signals (skip HOLD)
                if analysis.action.value == "HOLD":
                    logger.info(f"⏸️ HOLD signal for {signal.ticker} - not dispatching (no trade)")
                    self._emit_status(signal.ticker, "trade_rejected")
                    continue  # Skip to next signal

                # Sniper triple-check: trend/setup/entry must all align (5m/3m/1m).
                mtf_ok, mtf_votes = await self._evaluate_timeframe_alignment(
                    signal.ticker,
                    analysis.action.value,
                )
                if not mtf_ok:
                    logger.info(
                        "🎯 MTF block: %s %s rejected by 5m/3m/1m alignment %s",
                        analysis.action.value,
                        signal.ticker,
                        mtf_votes,
                    )
                    self._emit_status(signal.ticker, "trade_rejected")
                    continue

                approved_brain_verdict = f"[SIGNAL] {analysis.action.value}"
                logger.info(
                    "OPENROUTER STRIKE REVIEW: %s %s proposed=%s",
                    signal.signal_type,
                    signal.ticker,
                    analysis.action.value,
                )
                self._emit_status(signal.ticker, f"brain_reasoning:{analysis.action.value}")
                brain_decision = await self._request_brain_verdict(signal, analysis.action.value)
                brain_verdict = str(brain_decision.get("verdict", "[SIGNAL] WAIT") or "[SIGNAL] WAIT").upper()
                brain_used = str(brain_decision.get("brain_used", "OPENROUTER") or "OPENROUTER").upper()
                fallback_mode = bool(brain_decision.get("fallback_mode"))
                if fallback_mode:
                    self._emit_status(signal.ticker, f"brain_fallback:{brain_used}")
                self._emit_status(signal.ticker, f"brain_verdict:{brain_verdict}")
                if brain_verdict != approved_brain_verdict:
                    logger.info(
                        "BRAIN VETOED TRADE: %s %s | response=%s | brain=%s | model=%s | reasoning=%s",
                        analysis.action.value,
                        signal.ticker,
                        brain_verdict,
                        brain_used,
                        brain_decision.get("model", "n/a"),
                        brain_decision.get("reasoning", ""),
                    )
                    self._emit_status(signal.ticker, "trade_rejected")
                    continue

                # 🔥 FIX: Ensure entry/stop/tp are set (use signal price if LLM returned 0)
                signal_price = signal.metadata.get("price", market_data.price)
                if analysis.entry_price == 0.0 or analysis.entry_price is None:
                    analysis.entry_price = signal_price
                if analysis.stop_loss == 0.0 or analysis.stop_loss is None:
                    analysis.stop_loss = signal_price * 0.99  # 1% SL default
                if analysis.take_profit == 0.0 or analysis.take_profit is None:
                    analysis.take_profit = signal_price * 1.01  # 1% TP default

                # Build clean signal data - NO datetime objects!
                return {
                    "ticker": signal.ticker,
                    "action": analysis.action.value,
                    "confidence": confidence_score,
                    "entry_price": float(analysis.entry_price),
                    "stop_loss": float(analysis.stop_loss),
                    "take_profit": float(analysis.take_profit),
                    "reason": str(analysis.reason),
                    "signal_type": signal.signal_type,
                    "mtf_check": mtf_votes,
                    "brain_verdict": brain_verdict,
                    "brain_reasoning": str(brain_decision.get("reasoning", "") or ""),
                    "brain_model": str(brain_decision.get("model", self.brain.model) or self.brain.model),
                    "brain_used": brain_used,
                    "fallback_mode": fallback_mode,
                    "force_execute": brain_verdict in {"[SIGNAL] BUY", "[SIGNAL] SELL"},
                    "liquidity_zone": signal.metadata.get("liquidity_zone"),
                    "investment_amount": 1000.0,  # Default $1000 per trade
                    "transcript": {
                        "technical_sniper": {
                            "agent": transcript.technical_sniper.agent,
                            "action": transcript.technical_sniper.action,
                            "conviction": transcript.technical_sniper.conviction,
                        },
                        "macro_analyst": {
                            "agent": transcript.macro_analyst.agent,
                            "action": transcript.macro_analyst.action,
                            "conviction": transcript.macro_analyst.conviction,
                        },
                        "risk_manager": {
                            "agent": transcript.risk_manager.agent,
                            "verdict": transcript.risk_manager.verdict,
                            "conviction": transcript.risk_manager.conviction,
                        },
                        "devils_advocate": {
                            "rating": transcript.devils_advocate.get("rating", "NEUTRAL"),
                            "rejection_reasons": transcript.devils_advocate.get("rejection_reasons", []),
                            "hidden_risks": transcript.devils_advocate.get("hidden_risks", "Unknown"),
                        },
                        "ceo_verdict": transcript.ceo_verdict,
                    }
                }

            except Exception as e:
                logger.error(f"Swarm consensus failed: {e}")
                continue

        return None
    
    def _build_market_data(self, signal: TechnicalSignal) -> MarketDataPoint:
        """Build MarketDataPoint from technical signal."""
        price = signal.metadata.get("price", 0.0)
        volume = signal.metadata.get("last_volume", 0.0)
        
        return MarketDataPoint(
            asset=signal.ticker,
            price=price,
            volume=volume,
            price_change_1h=0.0,  # Would calculate from historical data
            price_change_24h=0.0,
            indicators={
                "RSI": signal.metadata.get("rsi", 50),
                "ATR": signal.metadata.get("atr", 0.0),
                "SIGNAL_TYPE": signal.signal_type,
                "SIGNAL_STRENGTH": signal.strength,
                "LIQUIDITY_ZONE": signal.metadata.get("liquidity_zone_label", self._format_liquidity_zone_label(signal.metadata.get("liquidity_zone"))),
                "RECENT_CANDLES": signal.metadata.get("recent_candle_lines", []),
                "RECENT_OHLCV": signal.metadata.get("recent_ohlcv", []),
                "LIQUIDITY_ZONES": signal.metadata.get("liquidity_zones", []),
            }
        )

    async def _request_brain_verdict(self, signal: TechnicalSignal, proposed_action: str) -> dict:
        """Ask OpenRouter for the final approval after triple alignment passes."""
        package = {
            "asset": signal.ticker,
            "recent_ohlcv": signal.metadata.get("recent_ohlcv", []),
            "rsi": signal.metadata.get("rsi", 50.0),
            "atr": signal.metadata.get("atr", 0.0),
            "liquidity_zones": signal.metadata.get("liquidity_zones", []),
            "liquidity_zone_label": signal.metadata.get("liquidity_zone_label", self._format_liquidity_zone_label(signal.metadata.get("liquidity_zone"))),
            "signal_type": signal.signal_type,
        }

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self.brain.request_decision, proposed_action, package),
                timeout=max(1, int(config.GEMINI_TIMEOUT)),
            )
        except asyncio.TimeoutError:
            logger.warning("OpenRouter brain timeout for %s %s - switching to local Predator", proposed_action, signal.ticker)
            return await asyncio.to_thread(
                self.brain.predator.request_decision,
                proposed_action,
                package,
            )
        except Exception as exc:
            logger.warning("OpenRouter brain request failed for %s %s: %s - switching to local Predator", proposed_action, signal.ticker, exc)
            return await asyncio.to_thread(
                self.brain.predator.request_decision,
                proposed_action,
                package,
            )
    
    def _calculate_confidence(self, analysis, transcript, signal_strength: float = 0.5) -> float:
        """
        Calculate numerical confidence score (0.0-1.0) from Swarm output.
        Maps ConfidenceLevel enum to numerical values.
        Applies Devil's Advocate penalty if risks identified.
        
        NUCLEAR FIX: Forces minimum MEDIUM for strong technical signals to prevent
        the system from being paralyzed by over-conservative LLM responses.
        """
        confidence_map = {
            ConfidenceLevel.LOW: 0.40,
            ConfidenceLevel.MEDIUM: 0.60,
            ConfidenceLevel.HIGH: 0.80,
            ConfidenceLevel.VERY_HIGH: 0.95
        }

        base_confidence = confidence_map.get(analysis.confidence, 0.50)
        
        # 🔥 NUCLEAR FIX: Force minimum MEDIUM (0.60) for strong technical signals
        # If technical signal is strong (strength > 0.6) but LLM returned LOW, boost to MEDIUM
        if base_confidence < 0.60 and signal_strength >= 0.60:
            logger.info(
                f"🔥 NUCLEAR OVERRIDE: Technical signal strong ({signal_strength:.2f}), "
                f"boosting LLM LOW to MEDIUM minimum"
            )
            base_confidence = max(base_confidence, 0.60)

        # Adjust based on agent alignment
        alignment_bonus = 0.0
        if transcript:
            agents_aligned = 0
            total_agents = 0

            # Check Technical Sniper
            if transcript.technical_sniper.action in ["BUY", "SELL"]:
                total_agents += 1
                if transcript.technical_sniper.action == analysis.action.value:
                    agents_aligned += 1

            # Check Macro Analyst
            if transcript.macro_analyst.action in ["BULLISH", "BEARISH"]:
                total_agents += 1
                macro_aligned = (
                    (transcript.macro_analyst.action == "BULLISH" and analysis.action == SignalAction.BUY) or
                    (transcript.macro_analyst.action == "BEARISH" and analysis.action == SignalAction.SELL)
                )
                if macro_aligned:
                    agents_aligned += 1

            # Check Risk Manager
            if transcript.risk_manager.verdict == "APPROVE":
                total_agents += 1
                agents_aligned += 1

            if total_agents > 0:
                alignment_ratio = agents_aligned / total_agents
                alignment_bonus = alignment_ratio * 0.15  # Max 15% bonus

        # Include signal strength
        signal_weight = 0.05  # Small weight for technical signal strength

        # 😈 Devil's Advocate Penalty - Reduce confidence if risks identified
        devils_penalty = 0.0
        if transcript and hasattr(transcript, 'devils_advocate') and transcript.devils_advocate:
            devils_penalty = transcript.devils_advocate.get("confidence_penalty", 0.0)
            rating = transcript.devils_advocate.get("rating", "NEUTRAL")
            if rating in ["STRONG_AVOID", "CAUTIOUS"]:
                logger.warning(
                    f"😈 Devil's Advocate applied {devils_penalty:.2f} penalty "
                    f"(rating: {rating})"
                )

        final_confidence = max(0.0, min(1.0, base_confidence + alignment_bonus + signal_weight + devils_penalty))
        return round(final_confidence, 2)
    
    async def dispatch_to_local(self, signal_data: dict) -> bool:
        """
        Dispatch trade signal to local laptop executor via HTTP.
        Returns True if successfully received.
        """
        try:
            local_url = f"{config.CLOUD_SCANNER_URL}/api/signal"
            
            logger.info(f"📡 Attempting to dispatch signal to: {local_url}")
            logger.info(f"   Signal: {signal_data.get('action')} {signal_data.get('ticker')} (confidence: {signal_data.get('confidence', 0):.2f})")

            response = requests.post(
                local_url,
                json=signal_data,
                timeout=config.LOCAL_EXECUTION_TIMEOUT,
                headers={"Content-Type": "application/json"}
            )

            if response.status_code == 200:
                logger.info(f"✅ Signal dispatched successfully to local executor")
                return True
            else:
                logger.error(f"❌ Failed to dispatch signal: HTTP {response.status_code} - {response.text}")
                return False

        except requests.exceptions.ConnectionError as e:
            logger.error(f"❌ Connection refused - Signal Listener not running at {config.CLOUD_SCANNER_URL}")
            logger.error(f"   Make sure the Signal Listener thread is started")
            return False
        except requests.exceptions.Timeout as e:
            logger.error(f"❌ Request timeout - Signal Listener didn't respond within {config.LOCAL_EXECUTION_TIMEOUT}s")
            return False
        except Exception as e:
            logger.error(f"❌ Signal dispatch failed: {type(e).__name__}: {e}")
            return False
    
    async def run_scanner(self):
        """Main scanner loop - continuous scanning."""
        logger.info(f" Cloud Scanner started - monitoring {len(self.tickers)} tickers")
        logger.info(f"Tickers: {', '.join(self.tickers)}")
        logger.info(f"Confidence threshold: {config.SWARM_CONFIDENCE_THRESHOLD}")
        
        while True:
            try:
                # Scan all tickers
                signals = await self.scan_all_tickers()
                
                # Process through Swarm
                if signals:
                    trade_signal = await self.process_signals(signals)
                    
                    if trade_signal:
                        # Dispatch to local executor
                        success = await self.dispatch_to_local(trade_signal)
                        
                        if success:
                            logger.info(f" Trade signal executed: {trade_signal}")
                        else:
                            logger.warning(f" Trade signal dispatch failed")
                
                # Wait before next scan
                await asyncio.sleep(self.get_scan_interval())
                
            except KeyboardInterrupt:
                logger.info("Scanner stopped by user")
                break
            except Exception as e:
                logger.error(f"Scanner error: {e}")
                await asyncio.sleep(5)  # Wait before retry


def run_cloud_scanner():
    """Entry point for cloud scanner (runs on Vast.ai server)."""
    import sys
    
    print("=" * 60)
    print("VcanTrade AI - Cloud Market Scanner")
    print("=" * 60)
    print(f"Monitoring: {len(config.CLOUD_TICKERS)} tickers")
    for ticker in config.CLOUD_TICKERS:
        print(f"  - {ticker}")
    print(f"Scan interval: {config.SCAN_INTERVAL}s")
    print(f"Confidence threshold: {config.SWARM_CONFIDENCE_THRESHOLD}")
    print("=" * 60)
    
    scanner = CloudScanner()
    
    try:
        asyncio.run(scanner.run_scanner())
    except KeyboardInterrupt:
        print("\nShutting down scanner...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal scanner error: {e}")
        raise


if __name__ == "__main__":
    run_cloud_scanner()
