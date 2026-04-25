"""
VcanTrade AI - Market Session Awareness (The "Clock")

Gives the bot temporal intelligence:
- Knows which markets are open RIGHT NOW
- Filters tickers based on day of week (Sunday = Crypto Only)
- Detects active trading session (Asian/London/New York)
- Adjusts scanning aggression based on session volatility

This transforms the bot from a "blind scanner" to a "context-aware predator."
"""

import logging
from datetime import datetime, timezone, time
from typing import List, Dict, Optional, Tuple
from enum import Enum
from dataclasses import dataclass

import holidays

import config

logger = logging.getLogger(__name__)


# ===================================================================
# Market Session Definitions
# ===================================================================

class MarketSession(str, Enum):
    """Global trading sessions by market hours."""
    ASIAN = "Asian"           # Tokyo/HK/Sydney
    EUROPEAN = "European"     # London/Frankfurt
    US = "US"                 # New York
    CRYPTO = "Crypto"         # 24/7
    CLOSED = "Closed"         # No major markets open
    HOLIDAY = "Holiday"       # Market holiday
    SATURDAY_AUDIT = "Saturday Audit"  # Close positions only, no new trades
    EARLY_CLOSE = "Early Close"  # Market closes early
    ALWAYS_OPEN = "Always Open"  # Dashboard override for manual/runtime watchlists


@dataclass
class MarketHours:
    """Market opening hours in UTC."""
    name: str
    open_utc: int    # Hour in UTC (0-23)
    close_utc: int   # Hour in UTC (0-23)
    peak_volatility_start: int  # Best trading hours
    peak_volatility_end: int
    is_24_7: bool = False


# Major market definitions (UTC hours)
MARKET_SCHEDULES = {
    "Sydney": MarketHours("Sydney", 22, 7, 23, 2),    # 10PM-7AM UTC
    "Tokyo": MarketHours("Tokyo", 0, 9, 1, 3),         # 12AM-9AM UTC (9AM-6PM JST)
    "HongKong": MarketHours("HongKong", 1, 8, 2, 4),   # 1AM-8AM UTC (9AM-4PM HKT)
    "London": MarketHours("London", 8, 16, 8, 10),     # 8AM-4PM UTC
    "NewYork": MarketHours("NewYork", 13, 21, 13, 15), # 1PM-9PM UTC (9AM-5PM EST)
    "Crypto": MarketHours("Crypto", 0, 23, 0, 23, True),  # 24/7
}

# Early close definitions (UTC hour when market closes)
EARLY_CLOSE_SCHEDULE = {
    # US Early Closes
    "Black Friday": (11, 4, "Friday after Thanksgiving"),  # Closes 1 PM EST = 18 UTC
    "Christmas Eve": (12, 24, "Christmas Eve"),  # Closes 1 PM EST = 18 UTC
    "July 3rd (if weekday)": (7, 3, "Independence Day Eve"),  # Closes 1 PM EST
}

# Initialize holiday calendars
US_HOLIDAYS = holidays.US(years=datetime.now(timezone.utc).year)
HK_HOLIDAYS = holidays.HK(years=datetime.now(timezone.utc).year)


# ===================================================================
# Ticker Classification
# ===================================================================

TICKER_BY_MARKET = {
    # Crypto (24/7)
    "CRYPTO": ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD", "ADA-USD"],

    # Forex (24/5 - Mon-Fri)
    "FOREX": ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "USDCAD=X"],

    # Asian Markets (HK/Tokyo stocks)
    "ASIAN": ["700.HK", "9988.HK", "TCEHY", "NTDOY", "SNE"],

    # European (London/Frankfurt)
    "EUROPEAN": ["^FTSE", "^GDAXI", "BP.L", "SIE.DE"],

    # US Stocks/Indices
    "US": ["TSLA", "AAPL", "NVDA", "SPY", "QQQ", "MSFT", "AMZN", "GOOGL", "META", "^GSPC", "^DJI"],

    # Commodities (mostly trade during US/London overlap)
    "COMMODITIES": ["GC=F", "CL=F", "SI=F", "HG=F"],
}

# Fast-lookup set for weekend filtering
_CRYPTO_SYMBOLS: set[str] = set()
for _sym in TICKER_BY_MARKET["CRYPTO"]:
    _CRYPTO_SYMBOLS.add(_sym.upper())
    _CRYPTO_SYMBOLS.add(_sym.upper().replace("-", ""))

_CME_FUTURES_HINTS = ("MNQ", "MES", "MCL", "ES", "NQ", "GC", "CL", "SI", "HG", "YM")


def is_crypto_ticker(ticker: str) -> bool:
    """Return True if the ticker is a known crypto pair."""
    if not ticker:
        return False
    upper = str(ticker).strip().upper()
    # Direct match or dash-stripped match
    if upper in _CRYPTO_SYMBOLS or upper.replace("-", "") in _CRYPTO_SYMBOLS:
        return True
    # Common crypto suffix/prefix patterns
    if any(upper.endswith(s) for s in ("USD", "USDT", "BTC", "ETH")):
        if any(upper.startswith(p) for p in ("BTC", "ETH", "SOL", "XRP", "ADA", "BNB")):
            return True
    return False


def is_futures_ticker(ticker: str) -> bool:
    """Return True if the ticker looks like a CME/NYMEX futures symbol."""
    if not ticker:
        return False
    upper = str(ticker).strip().upper()
    # TradingView prefixes
    if any(upper.startswith(p + ":") for p in ("CME_MINI", "NYMEX", "CME", "COMEX", "CBOT")):
        return True
    # Yahoo futures suffix
    if upper.endswith("=F"):
        return True
    # Contract month codes (e.g., MNQM6, MESM6, MCLM6)
    if any(upper.startswith(h) for h in _CME_FUTURES_HINTS):
        return True
    return False


def is_weekend_closed(ticker: str) -> bool:
    """
    Return True if the ticker should NOT be scanned on Saturday/Sunday.
    Crypto trades 24/7; everything else is closed on weekends.
    """
    return not is_crypto_ticker(ticker)


# ===================================================================
# Market Session Detector
# ===================================================================

class MarketSessionDetector:
    """
    Detects active market sessions and filters tickers accordingly.
    
    Features:
    - Sunday Detection: Auto-filters to crypto only
    - Session Detection: Asian/European/US
    - Peak Hour Awareness: Knows when volatility is highest
    - Dynamic Ticker Filtering: Returns only relevant tickers for current time
    """

    def __init__(self):
        self._current_session: Optional[MarketSession] = None
        self._active_markets: List[str] = []
        self._filtered_tickers: List[str] = []
        self._last_update: Optional[datetime] = None
        self.update_interval = 300  # Update every 5 minutes
        
        # Holiday/Early close tracking
        self._is_holiday_us = False
        self._is_holiday_hk = False
        self._holiday_name = ""
        self._is_early_close = False
        self._early_close_reason = ""
        self._runtime_mode = "AUTONOMOUS"
        self._dashboard_tickers: List[str] = []
        
        logger.info("[CLOCK] Market Session Detector initialized with Holiday Awareness")

    def set_runtime_context(self, mode: str, dashboard_tickers: Optional[List[str]] = None) -> None:
        """Store operator mode and active dashboard tickers for session overrides."""
        self._runtime_mode = str(mode or "").upper().strip() or "AUTONOMOUS"
        self._dashboard_tickers = [
            str(ticker).strip().upper()
            for ticker in (dashboard_tickers or [])
            if str(ticker).strip()
        ]
        self._last_update = None
        self._filtered_tickers = []

    def _dashboard_override_active(self) -> bool:
        """Teacher/Autonomous mode keeps dashboard tickers analyzable over weekend silence."""
        return self.is_weekend() and self._runtime_mode in {"TEACHER", "AUTONOMOUS"} and bool(self._dashboard_tickers)

    def _check_holidays(self) -> Tuple[bool, bool, str]:
        """
        Check if today is a market holiday.
        
        Returns:
            Tuple of (is_us_holiday, is_hk_holiday, holiday_name)
        """
        now = self.get_current_datetime()
        today = now.date()
        
        # Check US holidays
        is_us_holiday = today in US_HOLIDAYS
        us_holiday_name = US_HOLIDAYS.get(today, "")
        
        # Check HK holidays
        is_hk_holiday = today in HK_HOLIDAYS
        hk_holiday_name = HK_HOLIDAYS.get(today, "")
        
        holiday_name = ""
        if is_us_holiday:
            holiday_name = f"US Holiday: {us_holiday_name}"
        elif is_hk_holiday:
            holiday_name = f"HK Holiday: {hk_holiday_name}"
        
        return is_us_holiday, is_hk_holiday, holiday_name

    def _check_early_close(self) -> Tuple[bool, str, Optional[int]]:
        """
        Check if today is an early close day.
        
        Returns:
            Tuple of (is_early_close, reason, early_close_utc_hour)
        """
        now = self.get_current_datetime()
        
        # Check Black Friday (day after Thanksgiving)
        thanksgiving = self._get_thanksgiving(now.year)
        black_friday = thanksgiving.replace(day=thanksgiving.day + 1)
        if now.date() == black_friday:
            return True, "Black Friday (Early Close 1 PM EST)", 18  # 18 UTC = 1 PM EST
        
        # Check Christmas Eve
        if now.month == 12 and now.day == 24:
            return True, "Christmas Eve (Early Close 1 PM EST)", 18
        
        # Check July 3rd (if weekday)
        if now.month == 7 and now.day == 3 and now.weekday() < 5:
            return True, "Independence Day Eve (Early Close 1 PM EST)", 18
        
        return False, "", None

    def _get_thanksgiving(self, year: int) -> datetime:
        """Calculate Thanksgiving date (4th Thursday in November)."""
        thanksgiving = datetime(year, 11, 1)
        # Find first Thursday
        while thanksgiving.weekday() != 3:  # 3 = Thursday
            thanksgiving = thanksgiving.replace(day=thanksgiving.day + 1)
        # Add 3 weeks
        thanksgiving = thanksgiving.replace(day=thanksgiving.day + 21)
        return thanksgiving

    def get_current_datetime(self) -> datetime:
        """Get current UTC time."""
        return datetime.now(timezone.utc)

    def is_sunday(self) -> bool:
        """Check if current day is Sunday."""
        return self.get_current_datetime().weekday() == 6  # 6 = Sunday

    def is_weekend(self) -> bool:
        """Check if current day is Saturday or Sunday."""
        return self.get_current_datetime().weekday() >= 5  # 5=Sat, 6=Sun

    def is_weekday(self) -> bool:
        """Check if current day is Monday-Friday."""
        return self.get_current_datetime().weekday() < 5

    def is_sunday_gap_window(self) -> bool:
        """
        Sunday Gap Guard: Returns True during the first 15 minutes after
        futures markets open on Sunday (22:00-22:15 UTC).
        This prevents trading on unstable spreads and high-volatility gaps.
        """
        now = self.get_current_datetime()
        if now.weekday() != 6:  # Not Sunday
            return False
        # Sunday 22:00 to 22:15 UTC = gap window
        return 22 <= now.hour < 23 and now.minute < 15

    def is_weekend_mode(self) -> bool:
        """
        Weekend override check with Automatic Switchboard Flip.
        Returns True if:
          - Saturday (any time)
          - Sunday before 23:00 UTC
        Returns False if:
          - Sunday 23:00 UTC or later (futures resume)
          - Monday-Friday
        """
        now = self.get_current_datetime()
        weekday = now.weekday()
        if weekday == 5:  # Saturday = always weekend
            return True
        if weekday == 6:  # Sunday
            # Before 23:00 UTC = weekend mode
            # At/after 23:00 UTC = normal mode resumes
            return now.hour < 23
        return False

    def is_sunday_transition_complete(self) -> bool:
        """
        Returns True if we are on Sunday at or after 23:00 UTC,
        meaning the automatic switchboard flip has occurred and
        normal MULTI_ASSET_TICKERS should resume.
        """
        now = self.get_current_datetime()
        return now.weekday() == 6 and now.hour >= 23

    def detect_active_sessions(self) -> Tuple[List[str], MarketSession]:
        """
        Detect which market sessions are currently active.
        Includes holiday and early close awareness.
        
        Returns:
            Tuple of (active_market_names, primary_session)
        """
        now = self.get_current_datetime()
        current_hour = now.hour
        
        # Reset tracking
        self._is_holiday_us = False
        self._is_holiday_hk = False
        self._holiday_name = ""
        self._is_early_close = False
        self._early_close_reason = ""

        if self._dashboard_override_active():
            self._current_session = MarketSession.ALWAYS_OPEN
            self._active_markets = ["Dashboard Watchlist"]
            self._filtered_tickers = list(self._dashboard_tickers)
            self._last_update = now
            logger.info(
                "[CLOCK] DASHBOARD OVERRIDE: Treating %d dashboard tickers as always open for analysis in %s mode",
                len(self._dashboard_tickers),
                self._runtime_mode,
            )
            return ["Dashboard Watchlist"], MarketSession.ALWAYS_OPEN
        
        # Check Saturday first
        if now.weekday() == 5:  # Saturday
            self._current_session = MarketSession.SATURDAY_AUDIT
            self._active_markets = ["Crypto"]
            self._filtered_tickers = TICKER_BY_MARKET["CRYPTO"]
            self._last_update = now
            
            logger.info("[CLOCK] SATURDAY MODE: Close-only/Audit day. No new stock/forex trades.")
            return ["Crypto"], MarketSession.SATURDAY_AUDIT
        
        # Sunday = Crypto Only
        if self.is_sunday():
            self._current_session = MarketSession.CRYPTO
            self._active_markets = ["Crypto"]
            self._last_update = now
            
            logger.info("[CLOCK] SUNDAY MODE: Markets closed. Crypto only.")
            return ["Crypto"], MarketSession.CRYPTO
        
        # Check holidays
        is_us_holiday, is_hk_holiday, holiday_name = self._check_holidays()
        self._is_holiday_us = is_us_holiday
        self._is_holiday_hk = is_hk_holiday
        self._holiday_name = holiday_name
        
        # If US holiday, only crypto and non-US markets active
        if is_us_holiday:
            active_markets = ["Crypto"]
            if not is_hk_holiday:
                # HK might still be open
                active_markets.extend(["Tokyo", "HongKong"])
            
            self._current_session = MarketSession.HOLIDAY
            self._active_markets = active_markets
            self._last_update = now
            
            logger.info(f"[PALM] HOLIDAY MODE: {holiday_name} | Only crypto/non-US markets active")
            return active_markets, MarketSession.HOLIDAY
        
        # Check early close
        is_early, early_reason, early_hour = self._check_early_close()
        if is_early:
            self._is_early_close = True
            self._early_close_reason = early_reason
            
            logger.info(f"[ALARM] EARLY CLOSE: {early_reason}")
        
        # Weekday - check which markets are open
        active_markets = ["Crypto"]  # Crypto always active
        
        for market_name, hours in MARKET_SCHEDULES.items():
            if hours.is_24_7:
                continue  # Already added
            
            # Check if market is closed due to early close
            if is_early and market_name == "NewYork":
                if current_hour >= early_hour:
                    logger.debug(f"[CLOCK] {market_name} closed early at {early_hour} UTC")
                    continue
            
            if hours.open_utc < hours.close_utc:
                # Normal hours (e.g., 13-21)
                if hours.open_utc <= current_hour < hours.close_utc:
                    active_markets.append(market_name)
            else:
                # Overnight hours (e.g., 22-7 crosses midnight)
                if current_hour >= hours.open_utc or current_hour < hours.close_utc:
                    active_markets.append(market_name)
        
        # Determine primary session
        primary_session = self._classify_primary_session(active_markets, current_hour)
        
        # Update early close session type
        if is_early and primary_session == MarketSession.US:
            primary_session = MarketSession.EARLY_CLOSE
        
        self._active_markets = active_markets
        self._current_session = primary_session
        self._last_update = now
        
        return active_markets, primary_session

    def _classify_primary_session(self, active_markets: List[str], current_hour: int) -> MarketSession:
        """Classify which primary session is dominant."""
        # Check for overlapping sessions
        has_us = "NewYork" in active_markets
        has_london = "London" in active_markets
        has_asian = any(m in active_markets for m in ["Tokyo", "HongKong", "Sydney"])
        
        # US/London overlap (13:00-16:00 UTC) = highest volatility
        if has_us and has_london:
            return MarketSession.US  # US dominates
        
        # US session
        if has_us:
            return MarketSession.US
        
        # European session
        if has_london:
            return MarketSession.EUROPEAN
        
        # Asian session
        if has_asian:
            return MarketSession.ASIAN
        
        # Only crypto left
        return MarketSession.CRYPTO

    def get_filtered_tickers(self, base_tickers: List[str] = None) -> List[str]:
        """
        Get tickers filtered by current market session.
        
        Args:
            base_tickers: Original ticker list (from config)
            
        Returns:
            Filtered list of relevant tickers for current time
        """
        # Check if we need to update (cache for 5 minutes)
        now = self.get_current_datetime()
        if (
            self._last_update
            and (now - self._last_update).total_seconds() < self.update_interval
            and self._filtered_tickers
        ):
            return self._filtered_tickers
        
        # Detect active sessions
        active_markets, primary_session = self.detect_active_sessions()

        if self._dashboard_override_active() and base_tickers:
            filtered = [ticker for ticker in base_tickers if str(ticker).strip()]
            self._filtered_tickers = filtered
            self._current_session = MarketSession.ALWAYS_OPEN
            self._active_markets = ["Dashboard Watchlist"]
            self._last_update = now
            logger.info(
                "[CLOCK] DASHBOARD OVERRIDE: Weekend session filter bypassed for dashboard tickers: %s",
                ", ".join(filtered),
            )
            return filtered
        
        # Build filtered ticker list
        filtered = []
        
        # Sunday/Weekend = Crypto Only
        if self.is_weekend():
            # Only return crypto tickers
            crypto_tickers = TICKER_BY_MARKET["CRYPTO"]
            
            # Also include any crypto tickers from base_tickers
            if base_tickers:
                for ticker in base_tickers:
                    if any(crypto in ticker for crypto in ["BTC", "ETH", "SOL", "USD"]):
                        if ticker not in crypto_tickers:
                            crypto_tickers.append(ticker)
            
            self._filtered_tickers = crypto_tickers
            self._current_session = MarketSession.CRYPTO
            self._active_markets = ["Crypto"]
            self._last_update = now
            
            logger.info(
                f"[CLOCK] WEEKEND MODE: Markets closed. Scanning {len(crypto_tickers)} crypto tickers only."
            )
            return crypto_tickers
        
        # Weekday - filter based on active markets
        if base_tickers:
            # Use base tickers but log session context
            filtered = base_tickers.copy()
        else:
            # Build from market classifications
            for market in active_markets:
                if market in TICKER_BY_MARKET:
                    filtered.extend(TICKER_BY_MARKET[market])
        
        # Remove duplicates
        filtered = list(dict.fromkeys(filtered))
        
        # Update state
        self._filtered_tickers = filtered
        self._current_session = primary_session
        self._active_markets = active_markets
        self._last_update = now
        
        # Log session info
        session_name = primary_session.value
        market_count = len(active_markets)
        
        logger.info(
            f"[CLOCK] SESSION DETECTED: {session_name} Session | "
            f"Markets Open: {', '.join(active_markets)} | "
            f"Scanning {len(filtered)} tickers"
        )
        
        return filtered

    def get_session_context(self) -> Dict:
        """
        Get current session context for prompt injection.
        Used to give the CEO/Swarm agents temporal awareness.
        """
        now = self.get_current_datetime()
        active_markets, primary_session = self.detect_active_sessions()
        
        # Check if in peak volatility hours
        is_peak = self.is_peak_volatility()
        
        # Day of week
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        day_name = day_names[now.weekday()]
        
        # Check special modes
        is_saturday = now.weekday() == 5
        is_sunday = self.is_sunday()
        is_friday = now.weekday() == 4
        friday_close_cutoff = int(getattr(config, "FRIDAY_CLOSE_CUTOFF_UTC", 18) or 18)
        is_friday_close_window = is_friday and now.hour >= friday_close_cutoff
        
        # Build context
        context = {
            "current_time_utc": now.strftime("%H:%M:%S UTC"),
            "day_of_week": day_name,
            "is_weekend": self.is_weekend(),
            "is_saturday": is_saturday,
            "is_sunday": is_sunday,
            "is_friday": is_friday,
            "friday_close_cutoff_utc": friday_close_cutoff,
            "is_friday_close_window": is_friday_close_window,
            "is_holiday_us": self._is_holiday_us,
            "is_holiday_hk": self._is_holiday_hk,
            "holiday_name": self._holiday_name,
            "is_early_close": self._is_early_close,
            "early_close_reason": self._early_close_reason,
            "primary_session": primary_session.value,
            "active_markets": active_markets,
            "dashboard_override_active": self._dashboard_override_active(),
            "dashboard_tickers": list(self._dashboard_tickers),
            "runtime_mode": self._runtime_mode,
            "is_peak_volatility": is_peak,
            "session_note": self._generate_session_note(primary_session, is_peak, day_name),
        }
        
        return context

    def is_peak_volatility(self) -> bool:
        """Check if current time is during peak volatility hours."""
        now = self.get_current_datetime()
        current_hour = now.hour
        
        # US Open (13:30-15:30 UTC) = highest volatility
        if 13 <= current_hour <= 15:
            return True
        
        # London Open (08:00-10:00 UTC)
        if 8 <= current_hour <= 10:
            return True
        
        # Tokyo Open (01:00-03:00 UTC)
        if 1 <= current_hour <= 3:
            return True
        
        return False

    def _generate_session_note(self, session: MarketSession, is_peak: bool, day_name: str) -> str:
        """Generate human-readable session note for agent prompts."""
        # Saturday mode
        if session == MarketSession.SATURDAY_AUDIT:
            return f"{day_name} - Close-Only/Audit day. No new stock/forex trades. Review open positions."

        if session == MarketSession.ALWAYS_OPEN:
            return (
                f"{day_name} - Dashboard override active. Watchlist tickers stay analyzable in "
                f"{self._runtime_mode} mode even while broader markets are closed."
            )
        
        # Sunday/Weekend mode
        if self.is_weekend():
            return f"{day_name} - Weekend trading. Only crypto markets active. Expect lower volume."
        
        # Holiday mode
        if session == MarketSession.HOLIDAY:
            return f"{day_name} - {self._holiday_name}. US markets closed. Trading crypto/non-US markets only."
        
        # Early close mode
        if session == MarketSession.EARLY_CLOSE:
            return f"{day_name} - {self._early_close_reason}. Market closing early. Consider tightening stops."

        friday_close_cutoff = int(getattr(config, "FRIDAY_CLOSE_CUTOFF_UTC", 18) or 18)
        now = self.get_current_datetime()
        if now.weekday() == 4 and now.hour >= friday_close_cutoff:
            return (
                f"{day_name} - Post-{friday_close_cutoff:02d}:00 UTC Friday close-risk window. "
                "Avoid fresh swings and protect profits aggressively."
            )
        
        # Normal sessions
        if is_peak:
            return f"{day_name} - {session.value} session PEAK HOURS. Highest volatility expected."
        
        return f"{day_name} - {session.value} session active. Normal trading conditions."

    def get_session_status_log(self) -> str:
        """Get formatted status string for heartbeat logging."""
        now = self.get_current_datetime()
        active_markets, primary_session = self.detect_active_sessions()
        is_peak = self.is_peak_volatility()
        
        # Determine emoji and tag
        if primary_session == MarketSession.HOLIDAY:
            emoji = "[PALM]"
            tag = f"Holiday Mode ({'US' if self._is_holiday_us else 'HK'})"
        elif primary_session == MarketSession.ALWAYS_OPEN:
            emoji = "[GREEN]"
            tag = f"Always Open Override ({len(self._dashboard_tickers)} tickers)"
        elif primary_session == MarketSession.SATURDAY_AUDIT:
            emoji = "[CLIPBOARD]"
            tag = "Close-Only/Audit"
        elif primary_session == MarketSession.EARLY_CLOSE:
            emoji = "[ALARM]"
            tag = f"Early Close ({self._early_close_reason})"
        elif primary_session == MarketSession.CRYPTO:
            emoji = "[BTC]"
            tag = "Crypto Only"
        else:
            session_emoji = {
                MarketSession.ASIAN: "[EMOJI]",
                MarketSession.EUROPEAN: "[EMOJI]",
                MarketSession.US: "[EMOJI]",
                MarketSession.CLOSED: "[LOCK]",
            }
            emoji = session_emoji.get(primary_session, "[CLOCK]")
            peak_tag = " [PEAK]" if is_peak else ""
            tag = f"{primary_session.value}{peak_tag}"
        
        markets_str = ", ".join(active_markets[:3])
        
        return f"{emoji} {tag} | Markets: {markets_str}"

    def should_allow_new_trades(self, ticker: str = "") -> bool:
        """
        Check if new trades should be allowed based on current session.
        
        Returns False for:
        - Saturday (Close-Only/Audit day) — except crypto which trades 24/7
        - US/HK Holidays (for affected markets)
        """
        active_markets, primary_session = self.detect_active_sessions()

        if primary_session == MarketSession.ALWAYS_OPEN:
            return True
        
        # Saturday = Close only, no new trades — EXCEPT crypto trades 24/7
        if primary_session == MarketSession.SATURDAY_AUDIT:
            if ticker and is_crypto_ticker(ticker):
                return True
            return False
        
        # Sunday = Crypto only, but new trades allowed
        if primary_session == MarketSession.CRYPTO:
            return True
        
        # US Holiday = No new US stock trades
        if self._is_holiday_us and primary_session == MarketSession.HOLIDAY:
            return False
        
        # HK Holiday = No new HK stock trades
        if self._is_holiday_hk and primary_session == MarketSession.HOLIDAY:
            return False
        
        # Normal trading days = allow new trades
        return True

    def get_early_close_alert(self) -> str:
        """Get early close alert message if applicable."""
        if self._is_early_close:
            return f"[ALARM] [ALERT] Market closing early: {self._early_close_reason}. Tightening Stop Losses."
        return ""
