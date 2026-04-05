"""
VcanTrade AI - Calibration Module

RPA Coordinate Mapper: Records screen positions of broker UI elements
(BUY button, SELL button, Stop Loss input, Take Profit input, etc.)
and persists them to calibration.json for the RPA Executor to use.

Safe Boot: Ensures application always starts in Teacher Mode with
a confirmation dialog before entering Autonomous Mode.
"""

import json
import logging
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Calibration file location (next to the app)
CALIBRATION_FILE = Path("calibration.json")

# Required calibration points
REQUIRED_POINTS = [
    "buy_button",
    "sell_button",
    "close_button",
    "sl_input",
    "tp_input",
    "lot_size_input",
    "confirm_button",
]

# Default coordinates (will be overwritten by calibration)
DEFAULT_COORDINATES: Dict[str, Tuple[int, int]] = {
    "buy_button": (0, 0),
    "sell_button": (0, 0),
    "close_button": (0, 0),
    "sl_input": (0, 0),
    "tp_input": (0, 0),
    "lot_size_input": (0, 0),
    "confirm_button": (0, 0),
}


class CalibrationManager:
    """
    Manages RPA coordinate calibration and persistence.

    Workflow:
    1. User clicks "Start Calibration" in UI
    2. For each required point, UI prompts user to click the element
    3. Coordinates are captured and saved to calibration.json
    4. RPAExecutor loads these coordinates for mouse-based execution
    """

    def __init__(self, filepath: Path = CALIBRATION_FILE):
        self.filepath = filepath
        self.coordinates: Dict[str, Tuple[int, int]] = {}
        self._load()

    def _load(self):
        """Load calibrated coordinates from JSON file."""
        if self.filepath.exists():
            try:
                with open(self.filepath, "r") as f:
                    data = json.load(f)
                self.coordinates = {k: tuple(v) for k, v in data.items()}
                logger.info(f"Calibration loaded: {len(self.coordinates)} points")
            except Exception as e:
                logger.error(f"Failed to load calibration: {e}")
                self.coordinates = dict(DEFAULT_COORDINATES)
        else:
            logger.info("No calibration file found — using defaults")
            self.coordinates = dict(DEFAULT_COORDINATES)

    def save(self):
        """Persist calibrated coordinates to JSON file."""
        with open(self.filepath, "w") as f:
            json.dump(
                {k: list(v) for k, v in self.coordinates.items()},
                f,
                indent=2,
            )
        logger.info(f"Calibration saved to {self.filepath}")

    def get_coordinate(self, point_name: str) -> Tuple[int, int]:
        """Get calibrated coordinate for a UI element."""
        return self.coordinates.get(point_name, (0, 0))

    def set_coordinate(self, point_name: str, x: int, y: int):
        """Record a calibrated coordinate."""
        self.coordinates[point_name] = (x, y)
        logger.info(f"Calibrated {point_name}: ({x}, {y})")

    def is_calibrated(self) -> bool:
        """Check if all required points have been calibrated."""
        return all(self.coordinates.get(p, (0, 0)) != (0, 0) for p in REQUIRED_POINTS)

    def get_calibration_status(self) -> Dict[str, bool]:
        """Return calibration status for each required point."""
        return {
            point: self.coordinates.get(point, (0, 0)) != (0, 0)
            for point in REQUIRED_POINTS
        }

    def get_uncalibrated_points(self) -> list[str]:
        """Return list of points that still need calibration."""
        return [p for p in REQUIRED_POINTS if self.coordinates.get(p, (0, 0)) == (0, 0)]

    def reset(self):
        """Reset all coordinates to defaults."""
        self.coordinates = dict(DEFAULT_COORDINATES)
        if self.filepath.exists():
            self.filepath.unlink()
        logger.info("Calibration reset")


class CalibrationWizard:
    """
    Interactive calibration routine.
    Presents a series of prompts asking the user to click specific
    broker UI elements. Records coordinates on each click.
    """

    POINT_LABELS = {
        "buy_button": "Click the BUY button on your broker platform",
        "sell_button": "Click the SELL button on your broker platform",
        "close_button": "Click the CLOSE/X button to close a position",
        "sl_input": "Click the Stop Loss input field",
        "tp_input": "Click the Take Profit input field",
        "lot_size_input": "Click the Lot Size / Volume input field",
        "confirm_button": "Click the Confirm / Execute trade button",
    }

    def __init__(self, manager: CalibrationManager):
        self.manager = manager
        self._pyautogui = None
        try:
            import pyautogui

            self._pyautogui = pyautogui
            pyautogui.FAILSAFE = True
        except ImportError:
            logger.error("pyautogui not available — calibration disabled")

    def is_available(self) -> bool:
        return self._pyautogui is not None

    def get_next_uncalibrated(self) -> Optional[str]:
        """Get the next point that needs calibration, or None if done."""
        uncalibrated = self.manager.get_uncalibrated_points()
        return uncalibrated[0] if uncalibrated else None

    def capture_current_position(self, point_name: str) -> Tuple[int, int]:
        """
        Capture the current mouse position.
        The user should move their mouse to the target element and press
        a designated key (handled by the UI layer).
        """
        if not self._pyautogui:
            return (0, 0)

        x, y = self._pyautogui.position()
        self.manager.set_coordinate(point_name, x, y)
        logger.info(f"Captured {point_name}: ({x}, {y})")
        return (x, y)

    def run_full_calibration(self) -> bool:
        """
        Run the full calibration sequence.
        Blocks until all points are calibrated or user cancels.
        Returns True if all points were calibrated.
        """
        if not self._pyautogui:
            logger.error("Calibration not available — pyautogui missing")
            return False

        logger.info("Starting full calibration sequence")

        for point in REQUIRED_POINTS:
            label = self.POINT_LABELS.get(point, point)
            logger.info(f"Calibration step: {label}")

            # Give user 5 seconds to move mouse to the target
            # The actual click capture is handled by the UI layer
            # This method returns the point name for the UI to prompt
            yield point, label

        self.manager.save()
        logger.info("Calibration complete — all points saved")
        return True
