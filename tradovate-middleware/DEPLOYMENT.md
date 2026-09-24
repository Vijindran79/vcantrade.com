# Deployment, Monitoring & Logging

The middleware is a single stateless-ish FastAPI process. It should run on a
small VPS in the **same geographic region as Tradovate's API** (us-east) to
minimize the broker round-trip. A $5/mo instance (1 vCPU, 1 GB RAM) is
sufficient; the process idles at ~60 MB RSS.

---

## 1. Host selection

| Requirement | Why |
|---|---|
| us-east region (AWS us-east-1, DigitalOcean NYC, Vultr NJ, Hetzner Ashburn) | Lowest latency to `live.tradovateapi.com` |
| Static IP + reverse proxy with TLS | TradingView webhooks require HTTPS |
| `systemd` or Docker with `restart: unless-stopped` | Auto-recover after crash/reboot |
| Persistent disk for `./state/` | Risk state must survive restarts |
| NTP synchronized clock | Duplicate-window and daily-roll logic depend on accurate time |

Avoid serverless (Lambda / Cloud Run) for v1: cold starts add 200 ms–2 s,
which blows the latency budget, and the background token-refresh timer does
not fit the request/response model. A always-on container is simpler and
faster. Revisit serverless only if you switch to provisioned concurrency.

---

## 2. Docker deployment (recommended)

```bash
# on the VPS
git clone https://github.com/Vijindran79/vcantrade.com.git
cd vcantrade.com/tradovate-middleware

cp .env.example .env
nano .env                      # fill in credentials, TRADOVATE_ENV=demo first
mkdir -p state

docker compose up -d --build
docker compose logs -f middleware
```

The compose file binds to `127.0.0.1:8080` only — it is **not** exposed to the
public internet directly. nginx terminates TLS in front of it (next section).

Verify:

```bash
curl -fsS http://127.0.0.1:8080/health
# {"status":"ok","env":"demo"}
```

### 2.1 First boot in DRY_RUN (no credentials, nothing can trade)

Bring the service up with `DRY_RUN=true` before the demo credentials exist and
drive the harness through the proxy. That single pass proves TLS termination,
the `/webhook/tradingview/<key>` route, the key carrier and the bracket
construction — with nothing that can reach a broker:

```bash
python3 scripts/e2e_signal_test.py --url http://127.0.0.1:8080 --key "$WEBHOOK_API_KEY" --url-key
docker compose up -d --build          # after copying the DRY_RUN .env
python3 scripts/e2e_signal_test.py --url https://trade.yourdomain.com --key "$WEBHOOK_API_KEY" --url-key
```

Exit code `0` means every scenario matched; anything else names the failing
check. Only then set `DRY_RUN=false` and restart. With DRY_RUN off, the harness
replaces its simulator assertions with `NOTE: open Tradovate now ...` prompts,
so the same command verifies the real broker path.

---

## 3. Reverse proxy (nginx + TLS)

TradingView webhooks cannot send custom headers. Two arrangements work; pick
one deliberately.

**A. Key in the URL (no proxy logic).** The alert posts to
`https://trade.yourdomain.com/webhook/tradingview/<WEBHOOK_API_KEY>`;
nginx only terminates TLS and proxies. Keep `ALLOW_URL_KEY=true` and treat the
nginx `allow` list below (or the middleware's `TRADINGVIEW_IP_ALLOWLIST`,
which is the equivalent one hop later) as the compensating control. The secret
is part of the request URI, so keep it out of the access log — section 7.

**B. Key injected by nginx (stronger).** The alert posts to
`https://trade.yourdomain.com/webhook/tradingview` and the proxy adds
`X-API-Key`. Set `ALLOW_URL_KEY=false`. Header auth takes priority over the URL
carrier, so switching from A to B needs no change to any TradingView alert. The
config below is this arrangement.

```nginx
server {
    listen 443 ssl http2;
    server_name trade.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/trade.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/trade.yourdomain.com/privkey.pem;

    # TradingView webhook egress ranges (verify current list in TV docs)
    allow 52.89.214.238;
    allow 34.212.75.30;
    allow 54.218.53.128;
    allow 52.32.178.7;
    deny all;

    location /webhook/tradingview {
        auth_basic "vcantrade";
        auth_basic_user_file /etc/nginx/.htpasswd;

        proxy_pass http://127.0.0.1:8080;
        proxy_set_header X-API-Key "YOUR_WEBHOOK_API_KEY";   # injected here
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header Host $host;
        proxy_read_timeout 10s;
        proxy_connect_timeout 3s;
    }

    location /health {
        proxy_pass http://127.0.0.1:8080/health;
    }

    # /status and /admin/* are NOT exposed publicly — reach them via SSH tunnel
    location /admin { deny all; }
    location /status { deny all; }
}
```

TradingView alert webhook URL:

- arrangement A (key in the URL): `https://user:pass@trade.yourdomain.com/webhook/tradingview/<WEBHOOK_API_KEY>`
- arrangement B (nginx injects the key): `https://user:pass@trade.yourdomain.com/webhook/tradingview`

The `user:pass@` embedded basic-auth credentials are optional — drop them if you
rely on `TRADINGVIEW_IP_ALLOWLIST` (or the nginx `allow` list) instead. For the
alert dialog fields themselves, see `TRADINGVIEW_SETUP.md` section 1.

Get TLS: `certbot --nginx -d trade.yourdomain.com`.

---

## 4. Bare-metal / systemd alternative

```ini
# /etc/systemd/system/vcantrade-middleware.service
[Unit]
Description=vcantrade Tradovate execution middleware
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=vcantrade
WorkingDirectory=/opt/vcantrade/tradovate-middleware
EnvironmentFile=/opt/vcantrade/tradovate-middleware/.env
ExecStart=/opt/vcantrade/tradovate-middleware/.venv/bin/python -m app.main
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal
# hardening
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/vcantrade/tradovate-middleware/state
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vcantrade-middleware
journalctl -u vcantrade-middleware -f
```

---

## 5. Logging

The middleware emits **one JSON line per event** to stdout (captured by Docker
json-file driver or journald). Fields: `ts`, `level`, `logger`, `msg`.

Key log lines to alert on:

| Pattern | Meaning | Action |
|---|---|---|
| `ORDER PLACED signal=... total_ms=...` | Successful execution | Metric: latency histogram |
| `RISK BLOCK ... rule=DAILY_LOSS` | Daily ceiling hit | Page ops — flatten and stop |
| `RISK BLOCK ... rule=TRAILING_DD` | Drawdown breaker | Page ops immediately |
| `RISK BLOCK ... rule=DUPLICATE` | Filtered re-fire | Info only, unless frequent |
| `broker rejected order` | Tradovate refused | Page ops — check margin/contract |
| `401 from ... forcing token refresh` | Token expired early | Warn; investigate if repeated |
| `tradovate token acquired` | Normal hourly refresh | Info |
| `KILL SWITCH ENGAGED` | Manual or auto halt | Page ops |
| `LIVE TRADOVATE ENDPOINT ARMED` | Running against real money | Should appear once at boot |

Ship logs to your stack of choice. Minimal setup — Docker json-file is already
configured with rotation (20 MB × 10 files) in `docker-compose.yml`. For
centralized logging, point a Promtail / Filebeat / Vector agent at the
container logs and ship to Loki / ELK / Datadog.

Example Vector config:

```toml
[sources.middleware]
type = "docker_logs"
include_containers = ["tradovate-middleware-middleware-1"]

[sinks.loki]
type = "loki"
inputs = ["middleware"]
endpoint = "http://loki:3100"
labels = { app = "vcantrade", env = "${TRADOVATE_ENV}" }
```

---

## 6. Monitoring & alerting

Minimum viable monitoring:

1. **Uptime:** external ping to `https://trade.yourdomain.com/health` every
   30 s (UptimeRobot, Healthchecks.io, Better Stack). Alert on 2 consecutive
   failures.
2. **Latency:** scrape the `total_ms` from `ORDER PLACED` lines into a
   histogram. Alert if p99 > 200 ms for 5 minutes.
3. **Risk state:** poll `/status` (via SSH tunnel or internal network) every
   minute. Alert if:
   - `kill_switch == true`
   - `trailing_drawdown_usd > 0.75 * TRAILING_DRAWDOWN_USD` (early warning)
   - `realized_pnl_today_usd < -0.75 * MAX_DAILY_LOSS_USD` (early warning)
4. **Process health:** `docker compose ps` / `systemctl is-active`. Alert on
   restart loops (`RestartSec=3` + > 5 restarts in 10 min).
5. **Token age:** alert if no `tradovate token acquired` line in 70 minutes
   (refresh should fire hourly).

Grafana + Prometheus example (requires adding a `/metrics` endpoint — left as
an exercise; the JSON logs are sufficient for Loki-based alerting).

---

## 7. Secrets management

- `.env` must be `chmod 600` and owned by the service user. It is in
  `.gitignore` — verify before every commit.
- While `ALLOW_URL_KEY=true` the shared secret is part of the request URI and
  therefore lands in nginx `access.log` in clear text. Either add
  `access_log off;` to the webhook `location`, or switch to header injection
  (`ALLOW_URL_KEY=false`) on any host whose logs leave the machine.
- `WEBHOOK_API_KEY`, `TRADOVATE_SEC`, and the nginx basic-auth password are
  the three secrets that matter. Rotate quarterly.
- Never bake secrets into the Docker image. They are injected at runtime via
  `env_file`.
- For production, consider Docker secrets or an external vault (Infisical,
  Doppler, AWS Secrets Manager) rather than a plaintext `.env`.

---

## 8. Go-live runbook

1. Complete every phase in `SIM_TESTING.md` on demo. Get sign-off. Before any
   credential exists, run section 2.1 (DRY_RUN + the harness through nginx) —
   the cheapest possible place to catch a proxy, TLS or auth mistake.
2. On the VPS: set `TRADOVATE_ENV=live`, swap in Apex-funded `CID`/`SEC`,
   set `TRADOVATE_ACCOUNT_SPEC` to the funded spec, set
   `MAX_CONTRACTS_PER_ORDER=1` for week one.
3. `docker compose up -d --force-recreate`.
4. Confirm the boot log shows `LIVE TRADOVATE ENDPOINT ARMED`.
5. Send one manual MNQ webhook with a tight stop. Confirm the fill + bracket
   in the **live** Apex account UI.
6. Flatten it via `/admin/flatten`. Confirm flat.
7. Enable the TradingView alert.
8. Watch the first session live. Do not walk away.
9. After 5 clean live days, raise `MAX_CONTRACTS_PER_ORDER` to the Apex limit
   for the account size (50k = 2 minis / 20 micros).

**Emergency stop:** `curl -X POST https://.../admin/kill-switch -H "X-API-Key: $KEY"`
or `ssh vps 'touch /opt/vcantrade/tradovate-middleware/state/KILL_SWITCH'`.
Then `/admin/flatten`. Both are idempotent and safe to call repeatedly.
