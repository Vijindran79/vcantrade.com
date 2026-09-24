"""Forced-signal end-to-end harness for a *running* middleware instance.

This is the executable form of sections 1 and 4 in TRADINGVIEW_SETUP.md. It
fires a series of TradingView-shaped alerts at a live HTTP endpoint and reports,
scenario by scenario, whether the service behaved as specified. It is
deliberately not a pytest file: it talks to a real socket, so it works the same
against DRY_RUN, a local demo run, or the VPS behind nginx.

    # DRY_RUN, no credentials, nothing leaves the box
    python scripts/e2e_signal_test.py --key $env:WEBHOOK_API_KEY

    # exactly how TradingView calls it: secret in the URL, no headers at all
    python scripts/e2e_signal_test.py --key $env:WEBHOOK_API_KEY --url-key

    # against the public endpoint
    python scripts/e2e_signal_test.py --url https://trade.example.com --key ... --url-key

Scenarios (each one is a check, not a print):

  [0] liveness, auth gating (401 with no/!wrong key), mode + risk snapshot
  [1] unsafe or malformed alerts are rejected: missing stop, stop on the wrong
      side, oversized quantity, unknown symbol, bad types, non-JSON body
  [2] idempotency: a byte-identical re-fire is filtered as DUPLICATE
  [3] forced BUY -> bracketed order, bracket proven from the broker payload
  [4] forced SELL -> bracketed order, stop on the correct side
  [5] closing snapshot: flat, zero naked entries

Entries are FLATTENed as it goes, so the run always ends flat and the
position-count risk cap never masks the rule a later scenario is testing.

Exit code 0 means every scenario matched; 1 means at least one deviation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Optional

import httpx

WEBHOOK = "/webhook/tradingview"


class Report:
    """Collects PASS/FAIL rows so the run ends with a verdict + exit code."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        ok = bool(ok)
        self.rows.append((name, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<44} {detail}")
        return ok

    @property
    def failures(self) -> list[tuple[str, bool, str]]:
        return [row for row in self.rows if not row[1]]


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fire forced TradingView alerts at the middleware",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--url", default=os.environ.get("MIDDLEWARE_URL", "http://127.0.0.1:8080"))
    p.add_argument("--key", default=os.environ.get("WEBHOOK_API_KEY", ""),
                   help="shared secret (or set WEBHOOK_API_KEY)")
    p.add_argument("--url-key", action="store_true",
                   help="carry the secret in the URL path, the way TradingView must")
    p.add_argument("--symbol", default="MNQ1!", help="TradingView symbol to trade")
    p.add_argument("--entry", type=float, default=21000.0, help="reference entry price")
    p.add_argument("--stop-points", type=float, default=20.0)
    p.add_argument("--tp-points", type=float, default=40.0)
    p.add_argument("--quantity", type=int, default=1)
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument("--json", action="store_true", help="dump raw bodies for failed checks")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    base = args.url.rstrip("/")
    if not args.key:
        print("error: --key or WEBHOOK_API_KEY is required", file=sys.stderr)
        return 2

    run_id = time.strftime("%Y%m%d-%H%M%S")
    rep = Report()
    admin = {"X-API-Key": args.key}
    headers = dict(admin) if not args.url_key else {}
    headers["Content-Type"] = "application/json"

    def webhook_url() -> str:
        return f"{base}{WEBHOOK}/{args.key}" if args.url_key else f"{base}{WEBHOOK}"

    def signal(action: str, sid: str, **over: Any) -> dict:
        body: dict[str, Any] = {
            "signal_id": sid,
            "action": action,
            "symbol": args.symbol,
            "quantity": args.quantity,
            "entry_price": args.entry,
            "timestamp_ms": int(time.time() * 1000),
        }
        if action == "BUY":
            body["stop_loss"] = round(args.entry - args.stop_points, 2)
            body["take_profit"] = round(args.entry + args.tp_points, 2)
        elif action == "SELL":
            body["stop_loss"] = round(args.entry + args.stop_points, 2)
            body["take_profit"] = round(args.entry - args.tp_points, 2)
        body.update(over)
        return body

    def flatten_body(sid: str) -> dict:
        return {
            "signal_id": sid,
            "action": "FLATTEN",
            "symbol": args.symbol,
            "quantity": args.quantity,
        }

    dry = False
    with httpx.Client(timeout=args.timeout) as c:
        print(f"target : {base}")
        print(f"key via: {'URL path (TradingView style)' if args.url_key else 'X-API-Key header'}")
        print(f"symbol : {args.symbol}  qty={args.quantity}  entry={args.entry}")

        def fire(payload: Any) -> dict:
            """POST one alert; always returns a dict, never raises.

            The response status is echoed under ``_status`` so checks can assert
            on it even though the middleware answers most rejections with 200.
            """
            try:
                r = c.post(webhook_url(), json=payload, headers=headers)
            except httpx.HTTPError as exc:
                return {"_status": 0, "reason": f"transport error: {exc}"}
            try:
                body = r.json()
            except ValueError:
                body = {"reason": f"non-JSON body: {r.text[:60]}"}
            if not isinstance(body, dict):
                body = {"reason": f"non-object body: {str(body)[:60]}"}
            body["_status"] = r.status_code
            return body

        def rejected_as(resp: dict, rule: str) -> bool:
            return resp.get("accepted") is False and rule in str(resp.get("reason"))

        # ------------------------------------------------------------ [0] gate
        print("\n[0] liveness, auth gating, mode")
        try:
            r = c.get(f"{base}/health")
        except httpx.HTTPError as exc:
            print(f"\nFATAL: cannot reach {base} — is the service running? ({exc})")
            return 1
        rep.check(
            "GET /health -> ok",
            r.status_code == 200 and r.json().get("status") == "ok",
            f"HTTP {r.status_code} {r.text[:50]}",
        )

        r = c.post(f"{base}{WEBHOOK}", json=signal("BUY", f"{run_id}-noauth"))
        rep.check("no credentials -> 401", r.status_code == 401, f"HTTP {r.status_code}")

        r = c.post(
            f"{base}{WEBHOOK}",
            json=signal("BUY", f"{run_id}-badkey"),
            headers={"X-API-Key": "definitely-not-the-key"},
        )
        rep.check("wrong key -> 401", r.status_code == 401, f"HTTP {r.status_code}")

        st = c.get(f"{base}/status", headers=admin)
        if st.status_code != 200:
            print(f"\nFATAL: /status returned HTTP {st.status_code} — check --key\n{st.text[:300]}")
            return 1
        status = st.json()
        dry = bool(status.get("dry_run"))
        mode = "DRY_RUN (simulated broker)" if dry else f"BROKER (env={status.get('tradovate_env')})"
        print(f"mode   : {mode}")
        print(f"account: id={status.get('account_id')} spec={status.get('account_spec')} "
              f"risk={json.dumps(status.get('risk', {}))}")

        if dry:
            c.post(f"{base}/sim/reset", headers=admin)
            print("         simulated order log cleared for a clean run")
        else:
            r = c.get(f"{base}/sim/orders", headers=admin)
            rep.check("/sim/orders -> 409 outside DRY_RUN", r.status_code == 409, f"HTTP {r.status_code}")

        def sim_orders() -> list[dict]:
            rr = c.get(f"{base}/sim/orders", headers=admin, params={"limit": 50})
            return rr.json().get("orders", []) if rr.status_code == 200 else []

        def last_sim() -> dict:
            orders = sim_orders()
            return orders[-1] if orders else {}

        # ------------------------------------------------- [1] unsafe = rejected
        print("\n[1] unsafe alerts must be rejected, not forwarded")
        bad = signal("BUY", f"{run_id}-nostop")
        bad.pop("stop_loss")
        bad.pop("take_profit")
        resp = fire(bad)
        rep.check("no stop_loss -> ORDER_BUILD", rejected_as(resp, "ORDER_BUILD"),
                  str(resp.get("reason"))[:90])

        resp = fire(signal("BUY", f"{run_id}-badstop", stop_loss=args.entry + 10))
        rep.check("stop above a BUY entry -> ORDER_BUILD", rejected_as(resp, "ORDER_BUILD"),
                  str(resp.get("reason"))[:90])

        resp = fire(signal("BUY", f"{run_id}-big", quantity=99))
        rep.check("quantity 99 -> MAX_CONTRACTS", rejected_as(resp, "MAX_CONTRACTS"),
                  str(resp.get("reason"))[:90])

        resp = fire(signal("BUY", f"{run_id}-sym", symbol="ZZZ1!"))
        rep.check("unknown symbol -> SYMBOL", rejected_as(resp, "SYMBOL"),
                  str(resp.get("reason"))[:90])

        resp = fire({"signal_id": f"{run_id}-junk", "action": "BUY", "symbol": args.symbol,
                     "stop_loss": "not-a-number"})
        rep.check("garbage stop_loss -> HTTP 422, not 500",
                  resp.get("_status") == 422 and resp.get("accepted") is False,
                  f"HTTP {resp.get('_status')} {str(resp.get('reason'))[:70]}")

        resp = fire(["not", "an", "object"])
        rep.check("array body -> HTTP 400", resp.get("_status") == 400,
                  f"HTTP {resp.get('_status')} {str(resp.get('reason'))[:70]}")

        # ---------------------------------------------------- [2] idempotency
        print("\n[2] a re-fired alert must be filtered (idempotency)")
        dup = signal("BUY", f"{run_id}-dup")
        first = fire(dup)
        rep.check("first fire accepted", first.get("accepted") is True,
                  f"order_id={first.get('order_id')}")
        second = fire(dup)
        rep.check("byte-identical re-fire -> DUPLICATE", rejected_as(second, "DUPLICATE"),
                  str(second.get("reason"))[:90])
        resp = fire(flatten_body(f"{run_id}-dup-close"))
        rep.check("FLATTEN closes it again", resp.get("accepted") is True, f"order_id={resp.get('order_id')}")

        if status.get("risk", {}).get("open_positions"):
            print(f"WARNING: risk state already shows {status['risk']['open_positions']} open position(s).")
            print("         Entry scenarios may then hit the MAX_POSITIONS rule instead of the")
            print("         rule under test. Clear state/risk_state.json for a clean run.")

        # ------------------------------------------------- [3] BUY, bracketed
        print("\n[3] forced BUY alert -> bracketed order")
        payload = signal("BUY", f"{run_id}-buy")
        print(f"    payload: {json.dumps(payload)}")
        resp = fire(payload)
        rep.check(
            "BUY accepted with order_id",
            resp.get("accepted") is True and bool(resp.get("order_id")),
            f"HTTP {resp.get('_status')} order_id={resp.get('order_id')} "
            f"symbol={resp.get('symbol')} broker_status={resp.get('broker_status')} "
            f"latency={resp.get('latency_ms')}ms",
        )
        if resp.get("accepted") is not True and args.json:
            print(json.dumps(resp, indent=2))

        if dry:
            o = last_sim()
            rep.check("bracket.stopLoss reached the broker", o.get("bracket_attached") is True,
                      f"stop={o.get('stop_price')}")
            rep.check("bracket.takeProfit reached the broker", o.get("take_profit_price") is not None,
                      f"tp={o.get('take_profit_price')}")
            rep.check("submitted as Market + isAutomated",
                      o.get("order_type") == "Market" and o.get("is_automated") is True,
                      f"type={o.get('order_type')} automated={o.get('is_automated')}")
            want = round(args.entry - args.stop_points, 2)
            rep.check("stop sits below the BUY entry",
                      abs(float(o.get("stop_price") or 0) - want) <= 0.5,
                      f"stop={o.get('stop_price')} expected~{want}")
        else:
            print("    NOTE: open Tradovate now — the position must already show a working stop + limit.")

        resp = fire(flatten_body(f"{run_id}-buy-close"))
        rep.check("FLATTEN accepted after the BUY", resp.get("accepted") is True,
                  f"order_id={resp.get('order_id')}")
        if dry:
            rep.check("a FLATTEN carries no bracket", last_sim().get("is_flatten") is True, "")

        # ------------------------------------------------ [4] SELL, bracketed
        print("\n[4] forced SELL alert -> bracketed order")
        resp = fire(signal("SELL", f"{run_id}-sell"))
        rep.check("SELL accepted", resp.get("accepted") is True,
                  f"HTTP {resp.get('_status')} order_id={resp.get('order_id')} "
                  f"symbol={resp.get('symbol')} latency={resp.get('latency_ms')}ms")
        if resp.get("accepted") is not True and args.json:
            print(json.dumps(resp, indent=2))

        if dry:
            o = last_sim()
            rep.check("recorded as Side=Sell with a bracket",
                      o.get("action") == "Sell" and o.get("bracket_attached") is True,
                      f"action={o.get('action')} stop={o.get('stop_price')} tp={o.get('take_profit_price')}")
            want = round(args.entry + args.stop_points, 2)
            rep.check("stop sits above the SELL entry",
                      abs(float(o.get("stop_price") or 0) - want) <= 0.5,
                      f"stop={o.get('stop_price')} expected~{want}")
        else:
            print("    NOTE: the new short must show a stop above its entry in Tradovate.")

        resp = fire(flatten_body(f"{run_id}-sell-close"))
        rep.check("FLATTEN accepted after the SELL", resp.get("accepted") is True, "")

        # ------------------------------------------------------- [5] snapshot
        print("\n[5] closing snapshot")
        st = c.get(f"{base}/status", headers=admin)
        snapshot = st.json() if st.status_code == 200 else {}
        risk = snapshot.get("risk", {})
        rep.check("risk snapshot readable", "risk" in snapshot,
                  f"day={risk.get('day')} equity={risk.get('equity_usd')} "
                  f"open={risk.get('open_positions')} realized_today={risk.get('realized_pnl_today_usd')} "
                  f"trailing_dd={risk.get('trailing_drawdown_usd')}")
        rep.check("nothing left open on the book", risk.get("open_positions") == 0,
                  f"open={risk.get('open_positions')}")
        rep.check("kill switch not engaged", risk.get("kill_switch") is False,
                  f"kill_switch={risk.get('kill_switch')}")
        if dry:
            sim = snapshot.get("sim", {})
            rep.check("zero naked entries for the entire run", sim.get("naked_orders") == 0,
                      f"naked={sim.get('naked_orders')} bracketed={sim.get('bracketed_entries')} "
                      f"orders={sim.get('orders')}")
            rep.check("simulated positions are flat", not sim.get("positions"),
                      f"{sim.get('positions')}")
        else:
            print("    NOTE: confirm in Tradovate that nothing is open without a stop.")

    print()
    if rep.failures:
        print(f"RESULT: FAIL — {len(rep.failures)}/{len(rep.rows)} checks failed")
        for name, _, detail in rep.failures:
            print(f"  - {name}  {detail}")
        print("\nDo not point a live TradingView alert at this service until every check passes.")
        return 1

    print(f"RESULT: PASS — all {len(rep.rows)} checks passed (run id {run_id})")
    if dry:
        print("This was DRY_RUN: the whole path ran, but zero bytes went to Tradovate.")
        print("Next: set TRADOVATE_ENV=demo + real credentials + DRY_RUN=false and re-run.")
    else:
        print("Broker path verified — cross-check the fills in the Tradovate order list.")
    print("Alert wiring: see TRADINGVIEW_SETUP.md section 1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
