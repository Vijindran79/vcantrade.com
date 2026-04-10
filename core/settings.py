"""
VcanTrade AI - Persistent Settings Manager

Saves user trading settings (investment amount, lots, risk parameters) to disk.
Loads automatically on startup so user doesn't have to re-enter every time.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)


class SettingsManager:
    """Manages persistent trading settings across sessions."""
    
    SETTINGS_FILE = "trading_settings.json"
    
    DEFAULT_SETTINGS = {
        # Investment mode: "dollar" or "lots"
        "investment_mode": "dollar",
        
        # Dollar amount mode
        "investment_amount": 1000.0,  # $1000 per trade
        
        # Lots/Units mode
        "lot_size": 2.0,  # 2 lots/units per trade
        
        # Risk settings
        "take_profit_pct": 2.0,  # 2% take profit
        "stop_loss_pct": 1.0,  # 1% stop loss
        "max_daily_loss": 500.0,  # $500 max loss per day
        
        # Trading mode
        "auto_execute_threshold": 0.80,  # Auto-execute if confidence >= 0.80
        
        # Prop firm settings
        "prop_firm_enabled": True,
        "prop_firm_name": "TopStep",
        "prop_account_size": 50000.0,
    }
    
    def __init__(self):
        self.settings = self.DEFAULT_SETTINGS.copy()
        self.settings_file = Path(self.SETTINGS_FILE)
        self.load()
    
    def load(self):
        """Load settings from file or create with defaults."""
        if self.settings_file.exists():
            try:
                with open(self.settings_file, 'r') as f:
                    saved_settings = json.load(f)
                
                # Merge with defaults (in case new settings were added)
                self.settings.update(saved_settings)
                logger.info(f"✅ Settings loaded from {self.SETTINGS_FILE}")
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
            logger.info(f"✅ Settings saved to {self.SETTINGS_FILE}")
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")
    
    def get(self, key: str, default=None):
        """Get a setting value."""
        return self.settings.get(key, default)
    
    def set(self, key: str, value):
        """Update a setting and auto-save."""
        if key in self.settings:
            self.settings[key] = value
            self.save()
            logger.info(f"⚙️ Setting updated: {key} = {value}")
        else:
            logger.warning(f"Unknown setting key: {key}")
    
    def update(self, updates: Dict[str, Any]):
        """Update multiple settings at once."""
        for key, value in updates.items():
            if key in self.settings:
                self.settings[key] = value
        self.save()
        logger.info(f"⚙️ Settings batch updated: {list(updates.keys())}")
    
    def get_all(self) -> Dict[str, Any]:
        """Get all settings."""
        return self.settings.copy()
    
    def reset_to_defaults(self):
        """Reset all settings to defaults."""
        self.settings = self.DEFAULT_SETTINGS.copy()
        self.save()
        logger.info("🔄 Settings reset to defaults")
    
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
