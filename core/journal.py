"""SQLite journal for Professor Mode autonomous trades and Vibe memory."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


class TradeJournalDB:
    def __init__(self, db_path: str = "trades.db"):
        self.db_path = str(Path(db_path))
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
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
                    brain_used TEXT NOT NULL DEFAULT 'UNKNOWN',
                    outcome TEXT NOT NULL
                )
                """
            )
            trade_columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
            if "brain_used" not in trade_columns:
                conn.execute("ALTER TABLE trades ADD COLUMN brain_used TEXT NOT NULL DEFAULT 'UNKNOWN'")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_vibes (
                    trade_id INTEGER PRIMARY KEY,
                    asset TEXT NOT NULL,
                    mood TEXT,
                    mood_bias TEXT,
                    liquidity_verdict TEXT,
                    closer_action TEXT,
                    market_regime TEXT,
                    volatility_state TEXT,
                    liquidity_zone TEXT,
                    confidence_penalty REAL NOT NULL DEFAULT 0,
                    aggression_mode INTEGER NOT NULL DEFAULT 0,
                    force_action INTEGER NOT NULL DEFAULT 0,
                    prompt_context TEXT,
                    memory_summary TEXT,
                    outcome TEXT NOT NULL DEFAULT 'OPEN',
                    pnl REAL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vibe_failures (
                    pattern_key TEXT PRIMARY KEY,
                    asset TEXT NOT NULL,
                    mood TEXT,
                    liquidity_verdict TEXT,
                    closer_action TEXT,
                    market_regime TEXT,
                    volatility_state TEXT,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    last_outcome TEXT NOT NULL,
                    last_pnl REAL,
                    last_failure_at TEXT NOT NULL,
                    note TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_trade_vibes_asset ON trade_vibes(asset, outcome)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_vibe_failures_asset ON vibe_failures(asset, last_failure_at DESC)"
            )
            try:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS vibe_failures_fts
                    USING fts5(asset, note, content='vibe_failures', content_rowid='rowid')
                    """
                )
            except sqlite3.OperationalError:
                pass
            conn.commit()

    def save_trade(
        self,
        coin: str,
        entry: float,
        stop_loss: float,
        ai_confidence: float,
        ai_reasoning: str,
        brain_used: str,
        outcome: str,
        timestamp: Optional[str] = None,
    ) -> int:
        ts = timestamp or datetime.utcnow().isoformat(timespec="seconds")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO trades (
                    timestamp, coin, entry, stop_loss,
                    ai_confidence, ai_reasoning, brain_used, outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    coin,
                    float(entry),
                    float(stop_loss),
                    float(ai_confidence),
                    ai_reasoning,
                    str(brain_used or "UNKNOWN").strip() or "UNKNOWN",
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

    def save_trade_vibe(
        self,
        trade_id: int,
        asset: str,
        vibe_context: Optional[dict[str, Any]] = None,
        *,
        confidence_penalty: float = 0.0,
    ) -> None:
        context = dict(vibe_context or {})
        ts = datetime.utcnow().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trade_vibes (
                    trade_id, asset, mood, mood_bias, liquidity_verdict,
                    closer_action, market_regime, volatility_state,
                    liquidity_zone, confidence_penalty, aggression_mode,
                    force_action, prompt_context, memory_summary,
                    outcome, pnl, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_id) DO UPDATE SET
                    asset = excluded.asset,
                    mood = excluded.mood,
                    mood_bias = excluded.mood_bias,
                    liquidity_verdict = excluded.liquidity_verdict,
                    closer_action = excluded.closer_action,
                    market_regime = excluded.market_regime,
                    volatility_state = excluded.volatility_state,
                    liquidity_zone = excluded.liquidity_zone,
                    confidence_penalty = excluded.confidence_penalty,
                    aggression_mode = excluded.aggression_mode,
                    force_action = excluded.force_action,
                    prompt_context = excluded.prompt_context,
                    memory_summary = excluded.memory_summary,
                    updated_at = excluded.updated_at
                """,
                (
                    int(trade_id),
                    asset,
                    context.get("mood"),
                    context.get("mood_bias"),
                    context.get("liquidity_verdict"),
                    context.get("closer_action"),
                    context.get("market_regime"),
                    context.get("volatility_state"),
                    context.get("liquidity_zone"),
                    float(confidence_penalty or 0.0),
                    1 if context.get("aggression_mode") else 0,
                    1 if context.get("force_action") else 0,
                    context.get("prompt_context", ""),
                    context.get("memory_summary", ""),
                    context.get("outcome", "OPEN"),
                    context.get("pnl"),
                    ts,
                    ts,
                ),
            )
            conn.commit()

    def update_trade_vibe_outcome(
        self,
        trade_id: int,
        outcome: str,
        *,
        pnl: Optional[float] = None,
    ) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM trade_vibes WHERE trade_id = ?",
                (int(trade_id),),
            ).fetchone()
            if not row:
                return

            ts = datetime.utcnow().isoformat(timespec="seconds")
            conn.execute(
                "UPDATE trade_vibes SET outcome = ?, pnl = ?, updated_at = ? WHERE trade_id = ?",
                (outcome, pnl, ts, int(trade_id)),
            )

            if pnl is not None and pnl < 0:
                self._remember_vibe_failure(conn, row, outcome=outcome, pnl=pnl, timestamp=ts)
            conn.commit()

    def get_vibe_penalty(
        self,
        asset: str,
        vibe_context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        context = vibe_context or {}
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM vibe_failures
                WHERE asset = ?
                ORDER BY last_failure_at DESC
                LIMIT 5
                """,
                (asset,),
            ).fetchall()

        if not rows:
            return {
                "penalty": 0.0,
                "block": False,
                "summary": "",
                "matched_patterns": 0,
            }

        total_failures = sum(int(row["failure_count"] or 0) for row in rows)
        matched_rows: list[sqlite3.Row] = []
        pattern_bonus = 0.0
        for row in rows:
            match_score = 0
            for key, column in (
                ("mood", "mood"),
                ("liquidity_verdict", "liquidity_verdict"),
                ("closer_action", "closer_action"),
                ("market_regime", "market_regime"),
                ("volatility_state", "volatility_state"),
            ):
                if context.get(key) and row[column] and str(context[key]).upper() == str(row[column]).upper():
                    match_score += 1
            if match_score:
                matched_rows.append(row)
                pattern_bonus += 4.0 + (match_score * 2.5)

        base_penalty = min(12.0, float(total_failures) * 2.5)
        penalty = min(30.0, base_penalty + pattern_bonus)
        block = penalty >= 20.0

        if matched_rows:
            head = matched_rows[0]
            summary = (
                f"Recent losing vibe on {asset}: mood={head['mood'] or 'N/A'}, "
                f"liquidity={head['liquidity_verdict'] or 'N/A'}, "
                f"regime={head['market_regime'] or 'N/A'}"
            )
        else:
            summary = f"Recent losses recorded on {asset}; tighten confidence before repeating the setup."

        return {
            "penalty": penalty,
            "block": block,
            "summary": summary,
            "matched_patterns": len(matched_rows),
        }

    def get_trade_vibe(self, trade_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM trade_vibes WHERE trade_id = ?",
                (int(trade_id),),
            ).fetchone()
        return dict(row) if row else {}

    def _remember_vibe_failure(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        outcome: str,
        pnl: float,
        timestamp: str,
    ) -> None:
        pattern_key = self._pattern_key(dict(row))
        note = (
            f"Loss on {row['asset']} with mood={row['mood'] or 'N/A'}, "
            f"liquidity={row['liquidity_verdict'] or 'N/A'}, "
            f"closer={row['closer_action'] or 'N/A'}, "
            f"regime={row['market_regime'] or 'N/A'}, "
            f"volatility={row['volatility_state'] or 'N/A'}"
        )
        conn.execute(
            """
            INSERT INTO vibe_failures (
                pattern_key, asset, mood, liquidity_verdict, closer_action,
                market_regime, volatility_state, failure_count,
                last_outcome, last_pnl, last_failure_at, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT(pattern_key) DO UPDATE SET
                failure_count = failure_count + 1,
                last_outcome = excluded.last_outcome,
                last_pnl = excluded.last_pnl,
                last_failure_at = excluded.last_failure_at,
                note = excluded.note
            """,
            (
                pattern_key,
                row["asset"],
                row["mood"],
                row["liquidity_verdict"],
                row["closer_action"],
                row["market_regime"],
                row["volatility_state"],
                outcome,
                pnl,
                timestamp,
                note,
            ),
        )

    def _pattern_key(self, row: dict[str, Any]) -> str:
        parts = [
            str(row.get("asset") or "*"),
            str(row.get("mood") or "*"),
            str(row.get("liquidity_verdict") or "*"),
            str(row.get("closer_action") or "*"),
            str(row.get("market_regime") or "*"),
            str(row.get("volatility_state") or "*"),
        ]
        return "|".join(part.upper() for part in parts)
