"""
VcaniTrade Diagnostic - shows why no trades are happening.

Run: python diagnose.py

This is a non-destructive read-only check. It will tell you, in plain English,
which guard is blocking your bot from taking a Gold trade right now.
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 70)
print("  VcaniTrade Diagnostic - 'Why is the bot not trading?'")
print("=" * 70)

# 1. config.py sanity check
import config

print("\n[1] Config snapshot")
print(f"    ACTIVE_SYMBOLS       = {config.ACTIVE_SYMBOLS}")
print(f"    ACTIVE_WATCHLIST     = {config.ACTIVE_WATCHLIST}")
print(f"    MIN_CONFIDENCE       = {config.MIN_CONFIDENCE_THRESHOLD}  (entry gate)")
print(f"    SWARM_CONFIDENCE     = {config.SWARM_CONFIDENCE_THRESHOLD}  (brain gate)")
print(f"    HAWK_BLINK           = {config.HAWK_BLINK_CONFIDENCE}  (visual blink)")
print(f"    SCAN_INTERVAL        = {config.SCAN_INTERVAL}s")
print(f"    DRY_RUN              = {config.DRY_RUN}")
print(f"    KILL_SWITCH          = {config.KILL_SWITCH}")

gold_aliases = ("MGC1!", "MGC=F", "MGC", "GC=F", "GC", "GOLD", "XAUUSD")
gold_in_list = [s for s in config.ACTIVE_SYMBOLS if s.upper() in gold_aliases]
if not gold_in_list:
    print("\n  [!!] PROBLEM: Gold (MGC1!/GC=F/GOLD) is NOT in ACTIVE_SYMBOLS.")
    print("      The bot is configured to scan a different symbol — add it to the")
    print("      watchlist in the dashboard, or edit trading_settings.json.")
else:
    print(f"    Gold present as:    {gold_in_list}")

# 2. trading_settings.json sanity
print("\n[2] trading_settings.json")
try:
    import json
    with open("trading_settings.json", encoding="utf-8") as f:
        settings = json.load(f)
    print(f"    session_watchlist    = {settings.get('session_watchlist')}")
    print(f"    auto_execute_threshold = {settings.get('auto_execute_threshold')}")
    print(f"    max_daily_loss       = {settings.get('max_daily_loss')}")
    print(f"    prop_firm_mode       = {settings.get('prop_firm_mode')}")
    if "MGC1!" in settings.get("session_watchlist", []):
        print("    [OK] Gold IS in the session watchlist")
    else:
        print("    [!!] Gold is NOT in the session watchlist")
except Exception as e:
    print(f"    [!!] Cannot read trading_settings.json: {e}")

# 3. Data feed sanity (try to fetch Gold)
print("\n[3] Data feed for Gold (MGC1! -> MGC=F)")
try:
    from core.data_feed import data_feed
    bars = data_feed.get_bars("MGC1!", count=20, use_cache=True)
    if bars:
        last = bars[-1]
        print(f"    [OK] data_feed returned {len(bars)} bars; last close = {last.get('close')}")
    else:
        print("    [!!] data_feed returned NO bars for MGC1!")
        print("        This means the scanner will skip Gold every cycle.")
        print("        Likely cause: no internet, or yfinance rate-limited,")
        print("        or the symbol is muted / not in YFINANCE_SYMBOL_MAP.")
except Exception as e:
    print(f"    [!!] data_feed.get_bars(MGC1!) raised: {e}")

# 4. Scanner dry-run
print("\n[4] Scanner dry-run for MGC1!")
try:
    from core.scanner import Scanner
    sc = Scanner()
    sc.tickers = ["MGC1!"]
    sig = sc._scan_ticker("MGC1!")
    if sig:
        print(f"    [OK] Scanner found a signal: {sig.signal_type} strength={sig.strength:.2f}")
    else:
        print("    [INFO] Scanner did NOT find a signal right now. This is normal if the")
        print("            market is choppy or not trending. The scanner needs:")
        print("              - EMA9 > EMA21 (bullish) OR EMA9 < EMA21 (bearish)")
        print("              - 3+ rising/falling candles in last 5")
        print("              - MACD crossing in trade direction")
        print("              - RSI in safe zone (40-65 for buy, 35-60 for sell)")
        print("              - 5-minute higher-timeframe agrees with 1-minute")
        print("              - Same direction for 2 consecutive cycles")
        print("            If all of these align, you should see a [TARGET] entry in the dashboard.")
except Exception as e:
    print(f"    [!!] Scanner raised: {e}")

# 5. Browser agent + TV window sanity
print("\n[5] TradingView window detection")
try:
    import pygetwindow as gw
    tv_windows = [w for w in gw.getAllWindows() if "tradingview" in w.title.lower()]
    if tv_windows:
        print(f"    [OK] Found {len(tv_windows)} TradingView window(s):")
        for w in tv_windows[:3]:
            print(f"          - {w.title[:80]}")
    else:
        print("    [!!] No TradingView window is currently open.")
        print("        The bot needs a TradingView tab open with Chrome remote-debugging")
        print("        enabled (port 9222) so the browser agent can connect.")
except Exception as e:
    print(f"    [INFO] pygetwindow: {e}")

# 6. Mode check
print("\n[6] Runtime mode")
try:
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from main import VcanTradeEngine
    eng = VcanTradeEngine.__new__(VcanTradeEngine)
    eng.current_mode = "?"
    print("    (Engine mode is stored in dashboard state — check the dashboard's mode toggle.)")
    print("    If you're in TEACHER mode, signals will appear as 'approval required' but")
    print("    will NOT execute. Switch to AUTONOMOUS to let the bot actually trade.")
except Exception:
    print("    (Can't introspect Qt state from here — check the dashboard's mode button.)")

print("\n" + "=" * 70)
print("  End of diagnostic.  Most likely causes, in order of frequency:")
print("  1) Market is choppy right now — no MOMENTUM signal aligned.")
print("  2) Bot is in TEACHER mode (not AUTONOMOUS).")
print("  3) Data feed is empty (no internet, yfinance blocked, MT5 not connected).")
print("  4) TradingView window is not open / not connected via CDP (port 9222).")
print("  5) Stability requirement not yet met (need 2 consecutive same-direction cycles).")
print("  6) Confidence is below 0.65 — that gets the signal filtered in the dispatcher.")
print("=" * 70)
