"""
VcanTrade AI - Calibration Wizard Dialog

Guides the user through clicking each broker UI element so
RPA Executor can reliably automate order entry in future sessions.
"""

import logging
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QFrame,
)
import pyautogui

from core.calibration import CalibrationManager, REQUIRED_POINTS

logger = logging.getLogger(__name__)

BG_DARK = "#0D1117"
BG_PANEL = "#161B22"
BORDER = "#30363D"
CYAN = "#00D4FF"
GREEN = "#3FB950"
ORANGE = "#D29922"
GRAY = "#8B949E"
WHITE = "#E6EDF3"

POINT_LABELS = {
    "buy_button": "BUY Button",
    "sell_button": "SELL Button",
    "close_button": "CLOSE Position Button",
    "sl_input": "Stop-Loss Input Field",
    "tp_input": "Take-Profit Input Field",
    "lot_size_input": "Lot Size / Quantity Input",
    "confirm_button": "Confirm / Submit Order Button",
}


class CalibrationWizardDialog(QDialog):
    """Step-by-step wizard to record broker UI coordinates for RPA automation."""

    calibration_complete = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cal = CalibrationManager()
        self._steps = REQUIRED_POINTS[:]
        self._current_step = 0
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("RPA Calibration Wizard")
        self.setModal(True)
        self.setFixedSize(520, 340)
        self.setStyleSheet(f"background: {BG_DARK}; color: {WHITE};")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 20, 24, 20)

        # Title
        title = QLabel("🖱️ RPA Coordinate Calibration Wizard")
        title.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {CYAN};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Instructions
        self._instr = QLabel(self._instruction_text())
        self._instr.setFont(QFont("Segoe UI", 11))
        self._instr.setStyleSheet(f"color: {WHITE}; padding: 8px;")
        self._instr.setWordWrap(True)
        self._instr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._instr)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setMaximum(len(self._steps))
        self._progress.setValue(self._current_step)
        self._progress.setStyleSheet(f"""
            QProgressBar {{ border: 1px solid {BORDER}; border-radius: 4px;
                            background: {BG_PANEL}; height: 12px; }}
            QProgressBar::chunk {{ background: {CYAN}; border-radius: 3px; }}
        """)
        layout.addWidget(self._progress)

        # Status label
        self._status = QLabel("")
        self._status.setFont(QFont("Segoe UI", 10))
        self._status.setStyleSheet(f"color: {GRAY};")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet(f"color: {BORDER};")
        layout.addWidget(separator)

        # Buttons
        btn_row = QHBoxLayout()

        self._skip_btn = QPushButton("Skip")
        self._skip_btn.setFixedHeight(36)
        self._skip_btn.setStyleSheet(f"""
            QPushButton {{ background: {BG_PANEL}; color: {GRAY}; border: 1px solid {BORDER};
                           border-radius: 6px; padding: 0 18px; }}
            QPushButton:hover {{ border-color: {ORANGE}; color: {ORANGE}; }}
        """)
        self._skip_btn.clicked.connect(self._on_skip)
        btn_row.addWidget(self._skip_btn)

        self._capture_btn = QPushButton("📍 Click & Capture Coordinates")
        self._capture_btn.setFixedHeight(36)
        self._capture_btn.setStyleSheet(f"""
            QPushButton {{ background: {CYAN}; color: #000; border-radius: 6px;
                           font-weight: bold; padding: 0 18px; }}
            QPushButton:hover {{ background: #00b8d9; }}
        """)
        self._capture_btn.clicked.connect(self._on_capture)
        btn_row.addWidget(self._capture_btn)

        layout.addLayout(btn_row)

        self._close_btn = QPushButton("Finish & Close")
        self._close_btn.setFixedHeight(32)
        self._close_btn.setStyleSheet(f"""
            QPushButton {{ background: {GREEN}; color: #000; border-radius: 6px;
                           font-weight: bold; padding: 0 14px; }}
            QPushButton:hover {{ background: #2ea043; }}
        """)
        self._close_btn.clicked.connect(self._on_finish)
        self._close_btn.setVisible(False)
        layout.addWidget(self._close_btn, alignment=Qt.AlignmentFlag.AlignCenter)

    def _instruction_text(self) -> str:
        if self._current_step >= len(self._steps):
            return "All points captured! Click Finish to save."
        point = self._steps[self._current_step]
        label = POINT_LABELS.get(point, point.replace("_", " ").title())
        return (
            f"Step {self._current_step + 1} of {len(self._steps)}: "
            f"Hover your mouse over the <b>{label}</b> in your broker window, "
            f"then click <b>Click & Capture Coordinates</b>."
        )

    def _on_capture(self):
        if self._current_step >= len(self._steps):
            return
        try:
            x, y = pyautogui.position()
            point = self._steps[self._current_step]
            self._cal.coordinates[point] = (int(x), int(y))
            self._cal.save()
            self._status.setText(f"✅ Captured ({int(x)}, {int(y)}) for '{POINT_LABELS.get(point, point)}'")
            self._status.setStyleSheet(f"color: {GREEN};")
            logger.info("Calibration point '%s' captured at (%d, %d)", point, x, y)
        except Exception as exc:
            self._status.setText(f"❌ Could not capture: {exc}")
            self._status.setStyleSheet(f"color: #F85149;")
            logger.error("Calibration capture error: %s", exc)
            return

        self._current_step += 1
        self._progress.setValue(self._current_step)
        self._instr.setText(self._instruction_text())

        if self._current_step >= len(self._steps):
            self._capture_btn.setEnabled(False)
            self._skip_btn.setEnabled(False)
            self._close_btn.setVisible(True)
            self.calibration_complete.emit()

    def _on_skip(self):
        if self._current_step >= len(self._steps):
            return
        self._current_step += 1
        self._progress.setValue(self._current_step)
        self._instr.setText(self._instruction_text())
        self._status.setText("(step skipped)")
        self._status.setStyleSheet(f"color: {GRAY};")
        if self._current_step >= len(self._steps):
            self._capture_btn.setEnabled(False)
            self._skip_btn.setEnabled(False)
            self._close_btn.setVisible(True)
            self.calibration_complete.emit()

    def _on_finish(self):
        self.accept()
