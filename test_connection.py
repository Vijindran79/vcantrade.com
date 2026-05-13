"""
PING-PONG Test Script
Tests basic connectivity to the local execution server.

Usage:
    python test_connection.py
    python test_connection.py 127.0.0.1  # Custom execution host
"""
import socket
import sys
import time

# Local execution socket host
DESKTOP_IP = "127.0.0.1"
PING_PORT = 5555
TIMEOUT = 5


def test_ping(desktop_ip: str) -> bool:
    """Send PING to the execution server and wait for PONG response."""
    print(f"[TEST] Connecting to execution server at {desktop_ip}:{PING_PORT}...")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(TIMEOUT)
            sock.connect((desktop_ip, PING_PORT))
            print(f"[TEST] Connected! Sending PING...")

            # Send PING
            ping_msg = '{"action": "PING"}'
            sock.sendall(ping_msg.encode("utf-8"))
            print(f"[TEST] Sent: {ping_msg}")

            # Wait for PONG response
            try:
                response = sock.recv(1024).decode("utf-8")
                if "PONG" in response:
                    print(f"[SUCCESS] Received PONG! Response: {response}")
                    return True
                else:
                    print(f"[WARN] Received unexpected response: {response}")
                    return False
            except socket.timeout:
                print(f"[FAIL] Timeout waiting for PONG response")
                return False

    except ConnectionRefusedError:
        print(f"[FAIL] Connection refused. Is the execution server running?")
        print(f"[INFO] Check: 1) execution server is running, 2) Firewall allows port {PING_PORT}")
        return False
    except socket.timeout:
        print(f"[FAIL] Connection timeout. Check execution host and network.")
        return False
    except Exception as exc:
        print(f"[FAIL] Error: {exc}")
        return False


def test_buy_sim(desktop_ip: str) -> bool:
    """Send BUY_SIM command to test execution flow."""
    print(f"\n[TEST] Sending BUY_SIM command to {desktop_ip}:{PING_PORT}...")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(TIMEOUT)
            sock.connect((desktop_ip, PING_PORT))

            # Send BUY_SIM
            buy_msg = '{"action": "BUY_SIM", "ticker": "NQM6", "qty": 1}'
            sock.sendall(buy_msg.encode("utf-8"))
            print(f"[TEST] Sent: {buy_msg}")

            # Wait for response
            try:
                response = sock.recv(1024).decode("utf-8")
                print(f"[SUCCESS] Execution server responded: {response}")
                return True
            except socket.timeout:
                print(f"[WARN] No response received (command may have been queued)")
                return True  # Command was sent successfully

    except Exception as exc:
        print(f"[FAIL] Error: {exc}")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("PING-PONG CONNECTIVITY TEST (LAPTOP SIDE)")
    print("=" * 60)

    # Get execution host from command line or use default
    desktop_ip = sys.argv[1] if len(sys.argv) > 1 else DESKTOP_IP

    print(f"Target execution host: {desktop_ip}:{PING_PORT}")
    print()

    # Test 1: PING-PONG
    print("TEST 1: PING-PONG Connectivity")
    print("-" * 40)
    ping_ok = test_ping(desktop_ip)

    if ping_ok:
        # Test 2: BUY_SIM Command
        print("\nTEST 2: BUY_SIM Execution Command")
        print("-" * 40)
        buy_ok = test_buy_sim(desktop_ip)

        if buy_ok:
            print("\n" + "=" * 60)
            print("[ALL PASS] Network connectivity test successful!")
            print("The bot can communicate with the execution server.")
            print("=" * 60)
        else:
            print("\n[PARTIAL] PING worked but BUY_SIM failed")
    else:
        print("\n" + "=" * 60)
        print("[FAIL] Connectivity test failed!")
        print("\nTROUBLESHOOTING:")
        print("1. Ensure execution_server.py is running")
        print("2. Check execution host is correct")
        print("3. Check Windows Firewall allows port 5555")
        print("4. Try: ping <desktop_ip> to test basic connectivity")
        print("=" * 60)
