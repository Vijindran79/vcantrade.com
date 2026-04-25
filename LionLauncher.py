"""
One-click launcher for The Lion.

Shows the startup switchboard, runs human-readable preflight checks, and then
hands off to the main dashboard without exposing raw Python crashes to the
operator.
"""

from __future__ import annotations

import socket
import sys
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests
from PyQt6.QtWidgets import QApplication

import config
import main as lion_main
from core.ollama_utils import build_ollama_url, normalize_ollama_base_url
from ui.lion_switchboard import choose_launch_profile


@dataclass
class PreflightResult:
    ok: bool
    title: str
    detail: str
    fix_tip: str = ""


def _check_cloud_brain() -> PreflightResult:
    base_url = normalize_ollama_base_url(config.OLLAMA_BASE_URL)
    try:
        response = requests.get(build_ollama_url(base_url, "api/tags"), timeout=6)
        response.raise_for_status()
        return PreflightResult(True, "Cloud Brain", f"Connected to Ollama at {base_url}")
    except Exception as exc:
        return PreflightResult(
            False,
            "Cloud Brain",
            f"Could not reach Ollama at {base_url}: {exc}",
            "Check your Vast.ai / SSH tunnel first, then confirm the Ollama port is forwarded and the server is running.",
        )


def _check_browser_cdp() -> PreflightResult:
    cdp_url = str(getattr(config, "BROWSER_CDP_URL", "http://127.0.0.1:9223") or "").strip()
    try:
        parsed = urlsplit(cdp_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 9223
        with socket.create_connection((host, port), timeout=3):
            return PreflightResult(True, "Browser Port", f"Chrome CDP is reachable at {host}:{port}")
    except Exception as exc:
        return PreflightResult(
            False,
            "Browser Port",
            f"Cannot reach Chrome CDP at {cdp_url}: {exc}",
            'Launch Chrome with `--remote-debugging-port=9223` or update `BROWSER_CDP_URL` to the right port.',
        )


def _check_mt5() -> PreflightResult:
    try:
        from core.mt5_executor import MT5Executor

        executor = MT5Executor()
        if executor.initialize():
            executor.shutdown()
            return PreflightResult(True, "MetaTrader 5", "MT5 terminal connection succeeded")
        return PreflightResult(
            False,
            "MetaTrader 5",
            "MT5 terminal did not accept the connection.",
            "Open MetaTrader 5 first, enable Algo Trading / API access, and make sure the correct account is logged in.",
        )
    except Exception as exc:
        return PreflightResult(
            False,
            "MetaTrader 5",
            f"MT5 preflight failed: {exc}",
            "Install the `MetaTrader5` Python package and keep the MT5 desktop terminal open before launching The Lion.",
        )


def _print_preflight(results: list[PreflightResult]) -> bool:
    print("=" * 68)
    print("Lion Launcher Preflight")
    print("=" * 68)
    all_ok = True
    for result in results:
        state = "OK" if result.ok else "FAIL"
        print(f"[{state}] {result.title}: {result.detail}")
        if not result.ok and result.fix_tip:
            print(f"      Fix it: {result.fix_tip}")
            all_ok = False
    print("=" * 68)
    return all_ok


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    profile = choose_launch_profile()
    config.SHOW_STARTUP_SWITCHBOARD = False

    # Resolve execution mode from profile or config (support both EXECUTION_MODE and EXECUTOR_TYPE)
    exec_mode = str(
        getattr(profile, "execution_mode", "")
        or getattr(config, "EXECUTION_MODE", "")
        or getattr(config, "EXECUTOR_TYPE", "")
        or "UI"
    ).upper().strip()

    checks = [_check_cloud_brain()]
    if exec_mode == "UI":
        checks.append(_check_browser_cdp())
    elif exec_mode == "MT5":
        checks.append(_check_mt5())
    else:
        # Unknown mode — check both so the operator sees what is missing
        checks.append(_check_browser_cdp())
        checks.append(_check_mt5())

    if not _print_preflight(checks):
        print("Launcher stopped before startup so you can fix the items above.")
        return 1

    try:
        lion_main.main()
        return 0
    except Exception as exc:
        print("=" * 68)
        print("Lion Launcher stopped the bot cleanly because startup failed.")
        print(f"Problem: {exc}")
        print("Fix it: Re-run the launcher after checking the preflight targets above.")
        print("=" * 68)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
