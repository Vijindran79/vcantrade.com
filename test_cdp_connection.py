import json
import urllib.request

try:
    # Test CDP connection
    response = urllib.request.urlopen('http://127.0.0.1:9222/json', timeout=5)
    data = json.loads(response.read())
    print('CDP Connection: SUCCESS')
    print(f'Number of tabs: {len(data)}')
    
    # Check if TradingView tabs exist
    tv_tabs = [t for t in data if 'tradingview.com' in t.get('url', '').lower()]
    print(f'TradingView tabs: {len(tv_tabs)}')
    
    if tv_tabs:
        print(f'\nFirst TradingView tab: {tv_tabs[0].get("title", "")[:60]}')
        print(f'URL: {tv_tabs[0].get("url", "")[:80]}')
    else:
        print('\nNO TradingView tabs found!')
        print('Make sure TradingView is open in Chrome')
        
except Exception as e:
    print(f'CDP Connection FAILED: {e}')