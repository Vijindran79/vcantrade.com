"""End-to-end webhook -> bracket-order tests (DRY_RUN, no broker, no network).

These are the counterpart to ``tests/test_orders.py`` (unit) and
``tests/test_risk.py`` (unit): here the *whole* FastAPI app runs, a real HTTP
POST is made through ``TestClient``, and the resulting order is inspected in the
simulated broker. That is what proves the TradingView loop works end to end
without waiting for demo credentials.

Every test asserts against ``/sim/orders`` — the recorded broker payload — not
just against the HTTP response, so a "200 OK" that forgot to attach the bracket
still fails.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app.symbols import resolve

API_KEY = "test-key-0123456789abcdef"

# The contract the Pine script emits. Keep in sync with
# tradingview/vcantrade_webhook_alerts.pine and app/models.py.
ALERT_TEMPLATE = {
    "signal_id": "MNQ1!-1234-1758000000000",
    "action": "BUY",
    "symbol": "MNQ1!",
    "quantity": 1,
    "entry_price": 21000.0,
    "stop_loss": 20980.0,
    "take_profit": 21040.0,
}


def _base_env(tmp_path, **overrides) -> dict[str, str]:
    env = {
        "DRY_RUN": "true",
        "TRADOVATE_ENV": "demo",
        "WEBHOOK_API_KEY": API_KEY,
        "TRADOVATE_ACCOUNT_ID": "1",
        "TRADOVATE_ACCOUNT_SPEC": "Trials",
        "ALLOW_URL_KEY": "true",
        "STATE_FILE": str(tmp_path / "risk_state.json"),
        "KILL_SWITCH_FILE": str(tmp_path / "KILL_SWITCH"),
        "STARTING_EQUITY_USD": "50000",
        "MAX_DAILY_LOSS_USD": "1000",
        "TRAILING_DRAWDOWN_USD": "2000",
        "MAX_CONTRACTS_PER_ORDER": "2",
        "MAX_TOTAL_OPEN_POSITIONS": "2",
        "DUPLICATE_SIGNAL_WINDOW_SEC": "5",
        "REQUIRE_EXPLICIT_STOP": "true",
        "DEFAULT_STOP_TICKS": "40",
    }
    env.update({k: str(v) for k, v in overrides.items()})
    return env


@contextmanager
def boot(tmp_path, monkeypatch, **overrides):
    """Start the real app with DRY_RUN settings, which also starts the lifespan."""
    for key, value in _base_env(tmp_path, **overrides).items():
        monkeypatch.setenv(key, value)

    from app.config import get_settings

    get_settings.cache_clear()

    from app.main import app

    with TestClient(app) as client:
        yield client

    get_settings.cache_clear()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    with boot(tmp_path, monkeypatch) as c:
        yield c


def hdr() -> dict[str, str]:
    return {"X-API-Key": API_KEY, "Content-Type": "application/json"}


def alert(**overrides) -> dict:
    body = dict(ALERT_TEMPLATE)
    body.update(overrides)
    return body


def sim_orders(client: TestClient) -> dict:
    r = client.get("/sim/orders", headers=hdr())
    assert r.status_code == 200, r.text
    return r.json()


def sim_summary(client: TestClient) -> dict:
    return sim_orders(client)["summary"]


# --------------------------------------------------------------------- boot
def test_health_and_status_report_dry_run(client):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "env": "demo"}

    assert client.get("/status").status_code == 401

    status = client.get("/status", headers=hdr())
    assert status.status_code == 200
    body = status.json()
    assert body["dry_run"] is True
    assert body["account_spec"] == "Trials"
    assert body["account_id"] == 1
    assert body["sim"]["orders"] == 0
    assert body["sim"]["naked_orders"] == 0


def test_auth_required_on_webhook(client):
    assert client.post("/webhook/tradingview", json=alert()).status_code == 401
    assert (
        client.post(
            "/webhook/tradingview",
            json=alert(),
            headers={"X-API-Key": "wrong-key"},
        ).status_code
        == 401
    )


def test_bearer_token_also_accepted(client):
    r = client.post(
        "/webhook/tradingview",
        json=alert(),
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] is True


# ------------------------------------------------------- the core acceptance
def test_forced_buy_places_order_with_stop_and_tp_attached(client):
    """The headline scenario: one alert in, one bracketed order out."""
    r = client.post("/webhook/tradingview", json=alert(), headers=hdr())
    assert r.status_code == 200, r.text

    body = r.json()
    assert body["accepted"] is True
    assert body["signal_id"] == ALERT_TEMPLATE["signal_id"]
    assert body["quantity"] == 1
    assert body["order_id"] == "1"
    assert body["broker_status"] == "Working"
    assert body["symbol"] == resolve("MNQ1!").tradovate_symbol
    assert body["latency_ms"] >= 0

    recorded = sim_orders(client)
    assert recorded["summary"]["orders"] == 1
    assert recorded["summary"]["naked_orders"] == 0

    order = recorded["orders"][-1]
    assert order["action"] == "Buy"
    assert order["symbol"] == resolve("MNQ1!").tradovate_symbol
    assert order["quantity"] == 1
    assert order["order_type"] == "Market"
    assert order["is_automated"] is True
    assert order["account_spec"] == "Trials"

    # Bracket legs must ride along in the SAME order payload.
    assert order["bracket_attached"] is True
    assert order["stop_price"] == 20980.0
    assert order["take_profit_price"] == 21040.0
    assert order["payload"]["bracket"]["stopLoss"] == {
        "orderQty": 1,
        "stopPrice": 20980.0,
    }
    assert order["payload"]["bracket"]["takeProfit"] == {
        "orderQty": 1,
        "limitPrice": 21040.0,
    }


def test_forced_sell_places_order_with_stop_and_tp_attached(client):
    r = client.post(
        "/webhook/tradingview",
        json=alert(
            signal_id="sell-1",
            action="SELL",
            entry_price=21000.0,
            stop_loss=21020.0,
            take_profit=20960.0,
        ),
        headers=hdr(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] is True

    order = sim_orders(client)["orders"][-1]
    assert order["action"] == "Sell"
    assert order["stop_price"] == 21020.0
    assert order["take_profit_price"] == 20960.0
    assert order["payload"]["bracket"]["stopLoss"]["stopPrice"] == 21020.0


def test_stop_and_tp_are_rounded_to_the_tick(client):
    client.post(
        "/webhook/tradingview",
        json=alert(signal_id="tick-1", stop_loss=20980.13, take_profit=21040.12),
        headers=hdr(),
    )
    order = sim_orders(client)["orders"][-1]
    assert order["stop_price"] == 20980.25
    assert order["take_profit_price"] == 21040.0


def test_alert_contract_matches_the_pine_template(client):
    """The doc'd payload shape must be accepted verbatim (minus TV placeholders)."""
    payload = {
        "signal_id": "MNQ1!-4821-1758000000000",
        "action": "buy",
        "symbol": " mnq1! ",
        "quantity": 2,
        "entry_price": 21000,
        "stop_loss": 20990,
        "take_profit": 21020,
        "timestamp_ms": 1758000000000,
    }
    r = client.post("/webhook/tradingview", json=payload, headers=hdr())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] is True
    assert body["action"] == "BUY"
    assert body["quantity"] == 2

    order = sim_orders(client)["orders"][-1]
    assert order["action"] == "Buy"
    assert order["payload"]["bracket"]["stopLoss"]["orderQty"] == 2


# ---------------------------------------------------- TradingView URL-key auth
def test_url_key_in_path_is_accepted(client):
    """TradingView sends no custom headers, so the key may live in the URL."""
    r = client.post(f"/webhook/tradingview/{API_KEY}", json=alert(signal_id="urlpath-1"))
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] is True
    assert sim_summary(client)["orders"] == 1


def test_url_key_in_query_is_accepted(client):
    r = client.post(
        "/webhook/tradingview",
        params={"key": API_KEY},
        json=alert(signal_id="urlquery-1"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] is True


def test_wrong_url_key_is_rejected(client):
    assert client.post("/webhook/tradingview/nope", json=alert()).status_code == 401
    assert (
        client.post("/webhook/tradingview", params={"key": "nope"}, json=alert()).status_code
        == 401
    )
    assert sim_summary(client)["orders"] == 0


def test_url_key_can_be_disabled_for_proxied_deployments(tmp_path, monkeypatch):
    with boot(tmp_path, monkeypatch, ALLOW_URL_KEY="false") as client:
        assert (
            client.post(f"/webhook/tradingview/{API_KEY}", json=alert()).status_code == 401
        )
        # header auth (nginx injection) still works
        assert (
            client.post("/webhook/tradingview", json=alert(), headers=hdr()).status_code
            == 200
        )


def test_url_key_ip_allowlist_blocks_other_sources(tmp_path, monkeypatch):
    with boot(tmp_path, monkeypatch, TRADINGVIEW_IP_ALLOWLIST="52.89.214.238") as client:
        assert (
            client.post(f"/webhook/tradingview/{API_KEY}", json=alert()).status_code == 403
        )
        # header auth stays usable from the operator's machine
        assert (
            client.post("/webhook/tradingview", json=alert(), headers=hdr()).status_code
            == 200
        )


def test_empty_or_commented_allowlist_means_no_restriction(tmp_path, monkeypatch):
    """An empty or commented-out allowlist must not become a blocking entry.

    Regression: ``TRADINGVIEW_IP_ALLOWLIST=   # note`` parsed as a single
    allowlist entry, so every URL-key webhook -- the carrier a TradingView alert
    must use -- was answered with 403 from a perfectly valid source.
    """
    for i, value in enumerate(("", "# empty = any source IP")):
        case = tmp_path / f"case{i}"
        case.mkdir()
        with boot(case, monkeypatch, TRADINGVIEW_IP_ALLOWLIST=value) as client:
            r = client.post(f"/webhook/tradingview/{API_KEY}", json=alert(signal_id=f"allow-{i}"))
            assert r.status_code == 200, (value, r.text)
            assert r.json()["accepted"] is True, (value, r.text)


def test_ip_allowlist_parsing_ignores_inline_comments():
    from app.config import Settings

    s = Settings(TRADINGVIEW_IP_ALLOWLIST="52.89.214.238, 34.212.75.30 # TradingView egress")
    assert s.ip_allowlist == ["52.89.214.238", "34.212.75.30"]
    assert Settings(TRADINGVIEW_IP_ALLOWLIST="  ").ip_allowlist == []


# --------------------------------------------------------- guard rail matrix
def test_missing_stop_is_rejected_and_never_reaches_the_broker(client):
    payload = alert(signal_id="nostop-1")
    payload.pop("stop_loss")
    r = client.post("/webhook/tradingview", json=payload, headers=hdr())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] is False
    assert "[ORDER_BUILD]" in body["reason"]
    assert "stop_loss" in body["reason"]
    assert sim_summary(client)["orders"] == 0


def test_stop_on_the_wrong_side_is_rejected(client):
    r = client.post(
        "/webhook/tradingview",
        json=alert(signal_id="badstop-1", stop_loss=21010.0),
        headers=hdr(),
    )
    assert r.json()["accepted"] is False
    assert "[ORDER_BUILD]" in r.json()["reason"]
    assert sim_summary(client)["orders"] == 0


def test_unknown_symbol_is_rejected(client):
    r = client.post(
        "/webhook/tradingview",
        json=alert(signal_id="sym-1", symbol="ZZZ1!"),
        headers=hdr(),
    )
    assert r.json()["accepted"] is False
    assert "[SYMBOL]" in r.json()["reason"]
    assert sim_summary(client)["orders"] == 0


def test_duplicate_alert_is_filtered(client):
    first = client.post("/webhook/tradingview", json=alert(signal_id="dup-1"), headers=hdr())
    second = client.post("/webhook/tradingview", json=alert(signal_id="dup-1"), headers=hdr())
    assert first.json()["accepted"] is True
    assert second.json()["accepted"] is False
    assert "[DUPLICATE]" in second.json()["reason"]
    assert sim_summary(client)["orders"] == 1


def test_quantity_cap_is_enforced(client):
    r = client.post(
        "/webhook/tradingview", json=alert(signal_id="big-1", quantity=5), headers=hdr()
    )
    assert r.json()["accepted"] is False
    assert "[MAX_CONTRACTS]" in r.json()["reason"]
    assert sim_summary(client)["orders"] == 0


def test_position_cap_is_enforced(tmp_path, monkeypatch):
    with boot(tmp_path, monkeypatch, MAX_TOTAL_OPEN_POSITIONS="1") as client:
        first = client.post("/webhook/tradingview", json=alert(signal_id="p1"), headers=hdr())
        assert first.json()["accepted"] is True

        second = client.post("/webhook/tradingview", json=alert(signal_id="p2"), headers=hdr())
        assert second.json()["accepted"] is False
        assert "[MAX_POSITIONS]" in second.json()["reason"]
        assert sim_summary(client)["orders"] == 1


# ------------------------------------------------------- malformed alert input
def test_invalid_json_returns_400(client):
    r = client.post(
        "/webhook/tradingview",
        content="{not json",
        headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert "invalid JSON" in r.text


def test_non_object_json_returns_400(client):
    r = client.post("/webhook/tradingview", content="[1, 2]", headers=hdr())
    assert r.status_code == 400
    assert "JSON object" in r.text


def test_schema_violation_returns_422(client):
    r = client.post("/webhook/tradingview", json={"action": "BUY"}, headers=hdr())
    assert r.status_code == 422
    body = r.json()
    assert body["accepted"] is False
    assert "schema validation failed" in body["reason"]
    assert sim_summary(client)["orders"] == 0


def test_unknown_action_is_rejected_without_a_500(client):
    r = client.post(
        "/webhook/tradingview",
        json={"signal_id": "x", "action": "YOLO", "symbol": "MNQ1!"},
        headers=hdr(),
    )
    assert r.status_code == 422
    assert r.json()["accepted"] is False
    assert r.json()["action"] == "YOLO"
    assert sim_summary(client)["orders"] == 0


# ------------------------------------------------------------- flatten / ops
def test_flatten_submits_a_bracketless_market_order(client):
    client.post("/webhook/tradingview", json=alert(signal_id="f-buy"), headers=hdr())
    assert sim_summary(client)["positions"] != {}

    r = client.post(
        "/webhook/tradingview",
        json={"signal_id": "f-flat", "action": "FLATTEN", "symbol": "MNQ1!", "quantity": 1},
        headers=hdr(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] is True

    order = sim_orders(client)["orders"][-1]
    assert order["is_flatten"] is True
    assert order["bracket_attached"] is False
    assert order["action"] == "Sell"
    assert sim_summary(client)["positions"] == {}


def test_kill_switch_halts_the_loop_and_can_be_released(client):
    assert client.post("/admin/kill-switch", headers=hdr()).status_code == 200

    blocked = client.post("/webhook/tradingview", json=alert(signal_id="ks-1"), headers=hdr())
    assert blocked.json()["accepted"] is False
    assert "[KILL_SWITCH]" in blocked.json()["reason"]
    assert sim_summary(client)["orders"] == 0

    assert client.delete("/admin/kill-switch", headers=hdr()).status_code == 200
    after = client.post("/webhook/tradingview", json=alert(signal_id="ks-2"), headers=hdr())
    assert after.json()["accepted"] is True


def test_admin_flatten_closes_every_simulated_position(client):
    client.post("/webhook/tradingview", json=alert(signal_id="af-1"), headers=hdr())
    assert sim_summary(client)["positions"] != {}

    r = client.post("/admin/flatten", headers=hdr())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["closed"], "expected at least one closed position"
    assert sim_summary(client)["positions"] == {}


def test_sim_reset_clears_recorded_orders(client):
    client.post("/webhook/tradingview", json=alert(signal_id="r-1"), headers=hdr())
    assert sim_summary(client)["orders"] == 1

    assert client.post("/sim/reset", headers=hdr()).status_code == 200
    assert sim_summary(client)["orders"] == 0


def test_booting_without_credentials_or_dry_run_fails_loudly(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="TRADOVATE_APP_ID"):
        with boot(tmp_path, monkeypatch, DRY_RUN="false"):
            pass


# ------------------------------------------------------------------ invariant
def test_session_runs_end_to_end_and_leaves_zero_naked_positions(client):
    """Mixed signal flow; the Apex-critical invariant is asserted at the end."""
    signals = [
        alert(signal_id="s-1"),
        alert(
            signal_id="s-2",
            action="SELL",
            entry_price=21000.0,
            stop_loss=21020.0,
            take_profit=20960.0,
        ),
        {"signal_id": "s-3", "action": "FLATTEN", "symbol": "MNQ1!", "quantity": 1},
        alert(signal_id="s-4", quantity=2, stop_loss=20970.25, take_profit=21050.0),
    ]
    for payload in signals:
        r = client.post("/webhook/tradingview", json=payload, headers=hdr())
        assert r.status_code == 200, r.text
        assert r.json()["accepted"] is True, r.text

    summary = sim_summary(client)
    assert summary["orders"] == 4
    assert summary["naked_orders"] == 0
    assert summary["bracketed_entries"] == 3
    assert summary["positions"] == {resolve("MNQ1!").tradovate_symbol: 2}
