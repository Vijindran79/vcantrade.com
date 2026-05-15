"""
VcaniTrade AI - Screen Watcher Agent
Continuous chart observation that detects price/candle changes
and streams updates to the brain for instant decisions.

Architecture:
  Screen Watcher (EYES) --> detects changes --> Brain Swarm (BRAIN) --> decides --> Executor (HAND)

The watcher captures the chart every N seconds, compares to the last snapshot,
and only triggers the brain when something meaningful changes:
  - New candle formed
  - Price moved beyond threshold
  - RSI crossed key levels (30/70)
  - Volume spike detected
  - Liquidity zone approached
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable, Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)


class ChangeType(Enum):
    """Types of changes the watcher can detect."""
    NEW_CANDLE = "NEW_CANDLE"
    PRICE_MOVE = "PRICE_MOVE"
    RSI_CROSS = "RSI_CROSS"
    VOLUME_SPIKE = "VOLUME_SPIKE"
    LIQUIDITY_APPROACH = "LIQUIDITY_APPROACH"
    BREAKOUT = "BREAKOUT"
    NO_CHANGE = "NO_CHANGE"


@dataclass
class ChartSnapshot:
    """A single snapshot of the chart state."""
    timestamp: float
    asset: str
    price: float
    rsi: float = 50.0
    volume: float = 0.0
    candle_count: int = 0
    last_candle_o: float = 0.0
    last_candle_h: float = 0.0
    last_candle_l: float = 0.0
    last_candle_c: float = 0.0
    ema_20: float = 0.0
    ema_50: float = 0.0
    liquidity_zone: str = ""
    indicators: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChangeEvent:
    """A detected change that should trigger brain analysis."""
    change_type: ChangeType
    asset: str
    severity: float  # 0.0 = minor, 1.0 = critical
    details: str
    old_value: float = 0.0
    new_value: float = 0.0
    snapshot: Optional[ChartSnapshot] = None


class ScreenWatcherAgent:
    """
    Continuous chart observation agent.

    Captures the chart every `scan_interval` seconds, detects meaningful changes,
    and triggers the brain swarm when action-worthy events occur.

    Usage:
        watcher = ScreenWatcherAgent(
            capture_fn=capture_chart_screenshot,
            indicators_fn=get_current_indicators,
            on_change=brain_analyze,
        )
        await watcher.start()
    """

    def __init__(
        self,
        capture_fn: Callable = None,
        indicators_fn: Callable = None,
        on_change: Callable[[ChangeEvent], Awaitable[None]] = None,
        scan_interval: float = 3.0,
        price_threshold_pct: float = 0.15,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        volume_spike_multiplier: float = 2.0,
        min_change_severity: float = 0.4,
        cooldown_seconds: float = 10.0,
    ):
        self.capture_fn = capture_fn
        self.indicators_fn = indicators_fn
        self.on_change = on_change
        self.scan_interval = scan_interval
        self.price_threshold_pct = price_threshold_pct
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.volume_spike_multiplier = volume_spike_multiplier
        self.min_change_severity = min_change_severity
        self.cooldown_seconds = cooldown_seconds

        self._running = False
        self._last_snapshot: Optional[ChartSnapshot] = None
        self._last_trigger_time: float = 0
        self._snapshot_history: list = []
        self._change_count: int = 0
        self._trigger_count: int = 0

    async def start(self, asset: str = "NQM6"):
        """Start the continuous observation loop."""
        self._running = True
        self._asset = asset
        logger.info(
            "[WATCHER] Started watching %s every %.1fs | Price threshold: %.2f%% | Min severity: %.2f",
            asset, self.scan_interval, self.price_threshold_pct, self.min_change_severity,
        )

        while self._running:
            try:
                await self._observe_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[WATCHER] Observation cycle error: %s", e)
            await asyncio.sleep(self.scan_interval)

        logger.info("[WATCHER] Stopped. Total snapshots: %d | Triggers: %d", self._change_count, self._trigger_count)

    def stop(self):
        """Stop the observation loop."""
        self._running = False
        logger.info("[WATCHER] Stop requested.")

    async def _observe_cycle(self):
        """Single observation cycle: capture -> compare -> decide -> trigger if needed."""
        self._change_count += 1

        # Get current market state
        snapshot = await self._capture_snapshot()
        if snapshot is None:
            return

        # Compare with last snapshot
        if self._last_snapshot is None:
            self._last_snapshot = snapshot
            logger.info("[WATCHER] First snapshot captured: %s @ %.2f", snapshot.asset, snapshot.price)
            return

        # Detect changes
        changes = self._detect_changes(self._last_snapshot, snapshot)

        # Filter to meaningful changes
        significant = [c for c in changes if c.severity >= self.min_change_severity]

        if significant:
            # Respect cooldown
            now = time.monotonic()
            if now - self._last_trigger_time < self.cooldown_seconds:
                logger.debug("[WATCHER] Change detected but in cooldown (%.1fs remaining)",
                           self.cooldown_seconds - (now - self._last_trigger_time))
                self._last_snapshot = snapshot
                return

            # Pick the most significant change
            top_change = max(significant, key=lambda c: c.severity)
            top_change.snapshot = snapshot

            logger.info(
                "[WATCHER] CHANGE DETECTED: %s | Severity: %.2f | %s",
                top_change.change_type.value, top_change.severity, top_change.details,
            )

            # Trigger brain analysis
            if self.on_change:
                self._trigger_count += 1
                self._last_trigger_time = now
                try:
                    await self.on_change(top_change)
                except Exception as e:
                    logger.error("[WATCHER] Brain trigger failed: %s", e)

        self._last_snapshot = snapshot
        self._snapshot_history.append(snapshot)

        # Keep only last 100 snapshots
        if len(self._snapshot_history) > 100:
            self._snapshot_history = self._snapshot_history[-100:]

    async def _capture_snapshot(self) -> Optional[ChartSnapshot]:
        """Capture current chart state from indicators or screenshot."""
        try:
            if self.indicators_fn:
                # Get indicators from the scanner/MT5 feed
                if asyncio.iscoroutinefunction(self.indicators_fn):
                    data = await self.indicators_fn(self._asset)
                else:
                    data = self.indicators_fn(self._asset)

                if data is None:
                    return None

                return ChartSnapshot(
                    timestamp=time.time(),
                    asset=self._asset,
                    price=float(data.get("price", 0)),
                    rsi=float(data.get("RSI", 50)),
                    volume=float(data.get("volume", 0)),
                    candle_count=int(data.get("candle_count", 0)),
                    last_candle_o=float(data.get("last_candle_o", 0)),
                    last_candle_h=float(data.get("last_candle_h", 0)),
                    last_candle_l=float(data.get("last_candle_l", 0)),
                    last_candle_c=float(data.get("last_candle_c", 0)),
                    ema_20=float(data.get("EMA_20", 0)),
                    ema_50=float(data.get("EMA_50", 0)),
                    liquidity_zone=str(data.get("LIQUIDITY_ZONE", "")),
                    indicators=data,
                )

            return None
        except Exception as e:
            logger.error("[WATCHER] Snapshot capture failed: %s", e)
            return None

    def _detect_changes(self, old: ChartSnapshot, new: ChartSnapshot) -> list:
        """Compare two snapshots and return list of detected changes."""
        changes = []

        # 1. Price movement
        if old.price > 0:
            price_change_pct = abs(new.price - old.price) / old.price * 100
            if price_change_pct >= self.price_threshold_pct:
                severity = min(1.0, price_change_pct / (self.price_threshold_pct * 3))
                direction = "UP" if new.price > old.price else "DOWN"
                changes.append(ChangeEvent(
                    change_type=ChangeType.PRICE_MOVE,
                    asset=new.asset,
                    severity=severity,
                    details=f"Price moved {direction} {price_change_pct:.2f}% ({old.price:.2f} -> {new.price:.2f})",
                    old_value=old.price,
                    new_value=new.price,
                ))

        # 2. New candle
        if new.candle_count > old.candle_count and old.candle_count > 0:
            changes.append(ChangeEvent(
                change_type=ChangeType.NEW_CANDLE,
                asset=new.asset,
                severity=0.5,
                details=f"New candle formed (#{new.candle_count})",
                old_value=float(old.candle_count),
                new_value=float(new.candle_count),
            ))

        # 3. RSI cross (oversold/overbought)
        if old.rsi and new.rsi:
            if old.rsi > self.rsi_oversold and new.rsi <= self.rsi_oversold:
                changes.append(ChangeEvent(
                    change_type=ChangeType.RSI_CROSS,
                    asset=new.asset,
                    severity=0.7,
                    details=f"RSI crossed INTO oversold ({old.rsi:.1f} -> {new.rsi:.1f})",
                    old_value=old.rsi,
                    new_value=new.rsi,
                ))
            elif old.rsi < self.rsi_overbought and new.rsi >= self.rsi_overbought:
                changes.append(ChangeEvent(
                    change_type=ChangeType.RSI_CROSS,
                    asset=new.asset,
                    severity=0.7,
                    details=f"RSI crossed INTO overbought ({old.rsi:.1f} -> {new.rsi:.1f})",
                    old_value=old.rsi,
                    new_value=new.rsi,
                ))

        # 4. Volume spike
        if old.volume > 0 and new.volume > 0:
            if new.volume >= old.volume * self.volume_spike_multiplier:
                severity = min(1.0, (new.volume / old.volume) / (self.volume_spike_multiplier * 2))
                changes.append(ChangeEvent(
                    change_type=ChangeType.VOLUME_SPIKE,
                    asset=new.asset,
                    severity=severity,
                    details=f"Volume spike: {new.volume:.0f} vs avg {old.volume:.0f} ({new.volume/old.volume:.1f}x)",
                    old_value=old.volume,
                    new_value=new.volume,
                ))

        # 5. Breakout detection (price broke above/below recent range)
        if old.last_candle_h > 0 and old.last_candle_l > 0:
            range_size = old.last_candle_h - old.last_candle_l
            if range_size > 0:
                if new.price > old.last_candle_h + range_size * 0.5:
                    changes.append(ChangeEvent(
                        change_type=ChangeType.BREAKOUT,
                        asset=new.asset,
                        severity=0.8,
                        details=f"BREAKOUT UP above {old.last_candle_h:.2f}",
                        old_value=old.last_candle_h,
                        new_value=new.price,
                    ))
                elif new.price < old.last_candle_l - range_size * 0.5:
                    changes.append(ChangeEvent(
                        change_type=ChangeType.BREAKOUT,
                        asset=new.asset,
                        severity=0.8,
                        details=f"BREAKOUT DOWN below {old.last_candle_l:.2f}",
                        old_value=old.last_candle_l,
                        new_value=new.price,
                    ))

        return changes

    def get_stats(self) -> dict:
        """Return watcher statistics."""
        return {
            "running": self._running,
            "total_snapshots": self._change_count,
            "total_triggers": self._trigger_count,
            "last_price": self._last_snapshot.price if self._last_snapshot else 0,
            "last_rsi": self._last_snapshot.rsi if self._last_snapshot else 0,
            "history_size": len(self._snapshot_history),
        }
