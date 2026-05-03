import socket
import json
import sys

DEFAULT_HOST = "192.168.1.100"
DEFAULT_PORT = 5555
TIMEOUT = 10


def send_command(host, port, action):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(TIMEOUT)
            s.connect((host, port))
            s.send(json.dumps({"action": action}).encode("utf-8"))
            data = s.recv(4096).decode("utf-8")
            return json.loads(data) if data else {"status": "ERROR", "message": "No response"}
    except socket.timeout:
        return {"status": "ERROR", "message": "Connection timed out"}
    except ConnectionRefusedError:
        return {"status": "ERROR", "message": f"Connection refused at {host}:{port}"}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}


def buy_sim(host=DEFAULT_HOST, port=DEFAULT_PORT):
    return send_command(host, port, "BUY_SIM")


def buy_real(host=DEFAULT_HOST, port=DEFAULT_PORT):
    return send_command(host, port, "BUY_REAL")


def sell_sim(host=DEFAULT_HOST, port=DEFAULT_PORT):
    return send_command(host, port, "SELL_SIM")


def sell_real(host=DEFAULT_HOST, port=DEFAULT_PORT):
    return send_command(host, port, "SELL_REAL")


def flatten(host=DEFAULT_HOST, port=DEFAULT_PORT):
    return send_command(host, port, "FLATTEN")


ACTIONS = {
    "BUY_SIM": buy_sim,
    "BUY_REAL": buy_real,
    "SELL_SIM": sell_sim,
    "SELL_REAL": sell_real,
    "FLATTEN": flatten,
}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Brain Client - Lion's Brain to Lion's Hand")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Desktop IP")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Desktop port")
    parser.add_argument("action", nargs="?", help="Action to execute")
    args = parser.parse_args()

    if not args.action:
        parser.print_help()
        print(f"\nActions: {', '.join(ACTIONS)}")
        print(f"Example: python brain_client.py --host 192.168.1.50 BUY_SIM")
        sys.exit(0)

    action = args.action.upper()
    func = ACTIONS.get(action)
    if not func:
        print(f"[ERROR] Unknown: {action}. Available: {', '.join(ACTIONS)}")
        sys.exit(1)

    print(f"[SENDING] {action} -> {args.host}:{args.port}")
    result = func(args.host, args.port)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "SUCCESS" else 1)
