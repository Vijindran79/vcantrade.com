"""
VcaniTrade AI - Pre-Flight Diagnostic Test Script

Standalone test that bypasses the GUI to validate the execution pipeline.
Measures exact execution time and identifies latency bottlenecks.

Usage:
    python test_pipeline.py
"""

import sys
import os
import time
import logging
from datetime import datetime

# Fix Windows console encoding
if sys.platform == "win32":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import config
from core.models import MarketDataPoint
from core.swarm_consensus import SwarmConsensus
from execution.rpa_executor import RPAExecutor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("test_pipeline")

CHECK = "[OK]"
CROSS = "[FAIL]"
WARN = "[WARN]"
INFO = "[INFO]"

CHECK = "[OK]"
CROSS = "[FAIL]"
WARN = "[WARN]"
INFO = "[INFO]"


def test_pipeline():
    """Run full pipeline test: Market Data → Swarm → RPA"""
    print("=" * 70)
    print("VCANITRADE AI - PRE-FLIGHT DIAGNOSTIC TEST")
    print("=" * 70)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"LLM Model: {config.OLLAMA_MODEL}")
    print(f"Vision: {config.VLM_MODEL if config.USE_VISION else 'Disabled'}")
    print(f"RPA Mode: {'Hotkeys' if config.USE_HOTKEYS else 'Mouse'}")
    print(f"DRY_RUN: {config.DRY_RUN}")
    print("=" * 70)

    results = {}
    success = True

    # ── TEST 1: Ollama Connectivity ──────────────────────────────────────
    print("\n[TEST 1] Checking Ollama connectivity...")
    start = time.time()
    try:
        import requests

        resp = requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5)
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", [])]
        elapsed = time.time() - start
        results["ollama_connect"] = elapsed
        print(f"  {CHECK} Ollama connected in {elapsed:.2f}s")
        print(
            f"  {INFO} Available models: {', '.join(models[:5]) if models else 'None'}..."
        )

        if config.OLLAMA_MODEL not in models and not any(
            m.startswith(config.OLLAMA_MODEL) for m in models
        ):
            print(f"  {WARN} Model '{config.OLLAMA_MODEL}' not found!")
            print(f"    Run: ollama pull {config.OLLAMA_MODEL}")
            success = False
        else:
            print(f"  {CHECK} Model '{config.OLLAMA_MODEL}' is available")
    except Exception as e:
        elapsed = time.time() - start
        results["ollama_connect"] = elapsed
        print(f"  {CROSS} Ollama connection FAILED: {e}")
        print(f"    Ensure Ollama is running at {config.OLLAMA_BASE_URL}")
        return False

    # ── TEST 2: Mock Market Data Creation ────────────────────────────────
    print("\n[TEST 2] Creating mock market data...")
    start = time.time()
    market_data = MarketDataPoint(
        asset="EURUSD",
        price=1.08750,
        volume=50000,
        price_change_1h=0.35,
        price_change_24h=1.2,
        indicators={"RSI": 62.5, "MACD": 0.0023, "EMA_20": 1.08700},
    )
    elapsed = time.time() - start
    results["data_creation"] = elapsed
    print(f"  {CHECK} Market data created in {elapsed:.4f}s")
    print(f"    Asset: {market_data.asset}, Price: {market_data.price}")

    # ── TEST 3: Swarm Consensus (Full Pipeline) ──────────────────────────
    print("\n[TEST 3] Running Swarm Consensus (async parallel debate)...")
    start = time.time()
    swarm = SwarmConsensus(
        base_url=config.OLLAMA_BASE_URL,
        model=config.OLLAMA_MODEL,
        timeout=config.LLM_TIMEOUT,
    )

    try:
        output, transcript = swarm.run(market_data)
        elapsed = time.time() - start
        results["swarm_consensus"] = elapsed
        print(f"  {CHECK} Swarm completed in {elapsed:.2f}s")
        print(f"    Action: {output.action.value}")
        print(f"    Confidence: {output.confidence.value}")
        print(f"    Reason: {output.reason[:100]}")

        if transcript:
            print(f"    {INFO} Agents engaged:")
            if transcript.technical_sniper:
                print(
                    f"      - Technical Sniper: [{transcript.technical_sniper.action}] {transcript.technical_sniper.brief[:50]}"
                )
            if transcript.macro_analyst:
                print(
                    f"      - Macro Analyst: [{transcript.macro_analyst.action}] {transcript.macro_analyst.brief[:50]}"
                )
            if transcript.risk_manager:
                print(
                    f"      - Risk Manager: [{transcript.risk_manager.verdict}] {transcript.risk_manager.brief[:50]}"
                )
    except Exception as e:
        elapsed = time.time() - start
        results["swarm_consensus"] = elapsed
        print(f"  {CROSS} Swarm FAILED: {e}")
        import traceback

        traceback.print_exc()
        success = False
        output = None

    # ── TEST 4: RPA Executor (Mock/Safe) ─────────────────────────────────
    print("\n[TEST 4] Testing RPA Executor (SAFE - no real clicks)...")
    start = time.time()
    try:
        rpa = RPAExecutor()
        # Test calibration check only (no actual clicks)
        is_calibrated = rpa.calibration_manager.is_calibrated()
        elapsed = time.time() - start
        results["rpa_check"] = elapsed

        if is_calibrated:
            print(f"  {CHECK} RPA calibration check passed in {elapsed:.4f}s")
            status = rpa.calibration_manager.get_calibration_status()
            done = sum(1 for v in status.values() if v)
            print(f"    Calibrated points: {done}/{len(status)}")
        else:
            print(f"  {WARN} RPA NOT calibrated - run calibration wizard first")
            print(f"    This is expected on first run")
    except Exception as e:
        elapsed = time.time() - start
        results["rpa_check"] = elapsed
        print(f"  {CROSS} RPA check FAILED: {e}")

    # ── TEST 5: RPA Dry-Run (if signal is not HOLD) ──────────────────────
    if output and output.action.value != "HOLD":
        print(f"\n[TEST 5] RPA Dry-Run: {output.action.value} {market_data.asset}")
        start = time.time()
        try:
            from core.models import TradeRecord

            trade = TradeRecord(
                asset=output.asset,
                action=output.action,
                entry_price=output.entry_price or market_data.price,
                stop_loss=output.stop_loss,
                take_profit=output.take_profit,
                confidence=output.confidence,
                ai_reason=output.reason,
                mode="TEACHER",
            )

            # Execute in dry-run mode (safe)
            original_dry_run = config.DRY_RUN
            config.DRY_RUN = True
            result = rpa.execute_trade(trade)
            config.DRY_RUN = original_dry_run

            elapsed = time.time() - start
            results["rpa_dry_run"] = elapsed

            if result:
                print(f"  {CHECK} RPA dry-run completed in {elapsed:.2f}s")
                print(f"    Trade executed: {trade.action.value} {trade.asset}")
            else:
                print(f"  {CROSS} RPA dry-run FAILED")
                success = False
        except Exception as e:
            elapsed = time.time() - start
            results["rpa_dry_run"] = elapsed
            print(f"  {CROSS} RPA dry-run FAILED: {e}")
            import traceback

            traceback.print_exc()
            success = False
    else:
        print("\n[TEST 5] RPA Dry-Run: Skipped (signal is HOLD or no signal)")
        results["rpa_dry_run"] = "skipped"

    # ── SUMMARY ──────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 70)

    for test_name, duration in results.items():
        if duration is None:
            status = "FAILED"
            duration_str = "N/A"
        elif isinstance(duration, str):
            status = "SKIPPED"
            duration_str = duration
        elif duration < 1.0:
            status = "PASS"
            duration_str = f"{duration * 1000:.0f}ms"
        elif duration < 5.0:
            status = "WARN"
            duration_str = f"{duration:.2f}s"
        else:
            status = "SLOW"
            duration_str = f"{duration:.2f}s"

        print(f"  {test_name:25s} [{status:5s}] {duration_str:>10s}")

    # Bottleneck analysis
    print("\n" + "-" * 70)
    print("BOTTLENECK ANALYSIS:")

    numeric_results = {
        k: v
        for k, v in results.items()
        if isinstance(v, (int, float)) and v is not None
    }

    if numeric_results:
        slowest = max(numeric_results, key=numeric_results.get)
        fastest = min(numeric_results, key=numeric_results.get)
        total_time = sum(numeric_results.values())

        print(f"  Fastest:  {fastest} ({numeric_results[fastest] * 1000:.0f}ms)")
        print(f"  Slowest:  {slowest} ({numeric_results[slowest]:.2f}s) ← BOTTLENECK")
        print(f"  Total:    {total_time:.2f}s")

        if slowest in ["swarm_consensus"]:
            print(f"\n  ⚠ LLM inference is the primary bottleneck.")
            print(
                f"    Consider: Using smaller models (qwen2.5-coder:1.5b) or cloud GPU"
            )
        elif slowest == "ollama_connect":
            print(f"\n  ⚠ Ollama connection is slow.")
            print(f"    Consider: Running Ollama on same machine, checking network")

    print("=" * 70)

    if success:
        print("\n" + "=" * 70)
        print("ALL TESTS PASSED - Pipeline is ready for GUI launch")
        print("=" * 70)
        return True
    else:
        print("\n" + "=" * 70)
        print("SOME TESTS FAILED - Review errors above before launching GUI")
        print("=" * 70)
        return False


if __name__ == "__main__":
    success = test_pipeline()
    sys.exit(0 if success else 1)
