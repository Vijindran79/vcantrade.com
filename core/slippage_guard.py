"""
Slippage Guard - Prevents trades when price deviation exceeds threshold
"""

from typing import Optional
import logging

logger = logging.getLogger(__name__)


class SlippageGuard:
    """Prevents trade execution when slippage exceeds configured threshold."""
    
    def __init__(self, max_slippage_pct: float = 0.5):
        self.max_slippage_pct = max_slippage_pct
        logger.info("[SLIPPAGE] Guard initialized with max slippage: %.2f%%", max_slippage_pct)
    
    def check_slippage(
        self,
        entry_price: float,
        current_price: float,
        ticker: str = ""
    ) -> tuple[bool, float]:
        """Check if slippage is within acceptable range.
        
        Returns (is_ok, slippage_pct).
        """
        if entry_price <= 0 or current_price <= 0:
            return True, 0.0  # Cannot calculate, allow trade
        
        slippage_pct = abs(current_price - entry_price) / entry_price * 100
        is_ok = slippage_pct <= self.max_slippage_pct
        
        if not is_ok:
            logger.warning(
                "[SLIPPAGE] Rejected %s: slippage %.2f%% exceeds %.2f%% (entry=%.2f, current=%.2f)",
                ticker, slippage_pct, self.max_slippage_pct, entry_price, current_price
            )
        return is_ok, slippage_pct