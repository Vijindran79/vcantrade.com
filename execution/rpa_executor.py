"""
VcanTrade AI - RPA Executor

Handles trade execution via keyboard hotkeys and mouse clicks.
Uses calibrated coordinates from calibration.json for mouse-based execution.
"""

import logging
import time
from typing import Optional

import config
from core.models import TradeRecord, SignalAction
from core.calibration import CalibrationManager

logger = logging.getLogger(__name__)


class RPAExecutor:
    """
    Robotic Process Automation executor for trade execution.
    Uses keyboard hotkeys as primary method, calibrated mouse clicks as fallback.
    """

    def __init__(self):
        self.use_hotkeys = config.USE_HOTKEYS
        self.hotkey_buy = config.HOTKEY_BUY
        self.hotkey_sell = config.HOTKEY_SELL
        self.hotkey_close = config.HOTKEY_CLOSE

        # Load calibrated coordinates
        self.calibration = CalibrationManager()

        # Only import pyautogui if needed
        self.pyautogui = None
        if not self.use_hotkeys:
            try:
                import pyautogui

                self.pyautogui = pyautogui
                pyautogui.FAILSAFE = True
                logger.info("PyAutoGUI initialized for mouse-based execution")
            except ImportError:
                logger.warning("PyAutoGUI not installed — mouse execution disabled")

    def execute_trade(self, trade: TradeRecord) -> bool:
        """Execute a trade via RPA. Returns True if successful."""
        if config.DRY_RUN:
            logger.info(f"[DRY RUN] Would execute {trade.action.value} {trade.asset}")
            return True

        try:
            if trade.action == SignalAction.BUY:
                return self._execute_buy(trade)
            elif trade.action == SignalAction.SELL:
                return self._execute_sell(trade)
            elif trade.action == SignalAction.CLOSE:
                return self._execute_close(trade)
            else:
                logger.warning(f"Unknown action: {trade.action}")
                return False
        except Exception as e:
            logger.error(f"RPA execution failed: {e}")
            return False

    def _execute_buy(self, trade: TradeRecord) -> bool:
        logger.info(f"Executing BUY for {trade.asset}")
        if self.use_hotkeys:
            return self._send_hotkey(self.hotkey_buy)
        return self._mouse_click_with_input(
            "buy_button", trade, fill_sl=True, fill_tp=True
        )

    def _execute_sell(self, trade: TradeRecord) -> bool:
        logger.info(f"Executing SELL for {trade.asset}")
        if self.use_hotkeys:
            return self._send_hotkey(self.hotkey_sell)
        return self._mouse_click_with_input(
            "sell_button", trade, fill_sl=True, fill_tp=True
        )

    def _execute_close(self, trade: TradeRecord) -> bool:
        logger.info(f"Executing CLOSE for {trade.asset}")
        if self.use_hotkeys:
            return self._send_hotkey(self.hotkey_close)
        return self._mouse_click("close_button")

    def _send_hotkey(self, hotkey: str) -> bool:
        """Send keyboard hotkey combination. Format: '<ctrl>+b'."""
        try:
            parts = hotkey.lower().replace("<", "").replace(">", "").split("+")
            if len(parts) == 2:
                modifier, key = parts
                from pyautogui import hotkey

                hotkey(modifier, key)
                logger.info(f"Hotkey sent: {hotkey}")
                time.sleep(0.5)
                return True
            else:
                logger.error(f"Invalid hotkey format: {hotkey}")
                return False
        except ImportError:
            logger.error("PyAutoGUI not installed — cannot send hotkeys")
            return False
        except Exception as e:
            logger.error(f"Failed to send hotkey {hotkey}: {e}")
            return False

    def _mouse_click(self, point_name: str) -> bool:
        """Click a calibrated screen position."""
        if not self.pyautogui:
            logger.error("Mouse execution not available")
            return False

        x, y = self.calibration.get_coordinate(point_name)
        if (x, y) == (0, 0):
            logger.error(f"No calibration for '{point_name}' — cannot click")
            return False

        self.pyautogui.click(x, y)
        logger.info(f"Clicked {point_name} at ({x}, {y})")
        time.sleep(0.3)
        return True

    def _mouse_click_with_input(
        self,
        button_point: str,
        trade: TradeRecord,
        fill_sl: bool = True,
        fill_tp: bool = True,
    ) -> bool:
        """
        Click a button, then optionally fill SL/TP input fields.
        Uses calibrated coordinates for all positions.
        """
        if not self.pyautogui:
            logger.error("Mouse execution not available")
            return False

        # Click the buy/sell button
        if not self._mouse_click(button_point):
            return False

        time.sleep(0.5)  # Wait for order dialog to open

        # Fill Stop Loss
        if fill_sl and trade.stop_loss:
            sl_x, sl_y = self.calibration.get_coordinate("sl_input")
            if (sl_x, sl_y) != (0, 0):
                self.pyautogui.click(sl_x, sl_y)
                time.sleep(0.2)
                self.pyautogui.hotkey("ctrl", "a")
                time.sleep(0.1)
                self.pyautogui.write(str(trade.stop_loss))
                logger.info(f"Filled Stop Loss: {trade.stop_loss}")
                time.sleep(0.3)

        # Fill Take Profit
        if fill_tp and trade.take_profit:
            tp_x, tp_y = self.calibration.get_coordinate("tp_input")
            if (tp_x, tp_y) != (0, 0):
                self.pyautogui.click(tp_x, tp_y)
                time.sleep(0.2)
                self.pyautogui.hotkey("ctrl", "a")
                time.sleep(0.1)
                self.pyautogui.write(str(trade.take_profit))
                logger.info(f"Filled Take Profit: {trade.take_profit}")
                time.sleep(0.3)

        # Fill Lot Size if calibrated
        lot_x, lot_y = self.calibration.get_coordinate("lot_size_input")
        if (lot_x, lot_y) != (0, 0):
            self.pyautogui.click(lot_x, lot_y)
            time.sleep(0.2)
            self.pyautogui.hotkey("ctrl", "a")
            time.sleep(0.1)
            self.pyautogui.write("0.01")  # Default minimum lot size
            logger.info("Filled Lot Size: 0.01")
            time.sleep(0.3)

        # Click Confirm
        self._mouse_click("confirm_button")
        logger.info(f"Trade executed: {trade.action.value} {trade.asset}")
        return True
