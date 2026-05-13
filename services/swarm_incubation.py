"""Local paper-trade incubation for medium-confidence swarm signals."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from typing import Dict, Tuple

logger = logging.getLogger(__name__)


class SwarmIncubationTracker:
    """Track 60-84% signals silently until local paper outcomes improve expectancy."""

    def __init__(self, state_path: str = "swarm_incubation_state.json"):
        self.state_path = state_path
        self._lock = threading.RLock()
        self.state = {"open": [], "expectancy": {}, "history": []}
        self._load()

    def should_incubate(self, confidence_score: float) -> bool:
        return 60.0 <= float(confidence_score or 0.0) < 85.0

    def is_high_priority(self, confidence_score: float) -> bool:
        return float(confidence_score or 0.0) >= 85.0

    def process_signal(self, signal: dict, confidence_score: float) -> Tuple[str, dict]:
        """Return route: high_priority, promoted, or incubating."""
        with self._lock:
            self._settle_open_simulations(signal)
            ticker = self._ticker(signal)
            action = self._action(signal)
            key = self._key(ticker, action)
            stats = self.state["expectancy"].get(key, {})

            if self.is_high_priority(confidence_score):
                self._save()
                return "high_priority", {"expectancy": stats}

            if self.should_incubate(confidence_score):
                if self._has_positive_expectancy(stats):
                    signal["swarm_incubated"] = True
                    signal["incubation_expectancy"] = stats
                    signal["confidence"] = max(float(signal.get("confidence", 0.0) or 0.0), 0.85)
                    self._save()
                    return "promoted", {"expectancy": stats}
                simulation = self._open_simulation(signal)
                self.state["open"].append(simulation)
                self._save()
                return "incubating", {"simulation": simulation, "expectancy": stats}

            self._save()
            return "rejected", {"reason": "below incubation floor"}

    def _settle_open_simulations(self, signal: dict) -> None:
        ticker = self._ticker(signal)
        price = float(signal.get("entry_price") or signal.get("price") or 0.0)
        if not ticker or price <= 0:
            return

        still_open = []
        for sim in self.state.get("open", []):
            if sim.get("ticker") != ticker:
                still_open.append(sim)
                continue
            action = str(sim.get("action", "")).upper()
            target = float(sim.get("take_profit") or 0.0)
            stop = float(sim.get("stop_loss") or 0.0)
            hit_target = (action == "BUY" and target > 0 and price >= target) or (
                action == "SELL" and target > 0 and price <= target
            )
            hit_stop = (action == "BUY" and stop > 0 and price <= stop) or (
                action == "SELL" and stop > 0 and price >= stop
            )
            if hit_target or hit_stop:
                outcome = "WIN" if hit_target else "LOSS"
                self._record_outcome(sim, outcome, price)
            else:
                still_open.append(sim)
        self.state["open"] = still_open[-200:]

    def _open_simulation(self, signal: dict) -> dict:
        entry = float(signal.get("entry_price") or 0.0)
        stop = float(signal.get("stop_loss") or 0.0)
        target = float(signal.get("take_profit") or 0.0)
        action = self._action(signal)
        if entry > 0 and target <= 0:
            risk = abs(entry - stop) if stop > 0 else max(entry * 0.005, 0.01)
            target = entry + risk * 1.5 if action == "BUY" else entry - risk * 1.5
        if entry > 0 and stop <= 0:
            stop = entry * 0.995 if action == "BUY" else entry * 1.005

        return {
            "id": f"sim_{self._ticker(signal)}_{action}_{int(datetime.utcnow().timestamp())}",
            "ticker": self._ticker(signal),
            "action": action,
            "entry_price": entry,
            "stop_loss": stop,
            "take_profit": target,
            "confidence": float(signal.get("confidence", 0.0) or 0.0),
            "opened_at": datetime.utcnow().isoformat(),
            "reason": str(signal.get("reason", ""))[:300],
        }

    def _record_outcome(self, sim: dict, outcome: str, exit_price: float) -> None:
        key = self._key(sim.get("ticker"), sim.get("action"))
        stats = self.state["expectancy"].setdefault(
            key, {"wins": 0, "losses": 0, "score": 0.0, "updated_at": ""}
        )
        if outcome == "WIN":
            stats["wins"] = int(stats.get("wins", 0)) + 1
            stats["score"] = float(stats.get("score", 0.0)) + 1.0
        else:
            stats["losses"] = int(stats.get("losses", 0)) + 1
            stats["score"] = float(stats.get("score", 0.0)) - 1.0
        stats["updated_at"] = datetime.utcnow().isoformat()
        history_row = dict(sim)
        history_row.update({"outcome": outcome, "exit_price": exit_price, "closed_at": stats["updated_at"]})
        self.state["history"].append(history_row)
        self.state["history"] = self.state["history"][-500:]
        logger.info("[INCUBATION] %s %s %s at %.4f | stats=%s", sim.get("ticker"), sim.get("action"), outcome, exit_price, stats)

    def _has_positive_expectancy(self, stats: Dict[str, object]) -> bool:
        wins = int(stats.get("wins", 0) or 0)
        losses = int(stats.get("losses", 0) or 0)
        score = float(stats.get("score", 0.0) or 0.0)
        return wins >= 2 and score > 0 and wins > losses

    def _load(self) -> None:
        if not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                self.state.update(loaded)
        except Exception as exc:
            logger.warning("[INCUBATION] Could not load state: %s", exc)

    def _save(self) -> None:
        try:
            tmp_path = f"{self.state_path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(self.state, handle, indent=2, sort_keys=True)
            os.replace(tmp_path, self.state_path)
        except Exception as exc:
            logger.warning("[INCUBATION] Could not save state: %s", exc)

    def _ticker(self, signal: dict) -> str:
        return str(signal.get("ticker", "") or "").upper().strip()

    def _action(self, signal: dict) -> str:
        return str(signal.get("action", "") or "").upper().strip()

    def _key(self, ticker: object, action: object) -> str:
        return f"{str(ticker or '').upper()}::{str(action or '').upper()}"
