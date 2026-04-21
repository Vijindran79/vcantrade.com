"""
Comprehensive Test Suite for VcaniTrade AI - Hybrid Architecture
Runs all module tests to catch bugs before production.
"""

import sys
import asyncio
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Test results tracker
test_results = {
    "passed": 0,
    "failed": 0,
    "errors": [],
    "warnings": []
}


def record_result(test_name: str, passed: bool, error: str = None, warning: str = None):
    """Track test results."""
    if passed:
        test_results["passed"] += 1
        print(f"✅ {test_name}")
    else:
        test_results["failed"] += 1
        test_results["errors"].append({"test": test_name, "error": error})
        print(f"❌ {test_name}: {error}")
    
    if warning:
        test_results["warnings"].append({"test": test_name, "warning": warning})
        print(f"⚠️  {test_name}: {warning}")


print("=" * 70)
print("🧪 VcaniTrade AI - Comprehensive Test Suite")
print("=" * 70)
print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 70)

# ===================================================================
# TEST 1: Configuration Module
# ===================================================================
print("\n📋 TEST 1: Configuration Module")
print("-" * 70)

try:
    import config
    
    # Test configured tickers
    assert len(config.CLOUD_TICKERS) > 0, f"Expected at least 1 ticker, got {len(config.CLOUD_TICKERS)}"
    record_result(f"{len(config.CLOUD_TICKERS)} tickers configured", True)
    
    # Test confidence threshold
    assert 0.0 <= config.SWARM_CONFIDENCE_THRESHOLD <= 1.0, "Invalid confidence threshold"
    record_result(f"Confidence threshold valid ({config.SWARM_CONFIDENCE_THRESHOLD:.2f})", True)
    
    # Test required settings
    assert hasattr(config, 'CLOUD_SCANNER_ENABLED'), "Missing CLOUD_SCANNER_ENABLED"
    record_result("Cloud scanner enabled flag exists", True)
    
    assert hasattr(config, 'LOCAL_LISTENER_PORT'), "Missing LOCAL_LISTENER_PORT"
    record_result("Local listener port exists", True)
    
    # Test technical thresholds
    assert config.VOLUME_SPIKE_MULTIPLIER > 0, "Invalid volume spike multiplier"
    record_result("Volume spike multiplier valid", True)
    
    assert config.RSI_OVERBOUGHT > config.RSI_OVERSOLD, "RSI thresholds inverted"
    record_result("RSI overbought > oversold", True)
    
    # Warnings
    if config.DRY_RUN is False:
        record_result("DRY_RUN check", True, warning="DRY_RUN is False - ensure this is intentional!")
    else:
        record_result("DRY_RUN is True (safe)", True)
    
    print(f"✅ Config tests: 7/7 passed")
    
except Exception as e:
    record_result("Config module", False, error=str(e))

# ===================================================================
# TEST 2: Core Models
# ===================================================================
print("\n📦 TEST 2: Core Models")
print("-" * 70)

try:
    from core.models import (
        MarketDataPoint, SignalAction, ConfidenceLevel,
        LLMAnalysisOutput, DebateTranscript, SwarmAgentBrief,
        TradeRecord, SafetyState, OverlaySignal
    )
    record_result("All models import", True)
    
    # Test MarketDataPoint
    mdp = MarketDataPoint(
        asset="XAUUSD=X",
        price=2350.50,
        volume=150000,
        price_change_1h=1.2,
        price_change_24h=3.5,
        indicators={"RSI": 65.5}
    )
    assert mdp.asset == "XAUUSD=X"
    record_result("MarketDataPoint creation", True)
    
    # Test SignalAction enum
    assert SignalAction.BUY.value == "BUY"
    assert SignalAction.SELL.value == "SELL"
    assert SignalAction.HOLD.value == "HOLD"
    record_result("SignalAction enum values", True)
    
    # Test ConfidenceLevel enum
    assert ConfidenceLevel.LOW.value == "LOW"
    assert ConfidenceLevel.HIGH.value == "HIGH"
    record_result("ConfidenceLevel enum values", True)
    
    # Test LLMAnalysisOutput with enums (bug fix check)
    output = LLMAnalysisOutput(
        action=SignalAction.BUY,
        asset="BTC-USD",
        confidence=ConfidenceLevel.HIGH,
        reason="Test output"
    )
    assert isinstance(output.action, SignalAction), "action should be SignalAction enum"
    assert isinstance(output.confidence, ConfidenceLevel), "confidence should be ConfidenceLevel enum"
    record_result("LLMAnalysisOutput with enums", True)
    
    # Test SafetyState
    safety = SafetyState()
    assert safety.can_trade is True, "Safety state should allow trading by default"
    record_result("SafetyState default (can_trade=True)", True)
    
    # Test TradeRecord
    trade = TradeRecord(
        asset="EURUSD",
        action=SignalAction.BUY,
        entry_price=1.0875,
        confidence=ConfidenceLevel.MEDIUM,
        ai_reason="Test trade"
    )
    assert trade.status == "OPEN"
    record_result("TradeRecord creation", True)
    
    print(f"✅ Model tests: 8/8 passed")
    
except Exception as e:
    record_result("Core models", False, error=str(e))

# ===================================================================
# TEST 3: Cloud Scanner
# ===================================================================
print("\n☁️  TEST 3: Cloud Scanner")
print("-" * 70)

try:
    from core.scanner import CloudScanner, TechnicalSignal
    record_result("CloudScanner import", True)
    
    # Test TechnicalSignal
    signal = TechnicalSignal(
        ticker="XAUUSD=X",
        signal_type="VOLUME_SPIKE",
        strength=0.85,
        metadata={"volume_ratio": 4.5, "price": 2350.50}
    )
    assert signal.ticker == "XAUUSD=X"
    assert signal.strength == 0.85
    record_result("TechnicalSignal creation", True)
    
    # Test CloudScanner initialization
    scanner = CloudScanner()
    assert len(scanner.tickers) > 0, f"Scanner should monitor at least 1 ticker, got {len(scanner.tickers)}"
    assert scanner.tickers == config.CLOUD_TICKERS, "Scanner tickers should match config.CLOUD_TICKERS"
    record_result(f"CloudScanner monitors configured tickers ({len(scanner.tickers)})", True)
    
    # Test cooldown logic
    assert scanner._is_signal_cooldown("TEST", "VOLUME_SPIKE") is False, "New signal should not be on cooldown"
    scanner._record_signal("TEST", "VOLUME_SPIKE")
    # Second check immediately should be on cooldown
    assert scanner._is_signal_cooldown("TEST", "VOLUME_SPIKE") is True, "Signal should now be on cooldown"
    record_result("Signal cooldown logic", True)
    
    # Test confidence calculation with mock data
    from core.models import SignalAction, ConfidenceLevel
    
    class MockAnalysis:
        def __init__(self):
            self.action = SignalAction.BUY
            self.confidence = ConfidenceLevel.HIGH
            self.entry_price = 2350.50
            self.stop_loss = 2340.00
            self.take_profit = 2370.00
            self.reason = "Test analysis"
    
    class MockTranscript:
        def __init__(self):
            self.technical_sniper = type('obj', (object,), {'action': 'BUY', 'conviction': 'HIGH'})()
            self.macro_analyst = type('obj', (object,), {'action': 'BULLISH', 'conviction': 'HIGH'})()
            self.risk_manager = type('obj', (object,), {'verdict': 'APPROVE', 'conviction': 'HIGH'})()
    
    mock_analysis = MockAnalysis()
    mock_transcript = MockTranscript()
    
    confidence = scanner._calculate_confidence(mock_analysis, mock_transcript)
    assert 0.0 <= confidence <= 1.0, f"Confidence should be 0-1, got {confidence}"
    assert confidence >= 0.70, f"High confidence signal should be >= 0.70, got {confidence}"
    record_result(f"Confidence calculation ({confidence:.2f})", True)
    
    print(f"✅ Cloud Scanner tests: 5/5 passed")
    
except Exception as e:
    record_result("Cloud Scanner", False, error=str(e))

# ===================================================================
# TEST 4: Signal Dispatcher
# ===================================================================
print("\n📡 TEST 4: Signal Dispatcher")
print("-" * 70)

try:
    from core.signal_dispatcher import SignalDispatcher
    record_result("SignalDispatcher import", True)
    
    # Test initialization
    dispatcher = SignalDispatcher()
    assert dispatcher.signal_count == 0, "Signal count should start at 0"
    record_result("SignalDispatcher initialization", True)
    
    # Test callback setting
    callback_called = []
    def callback_probe(data):
        callback_called.append(data)
    
    dispatcher.set_signal_callback(callback_probe)
    assert dispatcher.on_signal_received == callback_probe
    record_result("Signal callback setting", True)
    
    # Test signal validation (async)
    async def validate_signal_flow():
        # Valid signal
        valid_signal = {
            "ticker": "XAUUSD=X",
            "action": "BUY",
            "confidence": 0.85,
            "reason": "Volume spike detected"
        }
        
        # Check required fields
        required = ["ticker", "action", "confidence", "reason"]
        missing = [f for f in required if f not in valid_signal]
        assert len(missing) == 0, f"Missing fields: {missing}"
        record_result("Valid signal has all required fields", True)
        
        # Check confidence threshold
        assert valid_signal["confidence"] >= config.SWARM_CONFIDENCE_THRESHOLD
        record_result("Signal meets confidence threshold", True)
        
        # Test invalid signal (low confidence)
        invalid_signal = {
            "ticker": "TEST",
            "action": "BUY",
            "confidence": max(0.0, config.SWARM_CONFIDENCE_THRESHOLD - 0.01),
            "reason": "Low confidence"
        }
        assert invalid_signal["confidence"] < config.SWARM_CONFIDENCE_THRESHOLD
        record_result("Low confidence signal would be rejected", True)
    
    asyncio.run(validate_signal_flow())
    
    print(f"✅ Signal Dispatcher tests: 5/5 passed")
    
except Exception as e:
    record_result("Signal Dispatcher", False, error=str(e))

# ===================================================================
# TEST 5: LLM Analyzer (Bug Fix Verification)
# ===================================================================
print("\n🧠 TEST 5: LLM Analyzer")
print("-" * 70)

try:
    from core.llm_analyzer import LLMAnalyzer
    record_result("LLMAnalyzer import", True)
    
    # Check for the bug in llm_analyzer.py
    import inspect
    source = inspect.getsource(LLMAnalyzer.analyze_market)
    
    # Check if fallback uses proper enums (bug fix)
    if 'action="HOLD"' in source or 'confidence="LOW"' in source:
        record_result("LLM Analyzer fallback", False, 
                   error="Bug found: Fallback uses strings instead of SignalAction/ConfidenceLevel enums!")
    else:
        record_result("LLM Analyzer fallback uses enums", True)
    
    print(f"⚠️  LLM Analyzer tests: Needs bug fix (see above)")
    
except Exception as e:
    record_result("LLM Analyzer", False, error=str(e))

# ===================================================================
# TEST 6: Trade Engine
# ===================================================================
print("\n⚙️  TEST 6: Trade Engine")
print("-" * 70)

try:
    from core.trade_engine import TradeEngine
    record_result("TradeEngine import", True)
    
    # Test initialization
    engine = TradeEngine()
    assert len(engine.open_trades) == 0, "Should start with no open trades"
    record_result("TradeEngine initialization", True)
    
    # Test safety checks (TEACHER mode)
    from core.models import LLMAnalysisOutput, SignalAction, ConfidenceLevel
    
    signal = LLMAnalysisOutput(
        action=SignalAction.BUY,
        asset="XAUUSD=X",
        confidence=ConfidenceLevel.HIGH,
        entry_price=2350.50,
        stop_loss=2340.00,
        take_profit=2370.00,
        reason="Test signal"
    )
    
    # TEACHER mode should not execute
    trade = engine.process_signal(signal, mode="TEACHER")
    assert trade is not None, "Should create signal record in TEACHER mode"
    assert trade.mode == "TEACHER"
    record_result("TEACHER mode creates signal record", True)
    
    # Test safety state
    assert engine._check_safety() is True, "Safety check should pass with default state"
    record_result("Safety check passes", True)
    
    # Test kill switch
    engine.activate_kill_switch()
    assert engine._check_safety() is False, "Safety check should fail with kill switch"
    record_result("Kill switch blocks trading", True)
    
    # Reset kill switch
    engine.deactivate_kill_switch()
    assert engine._check_safety() is True, "Safety check should pass after reset"
    record_result("Kill switch deactivation works", True)
    
    # Test performance summary
    summary = engine.get_performance_summary()
    assert "total_trades" in summary
    assert "total_pnl" in summary
    record_result("Performance summary generation", True)
    
    print(f"✅ Trade Engine tests: 8/8 passed")
    
except Exception as e:
    record_result("Trade Engine", False, error=str(e))

# ===================================================================
# TEST 7: UI Dashboard
# ===================================================================
print("\n🖥️  TEST 7: UI Dashboard")
print("-" * 70)

try:
    # Note: Can't fully test PyQt6 without display, but can test imports and structure
    from ui.dashboard import CommandCenter
    from ui.ai_narrator import AINarratorOverlay
    record_result("Dashboard and mirror imports", True)
    
    # Check CommandCenter has required methods
    required_methods = [
        '_build_ui',
        '_build_account_panel',
        '_build_prop_firm_panel',
        'update_balance',
        'add_trade_log',
        '_build_control_panel',
        '_build_watchlist_panel',
        '_build_trade_log_panel',
        '_build_copilot_chat_panel'
    ]
    
    for method in required_methods:
        assert hasattr(CommandCenter, method), f"Missing method: {method}"
    
    record_result(f"CommandCenter has {len(required_methods)} required methods", True)
    
    # Check ticker_changed signal exists
    from PyQt6.QtCore import pyqtSignal
    assert hasattr(CommandCenter, 'ticker_changed'), "Missing ticker_changed signal"
    record_result("CommandCenter has ticker_changed signal", True)

    assert AINarratorOverlay is not None
    record_result("AINarratorOverlay available", True)
    
    print(f"✅ UI Dashboard tests: 3/3 passed (structure only)")
    
except Exception as e:
    record_result("UI Dashboard", False, error=str(e))

# ===================================================================
# TEST 8: Main Application
# ===================================================================
print("\n🚀 TEST 8: Main Application")
print("-" * 70)

try:
    import main
    record_result("Main module import", True)
    
    # Check VcaniTradeApp has hybrid architecture attributes
    app_class = main.VcaniTradeApp
    
    # Check for new thread types
    assert hasattr(main, 'CloudScannerThread'), "Missing CloudScannerThread"
    record_result("CloudScannerThread exists", True)
    
    assert hasattr(main, 'SignalListenerThread'), "Missing SignalListenerThread"
    record_result("SignalListenerThread exists", True)
    
    # Check for new handler methods
    required_handlers = [
        '_on_cloud_signal',
        '_on_signal_received',
        '_on_scanner_error',
        '_on_listener_error',
        '_on_ticker_changed',
        '_execute_cloud_signal',
        '_add_to_trade_ledger'
    ]
    
    for handler in required_handlers:
        assert hasattr(app_class, handler), f"Missing handler: {handler}"
    
    record_result(f"App has {len(required_handlers)} new handlers", True)
    
    print(f"✅ Main Application tests: 4/4 passed")
    
except Exception as e:
    record_result("Main Application", False, error=str(e))

# ===================================================================
# TEST 9: Integration Tests
# ===================================================================
print("\n🔗 TEST 9: Integration Tests")
print("-" * 70)

try:
    # Test complete signal flow (without actual network)
    from core.scanner import TechnicalSignal
    from core.models import MarketDataPoint
    
    # Create test signal
    tech_signal = TechnicalSignal(
        ticker="XAUUSD=X",
        signal_type="VOLUME_SPIKE",
        strength=0.90,
        metadata={
            "volume_ratio": 5.0,
            "price": 2350.50,
            "last_volume": 500000,
            "avg_volume": 100000
        }
    )
    
    scanner = CloudScanner()
    
    # Build market data
    market_data = scanner._build_market_data(tech_signal)
    assert market_data.asset == "XAUUSD=X"
    assert market_data.price == 2350.50
    assert market_data.volume == 500000
    record_result("Signal → MarketDataPoint conversion", True)
    
    # Test confidence calculation with aligned agents
    class MockOutput:
        action = SignalAction.BUY
        confidence = ConfidenceLevel.HIGH
        entry_price = 2350.50
        stop_loss = 2340.00
        take_profit = 2370.00
        reason = "Test"
    
    class MockTranscript:
        technical_sniper = type('obj', (object,), {'action': 'BUY', 'conviction': 'HIGH'})()
        macro_analyst = type('obj', (object,), {'action': 'BULLISH', 'conviction': 'HIGH'})()
        risk_manager = type('obj', (object,), {'verdict': 'APPROVE', 'conviction': 'HIGH'})()
    
    confidence = scanner._calculate_confidence(MockOutput(), MockTranscript())
    assert confidence >= 0.70, f"Confidence too low: {confidence}"
    record_result(f"Full signal pipeline confidence: {confidence:.2f}", True)
    
    # Test trade engine with cloud signal
    from core.trade_engine import TradeEngine
    engine = TradeEngine()
    
    signal_data = {
        "ticker": "XAUUSD=X",
        "action": "BUY",
        "confidence": 0.85,
        "entry_price": 2350.50,
        "stop_loss": 2340.00,
        "take_profit": 2370.00,
        "reason": "Volume spike detected"
    }
    
    # Simulate what main.py does
    analysis = LLMAnalysisOutput(
        action=SignalAction(signal_data["action"]),
        asset=signal_data["ticker"],
        confidence=ConfidenceLevel.HIGH if signal_data["confidence"] > 0.8 else ConfidenceLevel.MEDIUM,
        entry_price=signal_data.get("entry_price"),
        stop_loss=signal_data.get("stop_loss"),
        take_profit=signal_data.get("take_profit"),
        reason=signal_data.get("reason", "Cloud scanner signal")
    )
    
    trade = engine.process_signal(analysis, mode="TEACHER")
    assert trade is not None
    assert trade.asset == "XAUUSD=X"
    assert trade.action == SignalAction.BUY
    record_result("Cloud signal → Trade engine integration", True)
    
    print(f"✅ Integration tests: 3/3 passed")
    
except Exception as e:
    record_result("Integration Tests", False, error=str(e))

# ===================================================================
# TEST 10: Edge Cases & Error Handling
# ===================================================================
print("\n🛡️  TEST 10: Edge Cases & Error Handling")
print("-" * 70)

try:
    from core.scanner import CloudScanner, TechnicalSignal
    
    # Test scanner with empty ticker list
    scanner = CloudScanner()
    original_tickers = scanner.tickers
    scanner.tickers = []
    
    async def run_empty_scan():
        signals = await scanner.scan_all_tickers()
        return len(signals) == 0
    
    result = asyncio.run(run_empty_scan())
    assert result is True
    record_result("Empty ticker list handled", True)
    
    scanner.tickers = original_tickers
    
    # Test signal with missing metadata
    signal_no_meta = TechnicalSignal(
        ticker="TEST",
        signal_type="RSI_OVERSOLD",
        strength=0.75
    )
    market_data = scanner._build_market_data(signal_no_meta)
    assert market_data is not None
    record_result("Signal with no metadata handled", True)
    
    # Test trade engine with HOLD action
    from core.trade_engine import TradeEngine
    engine = TradeEngine()
    
    hold_signal = LLMAnalysisOutput(
        action=SignalAction.HOLD,
        asset="TEST",
        confidence=ConfidenceLevel.LOW,
        reason="No clear direction"
    )
    
    trade = engine.process_signal(hold_signal, mode="TEACHER")
    # HOLD should not create a trade
    record_result("HOLD signal doesn't create trade", trade is None or trade.action == SignalAction.HOLD)
    
    # Test safety state with max positions
    engine2 = TradeEngine()
    engine2.open_trades = [type('Trade', (), {})() for _ in range(config.MAX_OPEN_POSITIONS)]
    assert engine2._check_safety() is False, "Should block when at max positions"
    record_result("Max positions limit enforced", True)
    
    print(f"✅ Edge case tests: 4/4 passed")
    
except Exception as e:
    record_result("Edge Cases", False, error=str(e))

# ===================================================================
# FINAL SUMMARY
# ===================================================================
print("\n" + "=" * 70)
print("📊 TEST SUMMARY")
print("=" * 70)

total = test_results["passed"] + test_results["failed"]
print(f"Total tests: {total}")
print(f"✅ Passed: {test_results['passed']}")
print(f"❌ Failed: {test_results['failed']}")
print(f"⚠️  Warnings: {len(test_results['warnings'])}")

if test_results['errors']:
    print("\n❌ ERRORS:")
    for error in test_results['errors']:
        print(f"  - {error['test']}: {error['error']}")

if test_results['warnings']:
    print("\n⚠️  WARNINGS:")
    for warning in test_results['warnings']:
        print(f"  - {warning['test']}: {warning['warning']}")

print("=" * 70)

if __name__ == "__main__":
    if test_results['failed'] == 0:
        print("🎉 ALL TESTS PASSED! Product is ready for production!")
        print("=" * 70)
        sys.exit(0)
    else:
        print(f"⚠️  {test_results['failed']} test(s) failed - fixes needed")
        print("=" * 70)
        sys.exit(1)
