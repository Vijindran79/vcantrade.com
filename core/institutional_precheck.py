"""
VcanTrade AI - Institutional Pre-Check (pre-trade confluence)

Runs BEFORE any order is placed. Analyzes the four things the user wants
on the live chart:

  1. VOLUME        - volume vs average, up/down volume split
  2. VOLUME PROFILE- POC + value area (VAH/VAL); is price in discount/premium
  3. ORDER FLOW    - cumulative delta (proxy from per-bar volume) + absorption
  4. LIQUIDITY SWEEP- SMC sweep of recent equal highs/lows (spring / upthrust)

Returns a structured report plus a go/no-go. By default it only HARD-BLOCKS
on clearly adverse reads (a liquidity sweep against the direction, or order
flow strongly opposed) so it sharpens entries instead of killing them.

NOTE: true tick-level order flow (footprint) is not available from the
yfinance/MT5 OHLCV feed, so delta is approximated as sign(close-open)*volume
per bar. It is directionally useful, not exact.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _volume_profile(close: pd.Series, vol: pd.Series, bins: int = 24):
    """Return (poc, val, vah, volsum) or None if not computable."""
    try:
        lo = float(close.min())
        hi = float(close.max())
        if hi <= lo:
            return None
        edges = np.linspace(lo, hi, bins + 1)
        idx = np.digitize(close.values, edges)
        volsum = np.zeros(bins, dtype=float)
        for b, v in zip(idx, vol.values):
            if 1 <= b <= bins:
                volsum[b - 1] += float(v)
        if volsum.sum() <= 0:
            return None
        poc_i = int(np.argmax(volsum))
        poc = float((edges[poc_i] + edges[poc_i + 1]) / 2.0)
        total = float(volsum.sum())
        cum = volsum[poc_i]
        lo_i = hi_i = poc_i
        while cum < 0.70 * total and (lo_i > 0 or hi_i < bins - 1):
            left = volsum[lo_i - 1] if lo_i > 0 else -1.0
            right = volsum[hi_i + 1] if hi_i < bins - 1 else -1.0
            if left >= right and left >= 0:
                lo_i -= 1
                cum += volsum[lo_i]
            elif right >= 0:
                hi_i += 1
                cum += volsum[hi_i]
            else:
                break
        val = float(edges[lo_i])
        vah = float(edges[hi_i + 1])
        return poc, val, vah, volsum
    except Exception as e:
        logger.debug("[PRE-CHECK] volume_profile error: %s", e)
        return None


def run_institutional_precheck(ticker: str, df: pd.DataFrame, action: str, cfg: Optional[Dict] = None) -> Dict:
    """Analyze volume, volume profile, order flow, liquidity sweep for a ticker.

    Returns dict with keys: summary, verdict, block (bool), reason, and the
    individual sub-reads (volume, profile, flow, sweep).
    """
    cfg = cfg or {}
    action = str(action or "").upper()
    out: Dict = {
        "ticker": ticker,
        "action": action,
        "block": False,
        "reason": "",
        "verdict": "OK",
        "summary": "",
        "volume": {},
        "profile": {},
        "flow": {},
        "sweep": {},
    }
    if df is None or len(df) < 20:
        out["verdict"] = "INSUFFICIENT DATA"
        out["summary"] = f"[PRE-CHECK] {ticker}: not enough bars to analyze"
        return out

    try:
        close = df["Close"] if "Close" in df else df.get("close")
        open_ = df["Open"] if "Open" in df else df.get("open")
        high = df["High"] if "High" in df else df.get("high")
        low = df["Low"] if "Low" in df else df.get("low")
        vol = df.get("Volume", pd.Series([0.0] * len(df)))
        has_vol = float(vol.sum()) > 0
        price = float(close.iloc[-1])
        n = len(close)

        # ---------- 1. VOLUME ----------
        vol_parts: Dict = {}
        if has_vol:
            avg_vol = float(vol.tail(50).mean()) if n >= 50 else float(vol.mean())
            last_vol = float(vol.iloc[-1])
            vol_ratio = (last_vol / avg_vol) if avg_vol > 0 else 1.0
            last_n = min(20, n)
            up_vol = float(vol[close > open_].tail(last_n).sum())
            dn_vol = float(vol[close < open_].tail(last_n).sum())
            vol_parts = {
                "avg_vol": round(avg_vol, 1),
                "last_vol": round(last_vol, 1),
                "vol_ratio": round(vol_ratio, 2),
                "up_vol": round(up_vol, 1),
                "dn_vol": round(dn_vol, 1),
                "buying_favor": (up_vol > dn_vol),
            }
        out["volume"] = vol_parts

        # ---------- 2. VOLUME PROFILE ----------
        profile: Dict = {}
        if has_vol:
            vp = _volume_profile(close.tail(200), vol.tail(200))
            if vp:
                poc, val, vah, _ = vp
                if price > vah:
                    pos = "PREMIUM(above value)"
                elif price < val:
                    pos = "DISCOUNT(below value)"
                else:
                    pos = "inside value area"
                profile = {
                    "poc": round(poc, 2),
                    "val": round(val, 2),
                    "vah": round(vah, 2),
                    "price_position": pos,
                }
        out["profile"] = profile

        # ---------- 3. ORDER FLOW (cumulative delta proxy) ----------
        flow: Dict = {}
        if has_vol:
            delta = np.sign((close - open_).values) * vol.values
            last_n = min(15, n)
            cum_delta = float(np.sum(delta[-last_n:]))
            total_vol = float(np.sum(vol.tail(last_n).values)) or 1.0
            delta_pct = cum_delta / total_vol  # -1..1
            last_delta = float(delta[-1])
            # absorption: big volume, tiny range, near-zero delta
            rng = float((high.iloc[-1] - low.iloc[-1])) or 1.0
            body = float(abs(close.iloc[-1] - open_.iloc[-1]))
            absorption = bool(has_vol and vol.iloc[-1] > avg_vol * 1.5 and (body / rng) < 0.3 and abs(last_delta) < 0.2 * vol.iloc[-1])
            flow = {
                "cum_delta": round(cum_delta, 1),
                "delta_pct": round(delta_pct, 3),
                "last_delta": round(last_delta, 1),
                "absorption": absorption,
            }
        out["flow"] = flow

        # ---------- 4. LIQUIDITY SWEEP ----------
        sweep: Dict = {"sweep_high": False, "sweep_low": False}
        look = min(60, n - 1)
        if look >= 5:
            prev = df.iloc[-look:-1]
            swing_high = float(prev["High"].max())
            swing_low = float(prev["Low"].min())
            last = df.iloc[-1]
            # price took liquidity above a prior swing high then rejected back below
            sweep["sweep_high"] = bool(last["High"] >= swing_high and last["Close"] < swing_high)
            # price took liquidity below a prior swing low then reclaimed above
            sweep["sweep_low"] = bool(last["Low"] <= swing_low and last["Close"] > swing_low)
        out["sweep"] = sweep

        # ---------- VERDICT / GATE ----------
        block_sweep = bool(cfg.get("block_sweep", True))
        require_flow = bool(cfg.get("require_flow", cfg.get("block_flow", True)))
        flow_min = float(cfg.get("flow_min", 0.10))
        require_discount = bool(cfg.get("require_discount", False))
        reasons = []

        if block_sweep and action == "BUY" and sweep.get("sweep_high"):
            out["block"] = True
            reasons.append("liquidity sweep of highs (don't buy the grab)")
        if block_sweep and action == "SELL" and sweep.get("sweep_low"):
            out["block"] = True
            reasons.append("liquidity sweep of lows (don't sell the grab)")

        # Sharp entries: only trade WITH order flow, not against it.
        if require_flow and flow:
            dp = flow.get("delta_pct", 0.0)
            # BUY: only block on CLEARLY bearish flow (delta well below 0).
            # Neutral/slightly-positive flow should NOT block a BUY that is
            # otherwise confirmed by trend + momentum + liquidity context.
            if action == "BUY" and dp < -flow_min:
                out["block"] = True
                reasons.append(f"order flow not supportive (delta {dp:+.2f} < {-flow_min})")
            # SELL: only block on CLEARLY bullish flow (delta well above 0).
            # Neutral/slightly-positive flow (e.g. +0.02) should NOT block a SELL
            # that is otherwise confirmed by trend + momentum + 5m context.
            if action == "SELL" and dp > flow_min:
                out["block"] = True
                reasons.append(f"order flow not supportive (delta {dp:+.2f} > {flow_min})")

        # Sharp entries: buy in discount, sell in premium (value-area context).
        if require_discount and profile:
            _pos = profile.get("price_position", "")
            if action == "BUY" and str(_pos).startswith("PREMIUM"):
                out["block"] = True
                reasons.append("price in premium - buy in discount")
            if action == "SELL" and str(_pos).startswith("DISCOUNT"):
                out["block"] = True
                reasons.append("price in discount - sell in premium")

        # Build human-readable summary
        vstr = f"volx{vol_parts.get('vol_ratio','?')}" if vol_parts else "vol=n/a"
        pstr = f"POC {profile.get('poc')} {profile.get('price_position','')}" if profile else "profile=n/a"
        fstr = f"delta {flow.get('delta_pct','?')}" if flow else "flow=n/a"
        sstr = ("SWEEP-H " if sweep.get("sweep_high") else "") + ("SWEEP-L " if sweep.get("sweep_low") else "")
        out["summary"] = (
            f"[PRE-CHECK] {ticker} {action} | {vstr} | {pstr} | {fstr} | {sstr}".strip()
        )

        if out["block"]:
            out["verdict"] = "BLOCKED"
            out["reason"] = "; ".join(reasons)
        elif sweep.get("sweep_low") and action == "BUY":
            out["verdict"] = "CONFLUENCE (spring)"
        elif sweep.get("sweep_high") and action == "SELL":
            out["verdict"] = "CONFLUENCE (upthrust)"
        else:
            out["verdict"] = "OK"
        return out
    except Exception as e:
        logger.debug("[PRE-CHECK] analysis error (allowing): %s", e)
        out["verdict"] = "ERROR"
        out["summary"] = f"[PRE-CHECK] {ticker}: analysis error ({e}) - allowed"
        return out
