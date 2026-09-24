# Sim / Demo Testing Plan

**Goal:** prove the middleware executes TradingView signals correctly on a
Tradovate **demo** account, with every Apex risk guard firing as designed,
before a single dollar of funded capital is at risk.

**Minimum duration:** 5 consecutive trading days, zero unexplained rejections,
zero naked positions (every fill must have a broker-attached stop).

---

## Phase 0 — Prerequisites (30 min)

- [ ] Tradovate demo credentials issued (`TRADOVATE_APP_ID`, `CID`, `SEC`).
- [ ] Apex sim account visible under those credentials (`TRADOVATE_ACCOUNT_SPEC=Trials`).
- [ ] VPS or local machine reachable from TradingView (public HTTPS for cloud alerts).
- [ ] `cp .env.example .env`, set `TRADOVATE_ENV=demo`, generate `WEBHOOK_API_KEY`
      with `openssl rand -hex 32`.
- [ ] `pip install -r requirements.txt && pytest -q` — all tests green.
- [ ] `python -m app.main` boots, logs `middleware ready env=demo`.
- [ ] **DRY_RUN pass first:** with `DRY_RUN=true` (no credentials needed), run
      `powershell -File scripts\run_dry_run_e2e.ps1` on Windows or
      `python scripts/e2e_signal_test.py --key $WEBHOOK_API_KEY --url-key`
      against a running instance — 28/28 checks, exit code 0.

**Pass criteria:** `/health` returns `{"status":"ok","env":"demo"}` and the
harness exits 0 before any broker credential is introduced.

---

## Phase 1 — Auth & account resolution (15 min)

- [ ] Confirm the token manager acquired a token: log line
      `tradovate token acquired env=demo expires_in_sec=...`.
- [ ] `GET /status` (with `X-API-Key`) returns the correct `account_id` and
      `account_spec`.
- [ ] Wait 50+ minutes (or temporarily set `TOKEN_REFRESH_MARGIN_SEC` high) and
      confirm the background refresh fires without an order failing.

**Pass criteria:** no `401` in logs; `/status` stays responsive across the
refresh boundary.

---

## Phase 2 — Symbol resolution (15 min)

Send one webhook per instrument and confirm the Tradovate demo UI shows the
correct contract:

| TradingView symbol | Expected Tradovate contract (front month) |
|---|---|
| `NQ1!` | `NQ` + current quarter code |
| `MNQ1!` | `MNQ` + current quarter code |
| `ES1!` | `ES` + current quarter code |
| `GC1!` | `GC` + front month |
| `CL1!` | `CL` + front month |

```bash
curl -X POST $URL/webhook/tradingview -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d '{
  "signal_id":"sym-nq-1","action":"BUY","symbol":"NQ1!","quantity":1,
  "entry_price":21000,"stop_loss":20980,"take_profit":21040}'
```

**Pass criteria:** order appears in Tradovate demo against the right contract;
middleware log shows `ORDER PLACED ... symbol=NQZ6` (or current front month).

**Fail action:** if the contract is wrong, pin it explicitly in
`app/symbols.py::SYMBOL_OVERRIDES` rather than trusting the roll heuristic.

---

## Phase 3 — Bracket atomicity (THE critical test, 30 min)

This is the single most important verification. A position must **never**
exist without a broker-attached stop.

- [ ] Place a BUY with stop + TP. In the Tradovate demo UI, open the position
      and confirm **both** a stop-loss and a take-profit order are already
      working, attached to the entry, before any Python code runs.
- [ ] Kill the middleware process (`Ctrl-C` / `docker stop`) immediately after
      the fill. Confirm the stop and TP **remain live at the broker**. They
      must not depend on the middleware being up.
- [ ] Restart the middleware. Confirm `/status` reflects the open position
      count from persisted state.
- [ ] Place a SELL with stop only (no TP). Confirm the stop is attached.
- [ ] Send a signal with `stop_loss` omitted while `REQUIRE_EXPLICIT_STOP=true`.
      Confirm rejection with rule `ORDER_BUILD`, **no order reaches the broker**.

**Pass criteria:** every filled position has a working stop at the broker,
verified in the Tradovate UI, surviving a middleware restart.

---

## Phase 4 — Risk guards (45 min)

Fire each scenario and confirm the rejection rule in the response `reason`
field and in the logs.

| # | Scenario | Expected rule |
|---|---|---|
| 1 | Same `signal_id` twice within 5 s | `DUPLICATE` |
| 2 | Same `signal_id` after 6 s | allowed |
| 3 | `quantity` = `MAX_CONTRACTS_PER_ORDER + 1` | `MAX_CONTRACTS` |
| 4 | Open positions = cap, send another entry | `MAX_POSITIONS` |
| 5 | `action=FLATTEN` while at position cap | allowed (flatten bypasses cap) |
| 6 | Realized loss > `MAX_DAILY_LOSS_USD` (simulate via `/admin` or manual state edit) | `DAILY_LOSS` |
| 7 | Equity drops so HWM − equity ≥ `TRAILING_DRAWDOWN_USD` | `TRAILING_DD` |
| 8 | `touch state/KILL_SWITCH`, send any entry | `KILL_SWITCH` |
| 9 | Wrong / missing `X-API-Key` | HTTP 401 |
| 10 | Malformed JSON body | HTTP 400 |
| 11 | Unknown symbol `FOO1!` | `SYMBOL` |

```bash
# duplicate test
for i in 1 2; do curl -s -X POST $URL/webhook/tradingview -H "X-API-Key: $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"signal_id":"dup-1","action":"BUY","symbol":"MNQ1!","quantity":1,"entry_price":21000,"stop_loss":20990}'; echo; done
# first -> accepted, second -> [DUPLICATE]
```

**Pass criteria:** all 11 scenarios behave exactly as specified. Any deviation
is a release blocker.

---

## Phase 5 — Latency (30 min)

- [ ] Send 50 sequential webhooks (script below) and collect the `latency_ms`
      field from each response.
- [ ] Middleware-side latency (parse + risk + bracket build) must be **< 5 ms**
      at p99. The `latency_ms` reported includes the broker round-trip.
- [ ] Total signal→broker-ack must be **< 150 ms** at p95 from a us-east VPS to
      Tradovate's us-east endpoint. If you see > 300 ms, the VPS region is
      wrong — move closer to the broker.

```bash
for i in $(seq 1 50); do
  /usr/bin/time -f "%e" curl -s -X POST $URL/webhook/tradingview \
    -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
    -d "{\"signal_id\":\"lat-$i\",\"action\":\"BUY\",\"symbol\":\"MNQ1!\",\"quantity\":1,\"entry_price\":21000,\"stop_loss\":20990}" \
    -o /dev/null
done
```

Note: use MNQ (micro) for latency tests to keep demo margin small, and flatten
between batches via `/admin/flatten`.

**Pass criteria:** p99 middleware-internal < 5 ms; p99 end-to-end < 150 ms.

---

## Phase 6 — TradingView end-to-end (1 day)

- [ ] Configure a real TradingView alert (see `TRADINGVIEW_SETUP.md` section 1)
      on `tradingview/vcantrade_webhook_alerts.pine`, posting to
      `https://<host>/webhook/tradingview/<WEBHOOK_API_KEY>`. The key rides in
      the URL because the alert dialog cannot set headers; pin the observed
      egress IP in `TRADINGVIEW_IP_ALLOWLIST` once a delivery has landed.
- [ ] Let it run for a full session on the demo account.
- [ ] Confirm every alert produces exactly one order, with a bracket, and that
      duplicate alerts (TradingView occasionally re-fires) are filtered.
- [ ] Confirm the `signal_id` strategy (`syminfo.ticker + bar_index + time`)
      is stable and unique per intended trade.

**Pass criteria:** 1:1 alert-to-order mapping over a full session, zero
duplicates, zero naked positions.

---

## Phase 7 — Failure injection (half day)

- [ ] Kill the network mid-order (block egress to `demo.tradovateapi.com`).
      Confirm the middleware returns a `BROKER` rejection and does **not**
      retry blindly into a double position.
- [ ] Send an order with a stop price on the wrong side of entry. Confirm
      `ORDER_BUILD` rejection.
- [ ] Corrupt `state/risk_state.json`. Confirm the middleware logs a warning
      and starts fresh rather than crashing.
- [ ] Restart the container mid-session. Confirm daily counters and open
      position count are restored from disk.

**Pass criteria:** no crash, no double-order, no naked position in any
failure mode.

---

## Sign-off checklist

Do not proceed to live until every box is checked and countersigned:

- [ ] Phases 0–7 complete on Tradovate **demo**.
- [ ] `scripts/e2e_signal_test.py` exit code 0 recorded against the demo service
      (28/28 checks, `naked_orders == 0`).
- [ ] ≥ 5 consecutive trading days of clean Phase-6 operation.
- [ ] Zero naked positions observed at any point.
- [ ] All 11 risk-guard scenarios verified.
- [ ] p99 end-to-end latency < 150 ms documented.
- [ ] Kill switch tested via the admin endpoint **and** via `touch`.
- [ ] `/admin/flatten` tested and confirmed to close every position.
- [ ] Logs reviewed: no unexplained `ERROR` lines.

Only then: set `TRADOVATE_ENV=live`, swap in the Apex-funded credentials, and
**start with `MAX_CONTRACTS_PER_ORDER=1`** for the first live week.
