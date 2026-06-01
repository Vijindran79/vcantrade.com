"""
VcanTrade AI - Institutional Metrics API
==========================================
Lightweight HTTP server exposing institutional metrics.
Dashboard queries this for live performance data.
"""
import logging
import json
from typing import Optional
from aiohttp import web

from core.institutional_suite import suite
from core.performance_metrics import metrics as perf
from core.backtester import backtester, sma_crossover_strategy
from core.walk_forward import wfo

logger = logging.getLogger(__name__)

API_PORT = 17198
APP: Optional[web.Application] = None
RUNNER: Optional[web.AppRunner] = None


async def handle_full_report(request):
    """GET /api/institutional/full - Full institutional report."""
    return web.json_response(suite.full_report())


async def handle_performance(request):
    """GET /api/institutional/performance - Performance metrics only."""
    return web.json_response(perf.institutional_report())


async def handle_equity(request):
    """GET /api/institutional/equity - Equity curve snapshot."""
    return web.json_response(suite.equity.snapshot())


async def handle_equity_history(request):
    """GET /api/institutional/equity/history?minutes=60 - Historical equity."""
    minutes = int(request.query.get("minutes", 60))
    return web.json_response({
        "points": suite.equity.recent_points(minutes),
        "snapshot": suite.equity.snapshot(),
    })


async def handle_regime(request):
    """GET /api/institutional/regime - Current market regime."""
    return web.json_response({
        "current": suite.regime.current_regime.value,
        "params": suite.regime.get_params(),
        "should_trade": suite.regime.should_trade(),
    })


async def handle_execution_quality(request):
    """GET /api/institutional/execution - TCA report."""
    return web.json_response(suite.exec_analytics.tca_report())


async def handle_alerts(request):
    """GET /api/institutional/alerts - Recent alerts."""
    count = int(request.query.get("count", 20))
    return web.json_response({"alerts": suite.alerts.recent(count)})


async def handle_backtest(request):
    """POST /api/institutional/backtest - Run a backtest.
    Body: {"symbol": "MNQ1!", "candles": [...], "strategy": "sma_crossover"}
    """
    try:
        body = await request.json()
        symbol = body.get("symbol", "MNQ1!")
        candles = body.get("candles", [])
        strat_name = body.get("strategy", "sma_crossover")

        if not candles:
            # Generate synthetic candles for demo
            import random
            base = 18000.0
            candles = []
            for i in range(500):
                base += random.gauss(0, 5)
                candles.append({
                    "timestamp": f"2026-01-{(i % 30) + 1:02d}T09:30:00",
                    "open": base, "high": base + 3, "low": base - 3,
                    "close": base + random.gauss(0, 1), "volume": 1000 + i,
                })

        strategy = sma_crossover_strategy
        if strat_name == "none":
            strategy = lambda c: "HOLD"

        report = backtester.run(symbol, candles, strategy)
        return web.json_response(report)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_size(request):
    """POST /api/institutional/size - Calculate position size.
    Body: {"symbol": "MNQ1!", "entry": 18000, "sl": 17980, "atr": 15, ...}
    """
    try:
        body = await request.json()
        result = suite.size_position(
            symbol=body.get("symbol", "MNQ1!"),
            entry=body.get("entry", 18000.0),
            sl=body.get("sl", 17980.0),
            atr=body.get("atr", 15.0),
            win_rate=body.get("win_rate", 0.55),
            avg_win=body.get("avg_win", 100.0),
            avg_loss=body.get("avg_loss", 50.0),
            confidence=body.get("confidence", 1.0),
        )
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_health(request):
    return web.json_response({"status": "ok", "service": "institutional_api"})


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/institutional/full", handle_full_report)
    app.router.add_get("/api/institutional/performance", handle_performance)
    app.router.add_get("/api/institutional/equity", handle_equity)
    app.router.add_get("/api/institutional/equity/history", handle_equity_history)
    app.router.add_get("/api/institutional/regime", handle_regime)
    app.router.add_get("/api/institutional/execution", handle_execution_quality)
    app.router.add_get("/api/institutional/alerts", handle_alerts)
    app.router.add_post("/api/institutional/backtest", handle_backtest)
    app.router.add_post("/api/institutional/size", handle_size)
    app.router.add_get("/api/institutional/health", handle_health)
    return app


async def start_server():
    """Start the institutional API server."""
    global APP, RUNNER
    APP = build_app()
    RUNNER = web.AppRunner(APP)
    await RUNNER.setup()
    site = web.TCPSite(RUNNER, "0.0.0.0", API_PORT)
    await site.start()
    logger.info("[INST-API] Listening on http://0.0.0.0:%d", API_PORT)
    print(f"\n[INSTITUTIONAL API] Running on port {API_PORT}")
    print(f"  Full report:    http://localhost:{API_PORT}/api/institutional/full")
    print(f"  Performance:    http://localhost:{API_PORT}/api/institutional/performance")
    print(f"  Equity:         http://localhost:{API_PORT}/api/institutional/equity")
    print(f"  Regime:         http://localhost:{API_PORT}/api/institutional/regime")
    print(f"  Backtest:       POST http://localhost:{API_PORT}/api/institutional/backtest\n")


async def stop_server():
    global RUNNER
    if RUNNER:
        await RUNNER.cleanup()


if __name__ == "__main__":
    import asyncio
    asyncio.run(start_server())
    try:
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        pass
