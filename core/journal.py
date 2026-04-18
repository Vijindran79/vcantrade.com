"""SQLite journal for Professor Mode autonomous trades."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional


class TradeJournalDB:
    def __init__(self, db_path: str = "trades.db"):
        self.db_path = str(Path(db_path))
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    coin TEXT NOT NULL,
                    entry REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    ai_confidence REAL NOT NULL,
                    ai_reasoning TEXT NOT NULL,
                    outcome TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def save_trade(
        self,
        coin: str,
        entry: float,
        stop_loss: float,
        ai_confidence: float,
        ai_reasoning: str,
        outcome: str,
        timestamp: Optional[str] = None,
    ) -> int:
        ts = timestamp or datetime.utcnow().isoformat(timespec="seconds")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO trades (
                    timestamp, coin, entry, stop_loss,
                    ai_confidence, ai_reasoning, outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    coin,
                    float(entry),
                    float(stop_loss),
                    float(ai_confidence),
                    ai_reasoning,
                    outcome,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def update_outcome(self, trade_id: int, outcome: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE trades SET outcome = ? WHERE id = ?",
                (outcome, int(trade_id)),
            )
            conn.commit()
