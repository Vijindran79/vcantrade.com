"""
VcanTrade AI - Profit Guard

Secures profits on a sniper entry the way a professional would:

1. Let the trade run while the move is still flowing (momentum STRONG/BUILDING).
2. Once unrealized profit reaches PROFIT_GUARD_TRIGGER_PCT (default 100% of
   risk = +1R), arm the guard and trail a stop.
3. If price pulls back PROFIT_GUARD_PULLBACK_PCT (default 12.5% = the 10-15%
   band) from its peak, exit the ENTIRE position.

Momentum states returned to the caller:
    STRONG     - new high since entry, stream still flowing
    BUILDING    - in profit but below the trigger, still running
    WEAKENING   - off the peak but not yet at the pullback exit
    PULLBACK    - hit the trailing stop -> caller should exit now

Market awareness:
    - Busy/high-volume sessions widen the pullback tolerance slightly so we
      don't bail on normal noise.
    - 1h/4h support/resistance "room" tightens the trail when the trend is
      running out of space and loosens it when there is room to continue.
"""

from __future__ import annotations

import logging
from typing import Dict

logger = logging.getLogger(__name__)


def evaluate(
    entry: float,
    stop: float,
    action: str,
    price: float,
    peak: float,
    armed: bool,
    trigger_pct: float,
    pullback_pct: float,
    session_widen_pct: float = 0.0,
    htf_room: str = "UNKNOWN",
) -> Dict:
    """
    Pure decision function for the profit guard.

    Args:
        entry: entry price
        stop: initial stop price (used for risk)
        action: "BUY" / "SELL"
        price: current price
        peak: highest (long) / lowest (short) price seen since entry
        armed: whether the guard is already engaged
        trigger_pct: profit (% of risk) at which to arm
        pullback_pct: pullback from peak (% of run-up) that triggers exit
        session_widen_pct: extra tolerance during busy sessions
        htf_room: "ROOM" | "TIGHT" | "UNKNOWN" from higher-timeframe S/R

    Returns dict with keys: exit, reason, peak, armed, momentum, profit_pct,
                            run_up_pct, trail, break_even.
    """
    result: Dict = {
        "exit": False,
        "reason": "",
        "peak": peak,
        "armed": armed,
        "momentum": "UNKNOWN",
        "profit_pct": 0.0,
        "run_up_pct": 0.0,
        "trail": 0.0,
        "break_even": False,
    }

    is_long = str(action).upper() in ("BUY", "LONG")
    if entry <= 0 or price <= 0:
        return result

    risk = abs(entry - stop) if stop and stop > 0 else entry * 0.005
    if risk <= 0:
        risk = entry * 0.005

    if is_long:
        profit = price - entry
        new_peak = max(peak, price) if peak > 0 else price
    else:
        profit = entry - price
        new_peak = min(peak, price) if peak > 0 else price

    run_up = (new_peak - entry) if is_long else (entry - new_peak)
    run_up_pct = (run_up / risk * 100.0) if risk > 0 else 0.0
    profit_pct = (profit / risk * 100.0) if risk > 0 else 0.0

    result["peak"] = new_peak
    result["profit_pct"] = profit_pct
    result["run_up_pct"] = run_up_pct

    if is_long:
        if price >= new_peak:
            result["momentum"] = "STRONG"
        elif run_up_pct < trigger_pct:
            result["momentum"] = "BUILDING"
        else:
            result["momentum"] = "WEAKENING"
    else:
        if price <= new_peak:
            result["momentum"] = "STRONG"
        elif run_up_pct < trigger_pct:
            result["momentum"] = "BUILDING"
        else:
            result["momentum"] = "WEAKENING"

    now_armed = bool(armed) or (run_up_pct >= trigger_pct)
    result["armed"] = now_armed

    if not now_armed:
        return result

    result["break_even"] = True

    eff = pullback_pct + session_widen_pct
    if htf_room == "TIGHT":
        eff = max(5.0, eff * 0.7)
    elif htf_room == "ROOM":
        eff = eff * 1.15

    if is_long:
        trail = new_peak - (eff / 100.0) * run_up
    else:
        trail = new_peak + (eff / 100.0) * run_up
    result["trail"] = trail

    if is_long and price <= trail:
        result["exit"] = True
        result["momentum"] = "PULLBACK"
        result["reason"] = (
            f"PROFIT-GUARD: BUY {entry:.2f} locked +{profit_pct:.0f}% "
            f"(peak +{run_up_pct:.0f}%), {eff:.1f}% pullback -> FULL EXIT @ {price:.2f}"
        )
    elif (not is_long) and price >= trail:
        result["exit"] = True
        result["momentum"] = "PULLBACK"
        result["reason"] = (
            f"PROFIT-GUARD: SELL {entry:.2f} locked +{profit_pct:.0f}% "
            f"(peak +{run_up_pct:.0f}%), {eff:.1f}% pullback -> FULL EXIT @ {price:.2f}"
        )

    return result


def htf_room_from_prices(ticker: str, price: float, scanner, count: int = 60) -> str:
    """
    Judge whether the trend still has room using 1h and 4h swing extremes.

    Returns "TIGHT" when price is within ~0.3% of the nearest higher-timeframe
    resistance (long) / support (short) so the caller tightens the trail,
    "ROOM" when there is space to continue, and "UNKNOWN" if data is missing.

    Best-effort: never raises. Requires a scanner with
    _fetch_market_data(ticker, interval=...).
    """
    try:
        if scanner is None or not hasattr(scanner, "_fetch_market_data"):
            return "UNKNOWN"
        extremes: list[float] = []
        for interval in ("60", "240"):
            try:
                df = scanner._fetch_market_data(ticker, interval=interval)
            except Exception:
                df = None
            if df is None or len(df) < 10:
                continue
            highs = df["high"] if "high" in df else df.get("High")
            lows = df["low"] if "low" in df else df.get("Low")
            if highs is None or lows is None:
                continue
            recent = -min(count, len(df))
            extremes.append(float(highs.iloc[recent:].max()))
            extremes.append(float(lows.iloc[recent:].min()))

        if not extremes or price <= 0:
            return "UNKNOWN"

        nearest = min((abs(x - price) for x in extremes), default=None)
        if nearest is None:
            return "UNKNOWN"
        pct = nearest / price * 100.0
        if pct <= 0.3:
            return "TIGHT"
        return "ROOM"
    except Exception as e:
        logger.debug("[PROFIT-GUARD] htf_room_from_prices error: %s", e)
        return "UNKNOWN"
