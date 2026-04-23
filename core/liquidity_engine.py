"""
VcanTrade AI - Liquidity Engine (Smart Money Concepts)

Detects institutional liquidity zones using Smart Money Concepts:
- Order Blocks (OB): Last opposing candle before a strong move
- Fair Value Gaps (FVG): Imbalanced price areas
- Liquidity Pools: Equal highs/lows where stops cluster
- Breaker Blocks: Invalidated order blocks that flip polarity
- Optimal Trade Entry (OTE): Fibonacci retracement into discount/premium

Provides zone scoring, freshness tracking, and TP/SL recommendations.
"""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class LiquidityZone:
    """Represents a single liquidity zone with metadata."""
    zone_type: str  # "order_block", "fvg", "liquidity_pool", "breaker"
    direction: str  # "bullish" or "bearish"
    top: float
    bottom: float
    start_time: datetime
    end_time: Optional[datetime] = None
    strength: float = 0.5  # 0.0 to 1.0
    touched: bool = False
    invalidated: bool = False
    volume_at_creation: float = 0.0
    source_candle_index: int = 0
    label: str = ""


@dataclass
class ZoneAnalysis:
    """Complete zone analysis for a ticker."""
    ticker: str
    current_price: float
    demand_zones: List[LiquidityZone] = field(default_factory=list)
    supply_zones: List[LiquidityZone] = field(default_factory=list)
    fvg_bullish: List[LiquidityZone] = field(default_factory=list)
    fvg_bearish: List[LiquidityZone] = field(default_factory=list)
    liquidity_pools: List[LiquidityZone] = field(default_factory=list)
    trend_lines: List[Dict] = field(default_factory=list)
    nearest_demand: Optional[LiquidityZone] = None
    nearest_supply: Optional[LiquidityZone] = None
    optimal_entry_long: Optional[float] = None
    optimal_entry_short: Optional[float] = None
    take_profit_long: Optional[float] = None
    take_profit_short: Optional[float] = None


class LiquidityEngine:
    """
    Smart Money Concepts liquidity analyzer.

    Detects zones that institutional traders use for entries and exits.
    """

    def __init__(self):
        self.zone_history: Dict[str, List[LiquidityZone]] = {}
        self.max_history = 100

    def analyze(self, df: pd.DataFrame, ticker: str) -> ZoneAnalysis:
        """Run full liquidity analysis on price data."""
        if df is None or len(df) < 20:
            return ZoneAnalysis(ticker=ticker, current_price=0.0)

        current_price = float(df["Close"].iloc[-1])
        result = ZoneAnalysis(ticker=ticker, current_price=current_price)

        # Detect all zone types
        result.demand_zones = self._detect_order_blocks(df, "bullish")
        result.supply_zones = self._detect_order_blocks(df, "bearish")
        result.fvg_bullish, result.fvg_bearish = self._detect_fvgs(df)
        result.liquidity_pools = self._detect_liquidity_pools(df)

        # Mark touched/invalidated zones
        self._update_zone_states(result, current_price)

        # Find nearest active zones
        result.nearest_demand = self._nearest_active_zone(
            result.demand_zones + result.fvg_bullish, current_price, below_price=True
        )
        result.nearest_supply = self._nearest_active_zone(
            result.supply_zones + result.fvg_bearish, current_price, below_price=False
        )

        # Calculate optimal entries using Fibonacci OTE
        result.optimal_entry_long = self._calculate_ote_long(df, result.nearest_demand)
        result.optimal_entry_short = self._calculate_ote_short(df, result.nearest_supply)

        # Calculate take-profits based on opposing liquidity
        result.take_profit_long = self._calculate_tp_long(result, current_price)
        result.take_profit_short = self._calculate_tp_short(result, current_price)

        # Detect trend lines for breakout analysis
        result.trend_lines = self._detect_trend_lines(df)

        # Log summary
        active_demand = sum(1 for z in result.demand_zones if not z.invalidated)
        active_supply = sum(1 for z in result.supply_zones if not z.invalidated)
        logger.info(
            "[LIQUIDITY] %s | Price: %.2f | Active Demand: %d | Active Supply: %d | FVGs: %d+%d | Pools: %d",
            ticker,
            current_price,
            active_demand,
            active_supply,
            len(result.fvg_bullish),
            len(result.fvg_bearish),
            len(result.liquidity_pools),
        )

        return result

    def _detect_order_blocks(self, df: pd.DataFrame, direction: str) -> List[LiquidityZone]:
        """Detect bullish or bearish order blocks."""
        zones = []
        if len(df) < 10:
            return zones

        opens = df["Open"].values
        highs = df["High"].values
        lows = df["Low"].values
        closes = df["Close"].values
        volumes = df.get("Volume", pd.Series([0] * len(df))).values

        for i in range(2, len(df) - 2):
            # Bullish OB: last bearish candle before a strong bullish impulse
            if direction == "bullish":
                # Check for bearish candle (close < open)
                if closes[i] >= opens[i]:
                    continue
                # Check if next 2 candles show strong bullish move
                move_1 = closes[i + 1] - opens[i + 1]
                move_2 = closes[i + 2] - opens[i + 2]
                if move_1 <= 0 and move_2 <= 0:
                    continue
                if closes[i + 2] <= closes[i]:
                    continue
                # This is a bullish order block
                strength = min(1.0, (volumes[i] / max(np.mean(volumes[max(0, i-5):i+1]), 1)) * 0.5)
                zone = LiquidityZone(
                    zone_type="order_block",
                    direction="bullish",
                    top=float(highs[i]),
                    bottom=float(lows[i]),
                    start_time=df.index[i],
                    strength=strength + 0.3,
                    volume_at_creation=float(volumes[i]),
                    source_candle_index=i,
                    label=f"Bullish OB [{i}]",
                )
                zones.append(zone)

            # Bearish OB: last bullish candle before a strong bearish impulse
            else:
                if closes[i] <= opens[i]:
                    continue
                move_1 = opens[i + 1] - closes[i + 1]
                move_2 = opens[i + 2] - closes[i + 2]
                if move_1 <= 0 and move_2 <= 0:
                    continue
                if closes[i + 2] >= closes[i]:
                    continue
                strength = min(1.0, (volumes[i] / max(np.mean(volumes[max(0, i-5):i+1]), 1)) * 0.5)
                zone = LiquidityZone(
                    zone_type="order_block",
                    direction="bearish",
                    top=float(highs[i]),
                    bottom=float(lows[i]),
                    start_time=df.index[i],
                    strength=strength + 0.3,
                    volume_at_creation=float(volumes[i]),
                    source_candle_index=i,
                    label=f"Bearish OB [{i}]",
                )
                zones.append(zone)

        return zones

    def _detect_fvgs(self, df: pd.DataFrame) -> Tuple[List[LiquidityZone], List[LiquidityZone]]:
        """Detect Fair Value Gaps (imbalanced candles)."""
        bullish_fvgs = []
        bearish_fvgs = []
        if len(df) < 5:
            return bullish_fvgs, bearish_fvgs

        highs = df["High"].values
        lows = df["Low"].values

        for i in range(1, len(df) - 1):
            prev_high = highs[i - 1]
            prev_low = lows[i - 1]
            next_high = highs[i + 1]
            next_low = lows[i + 1]

            # Bullish FVG: current low > previous high (gap up)
            if lows[i] > prev_high:
                zone = LiquidityZone(
                    zone_type="fvg",
                    direction="bullish",
                    top=float(lows[i]),
                    bottom=float(prev_high),
                    start_time=df.index[i],
                    strength=0.6,
                    source_candle_index=i,
                    label=f"Bullish FVG [{i}]",
                )
                bullish_fvgs.append(zone)

            # Bearish FVG: current high < previous low (gap down)
            if highs[i] < prev_low:
                zone = LiquidityZone(
                    zone_type="fvg",
                    direction="bearish",
                    top=float(prev_low),
                    bottom=float(highs[i]),
                    start_time=df.index[i],
                    strength=0.6,
                    source_candle_index=i,
                    label=f"Bearish FVG [{i}]",
                )
                bearish_fvgs.append(zone)

        return bullish_fvgs, bearish_fvgs

    def _detect_liquidity_pools(self, df: pd.DataFrame) -> List[LiquidityZone]:
        """Detect equal highs/lows where liquidity clusters."""
        pools = []
        if len(df) < 20:
            return pools

        lookback = min(50, len(df) - 1)
        recent = df.tail(lookback)
        highs = recent["High"].values
        lows = recent["Low"].values
        tolerance = max(abs(recent["Close"].iloc[-1]) * 0.0015, 0.01)

        # Find equal highs (supply liquidity)
        for i in range(1, len(recent) - 1):
            for j in range(i + 2, min(i + 15, len(recent) - 1)):
                if abs(highs[i] - highs[j]) <= tolerance:
                    pool = LiquidityZone(
                        zone_type="liquidity_pool",
                        direction="bearish",
                        top=float(max(highs[i], highs[j]) + tolerance),
                        bottom=float(min(highs[i], highs[j]) - tolerance),
                        start_time=recent.index[i],
                        strength=0.7,
                        source_candle_index=i,
                        label=f"EqHighs [{i},{j}]",
                    )
                    pools.append(pool)

        # Find equal lows (demand liquidity)
        for i in range(1, len(recent) - 1):
            for j in range(i + 2, min(i + 15, len(recent) - 1)):
                if abs(lows[i] - lows[j]) <= tolerance:
                    pool = LiquidityZone(
                        zone_type="liquidity_pool",
                        direction="bullish",
                        top=float(max(lows[i], lows[j]) + tolerance),
                        bottom=float(min(lows[i], lows[j]) - tolerance),
                        start_time=recent.index[i],
                        strength=0.7,
                        source_candle_index=i,
                        label=f"EqLows [{i},{j}]",
                    )
                    pools.append(pool)

        return pools

    def _update_zone_states(self, result: ZoneAnalysis, current_price: float) -> None:
        """Mark zones as touched or invalidated based on price action."""
        all_zones = (
            result.demand_zones
            + result.supply_zones
            + result.fvg_bullish
            + result.fvg_bearish
            + result.liquidity_pools
        )
        for zone in all_zones:
            if zone.invalidated:
                continue
            if zone.bottom <= current_price <= zone.top:
                zone.touched = True
            # Invalidate if price fully violates the zone
            if zone.direction == "bullish" and current_price < zone.bottom * 0.998:
                zone.invalidated = True
            if zone.direction == "bearish" and current_price > zone.top * 1.002:
                zone.invalidated = True

    def _nearest_active_zone(
        self, zones: List[LiquidityZone], current_price: float, below_price: bool
    ) -> Optional[LiquidityZone]:
        """Find the nearest active zone above or below current price."""
        active = [z for z in zones if not z.invalidated]
        if not active:
            return None

        if below_price:
            candidates = [z for z in active if z.top < current_price]
            if not candidates:
                return None
            return max(candidates, key=lambda z: z.top)
        else:
            candidates = [z for z in active if z.bottom > current_price]
            if not candidates:
                return None
            return min(candidates, key=lambda z: z.bottom)

    def _calculate_ote_long(self, df: pd.DataFrame, demand_zone: Optional[LiquidityZone]) -> Optional[float]:
        """Calculate Optimal Trade Entry for longs (62% Fib retracement into discount)."""
        if demand_zone is None or len(df) < 10:
            return None
        recent_high = float(df["High"].tail(20).max())
        recent_low = demand_zone.bottom
        range_size = recent_high - recent_low
        if range_size <= 0:
            return None
        # OTE is the 62-79% retracement zone into the demand area
        ote = recent_high - (range_size * 0.62)
        return round(max(ote, demand_zone.bottom), 4)

    def _calculate_ote_short(self, df: pd.DataFrame, supply_zone: Optional[LiquidityZone]) -> Optional[float]:
        """Calculate Optimal Trade Entry for shorts (62% Fib retracement into premium)."""
        if supply_zone is None or len(df) < 10:
            return None
        recent_low = float(df["Low"].tail(20).min())
        recent_high = supply_zone.top
        range_size = recent_high - recent_low
        if range_size <= 0:
            return None
        ote = recent_low + (range_size * 0.62)
        return round(min(ote, supply_zone.top), 4)

    def _calculate_tp_long(self, result: ZoneAnalysis, current_price: float) -> Optional[float]:
        """Calculate long take-profit based on nearest opposing liquidity."""
        active_supply = [z for z in result.supply_zones + result.fvg_bearish + result.liquidity_pools
                         if z.direction == "bearish" and not z.invalidated and z.bottom > current_price]
        if not active_supply:
            return None
        nearest = min(active_supply, key=lambda z: z.bottom)
        return round(nearest.bottom, 4)

    def _calculate_tp_short(self, result: ZoneAnalysis, current_price: float) -> Optional[float]:
        """Calculate short take-profit based on nearest opposing liquidity."""
        active_demand = [z for z in result.demand_zones + result.fvg_bullish + result.liquidity_pools
                         if z.direction == "bullish" and not z.invalidated and z.top < current_price]
        if not active_demand:
            return None
        nearest = max(active_demand, key=lambda z: z.top)
        return round(nearest.top, 4)

    def _detect_trend_lines(self, df: pd.DataFrame) -> List[Dict]:
        """Detect slanted trend lines from swing points."""
        lines = []
        if len(df) < 20:
            return lines

        recent = df.tail(60)
        highs = recent["High"].values
        lows = recent["Low"].values
        closes = recent["Close"].values

        # Find swing highs and lows
        swing_highs = []
        swing_lows = []
        for i in range(2, len(recent) - 2):
            if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                swing_highs.append((i, float(highs[i])))
            if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
                swing_lows.append((i, float(lows[i])))

        # Draw descending trendline from last 2 swing highs (resistance)
        if len(swing_highs) >= 2:
            i1, p1 = swing_highs[-2]
            i2, p2 = swing_highs[-1]
            if i2 > i1 and p2 < p1:  # Lower highs = descending
                slope = (p2 - p1) / max(1, i2 - i1)
                current_val = p2 + slope * (len(recent) - 1 - i2)
                lines.append({
                    "type": "resistance",
                    "slope": slope,
                    "start_price": p1,
                    "end_price": p2,
                    "current_projection": round(current_val, 4),
                    "broken": closes[-1] > current_val * 1.001,
                })

        # Draw ascending trendline from last 2 swing lows (support)
        if len(swing_lows) >= 2:
            i1, p1 = swing_lows[-2]
            i2, p2 = swing_lows[-1]
            if i2 > i1 and p2 > p1:  # Higher lows = ascending
                slope = (p2 - p1) / max(1, i2 - i1)
                current_val = p2 + slope * (len(recent) - 1 - i2)
                lines.append({
                    "type": "support",
                    "slope": slope,
                    "start_price": p1,
                    "end_price": p2,
                    "current_projection": round(current_val, 4),
                    "broken": closes[-1] < current_val * 0.999,
                })

        return lines

    def to_dict(self, analysis: ZoneAnalysis) -> Dict:
        """Serialize analysis to dict for signals and UI."""
        def zone_to_dict(z: LiquidityZone) -> Dict:
            return {
                "type": z.zone_type,
                "direction": z.direction,
                "top": z.top,
                "bottom": z.bottom,
                "strength": round(z.strength, 2),
                "touched": z.touched,
                "invalidated": z.invalidated,
                "label": z.label,
            }

        return {
            "ticker": analysis.ticker,
            "current_price": analysis.current_price,
            "demand_zones": [zone_to_dict(z) for z in analysis.demand_zones if not z.invalidated],
            "supply_zones": [zone_to_dict(z) for z in analysis.supply_zones if not z.invalidated],
            "fvg_bullish": [zone_to_dict(z) for z in analysis.fvg_bullish if not z.invalidated],
            "fvg_bearish": [zone_to_dict(z) for z in analysis.fvg_bearish if not z.invalidated],
            "liquidity_pools": [zone_to_dict(z) for z in analysis.liquidity_pools if not z.invalidated],
            "nearest_demand": zone_to_dict(analysis.nearest_demand) if analysis.nearest_demand else None,
            "nearest_supply": zone_to_dict(analysis.nearest_supply) if analysis.nearest_supply else None,
            "optimal_entry_long": analysis.optimal_entry_long,
            "optimal_entry_short": analysis.optimal_entry_short,
            "take_profit_long": analysis.take_profit_long,
            "take_profit_short": analysis.take_profit_short,
            "trend_lines": analysis.trend_lines,
        }
