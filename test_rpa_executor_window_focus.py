import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "execution" / "rpa_executor.py"


def _load_rpa_module():
    fake_config = types.ModuleType("config")
    fake_config.USE_HOTKEYS = True
    fake_config.HOTKEY_BUY = "ctrl+b"
    fake_config.HOTKEY_SELL = "ctrl+s"
    fake_config.HOTKEY_CLOSE = "ctrl+x"
    fake_config.TRADINGVIEW_WINDOW_X = 0
    fake_config.TRADINGVIEW_WINDOW_Y = 0
    fake_config.POSITION_OPEN_IMAGE = "assets/position_open.png"

    fake_core = types.ModuleType("core")
    fake_models = types.ModuleType("core.models")
    fake_models.TradeRecord = object
    fake_models.SignalAction = object
    fake_calibration = types.ModuleType("core.calibration")

    class _DummyCalibrationManager:
        pass

    fake_calibration.CalibrationManager = _DummyCalibrationManager

    for name, mod in {
        "config": fake_config,
        "core": fake_core,
        "core.models": fake_models,
        "core.calibration": fake_calibration,
    }.items():
        sys.modules[name] = mod

    spec = importlib.util.spec_from_file_location("execution.rpa_executor", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


rpa_executor = _load_rpa_module()


class _FakeWindow:
    def __init__(self, title, minimized=False):
        self.title = title
        self.isMinimized = minimized
        self._hWnd = None
        self.events = []

    def restore(self):
        self.events.append("restore")
        self.isMinimized = False

    def maximize(self):
        self.events.append("maximize")

    def activate(self):
        self.events.append("activate")

    def moveTo(self, *_args):
        self.events.append("moveTo")


class _FakeGW:
    def __init__(self, windows):
        self._windows = windows

    def getAllWindows(self):
        return self._windows


class TestRPAWindowFocusGuards(unittest.TestCase):
    def setUp(self):
        self.executor = rpa_executor.RPAExecutor()

    def test_title_matches_rejects_blacklisted_titles(self):
        self.assertFalse(self.executor._title_matches_target("TradingView - pwsh"))
        self.assertFalse(self.executor._title_matches_target("Visual Studio Code - Terminal"))

    def test_get_browser_window_aborts_when_only_non_preferred_browser_exists(self):
        firefox = _FakeWindow("EURUSD Chart - Mozilla Firefox")
        self.executor._gw = _FakeGW([firefox])
        self.assertIsNone(self.executor._get_browser_window(ticker_hint="EURUSD"))

    def test_get_browser_window_fallback_accepts_only_chrome_edge_brave(self):
        terminal = _FakeWindow("PowerShell")
        brave = _FakeWindow("Work Profile - Brave")
        self.executor._gw = _FakeGW([terminal, brave])
        self.assertIs(self.executor._get_browser_window(), brave)

    def test_force_focus_rejects_blacklisted_window_before_activate(self):
        terminal = _FakeWindow("pwsh.exe")
        self.assertFalse(self.executor._force_focus_tradingview(terminal))
        self.assertNotIn("activate", terminal.events)

    def test_force_focus_sleeps_after_each_activate_call(self):
        win = _FakeWindow("Random App - Google Chrome")
        self.executor._active_window_title = lambda: "Unrelated Foreground"
        self.executor._cycle_tabs_until_match = lambda **_kwargs: False

        sequence = []

        def _sleep(seconds):
            sequence.append(("sleep", seconds))

        original_activate = win.activate

        def _activate():
            original_activate()
            sequence.append(("activate", None))

        win.activate = _activate

        with mock.patch.object(rpa_executor.time, "sleep", side_effect=_sleep):
            self.executor._force_focus_tradingview(win)

        activate_indices = [i for i, event in enumerate(sequence) if event[0] == "activate"]
        self.assertGreaterEqual(len(activate_indices), 2)
        for idx in activate_indices:
            self.assertLess(idx + 1, len(sequence))
            self.assertEqual(sequence[idx + 1], ("sleep", 1.5))


if __name__ == "__main__":
    unittest.main()
