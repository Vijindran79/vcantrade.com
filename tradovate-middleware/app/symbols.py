"""TradingView symbol -> Tradovate contract resolution.

TradingView continuous futures use the `1!` suffix (NQ1!, MNQ1!, ES1!, GC1!).
Tradovate requires an explicit month code (NQZ5, NQH6, ...) or a root symbol
that the broker resolves to the front month.

Strategy:
  1. If the user supplied an explicit Tradovate contract (e.g. NQZ5), pass through.
  2. Otherwise map the TV root to a Tradovate root and resolve the front month
     from the CME quarterly cycle (H, M, U, Z) based on today's date.
  3. Never guess. If the root is unknown, raise — the order is rejected.

The front-month rule below is the standard "roll on the 1st of the month
preceding expiry" approximation. For production, override with an explicit
contract map in SYMBOL_OVERRIDES and update quarterly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


CME_QUARTERLY = ["H", "M", "U", "Z"]  # Mar, Jun, Sep, Dec
CME_MONTHLY = ["F", "G", "H", "J", "K", "M", "N", "Q", "U", "V", "X", "Z"]

TV_TO_TRADOVATE_ROOT: dict[str, str] = {
    "NQ": "NQ",
    "MNQ": "MNQ",
    "ES": "ES",
    "MES": "MES",
    "YM": "YM",
    "MYM": "MYM",
    "RTY": "RTY",
    "M2K": "M2K",
    "GC": "GC",
    "MGC": "MGC",
    "CL": "CL",
    "QM": "QM",
    "ZB": "ZB",
    "ZN": "ZN",
    "6E": "6E",
    "6B": "6B",
    "6J": "6J",
}

QUARTERLY_ROOTS = {"NQ", "MNQ", "ES", "MES", "YM", "MYM", "RTY", "M2K", "ZB", "ZN"}

SYMBOL_OVERRIDES: dict[str, str] = {}


@dataclass(frozen=True)
class ContractSpec:
    tv_symbol: str
    tradovate_symbol: str
    root: str
    tick_size: float
    tick_value: float


TICK_SPECS: dict[str, tuple[float, float]] = {
    "NQ": (0.25, 20.0),
    "MNQ": (0.25, 2.0),
    "ES": (0.25, 12.5),
    "MES": (0.25, 1.25),
    "YM": (1.0, 5.0),
    "MYM": (1.0, 0.5),
    "RTY": (0.10, 5.0),
    "M2K": (0.10, 0.5),
    "GC": (0.10, 10.0),
    "MGC": (0.10, 1.0),
    "CL": (0.01, 10.0),
    "QM": (0.025, 2.5),
    "ZB": (1.0 / 32.0, 31.25),
    "ZN": (1.0 / 64.0, 15.625),
    "6E": (0.0001, 12.5),
    "6B": (0.0001, 6.25),
    "6J": (0.0000001, 6.25),
}


class SymbolResolutionError(ValueError):
    pass


def _strip_tv_suffix(sym: str) -> str:
    s = sym.strip().upper()
    for suffix in ("1!", "2!", "3!"):
        if s.endswith(suffix):
            return s[: -len(suffix)]
    return s


def _front_month_code(root: str, today: date) -> str:
    """Return the CME month code + year digit for the current front month.

    Approximation: a contract is considered expired once we pass the 15th of
    its delivery month (most CME equity/index futures stop trading on the 3rd
    Friday). For exact roll dates, populate SYMBOL_OVERRIDES.
    """
    quarterly = root in QUARTERLY_ROOTS
    cycle = CME_QUARTERLY if quarterly else CME_MONTHLY
    month_for = (
        (lambda code: {"H": 3, "M": 6, "U": 9, "Z": 12}[code])
        if quarterly
        else (lambda code: CME_MONTHLY.index(code) + 1)
    )

    year = today.year
    for offset in (0, 1):
        candidate_year = year + offset
        y_digit = candidate_year % 10
        for code in cycle:
            m = month_for(code)
            if (candidate_year, m, 15) > (today.year, today.month, today.day):
                return f"{code}{y_digit}"
    return f"{cycle[0]}{(year + 1) % 10}"


def resolve(tv_symbol: str, today: date | None = None) -> ContractSpec:
    today = today or date.today()
    sym = tv_symbol.strip().upper()

    if sym in SYMBOL_OVERRIDES:
        tv_symbol = SYMBOL_OVERRIDES[sym]
        sym = tv_symbol

    root = _strip_tv_suffix(sym)

    if root not in TV_TO_TRADOVATE_ROOT:
        for known in TV_TO_TRADOVATE_ROOT:
            if sym.startswith(known) and len(sym) == len(known) + 2:
                root = known
                break

    if root not in TV_TO_TRADOVATE_ROOT:
        raise SymbolResolutionError(
            f"Unknown symbol '{tv_symbol}'. Add it to TV_TO_TRADOVATE_ROOT or SYMBOL_OVERRIDES."
        )

    tv_root = TV_TO_TRADOVATE_ROOT[root]

    if len(sym) == len(root) + 2 and sym[len(root)].isalpha() and sym[-1].isdigit():
        tradovate_symbol = sym
    else:
        tradovate_symbol = f"{tv_root}{_front_month_code(root, today)}"

    tick_size, tick_value = TICK_SPECS[root]
    return ContractSpec(
        tv_symbol=tv_symbol,
        tradovate_symbol=tradovate_symbol,
        root=root,
        tick_size=tick_size,
        tick_value=tick_value,
    )
