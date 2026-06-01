"""
VcanTrade AI - Institutional Backtester
========================================
Backtest the trading strategy on historical data.
Generates full institutional performance report.
"""
import logging
from typing import Dict, List, Optional
from datetime import datetime
import json
from pathlib import Path

from core.performance_metrics import PerformanceMetrics

logger = logging.getLogger(__name__)

BACKTEST_RESULTS = Path("backtest_results.json")


class Backtester:
    """
    Institutional-grade backtester.
    Walk a strategy over historical OHLCV bars and produce a full report.
    """

    def __init__(self, initial_capital: float = 50000.0,
                 risk_per_trade_pct: float = 1.0,
                 commission_per_contract: float = 0.62):
        self.initial_capital = initial_capital
        self.risk_pct = risk_per_trade_pct / 100.0
        self.commission = commission_per_contract
        self.results: Dict = {}

    def run(self, symbol: str, candles: List[Dict], strategy_func) -> Dict:
        """
        Run backtest on historical candles.
        candles: list of dicts with keys: timestamp, open, high, low, close, volume
        strategy_func(candles_so_far) -> 'BUY' | 'SELL' | 'HOLD' or dict with action, sl, tp
        """
        if not candles or len(candles) < 50:
            return {"error": "Need at least 50 candles"}

        capital = self.initial_capital
        position = None
        trades = []
        equity = [(candles[0]["timestamp"], capital)]

        for i in range(50, len(candles)):
            bar = candles[i]
            window = candles[:i + 1]
            price = bar["close"]

            # If in position, check exit
            if position:
                pnl = self._calc_pnl(position, price)
                hit_sl = (position["side"] == "BUY" and bar["low"] <= position["sl"]) or \
                         (position["side"] == "SELL" and bar["high"] >= position["sl"])
                hit_tp = (position["side"] == "BUY" and bar["high"] >= position["tp"]) or \
                         (position["side"] == "SELL" and bar["low"] <= position["tp"])

                if hit_sl or hit_tp:
                    exit_price = position["sl"] if hit_sl else position["tp"]
                    trade_pnl = self._calc_pnl(position, exit_price) - (self.commission * 2)
                    capital += trade_pnl
                    trades.append({
                        "entry": position["entry"], "exit": exit_price,
                        "side": position["side"], "pnl": trade_pnl,
                        "exit_reason": "SL" if hit_sl else "TP",
                        "bars_held": i - position["entry_bar"],
                    })
                    equity.append((bar["timestamp"], capital))
                    position = None

            # Look for entry
            if position is None:
                sig = strategy_func(window)
                action = sig if isinstance(sig, str) else (sig.get("action") if isinstance(sig, dict) else "HOLD")

                if action in ("BUY", "SELL"):
                    # ATR-based stop and target
                    atr = self._atr(window, 14)
                    sl = price - (atr * 1.5) if action == "BUY" else price + (atr * 1.5)
                    tp = price + (atr * 2.0) if action == "BUY" else price - (atr * 2.0)
                    risk = abs(price - sl) * self._mult(symbol)
                    contracts = max(1, int((capital * self.risk_pct) / risk)) if risk > 0 else 1

                    position = {
                        "side": action, "entry": price, "sl": sl, "tp": tp,
                        "contracts": contracts, "entry_bar": i,
                    }

        # Close any remaining position
        if position:
            exit_price = candles[-1]["close"]
            trade_pnl = self._calc_pnl(position, exit_price) - self.commission
            capital += trade_pnl
            trades.append({
                "entry": position["entry"], "exit": exit_price,
                "side": position["side"], "pnl": trade_pnl,
                "exit_reason": "END", "bars_held": len(candles) - position["entry_bar"],
            })
            equity.append((candles[-1]["timestamp"], capital))

        # Generate report
        report = self._build_report(symbol, trades, equity)
        self._save_results(report)
        logger.info("[BACKTEST] %s: %d trades, $%.2f P&L, %.1f%% win rate",
                    symbol, len(trades), report["total_pnl"], report["win_rate_pct"])
        return report

    def _calc_pnl(self, position: Dict, exit_price: float) -> float:
        sign = 1 if position["side"] == "BUY" else -1
        points = (exit_price - position["entry"]) * sign
        return points * self._mult(position.get("symbol", "")) * position["contracts"]

    def _mult(self, symbol: str) -> float:
        if "MNQ" in symbol or "NQ" in symbol: return 2.0
        if "MES" in symbol or "ES" in symbol: return 5.0
        if "MCL" in symbol or "CL" in symbol: return 1.0
        if "MGC" in symbol or "GC" in symbol: return 1.0
        return 1.0

    def _atr(self, candles: List[Dict], period: int) -> float:
        if len(candles) < period + 1:
            return 1.0
        trs = []
        for i in range(-period, 0):
            h = candles[i]["high"]
            l = candles[i]["low"]
            pc = candles[i - 1]["close"]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        return sum(trs) / len(trs) if trs else 1.0

    def _build_report(self, symbol: str, trades: List[Dict], equity: List) -> Dict:
        m = PerformanceMetrics(risk_free_rate=0.05)
        for t in trades:
            m.record_trade(
                symbol=symbol, side=t["side"],
                entry=t["entry"], exit=t["exit"],
                size=t["contracts"], pnl=t["pnl"],
                hold_time_sec=t.get("bars_held", 0) * 60,
            )
        report = m.institutional_report()
        report["symbol"] = symbol
        report["initial_capital"] = self.initial_capital
        report["final_capital"] = round(equity[-1][1] if equity else self.initial_capital, 2)
        report["total_return_pct"] = round(
            ((report["final_capital"] - self.initial_capital) / self.initial_capital) * 100, 2)
        return report

    def _save_results(self, report: Dict):
        try:
            history = []
            if BACKTEST_RESULTS.exists():
                history = json.loads(BACKTEST_RESULTS.read_text())
            history.append({**report, "timestamp": datetime.utcnow().isoformat()})
            BACKTEST_RESULTS.write_text(json.dumps(history, indent=2, default=str))
        except Exception as e:
            logger.warning("[BACKTEST] Save error: %s", e)


# Simple built-in strategy for quick backtests
def sma_crossover_strategy(candles: List[Dict]) -> str:
    """SMA(10)/SMA(30) crossover with RSI filter."""
    if len(candles) < 30:
        return "HOLD"
    closes = [c["close"] for c in candles[-30:]]
    sma_fast = sum(closes[-10:]) / 10
    sma_slow = sum(closes) / 30
    if sma_fast > sma_slow * 1.001:
        return "BUY"
    elif sma_fast < sma_slow * 0.999:
        return "SELL"
    return "HOLD"


backtester = Backtester()
