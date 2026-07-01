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
from core.data_feed import data_feed
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
        self.session_detector = MarketSessionDetector()
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
        self.higher_timeframe_cache: Dict[Tuple[str, str], Tuple[float, Optional[pd.DataFrame]]] = {}
        self.higher_timeframe_cache_seconds = float(getattr(config, "HIGHER_TIMEFRAME_CACHE_SECONDS", 300) or 300)
        self.signal_history: Dict[str, List[Tuple[float, str]]] = {}
        self.signal_stability_cycles = int(getattr(config, "SIGNAL_STABILITY_CYCLES", 2) or 2)
        self.signal_stability_window_seconds = float(getattr(config, "SIGNAL_STABILITY_WINDOW_SECONDS", 180) or 180)
        self.require_higher_timeframe_confirmation = bool(getattr(config, "REQUIRE_HIGHER_TIMEFRAME_CONFIRMATION", True))
        self.higher_timeframe_interval = str(getattr(config, "HIGHER_TIMEFRAME_INTERVAL", "1h") or "1h")
        
        # Reference to engine lock (set externally)
        self._engine_lock = None
        
        logger.info("[SCANNER] Initialized with single-asset lock respect")
    
    def set_engine_lock(self, lock):
        """Set reference to VcaniTradeEngine.asset_lock."""
        self._engine_lock = lock
        logger.info("[SCANNER] Engine lock reference set")
    
    def _simple_adx(self, high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        prev_close = close.shift(1)
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
        tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        plus_di = 100 * plus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr
        minus_di = 100 * minus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)
        return dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    
    def _higher_timeframe_bias(self, df: Optional[pd.DataFrame]) -> Tuple[str, str, int, float, float, str]:
        """Return 1H dropdown-style directional bias using multiple indicators."""
        if df is None or df.empty or len(df) < 60:
            return "UNKNOWN", "Not enough higher-timeframe bars", 0, 0.0, 0.0, "UNKNOWN"
        try:
            close = df["Close"].dropna()
            if len(close) < 60:
                return "UNKNOWN", "Not enough clean higher-timeframe closes", 0, 0.0, 0.0, "UNKNOWN"
            ema9 = close.ewm(span=9, adjust=False).mean()
            ema21 = close.ewm(span=21, adjust=False).mean()
            ema50 = close.ewm(span=50, adjust=False).mean()
            ema200 = close.ewm(span=200, adjust=False).mean()
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd_line = ema12 - ema26
            macd_signal = macd_line.ewm(span=9, adjust=False).mean()
            macd_hist = float((macd_line - macd_signal).iloc[-1] or 0.0)
            adx = float(self._simple_adx(df["High"], df["Low"], close).iloc[-1] or 0.0)
            last_close = float(close.iloc[-1])
            last_ema9 = float(ema9.iloc[-1])
            last_ema21 = float(ema21.iloc[-1])
            last_ema50 = float(ema50.iloc[-1])
            last_ema200 = float(ema200.iloc[-1])
            ema_alignment = "UNKNOWN"
            score = 0
            if last_close > last_ema9 > last_ema21 > last_ema50 > last_ema200:
                score += 3
                ema_alignment = "BULL_STACK"
            elif last_close > last_ema21 > last_ema50:
                score += 2
                ema_alignment = "LEAN_BULL_STACK"
            elif last_close > last_ema50 and last_ema9 > last_ema21:
                score += 1
                ema_alignment = "LEAN_BULL"
            elif last_close < last_ema9 < last_ema21 < last_ema50 < last_ema200:
                score -= 3
                ema_alignment = "BEAR_STACK"
            elif last_close < last_ema21 < last_ema50:
                score -= 2
                ema_alignment = "LEAN_BEAR_STACK"
            elif last_close < last_ema50 and last_ema9 < last_ema21:
                score -= 1
                ema_alignment = "LEAN_BEAR"
            else:
                ema_alignment = "MIXED"
            if macd_hist > 0:
                score += 1
            elif macd_hist < 0:
                score -= 1
            if adx >= 25:
                if score > 0:
                    score += 1
                elif score < 0:
                    score -= 1
            if score >= 3:
                bias = "BULLISH"
                reason = "1H dropdown bullish: EMA stack and momentum aligned"
            elif score >= 1:
                bias = "LEAN_BULLISH"
                reason = "1H dropdown lean-bullish: partial bullish structure"
            elif score <= -3:
                bias = "BEARISH"
                reason = "1H dropdown bearish: EMA stack and momentum aligned"
            elif score <= -1:
                bias = "LEAN_BEARISH"
                reason = "1H dropdown lean-bearish: partial bearish structure"
            else:
                bias = "MIXED"
                reason = "1H dropdown mixed: no clean directional edge"
            return bias, reason, score, adx, macd_hist, ema_alignment
        except Exception as exc:
            logger.debug("[SCANNER] Higher-timeframe bias failed: %s", exc)
            return "UNKNOWN", f"1H filter error: {exc}", 0, 0.0, 0.0, "UNKNOWN"
    
    def _get_higher_timeframe_data(self, ticker: str) -> Optional[pd.DataFrame]:
        """Fetch 1H context with a short cache so scanning stays fast."""
        now = time.time()
        key = (str(ticker), self.higher_timeframe_interval)
        cached = self.higher_timeframe_cache.get(key)
        if cached and now - cached[0] < self.higher_timeframe_cache_seconds:
            return cached[1]
        df = self._fetch_market_data(ticker, interval=self.higher_timeframe_interval)
        self.higher_timeframe_cache[key] = (now, df)
        return df
    
    def _signal_history_is_stable(self, ticker: str, action: str) -> Tuple[bool, int]:
        """Require the same trade direction to survive multiple scanner cycles.

        HAWK MODE: require 2 consecutive same-direction cycles. We DON'T lower
        this to 1 because that would cause one-tick noise to become a real
        trade. Stability_cycles=2 means the signal has to survive a full
        SCAN_INTERVAL (10s) before firing.
        """
        now = time.monotonic()
        cutoff = now - self.signal_stability_window_seconds
        with self._lockdown_lock:
            history = self.signal_history.setdefault(ticker, [])
            history[:] = [(ts, past_action) for ts, past_action in history if ts >= cutoff]
            if not history or history[-1][1] != action:
                history[:] = [(now, action)]
                return False, 1
            history.append((now, action))
            recent = history[-self.signal_stability_cycles:]
            stable = len(recent) >= self.signal_stability_cycles and len({past_action for _, past_action in recent}) == 1
            return stable, len(recent)

    def _soft_signal(self, ticker: str, df: "pd.DataFrame", close: "pd.Series",
                     ema9: "pd.Series", ema21: "pd.Series",
                     current_rsi: float, trend_dir: str, vol_ratio: float) -> Optional[str]:
        """Light signal that fires on any clear directional bias, even in chop.

        This is the SOFT TIER — used so the user can see *something* happening
        even when the strict MOMENTUM/BREAKOUT signals don't fire. It does NOT
        bypass the stability requirement or any safety gate; it just adds more
        opportunities in markets that are mostly flat.

        Returns 'BUY' / 'SELL' / None.
        """
        try:
            if df is None or len(close) < 30:
                return None
            price = float(close.iloc[-1])
            ema9_v = float(ema9.iloc[-1])
            ema21_v = float(ema21.iloc[-1])
            # Heuristic 1: price is decisively above/below both EMAs AND RSI confirms.
            if price > ema9_v > ema21_v and current_rsi > 50 and current_rsi < 70:
                return "BUY"
            if price < ema9_v < ema21_v and current_rsi < 50 and current_rsi > 30:
                return "SELL"
            # Heuristic 2: 1H bias is clear (trend_dir says "Bull" / "Bear")
            # and price is on the right side of EMA21.
            if trend_dir == "Bull" and price > ema21_v and current_rsi > 45 and current_rsi < 65:
                return "BUY"
            if trend_dir == "Bear" and price < ema21_v and current_rsi < 55 and current_rsi > 35:
                return "SELL"
        except Exception:
            return None
        return None
    
    def _clear_signal_history(self, ticker: str) -> None:
        with self._lockdown_lock:
            self.signal_history.pop(ticker, None)
    
    def _is_locked_for_different_ticker(self, ticker: str) -> bool:
        """Check if engine is locked for a different ticker."""
        if self._engine_lock is None:
            return False
        return self._engine_lock.is_locked_for(ticker)
    
    def verify_correlation_exposure_allowed(self, target_ticker: str, current_positions: list) -> bool:
        """Blocks multi-index allocation patterns if an overlay contract pair is active in the trade ledger."""
        index_cluster = ["MNQ", "MES", "NAS100", "US500"]
        if any(idx in target_ticker.upper() for idx in index_cluster):
            for position in current_positions:
                if any(idx in position.get("ticker", "").upper() for idx in index_cluster):
                    logger.warning(f"[CORRELATION-GATE] Denied entry for {target_ticker}. Asset category cluster match running.")
                    return False
        return True
    
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
                if self.status_callback:
                    self.status_callback(str(ticker), "scanning")
                signal = self._scan_ticker(ticker)
                if signal:
                    signals.append(signal)
            except Exception as e:
                logger.warning("[SCANNER] Error scanning %s: %s", ticker, e)
                if self.status_callback:
                    self.status_callback(str(ticker), f"error: {str(e)[:60]}")
        
        return signals

    async def scan_all_tickers(self) -> List[TechnicalSignal]:
        """Async compatibility wrapper used by CloudScannerThread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.scan)

    async def process_signals(self, signals: List[TechnicalSignal]) -> Optional[dict]:
        """Run detected technical opportunities through the configured brain."""
        if not signals:
            return None

        signal = max(signals, key=lambda item: float(getattr(item, "strength", 0.0) or 0.0))
        ticker = str(getattr(signal, "ticker", "UNKNOWN") or "UNKNOWN")
        action = self._action_from_signal(signal)
        package = self._brain_package_from_signal(signal)

        if self.status_callback:
            self.status_callback(ticker, f"brain_reasoning:{action}")

        loop = asyncio.get_running_loop()
        decision = await loop.run_in_executor(
            None,
            lambda: self.brain.request_decision(action, package),
        )

        verdict = str(decision.get("verdict", "[SIGNAL] WAIT") or "[SIGNAL] WAIT").upper()
        if self.status_callback:
            if decision.get("fallback_mode"):
                self.status_callback(ticker, f"brain_fallback:{decision.get('brain_used', 'OLLAMA_PREDATOR')}")
            self.status_callback(ticker, f"brain_verdict:{verdict}")

        # Brain can only CONFIRM or REJECT (WAIT). It cannot flip direction.
        # The scanner's math-based direction is always correct.
        # If brain says WAIT, we skip. If brain says BUY/SELL (either direction), we proceed
        # with the SCANNER's original direction — not the brain's.
        if "BUY" not in verdict and "SELL" not in verdict:
            logger.info("[SCANNER] Brain returned WAIT for %s: %s", ticker, decision.get("reasoning", ""))
            return None

        # USE SCANNER'S DIRECTION, not brain's. Brain is just a go/no-go gate.
        final_action = action
        logger.info("[SCANNER] Brain approved %s for %s (scanner direction: %s)", verdict, ticker, action)
        metadata = getattr(signal, "metadata", {}) or {}
        confidence = self._combined_confidence(signal, decision, action_override=final_action)
        return {
            "ticker": ticker,
            "action": final_action,
            "confidence": confidence,
            "confidence_score": confidence,
            "reason": str(decision.get("reasoning") or f"{signal.signal_type} approved by local brain")[:500],
            "signal_type": str(getattr(signal, "signal_type", "SIGNAL") or "SIGNAL"),
            "stop_loss": float(metadata.get("stop_loss", 0.0) or 0.0),
            "take_profit": float(metadata.get("take_profit", 0.0) or 0.0),
            "brain_used": decision.get("brain_used", "LOCAL_BRAIN"),
            "fallback_mode": bool(decision.get("fallback_mode", False)),
            "h1_analysis": metadata.get("h1_analysis", {}),
            "raw_decision": decision,
            "metadata": getattr(signal, "metadata", {}) or {},
        }

    async def dispatch_to_local(self, trade_signal: dict) -> bool:
        """Local scanner compatibility hook.

        The desktop app already owns execution routing; returning True lets the
        QThread emit the signal back onto the Qt main thread.
        """
        return bool(trade_signal)

    def close(self):
        """Compatibility no-op for scanner threads."""
        return None

    def _action_from_signal(self, signal: TechnicalSignal) -> str:
        signal_type = str(getattr(signal, "signal_type", "") or "").upper()
        metadata = getattr(signal, "metadata", {}) or {}
        explicit = str(
            metadata.get("direction")
            or metadata.get("action_hint")
            or metadata.get("liquidity_bias")
            or metadata.get("action")
            or ""
        ).upper()
        if explicit in {"BUY", "SELL"}:
            return explicit
        if explicit in {"UP", "LONG"}:
            return "BUY"
        if explicit in {"DOWN", "SHORT"}:
            return "SELL"
        if "BULL" in signal_type or "OVERSOLD" in signal_type:
            return "BUY"
        if "BEAR" in signal_type or "OVERBOUGHT" in signal_type:
            return "SELL"
        return "WAIT"

    def _combined_confidence(self, signal: TechnicalSignal, decision: dict, action_override: Optional[str] = None) -> float:
        """Combine technical, brain, higher-timeframe, stability, and volume evidence."""
        metadata = getattr(signal, "metadata", {}) or {}
        action = str(action_override or self._action_from_signal(signal)).upper()
        technical = float(getattr(signal, "strength", 0.0) or 0.0)
        brain_conf = float(decision.get("confidence", 0.0) or 0.0) / 100.0
        h1_score = float(metadata.get("h1_score", 0.0) or 0.0)
        h1_strength = min(abs(h1_score) / 5.0, 1.0)
        cycles = max(int(metadata.get("stability_required", self.signal_stability_cycles) or 1), 1)
        stability = min(float(metadata.get("stability_count", 0.0) or 0.0) / cycles, 1.0)
        volume_ratio = float(metadata.get("volume_ratio", 0.0) or 0.0)
        rsi = float(metadata.get("rsi", 50.0) or 50.0)

        h1_aligned = (
            (action == "BUY" and h1_score >= 3)
            or (action == "SELL" and h1_score <= -3)
        )
        h1_mixed = abs(h1_score) < 2
        rsi_in_zone = (
            (action == "BUY" and 35.0 <= rsi <= 68.0)
            or (action == "SELL" and 32.0 <= rsi <= 65.0)
        )
        high_conviction_signal = any(
            token in str(getattr(signal, "signal_type", "") or "").upper()
            for token in ("SMA_CROSS", "MACD_CROSS", "RSI_OVER", "BB_OVER")
        )

        score = (
            0.35 * technical
            + 0.35 * brain_conf
            + 0.15 * h1_strength
            + 0.15 * stability
        )
        if h1_aligned:
            score += 0.08
        if h1_mixed:
            score -= 0.06
        if rsi_in_zone:
            score += 0.03
        if high_conviction_signal and technical >= 0.75:
            score += 0.04
        if volume_ratio >= 1.5:
            score += 0.03
        elif volume_ratio <= 0.4:
            score -= 0.02

        if technical >= 0.80 and brain_conf >= 0.90 and h1_aligned and stability >= 1.0:
            return 1.0
        return max(0.0, min(1.0, round(score, 3)))

    def _brain_package_from_signal(self, signal: TechnicalSignal) -> dict:
        ticker = str(getattr(signal, "ticker", "UNKNOWN") or "UNKNOWN")
        metadata = getattr(signal, "metadata", {}) or {}
        market_ticker = self._canonical_market_ticker(ticker)
        cache_key = (market_ticker, "1d", "1m")
        df = self.market_data_cache.get(cache_key)
        recent_ohlcv = []
        if df is not None and not df.empty:
            for _, row in df.tail(10).iterrows():
                recent_ohlcv.append({
                    "open": float(row.get("Open", 0.0) or 0.0),
                    "high": float(row.get("High", 0.0) or 0.0),
                    "low": float(row.get("Low", 0.0) or 0.0),
                    "close": float(row.get("Close", 0.0) or 0.0),
                    "volume": float(row.get("Volume", 0.0) or 0.0),
                })

        return {
            "asset": ticker,
            "signal_type": str(getattr(signal, "signal_type", "SIGNAL") or "SIGNAL"),
            "technical_strength": float(getattr(signal, "strength", 0.0) or 0.0),
            "rsi": float(metadata.get("rsi", 50.0) or 50.0),
            "atr": float(metadata.get("atr", 0.0) or 0.0),
            "recent_ohlcv": recent_ohlcv,
            "liquidity_zones": metadata.get("liquidity_zones", []),
            "liquidity_zone_label": metadata.get("liquidity_zone_label", "N/A"),
            "regime_context": metadata.get("regime_context", "") or metadata.get("h1_reason", ""),
            "h1_analysis": metadata.get("h1_analysis", {}),
        }
    
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
            price = float(close.iloc[-1])
            vol_ratio = current_volume / max(1, avg_volume_current)
            trend_dir = "Bull" if sma_fast.iloc[-1] > sma_slow.iloc[-1] else "Bear"
            
            high = df["High"]
            low = df["Low"]
            prev_close = close.shift(1)
            true_range = pd.concat(
                [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
                axis=1,
            ).max(axis=1)
            atr = float(true_range.rolling(window=14, min_periods=1).mean().iloc[-1] or 0.0)
            stop_distance = atr * float(getattr(config, "ATR_STOP_MULTIPLIER", 1.5) or 1.5)
            
            # Bollinger Bands (mean reversion signals for ranging markets)
            bb = volatility.BollingerBands(close=close, window=20, window_dev=2)
            bb_high = bb.bollinger_hband()
            bb_low = bb.bollinger_lband()
            bb_pct = (price - bb_low.iloc[-1]) / max(0.01, bb_high.iloc[-1] - bb_low.iloc[-1])
            
            # MACD (momentum)
            macd = trend.MACD(close=close)
            macd_line = macd.macd()
            macd_signal = macd.macd_signal()
            macd_hist = macd.macd_diff()
            macd_cross_bull = (macd_line.iloc[-1] > macd_signal.iloc[-1] and
                               macd_line.iloc[-2] <= macd_signal.iloc[-2])
            macd_cross_bear = (macd_line.iloc[-1] < macd_signal.iloc[-1] and
                               macd_line.iloc[-2] >= macd_signal.iloc[-2])
            
            # ================================================================
            # FIBONACCI RETRACEMENT LEVELS
            # ================================================================
            # Find the recent swing high and swing low (last 50 candles)
            # Fib levels tell us where price is likely to bounce (support/resistance)
            lookback = min(50, len(close))
            recent_high = float(high.iloc[-lookback:].max())
            recent_low = float(low.iloc[-lookback:].min())
            fib_range = recent_high - recent_low
            
            # Key Fibonacci levels
            fib_236 = recent_high - (fib_range * 0.236)  # Shallow pullback
            fib_382 = recent_high - (fib_range * 0.382)  # Standard pullback
            fib_500 = recent_high - (fib_range * 0.500)  # Deep pullback
            fib_618 = recent_high - (fib_range * 0.618)  # Golden ratio pullback
            
            # Determine if price is near a Fibonacci level (within 0.5 ATR)
            fib_tolerance = atr * 0.5
            near_fib_support = (
                abs(price - fib_382) < fib_tolerance or
                abs(price - fib_500) < fib_tolerance or
                abs(price - fib_618) < fib_tolerance
            )
            near_fib_resistance = (
                abs(price - fib_236) < fib_tolerance or
                abs(price - fib_382) < fib_tolerance or
                abs(price - fib_500) < fib_tolerance
            )
            
            # Price position relative to Fibonacci (0=at low, 1=at high)
            fib_position = (price - recent_low) / max(fib_range, 0.01)
            
            # ================================================================
            # MOMENTUM TREND-FOLLOWING STRATEGY
            # ================================================================
            # Simple, proven logic:
            # 1. Check 5-MINUTE trend direction FIRST (bigger picture)
            # 2. Determine trend from 9 EMA vs 21 EMA (short-term momentum)
            # 3. Confirm with RSI (not overbought/oversold in trade direction)
            # 4. Require price momentum (recent candles confirming direction)
            # ================================================================
            
            # === 5-MINUTE TREND FILTER (the bigger picture) ===
            # Only BUY if 5-min trend is bullish. Only SELL if 5-min trend is bearish.
            htf_bullish = False
            htf_bearish = False
            try:
                df_5m = self._fetch_market_data(ticker, interval="5m")
                if df_5m is not None and len(df_5m) >= 20:
                    close_5m = df_5m["Close"]
                    ema9_5m = close_5m.ewm(span=9, adjust=False).mean()
                    ema21_5m = close_5m.ewm(span=21, adjust=False).mean()
                    htf_bullish = float(ema9_5m.iloc[-1]) > float(ema21_5m.iloc[-1])
                    htf_bearish = float(ema9_5m.iloc[-1]) < float(ema21_5m.iloc[-1])
            except Exception:
                # If 5m data unavailable, allow trade (don't block)
                htf_bullish = True
                htf_bearish = True

            # EMA for trend detection (1-minute)
            ema9 = close.ewm(span=9, adjust=False).mean()
            ema21 = close.ewm(span=21, adjust=False).mean()
            
            # Price action: last 5 candles direction
            recent_closes = close.iloc[-5:].tolist()
            rises = sum(1 for i in range(1, len(recent_closes)) if recent_closes[i] > recent_closes[i-1])
            falls = sum(1 for i in range(1, len(recent_closes)) if recent_closes[i] < recent_closes[i-1])
            
            # Trend state
            ema_bullish = ema9.iloc[-1] > ema21.iloc[-1]
            ema_bearish = ema9.iloc[-1] < ema21.iloc[-1]
            price_above_ema9 = price > ema9.iloc[-1]
            price_below_ema9 = price < ema9.iloc[-1]
            
            # MACD for momentum confirmation
            macd = trend.MACD(close=close)
            macd_hist = macd.macd_diff()
            macd_positive = macd_hist.iloc[-1] > 0
            macd_negative = macd_hist.iloc[-1] < 0
            macd_increasing = macd_hist.iloc[-1] > macd_hist.iloc[-2] if len(macd_hist) > 1 else False
            macd_decreasing = macd_hist.iloc[-1] < macd_hist.iloc[-2] if len(macd_hist) > 1 else False
            
            # Bollinger Bands for volatility context
            bb = volatility.BollingerBands(close=close, window=20, window_dev=2)
            bb_high = bb.bollinger_hband()
            bb_low = bb.bollinger_lband()
            
            # === BUY CONDITIONS ===
            # Trend is up + momentum + RSI safe zone + 5-MIN AGREES
            if (ema_bullish 
                and price_above_ema9 
                and rises >= 3
                and macd_positive 
                and macd_increasing
                and current_rsi < 65
                and current_rsi > 40
                and htf_bullish  # 5-minute trend must be bullish too
            ):
                signal_type = "MOMENTUM_BUY"
                fib_bonus = 0.15 if (near_fib_support or fib_position < 0.60) else 0.0
                strength = min(1.0, 0.7 + (rises / 8.0) + fib_bonus)
            
            # === SELL CONDITIONS ===
            # Trend is down + momentum + RSI safe zone + 5-MIN AGREES
            elif (ema_bearish 
                  and price_below_ema9 
                  and falls >= 3
                  and macd_negative 
                  and macd_decreasing
                  and current_rsi > 35
                  and current_rsi < 60
                  and htf_bearish  # 5-minute trend must be bearish too
            ):
                signal_type = "MOMENTUM_SELL"
                fib_bonus = 0.15 if (near_fib_resistance or fib_position > 0.40) else 0.0
                strength = min(1.0, 0.7 + (falls / 8.0) + fib_bonus)
            
            # === BREAKOUT BUY (strong move with volume) ===
            elif (price > bb_high.iloc[-1] 
                  and vol_ratio > 1.5 
                  and rises >= 3
                  and current_rsi > 55
                  and current_rsi < 80
            ):
                signal_type = "BREAKOUT_BUY"
                strength = min(1.0, 0.7 + vol_ratio / 10.0)
            
            # === BREAKOUT SELL (strong move with volume) ===
            elif (price < bb_low.iloc[-1] 
                  and vol_ratio > 1.5 
                  and falls >= 3
                  and current_rsi < 45
                  and current_rsi > 20
            ):
                signal_type = "BREAKOUT_SELL"
                strength = min(1.0, 0.7 + vol_ratio / 10.0)
            
            if signal_type:
                # Direction is embedded in signal name — no ambiguity
                if "BUY" in signal_type:
                    action = "BUY"
                elif "SELL" in signal_type:
                    action = "SELL"
                else:
                    action = "WAIT"

                # Skip H1 confirmation — it lags and causes wrong-direction trades
                # Instead, use signal stability (same direction 2 cycles = confirmed)
                stable, stability_count = self._signal_history_is_stable(ticker, action)
                if not stable:
                    if self.status_callback:
                        self.status_callback(
                            str(ticker),
                            f"confirming {action}: {stability_count}/{self.signal_stability_cycles} cycles"
                        )
                    return None

                return TechnicalSignal(
                    ticker=ticker,
                    signal_type=signal_type,
                    strength=strength,
                    metadata={
                        "rsi": current_rsi,
                        "sma_fast": sma_fast.iloc[-1],
                        "sma_slow": sma_slow.iloc[-1],
                        "volume_ratio": vol_ratio,
                        "atr": atr,
                        "stop_loss": round(price - stop_distance, 2) if action == "BUY" else round(price + stop_distance, 2),
                        "take_profit": round(price + (stop_distance * 2.5), 2) if action == "BUY" else round(price - (stop_distance * 2.5), 2),
                        "ema9": round(float(ema9.iloc[-1]), 2),
                        "ema21": round(float(ema21.iloc[-1]), 2),
                        "macd_hist": round(float(macd_hist.iloc[-1]), 4),
                        "rises": rises,
                        "falls": falls,
                        "stability_count": stability_count,
                        "stability_required": self.signal_stability_cycles,
                        "direction": action,
                    }
                )

            # === SOFT TIER: catch clear directional bias even when the strict
            # MOMENTUM/BREAKOUT signals don't fire. This is what the user sees
            # when they say "the market is choppy but I can see it's going up".
            # The soft signal still goes through stability + safety gates, so
            # we never get a one-tick trade.
            soft_action = self._soft_signal(
                ticker, df, close, ema9, ema21, current_rsi, trend_dir, vol_ratio,
            )
            if soft_action:
                stable, stability_count = self._signal_history_is_stable(ticker, soft_action)
                if not stable:
                    if self.status_callback:
                        self.status_callback(
                            str(ticker),
                            f"soft confirming {soft_action}: {stability_count}/{self.signal_stability_cycles}"
                        )
                    return None
                # Soft signal has lower strength (0.55-0.7) — the brain will
                # still gate it.
                if soft_action == "BUY":
                    soft_stop = round(price - stop_distance, 2)
                    soft_tp = round(price + (stop_distance * 2.0), 2)
                else:
                    soft_stop = round(price + stop_distance, 2)
                    soft_tp = round(price - (stop_distance * 2.0), 2)
                return TechnicalSignal(
                    ticker=ticker,
                    signal_type=f"SOFT_{soft_action}",
                    strength=0.62,
                    metadata={
                        "rsi": current_rsi,
                        "sma_fast": sma_fast.iloc[-1],
                        "sma_slow": sma_slow.iloc[-1],
                        "volume_ratio": vol_ratio,
                        "atr": atr,
                        "stop_loss": soft_stop,
                        "take_profit": soft_tp,
                        "ema9": round(float(ema9.iloc[-1]), 2),
                        "ema21": round(float(ema21.iloc[-1]), 2),
                        "macd_hist": round(float(macd_hist.iloc[-1]), 4),
                        "rises": rises,
                        "falls": falls,
                        "stability_count": stability_count,
                        "stability_required": self.signal_stability_cycles,
                        "direction": soft_action,
                        "soft_tier": True,
                    },
                )

            # No signal — emit status
            if self.status_callback:
                dir_label = "↑ BULL" if ema_bullish else "↓ BEAR" if ema_bearish else "— FLAT"
                self.status_callback(
                    str(ticker),
                    f"RSI {current_rsi:.0f} | {dir_label} | ${price:,.2f} | Vol {vol_ratio:.1f}x | R{rises}/F{falls}"
                )
            self._clear_signal_history(ticker)
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
        """Fetch OHLCV market data via smart hybrid feed (MT5 → Yahoo → cache)."""
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
        if interval != "1m":
            count = max(80, 240 // interval_minutes)
        
        # ---- SMART HYBRID: use unified data feed (MT5 → Yahoo → cache). ----
        # Higher timeframes use yfinance/MT5 multi-timeframe bars instead of
        # 1-minute bars, so the 1H dropdown filter is real directional context.
        if interval != "1m":
            bars = data_feed.get_bars_multi(ticker, interval=interval, count=count)
            if bars:
                df = pd.DataFrame(bars)
                df["time"] = pd.to_datetime(df["timestamp"])
                df = df.rename(columns={
                    "open": "Open", "high": "High", "low": "Low",
                    "close": "Close", "volume": "Volume",
                })
                df = df.set_index("time")[["Open", "High", "Low", "Close", "Volume"]]
                self.market_data_cache[cache_key] = df.copy()
                self.last_close_cache[market_ticker] = float(df["Close"].dropna().iloc[-1])
                return df
            logger.debug("[SCANNER] Multi-timeframe feed empty, trying legacy fallback for %s @ %s", ticker, interval)
            return self._fallback_market_data(ticker, market_ticker, period, interval, cache_key)
        
        # Crypto: Yahoo Finance is the only path
        if is_crypto_ticker(ticker):
            yf_bars = data_feed.get_bars(ticker, count=count, use_cache=True)
            if yf_bars:
                df = pd.DataFrame(yf_bars)
                df["time"] = pd.to_datetime(df["timestamp"])
                df = df.rename(columns={
                    "open": "Open", "high": "High", "low": "Low",
                    "close": "Close", "volume": "Volume",
                })
                df = df.set_index("time")[["Open", "High", "Low", "Close", "Volume"]]
                self.market_data_cache[cache_key] = df.copy()
                self.last_close_cache[market_ticker] = float(df["Close"].dropna().iloc[-1])
                return df
            logger.warning("[WARN] Crypto data unavailable for %s - Skipping Cycle", ticker)
            return None
        
        # All other instruments: smart hybrid (MT5 first, Yahoo fallback)
        hybrid_bars = data_feed.get_bars(ticker, count=count, use_cache=True)
        if hybrid_bars:
            df = pd.DataFrame(hybrid_bars)
            df["time"] = pd.to_datetime(df["timestamp"])
            df = df.rename(columns={
                "open": "Open", "high": "High", "low": "Low",
                "close": "Close", "volume": "Volume",
            })
            df = df.set_index("time")[["Open", "High", "Low", "Close", "Volume"]]
            self.market_data_cache[cache_key] = df.copy()
            self.last_close_cache[market_ticker] = float(df["Close"].dropna().iloc[-1])
            return df
        
# Last resort: existing fallback path
        logger.debug("[SCANNER] Hybrid feed empty, trying legacy fallback for %s", ticker)
        return self._fallback_market_data(ticker, market_ticker, period, interval, cache_key)
    
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
        """Return cached bars if available, otherwise try yfinance as last resort."""
        cached = self.market_data_cache.get(cache_key)
        if cached is not None and not cached.empty:
            return cached.copy()
        
        try:
            import yfinance as yf
            yf_map = {
                "MNQ1!": "NQ=F", "MNQ": "NQ=F", "NQM6": "NQ=F",
                "MES1!": "ES=F", "MES": "ES=F", "ESM6": "ES=F",
                "MCL1!": "CL=F", "MCL": "CL=F",
                "MGC1!": "GC=F", "MGC": "GC=F",
            }
            yf_sym = yf_map.get(ticker, yf_map.get(market_ticker, ticker))
            yf_interval = "1h" if interval == "1h" else "1m"
            yf_period = "3mo" if yf_interval == "1h" else "1d"
            
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as ex:
                future = ex.submit(
                    yf.download, yf_sym, period=yf_period, interval=yf_interval,
                    progress=False, threads=False
                )
                raw = future.result(timeout=25)
            
            if raw is not None and not raw.empty:
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = raw.columns.get_level_values(0)
                raw = raw.rename(columns={
                    "Open": "Open", "High": "High",
                    "Low": "Low", "Close": "Close", "Volume": "Volume",
                })
                self.market_data_cache[cache_key] = raw.copy()
                return raw.copy()
        except Exception as e:
            logger.warning("[SCANNER] YFinance fallback failed for %s: %s", ticker, e)
        
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
