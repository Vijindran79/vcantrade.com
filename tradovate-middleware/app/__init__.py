"""Apex / Tradovate execution middleware.

TradingView webhook -> FastAPI -> Tradovate REST. No browser, no LLM in the
order path. See README.md for the architecture and AUDIT.md (repo root) for
why the legacy Playwright surface was retired.
"""
