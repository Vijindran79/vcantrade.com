"""
Test Ollama Connection on Vast.ai Server
Run this on your LAPTOP to diagnose connection issues
"""
import requests
import socket
import time

OLLAMA_IP = "localhost"
PORTS_TO_TEST = [11434, 17197, 80, 443, 8000]

print(f"🔍 Testing connection to {OLLAMA_IP}...\n")

# Test 1: Basic TCP connectivity
print("=" * 60)
print("TEST 1: TCP Port Scan")
print("=" * 60)
for port in PORTS_TO_TEST:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((OLLAMA_IP, port))
        if result == 0:
            print(f"✅ Port {port}: OPEN")
        else:
            print(f"❌ Port {port}: CLOSED/FILTERED")
        sock.close()
    except Exception as e:
        print(f"❌ Port {port}: ERROR - {e}")

print("\n" + "=" * 60)
print("TEST 2: HTTP Ollama API Test")
print("=" * 60)

# Test 3: Try Ollama API on different ports
for port in [11434, 17197]:
    url = f"http://{OLLAMA_IP}:{port}/api/tags"
    print(f"\nTesting: {url}")
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            print(f"✅ SUCCESS! Ollama is running on port {port}")
            models = response.json().get('models', [])
            print(f"📦 Available models: {[m['name'] for m in models]}")
            break
        else:
            print(f"❌ HTTP {response.status_code} - Wrong port or service")
    except requests.exceptions.Timeout:
        print(f"❌ TIMEOUT - Port is blocked by firewall")
    except requests.exceptions.ConnectionError:
        print(f"❌ CONNECTION REFUSED - Nothing listening on this port")
    except Exception as e:
        print(f"❌ ERROR: {e}")

print("\n" + "=" * 60)
print("RECOMMENDATIONS:")
print("=" * 60)
print("""
If ALL ports show CLOSED/FILTERED:
→ Your Vast.ai server has a FIREWALL blocking external connections
→ You need to open port 11434 in the Vast.ai dashboard

If port 11434 shows TIMEOUT:
→ Ollama is not running or not bound to 0.0.0.0
→ SSH into Vast.ai and run: ollama serve &

If port 11434 shows SUCCESS:
→ Update config.py with the working port
→ Restart main.py
""")
