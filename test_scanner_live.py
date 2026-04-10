#!/usr/bin/env python
"""Quick live scanner test - does the bot actually detect signals?"""
import sys
import io
import asyncio

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('scanner_test.log', encoding='utf-8', mode='w'),
        logging.StreamHandler(sys.stdout)
    ],
    force=True
)

async def main():
    from core.scanner import CloudScanner
    
    scanner = CloudScanner()
    print("=" * 60)
    print("LIVE SCANNER TEST - Checking 10 tickers for signals")
    print("=" * 60)
    print(f"Tickers: {scanner.tickers}")
    print()
    
    # Scan once
    print("Scanning markets...")
    signals = await scanner.scan_all_tickers()
    
    if signals:
        print(f"\n🔥 Found {len(signals)} signal(s)!")
        for s in signals:
            print(f"  - {s.ticker}: {s.signal_type} (strength: {s.strength:.2f})")
        
        # Process signals through swarm
        print("\nRunning AI analysis...")
        result = await scanner.process_signals(signals)
        
        if result:
            print(f"\n🎯 AI Decision:")
            print(f"  Action: {result['action']}")
            print(f"  Confidence: {result['confidence']:.2f}")
            print(f"  Entry: ${result.get('entry_price', 0):.2f}")
            print(f"  TP: ${result.get('take_profit', 0):.2f}")
            print(f"  SL: ${result.get('stop_loss', 0):.2f}")
            print(f"  Reason: {result.get('reason', 'N/A')}")
        else:
            print("\n⚠️ No trade signal generated (confidence too low)")
    else:
        print("\n⏸️ No signals detected this scan - markets are quiet")
        print("This is normal - the scanner checks every 10 seconds")
    
    print("\n✅ Scanner test complete!")
    print("Check scanner_test.log for full details")

if __name__ == "__main__":
    asyncio.run(main())
