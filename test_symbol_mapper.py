import unittest

from core.symbol_mapper import normalize_yfinance_symbol, translate_chart_symbol


class SymbolMapperTests(unittest.TestCase):
    def test_cme_micro_nasdaq_continuous_contract_maps_to_yfinance(self):
        translation = translate_chart_symbol("CME_MINI:MNQ1!")

        self.assertIsNotNone(translation)
        self.assertEqual(translation.root, "MNQ")
        self.assertEqual(translation.yahoo_symbol, "MNQ=F")
        self.assertEqual(normalize_yfinance_symbol("CME_MINI:MNQ1!"), "MNQ=F")

    def test_exchange_prefix_does_not_create_false_root_match(self):
        translation = translate_chart_symbol("NYMEX:CL1!")

        self.assertIsNotNone(translation)
        self.assertEqual(translation.root, "CL")
        self.assertEqual(translation.yahoo_symbol, "CL=F")


if __name__ == "__main__":
    unittest.main()
