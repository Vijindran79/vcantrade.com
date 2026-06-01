"""
VcanTrade AI - Institutional Position Sizer
============================================
Kelly Criterion, ATR-based volatility sizing, and risk-parity sizing.
"""
import logging
import math
from typing import Optional, Dict

logger = logging.getLogger(__name__)


class PositionSizer:
    """
    Institutional-grade position sizing.
    Combines Kelly Criterion (edge) with ATR (volatility) for optimal sizing.
    """

    def __init__(self, account_balance: float = 50000.0,
                 max_risk_per_trade_pct: float = 1.0,
                 max_position_pct: float = 10.0):
        self.account_balance = account_balance
        self.max_risk_pct = max_risk_per_trade_pct / 100.0
        self.max_position_pct = max_position_pct / 100.0
        # Micro futures multipliers
        self.multipliers = {
            "MNQ": 2.0, "MES": 5.0, "MCL": 1.0, "MGC": 1.0,
            "NQ": 20.0, "ES": 50.0, "CL": 1000.0, "GC": 100.0,
        }

    # ------------------------------------------------------------------
    # Kelly Criterion
    # ------------------------------------------------------------------
    def kelly_fraction(self, win_rate: float, avg_win: float, avg_loss: float,
                       kelly_multiplier: float = 0.5) -> float:
        """
        Kelly Criterion: f* = (p*b - q) / b
        where p=win_rate, q=1-win_rate, b=win/loss ratio.
        Half-Kelly (0.5x) is recommended for live trading — reduces volatility.
        """
        if avg_loss == 0 or win_rate <= 0 or win_rate >= 1:
            return 0.0
        b = abs(avg_win / avg_loss)
        q = 1 - win_rate
        kelly = (win_rate * b - q) / b
        # Never bet more than full Kelly, never negative
        kelly = max(0.0, min(kelly, 1.0))
        return kelly * kelly_multiplier

    # ------------------------------------------------------------------
    # ATR-Based Volatility Sizing
    # ------------------------------------------------------------------
    def atr_size(self, symbol: str, entry_price: float, stop_loss: float,
                 atr: float, confidence: float = 1.0) -> int:
        """
        Size position so that 1 ATR move = max_risk_pct of account.
        Volatility-adjusted — smaller size in choppy markets.
        """
        if stop_loss <= 0 or entry_price <= 0:
            return 1
        risk_per_unit = abs(entry_price - stop_loss)
        if risk_per_unit <= 0:
            return 1

        # Risk amount in dollars
        risk_dollars = self.account_balance * self.max_risk_pct * confidence
        # Contracts = risk $ / (stop distance * point value)
        mult = self._get_multiplier(symbol)
        contracts = risk_dollars / (risk_per_unit * mult)

        # Cap at max position size
        max_contracts = self._max_contracts_for_symbol(symbol, entry_price)
        return max(1, min(int(contracts), max_contracts))

    # ------------------------------------------------------------------
    # Kelly + ATR Combined (The Institutional Way)
    # ------------------------------------------------------------------
    def optimal_size(self, symbol: str, entry_price: float, stop_loss: float,
                     atr: float, win_rate: float, avg_win: float,
                     avg_loss: float, confidence: float = 1.0) -> Dict:
        """
        Combine Kelly (edge) with ATR (volatility). Take the smaller size
        to respect both. This is what top prop firms do.
        """
        kelly_f = self.kelly_fraction(win_rate, avg_win, avg_loss)
        atr_contracts = self.atr_size(symbol, entry_price, stop_loss, atr, confidence)

        # Kelly-based contracts
        kelly_dollars = self.account_balance * kelly_f * confidence
        mult = self._get_multiplier(symbol)
        kelly_contracts = int(kelly_dollars / (entry_price * mult)) if entry_price > 0 else 1

        # Take minimum — never exceed either limit
        final_size = max(1, min(atr_contracts, kelly_contracts))
        max_contracts = self._max_contracts_for_symbol(symbol, entry_price)
        final_size = min(final_size, max_contracts)

        return {
            "contracts": final_size,
            "kelly_fraction": round(kelly_f, 4),
            "kelly_contracts": kelly_contracts,
            "atr_contracts": atr_contracts,
            "method": "kelly+atr_conservative",
            "risk_dollars": round(self.account_balance * self.max_risk_pct * confidence, 2),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _get_multiplier(self, symbol: str) -> float:
        for k, v in self.multipliers.items():
            if k in symbol.upper():
                return v
        return 1.0

    def _max_contracts_for_symbol(self, symbol: str, price: float) -> int:
        max_pos_value = self.account_balance * self.max_position_pct
        mult = self._get_multiplier(symbol)
        return max(1, int(max_pos_value / (price * mult)))

    def update_balance(self, new_balance: float):
        self.account_balance = new_balance


# Singleton
sizer = PositionSizer()
