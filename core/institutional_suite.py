"""
VcanTrade AI - Institutional Suite Integration
================================================
Unified entry point for all institutional-grade features.
Hooks into the trade engine and dashboard.
"""
import logging
from typing import Dict, Optional
from datetime import datetime

from core.performance_metrics import metrics as perf_metrics
from core.position_sizer import sizer as position_sizer
from core.regime_detector import regime_detector
from core.backtester import backtester
from core.walk_forward import wfo
from core.execution_analytics import exec_analytics
from core.equity_curve import equity_curve
from core.alerts import alerts, AlertLevel

logger = logging.getLogger(__name__)


class InstitutionalSuite:
    """
    Single coordinator for all institutional features.
    Call this from the trade engine on every tick and every trade.
    """

    def __init__(self, account_balance: float = 50000.0):
        self.account_balance = account_balance
        self.position_sizer = position_sizer
        self.perf_metrics = perf_metrics
        self.regime = regime_detector
        self.equity = equity_curve
        self.alerts = alerts
        self.exec_analytics = exec_analytics
        self.backtester = backtester
        self.wfo = wfo
        self.daily_loss_limit_pct = 2.0
        self.daily_target_pct = 3.0
        self.max_drawdown_limit_pct = 5.0

    # ------------------------------------------------------------------
    # Hooks called from trade engine
    # ------------------------------------------------------------------
    def on_tick(self, current_balance: float):
        """Call on every price tick. Updates equity, checks alerts."""
        self.equity.update(current_balance)
        # Alert checks
        self.alerts.drawdown_alert(
            self.equity.current_drawdown_pct(), self.max_drawdown_limit_pct)
        self.alerts.daily_target_hit(
            self.equity.daily_pnl_pct(), self.daily_target_pct)
        self.alerts.daily_loss_breach(
            self.equity.daily_pnl_pct(), self.daily_loss_limit_pct)
        # Update account balance on sizer
        self.position_sizer.update_balance(current_balance)

    def on_signal(self, signal: Dict, prices: list) -> Dict:
        """
        Process signal through all institutional filters.
        Returns adjusted signal or {allow: False}.
        """
        # 1. Regime detection
        regime = self.regime.detect(prices)
        if not self.regime.should_trade():
            return {"allow": False, "reason": f"Regime {regime.value} blocks trading"}

        # 2. Regime filter
        filt = self.regime.filter_signal(signal.get("action", "HOLD"),
                                         signal.get("confidence", 0.5))
        if not filt.get("allow"):
            return {"allow": False, "reason": filt.get("reason", "regime_filter")}

        # 3. Daily loss / drawdown gates
        if self.equity.is_daily_loss_breached(self.daily_loss_limit_pct):
            return {"allow": False, "reason": "DAILY_LOSS_BREACH"}
        if self.equity.is_in_drawdown(self.max_drawdown_limit_pct):
            return {"allow": False, "reason": "DRAWDOWN_BREACH"}

        # 4. Position sizing
        adjusted = dict(signal)
        adjusted["confidence"] = filt["adjusted_confidence"]
        adjusted["regime"] = regime.value
        return {"allow": True, "signal": adjusted, "regime_params": filt}

    def on_execution(self, symbol: str, side: str, intended_price: float,
                     fill_price: float, intended_size: int, filled_size: int,
                     latency_ms: float = 0.0):
        """Record execution analytics."""
        self.exec_analytics.record_execution(
            symbol=symbol, side=side,
            intended_price=intended_price, fill_price=fill_price,
            intended_size=intended_size, filled_size=filled_size,
            latency_ms=latency_ms)

    def on_trade_close(self, symbol: str, side: str, entry: float, exit: float,
                       size: int, pnl: float, hold_sec: float = 0.0):
        """Record closed trade for performance metrics."""
        self.perf_metrics.record_trade(
            symbol=symbol, side=side, entry=entry, exit=exit,
            size=size, pnl=pnl, hold_time_sec=hold_sec)
        self.equity.add_pnl(pnl)

    def on_regime_shift(self, old_regime: str, new_regime: str):
        self.alerts.regime_shift(old_regime, new_regime)

    # ------------------------------------------------------------------
    # Sizing
    # ------------------------------------------------------------------
    def size_position(self, symbol: str, entry: float, sl: float, atr: float,
                      win_rate: float = 0.55, avg_win: float = 100.0,
                      avg_loss: float = 50.0, confidence: float = 1.0) -> Dict:
        """Get institutional position size."""
        return self.position_sizer.optimal_size(
            symbol=symbol, entry_price=entry, stop_loss=sl, atr=atr,
            win_rate=win_rate, avg_win=avg_win, avg_loss=avg_loss,
            confidence=confidence)

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------
    def full_report(self) -> Dict:
        """Full institutional report for dashboard/API."""
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "performance": self.perf_metrics.institutional_report(),
            "equity": self.equity.snapshot(),
            "regime": {
                "current": self.regime.current_regime.value,
                "params": self.regime.get_params(),
            },
            "execution": self.exec_analytics.tca_report(),
            "alerts": self.alerts.summary(),
        }

    def summary_string(self) -> str:
        """Human-readable summary."""
        lines = [
            "\n" + "=" * 70,
            "  INSTITUTIONAL SUITE — LIVE STATUS",
            "=" * 70,
            "",
            "  PERFORMANCE METRICS:",
        ]
        perf = self.perf_metrics.institutional_report()
        lines.append(f"    Trades: {perf['total_trades']}  |  P&L: ${perf['total_pnl']:,.2f}")
        lines.append(f"    Sharpe: {perf['sharpe_ratio']:.3f}  |  Sortino: {perf['sortino_ratio']:.3f}")
        lines.append(f"    Win Rate: {perf['win_rate_pct']:.1f}%  |  Profit Factor: {perf['profit_factor']:.2f}")
        lines.append(f"    Max Drawdown: {perf['max_drawdown_pct']:.2f}%")
        lines.append("")
        lines.append("  EQUITY:")
        eq = self.equity.snapshot()
        lines.append(f"    Balance: ${eq['current_balance']:,.2f}  |  Peak: ${eq['peak_balance']:,.2f}")
        lines.append(f"    Daily P&L: {eq['daily_pnl_pct']:.2f}%  |  Total: {eq['total_return_pct']:.2f}%")
        lines.append(f"    Current DD: {eq['current_drawdown_pct']:.2f}%")
        lines.append("")
        lines.append("  REGIME:")
        lines.append(f"    Current: {self.regime.current_regime.value}")
        params = self.regime.get_params()
        lines.append(f"    Size Mult: {params['position_size_mult']:.2f}x  |  Max Trades/Day: {params['max_trades_per_day']}")
        lines.append("")
        lines.append("  EXECUTION QUALITY:")
        ex = self.exec_analytics.tca_report()
        lines.append(f"    Fill Rate: {ex['fill_rate_pct']:.1f}%  |  Avg Slippage: {ex['avg_slippage_bps']:.1f} bps")
        lines.append(f"    Latency: {ex['avg_latency_ms']:.0f}ms  |  Missed: {ex['missed_trades']}")
        lines.append("")
        lines.append("  ALERTS:")
        a = self.alerts.summary()
        lines.append(f"    Total: {a['total_alerts']}  |  Critical: {a['critical_count']}  |  Warnings: {a['warning_count']}")
        lines.append("=" * 70 + "\n")
        return "\n".join(lines)


# Global singleton
suite = InstitutionalSuite()
