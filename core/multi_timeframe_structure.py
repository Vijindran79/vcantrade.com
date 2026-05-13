"""Higher-timeframe market structure filter for scanner trade candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd


@dataclass
class StructureZone:
    timeframe: str
    kind: str
    level: float
    low: float
    high: float
    strength: float

    def as_dict(self) -> dict:
        return {
            "timeframe": self.timeframe,
            "kind": self.kind,
            "level": round(self.level, 6),
            "low": round(self.low, 6),
            "high": round(self.high, 6),
            "strength": round(self.strength, 3),
        }


@dataclass
class StructureVerdict:
    allowed: bool
    bias: str
    reason: str
    resistance_zones: List[StructureZone]
    support_zones: List[StructureZone]
    timeframe_biases: Dict[str, str]

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "bias": self.bias,
            "reason": self.reason,
            "resistance_zones": [zone.as_dict() for zone in self.resistance_zones],
            "support_zones": [zone.as_dict() for zone in self.support_zones],
            "timeframe_biases": dict(self.timeframe_biases),
        }


class MultiTimeframeStructureAnalyzer:
    """Classify 15m/1h/4h trend and block trades into major structure."""

    def __init__(self, zone_atr_multiplier: float = 0.65, proximity_pct: float = 0.0025):
        self.zone_atr_multiplier = max(0.1, float(zone_atr_multiplier))
        self.proximity_pct = max(0.0005, float(proximity_pct))

    def evaluate(
        self,
        action: str,
        current_price: float,
        frames: Dict[str, pd.DataFrame],
    ) -> StructureVerdict:
        action = str(action or "").upper()
        current_price = float(current_price or 0.0)
        timeframe_biases: Dict[str, str] = {}
        resistance_zones: List[StructureZone] = []
        support_zones: List[StructureZone] = []

        for timeframe in ("15m", "1h", "4h"):
            prepared = self._prepare_frame(frames.get(timeframe))
            if prepared is None:
                timeframe_biases[timeframe] = "UNKNOWN"
                continue

            timeframe_biases[timeframe] = self._trend_bias(prepared)
            if timeframe in {"1h", "4h"}:
                supports, resistances = self._structure_zones(prepared, timeframe)
                support_zones.extend(supports)
                resistance_zones.extend(resistances)

        dominant_bias = self._dominant_bias(timeframe_biases)
        if action not in {"BUY", "SELL"} or current_price <= 0:
            return StructureVerdict(
                False,
                dominant_bias,
                "Invalid action or current price for structure validation.",
                resistance_zones,
                support_zones,
                timeframe_biases,
            )

        if action == "BUY":
            if dominant_bias == "BEARISH":
                return StructureVerdict(
                    False,
                    dominant_bias,
                    "BUY rejected: dominant 1h/4h trend is bearish.",
                    resistance_zones,
                    support_zones,
                    timeframe_biases,
                )
            blocking = self._nearest_zone(current_price, resistance_zones)
            if blocking is not None:
                return StructureVerdict(
                    False,
                    dominant_bias,
                    f"BUY rejected: price is inside/under major {blocking.timeframe} resistance.",
                    resistance_zones,
                    support_zones,
                    timeframe_biases,
                )

        if action == "SELL":
            if dominant_bias == "BULLISH":
                return StructureVerdict(
                    False,
                    dominant_bias,
                    "SELL rejected: dominant 1h/4h trend is bullish.",
                    resistance_zones,
                    support_zones,
                    timeframe_biases,
                )
            blocking = self._nearest_zone(current_price, support_zones)
            if blocking is not None:
                return StructureVerdict(
                    False,
                    dominant_bias,
                    f"SELL rejected: price is inside/over major {blocking.timeframe} support.",
                    resistance_zones,
                    support_zones,
                    timeframe_biases,
                )

        return StructureVerdict(
            True,
            dominant_bias,
            f"{action} accepted by higher-timeframe structure.",
            resistance_zones,
            support_zones,
            timeframe_biases,
        )

    def _prepare_frame(self, df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
        if df is None or df.empty or len(df) < 60:
            return None
        required = {"Open", "High", "Low", "Close"}
        if not required.issubset(df.columns):
            return None
        out = df.copy().dropna(subset=["Open", "High", "Low", "Close"])
        if len(out) < 60:
            return None
        out["EMA20"] = out["Close"].ewm(span=20, adjust=False).mean()
        out["EMA50"] = out["Close"].ewm(span=50, adjust=False).mean()
        out["EMA200"] = out["Close"].ewm(span=200, adjust=False).mean()
        out["ATR_PROXY"] = self._atr_proxy(out)
        return out

    def _trend_bias(self, df: pd.DataFrame) -> str:
        last = df.iloc[-1]
        close = float(last["Close"])
        ema20 = float(last["EMA20"])
        ema50 = float(last["EMA50"])
        ema200 = float(last["EMA200"])
        if close > ema20 > ema50 > ema200:
            return "BULLISH"
        if close < ema20 < ema50 < ema200:
            return "BEARISH"
        if close > ema50 and ema20 > ema50:
            return "LEAN_BULLISH"
        if close < ema50 and ema20 < ema50:
            return "LEAN_BEARISH"
        return "MIXED"

    def _dominant_bias(self, timeframe_biases: Dict[str, str]) -> str:
        weighted = {"BULLISH": 0, "BEARISH": 0}
        weights = {"1h": 2, "4h": 3, "15m": 1}
        for timeframe, bias in timeframe_biases.items():
            weight = weights.get(timeframe, 1)
            if bias == "BULLISH":
                weighted["BULLISH"] += weight
            elif bias == "BEARISH":
                weighted["BEARISH"] += weight
            elif bias == "LEAN_BULLISH":
                weighted["BULLISH"] += max(1, weight - 1)
            elif bias == "LEAN_BEARISH":
                weighted["BEARISH"] += max(1, weight - 1)
        if weighted["BULLISH"] >= 4 and weighted["BULLISH"] > weighted["BEARISH"]:
            return "BULLISH"
        if weighted["BEARISH"] >= 4 and weighted["BEARISH"] > weighted["BULLISH"]:
            return "BEARISH"
        return "MIXED"

    def _structure_zones(self, df: pd.DataFrame, timeframe: str) -> Tuple[List[StructureZone], List[StructureZone]]:
        lookback = df.tail(160)
        highs = lookback["High"].tolist()
        lows = lookback["Low"].tolist()
        atr = float(lookback["ATR_PROXY"].dropna().tail(20).median() or 0.0)
        last_close = float(lookback["Close"].iloc[-1])
        width = max(atr * self.zone_atr_multiplier, abs(last_close) * self.proximity_pct)

        supports: List[StructureZone] = []
        resistances: List[StructureZone] = []
        for idx in range(2, len(lookback) - 2):
            high = float(highs[idx])
            low = float(lows[idx])
            if high >= max(highs[idx - 2:idx] + highs[idx + 1:idx + 3]):
                resistances.append(self._zone(timeframe, "resistance", high, width, len(lookback) - idx))
            if low <= min(lows[idx - 2:idx] + lows[idx + 1:idx + 3]):
                supports.append(self._zone(timeframe, "support", low, width, len(lookback) - idx))

        supports = self._merge_zones(supports, width)
        resistances = self._merge_zones(resistances, width)
        return supports[-8:], resistances[-8:]

    def _zone(self, timeframe: str, kind: str, level: float, width: float, age: int) -> StructureZone:
        strength = 1.0 if timeframe == "4h" else 0.75
        strength += max(0.0, 0.25 - (age * 0.002))
        return StructureZone(timeframe, kind, level, level - width, level + width, strength)

    def _merge_zones(self, zones: List[StructureZone], width: float) -> List[StructureZone]:
        merged: List[StructureZone] = []
        for zone in sorted(zones, key=lambda z: z.level):
            if merged and abs(zone.level - merged[-1].level) <= width:
                prev = merged[-1]
                level = (prev.level + zone.level) / 2.0
                merged[-1] = StructureZone(
                    prev.timeframe if prev.strength >= zone.strength else zone.timeframe,
                    zone.kind,
                    level,
                    min(prev.low, zone.low),
                    max(prev.high, zone.high),
                    max(prev.strength, zone.strength) + 0.1,
                )
            else:
                merged.append(zone)
        return merged

    def _nearest_zone(self, price: float, zones: List[StructureZone]) -> Optional[StructureZone]:
        for zone in sorted(zones, key=lambda z: abs(price - z.level)):
            buffer = max(abs(zone.level) * self.proximity_pct, zone.high - zone.low)
            if zone.low - buffer <= price <= zone.high + buffer:
                return zone
        return None

    def _atr_proxy(self, df: pd.DataFrame) -> pd.Series:
        high_low = df["High"] - df["Low"]
        high_close = (df["High"] - df["Close"].shift()).abs()
        low_close = (df["Low"] - df["Close"].shift()).abs()
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return true_range.rolling(14, min_periods=1).mean()
