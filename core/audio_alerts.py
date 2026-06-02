"""
VcanTrade AI - Audio Alerts
============================
Simple sound notifications for key trading events:
- BUY/SELL execution (distinctive ascending tone)
- Confidence level increase (gentle rising tone)
- Trade failure (descending warning tone)
- Hand on/off (different tones for mode change)

Uses Windows winsound (built into Python) — no external files needed.
"""
import logging
import sys
import threading

logger = logging.getLogger(__name__)

# Track last known confidence per ticker so we can detect increases
_last_confidence: dict = {}
_confidence_lock = threading.Lock()


def _beep_thread(frequency: int, duration_ms: int):
    """Play a beep in a background thread so it never blocks trading."""
    def _play():
        try:
            if sys.platform == "win32":
                import winsound
                winsound.Beep(frequency, duration_ms)
            else:
                # Fallback for non-Windows
                print("\a", end="", flush=True)
        except Exception as e:
            logger.debug("[AUDIO] Beep failed: %s", e)
    t = threading.Thread(target=_play, daemon=True)
    t.start()


def _multi_beep_thread(tones: list):
    """Play a sequence of tones in a background thread."""
    def _play():
        try:
            if sys.platform == "win32":
                import winsound
                for freq, dur in tones:
                    winsound.Beep(freq, dur)
            else:
                for _ in tones:
                    print("\a", end="", flush=True)
        except Exception as e:
            logger.debug("[AUDIO] Multi-beep failed: %s", e)
    t = threading.Thread(target=_play, daemon=True)
    t.start()


def play_buy_sound():
    """
    Sound for BUY/SELL execution — distinctive ascending 3-tone.
    "Cha-ching" feel: low → mid → high.
    """
    logger.info("[AUDIO] Playing BUY sound (ascending 3-tone)")
    _multi_beep_thread([
        (600, 120),    # low tone
        (900, 120),    # mid tone
        (1200, 250),   # high tone (held)
    ])


def play_sell_sound():
    """
    Sound for SELL execution — distinctive descending 3-tone.
    Inverted "cha-ching": high → mid → low.
    """
    logger.info("[AUDIO] Playing SELL sound (descending 3-tone)")
    _multi_beep_thread([
        (1200, 120),   # high tone
        (900, 120),    # mid tone
        (600, 250),    # low tone (held)
    ])


def play_confidence_increase_sound():
    """
    Sound when confidence increases — gentle rising 2-tone.
    Quick "level up" feel.
    """
    logger.info("[AUDIO] Playing CONFIDENCE-UP sound (rising 2-tone)")
    _multi_beep_thread([
        (800, 80),
        (1100, 150),
    ])


def play_warning_sound():
    """Sound for failures — two short low beeps."""
    logger.info("[AUDIO] Playing WARNING sound (low double-beep)")
    _multi_beep_thread([
        (400, 150),
        (400, 150),
    ])


def play_hand_on_sound():
    """Sound when HAND ON (live trading) is activated — single high beep."""
    logger.info("[AUDIO] Playing HAND ON sound")
    _beep_thread(1400, 200)


def play_hand_off_sound():
    """Sound when HAND OFF (paper mode) is activated — single low beep."""
    logger.info("[AUDIO] Playing HAND OFF sound")
    _beep_thread(500, 200)


def play_ready_to_buy_sound():
    """
    Sound when confidence crosses the "ready to buy" threshold (>=80%).
    Distinctive "ding" — high single tone, longer duration.
    """
    logger.info("[AUDIO] Playing READY-TO-BUY sound (high ding)")
    _beep_thread(1500, 350)


def on_confidence_update(ticker: str, new_confidence: float):
    """
    Track confidence per ticker and play sound when it increases significantly.
    Called every time the brain returns a verdict.
    - If confidence jumps to >=80% (READY TO BUY), play ready-to-buy sound
    - If confidence increases by >=15% from last known, play confidence-up sound
    """
    if new_confidence is None or new_confidence <= 0:
        return
    ticker = str(ticker or "UNKNOWN")
    with _confidence_lock:
        prev = _last_confidence.get(ticker)
        _last_confidence[ticker] = new_confidence
    # First time seeing this ticker — no comparison
    if prev is None:
        return
    # Ready to buy threshold (>=80%)
    if new_confidence >= 0.80 and prev < 0.80:
        play_ready_to_buy_sound()
        return
    # Significant increase (>=15 percentage points)
    if (new_confidence - prev) >= 0.15:
        play_confidence_increase_sound()
