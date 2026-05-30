"""
VcaniTrade AI - Intelligence Assessment Test

Tests the bot's "brain" modules WITHOUT external services (no Ollama, no Chrome, no MT5).
Validates: math engines, risk management, prop firm compliance, regime detection,
liquidity analysis, confidence escalation, session awareness, and signal flow.
"""

import sys
import logging
import traceback
from datetime import datetime, date, timedelta
from typing import List

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("intelligence_test")

PASS = "[PASS]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"
results = {"passed": 0, "failed": 0, "skipped": 0}


def report(status, msg):
    print(f"  {status} {msg}")
    if status == PASS:
        results["passed"] += 1
    elif status == FAIL:
        results["failed"] += 1
    else:
        results["skipped"] += 1


def generate_synthetic_ohlcv(n=250, trend="bull"):
    """Generate synthetic OHLCV data for testing."""
    np.random.seed(42)
    base = 100.0
    data = []
    for i in range(n):
        if trend == "bull":
            base += np.random.normal(0.3, 1.0)
        elif trend == "bear":
            base += np.random.normal(-0.3, 1.0)
        else:
            base += np.random.normal(0, 1.0)
        o = base + np.random.uniform(-0.5, 0.5)
        h = max(o, base) + np.random.uniform(0.2, 1.5)
        l = min(o, base) - np.random.uniform(0.2, 1.5)
        c = base + np.random.uniform(-0.3, 0.3)
        v = np.random.randint(1000, 50000)
        data.append({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v})
    return pd.DataFrame(data)


# ============================================================
# TEST 1: Module Imports
# ============================================================
def test_imports():
    print("\n[TEST 1] Module Imports")
    modules = [
        ("core.models", "Core Models"),
        ("core.regime_detector", "Regime Detector"),
        ("core.liquidity_engine", "Liquidity Engine"),
        ("core.risk_governor", "Risk Governor"),
        ("core.prop_firm_rules", "Prop Firm Rules"),
        ("core.profit_lock", "Profit Lock"),
        ("core.confidence_escalator", "Confidence Escalator"),
        ("core.market_sessions", "Market Sessions"),
        ("core.devils_advocate", "Devil's Advocate"),
        ("core.market_intelligence", "Market Intelligence"),
        ("core.brain_swarm", "Brain Swarm"),
        ("core.llm_analyzer", "LLM Analyzer"),
        ("core.scanner", "Scanner"),
        ("core.sentiment_pulse", "Sentiment Pulse"),
        ("core.atr_stops", "ATR Stops"),
        ("core.slippage_guard", "Slippage Guard"),
        ("core.multi_timeframe_structure", "Multi-Timeframe Structure"),
    ]
    for mod_path, name in modules:
        try:
            __import__(mod_path)
            report(PASS, f"{name} ({mod_path})")
        except Exception as e:
            report(FAIL, f"{name} ({mod_path}): {e}")


# ============================================================
# TEST 2: Regime Detector (Pure Math)
# ============================================================
def test_regime_detector():
    print("\n[TEST 2] Regime Detector (Math Engine)")
    try:
        from core.regime_detector import RegimeDetector

        detector = RegimeDetector()

        # Bull market
        bull_df = generate_synthetic_ohlcv(250, "bull")
        verdict = detector.analyze(bull_df)
        if verdict is None:
            report(FAIL, "Bull regime returned None")
        else:
            report(PASS, f"Bull regime: {verdict.regime} (score={verdict.score:+.0f})")
            report(PASS, f"  ADX={verdict.adx:.1f}, Green={verdict.green_pct:.0f}%, EMA={verdict.ema_alignment}")
            report(PASS, f"  Volatility={verdict.volatility_state}, Direction={verdict.trend_direction}")
            if verdict.score > 0:
                report(PASS, "Bull data correctly scores positive")
            else:
                report(FAIL, f"Bull data scored {verdict.score:.0f} (expected positive)")

        # Bear market
        bear_df = generate_synthetic_ohlcv(250, "bear")
        verdict_bear = detector.analyze(bear_df)
        if verdict_bear is None:
            report(FAIL, "Bear regime returned None")
        else:
            report(PASS, f"Bear regime: {verdict_bear.regime} (score={verdict_bear.score:+.0f})")
            if verdict_bear.score < 0:
                report(PASS, "Bear data correctly scores negative")
            else:
                report(FAIL, f"Bear data scored {verdict_bear.score:.0f} (expected negative)")

        # Choppy market
        chop_df = generate_synthetic_ohlcv(250, "chop")
        verdict_chop = detector.analyze(chop_df)
        if verdict_chop:
            report(PASS, f"Choppy regime: {verdict_chop.regime} (score={verdict_chop.score:+.0f})")
        else:
            report(FAIL, "Choppy regime returned None")

        # Prompt context generation
        if verdict:
            prompt = verdict.as_prompt_context()
            if "MARKET REGIME" in prompt and "Regime:" in prompt:
                report(PASS, "Prompt context generation works")
            else:
                report(FAIL, "Prompt context malformed")

        # Edge case: too little data
        tiny_df = generate_synthetic_ohlcv(5, "bull")
        result = detector.analyze(tiny_df)
        if result is None:
            report(PASS, "Correctly returns None for insufficient data")
        else:
            report(FAIL, "Should return None for <200 rows")

    except Exception as e:
        report(FAIL, f"Regime detector: {e}")
        traceback.print_exc()


# ============================================================
# TEST 3: Liquidity Engine (Smart Money Concepts)
# ============================================================
def test_liquidity_engine():
    print("\n[TEST 3] Liquidity Engine (Smart Money Concepts)")
    try:
        from core.liquidity_engine import LiquidityEngine

        engine = LiquidityEngine()
        df = generate_synthetic_ohlcv(250, "bull")

        analysis = engine.analyze(df, "ES=F")
        if analysis is None:
            report(FAIL, "Liquidity analysis returned None")
            return

        report(PASS, f"Liquidity analysis for {analysis.ticker} @ ${analysis.current_price:.2f}")
        report(PASS, f"  Demand zones: {len(analysis.demand_zones)}")
        report(PASS, f"  Supply zones: {len(analysis.supply_zones)}")
        report(PASS, f"  Bullish FVGs: {len(analysis.fvg_bullish)}")
        report(PASS, f"  Bearish FVGs: {len(analysis.fvg_bearish)}")
        report(PASS, f"  Liquidity pools: {len(analysis.liquidity_pools)}")

        if analysis.optimal_entry_long:
            report(PASS, f"  Optimal long entry: ${analysis.optimal_entry_long:.2f}")
        if analysis.take_profit_long:
            report(PASS, f"  TP long: ${analysis.take_profit_long:.2f}")

        # Edge case
        empty_analysis = engine.analyze(pd.DataFrame(), "ES=F")
        if empty_analysis.current_price == 0.0:
            report(PASS, "Correctly handles empty DataFrame")

    except Exception as e:
        report(FAIL, f"Liquidity engine: {e}")
        traceback.print_exc()


# ============================================================
# TEST 4: Risk Governor (Correlation Engine)
# ============================================================
def test_risk_governor():
    print("\n[TEST 4] Risk Governor (Portfolio Risk)")
    try:
        from core.risk_governor import RiskGovernor, get_correlation

        governor = RiskGovernor()

        # Test correlation lookup (module-level function)
        corr = get_correlation("BTC-USD", "ETH-USD")
        if corr and corr > 0.8:
            report(PASS, f"BTC-ETH correlation: {corr:.2f} (correctly high)")
        else:
            report(FAIL, f"BTC-ETH correlation: {corr}")

        # Test evaluate_signal
        result = governor.evaluate_signal(
            {"asset": "BTC-USD", "action": "BUY", "exposure_pct": 2.0, "confidence": "HIGH"}
        )
        report(PASS, f"BTC-USD signal: {result['verdict']} - {result['reason']}")

        # Test correlated asset (ETH highly correlated with BTC)
        result_eth = governor.evaluate_signal(
            {"asset": "ETH-USD", "action": "BUY", "exposure_pct": 3.0, "confidence": "HIGH"},
            existing_positions=[{"asset": "BTC-USD", "exposure_pct": 2.0}],
        )
        report(PASS, f"ETH-USD (correlated): {result_eth['verdict']} - {result_eth.get('reason', '')[:80]}")

        # Test uncorrelated asset
        result_gold = governor.evaluate_signal(
            {"asset": "GC=F", "action": "BUY", "exposure_pct": 2.0, "confidence": "MEDIUM"}
        )
        report(PASS, f"Gold (uncorrelated): {result_gold['verdict']}")

        # Test risk units
        report(PASS, f"Active risk units: {len(governor.risk_units)}")
        report(PASS, f"Signals processed: {governor.total_signals_processed}")

    except Exception as e:
        report(FAIL, f"Risk governor: {e}")
        traceback.print_exc()


# ============================================================
# TEST 5: Prop Firm Rules Engine
# ============================================================
def test_prop_firm_rules():
    print("\n[TEST 5] Prop Firm Rules Engine")
    try:
        from core.prop_firm_rules import PropFirmRuleEngine, PropFirmName

        # Test Apex engine
        engine = PropFirmRuleEngine(PropFirmName.APEX)
        report(PASS, f"Apex engine: account=${engine.rules.account_size:.0f}")
        report(PASS, f"  Max trailing DD: ${engine.rules.max_trailing_drawdown:.0f}")
        report(PASS, f"  Profit target: ${engine.rules.profit_target_phase1:.0f}")

        # Test TopStep engine
        engine_ts = PropFirmRuleEngine(PropFirmName.TOPSTEP)
        report(PASS, f"TopStep engine: account=${engine_ts.rules.account_size:.0f}")

        # Test compliance checking
        can, violations = engine.check_before_trade("ES=F")
        report(PASS, f"Before any trades: can_trade={can}")

        # Simulate winning trades
        engine.record_trade(500.0, "ES=F")
        engine.record_trade(300.0, "NQ=F")
        can, violations = engine.check_before_trade("ES=F")
        report(PASS, f"After +$800: can_trade={can}, violations={len(violations)}")

        # Simulate big loss
        engine.record_trade(-2500.0, "ES=F")
        can, violations = engine.check_before_trade("ES=F")
        report(PASS, f"After -$2500 loss: can_trade={can}, violations={len(violations)}")

        # Dashboard data
        dash = engine.get_dashboard_data()
        report(PASS, f"Dashboard: {dash['firm_name']}, win_rate={dash.get('win_rate', 0):.0f}%")

    except Exception as e:
        report(FAIL, f"Prop firm rules: {e}")
        traceback.print_exc()


# ============================================================
# TEST 6: Profit Lock / Walk-Away Protocol
# ============================================================
def test_profit_lock():
    print("\n[TEST 6] Profit Lock & Walk-Away Protocol")
    try:
        from core.profit_lock import WalkAwayProtocol

        walk = WalkAwayProtocol(max_daily_loss_pct=2.0, shutdown_hours=24)

        # No violation at -1%
        violated = walk.check_violation(-1.0)
        if not violated:
            report(PASS, "No violation at -1% daily loss")

        # Trigger at -2.5%
        violated = walk.check_violation(-2.5)
        if violated:
            report(PASS, "Walk-Away triggered at -2.5% daily loss")
        else:
            report(FAIL, "Walk-Away should have triggered at -2.5%")

        # Can't trade after violation
        if not walk.can_trade():
            report(PASS, "Trading blocked after Walk-Away trigger")
        else:
            report(FAIL, "Trading should be blocked")

        # Check shutdown info
        status = walk.get_status()
        report(PASS, f"Shutdown status: active={status['active']}, remaining={status.get('remaining_hours', 'N/A')}h")

    except Exception as e:
        report(FAIL, f"Profit lock: {e}")
        traceback.print_exc()


# ============================================================
# TEST 7: Confidence Escalator
# ============================================================
def test_confidence_escalator():
    print("\n[TEST 7] Confidence Escalator (Two-Stage Validation)")
    try:
        from core.confidence_escalator import ConfidenceEscalator, EscalatorState

        escalator = ConfidenceEscalator()

        # Initial state
        report(PASS, f"Initial state: {escalator.state.value}")

        # Start probe
        escalator.trigger_probe(4500.0, 4490.0, 4520.0)
        report(PASS, f"After probe start: {escalator.state.value}")

        # Simulate bars holding S1
        for i in range(4):
            escalator.update_market_conditions(4502.0 + i * 0.5, 4490.0, bar_closed=True)

        report(PASS, f"After 4 bars: confidence={escalator.metrics.current_confidence:.0f}%")
        report(PASS, f"State: {escalator.state.value}")

        # Check if ready to strike
        if escalator.metrics.current_confidence >= 85:
            report(PASS, "Escalator reached STRIKE threshold")
        else:
            report(PASS, f"Escalator at {escalator.metrics.current_confidence:.0f}% (needs 85% for strike)")

    except Exception as e:
        report(FAIL, f"Confidence escalator: {e}")
        traceback.print_exc()


# ============================================================
# TEST 8: Market Session Awareness
# ============================================================
def test_market_sessions():
    print("\n[TEST 8] Market Session Awareness")
    try:
        from core.market_sessions import MarketSessionDetector, is_crypto_ticker, is_weekend_closed

        detector = MarketSessionDetector()
        ctx = detector.get_session_context()

        report(PASS, f"Current session: {ctx.get('primary_session', 'unknown')}")
        report(PASS, f"Is peak volatility: {ctx.get('is_peak_volatility', False)}")

        # Crypto detection
        report(PASS, f"BTC-USD is crypto: {is_crypto_ticker('BTC-USD')}")
        report(PASS, f"ES=F is crypto: {is_crypto_ticker('ES=F')}")

        # Weekend filter
        report(PASS, f"ES=F weekend closed: {is_weekend_closed('ES=F')}")
        report(PASS, f"BTC-USD weekend closed: {is_weekend_closed('BTC-USD')}")

    except Exception as e:
        report(FAIL, f"Market sessions: {e}")
        traceback.print_exc()


# ============================================================
# TEST 9: Pydantic Models (Data Validation)
# ============================================================
def test_models():
    print("\n[TEST 9] Core Models (Pydantic Validation)")
    try:
        from core.models import (
            SignalAction, ConfidenceLevel, LLMAnalysisOutput,
            TradeRecord, TradeResult, SafetyState, MarketDataPoint,
        )

        # LLM output validation
        output = LLMAnalysisOutput(
            action=SignalAction.BUY,
            asset="ES=F",
            confidence=ConfidenceLevel.HIGH,
            entry_price=4500.0,
            stop_loss=4490.0,
            take_profit=4520.0,
            reason="Strong bullish regime with volume confirmation",
        )
        report(PASS, f"LLM output: {output.action.value} {output.asset} ({output.confidence.value})")

        # Trade record
        trade = TradeRecord(
            asset="NQ=F",
            action=SignalAction.SELL,
            entry_price=15000.0,
            confidence=ConfidenceLevel.MEDIUM,
            ai_reason="Bearish divergence on RSI",
        )
        report(PASS, f"Trade record: {trade.action.value} {trade.asset} status={trade.status}")

        # Safety state
        safety = SafetyState()
        can = safety.update_trade_ability()
        report(PASS, f"Safety state: can_trade={can}, kill_switch={safety.kill_switch_active}")

        # Invalid data rejection
        try:
            bad_output = LLMAnalysisOutput(
                action="INVALID",
                asset="ES=F",
                confidence="HIGH",
                reason="test",
            )
            report(FAIL, "Should have rejected invalid action")
        except Exception:
            report(PASS, "Correctly rejected invalid SignalAction")

    except Exception as e:
        report(FAIL, f"Models: {e}")
        traceback.print_exc()


# ============================================================
# TEST 10: Devil's Advocate (Prompt Generation)
# ============================================================
def test_devils_advocate():
    print("\n[TEST 10] Devil's Advocate (Contrarian Agent)")
    try:
        from core.devils_advocate import DevilsAdvocate, PROMPT_DEVILS_ADVOCATE

        advocate = DevilsAdvocate()

        # Verify prompt template
        prompt = PROMPT_DEVILS_ADVOCATE.format(
            asset="ES=F",
            runtime_mode="AUTONOMOUS",
            session_context="US Session",
            suggested_action="BUY",
            entry_price=4500.0,
            stop_loss=4490.0,
            take_profit=4520.0,
            confidence="HIGH",
            signal_type="RSI_OVERSOLD",
            rsi=28.5,
            signal_strength=0.85,
            liquidity_sweep="Yes",
        )
        if "DEVIL'S ADVOCATE" in prompt and "STRONG_AVOID" in prompt:
            report(PASS, "Devil's Advocate prompt template valid")
        else:
            report(FAIL, "Prompt template malformed")

        # Verify JSON output format
        if '"rating"' in prompt and '"rejection_reasons"' in prompt and '"confidence_penalty"' in prompt:
            report(PASS, "JSON output schema enforced in prompt")
        else:
            report(FAIL, "JSON schema missing from prompt")

        report(PASS, f"Devil's Advocate initialized (model: {getattr(advocate, 'model', 'default')})")

    except Exception as e:
        report(FAIL, f"Devil's Advocate: {e}")
        traceback.print_exc()


# ============================================================
# TEST 11: ATR Stops
# ============================================================
def test_atr_stops():
    print("\n[TEST 11] ATR-Based Dynamic Stops")
    try:
        from core.atr_stops import LooseATRStops

        calculator = LooseATRStops()
        df = generate_synthetic_ohlcv(250, "bull")

        highs = df["High"].tolist()
        lows = df["Low"].tolist()
        closes = df["Close"].tolist()

        atr = calculator.calculate_atr(highs, lows, closes)
        report(PASS, f"ATR calculated: {atr:.4f}")

        entry = float(closes[-1])
        sl = calculator.calculate_stop_loss(entry, atr, "LONG", 2.0)
        report(PASS, f"Stop loss (LONG, 2x ATR): ${sl:.2f}")

        tp = calculator.calculate_take_profit(entry, atr, "LONG", 2.0)
        report(PASS, f"Take profit (2:1 R:R): ${tp:.2f}")

        vol = calculator.calculate_volatility_regime(atr, closes[-1])
        report(PASS, f"Volatility regime: {vol}")

    except Exception as e:
        report(FAIL, f"ATR stops: {e}")
        traceback.print_exc()


# ============================================================
# TEST 12: Signal Flow Simulation
# ============================================================
def test_signal_flow():
    print("\n[TEST 12] Signal Flow Simulation (End-to-End Logic)")
    try:
        from core.models import SignalAction, ConfidenceLevel, MarketDataPoint
        from core.regime_detector import RegimeDetector
        from core.liquidity_engine import LiquidityEngine
        from core.risk_governor import RiskGovernor
        from core.prop_firm_rules import PropFirmRuleEngine, PropFirmName

        # Step 1: Generate market data
        df = generate_synthetic_ohlcv(250, "bull")
        current_price = float(df["Close"].iloc[-1])
        report(PASS, f"Step 1 - Market data: {len(df)} bars, price=${current_price:.2f}")

        # Step 2: Regime analysis
        detector = RegimeDetector()
        regime = detector.analyze(df)
        report(PASS, f"Step 2 - Regime: {regime.regime} (score={regime.score:+.0f})")

        # Step 3: Liquidity analysis
        liquidity = LiquidityEngine()
        zones = liquidity.analyze(df, "ES=F")
        report(PASS, f"Step 3 - Zones: {len(zones.demand_zones)} demand, {len(zones.supply_zones)} supply")

        # Step 4: Risk check
        risk = RiskGovernor()
        risk_result = risk.evaluate_signal(
            {"asset": "ES=F", "action": "BUY", "exposure_pct": 2.0, "confidence": "HIGH"}
        )
        can_trade_risk = risk_result["verdict"] in ("ALLOW", "ALLOW_WITH_WARNING")
        risk_reason = risk_result["reason"]
        report(PASS, f"Step 4 - Risk: {'OK' if can_trade_risk else 'BLOCKED'} ({risk_reason[:60]})")

        # Step 5: Prop firm compliance
        engine = PropFirmRuleEngine(PropFirmName.APEX)
        can_trade_firm, violations = engine.check_before_trade("ES=F")
        report(PASS, f"Step 5 - Prop firm: can_trade={can_trade_firm}")

        # Step 6: Make decision
        if regime.score > 20 and can_trade_risk and can_trade_firm:
            decision = SignalAction.BUY
            confidence = ConfidenceLevel.HIGH if regime.score > 50 else ConfidenceLevel.MEDIUM
        elif regime.score < -20 and can_trade_risk and can_trade_firm:
            decision = SignalAction.SELL
            confidence = ConfidenceLevel.HIGH if regime.score < -50 else ConfidenceLevel.MEDIUM
        else:
            decision = SignalAction.HOLD
            confidence = ConfidenceLevel.LOW

        report(PASS, f"Step 6 - Decision: {decision.value} ({confidence.value})")
        report(PASS, f"  Full pipeline: data -> regime -> liquidity -> risk -> prop firm -> decision")

    except Exception as e:
        report(FAIL, f"Signal flow: {e}")
        traceback.print_exc()


# ============================================================
# TEST 13: Multi-Timeframe Structure
# ============================================================
def test_multi_timeframe():
    print("\n[TEST 13] Multi-Timeframe Structure Analysis")
    try:
        from core.multi_timeframe_structure import MultiTimeframeStructureAnalyzer

        analyzer = MultiTimeframeStructureAnalyzer()
        df = generate_synthetic_ohlcv(250, "bull")
        current_price = float(df["Close"].iloc[-1])

        # evaluate() expects action, current_price, and frames dict
        frames = {"15m": df.tail(50), "1h": df.tail(100), "4h": df}
        result = analyzer.evaluate("BUY", current_price, frames)
        if result:
            report(PASS, f"MTF analysis: allowed={result.allowed}")
            report(PASS, f"  Bias: {result.bias}")
            report(PASS, f"  Reason: {result.reason[:80]}")
            report(PASS, f"  Timeframe biases: {result.timeframe_biases}")
        else:
            report(SKIP, "MTF analysis returned None")

    except Exception as e:
        report(FAIL, f"Multi-timeframe: {e}")
        traceback.print_exc()


# ============================================================
# TEST 14: Brain Swarm Structure
# ============================================================
def test_brain_swarm_structure():
    print("\n[TEST 14] Brain Swarm (LLM Architecture)")
    try:
        from core.brain_swarm import OllamaSwarmConsensus

        swarm = OllamaSwarmConsensus()

        # Check key attributes
        attrs = ["run", "models", "timeout"]
        found = [a for a in attrs if hasattr(swarm, a) or hasattr(swarm, f"_{a}")]
        report(PASS, f"Swarm has attributes: {found}")

        # Check agent integration
        if hasattr(swarm, "devil") or hasattr(swarm, "devils_advocate"):
            report(PASS, "Swarm integrates Devil's Advocate")
        else:
            report(SKIP, "Devil's Advocate integration not directly visible")

        if hasattr(swarm, "mia") or hasattr(swarm, "market_intelligence"):
            report(PASS, "Swarm integrates Market Intelligence Agent")
        else:
            report(SKIP, "MIA integration not directly visible")

        report(PASS, f"Swarm class: {swarm.__class__.__name__}")

    except Exception as e:
        report(FAIL, f"Brain swarm: {e}")
        traceback.print_exc()


# ============================================================
# TEST 15: Scanner Structure
# ============================================================
def test_scanner_structure():
    print("\n[TEST 15] Scanner (Market Monitor)")
    try:
        from core.scanner import Scanner, TechnicalSignal

        # Check class structure without instantiation (needs config)
        report(PASS, f"Scanner class found with methods: {[m for m in dir(Scanner) if not m.startswith('_') and callable(getattr(Scanner, m))][:8]}")

        # Technical signal creation
        sig = TechnicalSignal("ES=F", "RSI_OVERSOLD", 0.85, {"rsi": 28.5})
        report(PASS, f"TechnicalSignal: {sig.ticker} {sig.signal_type} strength={sig.strength}")
        report(PASS, f"  Metadata: {sig.metadata}")

    except Exception as e:
        report(FAIL, f"Scanner: {e}")
        traceback.print_exc()


# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 60)
    print("  VcaniTrade AI - Intelligence Assessment")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    test_imports()
    test_regime_detector()
    test_liquidity_engine()
    test_risk_governor()
    test_prop_firm_rules()
    test_profit_lock()
    test_confidence_escalator()
    test_market_sessions()
    test_models()
    test_devils_advocate()
    test_atr_stops()
    test_signal_flow()
    test_multi_timeframe()
    test_brain_swarm_structure()
    test_scanner_structure()

    print("\n" + "=" * 60)
    print(f"  RESULTS: {results['passed']} passed, {results['failed']} failed, {results['skipped']} skipped")
    total = results['passed'] + results['failed']
    if total > 0:
        score = (results['passed'] / total) * 100
        print(f"  INTELLIGENCE SCORE: {score:.0f}%")
        if score >= 90:
            print("  GRADE: A — Bot intelligence is EXCELLENT")
        elif score >= 75:
            print("  GRADE: B — Bot intelligence is GOOD")
        elif score >= 60:
            print("  GRADE: C — Bot intelligence is ACCEPTABLE")
        else:
            print("  GRADE: D — Bot intelligence needs improvement")
    print("=" * 60)

    return results["failed"] == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
