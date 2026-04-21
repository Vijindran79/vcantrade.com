"""
Google Colab Heavy Brain for VcaniTrade AI.

What this does:
1. Installs the required packages, including pyngrok.
2. Opens a public ngrok URL to a lightweight aiohttp service running in Colab.
3. Accepts market-data POSTs on /market-data.
4. Runs a simple RSI + volume + SMA momentum check.
5. Sends approved BUY/SELL signals back to your local VcaniTrade listener through
   your laptop's public ngrok URL using an API-key handshake.

Expected local setup:
- Your laptop is running main.py.
- Your laptop exposes http://localhost:17199 via ngrok.
- The public ngrok URL is stored in LOCAL_SIGNAL_URL below.
- main.py / core.signal_dispatcher.py use the same SIGNAL_API_KEY.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from typing import Any, Iterable


def _ensure_package(package: str) -> None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", package])


for requirement in ("aiohttp", "numpy", "pandas", "pyngrok", "requests", "yfinance"):
    _ensure_package(requirement)


import numpy as np
import pandas as pd
import requests
import yfinance as yf
from aiohttp import web
from pyngrok import ngrok


NGROK_AUTHTOKEN = os.getenv("NGROK_AUTHTOKEN", "").strip()
COLAB_LISTEN_PORT = int(os.getenv("COLAB_LISTEN_PORT", "8080"))
LOCAL_SIGNAL_URL = os.getenv("LOCAL_SIGNAL_URL", "https://YOUR-LAPTOP-NGROK-URL.ngrok-free.app/api/signal").strip()
SIGNAL_API_KEY = os.getenv("SIGNAL_API_KEY", "replace-me").strip()
SIGNAL_API_HEADER = os.getenv("SIGNAL_API_HEADER", "X-Signal-Key").strip() or "X-Signal-Key"
EXTERNAL_BRAIN_NAME = os.getenv("EXTERNAL_BRAIN_NAME", "google-colab-heavy-brain").strip() or "google-colab-heavy-brain"
FETCH_PERIOD = os.getenv("COLAB_FETCH_PERIOD", "1d").strip()
FETCH_INTERVAL = os.getenv("COLAB_FETCH_INTERVAL", "1m").strip()
DEFAULT_TICKERS = [
    ticker.strip()
    for ticker in os.getenv("COLAB_WATCHLIST", "BTC-USD,ES=F,NQ=F").split(",")
    if ticker.strip()
]


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50.0)


def normalize_bars(raw_bars: Iterable[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(list(raw_bars))
    if frame.empty:
        raise ValueError("No bars supplied")
    renamed = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    }
    frame = frame.rename(columns=renamed)
    required = {"Close", "Volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Bars missing fields: {sorted(missing)}")
    for column in ("Open", "High", "Low", "Close", "Volume"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["Close", "Volume"])
    if frame.empty:
        raise ValueError("Bars contain no numeric close/volume values")
    return frame.reset_index(drop=True)


def fetch_bars_from_yfinance(ticker: str) -> pd.DataFrame:
    history = yf.download(
        tickers=ticker,
        period=FETCH_PERIOD,
        interval=FETCH_INTERVAL,
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    if history.empty:
        raise ValueError(f"No market data returned for {ticker}")
    if isinstance(history.columns, pd.MultiIndex):
        history.columns = [col[0] for col in history.columns]
    return history.dropna().reset_index(drop=True)


def enrich_indicators(frame: pd.DataFrame) -> dict[str, float]:
    closes = frame["Close"]
    volume = frame["Volume"]
    sma_fast = closes.rolling(20).mean()
    sma_slow = closes.rolling(50).mean()
    vol_sma = volume.rolling(20).mean()
    rsi = compute_rsi(closes, 14)

    latest_close = float(closes.iloc[-1])
    latest_volume = float(volume.iloc[-1])
    latest_fast = float(sma_fast.iloc[-1]) if not pd.isna(sma_fast.iloc[-1]) else latest_close
    latest_slow = float(sma_slow.iloc[-1]) if not pd.isna(sma_slow.iloc[-1]) else latest_close
    latest_rsi = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0
    volume_ratio = latest_volume / max(float(vol_sma.iloc[-1]) if not pd.isna(vol_sma.iloc[-1]) else latest_volume, 1.0)

    return {
        "close": latest_close,
        "rsi": latest_rsi,
        "sma_fast": latest_fast,
        "sma_slow": latest_slow,
        "volume": latest_volume,
        "volume_ratio": float(volume_ratio),
    }


def build_signal(ticker: str, indicators: dict[str, float]) -> dict[str, Any] | None:
    close = indicators["close"]
    rsi = indicators["rsi"]
    sma_fast = indicators["sma_fast"]
    sma_slow = indicators["sma_slow"]
    volume_ratio = indicators["volume_ratio"]

    buy_signal = close > sma_fast > sma_slow and 52 <= rsi <= 68 and volume_ratio >= 1.2
    sell_signal = close < sma_fast < sma_slow and 32 <= rsi <= 48 and volume_ratio >= 1.2

    if not buy_signal and not sell_signal:
        return None

    action = "BUY" if buy_signal else "SELL"
    confidence = min(
        0.95,
        0.55
        + min(abs(sma_fast - sma_slow) / max(abs(close), 1.0), 0.10)
        + min(abs(rsi - 50.0) / 100.0, 0.15)
        + min(max(volume_ratio - 1.0, 0.0) / 4.0, 0.15),
    )
    stop_distance = max(close * 0.0035, 1.0)
    take_distance = stop_distance * 2.0

    stop_loss = close - stop_distance if action == "BUY" else close + stop_distance
    take_profit = close + take_distance if action == "BUY" else close - take_distance

    return {
        "ticker": ticker,
        "action": action,
        "confidence": round(float(confidence), 4),
        "entry_price": round(close, 4),
        "stop_loss": round(stop_loss, 4),
        "take_profit": round(take_profit, 4),
        "reason": (
            f"Colab Heavy Brain {action}: RSI={rsi:.1f}, "
            f"VolRatio={volume_ratio:.2f}, SMA20={sma_fast:.2f}, SMA50={sma_slow:.2f}"
        ),
        "signal_type": "COLAB_HEAVY_BRAIN",
        "source": "google_colab",
        "brain_used": "COLAB_HEAVY_BRAIN",
        "brain_verdict": f"[SIGNAL] {action}",
        "brain_reasoning": "RSI + volume + SMA alignment confirmed in Colab",
        "meta": {
            "rsi": round(rsi, 2),
            "sma_fast": round(sma_fast, 4),
            "sma_slow": round(sma_slow, 4),
            "volume_ratio": round(volume_ratio, 3),
        },
    }


def forward_signal(signal: dict[str, Any]) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        SIGNAL_API_HEADER: SIGNAL_API_KEY,
    }
    payload = dict(signal)
    payload["api_key"] = SIGNAL_API_KEY
    response = requests.post(LOCAL_SIGNAL_URL, json=payload, headers=headers, timeout=20)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return {"status": "accepted", "raw": response.text}


def handshake_local_listener() -> dict[str, Any]:
    headers = {
        SIGNAL_API_HEADER: SIGNAL_API_KEY,
    }
    handshake_url = LOCAL_SIGNAL_URL.rsplit("/api/signal", 1)[0] + "/api/handshake"
    response = requests.get(
        handshake_url,
        headers=headers,
        params={"brain": EXTERNAL_BRAIN_NAME},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


async def handle_health(_: web.Request) -> web.Response:
    return web.json_response(
        {
            "status": "healthy",
            "service": "colab-heavy-brain",
            "listener_url": LOCAL_SIGNAL_URL,
            "tickers": DEFAULT_TICKERS,
        }
    )


async def handle_market_data(request: web.Request) -> web.Response:
    body = await request.json()
    ticker = str(body.get("ticker", "")).strip()
    if not ticker:
        return web.json_response({"status": "error", "message": "ticker is required"}, status=400)

    try:
        if body.get("bars"):
            frame = normalize_bars(body["bars"])
        else:
            frame = fetch_bars_from_yfinance(ticker)

        indicators = enrich_indicators(frame)
        signal = build_signal(ticker, indicators)
        if not signal:
            return web.json_response(
                {
                    "status": "no_signal",
                    "ticker": ticker,
                    "indicators": indicators,
                }
            )

        local_response = forward_signal(signal)
        return web.json_response(
            {
                "status": "forwarded",
                "signal": signal,
                "local_response": local_response,
            }
        )
    except Exception as exc:
        return web.json_response(
            {"status": "error", "ticker": ticker, "message": str(exc)},
            status=500,
        )


async def handle_scan_watchlist(_: web.Request) -> web.Response:
    outcomes: list[dict[str, Any]] = []
    for ticker in DEFAULT_TICKERS:
        try:
            frame = fetch_bars_from_yfinance(ticker)
            indicators = enrich_indicators(frame)
            signal = build_signal(ticker, indicators)
            if signal:
                local_response = forward_signal(signal)
                outcomes.append({"ticker": ticker, "status": "forwarded", "signal": signal, "local_response": local_response})
            else:
                outcomes.append({"ticker": ticker, "status": "no_signal", "indicators": indicators})
        except Exception as exc:
            outcomes.append({"ticker": ticker, "status": "error", "message": str(exc)})
    return web.json_response({"status": "complete", "results": outcomes})


async def main() -> None:
    if not NGROK_AUTHTOKEN:
        raise RuntimeError("Set NGROK_AUTHTOKEN before starting the Colab Heavy Brain.")
    if "YOUR-LAPTOP-NGROK-URL" in LOCAL_SIGNAL_URL or not LOCAL_SIGNAL_URL:
        raise RuntimeError("Set LOCAL_SIGNAL_URL to your laptop ngrok /api/signal endpoint before starting.")
    if not SIGNAL_API_KEY or SIGNAL_API_KEY == "replace-me":
        raise RuntimeError("Set SIGNAL_API_KEY to the same key used by your local VcaniTrade listener.")

    ngrok.set_auth_token(NGROK_AUTHTOKEN)
    tunnel = ngrok.connect(addr=COLAB_LISTEN_PORT, bind_tls=True)
    print("=" * 70)
    print("COLAB HEAVY BRAIN ONLINE")
    print("=" * 70)
    print(f"Public market-data URL: {tunnel.public_url}/market-data")
    print(f"Health URL:             {tunnel.public_url}/health")
    print(f"Manual watchlist scan:  {tunnel.public_url}/scan-watchlist")
    print(f"Local signal target:    {LOCAL_SIGNAL_URL}")
    print(f"Signal auth header:     {SIGNAL_API_HEADER}")
    print("=" * 70)

    handshake_response = handshake_local_listener()
    print(f"Handshake:              {json.dumps(handshake_response)}")
    print("=" * 70)

    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_post("/market-data", handle_market_data)
    app.router.add_post("/scan-watchlist", handle_scan_watchlist)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", COLAB_LISTEN_PORT)
    await site.start()

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
        ngrok.disconnect(tunnel.public_url)
        ngrok.kill()


if __name__ == "__main__":
    asyncio.run(main())
