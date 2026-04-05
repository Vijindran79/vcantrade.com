"""
VcaniTrade AI - Grader Module (Post-Trade Autopsy)

After every closed trade, two agents independently grade the decision:
- Technical Sniper: Was the entry/exit technically sound?
- Risk Manager: Was the risk/reward justified? Were there hidden dangers?

The grades (A-F) and plain-English explanations are logged to SQLite
so the user can review win/loss reasoning over time.
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests

import config
from core.models import ConfidenceLevel, TradeAutopsy, TradeRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt Templates
# ---------------------------------------------------------------------------

PROMPT_SNIPER_GRADE = """\
You are the TECHNICAL SNIPER grading a completed trade. Evaluate ONLY the
technical quality of the entry and exit.

Trade Details:
- Asset: {asset}
- Direction: {action}
- Entry Price: {entry_price}
- Exit Price: {exit_price}
- Stop Loss: {stop_loss}
- Take Profit: {take_profit}
- PnL: {pnl}
- AI Reason at Entry: {ai_reason}

Grade the entry quality from A to F:
- A: Perfect entry at support/resistance, clean exit at target
- B: Good entry, minor slippage or early/late exit
- C: Acceptable entry but ignored a key level
- D: Poor entry — chased price or entered against structure
- F: Terrible entry — completely wrong direction or no setup

Respond in STRICT JSON:
{{
  "grade": "A|B|C|D|F",
  "explanation": "<max 150 chars explaining the technical grade>",
  "lessons": ["lesson1", "lesson2"]
}}
"""

PROMPT_RISK_GRADE = """\
You are the RISK MANAGER grading a completed trade. Evaluate ONLY the risk
management quality — position sizing, risk/reward ratio, and whether the
trade respected the safety rules.

Trade Details:
- Asset: {asset}
- Direction: {action}
- Entry Price: {entry_price}
- Exit Price: {exit_price}
- Stop Loss: {stop_loss}
- Take Profit: {take_profit}
- PnL: {pnl}
- Confidence at Entry: {confidence}

Grade the risk management from A to F:
- A: Excellent risk/reward (>2:1), proper sizing, stopped out cleanly or hit TP
- B: Good risk management, minor issues with sizing or timing
- C: Acceptable but risk was borderline or position too large
- D: Poor risk management — ignored stop, oversized, or held too long
- F: Dangerous — no stop loss, massive overexposure, or revenge trading

Respond in STRICT JSON:
{{
  "grade": "A|B|C|D|F",
  "explanation": "<max 150 chars explaining the risk grade>",
  "lessons": ["lesson1", "lesson2"]
}}
"""


# ---------------------------------------------------------------------------
# Grader
# ---------------------------------------------------------------------------


class Grader:
    """
    Grades individual trades post-close using LLM-powered analysis.
    Stores autopsies in SQLite for longitudinal review.
    """

    def __init__(self, db_path: str = "vcanitrade_ledger.db"):
        self.db_path = db_path
        self.base_url = config.OLLAMA_BASE_URL
        self.model = config.OLLAMA_MODEL
        self.timeout = config.LLM_TIMEOUT

    def _get_connection(self):
        """Get database connection"""
        return sqlite3.connect(self.db_path)

    # -- public API ----------------------------------------------------------

    def autopsy_trade(self, trade: TradeRecord) -> TradeAutopsy:
        """
        Perform a full post-trade autopsy on a closed trade.
        Returns a TradeAutopsy with grades and explanations.
        """
        logger.info(f"Starting post-trade autopsy: {trade.id}")

        ollama_ready = self._is_ollama_available()

        if not ollama_ready:
            return self._mock_autopsy(trade)

        # Parallel-grade from both agents
        sniper_result = self._grade_with_llm(
            PROMPT_SNIPER_GRADE.format(
                asset=trade.asset,
                action=trade.action.value,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price or trade.entry_price,
                stop_loss=trade.stop_loss or "N/A",
                take_profit=trade.take_profit or "N/A",
                pnl=trade.pnl or 0,
                ai_reason=trade.ai_reason,
            ),
            agent="Technical Sniper",
        )

        risk_result = self._grade_with_llm(
            PROMPT_RISK_GRADE.format(
                asset=trade.asset,
                action=trade.action.value,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price or trade.entry_price,
                stop_loss=trade.stop_loss or "N/A",
                take_profit=trade.take_profit or "N/A",
                pnl=trade.pnl or 0,
                confidence=trade.confidence.value,
            ),
            agent="Risk Manager",
        )

        # Combine into final autopsy
        autopsy = TradeAutopsy(
            trade_id=trade.id,
            asset=trade.asset,
            action=trade.action.value,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price or trade.entry_price,
            pnl=trade.pnl or 0,
            grade=self._combine_grades(sniper_result["grade"], risk_result["grade"]),
            technical_grade=sniper_result["grade"],
            risk_grade=risk_result["grade"],
            explanation=self._combine_explanations(
                sniper_result["explanation"], risk_result["explanation"]
            ),
            lessons=list(
                set(sniper_result.get("lessons", []) + risk_result.get("lessons", []))
            ),
        )

        # Persist to database
        self._save_autopsy(autopsy)
        logger.info(
            f"Autopsy complete: {trade.asset} — Grade: {autopsy.grade} "
            f"(Technical: {autopsy.technical_grade}, Risk: {autopsy.risk_grade})"
        )

        return autopsy

    # -- grading helpers -----------------------------------------------------

    def _grade_with_llm(self, prompt: str, agent: str) -> Dict:
        """Send grading prompt to Ollama and parse response."""
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "{}")
            parsed = json.loads(raw)
            return {
                "grade": parsed.get("grade", "C"),
                "explanation": parsed.get("explanation", "No explanation provided."),
                "lessons": parsed.get("lessons", []),
            }
        except Exception as e:
            logger.error(f"[{agent}] Grading failed: {e}")
            return {
                "grade": "C",
                "explanation": f"Grading failed — defaulting to neutral.",
                "lessons": ["Review this trade manually."],
            }

    def _combine_grades(self, technical: str, risk: str) -> str:
        """Combine two letter grades into an overall grade."""
        grade_values = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}
        avg = (grade_values.get(technical, 2) + grade_values.get(risk, 2)) / 2

        if avg >= 3.5:
            return "A"
        elif avg >= 2.5:
            return "B"
        elif avg >= 1.5:
            return "C"
        elif avg >= 0.5:
            return "D"
        else:
            return "F"

    def _combine_explanations(self, tech_exp: str, risk_exp: str) -> str:
        """Merge two agent explanations into one coherent summary."""
        return f"Technical: {tech_exp} Risk: {risk_exp}"

    # -- mock autopsy (offline fallback) -------------------------------------

    def _mock_autopsy(self, trade: TradeRecord) -> TradeAutopsy:
        """Generate a realistic mock autopsy without Ollama."""
        pnl = trade.pnl or 0
        won = pnl > 0

        # Technical grade
        if won:
            tech_grade = "A" if pnl > 50 else "B"
            tech_exp = "Clean entry at support. Price moved in your favor as expected."
            tech_lessons = ["This setup has a proven edge — keep tracking it."]
        else:
            tech_grade = "C" if trade.stop_loss else "D"
            tech_exp = (
                "Entry was technically valid but the move reversed against you."
                if trade.stop_loss
                else "Entry lacked a clear technical setup. No stop loss was set."
            )
            tech_lessons = [
                "Wait for stronger confirmation before entering.",
                "Always set a stop loss — never trade without one.",
            ]

        # Risk grade
        if trade.stop_loss and trade.take_profit:
            rr = (
                abs(
                    (trade.take_profit - trade.entry_price)
                    / (trade.entry_price - trade.stop_loss)
                )
                if trade.entry_price != trade.stop_loss
                else 0
            )
            risk_grade = "A" if rr >= 2 else "B" if rr >= 1.5 else "C"
            risk_exp = (
                f"Risk/reward was {rr:.1f}:1. "
                f"{'Excellent ratio.' if rr >= 2 else 'Acceptable but could be tighter.'}"
            )
            risk_lessons = ["Maintain this risk/reward discipline."]
        else:
            risk_grade = "D"
            risk_exp = "No stop loss or take profit defined. This is dangerous."
            risk_lessons = ["Always define SL and TP before entering a trade."]

        overall = self._combine_grades(tech_grade, risk_grade)

        autopsy = TradeAutopsy(
            trade_id=trade.id,
            asset=trade.asset,
            action=trade.action.value,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price or trade.entry_price,
            pnl=pnl,
            grade=overall,
            technical_grade=tech_grade,
            risk_grade=risk_grade,
            explanation=f"Technical: {tech_exp} Risk: {risk_exp}",
            lessons=list(set(tech_lessons + risk_lessons)),
        )

        self._save_autopsy(autopsy)
        return autopsy

    # -- database operations -------------------------------------------------

    def _save_autopsy(self, autopsy: TradeAutopsy):
        """Save autopsy result to SQLite"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS autopsies (
                trade_id TEXT PRIMARY KEY,
                asset TEXT,
                action TEXT,
                entry_price REAL,
                exit_price REAL,
                pnl REAL,
                overall_grade TEXT,
                technical_grade TEXT,
                risk_grade TEXT,
                explanation TEXT,
                lessons TEXT,
                timestamp TEXT
            )
        """)

        cursor.execute(
            """
            INSERT OR REPLACE INTO autopsies (
                trade_id, asset, action, entry_price, exit_price, pnl,
                overall_grade, technical_grade, risk_grade, explanation,
                lessons, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                autopsy.trade_id,
                autopsy.asset,
                autopsy.action,
                autopsy.entry_price,
                autopsy.exit_price,
                autopsy.pnl,
                autopsy.grade,
                autopsy.technical_grade,
                autopsy.risk_grade,
                autopsy.explanation,
                json.dumps(autopsy.lessons),
                autopsy.timestamp.isoformat(),
            ),
        )

        conn.commit()
        conn.close()
        logger.info(f"Autopsy saved: {autopsy.trade_id} — Grade {autopsy.grade}")

    def get_autopsy_history(self, limit: int = 50) -> List[Dict]:
        """Retrieve recent autopsies"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT trade_id, asset, action, entry_price, exit_price, pnl,
                   overall_grade, technical_grade, risk_grade, explanation,
                   lessons, timestamp
            FROM autopsies
            ORDER BY timestamp DESC
            LIMIT ?
        """,
            (limit,),
        )

        results = []
        for row in cursor.fetchall():
            results.append(
                {
                    "trade_id": row[0],
                    "asset": row[1],
                    "action": row[2],
                    "entry_price": row[3],
                    "exit_price": row[4],
                    "pnl": row[5],
                    "overall_grade": row[6],
                    "technical_grade": row[7],
                    "risk_grade": row[8],
                    "explanation": row[9],
                    "lessons": json.loads(row[10]) if row[10] else [],
                    "timestamp": row[11],
                }
            )

        conn.close()
        return results

    def get_grade_distribution(self) -> Dict[str, int]:
        """Count of each grade letter across all autopsies"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT overall_grade, COUNT(*) FROM autopsies GROUP BY overall_grade
        """)

        distribution = {}
        for grade, count in cursor.fetchall():
            distribution[grade] = count

        conn.close()
        return distribution

    # -- legacy report card (kept for backward compatibility) ----------------

    def generate_report_card(self, days: int = 30) -> Dict:
        """Generate comprehensive performance report"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cursor.execute(
            """
            SELECT * FROM trades WHERE timestamp >= ? AND status = 'CLOSED'
        """,
            (cutoff,),
        )

        trades = cursor.fetchall()
        conn.close()

        if not trades:
            return {
                "overall_grade": "N/A",
                "total_trades": 0,
                "message": "No trades to analyze yet",
            }

        winning_trades = [t for t in trades if t[8] and t[8] > 0]
        losing_trades = [t for t in trades if t[8] and t[8] < 0]

        total_pnl = sum(t[8] for t in trades if t[8]) or 0
        win_rate = len(winning_trades) / len(trades) * 100 if trades else 0

        avg_win = (
            sum(t[8] for t in winning_trades) / len(winning_trades)
            if winning_trades
            else 0
        )
        avg_loss = (
            sum(t[8] for t in losing_trades) / len(losing_trades)
            if losing_trades
            else 0
        )

        gross_wins = sum(t[8] for t in winning_trades) if winning_trades else 0
        gross_losses = abs(sum(t[8] for t in losing_trades)) if losing_trades else 1
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else 0

        confidence_grades = self._grade_by_confidence(trades)
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
            "period_days": days,
        }

    def _grade_by_confidence(self, trades) -> Dict:
        """Grade AI's confidence level accuracy"""
        grades = {}
        for conf in ConfidenceLevel:
            conf_trades = [t for t in trades if t[9] == conf.value]

            if conf_trades:
                wins = len([t for t in conf_trades if t[8] and t[8] > 0])
                win_rate = wins / len(conf_trades) * 100

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
                    "total_trades": len(conf_trades),
                }

        return grades

    def _calculate_overall_grade(
        self, win_rate: float, profit_factor: float, total_pnl: float, trade_count: int
    ) -> str:
        """Calculate overall letter grade"""
        score = 0
        score += min(win_rate / 100 * 30, 30)
        score += min(profit_factor / 2.0 * 30, 30)
        if total_pnl > 0:
            score += min(20, 20 * (1 - 1 / (1 + total_pnl / 100)))
        score += min(trade_count / 20 * 20, 20)

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

    def _is_ollama_available(self) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=2)
            return resp.status_code == 200
        except Exception:
            return False
