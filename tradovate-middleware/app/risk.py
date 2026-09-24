"""Apex Trader Funding risk guards.

All checks run synchronously in-process before any HTTP call to Tradovate.
State is persisted to disk after every mutation so a restart mid-session does
not reset the daily counters.

Guards:
  * duplicate-signal window (idempotency on signal_id)
  * per-order contract cap
  * total open-position cap
  * daily realized-loss ceiling
  * intraday trailing-drawdown circuit breaker (high-water mark of equity)
  * kill-switch file
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, Optional

from .config import Settings
from .logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class RiskDecision:
    allowed: bool
    reason: str = ""
    rule: str = ""


@dataclass
class _State:
    day: str = ""
    realized_pnl_usd: float = 0.0
    equity_hwm_usd: float = 0.0
    open_positions: int = 0
    seen_signals: list = field(default_factory=list)


class RiskGuard:
    def __init__(self, settings: Settings, starting_equity_usd: float):
        self._s = settings
        self._lock = threading.RLock()
        self._starting_equity = float(starting_equity_usd)
        self._equity = float(starting_equity_usd)
        self._hwm = float(starting_equity_usd)
        self._realized_today = 0.0
        self._open_positions = 0
        self._seen: Deque[tuple[str, float]] = deque(maxlen=4096)
        self._day = self._today()
        self._state_path = Path(settings.state_file)
        self._load()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _roll_day_if_needed(self) -> None:
        today = self._today()
        if today != self._day:
            log.info("risk day roll %s -> %s; resetting daily counters", self._day, today)
            self._day = today
            self._realized_today = 0.0
            self._hwm = self._equity
            self._save()

    def _load(self) -> None:
        if not self._state_path.exists():
            self._save()
            return
        try:
            raw = json.loads(self._state_path.read_text())
            if raw.get("day") == self._day:
                self._realized_today = float(raw.get("realized_pnl_usd", 0.0))
                self._hwm = float(raw.get("equity_hwm_usd", self._starting_equity))
                self._open_positions = int(raw.get("open_positions", 0))
                for sid, ts in raw.get("seen_signals", [])[-512:]:
                    self._seen.append((sid, float(ts)))
            else:
                log.info("persisted risk state is from %s; starting fresh day", raw.get("day"))
        except Exception as exc:
            log.warning("could not load risk state (%s); starting fresh", exc)

    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "day": self._day,
                "realized_pnl_usd": self._realized_today,
                "equity_hwm_usd": self._hwm,
                "open_positions": self._open_positions,
                "seen_signals": list(self._seen)[-256:],
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload))
            os.replace(tmp, self._state_path)
        except Exception as exc:
            log.error("failed to persist risk state: %s", exc)

    def kill_switch_engaged(self) -> bool:
        return Path(self._s.kill_switch_file).exists()

    def is_duplicate(self, signal_id: str, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        window = float(self._s.duplicate_signal_window_sec)
        with self._lock:
            while self._seen and (now - self._seen[0][1]) > window:
                self._seen.popleft()
            for sid, ts in self._seen:
                if sid == signal_id and (now - ts) <= window:
                    return True
            self._seen.append((signal_id, now))
            return False

    def check_order(
        self,
        *,
        signal_id: str,
        quantity: int,
        symbol: str,
        action: str,
    ) -> RiskDecision:
        with self._lock:
            self._roll_day_if_needed()

            if self.kill_switch_engaged():
                return RiskDecision(False, "kill switch engaged", "KILL_SWITCH")

            if action == "FLATTEN":
                return RiskDecision(True, "", "")

            if self.is_duplicate(signal_id):
                return RiskDecision(
                    False,
                    f"duplicate signal_id '{signal_id}' within {self._s.duplicate_signal_window_sec}s",
                    "DUPLICATE",
                )

            if quantity <= 0:
                return RiskDecision(False, "quantity must be > 0", "QTY")

            if quantity > self._s.max_contracts_per_order:
                return RiskDecision(
                    False,
                    f"quantity {quantity} exceeds per-order cap {self._s.max_contracts_per_order}",
                    "MAX_CONTRACTS",
                )

            if self._open_positions >= self._s.max_total_open_positions:
                return RiskDecision(
                    False,
                    f"open positions {self._open_positions} >= cap {self._s.max_total_open_positions}",
                    "MAX_POSITIONS",
                )

            if self._s.max_daily_loss_usd > 0 and self._realized_today <= -self._s.max_daily_loss_usd:
                return RiskDecision(
                    False,
                    f"daily loss ${self._realized_today:.2f} breached ceiling ${self._s.max_daily_loss_usd:.2f}",
                    "DAILY_LOSS",
                )

            if self._s.trailing_drawdown_usd > 0:
                dd = self._hwm - self._equity
                if dd >= self._s.trailing_drawdown_usd:
                    return RiskDecision(
                        False,
                        f"trailing drawdown ${dd:.2f} >= limit ${self._s.trailing_drawdown_usd:.2f}",
                        "TRAILING_DD",
                    )

            return RiskDecision(True, "", "")

    def on_position_opened(self) -> None:
        with self._lock:
            self._open_positions += 1
            self._save()

    def on_position_closed(self, realized_pnl_usd: float = 0.0) -> None:
        with self._lock:
            self._open_positions = max(0, self._open_positions - 1)
            self._realized_today += float(realized_pnl_usd)
            self._save()

    def update_equity(self, equity_usd: float) -> None:
        with self._lock:
            self._equity = float(equity_usd)
            if self._equity > self._hwm:
                self._hwm = self._equity
            self._save()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "day": self._day,
                "equity_usd": self._equity,
                "equity_hwm_usd": self._hwm,
                "trailing_drawdown_usd": max(0.0, self._hwm - self._equity),
                "realized_pnl_today_usd": self._realized_today,
                "open_positions": self._open_positions,
                "kill_switch": self.kill_switch_engaged(),
                "max_daily_loss_usd": self._s.max_daily_loss_usd,
                "trailing_drawdown_limit_usd": self._s.trailing_drawdown_usd,
                "max_contracts_per_order": self._s.max_contracts_per_order,
                "max_total_open_positions": self._s.max_total_open_positions,
            }
