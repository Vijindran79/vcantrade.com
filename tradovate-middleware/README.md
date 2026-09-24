# Tradovate Execution Middleware

A standalone FastAPI service that turns TradingView webhook alerts into
Tradovate bracket orders. **No browser, no Playwright, no LLM in the order
path.** Built to replace the legacy PyQt6 / GhostExecutor surface described in
[`../AUDIT.md`](../AUDIT.md).

```
TradingView alert (Pine script)
        |  HTTPS POST, JSON
        v
+---------------------------+
|  FastAPI middleware       |   <- this service, ~1.2k LOC
|  - schema validation      |
|  - Apex risk guards       |
|  - bracket construction   |
|  - Tradovate REST call    |
+---------------------------+
        |  HTTPS, Bearer token
        v
Tradovate API (demo or live)
        |
        v
Apex Trader Funding account
```

Target latency: **< 100 ms** middleware-side, excluding the broker round-trip
(typically 40–90 ms to Tradovate's us-east endpoint from a us-east VPS).

---

## Why not a hosted bridge (Option B)?

PickMyTrade, Roboquant Connect, and similar services were evaluated. They are
fine for hobby use but rejected here for three reasons:

1. **You do not control the risk layer.** Apex's trailing drawdown is unforgiving;
   the per-order contract cap, daily-loss ceiling, and duplicate-signal window
   must be enforced *before* the order leaves your infrastructure, with your
   thresholds, auditable in your logs.
2. **Per-trade fees + latency.** A hosted bridge adds a network hop (typically
   150–400 ms) and a monthly fee that scales with trade count.
3. **Vendor lock-in on the order schema.** Bracket construction (stop + TP
   attached atomically to the entry) is the single most important safety
   property for Apex. A bridge that does not expose Tradovate's native
   `bracket` field forces you into a Python-managed stop, which is exactly the
   failure mode the audit identified in the legacy code.

Building the middleware is ~1,200 lines of well-tested Python. The ongoing
cost is a $5/mo VPS. The bridge is never cheaper and never safer.

---

## Quickstart

### 1. Prove the whole path with no credentials (DRY_RUN)

```powershell
cd tradovate-middleware
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# in .env: the example ships with DRY_RUN=false, so set DRY_RUN=true, and give
#          WEBHOOK_API_KEY a long random value:
#          python -c "import secrets; print(secrets.token_hex(32))"

# one command: starts the service in DRY_RUN and forces a full pass of test alerts
powershell -File scripts\run_dry_run_e2e.ps1
```

That runner boots the middleware, waits for `/health`, and then drives
`scripts/e2e_signal_test.py` — a suite of forced signals (valid BUY/SELL
brackets, a re-fired duplicate, a missing stop, a stop on the wrong side, an
oversized quantity, an unknown symbol, garbage JSON, FLATTEN) — with the shared
secret in the URL exactly the way a TradingView alert must carry it. It exits
`0` only if every scenario behaved as specified.

`DRY_RUN=true` swaps the Tradovate client for `app/sim_broker.py`: schema
validation, the risk guards, bracket construction and the submit call all run
unchanged, orders are recorded **with their bracket legs** to
`state/sim_orders.json`, and no order can reach the broker. The one invariant it
tracks explicitly is `naked_orders` — an entry that arrived without a stop —
which must stay at zero.

### 2. Then the real thing (demo account)

```bash
cd tradovate-middleware
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env:
#   DRY_RUN=false
#   TRADOVATE_ENV=demo
#   TRADOVATE_APP_ID / CID / SEC  <- from https://developer.tradovate.com
#   WEBHOOK_API_KEY               <- openssl rand -hex 32
#   TRADOVATE_ACCOUNT_SPEC=Trials

python -m app.main
# -> middleware ready env=demo account=... webhook=/webhook/tradingview
```

Then run the same harness against the real service — it skips the
simulator-only assertions and tells you what to confirm in the Tradovate UI:

```powershell
python scripts\e2e_signal_test.py --key $env:WEBHOOK_API_KEY --url-key
```

Smoke test:

```bash
curl -X POST http://127.0.0.1:8080/webhook/tradingview \
  -H "X-API-Key: $WEBHOOK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "signal_id": "test-001",
        "action": "BUY",
        "symbol": "MNQ1!",
        "quantity": 1,
        "entry_price": 21000.00,
        "stop_loss": 20980.00,
        "take_profit": 21040.00
      }'
```

Expected: `200 OK` with `accepted: true` and an `order_id` from Tradovate.
Check the Tradovate demo UI — the position should appear with a stop and a
limit already attached.

---

## TradingView alert configuration

In your Pine script, emit alerts with `alert(message, alert.freq_once_per_bar_close)`
where `message` is a JSON string:

```pine
alert_message = '{"signal_id":"' + syminfo.ticker + '-' + str.tostring(bar_index) + '-' + str.tostring(time) + '",' +
                '"action":"' + (long_signal ? "BUY" : short_signal ? "SELL" : "FLATTEN") + '",' +
                '"symbol":"' + syminfo.ticker + '",' +
                '"quantity":1,' +
                '"entry_price":' + str.tostring(close) + ',' +
                '"stop_loss":' + str.tostring(stop_price) + ',' +
                '"take_profit":' + str.tostring(tp_price) + '}'
alert(alert_message, alert.freq_once_per_bar_close)
```

In TradingView's alert dialog:
- **Condition:** your indicator -> *Any alert() function call*
- **Webhook URL:** `https://your-vps.example.com/webhook/tradingview/<WEBHOOK_API_KEY>`
- **Message:** leave the dialog text alone — `alert()` supplies the JSON body
- **Trigger:** once per bar close (matches `alert.freq_once_per_bar_close`)

A ready-made script lives in
[`tradingview/vcantrade_webhook_alerts.pine`](tradingview/vcantrade_webhook_alerts.pine);
[TRADINGVIEW_SETUP.md](TRADINGVIEW_SETUP.md) documents the payload contract,
every rejection reason, and how to verify a delivery end to end.

The key travels **in the URL** because TradingView's alert dialog cannot set HTTP
headers. That is the only reason `ALLOW_URL_KEY` defaults to `true`: pin the
source with `TRADINGVIEW_IP_ALLOWLIST`, terminate TLS in front of the service,
and rotate the key if the URL ever leaks. Once a reverse proxy can inject
`X-API-Key` (see DEPLOYMENT.md), set `ALLOW_URL_KEY=false` — header auth takes
priority, so the TradingView alerts need no change.

---

## Risk guards (server-side, non-negotiable)

| Guard | Env var | Default | Behavior on breach |
|---|---|---|---|
| Duplicate signal window | `DUPLICATE_SIGNAL_WINDOW_SEC` | 5 | Reject, log `DUPLICATE` |
| Per-order contract cap | `MAX_CONTRACTS_PER_ORDER` | 2 | Reject, log `MAX_CONTRACTS` |
| Total open positions | `MAX_TOTAL_OPEN_POSITIONS` | 2 | Reject, log `MAX_POSITIONS` |
| Daily realized loss | `MAX_DAILY_LOSS_USD` | 1500 | Reject all new entries until UTC midnight |
| Trailing drawdown | `TRAILING_DRAWDOWN_USD` | 2000 | Reject all new entries; ops should flatten |
| Kill switch file | `KILL_SWITCH_FILE` | `./state/KILL_SWITCH` | Reject everything; `touch` the file to engage |
| Explicit stop required | `REQUIRE_EXPLICIT_STOP` | true | Reject signals with no `stop_loss` |

State persists to `STATE_FILE` (default `./state/risk_state.json`) after every
mutation, atomically via `os.replace`. A restart mid-session does not reset the
daily counters.

### Runtime profiles

| Env var | Default | Purpose |
|---|---|---|
| `DRY_RUN` | `false` | Swap the Tradovate client for `app/sim_broker.py`. The full path still runs; orders are recorded to `state/sim_orders.json` with their bracket legs, and nothing can reach the broker. No credentials required. |
| `ALLOW_URL_KEY` | `true` | Accept the shared secret in the URL path/query. Exists only because a TradingView alert cannot send headers. |
| `TRADINGVIEW_IP_ALLOWLIST` | empty | Comma-separated source IPs allowed to use URL-embedded keys. Empty = any source. Header auth is not IP-checked. |

URL-key auth is the weaker carrier, so it is the one that carries the IP
allowlist, and the middleware logs a warning at startup while it is enabled.

---

## Admin endpoints

All require `X-API-Key`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness (no auth) |
| GET | `/status` | Risk snapshot, account info, `dry_run` flag, sim summary |
| POST | `/webhook/tradingview` | Alert endpoint (auth: `X-API-Key`, Bearer, or `?key=`) |
| POST | `/webhook/tradingview/{key}` | Same, key in the path — the TradingView carrier |
| POST | `/admin/kill-switch` | Engage kill switch |
| DELETE | `/admin/kill-switch` | Disengage |
| POST | `/admin/flatten` | Market-close every open position |
| GET | `/sim/orders` | Simulated orders + bracket legs (DRY_RUN only, else 409) |
| POST | `/sim/reset` | Clear the simulated order log (DRY_RUN only, else 409) |

---

## Files

```
tradovate-middleware/
├── app/
│   ├── __init__.py
│   ├── main.py            FastAPI app + webhook handler
│   ├── config.py          pydantic-settings
│   ├── models.py          signal schema
│   ├── symbols.py         TV symbol -> Tradovate contract
│   ├── auth.py            access-token lifecycle (auto-refresh)
│   ├── client.py          Tradovate REST client
│   ├── sim_broker.py      DRY_RUN stand-in client (records bracketed orders)
│   ├── orders.py          bracket-order construction
│   ├── risk.py            Apex guards + persisted state
│   └── logging_setup.py   structured JSON logs
├── tests/
│   ├── test_risk.py
│   ├── test_symbols.py
│   ├── test_orders.py
│   └── test_webhook_e2e.py   whole app: webhook -> risk -> bracket -> sim broker
├── scripts/
│   ├── run_dry_run_e2e.ps1   start DRY_RUN + drive the harness (one command)
│   └── e2e_signal_test.py    forced-signal harness against any running instance
├── tradingview/
│   └── vcantrade_webhook_alerts.pine   alert() payload generator
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── README.md              <- you are here
├── TRADINGVIEW_SETUP.md   alert wiring, payload contract, troubleshooting
├── SIM_TESTING.md         demo-account verification plan
└── DEPLOYMENT.md          VPS / Docker / monitoring / logging
```

---

## What this service deliberately does NOT do

- Run an LLM. The signal *is* the decision.
- Drive a browser. There is no Playwright, Selenium, pyautogui, or CDP.
- Manage a PyQt GUI. The legacy dashboard can stay running for monitoring; it
  cannot place orders once `LEGACY_EXECUTION_DISABLED=True`.
- Backtest. Use `core/backtester.py` in the parent repo for that.
- Predict the market. That is the Pine script's job.
