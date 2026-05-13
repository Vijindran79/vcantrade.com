"""
Quick test to verify Chrome CDP connection works.
Run this to confirm the "blindfold" is removed.
"""
import json
import urllib.request

try:
    # Test CDP connection
    response = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
    data = json.loads(response.read().decode("utf-8"))

    print("=" * 60)
    print("[SUCCESS] Chrome CDP connection works!")
    print("=" * 60)
    print(f"\nFound {len(data)} tab(s):\n")

    for i, tab in enumerate(data):
        url = tab.get("url", "N/A")
        title = tab.get("title", "N/A")
        print(f"  [{i+1}] {title}")
        print(f"      URL: {url[:80]}")
        print()

    print("=" * 60)
    print("[OK] The 'blindfold' is REMOVED!")
    print("[OK] Your bot can now 'see' the charts!")
    print("=" * 60)

except Exception as e:
    print("=" * 60)
    print("[FAIL] Cannot connect to Chrome CDP")
    print(f"Error: {e}")
    print("\nTroubleshooting:")
    print("1. Make sure Chrome launched with --remote-debugging-port=9222")
    print("2. Check: netstat -ano | findstr 9222")
    print("=" * 60)
