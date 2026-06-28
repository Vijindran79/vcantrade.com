"""
Symbol intelligence for broker/chart labels.

The goal is to understand what the chart is, even when a broker uses a custom
name like MNQ-JUN26 instead of a TradingView/Yahoo symbol.
"""

from __future__ import annotations

import re
import config
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SymbolTranslation:
    raw_symbol: str
    root: str
    instrument_name: str
    family: str
    tradingview_symbol: str
    yahoo_symbol: str
    mt5_symbol: str
    confidence: float

    def to_dict(self) -> dict:
        return asdict(self)


_MONTH_WORDS = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "SEPT",
    "OCT",
    "NOV",
    "DEC",
)

_CONTRACT_SUFFIX_RE = re.compile(
    r"([._\-/\s])(?:"
    + "|".join(_MONTH_WORDS)
    + r"|[FGHJKMNQUVXZ])(?:\d{1,4})?$",
    re.IGNORECASE,
)

_ROOTS = {
    "MNQ": ("Micro Nasdaq 100", "MICRO_NASDAQ", "CME_MINI:MNQ1!", "MNQ=F"),
    "NQ": ("E-mini Nasdaq 100", "NASDAQ", "CME_MINI:NQ1!", "NQ=F"),
    "MES": ("Micro S&P 500", "MICRO_SP500", "CME_MINI:MES1!", "MES=F"),
    "ES": ("E-mini S&P 500", "SP500", "CME_MINI:ES1!", "ES=F"),
    "MYM": ("Micro Dow", "MICRO_DOW", "CBOT_MINI:MYM1!", "MYM=F"),
    "YM": ("E-mini Dow", "DOW", "CBOT_MINI:YM1!", "YM=F"),
    "M2K": ("Micro Russell 2000", "MICRO_RUSSELL", "CME_MINI:M2K1!", "RTY=F"),
    "RTY": ("E-mini Russell 2000", "RUSSELL", "CME_MINI:RTY1!", "RTY=F"),
    "MCL": ("Micro Crude Oil", "MICRO_CRUDE", "NYMEX:MCL1!", "MCL=F"),
    "CL": ("Crude Oil", "CRUDE", "NYMEX:CL1!", "CL=F"),
    "MGC": ("Micro Gold", "MICRO_GOLD", "COMEX:MGC1!", "MGC=F"),
    "GC": ("Gold", "GOLD", "COMEX:GC1!", "GC=F"),
    "XAU": ("Gold", "GOLD", "COMEX:MGC1!", "GC=F"),
    "XAUUSD": ("Gold", "GOLD", "COMEX:MGC1!", "GC=F"),
    "SIL": ("Micro Silver", "MICRO_SILVER", "COMEX:SIL1!", "SIL=F"),
    "SI": ("Silver", "SILVER", "COMEX:SI1!", "SI=F"),
}

_CRYPTO_YAHOO_ALIASES = {
    "BTC": "BTC-USD",
    "BTCUSD": "BTC-USD",
    "BTCUSDT": "BTC-USD",
    "XBT": "BTC-USD",
    "XBTUSD": "BTC-USD",
    "ETH": "ETH-USD",
    "ETHUSD": "ETH-USD",
    "ETHUSDT": "ETH-USD",
    "SOL": "SOL-USD",
    "SOLUSD": "SOL-USD",
    "SOLUSDT": "SOL-USD",
    "XRP": "XRP-USD",
    "XRPUSD": "XRP-USD",
    "XRPUSDT": "XRP-USD",
    "DOGE": "DOGE-USD",
    "DOGEUSD": "DOGE-USD",
    "DOGEUSDT": "DOGE-USD",
    "ADA": "ADA-USD",
    "ADAUSD": "ADA-USD",
    "ADAUSDT": "ADA-USD",
    "BNB": "BNB-USD",
    "BNBUSD": "BNB-USD",
    "BNBUSDT": "BNB-USD",
    "LTC": "LTC-USD",
    "LTCUSD": "LTC-USD",
    "LTCUSDT": "LTC-USD",
    "BCH": "BCH-USD",
    "BCHUSD": "BCH-USD",
    "BCHUSDT": "BCH-USD",
    "DOT": "DOT-USD",
    "DOTUSD": "DOT-USD",
    "DOTUSDT": "DOT-USD",
    "AVAX": "AVAX-USD",
    "AVAXUSD": "AVAX-USD",
    "AVAXUSDT": "AVAX-USD",
    "LINK": "LINK-USD",
    "LINKUSD": "LINK-USD",
    "LINKUSDT": "LINK-USD",
}

_FAMILY_GROUPS = (
    {"MICRO_NASDAQ", "NASDAQ"},
    {"MICRO_SP500", "SP500"},
    {"MICRO_DOW", "DOW"},
    {"MICRO_RUSSELL", "RUSSELL"},
    {"MICRO_CRUDE", "CRUDE"},
    {"MICRO_GOLD", "GOLD"},
    {"MICRO_SILVER", "SILVER"},
)

_TEXT_HINTS = (
    (("MICRO", "NASDAQ"), "MNQ"),
    (("NASDAQ", "100", "MICRO"), "MNQ"),
    (("NASDAQ", "100"), "NQ"),
    (("MICRO", "SP500"), "MES"),
    (("MICRO", "S&P"), "MES"),
    (("S&P", "500"), "ES"),
    (("MICRO", "CRUDE"), "MCL"),
    (("CRUDE", "OIL"), "CL"),
    (("MICRO", "GOLD"), "MGC"),
    (("GOLD"), "GC"),
    (("XAU", "USD"), "XAUUSD"),
    (("XAU"), "XAU"),
)


def translate_chart_symbol(raw_symbol: str | None) -> SymbolTranslation | None:
    """Translate a chart/broker label into the bot's known instrument family."""
    raw = str(raw_symbol or "").strip()
    if not raw:
        return None

    root = _extract_root(raw)
    if not root:
        return None

    details = _ROOTS.get(root)
    if not details:
        return None

    instrument_name, family, tradingview_symbol, yahoo_symbol = details
    # Use MT5_SYMBOL_MAP from config for mt5_symbol if available
    mt5_symbol = config.MT5_SYMBOL_MAP.get(raw, raw)
    return SymbolTranslation(
        raw_symbol=raw,
        root=root,
        instrument_name=instrument_name,
        family=family,
        tradingview_symbol=tradingview_symbol,
        yahoo_symbol=yahoo_symbol,
        mt5_symbol=mt5_symbol,
        confidence=0.95 if root in _canonicalize(raw) else 0.82,
    )


def normalize_to_analysis_symbol(raw_symbol: str | None) -> str:
    """Return a canonical analysis symbol while preserving unknown labels."""
    translation = translate_chart_symbol(raw_symbol)
    if translation:
        return translation.tradingview_symbol
    return str(raw_symbol or "").strip().upper()


def normalize_yfinance_symbol(raw_symbol: str | None) -> str:
    """Return the yfinance-compatible symbol for market-data calls.

    This intentionally does not change chart/RPA broker labels. It only fixes
    market-data lookups such as BTCUSD -> BTC-USD before calling yfinance.
    """
    raw = str(raw_symbol or "").strip()
    if not raw:
        return ""

    upper = raw.upper()
    compact = _canonicalize(raw)
    if upper in _CRYPTO_YAHOO_ALIASES:
        return _CRYPTO_YAHOO_ALIASES[upper]
    if compact in _CRYPTO_YAHOO_ALIASES:
        return _CRYPTO_YAHOO_ALIASES[compact]

    if re.fullmatch(r"[A-Z]{1,4}=F\d+", upper):
        return re.sub(r"\d+$", "", upper)
    if upper in {"MGC=F", "MGCF"} or compact == "MGCF":
        return "GC=F"
    if compact in {"XAUUSD", "XAU", "GOLD"}:
        return "GC=F"

    crypto_match = re.fullmatch(r"(BTC|ETH|SOL|XRP|DOGE|ADA|BNB|LTC|BCH|DOT|AVAX|LINK)(?:USD|USDT)?", compact)
    if crypto_match:
        return f"{crypto_match.group(1)}-USD"

    if "-" in raw or raw.endswith("=F") or raw.endswith("=X") or raw.startswith("^"):
        return raw

    translation = translate_chart_symbol(raw)
    if translation:
        return translation.yahoo_symbol

    return raw


def root_matches(left: str | None, right: str | None) -> bool:
    """Return True when two labels refer to the same instrument root/family."""
    left_translation = translate_chart_symbol(left)
    right_translation = translate_chart_symbol(right)
    if left_translation and right_translation:
        if left_translation.family == right_translation.family:
            return True
        return any(
            left_translation.family in group and right_translation.family in group
            for group in _FAMILY_GROUPS
        )
    return _canonicalize(left) == _canonicalize(right)


def _extract_root(raw_symbol: str) -> str:
    canonical = _canonicalize(raw_symbol)
    symbol_part = raw_symbol.rsplit(":", 1)[-1]
    symbol_canonical = _canonicalize(symbol_part)

    for hint_words, root in _TEXT_HINTS:
        words = hint_words if isinstance(hint_words, tuple) else (hint_words,)
        if all(str(word).replace("&", "").replace(" ", "") in canonical for word in words):
            return root

    candidates = [canonical, symbol_canonical]
    without_suffix = _CONTRACT_SUFFIX_RE.sub("", symbol_part).strip()
    candidates.append(_canonicalize(without_suffix))

    tokens = re.findall(r"[A-Z0-9]+", raw_symbol.upper())
    candidates.extend(tokens)

    for candidate in candidates:
        if candidate in _ROOTS:
            return candidate

    for root in sorted(_ROOTS, key=len, reverse=True):
        if re.search(rf"(^|[^A-Z0-9]){re.escape(root)}([^A-Z0-9]|$)", raw_symbol.upper()):
            return root
        if symbol_canonical.startswith(root) and len(symbol_canonical) <= len(root) + 8:
            return root
        if root in symbol_canonical:
            return root
    return ""


def _canonicalize(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())