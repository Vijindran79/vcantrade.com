"""
Shared utilities for background QThreads.
Contains alert helpers, vision throttles, and config-driven predicates
that were previously inlined inside main.py.
"""

import sys
import os
import re
import time
import threading
import logging

try:
    import winsound
    _ALERT_SOUND_AVAILABLE = True
except ImportError:
    _ALERT_SOUND_AVAILABLE = False  # Non-Windows platforms

import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Passive visual mode predicate
# ---------------------------------------------------------------------------

def _is_passive_visual_mode() -> bool:
    """Return True when the bot must not capture browser/desktop screenshots."""
    execution_mode = str(getattr(config, "EXECUTION_MODE", "")).upper().strip()
    trading_surface = str(getattr(config, "TRADING_SURFACE", "")).upper().strip()
    return execution_mode in {
        "TV_DESKTOP",
        "TRADOVATE",
    } or trading_surface in {
        "TV_DESKTOP",
        "TRADOVATE",
        "TRADINGVIEW_DESKTOP",
        "TRADINGVIEW_TRADOVATE",
    }


# ---------------------------------------------------------------------------
# Alert sounds
# ---------------------------------------------------------------------------

def _play_trade_alert(action: str, success: bool):
    """Play a distinctive alert sound when a trade is executed.
    BUY = rising tone (optimistic). SELL = falling tone (urgent).
    Failed trade = low buzz.
    """
    if not bool(getattr(config, "PLAY_ALERT_SOUNDS", False)):
        return
    if not _ALERT_SOUND_AVAILABLE:
        return
    try:
        if success:
            if action == "BUY":
                winsound.Beep(800, 200)
                winsound.Beep(1200, 300)
            else:
                winsound.Beep(1200, 200)
                winsound.Beep(800, 300)
        else:
            winsound.Beep(400, 500)
    except Exception:
        pass


_last_scan_tick_sound_at = 0.0


def _play_ui_alert(kind: str, action: str = "", confidence: float = 0.0):
    """Play non-blocking dashboard/narrator alert sounds."""
    if not bool(getattr(config, "PLAY_ALERT_SOUNDS", False)):
        return
    if not _ALERT_SOUND_AVAILABLE:
        return

    kind = str(kind or "signal").lower()
    if kind == "scan":
        if not bool(getattr(config, "PLAY_SCAN_TICK_SOUNDS", False)):
            return
        global _last_scan_tick_sound_at
        now = time.monotonic()
        interval = float(getattr(config, "SCAN_TICK_SOUND_INTERVAL_SECONDS", 8.0) or 8.0)
        if now - _last_scan_tick_sound_at < interval:
            return
        _last_scan_tick_sound_at = now

    action = str(action or "").upper()
    if kind == "gatekeeper":
        pattern = [(420, 140), (360, 170), (300, 260)]
    elif kind == "error":
        pattern = [(320, 280), (320, 280)]
    elif kind == "scan":
        pattern = [(640, 55)]
    elif action == "SELL":
        pattern = [(1450, 110), (1050, 140), (720, 190)]
    elif action == "BUY":
        pattern = [(720, 110), (1050, 140), (1450, 190)]
    else:
        pattern = [(880, 110), (1180, 140), (980, 110)]

    def _runner():
        try:
            for frequency, duration in pattern:
                winsound.Beep(int(frequency), int(duration))
                time.sleep(0.03)
        except Exception:
            pass

    threading.Thread(target=_runner, daemon=True).start()


_last_spoken_alert_at = 0.0


def _speak_alert(message: str, min_interval_seconds: float = 3.0):
    """Use Windows speech synthesis for important alerts when enabled."""
    if not bool(getattr(config, "ENABLE_AUDIO_NARRATION", False)):
        return
    if sys.platform != "win32":
        return

    global _last_spoken_alert_at
    now = time.monotonic()
    if now - _last_spoken_alert_at < min_interval_seconds:
        return
    _last_spoken_alert_at = now

    text = re.sub(r"[^A-Za-z0-9 .,:;%$\\-]", " ", str(message or "")).strip()
    if not text:
        return
    text = text[:180]

    def _runner():
        try:
            import subprocess

            env = os.environ.copy()
            env["VCAN_SPEAK_TEXT"] = text
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        "Add-Type -AssemblyName System.Speech; "
                        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                        "$s.Rate = 1; "
                        "$s.Speak($env:VCAN_SPEAK_TEXT)"
                    ),
                ],
                env=env,
                capture_output=True,
                timeout=6,
                creationflags=flags,
            )
        except Exception:
            pass

    threading.Thread(target=_runner, daemon=True).start()


# ---------------------------------------------------------------------------
# Vision analysis cooldown throttle
# ---------------------------------------------------------------------------

VISION_ANALYSIS_COOLDOWN_SECONDS = 3.0
_vision_analysis_lock = threading.Lock()
_last_vision_analysis_at = 0.0


def _wait_for_vision_analysis_slot(label: str = "vision") -> None:
    """Throttle vision/AI image requests so browser and CPU stay responsive."""
    global _last_vision_analysis_at
    with _vision_analysis_lock:
        now = time.monotonic()
        wait_time = VISION_ANALYSIS_COOLDOWN_SECONDS - (now - _last_vision_analysis_at)
        if wait_time > 0:
            logger.info("[VISION] Cooldown %.2fs before %s analysis", wait_time, label)
            time.sleep(wait_time)
        _last_vision_analysis_at = time.monotonic()
