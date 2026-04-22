"""VcanTrade AI - Analytics Reporter with EOD Report Generation"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_DB = "trades.db"


class AnalyticsReporter:
    """Generates End-of-Day (EOD) reports and analytics."""

    def __init__(self, db_manager=None):
        self.db = db_manager

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Return a sqlite3 connection, using db_manager or a direct path."""
        if self.db is not None and hasattr(self.db, "get_connection"):
            return self.db.get_connection()
        conn = sqlite3.connect(_DEFAULT_DB, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_eod_report(self) -> str:
        """Generate comprehensive EOD report and return it as a formatted string."""
        today = datetime.now().date()
        try:
            conn = self._connect()
            cursor = conn.cursor()

            # Try the journal schema first (coin / entry / ai_confidence / outcome)
            try:
                cursor.execute(
                    "SELECT coin, entry, ai_confidence, outcome, timestamp "
                    "FROM trades WHERE date(timestamp) = ?",
                    (today.isoformat(),),
                )
                raw_trades = cursor.fetchall()
                wins = sum(1 for t in raw_trades if str(t[3]).upper() in ("WIN", "PROFIT"))
                losses = sum(1 for t in raw_trades if str(t[3]).upper() in ("LOSS", "LOSS"))
                total_trades = len(raw_trades)
                win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
                total_pnl = 0.0  # not stored in this schema
                trade_lines = [
                    f"  {t[1]} {t[0]}  conf={t[2]:.2f}  outcome={t[3]}"
                    for t in raw_trades
                ]
            except sqlite3.OperationalError:
                total_trades = wins = losses = 0
                win_rate = total_pnl = 0.0
                trade_lines = []

            conn.close()
        except Exception as exc:
            logger.error("Error generating EOD report: %s", exc)
            total_trades = wins = losses = 0
            win_rate = total_pnl = 0.0
            trade_lines = []

        lines = [
            f"VcanTrade AI - End-of-Day Report  {today}"
            "=" * 44,
            f"Total Trades : {total_trades}",
            f"Wins         : {wins}",
            f"Losses       : {losses}",
            f"Win Rate     : {win_rate:.1f}%",
            f"Net P&L      : ${total_pnl:+.2f}",
            "",
        ]
        if trade_lines:
            lines += ["Trade Log:", *trade_lines]
        else:
            lines.append("No trades recorded today.")

        return "\n".join(lines)

    def save_report(self, directory: str = ".") -> str:
        """Generate and save a plain-text EOD report. Returns the file path."""
        text = self.generate_eod_report()
        today = datetime.now().strftime("%Y%m%d")
        path = Path(directory) / f"eod_report_{today}.txt"
        path.write_text(text, encoding="utf-8")
        logger.info("EOD report saved: %s", path)
        return str(path)

    def save_html_report(self, directory: str = ".") -> str:
        """Generate and save an HTML EOD report. Returns the file path."""
        text = self.generate_eod_report()
        today = datetime.now().strftime("%Y%m%d")
        path = Path(directory) / f"eod_report_{today}.html"
        rows = "".join(
            f"<p style='margin:2px 0'>{line}</p>"
            for line in text.splitlines()
        )
        html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>VcanTrade EOD Report</title>"
            "<style>body{font-family:monospace;background:#0D1117;color:#E6EDF3;"
            "padding:24px;}h1{color:#00D4FF;}</style></head>"
            "<body><h1>VcanTrade AI - EOD Report</h1>"
            f"{rows}</body></html>"
        )
        path.write_text(html, encoding="utf-8")
        logger.info("HTML EOD report saved: %s", path)
        return str(path)

    def get_portfolio_stats(self) -> Dict:
        """Get current portfolio statistics."""
        try:
            conn = self._connect()
            cursor = conn.cursor()

            try:
                cursor.execute("SELECT outcome FROM trades")
                rows = cursor.fetchall()
                wins = sum(1 for r in rows if str(r[0]).upper() in ("WIN", "PROFIT"))
                losses = sum(1 for r in rows if str(r[0]).upper() in ("LOSS",))
                total_trades = wins + losses
                win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
            except sqlite3.OperationalError:
                wins = losses = total_trades = 0
                win_rate = 0.0

            conn.close()
            return {
                "total_pnl": 0.0,
                "wins": wins,
                "losses": losses,
                "win_rate": win_rate,
                "total_trades": total_trades,
            }
        except Exception as exc:
            logger.error("Error getting portfolio stats: %s", exc)
            return {"total_pnl": 0, "wins": 0, "losses": 0, "win_rate": 0, "total_trades": 0}

