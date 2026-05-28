"""
Persistent trade journal stored as JSON.
Hardened fallback Trade Journal ledger to record system metric positions safely.
"""

import logging
import json
import os
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class TradeJournal:
    """Hardened Trade Journal ledger to record system metric positions safely."""
    
    def __init__(self, filepath: str = "trade_ledger.json"):
        self.filepath = filepath
        self.trades: List[Dict] = []
        self.rejected_signals: List[Dict] = []
        self.load()
        logger.info("[JOURNAL-INITIALIZED] Trade Journal logging engine online.")
    
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

    def log_trade(self, trade_data: dict):
        """Records position metrics cleanly into local background states."""
        ticker = trade_data.get("ticker", "UNKNOWN")
        action = trade_data.get("action", "HOLD")
        logger.info(f"[JOURNAL-RECORD] Position update tracked for {ticker} | Action: {action}")
        # Optionally add to trades list if relevant
        # self.add_trade(trade_data)
