#!/usr/bin/env python
"""Full system test - writes results to test_output.txt"""

import sys
import io

# Force UTF-8 on Windows for standalone runs only.
if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

results = []


def log(msg):
    results.append(msg)
    print(msg, flush=True)


log("=" * 60)
log("VcaniTrade AI - Full System Test")
log("=" * 60)

# Test 1: Config
try:
    import config

    log(f"[OK] Config loaded")
    log(f"   PROP_FIRM: {config.PROP_FIRM_NAME}")
    log(f"   OLLAMA_URL: {config.OLLAMA_BASE_URL}")
    log(f"   MODEL: {config.OLLAMA_MODEL}")
    log(f"   TIMEOUT: {config.LLM_TIMEOUT}")
except Exception as e:
    log(f"[FAIL] Config error: {e}")

# Test 2: Models
try:
    from core.models import MarketDataPoint, SignalAction, ConfidenceLevel

    log("[OK] Models loaded")
except Exception as e:
    log(f"[FAIL] Models error: {e}")

# Test 3: Prop Firm Rules
try:
    from core.prop_firm_rules import PropFirmRuleEngine, PropFirmName

    engine = PropFirmRuleEngine(PropFirmName.TOPSTEP)
    can_trade, violations = engine.check_before_trade("BTC-USD", 50.0)
    log(f"[OK] Prop Firm Engine works")
    log(f"   Can trade: {can_trade}, Violations: {len(violations)}")
except Exception as e:
    log(f"[FAIL] Prop Firm error: {e}")
    import traceback

    traceback.print_exc()

# Test 4: Swarm Consensus
try:
    from core.brain_swarm import OllamaSwarmConsensus
    import asyncio

    async def test_swarm():
        consensus = OllamaSwarmConsensus()
        data = MarketDataPoint(
            asset="BTC-USD",
            price=85000.0,
            volume=1000.0,
            indicators={
                "RSI": 35.0,
                "SIGNAL_TYPE": "RSI_OVERSOLD",
                "SIGNAL_STRENGTH": 0.75,
            },
        )
        output, transcript = await consensus.run(data)
        return output.action.value, output.confidence.value

    action, confidence = asyncio.get_event_loop().run_until_complete(test_swarm())
    log(f"[OK] Swarm Consensus works")
    log(f"   Action: {action}, Confidence: {confidence}")
except Exception as e:
    log(f"[FAIL] Swarm error: {e}")
    import traceback

    traceback.print_exc()

# Test 5: Scanner
try:
    from core.scanner import CloudScanner

    log("[OK] Scanner loaded")
except Exception as e:
    log(f"[FAIL] Scanner error: {e}")

# Test 6: Dashboard
try:
    from PyQt6.QtWidgets import QApplication
    from ui.dashboard import CommandCenter

    app = QApplication(sys.argv)
    cmd = CommandCenter()
    log("[OK] Dashboard created")
    cmd.show()
    log("[OK] Dashboard shown")
except Exception as e:
    log(f"[FAIL] Dashboard error: {e}")
    import traceback

    traceback.print_exc()

# Summary
log("\n" + "=" * 60)
log("TEST SUMMARY")
log("=" * 60)
log(f"Tests run: {len(results)}")
log(f"Errors: {sum(1 for r in results if '[FAIL]' in r)}")
log(f"Success: {sum(1 for r in results if '[OK]' in r)}")

# Write to file
with open("test_output.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(results))

print("\n[OK] Results written to test_output.txt")
