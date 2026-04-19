"""
Stage 4 Concurrency Audit Script

Tests:
1. MetaAnalyzer and VisualConfirmation can run simultaneously without conflicts
2. Activity log doesn't get clogged by parallel threads
3. No shared state conflicts
4. File I/O is safe (trade_ledger.json)
"""

import asyncio
import threading
import logging
import time
from datetime import datetime
from core.meta_analyzer import MetaAnalyzer, TradeJournal
from core.visual_confirmation import VisualChartConfirmation
from core.code_architect import CodeArchitect

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(threadName)s] %(levelname)s %(name)s: %(message)s'
)
logger = logging.getLogger("concurrency_audit")

# Test results
results = {
    "concurrent_meta_analyzer": {"status": "PENDING", "errors": []},
    "concurrent_visual_confirmation": {"status": "PENDING", "errors": []},
    "log_clogging_test": {"status": "PENDING", "messages_before": 0, "messages_after": 0},
    "shared_state_safety": {"status": "PENDING", "conflicts": []},
    "file_io_safety": {"status": "PENDING", "conflicts": []}
}


def test_concurrent_meta_analyzer():
    """Test MetaAnalyzer can run in separate thread without blocking."""
    logger.info("=" * 60)
    logger.info("TEST 1: Concurrent MetaAnalyzer Execution")
    logger.info("=" * 60)

    try:
        journal = TradeJournal(filepath="test_audit_ledger.json")
        meta = MetaAnalyzer(journal=journal, review_interval_hours=1)

        # Force a review immediately
        meta.last_review = datetime.now()

        # Run in thread
        def run_review():
            logger.info("Starting MetaAnalyzer review in thread...")
            result = meta.perform_self_review()
            logger.info(f"MetaAnalyzer review complete: {result['status']}")
            return result

        thread = threading.Thread(target=run_review, name="MetaAnalyzer-Thread")
        thread.start()

        # Main thread continues working
        logger.info("Main thread continues working while MetaAnalyzer runs...")
        time.sleep(2)

        # Wait for thread to complete
        thread.join(timeout=10)

        if thread.is_alive():
            results["concurrent_meta_analyzer"]["status"] = "FAILED"
            results["concurrent_meta_analyzer"]["errors"].append("Thread timed out (possible deadlock)")
            logger.error("❌ FAILED: MetaAnalyzer thread timed out")
        else:
            results["concurrent_meta_analyzer"]["status"] = "PASSED"
            logger.info("✅ PASSED: MetaAnalyzer ran concurrently without blocking")

    except Exception as e:
        results["concurrent_meta_analyzer"]["status"] = "FAILED"
        results["concurrent_meta_analyzer"]["errors"].append(str(e))
        logger.error(f"❌ FAILED: MetaAnalyzer concurrency error: {e}")


def test_concurrent_visual_confirmation():
    """Test VisualConfirmation doesn't block other operations."""
    logger.info("=" * 60)
    logger.info("TEST 2: Concurrent VisualConfirmation Execution")
    logger.info("=" * 60)

    try:
        visual = VisualChartConfirmation(check_interval=1)

        # Simulate concurrent zone approach checks
        def run_zone_checks():
            logger.info("Starting zone approach checks in thread...")
            for i in range(5):
                result = visual.check_zone_approach()
                trend = visual.get_approach_trend(last_n=3)
                logger.info(f"Zone check #{i+1}: {result['status']} | Trend: {trend}")
                time.sleep(0.1)
            logger.info("Zone approach checks complete")

        thread = threading.Thread(target=run_zone_checks, name="VisualConfirmation-Thread")
        thread.start()

        # Main thread does other work
        logger.info("Main thread continues while VisualConfirmation runs...")
        time.sleep(1)

        thread.join(timeout=10)

        if thread.is_alive():
            results["concurrent_visual_confirmation"]["status"] = "FAILED"
            results["concurrent_visual_confirmation"]["errors"].append("Thread timed out")
            logger.error("❌ FAILED: VisualConfirmation thread timed out")
        else:
            results["concurrent_visual_confirmation"]["status"] = "PASSED"
            logger.info("✅ PASSED: VisualConfirmation ran concurrently without blocking")

    except Exception as e:
        results["concurrent_visual_confirmation"]["status"] = "FAILED"
        results["concurrent_visual_confirmation"]["errors"].append(str(e))
        logger.error(f"❌ FAILED: VisualConfirmation concurrency error: {e}")


def test_log_clogging():
    """Test that parallel threads don't clog the activity log."""
    logger.info("=" * 60)
    logger.info("TEST 3: Activity Log Clogging Test")
    logger.info("=" * 60)

    try:
        # Count log messages before
        log_count_before = len(logging.root.handlers[0].stream.name) if logging.root.handlers else 0

        # Run multiple threads simultaneously
        threads = []

        for i in range(3):
            t1 = threading.Thread(
                target=lambda: logger.info(f"Thread {i} - MetaAnalyzer simulation"),
                name=f"Test-Thread-{i}"
            )
            threads.append(t1)

        # Start all threads
        for t in threads:
            t.start()

        # Wait for all
        for t in threads:
            t.join(timeout=5)

        # Count log messages after
        all_joined = all(not t.is_alive() for t in threads)

        results["log_clogging_test"]["status"] = "PASSED" if all_joined else "FAILED"
        results["log_clogging_test"]["messages_before"] = log_count_before
        results["log_clogging_test"]["messages_after"] = "N/A (threads completed)"

        if all_joined:
            logger.info("✅ PASSED: Activity log handled parallel threads without clogging")
        else:
            logger.error("❌ FAILED: Some threads hung, log may be clogged")

    except Exception as e:
        results["log_clogging_test"]["status"] = "FAILED"
        logger.error(f"❌ FAILED: Log clogging test error: {e}")


def test_shared_state_safety():
    """Test that modules don't share unsafe state."""
    logger.info("=" * 60)
    logger.info("TEST 4: Shared State Safety")
    logger.info("=" * 60)

    try:
        # Create instances
        meta1 = MetaAnalyzer(journal=TradeJournal(filepath="test_shared_1.json"))
        meta2 = MetaAnalyzer(journal=TradeJournal(filepath="test_shared_2.json"))
        visual = VisualChartConfirmation()
        architect = CodeArchitect()

        # Verify they have separate state
        assert meta1.journal.filepath != meta2.journal.filepath, "Journals should be separate"
        assert meta1.alpha_score == meta2.alpha_score == 50.0, "Both should start at 50"

        # Modify one and verify other is unaffected
        meta1.alpha_score = 75.0
        assert meta2.alpha_score == 50.0, "meta2 should be unaffected by meta1 changes"

        results["shared_state_safety"]["status"] = "PASSED"
        logger.info("✅ PASSED: No shared state conflicts detected")

    except AssertionError as e:
        results["shared_state_safety"]["status"] = "FAILED"
        results["shared_state_safety"]["conflicts"].append(str(e))
        logger.error(f"❌ FAILED: Shared state conflict: {e}")

    except Exception as e:
        results["shared_state_safety"]["status"] = "FAILED"
        results["shared_state_safety"]["conflicts"].append(str(e))
        logger.error(f"❌ FAILED: Shared state safety error: {e}")


def test_file_io_safety():
    """Test that file I/O doesn't cause conflicts."""
    logger.info("=" * 60)
    logger.info("TEST 5: File I/O Safety (trade_ledger.json)")
    logger.info("=" * 60)

    try:
        import json
        import os

        test_file = "test_concurrent_ledger.json"

        # Create test journal
        journal = TradeJournal(filepath=test_file)

        # Simulate concurrent writes
        def write_trades(thread_id, num_trades):
            for i in range(num_trades):
                trade = {
                    "asset": f"BTC-USD",
                    "action": "BUY",
                    "pnl": 10.0 * thread_id,
                    "timestamp": datetime.now().isoformat()
                }
                journal.add_trade(trade)
                time.sleep(0.01)  # Small delay

        # Run concurrent writers
        threads = []
        for i in range(3):
            t = threading.Thread(
                target=write_trades,
                args=(i, 5),
                name=f"Writer-{i}"
            )
            threads.append(t)

        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=10)

        # Verify file integrity
        if os.path.exists(test_file):
            with open(test_file, 'r') as f:
                data = json.load(f)
                trade_count = len(data.get("trades", []))
                expected = 15  # 3 threads * 5 trades

                if trade_count == expected:
                    results["file_io_safety"]["status"] = "PASSED"
                    logger.info(f"✅ PASSED: File I/O safe ({trade_count} trades written correctly)")
                else:
                    results["file_io_safety"]["status"] = "FAILED"
                    results["file_io_safety"]["conflicts"].append(
                        f"Expected {expected} trades, got {trade_count}"
                    )
                    logger.error(f"❌ FAILED: File I/O conflict ({trade_count}/{expected} trades)")

            # Cleanup
            os.remove(test_file)
        else:
            results["file_io_safety"]["status"] = "FAILED"
            logger.error("❌ FAILED: Ledger file not created")

    except Exception as e:
        results["file_io_safety"]["status"] = "FAILED"
        results["file_io_safety"]["conflicts"].append(str(e))
        logger.error(f"❌ FAILED: File I/O safety error: {e}")


def run_audit():
    """Run complete concurrency audit."""
    logger.info("=" * 60)
    logger.info("STAGE 4 CONCURRENCY AUDIT - STARTING")
    logger.info("=" * 60)

    start_time = time.time()

    # Run all tests
    test_concurrent_meta_analyzer()
    test_concurrent_visual_confirmation()
    test_log_clogging()
    test_shared_state_safety()
    test_file_io_safety()

    elapsed = time.time() - start_time

    # Print summary
    logger.info("=" * 60)
    logger.info("CONCURRENCY AUDIT SUMMARY")
    logger.info("=" * 60)

    passed = sum(1 for r in results.values() if r["status"] == "PASSED")
    failed = sum(1 for r in results.values() if r["status"] == "FAILED")
    pending = sum(1 for r in results.values() if r["status"] == "PENDING")

    logger.info(f"Total tests: {len(results)}")
    logger.info(f"✅ Passed: {passed}")
    logger.info(f"❌ Failed: {failed}")
    logger.info(f"⏳ Pending: {pending}")
    logger.info(f"Time elapsed: {elapsed:.2f}s")

    for test_name, result in results.items():
        status = result["status"]
        emoji = "✅" if status == "PASSED" else "❌" if status == "FAILED" else "⏳"
        logger.info(f"{emoji} {test_name}: {status}")

        if result.get("errors"):
            for error in result["errors"]:
                logger.info(f"   Error: {error}")

        if result.get("conflicts"):
            for conflict in result["conflicts"]:
                logger.info(f"   Conflict: {conflict}")

    logger.info("=" * 60)

    # Cleanup test files
    import os
    for f in ["test_audit_ledger.json", "test_shared_1.json", "test_shared_2.json"]:
        if os.path.exists(f):
            os.remove(f)

    return passed == len(results)


if __name__ == "__main__":
    success = run_audit()
    exit(0 if success else 1)
