# TradingView -> Tradovate: alert wiring

Everything needed to go from "the strategy looks good on the chart" to "the
broker has a bracketed position", plus how to prove each hop before risking
money on it.

The pipeline is:

```
Pine alert (JSON)
   -> POST https://<host>/webhook/tradingview/<WEBHOOK_API_KEY>
      -> schema validation      (app/models.py)
      -> symbol resolution      (app/symbols.py: MNQ1! -> MNQZ6)
      -> Apex risk guards       (app/risk.py: caps, duplicate, drawdown, kill switch)
      -> bracket construction   (app/orders.py: stop + TP in the SAME /order call)
      -> Tradovate REST /order  (app/client.py)
   <- {"accepted": true, "order_id": ..., "latency_ms": ...}
```

---

## 1. Wire the alert

1. **Chart:** open the Pine Editor, paste
   [`tradingview/vcantrade_webhook_alerts.pine`](tradingview/vcantrade_webhook_alerts.pine),
   "Add to chart". It plots EMA/ATR and fires `alert()` on a confirmed bar.
   Replace its signal block with your own logic — only the payload contract in
   section 2 matters.

2. **Alert dialog** (right-click the chart -> Add alert, or the clock icon):

   | Field | Value |
   |---|---|
   | Condition | your indicator -> **Any alert() function call** |
   | Trigger | Once per bar close (matches `alert.freq_once_per_bar_close`) |
   | Webhook URL | `https://<host>/webhook/tradingview/<WEBHOOK_API_KEY>` |
   | Message | leave the default text — `alert()` supplies the body |

   TradingView's alert dialog **cannot set HTTP headers**. That is the only
   reason a key is allowed in the URL; `ALLOW_URL_KEY=true` (the default)
   enables it. Treat the whole URL as a credential.

3. **Verify before arming.** Run the harness against the running service — do
   not trust a chart signal as the first test:

   ```powershell
   # local, DRY_RUN, no credentials, nothing leaves the box
   powershell -File scripts\run_dry_run_e2e.ps1

   # remote / demo / live service
   python scripts\e2e_signal_test.py --url https://trade.example.com --key <WEBHOOK_API_KEY> --url-key
   ```

---

## 2. The payload contract

The Pine script must emit exactly this JSON as the alert body. Unknown fields
are ignored, so the schema can grow without touching the script.

| Field | Type | Required | Notes |
|---|---|---|---|
| `signal_id` | string | **yes** | Idempotency key. Fired twice inside `DUPLICATE_SIGNAL_WINDOW_SEC` (5 s) -> `[DUPLICATE]` rejection. Use `syminfo.ticker + action + bar_index + time`. |
| `action` | `BUY` / `SELL` / `FLATTEN` | **yes** | Case-insensitive. |
| `symbol` | string | **yes** | TradingView symbol; the `!` is fine (`MNQ1!`, `NQ1!`, `ES1!`). Resolved to the Tradovate contract server-side. |
| `quantity` | int >= 1 | no | Omitted -> 1. Above `MAX_CONTRACTS_PER_ORDER` -> `[MAX_CONTRACTS]`. |
| `entry_price` | float > 0 | in practice **yes** | The bracket geometry is validated against it (stop below a BUY entry, above a SELL entry). |
| `stop_loss` | float > 0 | **yes** | Required while `REQUIRE_EXPLICIT_STOP=true`. This is what makes the order bracketed. |
| `take_profit` | float > 0 | no | When present it must be on the correct side of the entry. |
| `timestamp_ms` | int | no | Alert fire time; recorded for traceability. |

A buy, as the Pine script emits it:

```json
{
  "signal_id": "MNQ1!-BUY-14823-1790207842380",
  "action": "BUY",
  "symbol": "MNQ1!",
  "quantity": 1,
  "entry_price": 21000.00,
  "stop_loss": 20980.00,
  "take_profit": 21040.00,
  "timestamp_ms": 1790207842380
}
```

A flatten — no price fields, because a market exit has no bracket:

```json
{
  "signal_id": "MNQ1!-FLATTEN-14830-1790207999000",
  "action": "FLATTEN",
  "symbol": "MNQ1!",
  "quantity": 1
}
```

Rules enforced before anything is sent to the broker:

- `BUY`: `stop_loss` < `entry_price` < `take_profit`
- `SELL`: `take_profit` < `entry_price` < `stop_loss`
- prices are re-rounded to the instrument tick (0.25 for NQ/MNQ)
- unknown symbol -> `[SYMBOL]`; malformed JSON -> `HTTP 400`; wrong field type
  (e.g. `"stop_loss": "abc"`) -> `HTTP 422`, never a 500

---

## 3. What comes back

Accepted (HTTP 200):

```json
{"accepted": true, "signal_id": "MNQ1!-BUY-14823-1790207842380",
 "symbol": "MNQZ6", "action": "BUY", "quantity": 1,
 "order_id": "3", "broker_status": "Working", "latency_ms": 6.32}
```

Rejected — HTTP 200 with `accepted: false`, or 401/403/422/502 for the
transport-level cases:

| `reason` prefix | Meaning | Fix |
|---|---|---|
| `[SYMBOL]` | Symbol not in the TV -> Tradovate map | Add it to `app/symbols.py` |
| `[DUPLICATE]` | Same `signal_id` inside the window | Expected on re-fires; make `signal_id` unique per intended trade |
| `[QTY]` / `[MAX_CONTRACTS]` | `quantity` <= 0 or above the cap | Lower `quantity` in the alert |
| `[MAX_POSITIONS]` | `MAX_TOTAL_OPEN_POSITIONS` reached | Flatten or raise the cap deliberately |
| `[DAILY_LOSS]` / `[TRAILING_DD]` | Apex loss guard tripped | Stop trading; review the account |
| `[KILL_SWITCH]` | `KILL_SWITCH_FILE` exists | Delete the file / `DELETE /admin/kill-switch` |
| `[ORDER_BUILD]` | Bracket is geometrically impossible or the stop is missing | Fix the Pine price levels |
| `[BROKER]` / `[BROKER_REJECT]` | Tradovate refused or failed | Check `TRADOVATE_*` credentials and the broker message |
| HTTP 401 | Missing/invalid key, or `ALLOW_URL_KEY=false` while using a URL key | Match `WEBHOOK_API_KEY`; check the flag |
| HTTP 403 | Source IP not in `TRADINGVIEW_IP_ALLOWLIST` | Add the egress IP, or leave the allowlist empty |
---

## 4. Verify without a broker (DRY_RUN)

`DRY_RUN=true` replaces `app/client.py` with `app/sim_broker.py`. Everything
above still runs — validation, risk guards, bracket construction, the submit
call — but orders land in `state/sim_orders.json` instead of Tradovate, and the
whole check needs no credentials:

```powershell
powershell -File scripts\run_dry_run_e2e.ps1
```

which runs `scripts/e2e_signal_test.py`, 28 checks in six scenarios:

| Scenario | What it proves |
|---|---|
| `[0]` | `/health` up; no key -> 401; wrong key -> 401; `/status` reports the mode |
| `[1]` | missing stop, stop on the wrong side, `quantity=99`, unknown symbol, garbage JSON (422), array body (400) are all rejected *before* the broker |
| `[2]` | a byte-identical re-fire is filtered as `[DUPLICATE]` |
| `[3]` | a BUY arrives at the broker as `Market` + `isAutomated` **with** `bracket.stopLoss` and `bracket.takeProfit` attached |
| `[4]` | a SELL arrives with the stop on the correct side |
| `[5]` | nothing is left open, kill switch idle, `naked_orders == 0` |

The simulated broker is inspectable while the service runs:

| Endpoint | Purpose |
|---|---|
| `GET /sim/orders?limit=20` | summary + recorded orders, each with its bracket legs |
| `POST /sim/reset` | clear the log for a clean run (both return 409 outside DRY_RUN) |

Each record contains the *payload that would have gone to Tradovate*:

```json
{"order_id": "3", "action": "Buy", "symbol": "MNQZ6", "quantity": 1,
 "order_type": "Market", "is_automated": true, "is_flatten": false,
 "bracket_attached": true, "stop_price": 20980.0, "take_profit_price": 21040.0,
 "payload": {"accountSpec": "Trials", "accountId": 1, "action": "Buy",
             "symbol": "MNQZ6", "orderQty": 1, "orderType": "Market",
             "isAutomated": true,
             "bracket": {"stopLoss": {"orderQty": 1, "stopPrice": 20980.0},
                         "takeProfit": {"orderQty": 1, "limitPrice": 21040.0}}}}
```

`naked_orders` counts entries that reached the broker without a
`bracket.stopLoss`. The simulator deliberately accepts them (a real broker
would too) so that the counter, not a rejection, is the thing you assert on:
**it must stay at zero for the life of the account.**

---

## 5. Hardening the URL key

The URL is a credential, so:

1. **TLS only.** TradingView will not post to plain HTTP, and you should not
   want it to. Terminate TLS at nginx/Caddy in front of the middleware.
2. **Pin the source IP** once you know it:

   ```
   TRADINGVIEW_IP_ALLOWLIST=52.89.214.238,34.212.75.30
   ```

   TradingView does not publish a guaranteed-stable webhook egress range, so
   take the IP from a real delivery: your nginx access log, or the middleware
   line `URL-key webhook rejected from non-allowlisted IP <ip>`. URL-key auth is
   the only carrier that is IP-checked; header auth is not.
3. **Rotate the key** by changing `WEBHOOK_API_KEY` and re-pasting the alert
   URL. Do it after any shared screenshot, screen-share or chat message.
4. **Close the URL carrier** entirely once a proxy injects the header:

   ```
   ALLOW_URL_KEY=false
   ```

   with nginx adding `proxy_set_header X-API-Key <key>;` — see `DEPLOYMENT.md`.
   Header auth takes priority over the URL, so the change is a one-line edit and
   no TradingView alert is touched.
5. **Keep the compensating controls on** regardless: the duplicate-signal
   window, the per-order cap, and the kill switch are what limit the damage if
   the URL ever leaks.

---

## 6. Alerts that never arrive

Work down this list — every hop logs something:

1. **Did TradingView fire?** The alert's log pane shows each delivery attempt
   and the HTTP status. Nothing there -> the condition or `alert()` never ran:
   check that the alert uses "Any alert() function call" and that the script
   actually calls `alert()` on that bar.
2. **Did it arrive?** The middleware logs `webhook received: {...}` at INFO for
   every body it parses. No such line -> the request never reached the service.
3. **HTTP 401 / 403?** See the table in section 3. 403 is always the IP
   allowlist; 401 is always the key.
4. **Reached, but rejected?** A `RISK BLOCK signal=... rule=...` line names the
   rule. HTTP 200 with `accepted: false` is a *deliberate* rejection.
5. **`connection refused` / timeout in the TV log?** The service is down or the
   port is not forwarded. Note that on Windows an unrelated process bound to the
   same port can answer 404 for `/health` and look deceptively alive — the
   runner script checks for this and names the offending process.
6. **Wrong symbol in Tradovate?** The alert carries the TradingView symbol; the
   middleware maps it (`MNQ1!` -> `MNQZ6`). Pin the contract in
   `app/symbols.py::SYMBOL_OVERRIDES` if the roll heuristic ever guesses wrong.
