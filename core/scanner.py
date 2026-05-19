"""
VcanTrade AI - Cloud Market Scanner
Monitors chosen counters using MetaTrader 5 or yfinance data.
Detects technical signals (RSI Cross, Volume Spike, SMA Cross) and triggers Swarm Debate.

Architecture:
- Runs on Vast.ai server (headless) or local Windows machine
- Uses MetaTrader5 (mt5.copy_rates_from_pos) when EXECUTION_MODE == "MT5"
- Falls back to cached data when MT5 is unavailable (UI/Tradovate mode)
- Calculates technical indicators (RSI, SMA, Volume)
- Triggers Swarm Consensus when signal detected
- Dispatches high-confidence signals (>0.70) to Local Executor
"""

import logging
import time
import asyncio
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


class CloudScanner:
    """
    Cloud-based market scanner that monitors tickers using MetaTrader 5 data.
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
        self.liquidity_engine = LiquidityEngine()
        self.recent_signals: Dict[str, datetime] = {}  # Legacy signal-type cooldown state
        self.last_signal_timestamps: Dict[str, datetime] = {}
        self.signal_cooldown = int(getattr(config, "SIGNAL_COOLDOWN_SECONDS", 300) or 300)
        self.status_callback = None
        self.market_data_cache: Dict[Tuple[str, str, str], pd.DataFrame] = {}
        self.last_close_cache: Dict[str, float] = {}

        # Circuit Breaker - prevents silent failures during API outages
        self.consecutive_errors = 0
        self.max_consecutive_errors = 20  # ~10 minutes of errors before alert
        self.last_successful_scan = None
        self.error_alert_threshold = 10  # Alert user after this many errors
        self.dispatch_failure_streak = 0
        self.dispatch_alert_emitted = False
        self.last_dispatch_error_message = ""
        self.last_dispatch_status_code: Optional[int] = None
        self.last_dispatch_target = ""
        self.public_dispatch_retry_after_ts = 0.0
        self.dispatcher = AsyncSignalDispatcher()

        # Market Session Awareness
        self.session_detector = MarketSessionDetector()
        self.structure_analyzer = MultiTimeframeStructureAnalyzer(
            zone_atr_multiplier=float(getattr(config, "MTF_STRUCTURE_ZONE_ATR_MULTIPLIER", 0.65)),
            proximity_pct=float(getattr(config, "MTF_STRUCTURE_PROXIMITY_PCT", 0.0025)),
        )

        # Dynamic Eye Sync — tracks what the Browser Agent is currently viewing
        self.eye_symbol: Optional[str] = None
        self.eye_symbol_at: Optional[datetime] = None
        self.eye_ttl_seconds = 300  # Eye symbol expires after 5 min if not refreshed

        # Initialize MT5 connection only when in MT5 mode
        self._mt5_initialized = False
        _active_mode = config.get_active_mode()
        logger.info(f"[CLOUD] Active mode: {_active_mode}")
        if _active_mode == "MT5":
            self._ensure_mt5()
        else:
            logger.info("[CLOUD] TradingView mode detected — MT5 initialization skipped")

        logger.info("[CLOUD] Cloud Scanner initialized with Market Session Awareness")
        logger.info(
            f"Circuit breaker: {self.max_consecutive_errors} consecutive errors before alert"
        )

    def _ensure_mt5(self) -> bool:
        """Lazy-initialize MetaTrader 5 connection."""
        if self._mt5_initialized:
            return True
        _mt5 = _lazy_mt5()
        if _mt5 is False:
            logger.debug("[MT5] MetaTrader5 not installed — skipping initialization")
            return False
        try:
            if not _mt5.initialize():
                err = _mt5.last_error()
                logger.error("[MT5] initialize() failed: %s", err)
                return False
            self._mt5_initialized = True
            account = _mt5.account_info()
            if account:
                logger.info(
                    "[MT5] Connected | Account: %s | Balance: %.2f %s",
                    account.login,
                    account.balance,
                    account.currency,
                )
            else:
                logger.warning("[MT5] Connected but no account info retrieved")
            self._warm_up_mt5_symbols()
            return True
        except Exception as e:
            logger.error("[MT5] Initialization error: %s", e)
            return False

    def _add_symbol_candidate(self, candidates: List[str], symbol: str) -> None:
        """Append a broker symbol candidate once, preserving order."""
        value = str(symbol or "").strip()
        if value and value not in candidates:
            candidates.append(value)

    def _mt5_symbol_candidates(self, ticker: str) -> List[str]:
        """Return broker-symbol candidates for an MT5 MarketWatch lookup.
        SYMBOL BRIDGE: includes SYMBOL_MAP, SYMBOL_BRIDGE_CANDIDATES, and canonical forms."""
        candidates: List[str] = []
        raw = str(ticker or "").strip()

        if raw:
            self._add_symbol_candidate(candidates, raw)

        # Symbol Bridge: primary map
        symbol_map = getattr(config, "SYMBOL_MAP", {}) or {}
        mapped = symbol_map.get(raw) or symbol_map.get(raw.upper()) or ""
        self._add_symbol_candidate(candidates, mapped)

        # Symbol Bridge: extra candidates
        bridge_candidates = getattr(config, "SYMBOL_BRIDGE_CANDIDATES", {}) or {}
        for extra in bridge_candidates.get(raw, bridge_candidates.get(raw.upper(), ())):
            self._add_symbol_candidate(candidates, extra)

        # Legacy MT5_SYMBOL_MAP
        mt5_map = getattr(config, "MT5_SYMBOL_MAP", {}) or {}
        if raw:
            legacy_mapped = mt5_map.get(raw) or mt5_map.get(raw.upper()) or ""
            self._add_symbol_candidate(candidates, legacy_mapped)

        canonical = self._canonical_market_ticker(raw) if raw else ""
        self._add_symbol_candidate(candidates, canonical)
        if canonical:
            self._add_symbol_candidate(candidates, mt5_map.get(canonical, ""))
            self._add_symbol_candidate(candidates, mt5_map.get(canonical.upper(), ""))
            self._add_symbol_candidate(candidates, symbol_map.get(canonical, ""))

        translation = translate_chart_symbol(raw) or translate_chart_symbol(canonical)
        if translation:
            self._add_symbol_candidate(candidates, translation.mt5_symbol)
            self._add_symbol_candidate(candidates, translation.yahoo_symbol)

        for symbol in list(candidates):
            upper = symbol.upper()
            if upper.endswith("=F"):
                self._add_symbol_candidate(candidates, symbol[:-2])
            if "=" in symbol:
                self._add_symbol_candidate(candidates, symbol.split("=", 1)[0])
            if ":" in symbol:
                self._add_symbol_candidate(candidates, symbol.split(":", 1)[-1])
            if upper.endswith("1!"):
                self._add_symbol_candidate(candidates, symbol[:-2])
            if upper.endswith("!"):
                self._add_symbol_candidate(candidates, symbol[:-1])

        return candidates

    def _select_mt5_symbol(self, ticker: str) -> Optional[str]:
        """Select the first available MT5 symbol into MarketWatch.
        SYMBOL BRIDGE: tries exact candidates first, then fuzzy MarketWatch search."""
        _mt5 = _lazy_mt5()
        if _mt5 is False:
            return None

        # Phase 1: Try exact candidates from SYMBOL_MAP + SYMBOL_BRIDGE_CANDIDATES
        for candidate in self._mt5_symbol_candidates(ticker):
            try:
                info = _mt5.symbol_info(candidate)
                if info is not None:
                    broker_name = getattr(info, "name", candidate) or candidate
                    if _mt5.symbol_select(broker_name, True):
                        logger.info("[MT5-BRIDGE] Resolved %s -> %s (exact candidate)", ticker, broker_name)
                        return broker_name
            except Exception as exc:
                logger.debug("[MT5] symbol_info/select(%s) failed: %s", candidate, exc)

        # Phase 2: Fuzzy MarketWatch search using SYMBOL_FUZZY_TERMS
        fuzzy_result = self._fuzzy_find_mt5_symbol(ticker, _mt5)
        if fuzzy_result:
            return fuzzy_result

        return None

    def _fuzzy_find_mt5_symbol(self, wealthcharts_symbol: str, _mt5) -> Optional[str]:
        """Fuzzy search MT5 MarketWatch using SYMBOL_FUZZY_TERMS.
        Scans symbol name, description, and path for matching terms."""
        bridge_terms = getattr(config, "SYMBOL_FUZZY_TERMS", {})
        terms = list(bridge_terms.get(wealthcharts_symbol, ()))
        if not terms:
            terms = [wealthcharts_symbol]
        terms_lower = [t.casefold() for t in terms if t]

        try:
            all_symbols = list(_mt5.symbols_get() or ())
        except Exception:
            logger.debug("[MT5-FUZZY] symbols_get() failed for %s", wealthcharts_symbol, exc_info=True)
            return None

        # Prefer visible symbols, fall back to all
        visible = [s for s in all_symbols if getattr(s, "visible", False)]
        pool = visible or all_symbols

        matches = []
        for sym in pool:
            name = getattr(sym, "name", None)
            if not name:
                continue
            search_text = " ".join(
                str(getattr(sym, field, "") or "")
                for field in ("name", "description", "path", "currency_base", "currency_profit")
            ).casefold()

            score = 0
            for term in terms_lower:
                if not term:
                    continue
                name_lower = name.casefold()
                if name_lower == term:
                    score += 100
                elif name_lower.startswith(term):
                    score += 40
                elif term in search_text:
                    score += 20

            if score > 0:
                matches.append((-score, len(name), name))

        if not matches:
            return None

        matches.sort()
        for _, _, name in matches:
            try:
                if _mt5.symbol_select(name, True):
                    logger.info("[MT5-FUZZY] Resolved %s -> %s (fuzzy match)", wealthcharts_symbol, name)
                    return name
            except Exception:
                continue

        return None

    def _warm_up_mt5_symbols(self) -> None:
        """Pre-select configured scanner symbols so MT5 MarketWatch is ready."""
        watchlist: List[str] = []
        muted = getattr(config, "MUTED_TICKERS", set())
        for attr in ("CLOUD_TICKERS", "WATCHLIST", "MULTI_ASSET_TICKERS"):
            values = getattr(config, attr, None)
            if isinstance(values, (list, tuple, set)):
                watchlist.extend(str(value).strip() for value in values if str(value).strip())
        watchlist.extend(str(ticker).strip() for ticker in self.tickers if str(ticker).strip())

        seen = set()
        for ticker in watchlist:
            if ticker in seen or ticker in muted:
                continue
            seen.add(ticker)
            selected = self._select_mt5_symbol(ticker)
            if selected:
                logger.info("[MT5] MarketWatch warm-up: %s -> %s", ticker, selected)
            else:
                logger.warning(
                    "[MT5] MarketWatch warm-up failed for %s (tried: %s)",
                    ticker,
                    ", ".join(self._mt5_symbol_candidates(ticker)),
                )

    def _mt5_timeframe(self, interval: str) -> Optional[int]:
        """Map string interval to MT5 timeframe constant. Returns None if MT5 unavailable."""
        _mt5 = _lazy_mt5()
        if _mt5 is False:
            return None
        mapping = {
            "1m": _mt5.TIMEFRAME_M1,
            "3m": _mt5.TIMEFRAME_M3,
            "5m": _mt5.TIMEFRAME_M5,
            "15m": _mt5.TIMEFRAME_M15,
            "30m": _mt5.TIMEFRAME_M30,
            "1h": _mt5.TIMEFRAME_H1,
            "4h": _mt5.TIMEFRAME_H4,
            "1d": _mt5.TIMEFRAME_D1,
        }
        return mapping.get(interval, _mt5.TIMEFRAME_M1)

    def set_runtime_context(
        self, mode: str, dashboard_tickers: Optional[List[str]] = None
    ):
        """Keep session awareness aligned with the operator's live dashboard state."""
        self.session_detector.set_runtime_context(mode, dashboard_tickers)

    def set_eye_symbol(self, symbol: str) -> None:
        """Tell the scanner what the Browser Agent is currently looking at."""
        if symbol and str(symbol).strip():
            self.eye_symbol = str(symbol).strip().upper()
            self.eye_symbol_at = datetime.utcnow()
            logger.info("[EYE] Scanner synced to active chart: %s", self.eye_symbol)

    def _get_active_scan_list(self) -> List[str]:
        """Return sniper-priority list when configured; otherwise session-filtered tickers."""
        muted = getattr(config, "MUTED_TICKERS", set())
        if self.priority_scan_list:
            candidates = [t for t in self.priority_scan_list if t not in muted]
        else:
            candidates = [ticker for ticker in self.tickers if str(ticker).strip() and ticker not in muted]

        # Dynamic Eye Sync: if Browser Agent is showing a symbol, prioritize it
        eye = None
        if self.eye_symbol and self.eye_symbol_at:
            age = (datetime.utcnow() - self.eye_symbol_at).total_seconds()
            if age <= self.eye_ttl_seconds:
                eye = self.eye_symbol
            else:
                logger.debug("[EYE] Eye symbol %s expired (%.0fs old)", self.eye_symbol, age)
                self.eye_symbol = None

        # Weekend Silence: disable all Futures / Commodities / Stocks / Forex on Sat/Sun
        # Automatic Switchboard Flip: Sunday 23:00 UTC resumes normal scanning
        is_weekend = self.session_detector.is_weekend_mode()
        if is_weekend:
            filtered = [t for t in candidates if not is_weekend_closed(t)]
            skipped = [t for t in candidates if is_weekend_closed(t)]
            if skipped:
                logger.info(
                    "[CLOCK] [WEEKEND SKIP] Skipping %d closed-market symbols: %s",
                    len(skipped),
                    ", ".join(skipped[:8]),
                )
            candidates = filtered

        # If eye symbol is not already in the list, prepend it for immediate scan
        if eye and eye not in {t.upper() for t in candidates}:
            # Map eye symbol to a yfinance-compatible ticker if possible
            eye_ticker = eye
            if hasattr(config, "SYMBOL_MAP") and eye in config.SYMBOL_MAP:
                eye_ticker = config.SYMBOL_MAP[eye]
            elif eye in {"BTC", "BTCUSD", "XBT"}:
                eye_ticker = "BTC-USD"
            elif eye in {"ETH", "ETHUSD"}:
                eye_ticker = "ETH-USD"
            elif eye in {"SOL", "SOLUSD"}:
                eye_ticker = "SOL-USD"
            elif eye in {"XRP", "XRPUSD"}:
                eye_ticker = "XRP-USD"
            candidates = [eye_ticker] + candidates
            logger.info("[EYE] Prioritizing active chart symbol: %s", eye_ticker)

        return candidates

    def _emit_status(self, ticker: str, status: str):
        """Emit optional per-ticker status updates for dashboard/mirror feedback."""
        if self.status_callback:
            try:
                self.status_callback(ticker, status)
            except Exception as e:
                logger.debug(f"Status callback failed for {ticker}/{status}: {e}")

    def _canonical_market_ticker(self, ticker: str) -> str:
        """Map watchlist aliases to yfinance-compatible symbols."""
        raw = str(ticker or "").strip()
        yf_map = getattr(config, "YFINANCE_SYMBOL_MAP", {})
        for candidate in (raw, raw.upper(), raw.replace("-", "").upper()):
            if candidate in yf_map:
                return yf_map[candidate]

        # 1. Check explicit TradingView -> Yahoo Finance mapping
        if hasattr(config, "SYMBOL_MAP") and raw in config.SYMBOL_MAP:
            return normalize_yfinance_symbol(config.SYMBOL_MAP[raw])
        # 2. Normalize via settings aliases
        normalized = settings_manager.normalize_ticker(raw)
        for candidate in (normalized, normalized.upper(), normalized.replace("-", "").upper()):
            if candidate in yf_map:
                return yf_map[candidate]
        if hasattr(config, "SYMBOL_MAP") and normalized in config.SYMBOL_MAP:
            return normalize_yfinance_symbol(config.SYMBOL_MAP[normalized])
        # 3. Broker/chart labels like MNQ-JUN26 are translated automatically.
        translation = translate_chart_symbol(ticker) or translate_chart_symbol(normalized)
        if translation:
            return translation.yahoo_symbol
        # 4. Fall back to normalized ticker
        return normalize_yfinance_symbol(normalized)

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

    def _build_signal_endpoint(self, base_or_full_url: str) -> str:
        """Normalize a configured base URL into the /api/signal endpoint."""
        raw = str(base_or_full_url or "").strip().rstrip("/")
        if not raw:
            return ""
        if raw.endswith("/api/signal"):
            return raw
        parts = urlsplit(raw)
        path = parts.path.rstrip("/")
        if path.endswith("/api"):
            path = f"{path}/signal"
        else:
            path = f"{path}/api/signal"
        return urlunsplit(
            (parts.scheme, parts.netloc, path, parts.query, parts.fragment)
        )

    def _urls_equivalent(self, left: str, right: str) -> bool:
        """Treat localhost aliases on the same port/path as the same dispatch endpoint."""
        if left == right:
            return True
        left_parts = urlsplit(left)
        right_parts = urlsplit(right)
        left_port = left_parts.port or (443 if left_parts.scheme == "https" else 80)
        right_port = right_parts.port or (443 if right_parts.scheme == "https" else 80)
        left_host = (left_parts.hostname or "").lower()
        right_host = (right_parts.hostname or "").lower()
        localhost_aliases = {"localhost", "127.0.0.1", "::1"}
        hosts_match = left_host == right_host or (
            left_host in localhost_aliases and right_host in localhost_aliases
        )
        return (
            left_parts.scheme == right_parts.scheme
            and hosts_match
            and left_port == right_port
            and left_parts.path.rstrip("/") == right_parts.path.rstrip("/")
        )

    def _get_dispatch_targets(self) -> List[Tuple[str, str]]:
        """Return ordered dispatch targets with bridge priority and localhost fallback."""
        targets: List[Tuple[str, str]] = []
        public_url = self._build_signal_endpoint(
            getattr(config, "PUBLIC_SIGNAL_URL", "")
        )
        local_host = getattr(config, "LOCAL_LISTENER_HEALTH_HOST", "127.0.0.1")
        local_url = self._build_signal_endpoint(
            f"http://{local_host}:{config.LOCAL_LISTENER_PORT}"
        )
        now = time.time()

        if public_url and now >= self.public_dispatch_retry_after_ts:
            targets.append(("bridge", public_url))

        if local_url and not any(
            self._urls_equivalent(local_url, url) for _, url in targets
        ):
            targets.append(("local", local_url))

        legacy_url = self._build_signal_endpoint(
            getattr(config, "CLOUD_SCANNER_URL", "")
        )
        if legacy_url and not any(
            self._urls_equivalent(legacy_url, url) for _, url in targets
        ):
            targets.append(("legacy", legacy_url))

        return targets

    async def scan_all_tickers(self) -> List[TechnicalSignal]:
        """Scan all tickers and return detected signals."""
        signals = []

        # Apply sniper priority list first; otherwise normal session filtering.
        active_tickers = self._get_active_scan_list()
        session_context = self.session_detector.get_session_context()

        # Log session info
        if self.priority_scan_list:
            logger.info(
                f"[TARGET] [SNIPER MODE] Priority list active. "
                f"Scanning only: {', '.join(active_tickers)}"
            )
        elif session_context.get("dashboard_override_active"):
            logger.info(
                "[CLOCK] [OVERRIDE MODE] Weekend silence bypassed. Scanning dashboard tickers: %s",
                ", ".join(active_tickers),
            )
        elif self.session_detector.is_weekend():
            logger.info(
                f"[CLOCK] [WEEKEND MODE] Markets closed. "
                f"Scanning {len(active_tickers)} crypto tickers only."
            )
        else:
            logger.debug(
                f"[CLOCK] [SESSION] {session_context['primary_session']} | "
                f"Scanning {len(active_tickers)} tickers"
            )

        for ticker in active_tickers:
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
        direction, price_change_pct = self._compute_directional_bias(df)
        latest_rsi = self._extract_latest_rsi(df)
        logger.info(
            "[SCANNER] %s Direction: %s (%+.2f%%) | RSI: %.0f",
            ticker,
            direction,
            price_change_pct,
            latest_rsi,
        )
        liquidity_zone = self._detect_liquidity_zone(df, ticker)
        # Enhanced Smart Money liquidity analysis
        smart_money = self.liquidity_engine.analyze(df, ticker)
        smart_money_dict = self.liquidity_engine.to_dict(smart_money)
        brain_package = self._build_brain_package(df, liquidity_zone)
        brain_package["smart_money"] = smart_money_dict
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

        # PRICE SPIKE: sudden large move in recent bars
        price_spike = self._detect_price_spike(df, ticker)
        if price_spike:
            price_spike.metadata.update(brain_package)
            price_spike.metadata["liquidity_zone"] = liquidity_zone
            signals.append(price_spike)

        sma_signal = self._detect_sma_cross(df, ticker)
        if sma_signal:
            sma_signal.metadata.update(brain_package)
            sma_signal.metadata["liquidity_zone"] = liquidity_zone
            signals.append(sma_signal)

        return signals

    def _compute_directional_bias(self, df: pd.DataFrame) -> Tuple[str, float]:
        """Measure directional strength from the last 10 closes."""
        if df is None or df.empty or "Close" not in df:
            return "FLAT", 0.0

        recent_closes = df["Close"].dropna().tail(10)
        if recent_closes.empty:
            return "FLAT", 0.0

        start_price = float(recent_closes.iloc[0] or 0.0)
        end_price = float(recent_closes.iloc[-1] or 0.0)
        if start_price <= 0:
            return "FLAT", 0.0

        price_change_pct = ((end_price - start_price) / start_price) * 100.0
        if price_change_pct > 0.01:
            direction = "UP"
        elif price_change_pct < -0.01:
            direction = "DOWN"
        else:
            direction = "FLAT"
        return direction, price_change_pct

    def _extract_latest_rsi(self, df: pd.DataFrame) -> float:
        """Return the latest RSI value or a neutral fallback."""
        if df is None or df.empty or "RSI" not in df:
            return 50.0

        recent_rsi = df["RSI"].dropna()
        if recent_rsi.empty:
            return 50.0
        return float(recent_rsi.iloc[-1])

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
                zone_width = max(abs(zone_level) * 0.0015, tolerance, 0.5)
                zone = {
                    "type": zone_type,
                    "level": zone_level,
                    "low": round(zone_level - zone_width, 4),
                    "high": round(zone_level + zone_width, 4),
                    "zone_width": float(zone_width),
                    "start_index": min(index, index_2),
                    "end_index": max(index, index_2),
                    "current_price": float(current_price),
                    "label": "LIQUIDITY TARGET",
                    "distance": float(distance),
                }
                if best_zone is None:
                    best_zone = zone
                elif zone["distance"] < best_zone["distance"]:
                    best_zone = zone

        if best_zone:
            logger.info(
                "[DROP] Liquidity zone detected for %s: %s @ %.4f",
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

    def _liquidity_zone_stop_boundary(
        self,
        action: str,
        entry_price: float,
        liquidity_zone: Optional[dict],
    ) -> float:
        """Derive a structure-based stop from the detected liquidity boundary."""
        if not liquidity_zone or entry_price <= 0:
            return 0.0

        action_up = str(action or "").upper()
        low = float(liquidity_zone.get("low", 0.0) or 0.0)
        high = float(liquidity_zone.get("high", 0.0) or 0.0)
        level = float(liquidity_zone.get("level", 0.0) or 0.0)

        if action_up == "BUY":
            for candidate in (low, level):
                if 0 < candidate < entry_price:
                    return candidate
        elif action_up == "SELL":
            for candidate in (high, level):
                if candidate > entry_price:
                    return candidate

        return 0.0

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

        prev_close = float(df["Close"].iloc[-2]) if len(df) >= 2 else candle["close"]
        recent_closes = df["Close"].dropna().tail(4)
        recent_momentum_pct = 0.0
        if len(recent_closes) >= 2 and float(recent_closes.iloc[0]) > 0:
            recent_momentum_pct = (
                (float(recent_closes.iloc[-1]) - float(recent_closes.iloc[0]))
                / float(recent_closes.iloc[0])
            ) * 100.0

        rsi_value = float(last.get("RSI", 50.0) or 50.0)
        atr_value = float(last.get("ATR", 0.0) or 0.0)
        distance_ratio = abs(candle["close"] - zone_level) / max(abs(zone_level), 1.0)
        zone_width = max(
            abs(zone_level) * 0.0015, atr_value * 0.25 if atr_value > 0 else 0.0, 0.5
        )

        demand_zones = []
        supply_zones = []
        if zone_type == "equal_highs":
            supply_zones.append(
                {
                    "low": zone_level - zone_width,
                    "high": zone_level + zone_width,
                    "strength": 0.7,
                }
            )
        else:
            demand_zones.append(
                {
                    "low": zone_level - zone_width,
                    "high": zone_level + zone_width,
                    "strength": 0.7,
                }
            )

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
            near_supply = (
                zone_type == "equal_highs"
                and candle["high"] >= (zone_level - zone_width)
                and candle["close"] < candle["open"]
            )
            near_demand = (
                zone_type == "swing_lows"
                and candle["low"] <= (zone_level + zone_width)
                and candle["close"] > candle["open"]
            )
            if near_supply:
                bias = "SELL"
                signal_type = "LIQUIDITY_REJECTION_SELL"
            elif near_demand:
                bias = "BUY"
                signal_type = "LIQUIDITY_REJECTION_BUY"
            else:
                return None

            strength = max(0.60, min(0.85, 0.85 - (distance_ratio * 100)))

        # SNIPER ENTRY: Light validation — let the brain decide, don't over-filter.
        # Old logic required 15% wick + single-candle momentum confirmation.
        # That killed 99% of signals on 1-minute futures. Now we only block
        # the most obvious counter-trend entries (strong 4-bar momentum against us).
        candle_range = candle["high"] - candle["low"]
        if candle_range > 0 and bias:
            if bias == "SELL" and recent_momentum_pct > 0.05:
                # Only block SELL if there's STRONG bullish momentum (>0.05% in 4 bars)
                logger.info(
                    "[SNIPER] Rejected %s %s: shorting into strong bullish 4-bar momentum (%+.2f%%)",
                    ticker, signal_type, recent_momentum_pct,
                )
                return None
            elif bias == "BUY" and recent_momentum_pct < -0.05:
                # Only block BUY if there's STRONG bearish momentum
                logger.info(
                    "[SNIPER] Rejected %s %s: buying into strong bearish 4-bar momentum (%+.2f%%)",
                    ticker, signal_type, recent_momentum_pct,
                )
                return None

        logger.info(
            "[TARGET] Liquidity trigger armed for %s: %s near %s",
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
                "liquidity_zone_label": self._format_liquidity_zone_label(
                    liquidity_zone
                ),
            },
        )

    async def _fetch_market_data(
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
            yf_df = await self._fetch_crypto_yfinance(ticker, market_ticker, period, interval, cache_key)
            if yf_df is not None and not yf_df.empty:
                return yf_df
            fallback = self._fallback_market_data(ticker, market_ticker, period, interval, cache_key)
            if fallback is not None:
                return fallback
            logger.warning("[WARN] Crypto data unavailable for %s - Skipping Cycle", ticker)
            return None

        # Fast path: MT5 unavailable (UI mode or not installed) — skip directly to fallback
        if mt5_tf is None:
            fallback = self._fallback_market_data(ticker, market_ticker, period, interval, cache_key)
            if fallback is not None:
                return fallback
            logger.warning("[WARN] MT5 unavailable - Skipping Cycle for %s", ticker)
            return None

        # Futures/Forex on weekends: markets are closed, skip MT5 noise entirely
        if self.session_detector.is_weekend() and is_weekend_closed(ticker):
            fallback = self._fallback_market_data(ticker, market_ticker, period, interval, cache_key)
            if fallback is not None:
                return fallback
            logger.info("[CLOCK] [WEEKEND SKIP] %s market closed - Skipping Cycle", ticker)
            return None

        # FORCE symbol active BEFORE retry loop: keep Pepperstone symbol in MarketWatch
        # MT5 can silently drop symbols between cycles if not explicitly selected each time.
        forced_symbol = None
        if mt5_tf is not None:
            try:
                _mt5_force = _lazy_mt5()
                if _mt5_force is not False:
                    forced_symbol = self._select_mt5_symbol(market_ticker)
                    if forced_symbol:
                        logger.debug("[MT5] Pre-cycle symbol refresh: %s active", forced_symbol)
                    else:
                        logger.debug("[MT5] Pre-cycle symbol NOT selectable for %s", market_ticker)
            except Exception:
                pass

        for attempt in range(max_retries):
            try:
                if not self._ensure_mt5():
                    # MT5 terminal not running — retry once then abort
                    if attempt < 1:
                        await asyncio.sleep(retry_delay)
                        continue
                    fallback = self._fallback_market_data(ticker, market_ticker, period, interval, cache_key)
                    if fallback is not None:
                        return fallback
                    logger.error("[WARN] MT5 not initialized - Skipping Cycle for %s", ticker)
                    return None

                _mt5 = _lazy_mt5()
                selected_symbol = forced_symbol or self._select_mt5_symbol(market_ticker)
                if not selected_symbol:
                    tried = ", ".join(self._mt5_symbol_candidates(market_ticker))
                    logger.warning(
                        "[WARN] Symbol %s not selectable in MarketWatch (tried: %s) - Skipping Cycle",
                        market_ticker,
                        tried,
                    )
                    fallback = self._fallback_market_data(ticker, market_ticker, period, interval, cache_key)
                    if fallback is not None:
                        return fallback
                    logger.error("[WARN] Symbol %s unavailable in MT5 - Skipping Cycle", ticker)
                    return None

                symbol_info = _mt5.symbol_info(selected_symbol)
                if symbol_info is None:
                    logger.warning(
                        "[WARN] Symbol %s selected but has no MT5 info - Skipping Cycle",
                        selected_symbol,
                    )
                    fallback = self._fallback_market_data(ticker, market_ticker, period, interval, cache_key)
                    if fallback is not None:
                        return fallback
                    logger.error("[WARN] Symbol %s unavailable in MT5 - Skipping Cycle", ticker)
                    return None

                if selected_symbol != market_ticker:
                    logger.info("[MT5] Using broker symbol %s for %s", selected_symbol, market_ticker)

                def _fetch():
                    rates = _mt5.copy_rates_from_pos(selected_symbol, mt5_tf, 0, count)
                    return rates

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(_fetch)
                    rates = future.result(timeout=15)

                if rates is None or len(rates) == 0:
                    if attempt < max_retries - 1:
                        logger.warning(
                            "Empty MT5 data for %s (attempt %d/%d) - retrying...",
                            market_ticker, attempt + 1, max_retries
                        )
                        await asyncio.sleep(retry_delay)
                        continue
                    fallback = self._fallback_market_data(ticker, market_ticker, period, interval, cache_key)
                    if fallback is not None:
                        return fallback
                    logger.error("[WARN] Market Data Timeout - Skipping Cycle for %s", ticker)
                    return None

                df = pd.DataFrame(rates)
                # MT5 returns time as seconds since epoch; convert to datetime
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

                if df.empty or df["Close"].iloc[-1] is None:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        continue
                    fallback = self._fallback_market_data(ticker, market_ticker, period, interval, cache_key)
                    if fallback is not None:
                        return fallback
                    logger.error("[WARN] Market Data Timeout - Skipping Cycle for %s", ticker)
                    return None

                sanitized = df.copy()
                self.market_data_cache[cache_key] = sanitized
                last_close = (
                    sanitized["Close"].dropna().iloc[-1]
                    if "Close" in sanitized and not sanitized["Close"].dropna().empty
                    else None
                )
                if last_close is not None:
                    self.last_close_cache[market_ticker] = float(last_close)

                return sanitized

            except concurrent.futures.TimeoutError:
                if attempt < max_retries - 1:
                    logger.warning(
                        "Timeout fetching MT5 data for %s (attempt %d/%d) - retrying...",
                        ticker, attempt + 1, max_retries
                    )
                    await asyncio.sleep(retry_delay)
                    continue
                fallback = self._fallback_market_data(ticker, market_ticker, period, interval, cache_key)
                if fallback is not None:
                    return fallback
                logger.error("[WARN] Market Data Timeout - Skipping Cycle for %s", ticker)
                return None
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        "Error fetching MT5 data for %s: %s (attempt %d/%d) - retrying...",
                        ticker, e, attempt + 1, max_retries
                    )
                    await asyncio.sleep(retry_delay)
                    continue
                fallback = self._fallback_market_data(ticker, market_ticker, period, interval, cache_key)
                if fallback is not None:
                    logger.warning(
                        "Using fallback market data for %s after fetch failure: %s",
                        ticker, e,
                    )
                    return fallback
                logger.error("[WARN] Market Data Timeout - Skipping Cycle for %s: %s", ticker, e)
                return None

        return None

    def _fallback_market_data(
        self,
        ticker: str,
        market_ticker: str,
        period: str,
        interval: str,
        cache_key: Tuple[str, str, str],
    ) -> Optional[pd.DataFrame]:
        """Return cached bars if available. MT5 does not require synthetic fallback."""
        cached = self.market_data_cache.get(cache_key)
        if cached is not None and not cached.empty:
            logger.warning(
                "Using cached market data for %s (%s/%s)", ticker, period, interval
            )
            return cached.copy()

        if market_ticker in self.last_close_cache:
            close_val = self.last_close_cache[market_ticker]
            logger.warning(
                "No MT5 data for %s — returning minimal single-bar fallback", ticker
            )
            now = datetime.utcnow()
            df = pd.DataFrame(
                [[close_val, close_val, close_val, close_val, 0.0]],
                index=[now],
                columns=["Open", "High", "Low", "Close", "Volume"],
            )
            self.market_data_cache[cache_key] = df.copy()
            return df

        return None

    async def _fetch_crypto_yfinance(
        self,
        ticker: str,
        market_ticker: str,
        period: str,
        interval: str,
        cache_key: Tuple[str, str, str],
    ) -> Optional[pd.DataFrame]:
        """Fetch crypto OHLCV from Yahoo Finance (yfinance) asynchronously."""
        try:
            import yfinance as yf
        except ImportError:
            logger.debug("yfinance not installed — skipping Yahoo Finance crypto fetch for %s", ticker)
            return None

        try:
            # Map period/interval to yfinance params
            yf_period = period or "1d"
            yf_interval = interval or "1m"
            if yf_interval.endswith("m"):
                mins = int(yf_interval[:-1])
                # yfinance supports 1m, 2m, 5m, 15m, 30m, 60m, 90m
                if mins not in {1, 2, 5, 15, 30, 60, 90}:
                    yf_interval = "5m"
            elif yf_interval.endswith("h"):
                hrs = int(yf_interval[:-1])
                yf_interval = f"{hrs}h"

            symbol = market_ticker if market_ticker else ticker
            # Ensure crypto uses Yahoo format (BTC-USD)
            if "-" not in symbol and any(symbol.upper().endswith(s) for s in ("USD", "USDT")):
                # Convert BTCUSD -> BTC-USD, ETHUSD -> ETH-USD, etc.
                for suffix in ("USDT", "USD"):
                    if symbol.upper().endswith(suffix):
                        base = symbol[:-len(suffix)]
                        symbol = f"{base}-USD"
                        break
            elif symbol.upper() in {"BTC", "BTCUSD", "XBT"}:
                symbol = "BTC-USD"
            elif symbol.upper() in {"ETH", "ETHUSD"}:
                symbol = "ETH-USD"
            elif symbol.upper() in {"SOL", "SOLUSD"}:
                symbol = "SOL-USD"
            elif symbol.upper() in {"XRP", "XRPUSD"}:
                symbol = "XRP-USD"

            logger.info("[CRYPTO] Fetching %s from Yahoo Finance (%s/%s)", symbol, yf_period, yf_interval)

            # Run blocking yfinance call in thread pool
            import concurrent.futures

            def _download():
                return yf.download(
                    symbol,
                    period=yf_period,
                    interval=yf_interval,
                    progress=False,
                    threads=False,
                )

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(_download)
                raw_df = future.result(timeout=20)

            if raw_df is None or raw_df.empty:
                logger.warning("[CRYPTO] Yahoo Finance returned empty data for %s", symbol)
                return None

            # yfinance returns MultiIndex columns like ('Close', 'BTC-USD'); flatten them
            if isinstance(raw_df.columns, pd.MultiIndex):
                raw_df.columns = raw_df.columns.get_level_values(0)

            # Normalize column names
            rename_map = {}
            for col in raw_df.columns:
                col_str = str(col).strip()
                lower = col_str.lower()
                if lower in {"open", "opens"}:
                    rename_map[col_str] = "Open"
                elif lower in {"high", "highs"}:
                    rename_map[col_str] = "High"
                elif lower in {"low", "lows"}:
                    rename_map[col_str] = "Low"
                elif lower in {"close", "closes", "adj close", "adj_close"}:
                    rename_map[col_str] = "Close"
                elif lower in {"volume", "volumes"}:
                    rename_map[col_str] = "Volume"
            if rename_map:
                raw_df = raw_df.rename(columns=rename_map)

            # Ensure required columns exist
            for required in ("Open", "High", "Low", "Close", "Volume"):
                if required not in raw_df.columns:
                    logger.warning("[CRYPTO] Missing %s column in Yahoo data for %s", required, symbol)
                    return None

            # Drop NaN rows and ensure index is DatetimeIndex
            raw_df = raw_df.dropna(subset=["Open", "High", "Low", "Close"])
            if raw_df.empty:
                logger.warning("[CRYPTO] All rows NaN after drop for %s", symbol)
                return None

            self.market_data_cache[cache_key] = raw_df.copy()
            last_close = float(raw_df["Close"].dropna().iloc[-1])
            self.last_close_cache[market_ticker] = last_close
            logger.info("[CRYPTO] Yahoo Finance returned %d bars for %s | last=%.2f", len(raw_df), symbol, last_close)
            return raw_df

        except Exception as exc:
            logger.warning("[CRYPTO] Yahoo Finance fetch failed for %s: %s", ticker, exc)
            return None

    async def _evaluate_timeframe_alignment(
        self, ticker: str, action: str, signal_type: str = "", strength: float = 0.0,
        confidence: float = 0.0,
    ) -> Tuple[bool, Dict[str, str]]:
        """Evaluate 5m/3m/1m alignment with aggressive autonomous-mode sniper gate.

        AGGRESSIVE HUNTER: If confidence >= AGGRESSIVE_HUNTER_CONFIDENCE_PCT (default 75%),
        skip 1m/3m alignment and strike on 5m chart alone.
        For liquidity-based signals, uses a relaxed check because SMA crossovers naturally
        oppose sweep direction (price drops during a demand sweep)."""
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
            df = await self._fetch_market_data(
                ticker, period=period, interval=interval
            )
            if df is None or len(df) < 30:
                votes[label] = "WAIT"
                continue

            required_cols = {"Open", "High", "Low", "Close", "Volume"}
            if not required_cols.issubset(df.columns):
                votes[label] = "WAIT"
                continue

            if df[list(required_cols)].tail(1).isnull().any(axis=1).iloc[0]:
                logger.info(
                    "[WAIT] MTF wait-mode: %s %s has partial candle data", ticker, label
                )
                votes[label] = "WAIT"
                continue

            fast = trend.sma_indicator(df["Close"], window=9)
            slow = trend.sma_indicator(df["Close"], window=21)
            rsi = momentum.rsi(df["Close"], window=14)

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

        # AGGRESSIVE HUNTER: High-confidence BUY signals can strike on 5m alone.
        # SELL signals need extra confirmation because crypto equal-high sweeps
        # often continue upward instead of reversing.
        hunter_threshold = getattr(config, "AGGRESSIVE_HUNTER_CONFIDENCE_PCT", 75.0)
        if confidence >= hunter_threshold and votes.get("5m") == action and action == "BUY":
            logger.info(
                "[FIRE] AGGRESSIVE HUNTER: %s %s confidence=%.1f%% >= %.1f%% — striking on 5m alone | votes=%s",
                action, ticker, confidence, hunter_threshold, votes,
            )
            return True, votes

        runtime_mode = str(
            self.session_detector.get_session_context().get(
                "runtime_mode", "AUTONOMOUS"
            )
            or "AUTONOMOUS"
        ).upper()
        one_min_match = votes.get("1m") == action
        aligned_helpers = sum(1 for tf in ["5m", "3m"] if votes.get(tf) == action)

        is_liquidity = "LIQUIDITY" in str(signal_type or "").upper()
        is_strong = float(strength or 0) >= 0.70

        if runtime_mode == "AUTONOMOUS":
            matching_timeframes = sum(1 for tf in ["5m", "3m", "1m"] if votes.get(tf) == action)
            if is_liquidity and is_strong:
                # BUY liquidity reversals can be relaxed because demand sweeps
                # often print while SMAs still lag. SELL reversals are stricter:
                # do not short BTC/ETH uptrends unless 5m and one helper agree.
                if action == "SELL":
                    aligned = votes.get("5m") == "SELL" and matching_timeframes >= 2
                    five_m_opposite = votes.get("5m") == "BUY"
                else:
                    five_m_opposite = votes.get("5m") == "SELL"
                    aligned = matching_timeframes >= 1 or not five_m_opposite
                if aligned:
                    logger.info(
                        "[TARGET] AUTONOMOUS LIQUIDITY SNIPER: %s %s passed | %d/3 agree | 5m_opposite=%s | votes=%s",
                        action,
                        ticker,
                        matching_timeframes,
                        five_m_opposite,
                        votes,
                    )
            else:
                # Standard signals: require at least 2 out of 3 timeframes to agree.
                aligned = matching_timeframes >= 2
                if aligned:
                    logger.info(
                        "[TARGET] AUTONOMOUS SNIPER: %s %s confirmed by %d/3 timeframes | votes=%s",
                        action,
                        ticker,
                        matching_timeframes,
                        votes,
                    )
        else:
            aligned = all(votes.get(tf) == action for tf in ["5m", "3m", "1m"])
        return aligned, votes

    async def _evaluate_level2_structure(
        self,
        ticker: str,
        action: str,
        entry_price: float,
    ) -> Tuple[bool, dict]:
        """Reject low-timeframe signals that fight 1h/4h trend or major zones."""
        if not bool(getattr(config, "MTF_STRUCTURE_FILTER_ENABLED", True)):
            return True, {"enabled": False, "allowed": True, "reason": "disabled"}

        def _resample_ohlcv(df: pd.DataFrame, rule: str) -> Optional[pd.DataFrame]:
            if df is None or df.empty or not isinstance(df.index, pd.DatetimeIndex):
                return None
            try:
                out = pd.DataFrame()
                out["Open"] = df["Open"].resample(rule).first()
                out["High"] = df["High"].resample(rule).max()
                out["Low"] = df["Low"].resample(rule).min()
                out["Close"] = df["Close"].resample(rule).last()
                out["Volume"] = df["Volume"].resample(rule).sum()
                return out.dropna()
            except Exception:
                return None

        frames: Dict[str, pd.DataFrame] = {}
        for label, period, interval in (
            ("15m", "7d", "15m"),
            ("1h", "7d", "1h"),
            ("4h", "7d", "4h"),
        ):
            df = await self._fetch_market_data(ticker, period=period, interval=interval)
            if (df is None or df.empty) and label == "4h":
                hourly = frames.get("1h")
                if hourly is None or hourly.empty:
                    hourly = await self._fetch_market_data(ticker, period="30d", interval="1h")
                df = _resample_ohlcv(hourly, "4h")
            if df is not None and not df.empty:
                frames[label] = df

        if len(frames) < 2:
            logger.warning(
                "[LEVEL2] %s %s structure data incomplete (%s). Blocking by default.",
                action,
                ticker,
                sorted(frames.keys()),
            )
            return False, {
                "enabled": True,
                "allowed": False,
                "reason": "Insufficient 15m/1h/4h structure data.",
                "frames": sorted(frames.keys()),
            }

        current_price = float(entry_price or 0.0)
        if current_price <= 0:
            for df in frames.values():
                if "Close" in df and not df["Close"].dropna().empty:
                    current_price = float(df["Close"].dropna().iloc[-1])
                    break

        verdict = self.structure_analyzer.evaluate(action, current_price, frames)
        logger.info(
            "[LEVEL2] %s %s allowed=%s bias=%s reason=%s timeframes=%s",
            action,
            ticker,
            verdict.allowed,
            verdict.bias,
            verdict.reason,
            verdict.timeframe_biases,
        )
        payload = verdict.as_dict()
        payload["enabled"] = True
        payload["current_price"] = current_price
        return verdict.allowed, payload

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate technical indicators (RSI, SMA, etc.)."""
        # RSI (14-period)
        df["RSI"] = momentum.rsi(df["Close"], window=14)

        # SMA (20 and 50 period)
        df["SMA_FAST"] = trend.sma_indicator(df["Close"], window=config.SMA_FAST)
        df["SMA_SLOW"] = trend.sma_indicator(df["Close"], window=config.SMA_SLOW)

        # Volume moving average
        df["VOL_MA"] = trend.sma_indicator(df["Volume"], window=20)

        # ATR (14-period) for Gemini data package
        df["ATR"] = volatility.average_true_range(df["High"], df["Low"], df["Close"], window=14)

        return df

    def _build_brain_package(
        self, df: pd.DataFrame, liquidity_zone: Optional[dict]
    ) -> dict:
        """Build the data package from the latest candles, liquidity geometry, and market regime."""
        from core.regime_detector import RegimeDetector

        recent = df.tail(10).copy() if df is not None else pd.DataFrame()
        recent_ohlcv = []
        recent_lines = []
        for index, row in recent.iterrows():
            candle = {
                "timestamp": index.isoformat()
                if hasattr(index, "isoformat")
                else str(index),
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
                    "current_price": float(
                        liquidity_zone.get("current_price", 0.0) or 0.0
                    ),
                }
            )

        # --- MARKET REGIME (hard math, no LLM) ---
        regime_context = ""
        regime_data = {}
        try:
            detector = RegimeDetector()
            verdict = detector.analyze(df)
            if verdict:
                regime_context = verdict.as_prompt_context()
                regime_data = verdict.as_dict()
        except Exception as e:
            logger.debug("[REGIME] Could not compute regime for brain package: %s", e)

        return {
            "recent_ohlcv": recent_ohlcv,
            "recent_candle_lines": recent_lines,
            "rsi": rsi_value,
            "atr": atr_value,
            "liquidity_zones": liquidity_zones,
            "regime_context": regime_context,
            "regime": regime_data,
        }

    def _detect_volume_spike(
        self, df: pd.DataFrame, ticker: str
    ) -> Optional[TechnicalSignal]:
        """Detect volume spike (>3x average)."""
        if len(df) < 2:
            return None

        last_vol = df["Volume"].iloc[-1]
        avg_vol = df["VOL_MA"].iloc[-1]

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
                    "price": df["Close"].iloc[-1],
                },
            )

        return None

    def _detect_price_spike(
        self, df: pd.DataFrame, ticker: str
    ) -> Optional[TechnicalSignal]:
        """Detect a sudden large price move in the last 5 bars.
        A PRICE_SPIKE is when price moves more than PRICE_SPIKE_THRESHOLD_PCT
        (default 1.5%) over a short window. This catches the kind of sharp
        move that volume-based detectors miss when volume is normal but the
        price action is dramatic."""
        if len(df) < 5:
            return None

        recent_closes = df["Close"].dropna().tail(5)
        if recent_closes.empty or len(recent_closes) < 2:
            return None

        start_price = float(recent_closes.iloc[0])
        end_price = float(recent_closes.iloc[-1])
        if start_price <= 0:
            return None

        spike_pct = ((end_price - start_price) / start_price) * 100.0
        threshold = getattr(config, "PRICE_SPIKE_THRESHOLD_PCT", 1.5)

        if abs(spike_pct) >= threshold:
            # Determine direction
            action = "BUY" if spike_pct > 0 else "SELL"
            # Strength proportional to spike magnitude
            strength = min(1.0, abs(spike_pct) / 10.0)  # 1.5%=0.15, 5%=0.5, 10%=1.0
            # Boost strength for very large spikes (>= 3%)
            if abs(spike_pct) >= 3.0:
                strength = min(1.0, 0.5 + abs(spike_pct) / 20.0)

            logger.info(
                "[FIRE] PRICE SPIKE: %s moved %+.2f%% over 5 bars (%s direction) | threshold=%.1f%%",
                ticker, spike_pct, action, threshold,
            )
            return TechnicalSignal(
                ticker=ticker,
                signal_type="PRICE_SPIKE",
                strength=strength,
                metadata={
                    "spike_pct": spike_pct,
                    "start_price": start_price,
                    "end_price": end_price,
                    "bars": 5,
                    "action_hint": action,
                },
            )

        return None

    def _detect_rsi_signal(
        self, df: pd.DataFrame, ticker: str
    ) -> Optional[TechnicalSignal]:
        """Detect RSI overbought/oversold conditions."""
        if len(df) < 2:
            return None

        current_rsi = df["RSI"].iloc[-1]
        prev_rsi = df["RSI"].iloc[-2]

        if pd.isna(current_rsi) or pd.isna(prev_rsi):
            return None

        # RSI crosses below oversold (bullish)
        if prev_rsi >= config.RSI_OVERSOLD and current_rsi < config.RSI_OVERSOLD:
            return TechnicalSignal(
                ticker=ticker,
                signal_type="RSI_OVERSOLD",
                strength=0.75,
                metadata={"rsi": current_rsi, "price": df["Close"].iloc[-1]},
            )

        # RSI crosses above overbought (bearish)
        if prev_rsi <= config.RSI_OVERBOUGHT and current_rsi > config.RSI_OVERBOUGHT:
            return TechnicalSignal(
                ticker=ticker,
                signal_type="RSI_OVERBOUGHT",
                strength=0.75,
                metadata={"rsi": current_rsi, "price": df["Close"].iloc[-1]},
            )

        return None

    def _detect_sma_cross(
        self, df: pd.DataFrame, ticker: str
    ) -> Optional[TechnicalSignal]:
        """Detect SMA fast/slow cross (Golden Cross / Death Cross)."""
        if len(df) < 2:
            return None

        current_fast = df["SMA_FAST"].iloc[-1]
        current_slow = df["SMA_SLOW"].iloc[-1]
        prev_fast = df["SMA_FAST"].iloc[-2]
        prev_slow = df["SMA_SLOW"].iloc[-2]

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
                    "price": df["Close"].iloc[-1],
                },
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
                    "price": df["Close"].iloc[-1],
                },
            )

        return None

    def _is_signal_cooldown(self, ticker: str, signal_type: str = "") -> bool:
        """Check if this ticker is still inside the post-dispatch debounce window."""
        remaining = self._ticker_cooldown_remaining(ticker)
        return remaining > 0

    def _record_signal(self, ticker: str, signal_type: str):
        """Record legacy signal timestamp for diagnostics."""
        key = f"{ticker}:{signal_type}"
        self.recent_signals[key] = datetime.utcnow()

    def _ticker_cooldown_remaining(self, ticker: str) -> int:
        """Return seconds left before this ticker can dispatch another signal."""
        key = str(ticker or "").upper().strip()
        if not key:
            return 0
        last_seen = self.last_signal_timestamps.get(key)
        if not last_seen:
            return 0
        elapsed = (datetime.utcnow() - last_seen).total_seconds()
        remaining = int(max(0.0, float(self.signal_cooldown) - elapsed))
        return remaining

    def _mark_ticker_dispatched(self, ticker: str) -> None:
        """Start ticker-level debounce after a signal has been approved for dispatch."""
        key = str(ticker or "").upper().strip()
        if key:
            self.last_signal_timestamps[key] = datetime.utcnow()

    def _technical_action_from_signal(self, signal: TechnicalSignal) -> str:
        """Infer BUY/SELL from deterministic scanner signal metadata."""
        signal_type = str(signal.signal_type or "").upper()
        metadata = signal.metadata or {}
        if "BUY" in signal_type or "BULLISH" in signal_type or "OVERSOLD" in signal_type:
            return "BUY"
        if "SELL" in signal_type or "BEARISH" in signal_type or "OVERBOUGHT" in signal_type:
            return "SELL"

        action = str(
            metadata.get("action_hint")
            or metadata.get("liquidity_bias")
            or metadata.get("action")
            or metadata.get("direction")
            or ""
        ).upper()
        if action in {"BUY", "SELL"}:
            return action
        if action == "UP":
            return "BUY"
        if action == "DOWN":
            return "SELL"
        return "HOLD"

    def _brain_unavailable(self, brain_decision: dict) -> bool:
        """Return True when the confirmation model failed rather than vetoed."""
        if not bool(brain_decision.get("fallback_mode")):
            return False
        text = " ".join(
            str(brain_decision.get(key, "") or "").lower()
            for key in ("reasoning", "raw_text")
        )
        return any(
            marker in text
            for marker in (
                "not found",
                "unavailable",
                "http error",
                "404",
                "connection",
                "timeout",
            )
        )

    def _validate_zone_origin(self, signal: TechnicalSignal, action: str) -> bool:
        """
        Validate that BUY signals originate from demand zones
        and SELL signals originate from supply zones.
        A signal is only valid if price is at or near the correct zone.
        """
        smart_money = signal.metadata.get("smart_money")
        if not smart_money:
            return True  # No zone data available; do not block

        price = float(signal.metadata.get("price", 0.0))
        if price <= 0:
            return True

        tolerance_pct = 0.003  # 0.3% tolerance for zone proximity
        tolerance = max(price * tolerance_pct, 0.5)

        if action == "BUY":
            nearest_demand = smart_money.get("nearest_demand")
            if not nearest_demand:
                logger.info(
                    "[ZONE] BUY %s rejected: no active demand zone", signal.ticker
                )
                return False
            zone_top = float(nearest_demand.get("top", 0))
            zone_bottom = float(nearest_demand.get("bottom", 0))
            if price > zone_top + tolerance:
                logger.info(
                    "[ZONE] BUY %s rejected: price %.2f far from demand zone %.2f-%.2f",
                    signal.ticker, price, zone_bottom, zone_top,
                )
                return False
            logger.info(
                "[ZONE] BUY %s validated at demand zone %.2f-%.2f",
                signal.ticker, zone_bottom, zone_top,
            )
            return True

        if action == "SELL":
            nearest_supply = smart_money.get("nearest_supply")
            if not nearest_supply:
                logger.info(
                    "[ZONE] SELL %s rejected: no active supply zone", signal.ticker
                )
                return False
            zone_top = float(nearest_supply.get("top", 0))
            zone_bottom = float(nearest_supply.get("bottom", 0))
            if price < zone_bottom - tolerance:
                logger.info(
                    "[ZONE] SELL %s rejected: price %.2f far from supply zone %.2f-%.2f",
                    signal.ticker, price, zone_bottom, zone_top,
                )
                return False
            logger.info(
                "[ZONE] SELL %s validated at supply zone %.2f-%.2f",
                signal.ticker, zone_bottom, zone_top,
            )
            return True

        return True

    async def process_signals(self, signals: List[TechnicalSignal]) -> Optional[dict]:
        """
        Process detected signals through Swarm Debate.
        Returns trade signal if confidence > threshold.
        FAST MODE: Single agent call with 15s timeout for local execution.
        """
        if not signals:
            return None

        # Sunday Gap Guard: block execution during the first 15 minutes after
        # futures markets open on Sunday (22:00-22:15 UTC) to avoid gap volatility.
        if self.session_detector.is_sunday_gap_window():
            logger.warning(
                "[SUNDAY-GAP] Execution PAUSED: Sunday gap window active "
                "(22:00-22:15 UTC). Waiting for spreads to stabilize."
            )
            self._emit_status("GLOBAL", "sunday_gap_guard")
            return None

        for signal in signals:
            # Check cooldown
            if self._is_signal_cooldown(signal.ticker, signal.signal_type):
                remaining = self._ticker_cooldown_remaining(signal.ticker)
                logger.info(
                    "[DEBOUNCE] Signal blocked for Ticker: %s (Cooldown active for another %d seconds)",
                    signal.ticker,
                    remaining,
                )
                continue

            logger.info(
                f"[FIRE] Signal detected: {signal.signal_type} on {signal.ticker} "
                f"(strength: {signal.strength:.2f})"
            )

            # Build market data for Swarm
            market_data = self._build_market_data(signal)

            # Run Swarm Debate
            try:
                analysis, transcript = await self.consensus.run(market_data)

                # [FIRE] NUCLEAR FIX: Override HOLD action if technical signal is strong
                # and agents are aligned (Technical Sniper + Risk Manager agree)
                if analysis.action.value == "HOLD" and signal.strength >= 0.60:
                    # Check if agents support a trade direction
                    tech_action = (
                        transcript.technical_sniper.action if transcript else "HOLD"
                    )
                    risk_verdict = (
                        transcript.risk_manager.verdict if transcript else "HOLD"
                    )

                    if tech_action in ["BUY", "SELL"] and risk_verdict == "APPROVE":
                        logger.info(
                            f"[FIRE] NUCLEAR OVERRIDE: LLM returned HOLD but technical signal "
                            f"strong ({signal.strength:.2f}) and agents aligned "
                            f"(Tech: {tech_action}, Risk: {risk_verdict})"
                        )
                        # Override to technical signal direction
                        analysis.action = SignalAction(tech_action)
                        analysis.reason = f"Strong technical {signal.signal_type} signal (strength: {signal.strength:.2f}) with agent alignment"

                # Calculate confidence score (0.0-1.0)
                confidence_score = self._calculate_confidence(
                    analysis, transcript, signal.strength
                )

                logger.info(f"Swarm consensus: {confidence_score:.2f} confidence")

                technical_action = self._technical_action_from_signal(signal)
                runtime_mode = str(
                    self.session_detector.get_session_context().get(
                        "runtime_mode", "AUTONOMOUS"
                    )
                    or "AUTONOMOUS"
                ).upper()
                # If the model collapses to HOLD while the deterministic scanner
                # has a strong directional setup, keep the trade candidate alive.
                if (
                    analysis.action.value == "HOLD"
                    and technical_action in {"BUY", "SELL"}
                    and signal.strength >= 0.60
                ):
                    logger.info(
                        "[FIRE] TECHNICAL OVERRIDE: %s %s kept alive as %s "
                        "(strength %.2f, mode=%s)",
                        signal.signal_type,
                        signal.ticker,
                        technical_action,
                        signal.strength,
                        runtime_mode,
                    )
                    analysis.action = SignalAction(technical_action)
                    analysis.reason = (
                        f"Strong technical {signal.signal_type} setup "
                        f"(strength {signal.strength:.2f}); model HOLD was treated as caution."
                    )

                # FILTER: Only dispatch BUY/SELL signals (skip HOLD)
                if analysis.action.value == "HOLD":
                    logger.info(
                        f"[PAUSE] HOLD signal for {signal.ticker} - not dispatching (no trade)"
                    )
                    self._emit_status(signal.ticker, "trade_rejected")
                    continue  # Skip to next signal

                # Sniper triple-check: trend/setup/entry must all align (5m/3m/1m).
                # If yfinance data is unavailable (futures symbols), skip this gate
                # and rely on the regime detector + brain swarm for direction.
                mtf_ok, mtf_votes = await self._evaluate_timeframe_alignment(
                    signal.ticker,
                    analysis.action.value,
                    signal_type=signal.signal_type,
                    strength=signal.strength,
                    confidence=confidence_score * 100.0,
                )
                # If all votes are WAIT, yfinance data was unavailable — pass through
                all_wait = all(v == "WAIT" for v in mtf_votes.values())
                if not mtf_ok and not all_wait:
                    logger.info(
                        "[TARGET] MTF block: %s %s rejected by 5m/3m/1m alignment %s",
                        analysis.action.value,
                        signal.ticker,
                        mtf_votes,
                    )
                    self._emit_status(signal.ticker, "trade_rejected")
                    continue
                elif all_wait:
                    logger.info(
                        "[TARGET] MTF data unavailable for %s — bypassing MTF gate (regime detector active)",
                        signal.ticker,
                    )

                signal_price = float(signal.metadata.get("price", market_data.price) or 0.0)
                level2_ok, level2_structure = await self._evaluate_level2_structure(
                    signal.ticker,
                    analysis.action.value,
                    signal_price,
                )
                if not level2_ok:
                    logger.info(
                        "[LEVEL2] Structure block: %s %s rejected | %s",
                        analysis.action.value,
                        signal.ticker,
                        level2_structure.get("reason"),
                    )
                    self._emit_status(signal.ticker, "trade_rejected")
                    continue

                # ZONE ORIGIN VALIDATION: BUY must come from demand zone, SELL from supply zone
                zone_ok = self._validate_zone_origin(signal, analysis.action.value)
                if not zone_ok:
                    logger.info(
                        "[ZONE] %s %s rejected: no supporting liquidity zone",
                        analysis.action.value,
                        signal.ticker,
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
                self._emit_status(
                    signal.ticker, f"brain_reasoning:{analysis.action.value}"
                )
                brain_decision = await self._request_brain_verdict(
                    signal, analysis.action.value
                )
                brain_verdict = str(
                    brain_decision.get("verdict", "[SIGNAL] WAIT") or "[SIGNAL] WAIT"
                ).upper()
                brain_used = str(
                    brain_decision.get("brain_used", "OPENROUTER") or "OPENROUTER"
                ).upper()
                fallback_mode = bool(brain_decision.get("fallback_mode"))
                if fallback_mode:
                    self._emit_status(signal.ticker, f"brain_fallback:{brain_used}")
                self._emit_status(signal.ticker, f"brain_verdict:{brain_verdict}")
                if brain_verdict != approved_brain_verdict:
                    if (
                        self._brain_unavailable(brain_decision)
                        and signal.strength >= 0.60
                        and confidence_score >= float(getattr(config, "SWARM_CONFIDENCE_THRESHOLD", 0.60))
                    ):
                        logger.warning(
                            "BRAIN UNAVAILABLE: allowing technical strike gate for %s %s "
                            "| response=%s | model=%s | reasoning=%s",
                            analysis.action.value,
                            signal.ticker,
                            brain_verdict,
                            brain_decision.get("model", "n/a"),
                            brain_decision.get("reasoning", ""),
                        )
                        brain_verdict = approved_brain_verdict
                    else:
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
                # [FIRE] FIX: Ensure entry/stop/tp are set (use signal price if LLM returned 0)
                if analysis.entry_price == 0.0 or analysis.entry_price is None:
                    analysis.entry_price = signal_price
                liquidity_zone = signal.metadata.get("liquidity_zone")
                if analysis.stop_loss == 0.0 or analysis.stop_loss is None:
                    analysis.stop_loss = self._liquidity_zone_stop_boundary(
                        analysis.action.value,
                        float(analysis.entry_price or signal_price),
                        liquidity_zone,
                    )
                if analysis.take_profit == 0.0 or analysis.take_profit is None:
                    analysis.take_profit = 0.0

                # Build clean signal data - NO datetime objects!
                self._record_signal(signal.ticker, signal.signal_type)
                self._mark_ticker_dispatched(signal.ticker)
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
                    "level2_structure": level2_structure,
                    "brain_verdict": brain_verdict,
                    "brain_reasoning": str(brain_decision.get("reasoning", "") or ""),
                    "brain_model": str(
                        brain_decision.get("model", self.brain.model)
                        or self.brain.model
                    ),
                    "brain_used": brain_used,
                    "fallback_mode": fallback_mode,
                    "force_execute": False,
                    "liquidity_zone": liquidity_zone,
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
                            "rating": transcript.devils_advocate.get(
                                "rating", "NEUTRAL"
                            ),
                            "rejection_reasons": transcript.devils_advocate.get(
                                "rejection_reasons", []
                            ),
                            "hidden_risks": transcript.devils_advocate.get(
                                "hidden_risks", "Unknown"
                            ),
                        },
                        "ceo_verdict": transcript.ceo_verdict,
                    },
                }

            except Exception as e:
                logger.error(f"Swarm consensus failed: {e}")
                continue

        return None

    def _build_market_data(self, signal: TechnicalSignal) -> MarketDataPoint:
        """Build MarketDataPoint from technical signal."""
        price = signal.metadata.get("price", 0.0)
        volume = signal.metadata.get("last_volume", 0.0)
        runtime_mode = str(
            self.session_detector.get_session_context().get(
                "runtime_mode", "AUTONOMOUS"
            )
            or "AUTONOMOUS"
        ).upper()

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
                "LIQUIDITY_ZONE": signal.metadata.get(
                    "liquidity_zone_label",
                    self._format_liquidity_zone_label(
                        signal.metadata.get("liquidity_zone")
                    ),
                ),
                "LIQUIDITY_SWEEP": signal.metadata.get("liquidity_sweep") or {},
                "RUNTIME_MODE": runtime_mode,
                "RECENT_CANDLES": signal.metadata.get("recent_candle_lines", []),
                "RECENT_OHLCV": signal.metadata.get("recent_ohlcv", []),
                "LIQUIDITY_ZONES": signal.metadata.get("liquidity_zones", []),
            },
        )

    async def _request_brain_verdict(
        self, signal: TechnicalSignal, proposed_action: str
    ) -> dict:
        """Ask OpenRouter for the final approval after triple alignment passes."""
        package = {
            "asset": signal.ticker,
            "recent_ohlcv": signal.metadata.get("recent_ohlcv", []),
            "rsi": signal.metadata.get("rsi", 50.0),
            "atr": signal.metadata.get("atr", 0.0),
            "liquidity_zones": signal.metadata.get("liquidity_zones", []),
            "liquidity_zone_label": signal.metadata.get(
                "liquidity_zone_label",
                self._format_liquidity_zone_label(
                    signal.metadata.get("liquidity_zone")
                ),
            ),
            "signal_type": signal.signal_type,
        }

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self.brain.request_decision, proposed_action, package
                ),
                timeout=max(1, int(config.GEMINI_TIMEOUT)),
            )
        except asyncio.TimeoutError:
            logger.warning(
                "OpenRouter brain timeout for %s %s - switching to local Predator",
                proposed_action,
                signal.ticker,
            )
            return await asyncio.to_thread(
                self.brain.predator.request_decision,
                proposed_action,
                package,
            )
        except Exception as exc:
            logger.warning(
                "OpenRouter brain request failed for %s %s: %s - switching to local Predator",
                proposed_action,
                signal.ticker,
                exc,
            )
            return await asyncio.to_thread(
                self.brain.predator.request_decision,
                proposed_action,
                package,
            )

    def _calculate_confidence(
        self, analysis, transcript, signal_strength: float = 0.5
    ) -> float:
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
            ConfidenceLevel.VERY_HIGH: 0.95,
        }

        base_confidence = confidence_map.get(analysis.confidence, 0.50)

        # [FIRE] NUCLEAR FIX: Force minimum MEDIUM (0.60) for strong technical signals
        # If technical signal is strong (strength > 0.6) but LLM returned LOW, boost to MEDIUM
        if base_confidence < 0.60 and signal_strength >= 0.60:
            logger.info(
                f"[FIRE] NUCLEAR OVERRIDE: Technical signal strong ({signal_strength:.2f}), "
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
                    transcript.macro_analyst.action == "BULLISH"
                    and analysis.action == SignalAction.BUY
                ) or (
                    transcript.macro_analyst.action == "BEARISH"
                    and analysis.action == SignalAction.SELL
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

        # [DEVIL] Devil's Advocate Penalty - Reduce confidence if risks identified
        # Capped at -0.10 max so the Devil doesn't kill every signal.
        # The regime detector already blocks counter-trend trades; the Devil
        # should only nudge, not veto.
        devils_penalty = 0.0
        if (
            transcript
            and hasattr(transcript, "devils_advocate")
            and transcript.devils_advocate
        ):
            raw_penalty = float(transcript.devils_advocate.get("confidence_penalty", 0.0) or 0.0)
            # Cap the penalty at -0.10 (was uncapped, often -0.25 to -0.35)
            devils_penalty = max(raw_penalty, -0.10)
            rating = transcript.devils_advocate.get("rating", "NEUTRAL")
            if rating in ["STRONG_AVOID", "CAUTIOUS"]:
                logger.warning(
                    f"[DEVIL] Devil's Advocate penalty: {raw_penalty:.2f} -> capped to {devils_penalty:.2f} "
                    f"(rating: {rating})"
                )

        final_confidence = max(
            0.0,
            min(
                1.0, base_confidence + alignment_bonus + signal_weight + devils_penalty
            ),
        )
        return round(final_confidence, 2)

    async def dispatch_to_local(self, signal_data: dict) -> bool:
        """
        Dispatch trade signal to local laptop executor via HTTP.
        Uses shared async aiohttp.ClientSession to avoid blocking the event loop.
        Returns True if successfully received.
        """
        timeout = float(getattr(config, "LOCAL_EXECUTION_TIMEOUT", 30.0) or 30.0)
        headers = {"Content-Type": "application/json"}
        if getattr(config, "SIGNAL_API_KEY", ""):
            headers[getattr(config, "SIGNAL_API_HEADER", "X-Signal-Key")] = (
                config.SIGNAL_API_KEY
            )

        payload = dict(signal_data)
        if getattr(config, "SIGNAL_API_KEY", ""):
            payload.setdefault("api_key", config.SIGNAL_API_KEY)

        targets = self._get_dispatch_targets()
        if not targets:
            self.dispatch_failure_streak += 1
            self.last_dispatch_status_code = None
            self.last_dispatch_target = "none"
            self.last_dispatch_error_message = (
                "Signal dispatch failed: no configured dispatch targets"
            )
            logger.error("[FAIL] %s", self.last_dispatch_error_message)
            return False

        bridge_failure: Optional[str] = None
        bridge_retry_seconds = max(
            30.0, float(getattr(config, "SCAN_INTERVAL", 10)) * 12.0
        )

        for target_name, target_url in targets:
            try:
                logger.info(
                    "[SAT] Dispatch attempt via %s -> %s | %s %s (confidence: %.2f)",
                    target_name,
                    target_url,
                    signal_data.get("action"),
                    signal_data.get("ticker"),
                    float(signal_data.get("confidence", 0.0) or 0.0),
                )

                ok, status_code, body_text = await self.dispatcher.dispatch(
                    payload,
                    target_url,
                    headers=headers,
                    timeout=timeout,
                )
                body_preview = body_text.strip().replace("\n", " ")[:240]

                if ok:
                    self.dispatch_failure_streak = 0
                    self.dispatch_alert_emitted = False
                    self.last_dispatch_status_code = status_code
                    self.last_dispatch_target = target_name
                    self.last_dispatch_error_message = ""
                    if target_name == "bridge":
                        self.public_dispatch_retry_after_ts = 0.0
                    logger.info("[OK] Signal dispatched successfully via %s", target_name)
                    return True

                failure_message = (
                    f"Signal dispatch failed via {target_name}: HTTP {status_code}"
                    + (f" - {body_preview}" if body_preview else "")
                )
                self.last_dispatch_status_code = status_code
                self.last_dispatch_target = target_name

                if target_name == "bridge" and any(
                    name == "local" for name, _ in targets
                ):
                    bridge_failure = failure_message
                    self.public_dispatch_retry_after_ts = (
                        time.time() + bridge_retry_seconds
                    )
                    logger.warning(
                        "[EMOJI] Bridge dispatch unavailable (%s). Falling back to localhost for %.0fs.",
                        f"HTTP {status_code}",
                        bridge_retry_seconds,
                    )
                    continue

                self.dispatch_failure_streak += 1
                self.last_dispatch_error_message = failure_message
                logger.error("[FAIL] %s", failure_message)
                return False

            except Exception as e:
                failure_message = (
                    f"Signal dispatch failed via {target_name}: {type(e).__name__}: {e}"
                )
                self.last_dispatch_status_code = None
                self.last_dispatch_target = target_name

                if target_name == "bridge" and any(
                    name == "local" for name, _ in targets
                ):
                    bridge_failure = failure_message
                    self.public_dispatch_retry_after_ts = (
                        time.time() + bridge_retry_seconds
                    )
                    logger.warning(
                        "[EMOJI] Bridge dispatch raised %s. Falling back to localhost for %.0fs.",
                        type(e).__name__,
                        bridge_retry_seconds,
                    )
                    continue

                self.dispatch_failure_streak += 1
                self.last_dispatch_error_message = failure_message
                logger.error("[FAIL] %s", failure_message)
                return False

        self.dispatch_failure_streak += 1
        self.last_dispatch_status_code = self.last_dispatch_status_code
        self.last_dispatch_error_message = (
            bridge_failure or "Signal dispatch failed after exhausting all targets"
        )
        logger.error("[FAIL] %s", self.last_dispatch_error_message)
        return False

    async def run_scanner(self):
        """Main scanner loop - continuous scanning with circuit breaker."""
        logger.info(f"[CLOUD] Cloud Scanner started - monitoring {len(self.tickers)} tickers")
        logger.info(f"Tickers: {', '.join(self.tickers)}")
        logger.info(f"Confidence threshold: {config.SWARM_CONFIDENCE_THRESHOLD}")
        logger.info(
            f"Circuit breaker: {self.max_consecutive_errors} consecutive errors before alert"
        )

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
                            logger.info(f"[OK] Trade signal executed: {trade_signal}")
                        else:
                            logger.warning(f"[WARN] Trade signal dispatch failed")

                # Success - reset error counter
                self.consecutive_errors = 0
                self.last_successful_scan = datetime.utcnow()

                # Wait before next scan
                await asyncio.sleep(self.get_scan_interval())

            except KeyboardInterrupt:
                logger.info("[STOP] Scanner stopped by user")
                break
            except Exception as e:
                self.consecutive_errors += 1
                logger.error(
                    f"[FAIL] Scanner error ({self.consecutive_errors}/{self.max_consecutive_errors}): {type(e).__name__}: {e}"
                )

                # Alert user if threshold exceeded
                if self.consecutive_errors >= self.error_alert_threshold:
                    logger.critical(
                        f"[SIREN] SCANNER ALERT: {self.consecutive_errors} consecutive errors! "
                        f"Last success: {self.last_successful_scan}. "
                        f"Check API connectivity, API keys, and network status."
                    )

                # Circuit breaker - suggest manual intervention
                if self.consecutive_errors >= self.max_consecutive_errors:
                    logger.critical(
                        f"[STOP] CIRCUIT BREAKER TRIGGERED: {self.max_consecutive_errors} consecutive errors. "
                    )
                    # Still wait, but log more aggressively
                    await asyncio.sleep(10)  # Longer wait when in error state
                else:
                    await asyncio.sleep(5)  # Normal retry delay


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
