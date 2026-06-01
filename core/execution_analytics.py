"""
VcanTrade AI - Execution Quality Analytics
============================================
Track slippage, fill rate, missed trades, and execution latency.
"""
import logging
from typing import Dict, List
from datetime import datetime
import json
from pathlib import Path

logger = logging.getLogger(__name__)

EXEC_ANALYTICS = Path("execution_analytics.json")


class ExecutionAnalytics:
    """
    Institutional execution analytics. Track every fill's quality.
    BlackRock's TCA (Transaction Cost Analysis) team lives on this data.
    """

    def __init__(self):
        self.executions: List[Dict] = []
        self.target_fills: List[Dict] = []
        self._load()

    def record_execution(self, symbol: str, side: str, intended_price: float,
                          fill_price: float, intended_size: int, filled_size: int,
                          latency_ms: float, order_type: str = "MARKET"):
        """Record every execution for TCA analysis."""
        slippage = (fill_price - intended_price) * (1 if side == "BUY" else -1)
        slippage_bps = (slippage / intended_price) * 10000 if intended_price > 0 else 0
        fill_rate = (filled_size / intended_size) if intended_size > 0 else 0
        is_partial = filled_size < intended_size

        record = {
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "side": side,
            "intended_price": intended_price,
            "fill_price": fill_price,
            "slippage": slippage,
            "slippage_bps": slippage_bps,
            "intended_size": intended_size,
            "filled_size": filled_size,
            "fill_rate": fill_rate,
            "is_partial": is_partial,
            "latency_ms": latency_ms,
            "order_type": order_type,
        }
        self.executions.append(record)
        if len(self.executions) > 5000:
            self.executions = self.executions[-5000:]
        self._save()
        if abs(slippage_bps) > 5:  # > 5 bps slippage
            logger.warning("[EXEC] High slippage: %s %s %.1f bps ($%.2f)",
                           side, symbol, slippage_bps, slippage)
        return record

    def record_missed_trade(self, symbol: str, side: str, intended_price: float,
                             reason: str):
        """Track trades we wanted but couldn't execute."""
        self.target_fills.append({
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": symbol, "side": side,
            "intended_price": intended_price, "reason": reason,
        })
        if len(self.target_fills) > 1000:
            self.target_fills = self.target_fills[-1000:]
        self._save()

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------
    def avg_slippage_bps(self) -> float:
        if not self.executions:
            return 0.0
        return sum(e["slippage_bps"] for e in self.executions) / len(self.executions)

    def fill_rate(self) -> float:
        if not self.executions:
            return 0.0
        full_fills = sum(1 for e in self.executions if not e["is_partial"])
        return (full_fills / len(self.executions)) * 100

    def avg_latency_ms(self) -> float:
        if not self.executions:
            return 0.0
        return sum(e["latency_ms"] for e in self.executions) / len(self.executions)

    def missed_trade_count(self) -> int:
        return len(self.target_fills)

    def tca_report(self) -> Dict:
        """Transaction Cost Analysis report."""
        return {
            "total_executions": len(self.executions),
            "avg_slippage_bps": round(self.avg_slippage_bps(), 2),
            "fill_rate_pct": round(self.fill_rate(), 2),
            "avg_latency_ms": round(self.avg_latency_ms(), 1),
            "missed_trades": self.missed_trade_count(),
            "miss_rate_pct": round(
                (self.missed_trade_count() / max(1, len(self.executions) + self.missed_trade_count())) * 100, 2),
            "high_slippage_count": sum(1 for e in self.executions if abs(e["slippage_bps"]) > 5),
        }

    def _save(self):
        try:
            data = {
                "executions": self.executions[-2000:],
                "target_fills": self.target_fills[-500:],
            }
            EXEC_ANALYTICS.write_text(json.dumps(data, indent=2, default=str))
        except Exception as e:
            logger.warning("[EXEC] Save error: %s", e)

    def _load(self):
        try:
            if EXEC_ANALYTICS.exists():
                data = json.loads(EXEC_ANALYTICS.read_text())
                self.executions = data.get("executions", [])
                self.target_fills = data.get("target_fills", [])
        except Exception as e:
            logger.warning("[EXEC] Load error: %s", e)


exec_analytics = ExecutionAnalytics()
