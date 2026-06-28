"""
Test script to actually run the bot and capture REAL errors
"""
import sys
import os
import logging

# Set up logging to capture errors
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot_test.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

try:
    logger.info("=" * 50)
    logger.info("Starting VcaniTrade AI Bot Test...")
    logger.info("=" * 50)
    
    # Try to import and run the main bot
    logger.info("[TEST] Importing main module...")
    import main
    
    logger.info("[TEST] Creating TradeEngine...")
    engine = main.TradeEngine()
    
    logger.info("[TEST] Bot initialized successfully!")
    logger.info("[TEST] Testing HAWK PROTOCOL...")
    
    # Test HAWK LOCK
    from core.models import LLMAnalysisOutput, SignalAction, ConfidenceLevel
    signal = LLMAnalysisOutput(
        asset="BTCUSD",
        action=SignalAction.BUY,
        entry_price=50000.0,
        stop_loss=49000.0,
        take_profit=52000.0,
        confidence=ConfidenceLevel.HIGH,
        reason="HAWK test"
    )
    
    result = engine.process_signal(signal, mode="TEACHER")
    logger.info("[TEST] HAWK PROTOCOL test complete")
    
    logger.info("=" * 50)
    logger.info("[SUCCESS] BOT TEST PASSED - Bot is working!")
    logger.info("=" * 50)
    
except Exception as e:
    logger.error("=" * 50)
    logger.error("[FAILURE] BOT TEST FAILED")
    logger.error(f"Error: {e}")
    logger.error("=" * 50)
    import traceback
    traceback.print_exc()
    sys.exit(1)