"""FastAPI application — TradingView webhook -> Tradovate REST.

Endpoints:
  POST {WEBHOOK_PATH}                receive a TradingView alert, place a bracket order
  POST {WEBHOOK_PATH}/{key}          same, with the shared secret in the URL
                                     (TradingView cannot send custom headers)
  POST /admin/flatten                flatten all open positions (auth required)
  POST /admin/kill-switch            engage the kill switch (creates the file)
  DELETE /admin/kill-switch          disengage
  GET  /health                       liveness
  GET  /status                       risk snapshot + token state
  GET  /sim/orders                   simulated orders (DRY_RUN only)
  POST /sim/reset                    clear simulated orders (DRY_RUN only)

DRY_RUN=true swaps the Tradovate client for an in-process simulator so the whole
webhook -> risk -> bracket -> submit path can be verified with no credentials.

The webhook handler is intentionally synchronous end-to-end: parse -> risk ->
build bracket -> POST /order -> respond. No queues, no background workers, no
LLM. Target latency budget: <100 ms excluding broker round-trip.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .auth import TokenManager, TradovateAuthError
from .client import TradovateAPIError, TradovateClient
from .config import Settings, get_settings
from .logging_setup import get_logger, setup_logging
from .models import OrderResult, TradingViewSignal
from .orders import OrderConstructionError, build_entry, build_flatten
from .risk import RiskGuard
from .sim_broker import SimBroker
from .symbols import SymbolResolutionError, resolve

log = get_logger(__name__)


class AppState:
    settings: Settings
    http: httpx.AsyncClient
    tokens: TokenManager
    client: TradovateClient
    risk: RiskGuard


state = AppState()


def _starting_equity(settings: Settings) -> float:
    import os
    return float(os.environ.get("STARTING_EQUITY_USD", "50000"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    state.settings = get_settings()
    s = state.settings

    if not s.webhook_api_key:
        raise RuntimeError("WEBHOOK_API_KEY must be set to a long random string.")
    if s.tradovate_env.lower() not in ("demo", "live"):
        raise RuntimeError("TRADOVATE_ENV must be 'demo' or 'live'")

    state.http = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        timeout=httpx.Timeout(10.0),
    )

    if s.dry_run:
        # The whole webhook -> risk -> bracket -> submit path still runs; only the
        # last hop is swapped for the in-process simulator. Used to verify the
        # plumbing (and TradingView alerts) before demo credentials exist.
        log.warning(
            "DRY_RUN ACTIVE (env=%s) — no credentials required and NO order will "
            "reach Tradovate. Simulated orders -> %s",
            s.tradovate_env,
            Path(s.state_file).parent / "sim_orders.json",
        )
        state.tokens = None  # type: ignore[assignment]
        state.client = SimBroker(s)  # type: ignore[assignment]
        if s.tradovate_account_id is None:
            s.tradovate_account_id = 1
    else:
        if not s.tradovate_app_id or not s.tradovate_cid or not s.tradovate_sec:
            raise RuntimeError(
                "TRADOVATE_APP_ID / TRADOVATE_CID / TRADOVATE_SEC must be set "
                "(or set DRY_RUN=true to verify the plumbing without credentials). "
                "Copy .env.example to .env and fill in your developer credentials."
            )
        state.tokens = TokenManager(s, state.http)
        state.client = TradovateClient(s, state.tokens, state.http)

        try:
            await state.tokens.start()
        except TradovateAuthError as exc:
            await state.http.aclose()
            raise RuntimeError(f"Tradovate auth failed at startup: {exc}") from exc

        if s.tradovate_account_id is None:
            accounts = await state.client.list_accounts()
            if not accounts:
                raise RuntimeError("no Tradovate accounts visible to these credentials")
            s.tradovate_account_id = int(accounts[0]["id"])
            log.info("auto-selected accountId=%d (%s)", s.tradovate_account_id, accounts[0].get("name"))

    state.risk = RiskGuard(s, starting_equity_usd=_starting_equity(s))

    log.info(
        "middleware ready env=%s account=%d spec=%s webhook=%s dry_run=%s",
        s.tradovate_env, s.tradovate_account_id, s.tradovate_account_spec,
        s.webhook_path, s.dry_run,
    )
    if s.tradovate_env.lower() == "live" and not s.dry_run:
        log.warning("LIVE TRADOVATE ENDPOINT ARMED — real money at risk")
    if s.allow_url_key:
        log.warning(
            "URL keys enabled: %s/<key> and ?key=<key> are accepted because "
            "TradingView alerts cannot send custom headers. Terminate TLS in front "
            "of this service, consider TRADINGVIEW_IP_ALLOWLIST, and set "
            "ALLOW_URL_KEY=false once a reverse proxy injects X-API-Key.",
            s.webhook_path,
        )

    try:
        yield
    finally:
        if state.tokens is not None:
            await state.tokens.stop()
        await state.http.aclose()
        log.info("middleware shut down")


app = FastAPI(title="vcantrade Tradovate middleware", lifespan=lifespan)


def _client_ip(request: Optional[Request]) -> str:
    if request is None:
        return ""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _enforce_ip_allowlist(request: Optional[Request]) -> None:
    """Applied only to URL-key auth: a URL can leak, an injected header cannot."""
    allow = state.settings.ip_allowlist
    if not allow:
        return
    ip = _client_ip(request)
    if ip not in allow:
        log.warning("URL-key webhook rejected from non-allowlisted IP %s", ip)
        raise HTTPException(
            status_code=403, detail=f"source IP {ip or 'unknown'} is not allowlisted"
        )


def _check_webhook_auth(
    x_api_key: Optional[str] = None,
    authorization: Optional[str] = None,
    *,
    request: Optional[Request] = None,
    query_key: Optional[str] = None,
    path_key: Optional[str] = None,
) -> None:
    """Validate the shared secret.

    Three carriers are accepted, in priority order: the ``X-API-Key`` header, an
    ``Authorization: Bearer`` header, and — only while ``ALLOW_URL_KEY=true`` — a
    key embedded in the URL path or query string. The URL carrier exists solely
    because TradingView's alert dialog cannot send custom headers.
    """
    provided = ""
    from_url = False
    if x_api_key:
        provided = x_api_key.strip()
    elif authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    elif path_key and state.settings.allow_url_key:
        provided, from_url = path_key.strip(), True
    elif query_key and state.settings.allow_url_key:
        provided, from_url = query_key.strip(), True

    if not provided:
        raise HTTPException(status_code=401, detail="missing api key")

    import hmac

    if not hmac.compare_digest(provided, state.settings.webhook_api_key):
        raise HTTPException(status_code=401, detail="invalid api key")

    if from_url:
        _enforce_ip_allowlist(request)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "env": state.settings.tradovate_env}


@app.get("/status")
async def status(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
) -> dict:
    _check_webhook_auth(x_api_key, authorization)
    body = {
        "risk": state.risk.snapshot(),
        "tradovate_env": state.settings.tradovate_env,
        "account_id": state.settings.tradovate_account_id,
        "account_spec": state.settings.tradovate_account_spec,
        "dry_run": state.settings.dry_run,
        "url_keys_allowed": state.settings.allow_url_key,
    }
    sim = getattr(state.client, "snapshot", None)
    if callable(sim):
        body["sim"] = sim()
    return body


@app.post("/admin/kill-switch")
async def engage_kill_switch(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
) -> dict:
    _check_webhook_auth(x_api_key, authorization)
    from pathlib import Path
    p = Path(state.settings.kill_switch_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(int(time.time())))
    log.warning("KILL SWITCH ENGAGED via admin endpoint")
    return {"status": "engaged", "path": str(p)}


@app.delete("/admin/kill-switch")
async def disengage_kill_switch(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
) -> dict:
    _check_webhook_auth(x_api_key, authorization)
    from pathlib import Path
    p = Path(state.settings.kill_switch_file)
    if p.exists():
        p.unlink()
    log.warning("KILL SWITCH DISENGAGED via admin endpoint")
    return {"status": "disengaged"}


@app.post("/admin/flatten")
async def flatten_all(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
) -> dict:
    _check_webhook_auth(x_api_key, authorization)
    acct = state.settings.tradovate_account_id or 0
    positions = await state.client.list_positions(acct)
    closed = []
    for pos in positions:
        qty = int(pos.get("pos") or pos.get("netPos") or 0)
        if qty == 0:
            continue
        symbol = pos.get("symbol") or pos.get("contractId")
        side = "Sell" if qty > 0 else "Buy"
        try:
            r = await state.client.close_position(acct, str(symbol), side, abs(qty))
            closed.append({"symbol": symbol, "qty": qty, "result": r})
            state.risk.on_position_closed(0.0)
        except TradovateAPIError as exc:
            closed.append({"symbol": symbol, "qty": qty, "error": str(exc)})
    log.warning("flatten_all closed %d positions", len(closed))
    return {"status": "ok", "closed": closed}


@app.get("/sim/orders")
async def sim_orders(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
    limit: int = 20,
) -> dict:
    """Recorded simulated orders (with bracket legs) + counters. DRY_RUN only."""
    _check_webhook_auth(x_api_key, authorization)
    snap = getattr(state.client, "snapshot", None)
    listing = getattr(state.client, "orders", None)
    if not state.settings.dry_run or not callable(snap) or not callable(listing):
        raise HTTPException(status_code=409, detail="service is not in DRY_RUN mode")
    n = max(1, min(int(limit), 500))
    return {"summary": snap(), "orders": listing()[-n:]}


@app.post("/sim/reset")
async def sim_reset(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
) -> dict:
    """Clear simulated orders so a test run starts from a clean slate."""
    _check_webhook_auth(x_api_key, authorization)
    reset = getattr(state.client, "reset", None)
    if not state.settings.dry_run or not callable(reset):
        raise HTTPException(status_code=409, detail="service is not in DRY_RUN mode")
    reset()
    return {"status": "reset"}


async def _read_signal(
    request: Request,
) -> tuple[Optional[TradingViewSignal], Optional[JSONResponse]]:
    """Parse + validate the alert body, or hand back the rejection response."""
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON: {exc}")

    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400,
            detail="alert body must be a JSON object matching the signal schema",
        )

    log.info("webhook received: %s", body)

    try:
        return TradingViewSignal.model_validate(body), None
    except Exception as exc:
        # Build the rejection defensively. A malformed `action` (or a bad
        # quantity) must produce a 422, never a 500, or the alert loop dies on
        # input that TradingView happily sends.
        raw_action = str(body.get("action") or "BUY").strip().upper()
        rejection = OrderResult(
            accepted=False,
            signal_id=str(body.get("signal_id") or ""),
            symbol=str(body.get("symbol") or ""),
            action=raw_action if raw_action in ("BUY", "SELL", "FLATTEN") else "BUY",
            reason=f"schema validation failed: {exc}",
        ).model_dump()
        rejection["action"] = raw_action or "BUY"  # echo the raw value for debugging
        return None, JSONResponse(status_code=422, content=rejection)


async def _handle_alert(
    request: Request,
    *,
    x_api_key: Optional[str],
    authorization: Optional[str],
    path_key: Optional[str] = None,
    query_key: Optional[str] = None,
) -> JSONResponse:
    t_start = time.perf_counter()
    _check_webhook_auth(
        x_api_key,
        authorization,
        request=request,
        path_key=path_key,
        query_key=query_key,
    )
    sig, rejection = await _read_signal(request)
    if rejection is not None:
        return rejection
    assert sig is not None
    return await _execute_signal(sig, t_start)


@app.post("/webhook/tradingview")
async def tradingview_webhook(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
    key: Optional[str] = None,
) -> JSONResponse:
    """Primary alert endpoint. Auth via X-API-Key, Bearer, or `?key=`."""
    return await _handle_alert(
        request,
        x_api_key=x_api_key,
        authorization=authorization,
        query_key=key,
    )


@app.post("/webhook/tradingview/{token}")
async def tradingview_webhook_urlkey(
    request: Request,
    token: str,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    """TradingView-friendly variant: the shared secret travels in the URL path.

    The TradingView alert dialog cannot set custom headers, so this route lets a
    bare webhook URL work with no reverse proxy in front. Requires
    ALLOW_URL_KEY=true (the default) and pairs with TRADINGVIEW_IP_ALLOWLIST.
    """
    return await _handle_alert(
        request,
        x_api_key=x_api_key,
        authorization=authorization,
        path_key=token,
    )


async def _execute_signal(sig: TradingViewSignal, t_start: float) -> JSONResponse:
    """Risk-check, bracket-construct and submit one signal; always returns JSON."""
    try:
        spec = resolve(sig.symbol)
    except SymbolResolutionError as exc:
        return _reject(sig, str(exc), "SYMBOL", t_start)

    qty = sig.quantity if sig.quantity is not None else 1

    decision = state.risk.check_order(
        signal_id=sig.signal_id,
        quantity=qty,
        symbol=spec.tradovate_symbol,
        action=sig.action,
    )
    if not decision.allowed:
        log.warning(
            "RISK BLOCK signal=%s rule=%s reason=%s",
            sig.signal_id, decision.rule, decision.reason,
        )
        return _reject(sig, decision.reason, decision.rule, t_start)

    try:
        if sig.action == "FLATTEN":
            payload = build_flatten(
                settings=state.settings, spec=spec, side="Sell", quantity=qty
            )
            bracket_stop = 0.0
            bracket_tp = None
        else:
            bo = build_entry(
                settings=state.settings,
                spec=spec,
                action=sig.action,
                quantity=qty,
                entry_price=sig.entry_price,
                stop_loss=sig.stop_loss,
                take_profit=sig.take_profit,
            )
            payload = bo.payload
            bracket_stop = bo.stop_price
            bracket_tp = bo.take_profit_price
    except OrderConstructionError as exc:
        return _reject(sig, str(exc), "ORDER_BUILD", t_start)

    try:
        t_order = time.perf_counter()
        resp = await state.client.place_order(payload)
        broker_ms = (time.perf_counter() - t_order) * 1000.0
    except TradovateAPIError as exc:
        log.error("broker rejected order signal=%s: %s", sig.signal_id, exc)
        return _reject(sig, f"broker error: {exc}", "BROKER", t_start, status=502)

    order_id = None
    broker_status = None
    if isinstance(resp, dict):
        order_id = str(resp.get("orderId") or resp.get("order_id") or "")
        broker_status = str(resp.get("orderStatus") or resp.get("status") or "")
        if resp.get("failureText") or resp.get("error"):
            reason = resp.get("failureText") or resp.get("error")
            return _reject(sig, f"broker failure: {reason}", "BROKER_REJECT", t_start, status=502)

    if sig.action != "FLATTEN":
        state.risk.on_position_opened()
    else:
        state.risk.on_position_closed(0.0)

    total_ms = (time.perf_counter() - t_start) * 1000.0
    log.info(
        "ORDER PLACED signal=%s %s %s qty=%d stop=%s tp=%s order_id=%s broker_ms=%.1f total_ms=%.1f",
        sig.signal_id, sig.action, spec.tradovate_symbol, qty,
        bracket_stop, bracket_tp, order_id, broker_ms, total_ms,
    )

    return JSONResponse(
        status_code=200,
        content=OrderResult(
            accepted=True,
            signal_id=sig.signal_id,
            symbol=spec.tradovate_symbol,
            action=sig.action,
            quantity=qty,
            order_id=order_id,
            broker_status=broker_status,
            latency_ms=round(total_ms, 2),
        ).model_dump(),
    )


def _reject(
    sig: TradingViewSignal,
    reason: str,
    rule: str,
    t_start: float,
    status: int = 200,
) -> JSONResponse:
    total_ms = (time.perf_counter() - t_start) * 1000.0
    return JSONResponse(
        status_code=status,
        content=OrderResult(
            accepted=False,
            signal_id=sig.signal_id,
            symbol=sig.symbol,
            action=sig.action,
            quantity=sig.quantity or 0,
            reason=f"[{rule}] {reason}",
            latency_ms=round(total_ms, 2),
        ).model_dump(),
    )


def run() -> None:
    import uvicorn
    setup_logging()
    s = get_settings()
    uvicorn.run(
        "app.main:app",
        host=s.host,
        port=s.port,
        log_level=s.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    run()
