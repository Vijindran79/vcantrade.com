import time
from pathlib import Path

import pytest

from app.config import Settings
from app.risk import RiskGuard


def _settings(tmp_path, **kw):
    base = dict(
        TRADOVATE_APP_ID="x", TRADOVATE_CID="x", TRADOVATE_SEC="x",
        WEBHOOK_API_KEY="k", TRADOVATE_ACCOUNT_ID=1,
        MAX_DAILY_LOSS_USD="1000", TRAILING_DRAWDOWN_USD="1500",
        MAX_CONTRACTS_PER_ORDER="2", MAX_TOTAL_OPEN_POSITIONS="2",
        DUPLICATE_SIGNAL_WINDOW_SEC="5",
        STATE_FILE=str(tmp_path / "risk.json"),
        KILL_SWITCH_FILE=str(tmp_path / "KILL"),
    )
    base.update({k.upper(): v for k, v in kw.items()})
    return Settings(**base)


def test_first_order_allowed(tmp_path):
    g = RiskGuard(_settings(tmp_path), starting_equity_usd=50000)
    d = g.check_order(signal_id="s1", quantity=1, symbol="NQZ6", action="BUY")
    assert d.allowed


def test_duplicate_within_window_rejected(tmp_path):
    g = RiskGuard(_settings(tmp_path), starting_equity_usd=50000)
    assert g.check_order(signal_id="dup", quantity=1, symbol="NQZ6", action="BUY").allowed
    d = g.check_order(signal_id="dup", quantity=1, symbol="NQZ6", action="BUY")
    assert not d.allowed
    assert d.rule == "DUPLICATE"


def test_duplicate_outside_window_allowed(tmp_path):
    g = RiskGuard(_settings(tmp_path, DUPLICATE_SIGNAL_WINDOW_SEC="1"), starting_equity_usd=50000)
    assert g.check_order(signal_id="x", quantity=1, symbol="NQZ6", action="BUY").allowed
    time.sleep(1.1)
    assert g.check_order(signal_id="x", quantity=1, symbol="NQZ6", action="BUY").allowed


def test_contract_cap(tmp_path):
    g = RiskGuard(_settings(tmp_path, MAX_CONTRACTS_PER_ORDER="2"), starting_equity_usd=50000)
    d = g.check_order(signal_id="big", quantity=5, symbol="NQZ6", action="BUY")
    assert not d.allowed
    assert d.rule == "MAX_CONTRACTS"


def test_open_position_cap(tmp_path):
    g = RiskGuard(_settings(tmp_path, MAX_TOTAL_OPEN_POSITIONS="1"), starting_equity_usd=50000)
    assert g.check_order(signal_id="a", quantity=1, symbol="NQZ6", action="BUY").allowed
    g.on_position_opened()
    d = g.check_order(signal_id="b", quantity=1, symbol="MNQZ6", action="BUY")
    assert not d.allowed
    assert d.rule == "MAX_POSITIONS"


def test_flatten_bypasses_position_cap(tmp_path):
    g = RiskGuard(_settings(tmp_path, MAX_TOTAL_OPEN_POSITIONS="0"), starting_equity_usd=50000)
    d = g.check_order(signal_id="f", quantity=1, symbol="NQZ6", action="FLATTEN")
    assert d.allowed


def test_daily_loss_ceiling(tmp_path):
    g = RiskGuard(_settings(tmp_path, MAX_DAILY_LOSS_USD="500"), starting_equity_usd=50000)
    g.on_position_closed(realized_pnl_usd=-600)
    d = g.check_order(signal_id="after-loss", quantity=1, symbol="NQZ6", action="BUY")
    assert not d.allowed
    assert d.rule == "DAILY_LOSS"


def test_trailing_drawdown_breaker(tmp_path):
    g = RiskGuard(_settings(tmp_path, TRAILING_DRAWDOWN_USD="1000"), starting_equity_usd=50000)
    g.update_equity(51000)
    g.update_equity(49900)
    d = g.check_order(signal_id="dd", quantity=1, symbol="NQZ6", action="BUY")
    assert not d.allowed
    assert d.rule == "TRAILING_DD"


def test_trailing_drawdown_allows_small_dip(tmp_path):
    g = RiskGuard(_settings(tmp_path, TRAILING_DRAWDOWN_USD="1000"), starting_equity_usd=50000)
    g.update_equity(51000)
    g.update_equity(50500)
    assert g.check_order(signal_id="ok", quantity=1, symbol="NQZ6", action="BUY").allowed


def test_kill_switch(tmp_path):
    s = _settings(tmp_path)
    g = RiskGuard(s, starting_equity_usd=50000)
    Path(s.kill_switch_file).write_text("1")
    d = g.check_order(signal_id="k", quantity=1, symbol="NQZ6", action="BUY")
    assert not d.allowed
    assert d.rule == "KILL_SWITCH"


def test_state_persists_across_restart(tmp_path):
    s = _settings(tmp_path, MAX_DAILY_LOSS_USD="500")
    g1 = RiskGuard(s, starting_equity_usd=50000)
    g1.on_position_closed(realized_pnl_usd=-400)
    g1.on_position_opened()

    g2 = RiskGuard(s, starting_equity_usd=50000)
    snap = g2.snapshot()
    assert snap["realized_pnl_today_usd"] == pytest.approx(-400)
    assert snap["open_positions"] == 1


def test_snapshot_shape(tmp_path):
    g = RiskGuard(_settings(tmp_path), starting_equity_usd=50000)
    snap = g.snapshot()
    for key in (
        "day", "equity_usd", "equity_hwm_usd", "trailing_drawdown_usd",
        "realized_pnl_today_usd", "open_positions", "kill_switch",
    ):
        assert key in snap
