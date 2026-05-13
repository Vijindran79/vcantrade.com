"""
VcaniTrade AI - MetaTrader 5 Executor
Routes trade execution to MT5 when config.EXECUTION_MODE == "MT5".
"""

import logging
from typing import Optional

import config
from core.symbol_mapper import translate_chart_symbol

logger = logging.getLogger(__name__)


class MT5Executor:
    """
    Wrapper around MetaTrader5 Python library.
    Handles initialization, symbol mapping, and market-order execution.
    """

    def __init__(self):
        self.initialized = False
        self._mt5 = None

    def _lazy_import(self):
        """Lazy import to avoid hard dependency when UI mode is used."""
        if self._mt5 is None:
            try:
                import MetaTrader5 as mt5
                self._mt5 = mt5
            except ImportError:
                logger.error(
                    "[MT5] MetaTrader5 library not installed. Run: pip install MetaTrader5"
                )
                raise
        return self._mt5

    def initialize(self) -> bool:
        """Connect to the local MT5 terminal."""
        if self.initialized:
            return True
        try:
            mt5 = self._lazy_import()
            if not mt5.initialize():
                err = mt5.last_error()
                logger.error("[MT5] initialize() failed: %s", err)
                return False
            self.initialized = True
            account = mt5.account_info()
            if account:
                logger.info(
                    "[MT5] Connected | Account: %s | Balance: %.2f %s",
                    account.login,
                    account.balance,
                    account.currency,
                )
            else:
                logger.warning("[MT5] Connected but no account info retrieved")
            return True
        except Exception as e:
            logger.error("[MT5] Initialization error: %s", e)
            return False

    def shutdown(self):
        """Disconnect from MT5."""
        if self.initialized and self._mt5:
            try:
                self._mt5.shutdown()
                logger.info("[MT5] Disconnected")
            except Exception as e:
                logger.warning("[MT5] Shutdown error: %s", e)
            finally:
                self.initialized = False

    def execute_trade(
        self,
        symbol: str,
        action: str,
        volume: Optional[float] = None,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
    ) -> bool:
        """
        Send a protected market order to MT5.

        Args:
            symbol: TradingView-format symbol (e.g. CME_MINI:MNQ1!)
            action: "BUY" or "SELL"
            volume: Lot size (defaults to config.MT5_VOLUME)
            stop_loss: Broker price for the protective stop.
            take_profit: Optional broker price for the profit target.
        """
        if not self.initialized:
            if not self.initialize():
                return False

        mt5 = self._mt5
        mt5_symbol = self._map_symbol(symbol)

        # Ensure symbol is visible in MarketWatch
        if not mt5.symbol_select(mt5_symbol, True):
            logger.error("[MT5] Symbol %s not available in MarketWatch", mt5_symbol)
            return False

        tick = mt5.symbol_info_tick(mt5_symbol)
        if tick is None:
            logger.error("[MT5] Failed to get tick for %s", mt5_symbol)
            return False

        symbol_info = mt5.symbol_info(mt5_symbol)
        digits = int(getattr(symbol_info, "digits", 5) or 5) if symbol_info else 5

        lot = volume or float(getattr(config, "MT5_VOLUME", 0.1))
        sl = round(float(stop_loss or 0.0), digits)
        tp = round(float(take_profit or 0.0), digits)
        require_stop = bool(getattr(config, "MT5_REQUIRE_PROTECTIVE_STOP", True))

        if action.upper() == "BUY":
            order_type = mt5.ORDER_TYPE_BUY
            price = tick.ask
            if require_stop and sl <= 0:
                logger.error("[MT5] Rejected BUY %s: protective stop_loss is required", mt5_symbol)
                return False
            if sl > 0 and sl >= price:
                logger.error("[MT5] Rejected BUY %s: stop_loss %.5f must be below ask %.5f", mt5_symbol, sl, price)
                return False
            if tp > 0 and tp <= price:
                logger.error("[MT5] Rejected BUY %s: take_profit %.5f must be above ask %.5f", mt5_symbol, tp, price)
                return False
        else:
            order_type = mt5.ORDER_TYPE_SELL
            price = tick.bid
            if require_stop and sl <= 0:
                logger.error("[MT5] Rejected SELL %s: protective stop_loss is required", mt5_symbol)
                return False
            if sl > 0 and sl <= price:
                logger.error("[MT5] Rejected SELL %s: stop_loss %.5f must be above bid %.5f", mt5_symbol, sl, price)
                return False
            if tp > 0 and tp >= price:
                logger.error("[MT5] Rejected SELL %s: take_profit %.5f must be below bid %.5f", mt5_symbol, tp, price)
                return False

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": mt5_symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "deviation": 10,
            "magic": 234000,
            "comment": "VcaniTrade AI",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        if sl > 0:
            request["sl"] = sl
        if tp > 0:
            request["tp"] = tp

        result = mt5.order_send(request)
        if result is None:
            logger.error("[MT5] order_send returned None for %s", mt5_symbol)
            return False

        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(
                "[MT5] %s %s %.2f lots @ %.2f | SL=%s TP=%s | Ticket: %s",
                action.upper(),
                mt5_symbol,
                lot,
                price,
                sl or "none",
                tp or "none",
                result.order,
            )
            return True
        else:
            logger.error(
                "[MT5] Order failed: %s (retcode=%s)", result.comment, result.retcode
            )
            return False

    def _map_symbol(self, tv_symbol: str) -> str:
        """
        Map known symbols to MT5 names while allowing broker-specific labels.
        """
        raw_symbol = str(tv_symbol or "").strip()
        if hasattr(config, "MT5_SYMBOL_MAP") and raw_symbol in config.MT5_SYMBOL_MAP:
            return config.MT5_SYMBOL_MAP[raw_symbol]

        mapping = {
            "CME_MINI:MNQ1!": "MNQ1!",
            "CME_MINI:MES1!": "MES1!",
            "NYMEX:MCL1!": "MCL1!",
        }
        if raw_symbol in mapping:
            return mapping[raw_symbol]

        translation = translate_chart_symbol(raw_symbol)
        if translation:
            return translation.mt5_symbol

        return raw_symbol

    def _reverse_map_symbol(self, mt5_symbol: str) -> str:
        """Map MT5 symbol names back to TradingView format."""
        reverse = {
            "MNQ1!": "CME_MINI:MNQ1!",
            "MES1!": "CME_MINI:MES1!",
            "MCL1!": "NYMEX:MCL1!",
        }
        return reverse.get(mt5_symbol, mt5_symbol)

    def get_positions(self) -> list[dict]:
        """Return current open positions from MT5."""
        if not self.initialized:
            if not self.initialize():
                return []
        try:
            mt5 = self._mt5
            raw_positions = mt5.positions_get()
            if raw_positions is None:
                return []
            positions = []
            for p in raw_positions:
                positions.append({
                    "ticket": p.ticket,
                    "symbol": self._reverse_map_symbol(p.symbol),
                    "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                    "volume": p.volume,
                    "open_price": p.price_open,
                    "current_price": p.price_current,
                    "profit": p.profit,
                    "swap": p.swap,
                    "comment": p.comment,
                })
            return positions
        except Exception as e:
            logger.error("[MT5] Failed to get positions: %s", e)
            return []

    def get_account_info(self) -> Optional[dict]:
        """Return current account info from MT5."""
        if not self.initialized:
            if not self.initialize():
                return None
        try:
            info = self._mt5.account_info()
            if info is None:
                return None
            return {
                "login": info.login,
                "balance": info.balance,
                "equity": info.equity,
                "margin": info.margin,
                "currency": info.currency,
            }
        except Exception as e:
            logger.error("[MT5] Failed to get account info: %s", e)
            return None
