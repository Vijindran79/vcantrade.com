"""VcanTrade AI - Analytics Reporter with EOD Report Generation"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import sqlite3

logger = logging.getLogger(__name__)

class AnalyticsReporter:
    """Generates End-of-Day (EOD) reports and analytics."""
    
    def __init__(self, db_manager):
        self.db = db_manager
    
    def generate_eod_report(self) -> Dict:
        """Generate comprehensive EOD report."""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            # Get today's trades
            today = datetime.now().date()
            cursor.execute("""
                SELECT action, asset, entry_price, exit_price, pnl, timestamp 
                FROM trades 
                WHERE date(timestamp) = ?
            """, (today.isoformat(),))
            trades = cursor.fetchall()
            
            # Calculate stats
            total_trades = len(trades)
            wins = sum(1 for t in trades if t[4] and t[4] > 0)
            losses = sum(1 for t in trades if t[4] and t[4] <= 0)
            win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
            total_pnl = sum(t[4] for t in trades if t[4]) if trades else 0
            
            # Get autopsies if table exists
            autopsies = []
            try:
                cursor.execute("SELECT * FROM autopsies WHERE date(timestamp) = ?", (today.isoformat(),))
                autopsies = cursor.fetchall()
            except sqlite3.OperationalError:
                logger.warning("Autopsies table not found")
            
            report = {
                "date": today.isoformat(),
                "total_trades": total_trades,
                "wins": wins,
                "losses": losses,
                "win_rate": win_rate,
                "total_pnl": total_pnl,
                "trades": trades,
                "autopsies": autopsies
            }
            
            conn.close()
            logger.info(f"EOD Report generated: {total_trades} trades, {win_rate:.1f}% win rate, ${total_pnl:.2f} P&L")
            return report
            
        except Exception as e:
            logger.error(f"Error generating EOD report: {e}")
            raise
    
    def get_portfolio_stats(self) -> Dict:
        """Get current portfolio statistics."""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("SELECT SUM(pnl) FROM trades")
            total_pnl = cursor.fetchone()[0] or 0
            
            cursor.execute("SELECT COUNT(*) FROM trades WHERE pnl > 0")
            wins = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM trades WHERE pnl <= 0")
            losses = cursor.fetchone()[0]
            
            total_trades = wins + losses
            win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
            
            conn.close()
            
            return {
                "total_pnl": total_pnl,
                "wins": wins,
                "losses": losses,
                "win_rate": win_rate,
                "total_trades": total_trades
            }
        except Exception as e:
            logger.error(f"Error getting portfolio stats: {e}")
            return {"total_pnl": 0, "wins": 0, "losses": 0, "win_rate": 0, "total_trades": 0}
