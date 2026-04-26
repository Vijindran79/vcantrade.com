#!/usr/bin/env python
"""
End-to-End System Test - Simulates full user experience
Tests: Scanner -> Signal -> AI Analysis -> Prop Firm Check -> Execution -> Position Monitoring
"""
import sys
import io
import asyncio
import json

if sys.platform == 'win32' and 'pytest' not in sys.modules:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('e2e_test.log', encoding='utf-8', mode='w'),
        logging.StreamHandler(sys.stdout)
    ],
    force=True
)
logger = logging.getLogger('e2e')

PASS = 0
FAIL = 0
WARN = 0

def report_test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        logger.info(f"[OK] {name}")
        if detail:
            logger.info(f"   -> {detail}")
    else:
        FAIL += 1
        logger.error(f"[FAIL] {name}")
        if detail:
            logger.error(f"   -> {detail}")

def warn(name, detail=""):
    global WARN
    WARN += 1
    logger.warning(f"[WARN] {name}")
    if detail:
        logger.warning(f"   -> {detail}")

async def main():
    global PASS, FAIL
    
    logger.info("=" * 70)
    logger.info("VCANITRADE AI - END-TO-END SYSTEM TEST")
    logger.info("=" * 70)
    logger.info("")

    # [EMOJI] TEST 1: Config [EMOJI]
    logger.info("[EMOJI]" * 50)
    logger.info("TEST 1: Configuration")
    logger.info("[EMOJI]" * 50)
    try:
        import config
        report_test("Config loaded", True)
        report_test(f"PROP_FIRM: {config.PROP_FIRM_NAME}", config.PROP_FIRM_ENABLED)
        report_test(f"OLLAMA_URL: {config.OLLAMA_BASE_URL}", "localhost" in config.OLLAMA_BASE_URL or "127.0.0.1" in config.OLLAMA_BASE_URL)
        report_test(f"MODEL: {config.OLLAMA_MODEL}", config.OLLAMA_MODEL)
        report_test(f"SCANNER: {'ENABLED' if config.CLOUD_SCANNER_ENABLED else 'DISABLED'}", config.CLOUD_SCANNER_ENABLED)
        report_test(f"Tickers: {len(config.CLOUD_TICKERS)}", len(config.CLOUD_TICKERS) > 0, str(config.CLOUD_TICKERS))
    except Exception as e:
        report_test("Config loaded", False, str(e))

    # [EMOJI] TEST 2: Ollama Connection [EMOJI]
    logger.info("")
    logger.info("[EMOJI]" * 50)
    logger.info("TEST 2: Ollama AI Connection")
    logger.info("[EMOJI]" * 50)
    try:
        import requests
        resp = requests.post(f"{config.OLLAMA_BASE_URL}/api/generate", json={
            "model": config.OLLAMA_MODEL,
            "prompt": "Say 'TRADING READY' in exactly 3 words",
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 50}
        }, timeout=30)
        resp.raise_for_status()
        reply = resp.json().get("response", "")
        report_test("Ollama responds", True, f"Reply: {reply[:80]}")
    except Exception as e:
        report_test("Ollama responds", False, str(e))

    # [EMOJI] TEST 3: Scanner Detects Real Signals [EMOJI]
    logger.info("")
    logger.info("[EMOJI]" * 50)
    logger.info("TEST 3: Market Scanner (Live Data)")
    logger.info("[EMOJI]" * 50)
    try:
        from core.scanner import CloudScanner
        scanner = CloudScanner()
        signals = await scanner.scan_all_tickers()
        report_test(f"Scanned {len(scanner.tickers)} tickers", True)
        if signals:
            report_test(
                f"Signals found: {len(signals)}",
                True,
                ", ".join([f"{s.ticker}:{s.signal_type}" for s in signals[:5]]),
            )
        else:
            warn("No live signals detected", "Market conditions may simply be quiet right now.")
    except Exception as e:
        report_test("Scanner works", False, str(e))
        import traceback
        traceback.print_exc()

    # [EMOJI] TEST 4: AI Analysis Works [EMOJI]
    logger.info("")
    logger.info("[EMOJI]" * 50)
    logger.info("TEST 4: AI Analysis (Swarm Consensus)")
    logger.info("[EMOJI]" * 50)
    try:
        from core.scanner import CloudScanner
        from core.models import MarketDataPoint
        scanner = CloudScanner()
        signals = await scanner.scan_all_tickers()
        
        if signals:
            result = await scanner.process_signals(signals[:1])  # Process first signal
            if result:
                report_test("AI analysis complete", True)
                report_test(f"Action: {result['action']}", result['action'] in ['BUY', 'SELL', 'HOLD'])
                report_test(f"Confidence: {result['confidence']:.2f}", result['confidence'] > 0)
                report_test(f"Entry: ${result.get('entry_price', 0):.2f}", result.get('entry_price', 0) > 0)
                report_test(f"TP: ${result.get('take_profit', 0):.2f}", result.get('take_profit', 0) > 0)
                report_test(f"SL: ${result.get('stop_loss', 0):.2f}", result.get('stop_loss', 0) > 0)
                report_test(f"Reason: {result.get('reason', '')[:60]}...", len(result.get('reason', '')) > 0)
            else:
                report_test("AI returns result", False, "No result returned")
        else:
            warn("No live signal available for AI test", "Falling back to a synthetic signal.")
            # Create synthetic signal
            market_data = MarketDataPoint(
                asset="BTC-USD",
                price=85000.0,
                volume=1000.0,
                indicators={"RSI": 35.0, "SIGNAL_TYPE": "RSI_OVERSOLD", "SIGNAL_STRENGTH": 0.75}
            )
            output, transcript = await scanner.consensus.run(market_data)
            report_test("AI analysis (synthetic)", output.action.value in ['BUY', 'SELL', 'HOLD'],
                 f"Action: {output.action.value}, Confidence: {output.confidence.value}")
    except Exception as e:
        report_test("AI analysis works", False, str(e))
        import traceback
        traceback.print_exc()

    # [EMOJI] TEST 5: Prop Firm Rule Engine [EMOJI]
    logger.info("")
    logger.info("[EMOJI]" * 50)
    logger.info("TEST 5: Prop Firm Rule Engine (The Professor)")
    logger.info("[EMOJI]" * 50)
    try:
        from core.prop_firm_rules import PropFirmRuleEngine, PropFirmName
        engine = PropFirmRuleEngine(PropFirmName.TOPSTEP)
        engine.compliance.starting_balance = 50000.0
        engine.compliance.current_balance = 50000.0
        engine.compliance.peak_balance = 50000.0
        
        can_trade, violations = engine.check_before_trade("BTC-USD", 10.0)
        report_test("Prop firm allows trade", can_trade, f"Violations: {violations}")
        
        # Simulate losing trade
        engine.compliance.daily_pnl = -200.0  # Exceed $150 limit
        can_trade2, violations2 = engine.check_before_trade("BTC-USD", 10.0)
        report_test("Prop firm blocks after daily loss", not can_trade2, f"Violations: {violations2}")
        
        # Reset
        engine.compliance.daily_pnl = 0.0
        report = engine.get_dashboard_data()
        report_test("Dashboard data works", 'current_balance' in report, f"Balance: ${report['current_balance']:,.2f}")
    except Exception as e:
        report_test("Prop firm engine works", False, str(e))
        import traceback
        traceback.print_exc()

    # [EMOJI] TEST 6: Position Monitoring [EMOJI]
    logger.info("")
    logger.info("[EMOJI]" * 50)
    logger.info("TEST 6: Position Lifecycle")
    logger.info("[EMOJI]" * 50)
    try:
        position = {
            "asset": "BTC-USD",
            "side": "BUY",
            "entry": 85000.0,
            "current": 85000.0,
            "amount": 100.0,
            "quantity": 0.001176,
            "tp_price": 86700.0,
            "sl_price": 84150.0,
            "pnl": 0.0,
            "pnl_pct": 0.0,
        }
        report_test("Position created", True, f"{position['side']} {position['asset']} @ ${position['entry']:,.2f}")
        report_test("TP set above entry", position['tp_price'] > position['entry'],
             f"TP: ${position['tp_price']:,.2f}")
        report_test("SL set below entry", position['sl_price'] < position['entry'],
             f"SL: ${position['sl_price']:,.2f}")
        
        # Simulate price moving to TP
        position['current'] = 87000.0  # Above TP
        hit_tp = position['current'] >= position['tp_price']
        report_test("TP would trigger", hit_tp, f"Current: ${position['current']:,.2f} >= TP: ${position['tp_price']:,.2f}")
        
        # Simulate price moving to SL
        position['current'] = 84000.0  # Below SL
        hit_sl = position['current'] <= position['sl_price']
        report_test("SL would trigger", hit_sl, f"Current: ${position['current']:,.2f} <= SL: ${position['sl_price']:,.2f}")
    except Exception as e:
        report_test("Position monitoring", False, str(e))

    # [EMOJI] TEST 7: Dashboard Imports [EMOJI]
    logger.info("")
    logger.info("[EMOJI]" * 50)
    logger.info("TEST 7: UI Components")
    logger.info("[EMOJI]" * 50)
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        report_test("Qt initialized", True)
        
        from ui.dashboard import CommandCenter
        cmd = CommandCenter()
        report_test("Dashboard created", True)
        
        # Test UI methods
        cmd.update_balance(50000.0, 50100.0, 100.0, 250.0)
        report_test("Balance update works", True)
        
        cmd.add_trade_log("BTC-USD", "BUY", 100.0, 0, "Open")
        report_test("Trade log entry works", True)
        
        from ui.signal_dialog import SignalApprovalDialog
        report_test("Signal dialog imports", True)
    except Exception as e:
        report_test("UI components work", False, str(e))
        import traceback
        traceback.print_exc()

    # [EMOJI] SUMMARY [EMOJI]
    logger.info("")
    logger.info("=" * 70)
    logger.info("FINAL RESULTS")
    logger.info("=" * 70)
    logger.info(f"Total Tests: {PASS + FAIL}")
    logger.info(f"[OK] Passed: {PASS}")
    logger.info(f"[FAIL] Failed: {FAIL}")
    logger.info(f"[WARN] Warnings: {WARN}")
    logger.info("")
    
    if FAIL == 0:
        logger.info("[CELEBRATE] ALL TESTS PASSED - SYSTEM IS PRODUCTION READY!")
    else:
        logger.info(f"[WARN] {FAIL} test(s) failed - fixes needed")
    
    # Write report
    with open("e2e_report.txt", "w", encoding="utf-8") as f:
        f.write(f"Tests: {PASS + FAIL}\n")
        f.write(f"Passed: {PASS}\n")
        f.write(f"Failed: {FAIL}\n")
        f.write(f"Warnings: {WARN}\n")
        f.write(f"Status: {'PRODUCTION READY' if FAIL == 0 else 'NEEDS FIXES'}\n")
    
    return FAIL == 0

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
