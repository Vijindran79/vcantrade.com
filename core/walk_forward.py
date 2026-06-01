"""
VcanTrade AI - Walk-Forward Optimization
==========================================
Prevents overfitting by testing on out-of-sample data.
This is what separates quants from amateurs.
"""
import logging
import math
from typing import Dict, List, Callable, Optional
from datetime import datetime
import json
from pathlib import Path

logger = logging.getLogger(__name__)

WFO_RESULTS = Path("walk_forward_results.json")


class WalkForwardOptimizer:
    """
    Walk-forward optimization (WFO) — the institutional standard.
    Splits data into rolling train/test windows, optimizes on train,
    tests on out-of-sample. This prevents curve-fitting.
    """

    def __init__(self, train_pct: float = 0.7,
                 n_splits: int = 5,
                 anchored: bool = True):
        self.train_pct = train_pct
        self.n_splits = n_splits
        self.anchored = anchored
        self.results: List[Dict] = []

    def optimize(self, candles: List[Dict],
                 strategy_factory: Callable,
                 param_grid: Dict[str, List],
                 metric: str = "sharpe_ratio") -> Dict:
        """
        Run walk-forward optimization.

        strategy_factory: function that takes params dict and returns a strategy function
        param_grid: dict of param name -> list of values to test
        """
        if len(candles) < 200:
            return {"error": "Need at least 200 candles for WFO"}

        n = len(candles)
        split_size = n // self.n_splits
        if split_size < 100:
            return {"error": "Not enough data per split"}

        oos_results = []
        best_params_history = []

        for split_idx in range(self.n_splits):
            if self.anchored:
                train_end = (split_idx + 1) * split_size
                train_start = 0
            else:
                train_end = (split_idx + 1) * split_size
                train_start = max(0, train_end - int(split_size * 3))  # Rolling

            test_end = min(n, train_end + split_size)
            train = candles[train_start:train_end]
            test = candles[train_end:test_end]

            if len(train) < 100 or len(test) < 30:
                continue

            # Grid search on training set
            best_score = -math.inf
            best_params = {}
            for params in self._grid_iter(param_grid):
                strategy = strategy_factory(params)
                score = self._evaluate(train, strategy, metric)
                if score > best_score:
                    best_score = score
                    best_params = params

            # Test on out-of-sample
            strategy = strategy_factory(best_params)
            oos_score = self._evaluate(test, strategy, metric)
            oos_pnl = self._evaluate(test, strategy, "total_pnl")

            oos_results.append({
                "split": split_idx + 1,
                "train_size": len(train),
                "test_size": len(test),
                "best_params": best_params,
                "train_score": best_score,
                "test_score": oos_score,
                "test_pnl": oos_pnl,
            })
            best_params_history.append(best_params)
            logger.info("[WFO] Split %d: train=%.3f, test=%.3f, params=%s",
                        split_idx + 1, best_score, oos_score, best_params)

        # Degradation analysis
        report = self._build_report(oos_results, best_params_history)
        self._save(report)
        return report

    def _evaluate(self, candles: List[Dict], strategy_func, metric: str) -> float:
        """Run strategy on a window and return a single metric."""
        from core.backtester import Backtester
        bt = Backtester(initial_capital=50000, risk_per_trade_pct=1.0)
        # Quick simulate
        capital = 50000
        position = None
        for i in range(50, len(candles)):
            bar = candles[i]
            price = bar["close"]
            window = candles[:i + 1]
            if position:
                pnl = (price - position["entry"]) * position["contracts"] * (1 if position["side"] == "BUY" else -1)
                if (position["side"] == "BUY" and price >= position["tp"]) or \
                   (position["side"] == "SELL" and price <= position["tp"]):
                    capital += pnl - 1.24
                    position = None
                elif (position["side"] == "BUY" and price <= position["sl"]) or \
                     (position["side"] == "SELL" and price >= position["sl"]):
                    capital += pnl - 1.24
                    position = None
            if position is None:
                action = strategy_func(window)
                if action in ("BUY", "SELL"):
                    atr = sum(c["high"] - c["low"] for c in window[-14:]) / 14 or 1.0
                    sl = price - atr * 1.5 if action == "BUY" else price + atr * 1.5
                    tp = price + atr * 2.0 if action == "BUY" else price - atr * 2.0
                    position = {"side": action, "entry": price, "sl": sl, "tp": tp, "contracts": 1}
        if metric == "total_pnl":
            return capital - 50000
        return capital

    def _grid_iter(self, grid: Dict):
        """Iterate over all combinations of params."""
        keys = list(grid.keys())
        if not keys:
            yield {}
            return
        idx = [0] * len(keys)

        def to_dict():
            return {k: grid[k][i] for k, i in zip(keys, idx)}

        while True:
            yield to_dict()
            # Increment last index, carry over
            j = len(keys) - 1
            while j >= 0:
                idx[j] += 1
                if idx[j] < len(grid[keys[j]]):
                    break
                idx[j] = 0
                j -= 1
            if j < 0:
                break

    def _build_report(self, oos_results: List[Dict], param_history: List[Dict]) -> Dict:
        if not oos_results:
            return {"error": "No valid splits"}
        train_scores = [r["train_score"] for r in oos_results]
        test_scores = [r["test_score"] for r in oos_results]
        degradation = (sum(train_scores) - sum(test_scores)) / max(1, sum(train_scores))
        # Most common params
        from collections import Counter
        stable_params = {}
        if param_history:
            for k in param_history[0].keys():
                vals = [str(p[k]) for p in param_history if k in p]
                if vals:
                    stable_params[k] = Counter(vals).most_common(1)[0][0]
        return {
            "n_splits": len(oos_results),
            "avg_train_score": sum(train_scores) / len(train_scores),
            "avg_test_score": sum(test_scores) / len(test_scores),
            "degradation_pct": round(degradation * 100, 2),
            "is_robust": degradation < 0.5,  # <50% degradation = robust
            "stable_params": stable_params,
            "splits": oos_results,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _save(self, report: Dict):
        try:
            history = []
            if WFO_RESULTS.exists():
                history = json.loads(WFO_RESULTS.read_text())
            history.append(report)
            WFO_RESULTS.write_text(json.dumps(history[-20:], indent=2, default=str))
        except Exception as e:
            logger.warning("[WFO] Save error: %s", e)


wfo = WalkForwardOptimizer()
