import os
import time
import logging
from datetime import datetime
import config

logger = logging.getLogger(__name__)

class NinjaTraderSpecialist:
    """Handles NinjaTrader ATI order generation for multiple target accounts."""
    
    def __init__(self):
        self.ati_order_dir = config.ATI_ORDER_DIR
        os.makedirs(self.ati_order_dir, exist_ok=True)
        logger.info("[NINJATRADER] Initialized — ATI dir: %s, accounts: %s", 
                   self.ati_order_dir, config.TARGET_ACCOUNTS)

    def on_signal(self, signal: dict):
        """
        Process trading signal and generate ATI order files for all target accounts.
        
        Signal format:
            {
                "ticker": str,  # Internal ticker (e.g., "NQM6")
                "action": str,  # "BUY" or "SELL"
                "quantity": int, # Number of contracts
                "order_type": str,  # "MARKET" (default) or "LIMIT"
                "price": float,  # 0 for market orders
            }
        """
        ticker = signal.get("ticker")
        action = signal.get("action", "").upper()
        quantity = signal.get("quantity", 1)
        order_type = signal.get("order_type", "MARKET").upper()
        price = signal.get("price", 0)

        if not ticker or action not in ("BUY", "SELL"):
            logger.error("[NINJATRADER] Invalid signal: %s", signal)
            return

        # Map internal ticker to NinjaTrader contract name
        nt_ticker = config.NINJATRADER_TICKER_MAP.get(ticker, ticker)
        if nt_ticker != ticker:
            logger.debug("[NINJATRADER] Mapped ticker %s → %s", ticker, nt_ticker)

        # Generate ATI order for each target account
        for account in config.TARGET_ACCOUNTS:
            self._write_ati_order(
                account=account,
                instrument=nt_ticker,
                action=action,
                quantity=quantity,
                order_type=order_type,
                price=price
            )

    def _write_ati_order(self, account: str, instrument: str, action: str, 
                         quantity: int, order_type: str, price: float):
        """Write a single ATI order file for a specific account."""
        try:
            # Unique filename to prevent collisions
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            safe_account = account.replace("-", "_").replace(" ", "_")
            filename = f"order_{timestamp}_{safe_account}.txt"
            filepath = os.path.join(self.ati_order_dir, filename)

            # NinjaTrader ATI order format:
            # ORDER,ACCOUNT,INSTRUMENT,ACTION,QUANTITY,ORDER_TYPE,PRICE,TIMEINFORCE,OCAGROUP,COMMENT
            order_line = f"ORDER,{account},{instrument},{action},{quantity},{order_type},{price},DAY,,,"

            with open(filepath, "w") as f:
                f.write(order_line + "\n")

            logger.info("[NINJATRADER] Wrote ATI order: %s → %s %s %sx %s", 
                       filepath, account, action, quantity, instrument)

        except Exception as e:
            logger.error("[NINJATRADER] Failed to write ATI order for %s: %s", 
                        account, e, exc_info=True)
