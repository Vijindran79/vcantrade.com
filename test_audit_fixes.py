"""Regression tests for the audit-identified execution & safety defects.

Covers:
- Stale-price guard: baseline recording, freeze detection, auto-heal trigger,
  and the frozen-duration calculation on recovery (core/browser_agent.py).
- Risk-cap logging: PositionSizer.evaluate() must not raise NameError when
  the 1.5% max-risk ceiling is hit (core/risk_manager.py).
- Black Friday early-close detection: date vs datetime comparison fix
  (core/market_sessions.py).
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from core.browser_agent import BrowserAgent
from core.market_sessions import MarketSessionDetector
from core.risk_manager import PositionSizer


# ---------------------------------------------------------------------------
# Stale-price guard (core/browser_agent.py)
# ---------------------------------------------------------------------------

def _make_agent() -> BrowserAgent:
    agent = BrowserAgent()
    agent._stale_threshold = 0.2          # seconds — keep tests fast
    agent._heal_cooldown = 0.0            # allow immediate heal
    agent._heal_frozen_tab = AsyncMock()  # record heal triggers
    return agent


def test_stale_guard_records_baseline_on_first_observation():
    """First price read must arm the guard (baseline value + timestamp)."""
    agent = _make_agent()
    assert agent._last_price_value == 0.0
    assert agent._last_price_change_time == 0.0

    with patch("core.data_feed.data_feed.get_bars", return_value=[{"close": 100.0}]):
        price = asyncio.run(agent.get_current_price("TEST"))

    assert price == 100.0
    assert agent._last_price_value == 100.0, "baseline price must be recorded on first observation"
    assert agent._last_price_change_time > 0.0, "baseline timestamp must be recorded on first observation"
    assert agent._feed_stale is False
    agent._heal_frozen_tab.assert_not_awaited()


def test_stale_guard_marks_frozen_feed_and_triggers_heal():
    """A frozen price beyond the threshold must flip _feed_stale and heal."""
    agent = _make_agent()
    with patch("core.data_feed.data_feed.get_bars", return_value=[{"close": 100.0}]):
        asyncio.run(agent.get_current_price("TEST"))          # arms baseline
        assert agent._feed_stale is False

        # Simulate the price being frozen far longer than the threshold.
        agent._last_price_change_time = time.time() - 999.0
        price = asyncio.run(agent.get_current_price("TEST"))  # same price

    assert price == 100.0
    assert agent._feed_stale is True, "_feed_stale must turn True when price data freezes"
    agent._heal_frozen_tab.assert_awaited_once(), "_heal_frozen_tab() must trigger on a frozen feed"


def test_stale_guard_recovers_on_price_change_with_correct_duration(caplog):
    """Recovery must clear the flag and log the actual frozen duration."""
    agent = _make_agent()
    frozen_for_expected = 999.0
    with caplog.at_level(logging.INFO, logger="core.browser_agent"):
        with patch("core.data_feed.data_feed.get_bars", return_value=[{"close": 100.0}]):
            asyncio.run(agent.get_current_price("TEST"))                  # baseline
            agent._last_price_change_time = time.time() - frozen_for_expected
            asyncio.run(agent.get_current_price("TEST"))                  # -> stale

        assert agent._feed_stale is True

        with patch("core.data_feed.data_feed.get_bars", return_value=[{"close": 101.0}]):
            price = asyncio.run(agent.get_current_price("TEST"))          # price moved

    assert price == 101.0
    assert agent._feed_stale is False, "price movement must clear _feed_stale"
    assert agent._last_price_value == 101.0

    recovery_lines = [r.getMessage() for r in caplog.records if "recovered" in r.getMessage()]
    assert recovery_lines, "recovery must be logged"
    # The old code logged 0s (it computed now - _last_price_change_time AFTER
    # resetting the timestamp). It must now report ~999s.
    assert "was frozen for 999" in recovery_lines[-1], (
        f"expected frozen duration in recovery log, got: {recovery_lines[-1]}"
    )


def test_stale_guard_heal_respects_cooldown():
    """Auto-heal must not reload the tab more often than the cooldown."""
    agent = _make_agent()
    agent._heal_cooldown = 3600.0
    with patch("core.data_feed.data_feed.get_bars", return_value=[{"close": 100.0}]):
        asyncio.run(agent.get_current_price("TEST"))
        agent._last_price_change_time = time.time() - 999.0
        asyncio.run(agent.get_current_price("TEST"))   # stale -> heal #1
        asyncio.run(agent.get_current_price("TEST"))   # still stale, within cooldown
    agent._heal_frozen_tab.assert_awaited_once()


# ---------------------------------------------------------------------------
# Risk cap logging (core/risk_manager.py)
# ---------------------------------------------------------------------------

def test_risk_cap_ceiling_logs_without_nameerror(caplog):
    """risk_pct above the 1.5% equity ceiling must cap and log — not crash."""
    # risk_amount = 10000 * 2% = 200 > ceiling 10000 * 1.5% = 150 -> cap path
    sizer = PositionSizer(balance=10000.0, risk_pct=2.0, open_risk=0.0)
    with caplog.at_level(logging.WARNING, logger="core.risk_manager"):
        result = sizer.evaluate(
            entry_price=100.0,
            side="BUY",
            levels={"supports": [99.0], "resistances": [105.0]},
        )

    assert result["ok"] is True
    # Previously this path raised NameError('logger') before returning.
    assert result["risk_amount"] == pytest.approx(150.0), "risk must be capped at 1.5% of equity"
    assert result["quantity"] > 0
    assert "Risk amount capped" in caplog.text
    assert "1.5" in caplog.text


def test_risk_below_ceiling_does_not_log(caplog):
    """Normal risk sizing stays under the ceiling and logs nothing."""
    sizer = PositionSizer(balance=10000.0, risk_pct=1.0, open_risk=0.0)
    with caplog.at_level(logging.WARNING, logger="core.risk_manager"):
        result = sizer.evaluate(
            entry_price=100.0,
            side="BUY",
            levels={"supports": [99.0], "resistances": [105.0]},
        )

    assert result["ok"] is True
    assert result["risk_amount"] == pytest.approx(100.0)
    assert "Risk amount capped" not in caplog.text


# ---------------------------------------------------------------------------
# Black Friday early close (core/market_sessions.py)
# ---------------------------------------------------------------------------

def _detector_at(dt: datetime) -> MarketSessionDetector:
    detector = MarketSessionDetector()
    detector.get_current_datetime = lambda: dt  # instance-level override
    return detector


def test_black_friday_early_close_detected():
    """2025-11-28 (day after Thanksgiving) must be flagged as early close."""
    detector = _detector_at(datetime(2025, 11, 28, 15, 0, tzinfo=timezone.utc))
    is_early, reason, hour = detector._check_early_close()
    assert is_early is True, "Black Friday must be detected (date == datetime bug fixed)"
    assert "Black Friday" in reason
    assert hour == 18


def test_ordinary_thursday_is_not_early_close():
    detector = _detector_at(datetime(2025, 11, 20, 15, 0, tzinfo=timezone.utc))
    is_early, reason, hour = detector._check_early_close()
    assert is_early is False
    assert reason == ""
    assert hour is None


def test_christmas_eve_early_close_still_detected():
    detector = _detector_at(datetime(2025, 12, 24, 15, 0, tzinfo=timezone.utc))
    is_early, reason, hour = detector._check_early_close()
    assert is_early is True
    assert hour == 18
