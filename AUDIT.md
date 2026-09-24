# Codebase Audit — vcantrade.com (Apex / Tradovate)

**Audit date:** 2026-09-22
**Scope:** order-execution critical path, broker integration, risk controls, latency sources.
**Verdict:** the current system is **not safe to run against a live Apex funded account**. The execution path is built around browser automation of the TradingView Desktop client and gates orders behind an LLM. Both choices are incompatible with Apex's real-time trailing drawdown.

The recommended path forward is the new `tradovate-middleware/` service shipped with this audit. It is a clean, standalone FastAPI process that talks directly to the Tradovate REST API. The legacy PyQt6 / Playwright app is preserved as a research/dashboard tool but is **not** allowed to place live orders once the middleware is authoritative.

---

## 1. Critical findings

### F1 — Orders are executed by clicking the TradingView Desktop UI (BLOCKER)

Evidence:

- `requirements.txt:30` — `playwright>=1.40.0` listed as a runtime dep.
- `.env.example:14-17` — `BROWSER_CDP_URL=http://127.0.0.1:9222`, `TRADING_SURFACE=TRADINGVIEW_DESKTOP`.
- `core/ghost_executor.py` — 812 lines whose entire job is to drive a Playwright `Page` attached over CDP, evaluate JS in TradingView's renderer, dispatch synthetic `MouseEvent('click')` on the Buy/Sell buttons, then screenshot the result (`_save_post_click_screenshot`).
- `execution/rpa_executor.py` — 3,606 lines of RPA fallback (pyautogui, screen scraping, window enumeration) used when the Playwright click fails.
- `core/hybrid_execution_gateway.py:9-11` — comment explicitly states the two armed surfaces are `TRADINGVIEW` (GhostExecutor JS click) and `MT5` (native). There is **no Tradovate REST surface**.

Why this is fatal for Apex:

- **Latency.** Click → renderer event loop → TradingView's own order ticket → TradingView's backend → broker. Realistic end-to-end is 800 ms – 3 s. Apex's intraday trailing drawdown updates tick-by-tick on unrealized P&L; a 2 s slip on NQ at 20 ticks/$200 = $100 of avoidable drawdown per fill.
- **Fragility.** TradingView ships DOM changes weekly. The selector "top-left quote button" path in `ghost_executor.py:352` (`_click_top_left_quote_button`) is already a workaround for an earlier selector break.
- **Silent partial fills.** The post-click "verify" (`ghost_executor.py:170-200`) is a screenshot + heuristic, not an authoritative broker fill report. The bot has no way to know the order actually reached Tradovate, partially filled, or was rejected for insufficient margin.
- **No bracket attachment.** A UI click places a market order. The stop-loss / take-profit are managed by Python code polling the position afterwards. If the process crashes, restarts, or the network drops, the position is naked.
- **No idempotency.** A double-click, a stale UI state, or a retried signal places two orders.

### F2 — LLM (Ollama) is in the order-decision path (BLOCKER)

Evidence:

- `core/trade_engine.py:79` — `process_signal(self, signal: LLMAnalysisOutput, mode: str = "TEACHER")`. The signal type *is* an LLM output.
- `.env.example:46-52` — `OLLAMA_MODEL=predator:latest`, `MICRO_BRAIN_MODEL=qwen2.5:latest`, `LLM_TIMEOUT=180` (three minutes).
- Git log: `6049532 HAWK MODE: Brain override for confidence floor`, `cb2e53f REFLEX: decouple exit engine from Ollama` — the team has already been bitten by Ollama latency on the exit side and partially decoupled it. The entry side is still gated.
- `core/brain.py`, `core/brain_swarm.py`, `core/llm_analyzer.py`, `core/devils_advocate.py`, `core/headmaster_agent.py`, `core/swarm_consensus.py` — a multi-agent LLM consensus stack runs before a trade fires.

Why this is fatal:

- A 7B local model on a laptop is 1–8 s per inference. A swarm consensus is N × that. The user's stated requirement is **sub-100 ms signal-to-order**. LLMs cannot meet that budget and never will on local hardware.
- LLM outputs are non-deterministic. Two identical market states can produce different actions, which makes the system unauditable and unreproducible — a hard requirement for prop-firm compliance review.
- The "confidence floor" + "brain override" logic (commits `00a70d8`, `8187bf1`) is a patch on top of a patch. It is the kind of code that silently lets a bad trade through.

**Rule going forward:** the LLM may inform *research*, *journaling*, and *post-trade analysis*. It must never sit between a webhook and an order.

### F3 — No Tradovate API integration exists

Evidence: `grep -rn -i tradovate` returns 25 hits, all of which are *surface labels* (`TRADING_SURFACE=TRADINGVIEW_TRADOVATE`, `_is_tradingview_tradovate_mode()`). There is no `auth/accessTokenRequest` call, no REST client, no WebSocket subscription, no contract resolution, no bracket-order construction.

The system is named after Tradovate but has never spoken to Tradovate's API.

### F4 — Risk controls exist but are not enforced at the broker boundary

Evidence:

- `core/prop_firm_rules.py` — has `FirmRules`, `can_trade()`, `update_trade()`, `reset_daily()`. Good shape.
- `core/risk_governor.py` — correlation, exposure, cooldown-after-losses. Good shape.
- `core/risk_manager.py` — position sizing from balance + stop distance. Good shape.

But:

- All three run **inside the Python process**, before the order reaches the broker. If the process crashes mid-trade, the stop-loss is never placed.
- `.env.example:38` — `MAX_DAILY_LOSS=0` (disabled) by default. The comment says "Apex has no daily loss limit; the trailing drawdown is the active guard" — true, but the *trailing drawdown* is exactly what the bot must not breach, and there is no code that tracks intraday high-water-mark equity and aborts trading when the buffer shrinks below a configurable threshold.
- `.env.example:40` — `MAX_OPEN_POSITIONS=3`. No per-symbol contract cap. A faulty signal can send 10 NQ contracts on a 50k Apex account (limit is 2 minis) and breach the drawdown in one tick.
- No duplicate-signal filter. `signal_dispatcher.py` validates auth and confidence but does not dedupe by `(symbol, action, timestamp_window)`. A TradingView alert re-fire, a network retry, or a cloud-scanner double-post will place two orders.
- No server-side hard stop. The bracket is conceptual — it lives in Python state, not in a Tradovate `bracket` order attached to the entry.

### F5 — Architectural debt that compounds the above

- `main.py` is **2,835 lines** and `main.py.backup` (40 KB) is checked in. `core/trade_engine.py.backup` and `ui/dashboard.py.backup` are also checked in. Dead code in the critical path is how bugs hide.
- 25+ top-level `*.md` status documents (`NUCLEAR_FIX_SUMMARY.md`, `UNLEASH_PREDATOR_FIXES.md`, `HAWK_MODE_UPGRADES.md`, `PREDATOR_CLASS_UPGRADE_SUMMARY.md`, …). Each one describes a hot-fix layered on the previous one. The system has no single source of truth for "how does an order get placed."
- Two SQLite databases (`vcanitrade_alerts.db`, `vcanitrade_ledger.db`) are checked into the repo. They will drift from production state and confuse anyone reading the code.
- `nuclear_purge.py` (11 KB) exists as a "fix everything" script. Its existence is itself a finding.
- PyQt6 is pinned to 6.7.1 because 6.11.0 crashes (`requirements.txt:8-9`). A desktop GUI framework has no business being a dependency of an order-execution service.

---

## 2. Latency budget — current vs. target

| Stage | Current | Target (middleware) |
|---|---|---|
| Signal source → bot | Cloud scanner HTTP POST → aiohttp → Qt signal → main loop | TradingView webhook → FastAPI (direct) |
| Decision | LLM swarm consensus (1–30 s) | None — signal is already the decision |
| Risk check | Python in-process (5–50 ms) | Python in-process, <2 ms (pure arithmetic + dict lookup) |
| Order placement | Playwright `page.evaluate` → TradingView DOM → TV backend → broker (800 ms – 3 s) | `POST /order` to Tradovate REST (40–90 ms typical) |
| Stop attachment | Python polling loop, async, can fail silently | `bracket.stopLoss` in the same `/order` call — atomic at the broker |
| **Total** | **1 s – 30 s** | **< 100 ms** |

---

## 3. What the refactor delivers

The new `tradovate-middleware/` service implements **Option A** from the brief: TradingView webhook → middleware → Tradovate REST. Option B (PickMyTrade / Roboquant) was evaluated and rejected — see `tradovate-middleware/README.md` §"Why not a hosted bridge".

Core properties:

1. **No browser, no Playwright, no LLM in the order path.** The middleware imports `fastapi`, `httpx`, `pydantic`, `pydantic-settings`. Nothing else touches an order.
2. **Atomic bracket orders.** Every entry is submitted with `bracket.stopLoss` and (optionally) `bracket.takeProfit` in the same `/order` call. If the broker rejects the bracket, the entry is rejected too — there is no naked position.
3. **Server-side Apex guards.** Daily-loss ceiling, per-symbol contract cap, total open-position cap, intraday trailing-drawdown circuit breaker, and a 5-second duplicate-signal window — all enforced *before* the HTTP call to Tradovate, and all persisted to disk so a restart mid-session does not reset them.
4. **Token lifecycle handled.** Tradovate access tokens expire hourly. A background task refreshes at T-5 min. Orders never fail with `401` because of a stale token.
5. **Idempotent.** Each webhook carries a `signal_id`; the middleware dedupes on it for the configured window.
6. **Kill switch.** A file-based toggle (`/etc/vcantrade/KILL_SWITCH`) flattens all positions and refuses new orders. Ops can stop the bot without a redeploy.
7. **Structured JSON logs.** Every webhook, every risk decision, every broker call, every fill — one JSON line, shipped to stdout, collected by the host.

---

## 4. Migration path

| Step | Action | Reversible? |
|---|---|---|
| 1 | Deploy `tradovate-middleware/` to a VPS pointed at the Tradovate **demo** endpoint. | Yes |
| 2 | Configure TradingView alerts to POST to the middleware webhook. | Yes |
| 3 | Run the sim plan in `tradovate-middleware/SIM_TESTING.md` for ≥ 5 trading days. | Yes |
| 4 | Set `LEGACY_EXECUTION_DISABLED=True` in the legacy app's `.env`. This makes `SurfaceRouter.execute()` a no-op so the PyQt app can still run as a dashboard but cannot place orders. | Yes |
| 5 | Switch the middleware to the Tradovate **live** endpoint with the Apex-funded credentials. | Yes (flip `TRADOVATE_ENV=demo`) |
| 6 | After 30 days clean, delete `core/ghost_executor.py`, `execution/rpa_executor.py`, `core/browser_agent.py`, and the `playwright` / `pyautogui` / `dxcam` / `mss` / `pygetwindow` deps. | No — but by then nothing depends on them |

Steps 1–5 are all configuration changes. Only step 6 deletes code, and only after the middleware has proven itself.

---

## 5. Items intentionally out of scope

- Rewriting the PyQt6 dashboard. It is useful for research and monitoring; it just must not touch orders.
- Porting the LLM swarm / brain / vision stack. Keep it for post-trade journaling if desired.
- Backtester changes. `core/backtester.py` is independent of the execution path.
- The Pine Script (`liquidity_zones.pine`). It is the signal source and is fine as-is; the middleware consumes its alerts.

---

## 6. Deliverables shipped with this audit

- `tradovate-middleware/` — the new execution service (FastAPI + httpx, ~1,200 LOC, fully tested).
- `tradovate-middleware/SIM_TESTING.md` — day-by-day verification plan on Tradovate demo.
- `tradovate-middleware/DEPLOYMENT.md` — VPS / Docker / systemd setup, monitoring, log shipping.
- `config.py` + `.env.example` — `LEGACY_EXECUTION_DISABLED` kill-switch added so the old app cannot fire orders while the middleware is authoritative.
- `requirements.txt` — `playwright` moved to an optional extra with a deprecation note.
