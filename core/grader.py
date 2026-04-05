"""
VcaniTrade AI - Grader Module
Analyzes trading performance and provides letter grades (A-F)
"""

import logging
import sqlite3
from typing import Dict, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class Grader:
    """
    Grades AI trading performance based on historical data
    Provides A-F letter grades like a school report card
    """
    
    def __init__(self, db_path: str = "vcanitrade_ledger.db"):
        self.db_path = db_path
    
    def _get_connection(self):
        """Get database connection"""
        return sqlite3.connect(self.db_path)
    
    def generate_report_card(self, days: int = 30) -> Dict:
        """
        Generate comprehensive performance report
        Returns dict with grades and statistics
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Get trades from last N days
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cursor.execute("""
            SELECT * FROM trades WHERE timestamp >= ? AND status = 'CLOSED'
        """, (cutoff,))
        
        trades = cursor.fetchall()
        conn.close()
        
        if not trades:
            return {
                "overall_grade": "N/A",
                "total_trades": 0,
                "message": "No trades to analyze yet"
            }
        
        # Parse trades
        winning_trades = [t for t in trades if t[8] and t[8] > 0]  # pnl column
        losing_trades = [t for t in trades if t[8] and t[8] < 0]
        
        total_pnl = sum(t[8] for t in trades if t[8]) or 0
        win_rate = len(winning_trades) / len(trades) * 100 if trades else 0
        
        # Calculate average win/loss
        avg_win = sum(t[8] for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(t[8] for t in losing_trades) / len(losing_trades) if losing_trades else 0
        
        # Profit factor (gross wins / gross losses)
        gross_wins = sum(t[8] for t in winning_trades) if winning_trades else 0
        gross_losses = abs(sum(t[8] for t in losing_trades)) if losing_trades else 1
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else 0
        
        # Grade by confidence level accuracy
        confidence_grades = self._grade_by_confidence(trades)
        
        # Calculate overall grade
        overall_grade = self._calculate_overall_grade(
            win_rate, profit_factor, total_pnl, len(trades)
        )
        
        return {
            "overall_grade": overall_grade,
            "total_trades": len(trades),
            "winning_trades": len(winning_trades),
            "losing_trades": len(losing_trades),
            "win_rate": f"{win_rate:.1f}%",
            "total_pnl": f"${total_pnl:.2f}",
            "avg_win": f"${avg_win:.2f}",
            "avg_loss": f"${avg_loss:.2f}",
            "profit_factor": f"{profit_factor:.2f}",
            "confidence_grades": confidence_grades,
            "period_days": days
        }
    
    def _grade_by_confidence(self, trades) -> Dict:
        """Grade AI's confidence level accuracy"""
        from core.models import ConfidenceLevel
        
        grades = {}
        for conf in ConfidenceLevel:
            # Get trades with this confidence level
            conf_trades = [t for t in trades if t[9] == conf.value]  # confidence column
            
            if conf_trades:
                wins = len([t for t in conf_trades if t[8] and t[8] > 0])
                win_rate = wins / len(conf_trades) * 100
                
                # Grade: A = >70%, B = 60-70%, C = 50-60%, D = 40-50%, F = <40%
                if win_rate >= 70:
                    grade = "A"
                elif win_rate >= 60:
                    grade = "B"
                elif win_rate >= 50:
                    grade = "C"
                elif win_rate >= 40:
                    grade = "D"
                else:
                    grade = "F"
                
                grades[conf.value] = {
                    "grade": grade,
                    "win_rate": f"{win_rate:.1f}%",
                    "total_trades": len(conf_trades)
                }
        
        return grades
    
    def _calculate_overall_grade(self, win_rate: float, profit_factor: float, 
                                 total_pnl: float, trade_count: int) -> str:
        """
        Calculate overall letter grade
        Weighted scoring system:
        - Win Rate: 30%
        - Profit Factor: 30%
        - Total PnL: 20%
        - Sample Size: 20%
        """
        score = 0
        
        # Win rate component (0-30 points)
        score += min(win_rate / 100 * 30, 30)
        
        # Profit factor component (0-30 points)
        # PF of 2.0+ = 30 points, 1.5 = 22.5, 1.0 = 15, <1.0 = less
        score += min(profit_factor / 2.0 * 30, 30)
        
        # Total PnL component (0-20 points)
        # Positive PnL = points, scaled by trade count
        if total_pnl > 0:
            score += min(20, 20 * (1 - 1 / (1 + total_pnl / 100)))
        
        # Sample size component (0-20 points)
        # Need at least 20 trades for full points
        score += min(trade_count / 20 * 20, 20)
        
        # Convert score to letter grade
        if score >= 85:
            return "A"
        elif score >= 70:
            return "B"
        elif score >= 55:
            return "C"
        elif score >= 40:
            return "D"
        else:
            return "F"
    
    def get_asset_performance(self, days: int = 30) -> Dict[str, Dict]:
        """Get performance breakdown by asset"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cursor.execute("""
            SELECT asset, COUNT(*) as count, SUM(pnl) as total_pnl,
                   AVG(CASE WHEN pnl > 0 THEN pnl END) as avg_win,
                   AVG(CASE WHEN pnl < 0 THEN pnl END) as avg_loss
            FROM trades 
            WHERE timestamp >= ? AND status = 'CLOSED'
            GROUP BY asset
        """, (cutoff,))
        
        results = {}
        for row in cursor.fetchall():
            asset, count, total_pnl, avg_win, avg_loss = row
            win_rate = (avg_win / (avg_win + abs(avg_loss)) * 100) if avg_win and avg_loss else 0
            
            results[asset] = {
                "trades": count,
                "total_pnl": f"${total_pnl or 0:.2f}",
                "win_rate": f"{win_rate:.1f}%"
            }
        
        conn.close()
        return results
    
    def get_best_trading_hours(self) -> List[int]:
        """Identify which hours of the day have best performance"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT timestamp, pnl FROM trades WHERE status = 'CLOSED' AND pnl IS NOT NULL
        """)
        
        hourly_pnl = {}
        for timestamp_str, pnl in cursor.fetchall():
            try:
                dt = datetime.fromisoformat(timestamp_str)
                hour = dt.hour
                if hour not in hourly_pnl:
                    hourly_pnl[hour] = []
                hourly_pnl[hour].append(pnl or 0)
            except:
                continue
        
        # Calculate average PnL per hour
        hourly_avg = {}
        for hour, pnls in hourly_pnl.items():
            hourly_avg[hour] = sum(pnls) / len(pnls) if pnls else 0
        
        conn.close()
        
        # Sort by best performance
        sorted_hours = sorted(hourly_avg.items(), key=lambda x: x[1], reverse=True)
        return [hour for hour, avg in sorted_hours[:5]]  # Top 5 hours
