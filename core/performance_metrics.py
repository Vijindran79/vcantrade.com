"""
VcanTrade AI - Institutional Performance Metrics
=================================================
Sharpe Ratio, Sortino Ratio, Maximum Drawdown, Win Rate, Profit Factor,
Calmar Ratio, Expectancy, and Risk-Adjusted Return metrics.

Designed for BlackRock-grade institutional reporting.
"""
import logging
import math
from typing import List, Dict, Optional
from datetime import datetime
import json
from pathlib import Path

logger = logging.getLogger(__name__)

METRICS_DB = Path("performance_metrics.json")


class PerformanceMetrics:
    """
    Institutional-grade performance analytics.
    Tracks every metric BlackRock, Goldman, and Citadel look at.
    """

    def __init__(self, risk_free_rate: float = 0.05):
        self.risk_free_rate = risk_free_rate
        self.daily_rf = risk_free_rate / 252  # Daily risk-free rate
        self.trades: List[Dict] = []
        self.equity_curve: List[Dict] = []
        self.peak_equity: float = 0.0
        self.max_drawdown: float = 0.0
        self.max_drawdown_duration: int = 0
        self._dd_start: Optional[int] = None
        self._load_history()

    # ------------------------------------------------------------------
    # Trade Recording
    # ------------------------------------------------------------------
    def record_trade(self, symbol: str, side: str, entry: float, exit: float,
                     size: float, pnl: float, hold_time_sec: float = 0.0,
                     entry_time: str = "", exit_time: str = ""):
        """Record a closed trade for analytics."""
        ret = (exit - entry) / entry if entry > 0 else 0
        trade = {
            "symbol": symbol,
            "side": side,
            "entry": entry,
            "exit": exit,
            "size": size,
            "pnl": pnl,
            "return_pct": ret * 100,
            "hold_sec": hold_time_sec,
            "entry_time": entry_time or datetime.utcnow().isoformat(),
            "exit_time": exit_time or datetime.utcnow().isoformat(),
            "is_win": pnl > 0,
        }
        self.trades.append(trade)
        self._update_equity_curve(pnl)
        self._persist()
        logger.info("[METRICS] Trade recorded: %s %s pnl=$%.2f ret=%.2f%%",
                    side, symbol, pnl, trade["return_pct"])

    def _update_equity_curve(self, pnl: float):
        """Update equity curve and drawdown tracking."""
        prev_eq = self.equity_curve[-1]["equity"] if self.equity_curve else 0.0
        new_eq = prev_eq + pnl
        self.equity_curve.append({
            "timestamp": datetime.utcnow().isoformat(),
            "equity": new_eq,
            "pnl": pnl,
        })
        if new_eq > self.peak_equity:
            self.peak_equity = new_eq
            if self._dd_start is not None:
                self._dd_start = None
        else:
            if self._dd_start is None:
                self._dd_start = len(self.equity_curve) - 1
            dd = (self.peak_equity - new_eq) / self.peak_equity if self.peak_equity > 0 else 0
            self.max_drawdown = max(self.max_drawdown, dd)
            if self._dd_start is not None:
                dur = len(self.equity_curve) - self._dd_start
                self.max_drawdown_duration = max(self.max_drawdown_duration, dur)

    # ------------------------------------------------------------------
    # Core Metrics
    # ------------------------------------------------------------------
    def sharpe_ratio(self, lookback: int = 0) -> float:
        """
        Sharpe Ratio = (mean return - risk-free) / std dev of returns
        Annualized. BlackRock's primary metric.
        """
        rets = self._returns(lookback)
        if len(rets) < 2:
            return 0.0
        excess = [r - self.daily_rf for r in rets]
        mean = sum(excess) / len(excess)
        std = self._std(rets)
        if std == 0:
            return 0.0
        return (mean / std) * math.sqrt(252)

    def sortino_ratio(self, lookback: int = 0) -> float:
        """
        Sortino Ratio = excess return / downside deviation.
        Better than Sharpe — only penalizes harmful volatility.
        """
        rets = self._returns(lookback)
        if len(rets) < 2:
            return 0.0
        excess = [r - self.daily_rf for r in rets]
        mean = sum(excess) / len(excess)
        downside = [r for r in rets if r < self.daily_rf]
        if not downside:
            return 0.0
        down_std = math.sqrt(sum((r - self.daily_rf) ** 2 for r in downside) / len(downside))
        if down_std == 0:
            return 0.0
        return (mean / down_std) * math.sqrt(252)

    def calmar_ratio(self) -> float:
        """
        Calmar Ratio = annualized return / max drawdown.
        Reward-to-pain ratio used by hedge funds.
        """
        if self.max_drawdown == 0:
            return 0.0
        ann_ret = self._annualized_return()
        return ann_ret / self.max_drawdown

    def max_drawdown_pct(self) -> float:
        """Maximum peak-to-trough drawdown as percentage."""
        return self.max_drawdown * 100

    def win_rate(self) -> float:
        """Percentage of winning trades."""
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t["is_win"])
        return (wins / len(self.trades)) * 100

    def profit_factor(self) -> float:
        """
        Profit Factor = gross profit / gross loss.
        > 1.5 is good, > 2.0 is excellent.
        """
        gross_profit = sum(t["pnl"] for t in self.trades if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in self.trades if t["pnl"] < 0))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    def expectancy(self) -> float:
        """
        Expectancy = (win% * avg_win) - (loss% * avg_loss).
        Expected $ per trade. Must be positive to be profitable.
        """
        if not self.trades:
            return 0.0
        wins = [t["pnl"] for t in self.trades if t["is_win"]]
        losses = [abs(t["pnl"]) for t in self.trades if not t["is_win"]]
        wr = self.win_rate() / 100
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        return (wr * avg_win) - ((1 - wr) * avg_loss)

    def avg_win(self) -> float:
        wins = [t["pnl"] for t in self.trades if t["is_win"]]
        return sum(wins) / len(wins) if wins else 0.0

    def avg_loss(self) -> float:
        losses = [t["pnl"] for t in self.trades if not t["is_win"]]
        return sum(losses) / len(losses) if losses else 0.0

    def largest_win(self) -> float:
        return max((t["pnl"] for t in self.trades), default=0.0)

    def largest_loss(self) -> float:
        return min((t["pnl"] for t in self.trades), default=0.0)

    def total_pnl(self) -> float:
        return sum(t["pnl"] for t in self.trades)

    def total_trades(self) -> int:
        return len(self.trades)

    def avg_hold_time(self) -> float:
        if not self.trades:
            return 0.0
        return sum(t["hold_sec"] for t in self.trades) / len(self.trades)

    def _annualized_return(self) -> float:
        if len(self.equity_curve) < 2:
            return 0.0
        total_ret = (self.equity_curve[-1]["equity"] - self.equity_curve[0]["equity"])
        start_eq = self.equity_curve[0]["equity"] or 1.0
        days = max(1, len(self.equity_curve))
        return ((1 + total_ret / start_eq) ** (365 / days) - 1) * 100

    def _returns(self, lookback: int = 0) -> List[float]:
        eq = self.equity_curve[-(lookback + 1):] if lookback > 0 else self.equity_curve
        if len(eq) < 2:
            return []
        out = []
        for i in range(1, len(eq)):
            prev = eq[i - 1]["equity"]
            if prev != 0:
                out.append((eq[i]["equity"] - prev) / abs(prev))
        return out

    @staticmethod
    def _std(values: List[float]) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
        return math.sqrt(var)

    # ------------------------------------------------------------------
    # Institutional Report
    # ------------------------------------------------------------------
    def institutional_report(self) -> Dict:
        """
        Full institutional-grade report. What BlackRock's risk team sees.
        """
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "total_trades": self.total_trades(),
            "total_pnl": round(self.total_pnl(), 2),
            "win_rate_pct": round(self.win_rate(), 2),
            "profit_factor": round(self.profit_factor(), 3),
            "expectancy": round(self.expectancy(), 2),
            "avg_win": round(self.avg_win(), 2),
            "avg_loss": round(self.avg_loss(), 2),
            "largest_win": round(self.largest_win(), 2),
            "largest_loss": round(self.largest_loss(), 2),
            "avg_hold_sec": round(self.avg_hold_time(), 1),
            "sharpe_ratio": round(self.sharpe_ratio(), 3),
            "sortino_ratio": round(self.sortino_ratio(), 3),
            "calmar_ratio": round(self.calmar_ratio(), 3),
            "max_drawdown_pct": round(self.max_drawdown_pct(), 2),
            "max_dd_duration": self.max_drawdown_duration,
            "annualized_return_pct": round(self._annualized_return(), 2),
            "equity_curve_points": len(self.equity_curve),
        }

    def summary_string(self) -> str:
        r = self.institutional_report()
        return (
            f"\n{'='*60}\n"
            f"  INSTITUTIONAL PERFORMANCE REPORT\n"
            f"{'='*60}\n"
            f"  Trades:          {r['total_trades']}\n"
            f"  Total P&L:       ${r['total_pnl']:,.2f}\n"
            f"  Win Rate:        {r['win_rate_pct']:.1f}%\n"
            f"  Profit Factor:   {r['profit_factor']:.2f}\n"
            f"  Expectancy:      ${r['expectancy']:.2f}/trade\n"
            f"  Avg Win/Loss:    ${r['avg_win']:.2f} / ${r['avg_loss']:.2f}\n"
            f"  Sharpe Ratio:    {r['sharpe_ratio']:.3f}\n"
            f"  Sortino Ratio:   {r['sortino_ratio']:.3f}\n"
            f"  Calmar Ratio:    {r['calmar_ratio']:.3f}\n"
            f"  Max Drawdown:    {r['max_drawdown_pct']:.2f}%\n"
            f"  Annual Return:   {r['annualized_return_pct']:.2f}%\n"
            f"{'='*60}\n"
        )

    def _persist(self):
        try:
            data = {
                "trades": self.trades[-500:],
                "equity_curve": self.equity_curve[-1000:],
                "peak_equity": self.peak_equity,
                "max_drawdown": self.max_drawdown,
                "max_dd_duration": self.max_drawdown_duration,
            }
            METRICS_DB.write_text(json.dumps(data, indent=2, default=str))
        except Exception as e:
            logger.warning("[METRICS] Could not persist: %s", e)

    def _load_history(self):
        try:
            if METRICS_DB.exists():
                data = json.loads(METRICS_DB.read_text())
                self.trades = data.get("trades", [])
                self.equity_curve = data.get("equity_curve", [])
                self.peak_equity = data.get("peak_equity", 0.0)
                self.max_drawdown = data.get("max_drawdown", 0.0)
                self.max_drawdown_duration = data.get("max_dd_duration", 0)
                logger.info("[METRICS] Loaded %d historical trades", len(self.trades))
        except Exception as e:
            logger.warning("[METRICS] Could not load history: %s", e)


# Singleton instance for app-wide use
metrics = PerformanceMetrics()
