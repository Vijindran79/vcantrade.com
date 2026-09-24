import pytest

from app.config import Settings
from app.orders import OrderConstructionError, build_entry, build_flatten
from app.symbols import resolve


def _settings(**kw):
    base = dict(
        TRADOVATE_APP_ID="x", TRADOVATE_CID="x", TRADOVATE_SEC="x",
        WEBHOOK_API_KEY="k", TRADOVATE_ACCOUNT_ID=1, TRADOVATE_ACCOUNT_SPEC="Trials",
        REQUIRE_EXPLICIT_STOP="true", DEFAULT_STOP_TICKS="40", TICK_SIZE_NQ="0.25",
    )
    base.update({k.upper(): v for k, v in kw.items()})
    return Settings(**base)


def test_buy_bracket_has_stop_and_tp():
    s = _settings()
    spec = resolve("NQZ6")
    bo = build_entry(
        settings=s, spec=spec, action="BUY", quantity=1,
        entry_price=21000.0, stop_loss=20980.0, take_profit=21040.0,
    )
    p = bo.payload
    assert p["action"] == "Buy"
    assert p["symbol"] == "NQZ6"
    assert p["orderQty"] == 1
    assert p["orderType"] == "Market"
    assert p["isAutomated"] is True
    assert p["bracket"]["stopLoss"]["stopPrice"] == 20980.0
    assert p["bracket"]["takeProfit"]["limitPrice"] == 21040.0


def test_sell_bracket_stop_above_entry():
    s = _settings()
    spec = resolve("NQZ6")
    bo = build_entry(
        settings=s, spec=spec, action="SELL", quantity=2,
        entry_price=21000.0, stop_loss=21020.0, take_profit=20960.0,
    )
    assert bo.payload["action"] == "Sell"
    assert bo.payload["bracket"]["stopLoss"]["stopPrice"] == 21020.0


def test_stop_rounded_to_tick():
    s = _settings()
    spec = resolve("NQZ6")
    bo = build_entry(
        settings=s, spec=spec, action="BUY", quantity=1,
        entry_price=21000.0, stop_loss=20980.13,
    )
    assert bo.payload["bracket"]["stopLoss"]["stopPrice"] == 20980.25


def test_missing_stop_rejected_when_required():
    s = _settings(REQUIRE_EXPLICIT_STOP="true")
    spec = resolve("NQZ6")
    with pytest.raises(OrderConstructionError, match="stop_loss"):
        build_entry(settings=s, spec=spec, action="BUY", quantity=1, entry_price=21000.0)


def test_default_stop_derived_when_allowed():
    s = _settings(REQUIRE_EXPLICIT_STOP="false", DEFAULT_STOP_TICKS="40")
    spec = resolve("NQZ6")
    bo = build_entry(settings=s, spec=spec, action="BUY", quantity=1, entry_price=21000.0)
    assert bo.payload["bracket"]["stopLoss"]["stopPrice"] == 21000.0 - 40 * 0.25


def test_buy_stop_above_entry_rejected():
    s = _settings()
    spec = resolve("NQZ6")
    with pytest.raises(OrderConstructionError, match="below entry"):
        build_entry(
            settings=s, spec=spec, action="BUY", quantity=1,
            entry_price=21000.0, stop_loss=21010.0,
        )


def test_sell_tp_above_entry_rejected():
    s = _settings()
    spec = resolve("NQZ6")
    with pytest.raises(OrderConstructionError, match="below entry"):
        build_entry(
            settings=s, spec=spec, action="SELL", quantity=1,
            entry_price=21000.0, stop_loss=21020.0, take_profit=21010.0,
        )


def test_zero_quantity_rejected():
    s = _settings()
    spec = resolve("NQZ6")
    with pytest.raises(OrderConstructionError):
        build_entry(settings=s, spec=spec, action="BUY", quantity=0, entry_price=21000.0, stop_loss=20990.0)


def test_flatten_payload():
    s = _settings()
    spec = resolve("NQZ6")
    p = build_flatten(settings=s, spec=spec, side="Sell", quantity=2)
    assert p["action"] == "Sell"
    assert p["orderQty"] == 2
    assert "bracket" not in p
    assert p["isAutomated"] is True
