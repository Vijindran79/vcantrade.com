import unittest
import sys
from unittest import mock

import pandas as pd

for module_name in ("core", "core.models", "core.calibration"):
    module = sys.modules.get(module_name)
    if module is not None and not hasattr(module, "__path__"):
        sys.modules.pop(module_name, None)

from core.profit_lock import ProfitLock
from core.trade_executor import TradeExecutor


class TestProfitLock(unittest.TestCase):
    def setUp(self):
        self.lock = ProfitLock(starting_balance=10000.0)

    def test_break_even_moves_mnq_stop_to_entry_plus_two_dollars(self):
        position = {
            "asset": "NQ=F",
            "side": "BUY",
            "entry": 100.0,
            "sl_price": 95.0,
            "quantity": 1.0,
            "break_even_locked": False,
        }

        update = self.lock.check_break_even(position, current_price=107.5)

        self.assertIsNotNone(update)
        self.assertAlmostEqual(update["new_stop"], 101.0)
        self.assertTrue(update["break_even_locked"])

    def test_structural_trailing_uses_latest_higher_low_from_closed_one_minute_candles(self):
        candles = pd.DataFrame(
            {
                "Low": [96.0, 98.0, 101.0, 104.0],
                "High": [101.0, 103.0, 106.0, 108.0],
            }
        )
        position = {
            "asset": "NQ=F",
            "side": "BUY",
            "entry": 100.0,
            "sl_price": 95.0,
            "quantity": 1.0,
        }

        update = self.lock.calculate_structural_trailing_stop(
            position=position,
            recent_candles=candles,
            current_price=107.0,
        )

        self.assertIsNotNone(update)
        self.assertEqual(update["new_stop"], 101.0)
        self.assertIn("higher-low", update["reason"])


class TestTradeExecutorMetadata(unittest.TestCase):
    def test_exchange_order_submission_uses_standard_fields_only(self):
        exchange_client = mock.Mock()
        exchange_client.create_order.return_value = {"id": "abc123"}
        executor = TradeExecutor(exchange_client=exchange_client)

        executor.place_smart_entry(
            symbol="NQ=F",
            side="BUY",
            quantity=1.0,
            levels={"supports": [100.0], "resistances": [110.0]},
            fallback_price=101.0,
        )

        _args, kwargs = exchange_client.create_order.call_args
        self.assertEqual(
            set(kwargs.keys()),
            {"symbol", "side", "order_type", "quantity", "price"},
        )
        self.assertNotIn("comment", kwargs)
        self.assertNotIn("metadata", kwargs)


if __name__ == "__main__":
    unittest.main()
