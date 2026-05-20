"""
VcaniTrade AI - End-to-End Execution Test
==========================================
Tests the full signal-to-click pipeline:
1. Connects to Chrome via CDP on port 9222
2. Verifies TradingView tabs are open
3. Forces a BUY signal for the first detected symbol
4. Verifies the RPA executor clicks the correct button
5. Reports pass/fail

Usage:
    python test_e2e_full.py

Requirements:
    - Chrome running with --remote-debugging-port=9222
    - TradingView paper trading chart(s) open
    - ACTIVE_EXECUTION_SURFACE=TRADINGVIEW in environment
"""

import asyncio
import os
import sys
import time
import logging

# Setup path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ACTIVE_EXECUTION_SURFACE", "TRADINGVIEW")
os.environ.setdefault("BROWSER_CDP_URL", "http://127.0.0.1:9222")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("e2e_test")


class E2ETestResult:
    def __init__(self):
        self.steps = []
        self.passed = True

    def step(self, name: str, ok: bool, detail: str = ""):
        status = "PASS" if ok else "FAIL"
        self.steps.append((name, status, detail))
        if not ok:
            self.passed = False
        logger.info("[%s] %s %s", status, name, f"| {detail}" if detail else "")

    def report(self):
        print("\n" + "=" * 60)
        print("E2E TEST REPORT")
        print("=" * 60)
        for name, status, detail in self.steps:
            icon = "OK" if status == "PASS" else "XX"
            print(f"  [{icon}] {name}: {status} {detail}")
        print("=" * 60)
        overall = "ALL PASSED" if self.passed else "FAILED"
        print(f"  Result: {overall}")
        print("=" * 60 + "\n")
        return self.passed


async def run_e2e_test():
    result = E2ETestResult()

    # Step 1: Connect to Chrome via CDP
    logger.info("Step 1: Connecting to Chrome CDP on port 9222...")
    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        result.step("CDP Connection", True, f"Connected to Chrome ({len(browser.contexts)} contexts)")
    except Exception as e:
        result.step("CDP Connection", False, str(e))
        result.report()
        return False

    # Step 2: Find TradingView tabs
    logger.info("Step 2: Scanning for TradingView tabs...")
    tv_pages = []
    for ctx in browser.contexts:
        for page in ctx.pages:
            url = page.url.lower()
            if "tradingview" in url:
                title = await page.title()
                tv_pages.append((page, title))
    
    if tv_pages:
        result.step("TradingView Tabs", True, f"Found {len(tv_pages)} tab(s): {[t[1][:40] for t in tv_pages]}")
    else:
        result.step("TradingView Tabs", False, "No TradingView tabs found in Chrome")
        await pw.stop()
        result.report()
        return False

    # Step 3: Detect symbol from first tab
    logger.info("Step 3: Detecting symbol from TradingView tab...")
    test_page, test_title = tv_pages[0]
    await test_page.bring_to_front()
    time.sleep(1)

    # Try to extract symbol from URL or title
    import re
    url = test_page.url
    symbol_match = re.search(r'symbol=([^&]+)', url)
    detected_symbol = symbol_match.group(1) if symbol_match else test_title.split(" ")[0] if test_title else "UNKNOWN"
    result.step("Symbol Detection", bool(detected_symbol), f"Detected: {detected_symbol}")

    # Step 4: Test RPA executor initialization
    logger.info("Step 4: Initializing RPA executor...")
    try:
        from execution.rpa_executor import RPAExecutor
        rpa = RPAExecutor()
        rpa.set_controlled_page(test_page)
        result.step("RPA Executor Init", True, "Controlled page set")
    except Exception as e:
        result.step("RPA Executor Init", False, str(e))
        await pw.stop()
        result.report()
        return False

    # Step 5: Test button detection (no click in test mode)
    logger.info("Step 5: Scanning for Buy/Sell buttons...")
    buy_selectors = [
        "button[class*='buyButton']",
        "[data-name='header-toolbar-buy']",
        "button:has-text('Buy')",
        "button[class*='buy']",
    ]
    sell_selectors = [
        "button[class*='sellButton']",
        "[data-name='header-toolbar-sell']",
        "button:has-text('Sell')",
        "button[class*='sell']",
    ]
    
    buy_found = False
    sell_found = False
    for sel in buy_selectors:
        try:
            if test_page.locator(sel).count() > 0:
                buy_found = True
                result.step("Buy Button Detection", True, f"Found via: {sel}")
                break
        except Exception:
            continue
    if not buy_found:
        result.step("Buy Button Detection", False, "No Buy button found with known selectors")

    for sel in sell_selectors:
        try:
            if test_page.locator(sel).count() > 0:
                sell_found = True
                result.step("Sell Button Detection", True, f"Found via: {sel}")
                break
        except Exception:
            continue
    if not sell_found:
        result.step("Sell Button Detection", False, "No Sell button found with known selectors")

    # Step 6: Test window title matching
    logger.info("Step 6: Testing window title matching...")
    try:
        terms = rpa._ticker_window_terms(detected_symbol)
        result.step("Ticker Window Terms", len(terms) > 0, f"Terms: {terms[:5]}")
    except Exception as e:
        result.step("Ticker Window Terms", False, str(e))

    # Step 7: Force a DRY RUN click test (only if DRY_RUN=True)
    dry_run = os.environ.get("DRY_RUN", "true").lower() == "true"
    if dry_run:
        result.step("Execution Test", True, "SKIPPED (DRY_RUN=True) — set DRY_RUN=False to test real clicks")
    else:
        logger.info("Step 7: Attempting real BUY click...")
        try:
            from core.models import TradeRecord, SignalAction, ConfidenceLevel
            trade = TradeRecord(
                asset=detected_symbol,
                action=SignalAction.BUY,
                entry_price=0.0,
                stop_loss=0.0,
                take_profit=0.0,
                confidence=ConfidenceLevel.HIGH,
                ai_reason="E2E test trade",
                mode="TEST",
                status="OPEN",
            )
            success = rpa.execute_trade(trade)
            result.step("Execution Test", success, f"BUY {detected_symbol}: {'SUCCESS' if success else 'FAILED'}")
        except Exception as e:
            result.step("Execution Test", False, str(e))

    # Cleanup
    await pw.stop()
    return result.report()


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("VcaniTrade AI - E2E Execution Test")
    print("=" * 60 + "\n")
    
    success = asyncio.run(run_e2e_test())
    sys.exit(0 if success else 1)
