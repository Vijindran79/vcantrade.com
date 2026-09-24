from datetime import date

import pytest

from app.symbols import (
    SymbolResolutionError,
    resolve,
)


def test_strips_continuous_suffix():
    spec = resolve("NQ1!", today=date(2026, 9, 22))
    assert spec.root == "NQ"
    assert spec.tradovate_symbol.startswith("NQ")
    assert spec.tick_size == 0.25
    assert spec.tick_value == 20.0


def test_micro_nq():
    spec = resolve("MNQ1!", today=date(2026, 9, 22))
    assert spec.root == "MNQ"
    assert spec.tick_value == 2.0


def test_explicit_month_code_passes_through():
    spec = resolve("NQZ6", today=date(2026, 9, 22))
    assert spec.tradovate_symbol == "NQZ6"


def test_front_month_rolls_after_december():
    dec = resolve("ES1!", today=date(2026, 12, 1))
    mar = resolve("ES1!", today=date(2027, 1, 15))
    assert dec.tradovate_symbol.endswith("Z6")
    assert mar.tradovate_symbol.endswith("H7")


def test_september_contract_rolls_to_december_after_expiry():
    late_sep = resolve("NQ1!", today=date(2026, 9, 22))
    assert late_sep.tradovate_symbol == "NQZ6"
    early_sep = resolve("NQ1!", today=date(2026, 9, 1))
    assert early_sep.tradovate_symbol == "NQU6"


def test_unknown_symbol_raises():
    with pytest.raises(SymbolResolutionError):
        resolve("FOOBAR1!")


def test_case_insensitive():
    assert resolve("nq1!").root == "NQ"
    assert resolve("  NQ1!  ").root == "NQ"


def test_gold_and_oil():
    assert resolve("GC1!").root == "GC"
    assert resolve("CL1!").root == "CL"
    assert resolve("MGC1!").tick_value == 1.0
