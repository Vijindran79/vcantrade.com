"""
VcanTrade AI - Real-Time Equity Curve Tracker
==============================================
Tracks live equity, drawdown, and performance in real-time.
Provides data for dashboard visualization.
"""
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import json
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

EQUITY_CURVE_FILE = Path("equity_curve_live.json")


class EquityCurveTracker:
    """
    Real-time equity curve with drawdown shading.
    Powers the institutional dashboard.
    """

    def __init__(self, starting_balance: float = 50000.0):
        self.starting_balance = starting_balance
        self.current_balance = starting_balance
        self.peak_balance = starting_balance
        self.points: List[Dict] = []
        self.daily_start = starting_balance
        self.daily_date = datetime.utcnow().date().isoformat()
        self._lock = threading.Lock()
        self._load()

    def update(self, current_balance: float, timestamp: Optional[datetime] = None):
        """Update current equity."""
        ts = timestamp or datetime.utcnow()
        with self._lock:
            self.current_balance = current_balance
            self.peak_balance = max(self.peak_balance, current_balance)

            # Reset daily baseline
            today = ts.date().isoformat()
            if today != self.daily_date:
                self.daily_start = current_balance
                self.daily_date = today

            dd = ((self.peak_balance - current_balance) / self.peak_balance) * 100
            daily_pnl_pct = ((current_balance - self.daily_start) / self.daily_start) * 100
            total_pnl_pct = ((current_balance - self.starting_balance) / self.starting_balance) * 100

            point = {
                "timestamp": ts.isoformat(),
                "equity": current_balance,
                "drawdown_pct": dd,
                "daily_pnl_pct": daily_pnl_pct,
                "total_pnl_pct": total_pnl_pct,
            }
            self.points.append(point)
            if len(self.points) > 5000:
                self.points = self.points[-5000:]
            self._save()

    def add_pnl(self, pnl: float):
        """Add realized P&L to the curve."""
        self.update(self.current_balance + pnl)

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------
    def current_drawdown_pct(self) -> float:
        if self.peak_balance <= 0:
            return 0.0
        return ((self.peak_balance - self.current_balance) / self.peak_balance) * 100

    def daily_pnl_pct(self) -> float:
        if self.daily_start <= 0:
            return 0.0
        return ((self.current_balance - self.daily_start) / self.daily_start) * 100

    def total_return_pct(self) -> float:
        if self.starting_balance <= 0:
            return 0.0
        return ((self.current_balance - self.starting_balance) / self.starting_balance) * 100

    def is_in_drawdown(self, threshold_pct: float = 5.0) -> bool:
        """Whether current drawdown exceeds threshold."""
        return self.current_drawdown_pct() >= threshold_pct

    def is_daily_target_hit(self, target_pct: float = 3.0) -> bool:
        """Whether daily profit target has been reached."""
        return self.daily_pnl_pct() >= target_pct

    def is_daily_loss_breached(self, loss_pct: float = 2.0) -> bool:
        """Whether daily loss limit has been breached."""
        return self.daily_pnl_pct() <= -loss_pct

    def recent_points(self, minutes: int = 60) -> List[Dict]:
        """Get points from the last N minutes."""
        cutoff = datetime.utcnow() - timedelta(minutes=minutes)
        out = []
        for p in reversed(self.points):
            try:
                t = datetime.fromisoformat(p["timestamp"])
                if t < cutoff:
                    break
                out.append(p)
            except Exception:
                continue
        return list(reversed(out))

    def snapshot(self) -> Dict:
        """Current state snapshot for dashboard."""
        return {
            "starting_balance": self.starting_balance,
            "current_balance": round(self.current_balance, 2),
            "peak_balance": round(self.peak_balance, 2),
            "current_drawdown_pct": round(self.current_drawdown_pct(), 2),
            "daily_pnl_pct": round(self.daily_pnl_pct(), 2),
            "total_return_pct": round(self.total_return_pct(), 2),
            "points_count": len(self.points),
            "last_update": self.points[-1]["timestamp"] if self.points else None,
        }

    def _save(self):
        try:
            EQUITY_CURVE_FILE.write_text(json.dumps({
                "starting_balance": self.starting_balance,
                "current_balance": self.current_balance,
                "peak_balance": self.peak_balance,
                "daily_start": self.daily_start,
                "daily_date": self.daily_date,
                "points": self.points[-1000:],
            }, indent=2, default=str))
        except Exception as e:
            logger.warning("[EQUITY] Save error: %s", e)

    def _load(self):
        try:
            if EQUITY_CURVE_FILE.exists():
                data = json.loads(EQUITY_CURVE_FILE.read_text())
                self.starting_balance = data.get("starting_balance", 50000.0)
                self.current_balance = data.get("current_balance", self.starting_balance)
                self.peak_balance = data.get("peak_balance", self.current_balance)
                self.daily_start = data.get("daily_start", self.current_balance)
                self.daily_date = data.get("daily_date", self.daily_date)
                self.points = data.get("points", [])
        except Exception as e:
            logger.warning("[EQUITY] Load error: %s", e)


equity_curve = EquityCurveTracker()
