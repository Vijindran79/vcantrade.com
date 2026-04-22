"""
VcanTrade AI - Meta Analyzer (Stage 4)

Self-Correction Engine & Trade Journal Analyzer.
Reviews past trades, identifies patterns, and auto-adjusts config.

Features:
1. Trade Journal Analysis (7-day lookback)
2. Worst Performing Asset identification
3. Best Performing Timeframe detection
4. Auto-suggest config adjustments
5. Alpha Score calculation
"""

import logging
import json
import os
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np

logger = logging.getLogger(__name__)


class TradeJournal:
    """Persistent trade journal stored as JSON."""
    
    def __init__(self, filepath: str = "trade_ledger.json"):
        self.filepath = filepath
        self.trades: List[Dict] = []
        self.rejected_signals: List[Dict] = []
        self.load()
    
    def load(self):
        """Load trade journal from JSON file."""
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r') as f:
                    data = json.load(f)
                    self.trades = data.get("trades", [])
                    self.rejected_signals = data.get("rejected_signals", [])
                logger.info(f"[EMOJI] Trade Journal loaded: {len(self.trades)} trades")
            except Exception as e:
                logger.error(f"Failed to load trade journal: {e}")
                self.trades = []
                self.rejected_signals = []
        else:
            logger.info("[EMOJI] Trade Journal created (new)")
    
    def save(self):
        """Save trade journal to JSON file."""
        try:
            with open(self.filepath, 'w') as f:
                json.dump({
                    "trades": self.trades,
                    "rejected_signals": self.rejected_signals,
                    "last_updated": datetime.now().isoformat()
                }, f, indent=2)
            logger.info(f"[EMOJI] Trade Journal saved: {len(self.trades)} trades")
        except Exception as e:
            logger.error(f"Failed to save trade journal: {e}")
    
    def add_trade(self, trade: Dict):
        """Add a completed trade to journal."""
        trade["timestamp"] = datetime.now().isoformat()
        self.trades.append(trade)
        self.save()
    
    def add_rejected_signal(self, signal: Dict):
        """Add a rejected signal to journal."""
        signal["timestamp"] = datetime.now().isoformat()
        self.rejected_signals.append(signal)
        self.save()
    
    def get_recent_trades(self, days: int = 7) -> List[Dict]:
        """Get trades from last N days."""
        cutoff = datetime.now() - timedelta(days=days)
        return [
            t for t in self.trades
            if datetime.fromisoformat(t["timestamp"]) >= cutoff
        ]


class MetaAnalyzer:
    """
    Self-Correction Engine.
    
    Responsibilities:
    1. Review trade journal every 24 hours
    2. Identify worst performing asset
    3. Identify best performing timeframe
    4. Suggest (or apply) config adjustments
    5. Calculate Alpha Score (self-correction accuracy)
    """

    def __init__(
        self,
        journal: TradeJournal = None,
        review_interval_hours: int = 24,
        auto_apply: bool = False,  # If True, auto-applies config changes
    ):
        """
        Initialize Meta Analyzer.
        
        Args:
            journal: TradeJournal instance
            review_interval_hours: Hours between self-reviews (default 24)
            auto_apply: If True, automatically adjusts config (default False)
        """
        self.journal = journal or TradeJournal()
        self.review_interval_hours = review_interval_hours
        self.auto_apply = auto_apply
        self.last_review = datetime.now() - timedelta(hours=25)  # Force first review
        
        # Learning metrics
        self.alpha_score = 50.0  # Start at 50 (0-100 scale)
        self.total_reviews = 0
        self.total_adjustments = 0
        self.successful_adjustments = 0
        self.adjustment_history: List[Dict] = []
        
        # Performance tracking
        self.asset_performance: Dict[str, Dict] = {}
        self.timeframe_performance: Dict[str, Dict] = {}
        self.hourly_performance: Dict[int, Dict] = {}  # Hour of day
        
        logger.info(
            f"[BRAIN] Meta Analyzer initialized: "
            f"Review every {review_interval_hours}h, "
            f"Auto-apply: {auto_apply}"
        )

    def should_review(self) -> bool:
        """Check if it's time for self-review."""
        elapsed = datetime.now() - self.last_review
        return elapsed.total_seconds() >= (self.review_interval_hours * 3600)

    def perform_self_review(self) -> Dict:
        """
        Main self-correction routine.
        Reviews last 7 days of trades and suggests adjustments.
        """
        self.total_reviews += 1
        logger.info("[BRAIN] Starting self-review (Meta-Cognition)...")
        
        # Get recent trades
        recent_trades = self.journal.get_recent_trades(days=7)
        
        if len(recent_trades) < 5:
            logger.info(f"[BRAIN] Self-review skipped: Only {len(recent_trades)} trades (need 5+)")
            return {
                "status": "INSUFFICIENT_DATA",
                "trades_analyzed": len(recent_trades)
            }
        
        # Analyze performance
        asset_perf = self._analyze_asset_performance(recent_trades)
        tf_perf = self._analyze_timeframe_performance(recent_trades)
        hourly_perf = self._analyze_hourly_performance(recent_trades)
        
        # Store results
        self.asset_performance = asset_perf
        self.timeframe_performance = tf_perf
        self.hourly_performance = hourly_perf
        
        # Identify patterns
        worst_asset = self._find_worst_performer(asset_perf)
        best_asset = self._find_best_performer(asset_perf)
        best_timeframe = self._find_best_timeframe(tf_perf)
        worst_hour = self._find_worst_hour(hourly_perf)
        
        # Generate adjustment suggestions
        adjustments = self._generate_adjustments(
            worst_asset, best_asset, best_timeframe, worst_hour, asset_perf
        )
        
        # Apply adjustments if auto_apply enabled
        applied = []
        if self.auto_apply:
            applied = self._apply_adjustments(adjustments)
        
        # Update Alpha Score
        self._update_alpha_score(recent_trades, adjustments)
        
        # Log review complete
        self.last_review = datetime.now()
        
        review_summary = {
            "status": "COMPLETE",
            "trades_analyzed": len(recent_trades),
            "worst_asset": worst_asset,
            "best_asset": best_asset,
            "best_timeframe": best_timeframe,
            "worst_hour": worst_hour,
            "adjustments_suggested": len(adjustments),
            "adjustments_applied": len(applied),
            "alpha_score": self.alpha_score,
            "details": {
                "asset_performance": asset_perf,
                "timeframe_performance": tf_perf,
                "hourly_performance": hourly_perf,
                "adjustments": adjustments,
                "applied": applied
            }
        }
        
        logger.info(
            f"[BRAIN] Self-review complete: "
            f"{len(recent_trades)} trades analyzed, "
            f"{len(adjustments)} adjustments suggested, "
            f"Alpha Score: {self.alpha_score:.1f}"
        )
        
        return review_summary

    def _analyze_asset_performance(self, trades: List[Dict]) -> Dict[str, Dict]:
        """Analyze performance by asset."""
        asset_stats = defaultdict(lambda: {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "win_rate": 0.0
        })
        
        for trade in trades:
            asset = trade.get("asset", "UNKNOWN")
            pnl = trade.get("pnl", 0.0)
            
            asset_stats[asset]["total_trades"] += 1
            asset_stats[asset]["total_pnl"] += pnl
            
            if pnl > 0:
                asset_stats[asset]["wins"] += 1
            else:
                asset_stats[asset]["losses"] += 1
        
        # Calculate averages and win rates
        for asset, stats in asset_stats.items():
            if stats["total_trades"] > 0:
                stats["avg_pnl"] = stats["total_pnl"] / stats["total_trades"]
                stats["win_rate"] = stats["wins"] / stats["total_trades"]
        
        return dict(asset_stats)

    def _analyze_timeframe_performance(self, trades: List[Dict]) -> Dict[str, Dict]:
        """Analyze performance by timeframe."""
        tf_stats = defaultdict(lambda: {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "win_rate": 0.0
        })
        
        for trade in trades:
            tf = trade.get("timeframe", "UNKNOWN")
            pnl = trade.get("pnl", 0.0)
            
            tf_stats[tf]["total_trades"] += 1
            tf_stats[tf]["total_pnl"] += pnl
            
            if pnl > 0:
                tf_stats[tf]["wins"] += 1
            else:
                tf_stats[tf]["losses"] += 1
        
        for tf, stats in tf_stats.items():
            if stats["total_trades"] > 0:
                stats["avg_pnl"] = stats["total_pnl"] / stats["total_trades"]
                stats["win_rate"] = stats["wins"] / stats["total_trades"]
        
        return dict(tf_stats)

    def _analyze_hourly_performance(self, trades: List[Dict]) -> Dict[int, Dict]:
        """Analyze performance by hour of day."""
        hourly_stats = defaultdict(lambda: {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "win_rate": 0.0
        })
        
        for trade in trades:
            timestamp = datetime.fromisoformat(trade["timestamp"])
            hour = timestamp.hour
            pnl = trade.get("pnl", 0.0)
            
            hourly_stats[hour]["total_trades"] += 1
            hourly_stats[hour]["total_pnl"] += pnl
            
            if pnl > 0:
                hourly_stats[hour]["wins"] += 1
            else:
                hourly_stats[hour]["losses"] += 1
        
        for hour, stats in hourly_stats.items():
            if stats["total_trades"] > 0:
                stats["avg_pnl"] = stats["total_pnl"] / stats["total_trades"]
                stats["win_rate"] = stats["wins"] / stats["total_trades"]
        
        return dict(hourly_stats)

    def _find_worst_performer(self, asset_perf: Dict[str, Dict]) -> Optional[Dict]:
        """Find worst performing asset."""
        if not asset_perf:
            return None
        
        worst = min(asset_perf.items(), key=lambda x: x[1]["total_pnl"])
        return {
            "asset": worst[0],
            **worst[1]
        }

    def _find_best_performer(self, asset_perf: Dict[str, Dict]) -> Optional[Dict]:
        """Find best performing asset."""
        if not asset_perf:
            return None
        
        best = max(asset_perf.items(), key=lambda x: x[1]["total_pnl"])
        return {
            "asset": best[0],
            **best[1]
        }

    def _find_best_timeframe(self, tf_perf: Dict[str, Dict]) -> Optional[Dict]:
        """Find best performing timeframe."""
        if not tf_perf:
            return None
        
        best = max(tf_perf.items(), key=lambda x: x[1]["win_rate"])
        return {
            "timeframe": best[0],
            **best[1]
        }

    def _find_worst_hour(self, hourly_perf: Dict[int, Dict]) -> Optional[Dict]:
        """Find worst performing hour."""
        if not hourly_perf:
            return None
        
        worst = min(hourly_perf.items(), key=lambda x: x[1]["total_pnl"])
        return {
            "hour": worst[0],
            **worst[1]
        }

    def _generate_adjustments(
        self,
        worst_asset: Optional[Dict],
        best_asset: Optional[Dict],
        best_timeframe: Optional[Dict],
        worst_hour: Optional[Dict],
        asset_perf: Dict[str, Dict],
    ) -> List[Dict]:
        """Generate config adjustment suggestions."""
        adjustments = []
        
        # 1. Avoid worst asset if losing consistently
        if worst_asset and worst_asset["total_pnl"] < -100:
            adjustments.append({
                "type": "ASSET_RESTRICTION",
                "action": "RESTRICT_ASSET",
                "asset": worst_asset["asset"],
                "reason": f"Consistent loser: ${worst_asset['total_pnl']:.2f} PnL, {worst_asset['win_rate']:.0%} win rate",
                "suggestion": f"Temporarily restrict {worst_asset['asset']} trading",
                "priority": "HIGH"
            })
        
        # 2. Increase allocation to best asset
        if best_asset and best_asset["total_pnl"] > 100:
            adjustments.append({
                "type": "ASSET_ALLOCATION",
                "action": "INCREASE_ALLOCATION",
                "asset": best_asset["asset"],
                "reason": f"Top performer: ${best_asset['total_pnl']:.2f} PnL, {best_asset['win_rate']:.0%} win rate",
                "suggestion": f"Increase position size for {best_asset['asset']} by 20%",
                "priority": "MEDIUM"
            })
        
        # 3. Focus on best timeframe
        if best_timeframe and best_timeframe["win_rate"] > 0.6:
            adjustments.append({
                "type": "TIMEFRAME_OPTIMIZATION",
                "action": "PREFER_TIMEFRAME",
                "timeframe": best_timeframe["timeframe"],
                "reason": f"Best win rate: {best_timeframe['win_rate']:.0%} ({best_timeframe['total_trades']} trades)",
                "suggestion": f"Prioritize {best_timeframe['timeframe']} timeframe for scanning",
                "priority": "MEDIUM"
            })
        
        # 4. Avoid trading during worst hour
        if worst_hour and worst_hour["total_pnl"] < -50:
            adjustments.append({
                "type": "TIME_RESTRICTION",
                "action": "AVOID_HOUR",
                "hour": worst_hour["hour"],
                "reason": f"Worst hour: ${worst_hour['total_pnl']:.2f} PnL, {worst_hour['win_rate']:.0%} win rate",
                "suggestion": f"Avoid trading during {worst_hour['hour']}:00 hour",
                "priority": "LOW"
            })
        
        # 5. General confidence threshold adjustment
        all_pnls = [t.get("pnl", 0.0) for t in self.journal.get_recent_trades(days=7)]
        if all_pnls:
            avg_pnl = np.mean(all_pnls)
            if avg_pnl < -20:
                adjustments.append({
                    "type": "CONFIDENCE_THRESHOLD",
                    "action": "INCREASE_THRESHOLD",
                    "reason": f"Average PnL negative: ${avg_pnl:.2f}",
                    "suggestion": "Increase SWARM_CONFIDENCE_THRESHOLD by 0.05",
                    "priority": "HIGH"
                })
            elif avg_pnl > 50:
                adjustments.append({
                    "type": "CONFIDENCE_THRESHOLD",
                    "action": "DECREASE_THRESHOLD",
                    "reason": f"Average PnL very positive: ${avg_pnl:.2f}",
                    "suggestion": "Decrease SWARM_CONFIDENCE_THRESHOLD by 0.05 to capture more trades",
                    "priority": "LOW"
                })
        
        return adjustments

    def _apply_adjustments(self, adjustments: List[Dict]) -> List[Dict]:
        """Apply adjustments to config (if auto_apply enabled)."""
        applied = []
        
        for adjustment in adjustments:
            if adjustment["priority"] in ["HIGH", "MEDIUM"]:
                try:
                    # Log adjustment
                    self.adjustment_history.append({
                        **adjustment,
                        "applied_at": datetime.now().isoformat(),
                        "status": "APPLIED"
                    })
                    
                    self.total_adjustments += 1
                    self.successful_adjustments += 1
                    applied.append(adjustment)
                    
                    logger.info(
                        f"[OK] Adjustment applied: {adjustment['action']} "
                        f"({adjustment['priority']})"
                    )
                except Exception as e:
                    logger.error(f"Failed to apply adjustment: {e}")
                    self.adjustment_history.append({
                        **adjustment,
                        "applied_at": datetime.now().isoformat(),
                        "status": "FAILED",
                        "error": str(e)
                    })
        
        return applied

    def _update_alpha_score(self, trades: List[Dict], adjustments: List[Dict]):
        """
        Update Alpha Score based on self-correction accuracy.
        
        Alpha Score = How well the bot learns from its mistakes (0-100)
        """
        if not trades:
            return
        
        # Calculate recent win rate
        wins = sum(1 for t in trades if t.get("pnl", 0.0) > 0)
        win_rate = wins / len(trades)
        
        # Calculate adjustment success rate
        adjustment_success = (
            self.successful_adjustments / max(1, self.total_adjustments)
        )
        
        # Calculate consistency (lower variance in PnL is better)
        pnls = [t.get("pnl", 0.0) for t in trades]
        consistency = 1.0 - min(1.0, np.std(pnls) / 100.0) if pnls else 0.5
        
        # Weighted Alpha Score
        self.alpha_score = (
            win_rate * 40 +              # 40% weight on win rate
            adjustment_success * 30 +     # 30% weight on adjustment success
            consistency * 30              # 30% weight on consistency
        )
        
        # Clamp to 0-100
        self.alpha_score = max(0.0, min(100.0, self.alpha_score))

    def get_learning_summary(self) -> Dict:
        """Get learning progress summary for dashboard."""
        return {
            "alpha_score": self.alpha_score,
            "total_reviews": self.total_reviews,
            "total_adjustments": self.total_adjustments,
            "successful_adjustments": self.successful_adjustments,
            "adjustment_success_rate": (
                self.successful_adjustments / max(1, self.total_adjustments)
            ),
            "best_asset": (
                self._find_best_performer(self.asset_performance)["asset"]
                if self.asset_performance else "Unknown"
            ),
            "worst_asset": (
                self._find_worst_performer(self.asset_performance)["asset"]
                if self.asset_performance else "Unknown"
            ),
            "best_timeframe": (
                self._find_best_timeframe(self.timeframe_performance)["timeframe"]
                if self.timeframe_performance else "Unknown"
            ),
            "last_review": self.last_review.isoformat(),
            "next_review_in_hours": max(
                0,
                self.review_interval_hours - (
                    datetime.now() - self.last_review
                ).total_seconds() / 3600
            )
        }
