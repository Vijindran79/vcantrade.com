import logging

logger = logging.getLogger(__name__)

class SlippageGuard:
    """Hardened execution slippage protection matrix for institutional execution tracks."""
    def __init__(self, max_allowed_slippage_points: float = 3.0):
        self.max_slippage = max_allowed_slippage_points
        logger.info(f"[SLIPPAGE-INITIALIZED] Slippage protection live. Max limit: {self.max_slippage} points.")

    def verify_execution_spread(self, requested_price: float, current_market_price: float, ticker: str) -> bool:
        """Verifies market fill deviation stays within protective metric tolerances."""
        try:
            actual_deviation = abs(requested_price - current_market_price)
            if actual_deviation > self.max_slippage:
                logger.warning(f"[SLIPPAGE-REJECT] {ticker} execution denied. Deviation {round(actual_deviation, 2)} exceeds limit ({self.max_slippage}).")
                return False
            
            logger.info(f"[SLIPPAGE-PASS] {ticker} deviation verified clean at {round(actual_deviation, 2)} points.")
            return True
        except Exception as e:
            logger.error(f"[SLIPPAGE-ERROR] Critical failure in execution spread math: {str(e)}")
            return False