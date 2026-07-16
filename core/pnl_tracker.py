"""
VcaniTrade AI — Realized P&L Tracker + Post-Entry Reversal Detector + Adaptive Risk Manager

Solves three critical problems:
1. Tracks every realized P&L from closed trades (persistent across restarts)
2. Detects immediate post-entry reversals and cuts losses early
3. Adapts trade entry rules based on cumulative loss history
"""

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 1. REALIZED P&L TRACKER — persistent, survives restarts
# ═══════════════════════════════════════════════════════════════════


@dataclass
class TradeSnapshot:
    """One closed trade's realized P&L record."""
    trade_id: str
    asset: str
    side: str  # BUY / SELL
    entry_price: float
    exit_price: float
    pnl: float
    hold_seconds: float
    closed_at: str
    reason: str = ""


class RealizedPnLTracker:
    """Tracks cumulative realized P&L across all trades, persisted in SQLite.

    Unlike SafetyState.daily_pnl (resets every restart), this survives
    reboots and gives you the full loss/win history.
    """

    def __init__(self, db_path: str = "vcanitrade_pnl.db"):
        self.db_path = db_path
        self._init_db()

        # In-memory running totals (loaded from DB on startup)
        self.cumulative_pnl: float = 0.0
        self.total_trades: int = 0
        self.total_wins: int = 0
        self.total_losses: int = 0
        self.consecutive_losses: int = 0
        self.max_consecutive_losses: int = 0
        self.largest_win: float = 0.0
        self.largest_loss: float = 0.0
        self.recent_trades: List[TradeSnapshot] = []  # last 50

        self._load_totals()

    # ── DB setup ──────────────────────────────────────────────────
    def _init_db(self):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS realized_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id TEXT,
                    asset TEXT,
                    side TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    pnl REAL,
                    hold_seconds REAL,
                    closed_at TEXT,
                    reason TEXT,
                    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("[PnL] DB init failed: %s", e)

    def _load_totals(self):
        """Load cumulative stats from DB on startup."""
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()

            cur.execute("SELECT pnl FROM realized_trades ORDER BY id")
            rows = cur.fetchall()
            self.total_trades = len(rows)

            streak = 0
            max_streak = 0
            for (pnl,) in rows:
                self.cumulative_pnl += pnl
                if pnl >= 0:
                    self.total_wins += 1
                    streak = 0
                else:
                    self.total_losses += 1
                    streak += 1
                    max_streak = max(max_streak, streak)
                self.largest_win = max(self.largest_win, pnl)
                self.largest_loss = min(self.largest_loss, pnl)

            self.consecutive_losses = streak
            self.max_consecutive_losses = max_streak

            # Load last 50 for analysis
            cur.execute("""
                SELECT trade_id, asset, side, entry_price, exit_price,
                       pnl, hold_seconds, closed_at, reason
                FROM realized_trades ORDER BY id DESC LIMIT 50
            """)
            self.recent_trades = []
            for row in cur.fetchall():
                self.recent_trades.append(TradeSnapshot(
                    trade_id=row[0], asset=row[1], side=row[2],
                    entry_price=row[3], exit_price=row[4], pnl=row[5],
                    hold_seconds=row[6], closed_at=row[7], reason=row[8] or "",
                ))
            self.recent_trades.reverse()  # oldest first

            conn.close()
            logger.info(
                "[PnL] Loaded: %d trades | cumulative=%.2f | wins=%d losses=%d | streak=%d",
                self.total_trades, self.cumulative_pnl,
                self.total_wins, self.total_losses, self.consecutive_losses,
            )
        except Exception as e:
            logger.error("[PnL] Load failed: %s", e)

    # ── Record a closed trade ─────────────────────────────────────
    def record_close(
        self,
        trade_id: str,
        asset: str,
        side: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
        hold_seconds: float = 0.0,
        reason: str = "",
    ):
        """Record a realized P&L from a closed trade. Persists to DB immediately."""
        closed_at = datetime.now(timezone.utc).isoformat()

        # Update running totals
        self.cumulative_pnl += pnl
        self.total_trades += 1
        if pnl >= 0:
            self.total_wins += 1
            self.consecutive_losses = 0
        else:
            self.total_losses += 1
            self.consecutive_losses += 1
            self.max_consecutive_losses = max(
                self.max_consecutive_losses, self.consecutive_losses
            )
        self.largest_win = max(self.largest_win, pnl)
        self.largest_loss = min(self.largest_loss, pnl)

        snap = TradeSnapshot(
            trade_id=trade_id, asset=asset, side=side,
            entry_price=entry_price, exit_price=exit_price,
            pnl=pnl, hold_seconds=hold_seconds,
            closed_at=closed_at, reason=reason,
        )
        self.recent_trades.append(snap)
        if len(self.recent_trades) > 50:
            self.recent_trades = self.recent_trades[-50:]

        # Persist to DB
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT INTO realized_trades
                    (trade_id, asset, side, entry_price, exit_price, pnl, hold_seconds, closed_at, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (trade_id, asset, side, entry_price, exit_price, pnl, hold_seconds, closed_at, reason))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("[PnL] DB write failed: %s", e)

        emoji = "+" if pnl >= 0 else ""
        logger.info(
            "[PnL] REALIZED: %s %s %s%.2f | cumulative=%.2f | streak=%d losses | %d/%d W/L",
            side, asset, emoji, pnl, self.cumulative_pnl,
            self.consecutive_losses, self.total_wins, self.total_losses,
        )

    # ── Query helpers ─────────────────────────────────────────────
    def get_win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.total_wins / self.total_trades

    def get_avg_win(self) -> float:
        wins = [t.pnl for t in self.recent_trades if t.pnl >= 0]
        return sum(wins) / len(wins) if wins else 0.0

    def get_avg_loss(self) -> float:
        losses = [t.pnl for t in self.recent_trades if t.pnl < 0]
        return sum(losses) / len(losses) if losses else 0.0

    def get_profit_factor(self) -> float:
        gross_win = sum(t.pnl for t in self.recent_trades if t.pnl >= 0)
        gross_loss = abs(sum(t.pnl for t in self.recent_trades if t.pnl < 0))
        if gross_loss == 0:
            return float("inf") if gross_win > 0 else 0.0
        return gross_win / gross_loss

    def get_summary(self) -> dict:
        return {
            "cumulative_pnl": round(self.cumulative_pnl, 2),
            "total_trades": self.total_trades,
            "wins": self.total_wins,
            "losses": self.total_losses,
            "win_rate": round(self.get_win_rate() * 100, 1),
            "consecutive_losses": self.consecutive_losses,
            "max_consecutive_losses": self.max_consecutive_losses,
            "largest_win": round(self.largest_win, 2),
            "largest_loss": round(self.largest_loss, 2),
            "avg_win": round(self.get_avg_win(), 2),
            "avg_loss": round(self.get_avg_loss(), 2),
            "profit_factor": round(self.get_profit_factor(), 2),
        }

    def reset_daily_streak(self):
        """Optionally called at session start — does NOT clear history."""
        # The streak counter persists across sessions by design.
        # Call this only if you want to reset the consecutive loss counter
        # at the start of a new trading day.
        pass


# ═══════════════════════════════════════════════════════════════════
# 2. POST-ENTRY REVERSAL DETECTOR
# ═══════════════════════════════════════════════════════════════════


@dataclass
class TrackedEntry:
    """An open entry being monitored for immediate reversal."""
    asset: str
    side: str  # BUY / SELL
    entry_price: float
    entry_time: float  # time.time()
    stop_loss: float
    worst_price: float = 0.0  # tracks the most adverse price seen
    checked_count: int = 0


class PostEntryReversalDetector:
    """Detects when the market immediately reverses after the bot enters a trade.

    If price moves against us by more than `reversal_pct` within
    `window_seconds` of entry, triggers an early exit.
    """

    def __init__(
        self,
        reversal_pct: float = 0.15,   # 0.15% adverse move = reversal
        window_seconds: int = 120,     # within 2 minutes of entry
    ):
        self.reversal_pct = reversal_pct
        self.window_seconds = window_seconds
        self._tracked: Dict[str, TrackedEntry] = {}  # asset -> TrackedEntry

    def register_entry(self, asset: str, side: str, entry_price: float, stop_loss: float):
        """Call this immediately after a trade is executed."""
        self._tracked[asset] = TrackedEntry(
            asset=asset,
            side=side,
            entry_price=entry_price,
            entry_time=time.time(),
            stop_loss=stop_loss,
            worst_price=entry_price,
        )
        logger.info(
            "[REVERSAL] Monitoring %s %s @ %.2f for %ds reversal window",
            side, asset, entry_price, self.window_seconds,
        )

    def clear(self, asset: str):
        """Call when a position is closed (by any reason)."""
        self._tracked.pop(asset, None)

    def check(self, asset: str, current_price: float) -> Tuple[bool, str]:
        """Check if the current price indicates an immediate reversal.

        Returns (should_exit, reason).
        """
        entry = self._tracked.get(asset)
        if entry is None:
            return False, ""

        entry.checked_count += 1
        elapsed = time.time() - entry.entry_time

        # Update worst price
        if entry.side == "BUY":
            entry.worst_price = min(entry.worst_price, current_price)
        else:
            entry.worst_price = max(entry.worst_price, current_price)

        # Only monitor within the window
        if elapsed > self.window_seconds:
            # Window expired — trade survived the danger zone
            self._tracked.pop(asset, None)
            logger.info("[REVERSAL] %s survived %ds window — no longer monitoring", asset, self.window_seconds)
            return False, ""

        # Calculate adverse move
        if entry.side == "BUY":
            adverse_pct = ((entry.entry_price - current_price) / entry.entry_price) * 100
        else:
            adverse_pct = ((current_price - entry.entry_price) / entry.entry_price) * 100

        # Check if stop loss was hit (hard exit, not our job — trade engine handles this)
        if entry.side == "BUY" and current_price <= entry.stop_loss:
            self._tracked.pop(asset, None)
            return False, ""
        if entry.side == "SELL" and current_price >= entry.stop_loss:
            self._tracked.pop(asset, None)
            return False, ""

        # Reversal detection
        if adverse_pct >= self.reversal_pct:
            reason = (
                f"POST-ENTRY REVERSAL: {entry.side} {asset} @ {entry.entry_price:.2f} "
                f"reversed {adverse_pct:.2f}% in {elapsed:.0f}s "
                f"(current={current_price:.2f}, worst={entry.worst_price:.2f})"
            )
            logger.warning("[REVERSAL] %s", reason)
            self._tracked.pop(asset, None)
            return True, reason

        return False, ""

    def get_tracked(self) -> Dict[str, TrackedEntry]:
        return dict(self._tracked)


# ═══════════════════════════════════════════════════════════════════
# 3. ADAPTIVE RISK MANAGER — adjusts rules based on loss history
# ═══════════════════════════════════════════════════════════════════


@dataclass
class AdaptiveState:
    """Current adaptive risk state — computed from P&L history."""
    mode: str = "NORMAL"  # NORMAL / CAUTIOUS / DEFENSIVE / LOCKED
    confidence_floor: float = 0.85  # minimum confidence to trade
    max_consecutive_losses_before_pause: int = 4
    cumulative_loss_pause_threshold: float = -500.0  # dollar amount
    position_size_mult: float = 1.0  # 1.0 = normal, 0.5 = half size
    cooldown_seconds: int = 0  # extra cooldown between trades
    reason: str = ""


class AdaptiveRiskManager:
    """Adjusts trade entry rules based on cumulative realized losses.

    Modes:
      NORMAL    — default, no restrictions
      CAUTIOUS  — 2-3 consecutive losses OR small cumulative loss
      DEFENSIVE — 4+ consecutive losses OR large cumulative loss
      LOCKED    — extreme loss, stop trading entirely
    """

    def __init__(self, pnl_tracker: RealizedPnLTracker):
        self.pnl = pnl_tracker
        self.state = AdaptiveState()

    def evaluate(self) -> AdaptiveState:
        """Re-evaluate adaptive state based on current P&L history.
        Call this BEFORE every trade entry decision."""
        consecutive = self.pnl.consecutive_losses
        cumulative = self.pnl.cumulative_pnl
        win_rate = self.pnl.get_win_rate()

        # ── LOCKED: extreme loss ──────────────────────────────────
        if cumulative <= self.state.cumulative_loss_pause_threshold:
            self.state.mode = "LOCKED"
            self.state.confidence_floor = 1.0  # impossible to reach = no trades
            self.state.position_size_mult = 0.0
            self.state.cooldown_seconds = 0
            self.state.reason = (
                f"LOCKED: cumulative loss ${cumulative:.2f} exceeds "
                f"threshold ${self.state.cumulative_loss_pause_threshold:.2f}"
            )
            return self.state

        # ── DEFENSIVE: heavy losing streak ────────────────────────
        if consecutive >= self.state.max_consecutive_losses_before_pause:
            self.state.mode = "DEFENSIVE"
            self.state.confidence_floor = 0.95
            self.state.position_size_mult = 0.25
            self.state.cooldown_seconds = 300  # 5 min between trades
            self.state.reason = (
                f"DEFENSIVE: {consecutive} consecutive losses — "
                f"raising floor to 95%, size to 25%, 5min cooldown"
            )
            return self.state

        # ── CAUTIOUS: moderate losing streak ──────────────────────
        if consecutive >= 2 or (cumulative < -100 and win_rate < 0.40):
            self.state.mode = "CAUTIOUS"
            self.state.confidence_floor = 0.90
            self.state.position_size_mult = 0.50
            self.state.cooldown_seconds = 120  # 2 min between trades
            self.state.reason = (
                f"CAUTIOUS: {consecutive} streak, cumulative=${cumulative:.2f}, "
                f"winrate={win_rate*100:.0f}% — floor 90%, size 50%"
            )
            return self.state

        # ── NORMAL ────────────────────────────────────────────────
        self.state.mode = "NORMAL"
        self.state.confidence_floor = float(
            getattr(config, "MIN_CONFIDENCE_THRESHOLD", 0.85)
        )
        self.state.position_size_mult = 1.0
        self.state.cooldown_seconds = 0
        self.state.reason = "NORMAL: no restrictions"
        return self.state

    def should_allow_trade(self, confidence: float) -> Tuple[bool, str]:
        """Check if a trade with given confidence should be allowed.

        Returns (allowed, reason).
        """
        self.evaluate()

        if self.state.mode == "LOCKED":
            return False, self.state.reason

        if confidence < self.state.confidence_floor:
            return False, (
                f"[ADAPTIVE] {self.state.mode}: confidence {confidence*100:.1f}% "
                f"< floor {self.state.confidence_floor*100:.1f}% — {self.state.reason}"
            )

        return True, f"[ADAPTIVE] {self.state.mode}: allowed (conf={confidence*100:.1f}%)"

    def get_position_size_mult(self) -> float:
        """Multiplier for position sizing. 1.0 = normal."""
        self.evaluate()
        return self.state.position_size_mult

    def get_extra_cooldown(self) -> int:
        """Extra seconds to wait between trades."""
        self.evaluate()
        return self.state.cooldown_seconds


# ═══════════════════════════════════════════════════════════════════
# SINGLETON — one instance for the whole app
# ═══════════════════════════════════════════════════════════════════

pnl_tracker = RealizedPnLTracker()
reversal_detector = PostEntryReversalDetector()
adaptive_risk = AdaptiveRiskManager(pnl_tracker)
