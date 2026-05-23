"""
VcanTrade AI - Persistent Settings Manager

Saves user trading settings (investment amount, lots, risk parameters) to disk.
Loads automatically on startup so user doesn't have to re-enter every time.
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)


class SettingsManager:
    """Manages persistent trading settings across sessions."""
    
    SETTINGS_FILE = "trading_settings.json"
    
    DEFAULT_SETTINGS = {
        # Investment mode: "dollar" or "lots"
        "investment_mode": "dollar",

        # Session watchlist preset loaded into the dashboard at startup.
        # This is operator-controlled and can be replaced before each session.
        "session_watchlist": [],
        
        # Dollar amount mode
        "investment_amount": 1000.0,  # $1000 per trade
        
        # Lots/Units mode
        "lot_size": 2.0,  # 2 lots/units per trade
        "max_lots": 2.0,  # Hard cap for any single trade size
        "human_latency": True,  # Stealth click pacing for RPA execution
        "prop_firm_mode": False,  # Force strict prop-firm limits when enabled
        "auto_risk_enabled": True,  # Use structural stop/target data instead of fixed % inputs
        
        # Legacy fixed-target settings (kept for backwards compatibility only).
        # Autonomous risk mode ignores these values at runtime.
        "take_profit_pct": 0.0,
        "stop_loss_pct": 0.0,
        "max_daily_loss": 500.0,  # $500 max loss per day
        
        # Trading mode
        "auto_execute_threshold": 0.80,  # Auto-execute if confidence >= 0.80
        
        # Prop firm settings
        "prop_firm_enabled": True,
        "prop_firm_name": "TopStep",
        "prop_account_size": 50000.0,
    }

    FUTURES_ALIASES = {
        "ES1": "ES=F",
        "ES": "ES=F",
        "MES1": "ES=F",
        "MES": "ES=F",
        "NQ1": "NQ=F",
        "NQ": "NQ=F",
        "MNQ1": "NQ=F",
        "MNQ": "NQ=F",
        "GC1": "GC=F",
        "GC": "GC=F",
        "MGC1": "GC=F",
        "MGC": "GC=F",
        "CL1": "CL=F",
        "CL": "CL=F",
        "MCL1": "CL=F",
        "MCL": "CL=F",
        "CLM26": "CL=F",
        "CLM26!": "CL=F",
        "NYMEX:CLM26!": "CL=F",
        "NYMEX:CL1!": "CL=F",
        "YM1": "YM=F",
        "YM": "YM=F",
        "MYM1": "YM=F",
        "MYM": "YM=F",
        "RTY1": "RTY=F",
        "RTY": "RTY=F",
        "M2K1": "RTY=F",
        "M2K": "RTY=F",
        "SI1": "SI=F",
        "SI": "SI=F",
        "HG1": "HG=F",
        "HG": "HG=F",
    }
    
    def __init__(self):
        self.settings = self.DEFAULT_SETTINGS.copy()
        self.settings_file = Path(self.SETTINGS_FILE)
        self.load()

    def normalize_ticker(self, ticker: str) -> str:
        normalized = str(ticker or "").strip().upper()
        if not normalized:
            return ""
        # Reject accidental numeric values such as stop-loss / take-profit prices
        # before they can poison the persisted watchlist.
        if not re.search(r"[A-Z]", normalized):
            return ""
        # Guard against accidental UI suffixes such as NQ=F1, which yfinance
        # rejects even though NQ=F is valid.
        if re.fullmatch(r"[A-Z]{1,4}=F\d+", normalized):
            normalized = re.sub(r"\d+$", "", normalized)
        return self.FUTURES_ALIASES.get(normalized, normalized)

    def normalize_watchlist(self, watchlist) -> list[str]:
        normalized = []
        seen = set()
        for ticker in watchlist or []:
            value = self.normalize_ticker(ticker)
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        return normalized

    def _normalize_settings(self):
        self.settings["session_watchlist"] = self.normalize_watchlist(
            self.settings.get("session_watchlist", [])
        )
    
    def load(self):
        """Load settings from file or create with defaults."""
        if self.settings_file.exists():
            try:
                with open(self.settings_file, 'r') as f:
                    saved_settings = json.load(f)
                
                # Merge with defaults (in case new settings were added)
                self.settings.update(saved_settings)
                normalized_snapshot = dict(self.settings)
                self._normalize_settings()
                if self.settings != normalized_snapshot:
                    self.save()
                    logger.info("[BROOM] Settings normalized and re-saved to remove invalid watchlist entries")
                logger.info(f"[OK] Settings loaded from {self.SETTINGS_FILE}")
            except Exception as e:
                logger.error(f"Failed to load settings: {e}")
                self.settings = self.DEFAULT_SETTINGS.copy()
        else:
            logger.info(f"No settings file found - creating with defaults")
            self.save()
    
    def save(self):
        """Save current settings to file."""
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(self.settings, f, indent=2)
            logger.info(f"[OK] Settings saved to {self.SETTINGS_FILE}")
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")
    
    def get(self, key: str, default=None):
        """Get a setting value."""
        return self.settings.get(key, default)
    
    def set(self, key: str, value):
        """Update a setting and auto-save."""
        if key in self.settings:
            if key == "session_watchlist":
                value = self.normalize_watchlist(value)
            self.settings[key] = value
            self.save()
            logger.info(f"[GEAR] Setting updated: {key} = {value}")
        else:
            logger.warning(f"Unknown setting key: {key}")
    
    def update(self, updates: Dict[str, Any]):
        """Update multiple settings at once."""
        for key, value in updates.items():
            if key in self.settings:
                if key == "session_watchlist":
                    value = self.normalize_watchlist(value)
                self.settings[key] = value
        self.save()
        logger.info(f"[GEAR] Settings batch updated: {list(updates.keys())}")
    
    def get_all(self) -> Dict[str, Any]:
        """Get all settings."""
        return self.settings.copy()
    
    def reset_to_defaults(self):
        """Reset all settings to defaults."""
        self.settings = self.DEFAULT_SETTINGS.copy()
        self.save()
        logger.info("[REFRESH] Settings reset to defaults")
    
    def get_investment_for_trade(self, entry_price: float) -> tuple:
        """
        Calculate investment amount and quantity based on current mode.
        
        Returns:
            (amount, quantity) tuple
        """
        if self.settings["investment_mode"] == "lots":
            # User specified lots/units
            quantity = self.settings["lot_size"]
            amount = quantity * entry_price
            return amount, quantity
        else:
            # User specified dollar amount
            amount = self.settings["investment_amount"]
            quantity = amount / entry_price if entry_price > 0 else 0
            return amount, quantity


# Global instance
settings_manager = SettingsManager()
