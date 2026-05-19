"""
VcanTrade AI - Financial Safety Manager

Handles:
1. Micro-Lot Auto-Switch: When daily loss hits 70%, automatically reduce position sizes
2. News Filter: Pause trading 15min before high-impact news, resume 15min after

Protects the account while still collecting data during risky periods.
"""

import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum

import requests
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Micro-Lot Mode
# ---------------------------------------------------------------------------

class PositionSizeMode(str, Enum):
    NORMAL = "normal"
    MICRO = "micro"  # 10% of normal size
    MINIMAL = "minimal"  # 5% of normal size


class FinancialSafetyManager:
    """
    Manages financial safety controls:
    - Auto-switches to micro-lots when approaching daily loss limits
    - Pauses trading during high-impact news events
    - Tracks all safety interventions
    """

    def __init__(self):
        # Micro-lot settings
        self.current_mode = PositionSizeMode.NORMAL
        self.micro_lot_threshold = 0.70  # 70% of daily loss triggers micro-lot
        self.minimal_lot_threshold = 0.90  # 90% triggers minimal mode
        
        # News filter settings
        self.news_filter_enabled = True
        self.news_pause_before_minutes = 15
        self.news_pause_after_minutes = 15
        self.upcoming_news: List[Dict] = []
        self.last_news_check: Optional[datetime] = None
        self.news_check_interval = 300  # 5 minutes
        
        # Trading state
        self.trading_paused = False
        self.pause_reason = ""
        self.interventions_count = 0
        self.runtime_mode = "TEACHER"
        self.last_news_scrape_failed = False
        self.last_news_scrape_error = ""
        
        logger.info("[MONEY] Financial Safety Manager initialized")

    def set_runtime_mode(self, mode: str) -> None:
        self.runtime_mode = str(mode or "TEACHER").upper().strip() or "TEACHER"

    def _build_http_timeout(self):
        import aiohttp

        return aiohttp.ClientTimeout(
            total=max(float(config.NEWS_REQUEST_TIMEOUT), 1.0),
            connect=max(float(config.NEWS_CONNECT_TIMEOUT), 0.5),
            sock_connect=max(float(config.NEWS_CONNECT_TIMEOUT), 0.5),
            sock_read=max(float(config.NEWS_REQUEST_TIMEOUT), 1.0),
        )

    def _build_http_session(self, headers: Optional[Dict[str, str]] = None):
        import aiohttp

        timeout = self._build_http_timeout()
        connector = None
        try:
            from aiohttp.resolver import AsyncResolver

            connector = aiohttp.TCPConnector(
                resolver=AsyncResolver(nameservers=list(config.NEWS_DNS_FALLBACK)),
                ttl_dns_cache=300,
                ssl=False,
            )
        except Exception as exc:
            logger.debug(f"News scraper DNS fallback unavailable, using system resolver: {exc}")

        return aiohttp.ClientSession(timeout=timeout, headers=headers, connector=connector)

    # ===================================================================
    # MICRO-LOT AUTO-SWITCH
    # ===================================================================

    def check_position_size_mode(self, daily_pnl: float, max_daily_loss: float) -> PositionSizeMode:
        """
        Check if we should switch to micro-lot or minimal mode.
        
        Args:
            daily_pnl: Current daily P&L (negative = loss)
            max_daily_loss: Maximum allowed daily loss
            
        Returns:
            PositionSizeMode: NORMAL, MICRO, or MINIMAL
        """
        if max_daily_loss <= 0:
            return PositionSizeMode.NORMAL
        
        loss_ratio = abs(daily_pnl) / max_daily_loss
        
        # Check if we should switch modes
        if loss_ratio >= self.minimal_lot_threshold:
            if self.current_mode != PositionSizeMode.MINIMAL:
                self.current_mode = PositionSizeMode.MINIMAL
                self.interventions_count += 1
                logger.warning(
                    f"[RED] MINIMAL LOT MODE ACTIVATED | "
                    f"Daily loss: ${abs(daily_pnl):.2f} / ${max_daily_loss:.2f} "
                    f"({loss_ratio:.0%}) | "
                    f"Position size reduced to 5% of normal"
                )
            return PositionSizeMode.MINIMAL
            
        elif loss_ratio >= self.micro_lot_threshold:
            if self.current_mode != PositionSizeMode.MICRO:
                self.current_mode = PositionSizeMode.MICRO
                self.interventions_count += 1
                logger.warning(
                    f"[YELLOW] MICRO-LOT MODE ACTIVATED | "
                    f"Daily loss: ${abs(daily_pnl):.2f} / ${max_daily_loss:.2f} "
                    f"({loss_ratio:.0%}) | "
                    f"Position size reduced to 10% of normal"
                )
            return PositionSizeMode.MICRO
            
        else:
            if self.current_mode != PositionSizeMode.NORMAL:
                old_mode = self.current_mode
                self.current_mode = PositionSizeMode.NORMAL
                logger.info(
                    f"[GREEN] RETURNED TO NORMAL MODE | "
                    f"Daily loss ratio decreased to {loss_ratio:.0%}"
                )
            return PositionSizeMode.NORMAL

    def get_position_size_multiplier(self) -> float:
        """
        Get position size multiplier based on current mode.
        
        Returns:
            float: 1.0 for normal, 0.10 for micro, 0.05 for minimal
        """
        multipliers = {
            PositionSizeMode.NORMAL: 1.0,
            PositionSizeMode.MICRO: 0.10,  # 10% of normal
            PositionSizeMode.MINIMAL: 0.05,  # 5% of normal
        }
        return multipliers.get(self.current_mode, 1.0)

    def calculate_safe_position_size(
        self,
        base_amount: float,
        daily_pnl: float,
        max_daily_loss: float,
    ) -> Tuple[float, PositionSizeMode]:
        """
        Calculate safe position size based on current risk level.
        
        Args:
            base_amount: Normal position size
            daily_pnl: Current daily P&L
            max_daily_loss: Maximum allowed daily loss
            
        Returns:
            Tuple of (safe_amount, mode)
        """
        mode = self.check_position_size_mode(daily_pnl, max_daily_loss)
        multiplier = self.get_position_size_multiplier()
        
        safe_amount = base_amount * multiplier
        
        if multiplier < 1.0:
            logger.info(
                f"[RULER] Position size adjusted: ${base_amount:.2f} -> ${safe_amount:.2f} "
                f"({multiplier:.0%}) | Mode: {mode.value}"
            )
        
        return safe_amount, mode

    # ===================================================================
    # NEWS FILTER
    # ===================================================================

    async def check_upcoming_news(self) -> List[Dict]:
        """
        Scrape economic calendar for high-impact news events.
        Returns list of upcoming news in the next 2 hours.
        """
        try:
            # Check if we've checked recently
            if (
                self.last_news_check
                and (datetime.now() - self.last_news_check).total_seconds() 
                < self.news_check_interval
            ):
                return self.upcoming_news
            
            self.last_news_check = datetime.now()
            self.last_news_scrape_failed = False
            self.last_news_scrape_error = ""
            
            # Scrape Forex Factory economic calendar
            news_events = await self._scrape_economic_calendar()
            
            # Filter for high-impact events in next 2 hours
            self.upcoming_news = self._filter_high_impact_news(news_events)
            
            if self.upcoming_news:
                logger.warning(
                    f"[NEWS] High-impact news detected: {len(self.upcoming_news)} events"
                )
                for event in self.upcoming_news:
                    logger.warning(
                        f"   [BULLET] {event['time']} - {event['currency']} {event['event']} "
                        f"(Impact: {event['impact']})"
                    )
            
            return self.upcoming_news
            
        except Exception as e:
            self.last_news_scrape_failed = True
            self.last_news_scrape_error = str(e)
            if self.runtime_mode == "AUTONOMOUS":
                logger.warning(
                    "News scrape failed in AUTONOMOUS mode - failing open so execution can continue: %s",
                    e,
                )
                self.upcoming_news = []
                self.trading_paused = False
                self.pause_reason = ""
                return []
            logger.error(f"Failed to check economic calendar: {e}")
            return []

    async def _scrape_economic_calendar(self) -> List[Dict]:
        """Scrape economic calendar from web sources."""
        news_events = []
        
        try:
            # Source 1: Forex Factory (most reliable for forex)
            try:
                forex_events = await asyncio.wait_for(
                    self._scrape_forex_factory(),
                    timeout=max(float(config.NEWS_REQUEST_TIMEOUT) + 1.0, 2.0),
                )
            except asyncio.TimeoutError:
                logger.warning("Forex Factory scrape timed out after %.1fs", float(config.NEWS_REQUEST_TIMEOUT))
                forex_events = []
            news_events.extend(forex_events)
            
            # Source 2: Investing.com (broader coverage)
            try:
                investing_events = await asyncio.wait_for(
                    self._scrape_investing_com(),
                    timeout=max(float(config.NEWS_REQUEST_TIMEOUT) + 1.0, 2.0),
                )
            except asyncio.TimeoutError:
                logger.warning("Investing.com scrape timed out after %.1fs", float(config.NEWS_REQUEST_TIMEOUT))
                investing_events = []
            news_events.extend(investing_events)
            
            # Deduplicate events
            unique_events = self._deduplicate_events(news_events)
            
            return unique_events
            
        except Exception as e:
            self.last_news_scrape_failed = True
            self.last_news_scrape_error = str(e)
            if self.runtime_mode == "AUTONOMOUS":
                logger.warning(
                    "Economic calendar scraping failed in AUTONOMOUS mode - failing open: %s",
                    e,
                )
                return []
            logger.error(f"Economic calendar scraping failed: {e}")
            return []

    async def _scrape_forex_factory(self) -> List[Dict]:
        """Scrape Forex Factory economic calendar."""
        try:
            url = "https://www.forexfactory.com/calendar"

            async with self._build_http_session() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return []
                    
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    events = []
                    # Parse calendar rows
                    rows = soup.select('tr.calendar__row')
                    
                    for row in rows[:50]:  # Limit to next 50 events
                        try:
                            time_cell = row.select_one('td.calendar__time')
                            currency_cell = row.select_one('td.calendar__currency')
                            event_cell = row.select_one('td.calendar__event')
                            impact_cell = row.select_one('td.calendar__impact')
                            
                            if not all([time_cell, currency_cell, event_cell]):
                                continue
                            
                            # Extract impact (red = high, orange = medium, yellow = low)
                            impact_icon = impact_cell.select_one('span.calendar__impact-icon') if impact_cell else None
                            impact = "LOW"
                            if impact_icon:
                                icon_class = impact_icon.get('class', [])
                                if 'red' in str(icon_class):
                                    impact = "HIGH"
                                elif 'orange' in str(icon_class):
                                    impact = "MEDIUM"
                            
                            event_time = time_cell.get_text(strip=True)
                            currency = currency_cell.get_text(strip=True)
                            event_name = event_cell.get_text(strip=True)
                            
                            # Parse time
                            try:
                                event_dt = datetime.strptime(event_time, "%I:%M%p")
                                event_dt = event_dt.replace(
                                    year=datetime.now().year,
                                    month=datetime.now().month,
                                    day=datetime.now().day
                                )
                            except:
                                event_dt = datetime.now()
                            
                            events.append({
                                "time": event_time,
                                "datetime": event_dt,
                                "currency": currency,
                                "event": event_name,
                                "impact": impact,
                                "source": "ForexFactory",
                            })
                        except Exception as e:
                            logger.debug(f"Failed to parse calendar row: {e}")
                            continue
                    
                    return events
                    
        except Exception as e:
            self.last_news_scrape_failed = True
            self.last_news_scrape_error = str(e)
            err_msg = str(e)
            if "DNS" in err_msg or "Cannot connect" in err_msg:
                logger.info("Forex Factory unreachable (network/DNS) - skipping news check")
            else:
                logger.error(f"Forex Factory scrape failed: {e}")
            return []

    async def _scrape_investing_com(self) -> List[Dict]:
        """Scrape Investing.com economic calendar."""
        try:
            url = "https://www.investing.com/economic-calendar/"

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }

            async with self._build_http_session(headers=headers) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return []
                    
                    html = await response.text()
                    # Simple parsing - Investing.com often changes structure
                    # so we keep this basic
                    return []
                    
        except Exception as e:
            self.last_news_scrape_failed = True
            self.last_news_scrape_error = str(e)
            err_msg = str(e)
            if "DNS" in err_msg or "Cannot connect" in err_msg:
                logger.info("Investing.com unreachable (network/DNS) - skipping news check")
            else:
                logger.error(f"Investing.com scrape failed: {e}")
            return []

    def _deduplicate_events(self, events: List[Dict]) -> List[Dict]:
        """Remove duplicate events from multiple sources."""
        seen = set()
        unique = []
        
        for event in events:
            key = f"{event['time']}_{event['currency']}_{event['event']}"
            if key not in seen:
                seen.add(key)
                unique.append(event)
        
        return unique

    def _filter_high_impact_news(self, events: List[Dict]) -> List[Dict]:
        """Filter for high-impact events in the next 2 hours."""
        now = datetime.now()
        high_impact = []
        
        for event in events:
            event_dt = event.get("datetime")
            if not event_dt:
                continue
            
            # Check if event is in next 2 hours
            time_diff = (event_dt - now).total_seconds()
            if 0 <= time_diff <= 7200:  # 2 hours in seconds
                if event.get("impact") in ["HIGH", "MEDIUM"]:
                    high_impact.append(event)
        
        return high_impact

    def should_pause_trading(self) -> Tuple[bool, str]:
        """
        Check if we should pause trading due to upcoming news.
        
        Returns:
            Tuple of (should_pause, reason)
        """
        if not self.news_filter_enabled:
            return False, ""
        
        now = datetime.now()
        
        # Check upcoming news
        for event in self.upcoming_news:
            event_dt = event.get("datetime")
            if not event_dt:
                continue
            
            time_until = (event_dt - now).total_seconds()
            
            # Pause 15 minutes before high-impact news
            if 0 < time_until <= (self.news_pause_before_minutes * 60):
                return True, f"High-impact news in {int(time_until / 60)}min: {event['event']}"
            
            # Resume 15 minutes after news
            time_after = (now - event_dt).total_seconds()
            if 0 < time_after <= (self.news_pause_after_minutes * 60):
                return True, f"Recent news ({int(time_after / 60)}min ago): {event['event']}"
        
        return False, ""

    async def update_news_filter(self):
        """Update the news filter with latest events.

        FAIL CLOSED in AUTONOMOUS mode. If Forex Factory cannot be reached and
        we therefore have no event list, we PAUSE trading instead of charging
        ahead. The user can override with NEWS_FILTER_FAIL_OPEN=true in .env if
        they explicitly accept news risk.
        """
        await self.check_upcoming_news()

        fail_open = bool(getattr(config, "NEWS_FILTER_FAIL_OPEN", False))

        if self.runtime_mode == "AUTONOMOUS" and self.last_news_scrape_failed and not self.upcoming_news:
            if fail_open:
                if self.trading_paused:
                    logger.warning(
                        "News filter FAIL-OPEN override: clearing pause after scraper error: %s",
                        self.last_news_scrape_error or "unknown error",
                    )
                self.trading_paused = False
                self.pause_reason = ""
                return
            # Default: fail closed — pause until the scraper recovers.
            if not self.trading_paused:
                logger.warning(
                    "[NEWS-FAIL-CLOSED] Forex Factory unreachable; pausing trading. Error: %s",
                    self.last_news_scrape_error or "unknown error",
                )
            self.trading_paused = True
            self.pause_reason = (
                "News feed unreachable — paused until next successful scrape "
                "(set NEWS_FILTER_FAIL_OPEN=true to override)."
            )
            return

        # Check if we need to pause/resume
        should_pause, reason = self.should_pause_trading()

        if should_pause and not self.trading_paused:
            self.trading_paused = True
            self.pause_reason = reason
            logger.warning(f"[STOP] TRADING PAUSED: {reason}")

        elif not should_pause and self.trading_paused:
            self.trading_paused = False
            old_reason = self.pause_reason
            self.pause_reason = ""
            logger.info(f"[PLAY] TRADING RESUMED: {old_reason}")

    # ===================================================================
    # DASHBOARD DATA
    # ===================================================================

    def get_safety_status(self) -> Dict:
        """Get current safety manager status for dashboard."""
        return {
            "position_mode": self.current_mode.value,
            "position_multiplier": self.get_position_size_multiplier(),
            "trading_paused": self.trading_paused,
            "pause_reason": self.pause_reason,
            "news_filter_enabled": self.news_filter_enabled,
            "upcoming_news_count": len(self.upcoming_news),
            "interventions_count": self.interventions_count,
            "next_news": self.upcoming_news[0] if self.upcoming_news else None,
        }

    def reset_daily_counters(self):
        """Reset daily counters (call at start of each trading day)."""
        self.current_mode = PositionSizeMode.NORMAL
        self.trading_paused = False
        self.pause_reason = ""
        logger.info("[REFRESH] Financial Safety Manager daily reset complete")
