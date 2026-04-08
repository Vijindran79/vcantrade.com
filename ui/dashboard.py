"""
VcanTrade AI - Command Center Dashboard

Compact, always-on-top GUI that serves as the central control interface.
Does NOT block the Vision Engine from seeing charts behind it.

Architecture: All heavy backend work runs in QThreads. The GUI stays
responsive via PyQt6's signal/slot system — no blocking calls on the
main event loop.
"""

import logging
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import config
from core.models import DebateTranscript, OverlaySignal, SignalAction

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Color Palette
# ---------------------------------------------------------------------------

BG_DARK = "#0D1117"
BG_PANEL = "#161B22"
BG_INPUT = "#0D1117"
BORDER = "#30363D"
CYAN = "#00D4FF"
GREEN = "#3FB950"
RED = "#F85149"
ORANGE = "#D29922"
GRAY = "#8B949E"
WHITE = "#E6EDF3"
DIM = "#6E7681"
CEO_GREEN = "#00FF88"
CEO_RED = "#FF6644"


# ---------------------------------------------------------------------------
# StatusIndicator — small colored dot + label
# ---------------------------------------------------------------------------


class StatusIndicator(QWidget):
    """Green/red dot with label for system status bar."""

    def __init__(self, label: str, active: bool = False, parent=None):
        super().__init__(parent)
        self._active = active
        self._label = label
        self.setFixedHeight(24)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.dot = QLabel()
        self.dot.setFixedSize(10, 10)
        self.dot.setStyleSheet(self._dot_style())
        layout.addWidget(self.dot)

        self.lbl = QLabel(label)
        self.lbl.setStyleSheet(
            f"color: {GRAY}; font-size: 11px; font-family: 'Consolas', monospace;"
        )
        layout.addWidget(self.lbl)

    def set_active(self, active: bool, text: str = ""):
        self._active = active
        self.dot.setStyleSheet(self._dot_style())
        if text:
            self.lbl.setText(f"{self._label}: {text}")
            self.lbl.setStyleSheet(
                f"color: {GREEN if active else RED}; font-size: 11px; font-family: 'Consolas', monospace;"
            )

    def _dot_style(self) -> str:
        color = GREEN if self._active else RED
        return f"""
            background-color: {color};
            border-radius: 5px;
            border: 1px solid {BORDER};
        """


# ---------------------------------------------------------------------------
# SwarmTerminal — scrolling text area for live agent reasoning
# ---------------------------------------------------------------------------


class SwarmTerminal(QWidget):
    """
    Scrolling terminal that displays real-time Swarm debate output.
    Thread-safe: accepts strings via signal, appends on main thread.
    """

    append_line = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self.append_line.connect(self._append)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QLabel("SWARM TERMINAL")
        header.setStyleSheet(
            f"color: {CYAN}; font-size: 12px; font-weight: bold; "
            f"font-family: 'Consolas', monospace; padding: 6px 0;"
        )
        layout.addWidget(header)

        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setStyleSheet(f"""
            QTextEdit {{
                background-color: {BG_INPUT};
                color: {GREEN};
                border: 1px solid {BORDER};
                border-radius: 6px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px;
                padding: 8px;
            }}
        """)
        layout.addWidget(self.text)

    def log(self, message: str):
        """Thread-safe: emit signal to append on main thread."""
        self.append_line.emit(message)

    def _append(self, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.text.append(f'<span style="color:{DIM}">[{timestamp}]</span> {message}')
        # Auto-scroll
        cursor = self.text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.text.setTextCursor(cursor)

    def clear(self):
        self.text.clear()


# ---------------------------------------------------------------------------
# CommandCenter — Main Dashboard Window
# ---------------------------------------------------------------------------


class CommandCenter(QWidget):
    """
    VcanTrade AI Command Center.

    Always-on-top, compact window that does not block the Vision Engine
    from seeing charts behind it.

    Components:
    - Master Operating Switch (Teacher <-> Autonomous)
    - Swarm Terminal (live agent reasoning)
    - System Status Bar (Watchtower, Vision, RPA indicators)
    - Prop Firm Selection Board (asset selection checklist)
    - Emergency Kill Switch
    """

    mode_changed = pyqtSignal(str)  # "TEACHER" or "AUTONOMOUS"
    paper_toggled = pyqtSignal(bool)  # True = paper, False = live
    kill_switch_triggered = pyqtSignal()
    calibration_requested = pyqtSignal()
    vision_test_requested = pyqtSignal()
    calibration_reset_requested = pyqtSignal()
    eod_report_requested = pyqtSignal()
    selection_changed = pyqtSignal(list)  # List of selected assets

    def __init__(self):
        super().__init__()
        self._mode = "TEACHER"
        self._paper = True
        self._killed = False
        self._selected_assets = config.SELECTED_ASSETS.copy()

        self._setup_window()
        self._build_ui()

        logger.info("Command Center initialized")

    # -- window setup --------------------------------------------------------

    def _setup_window(self):
        self.setWindowTitle("VcanTrade AI — Command Center")
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint  # Always on top
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.resize(520, 680)
        self.setStyleSheet(f"background-color: {BG_DARK};")

    # -- UI construction -----------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── Title ──────────────────────────────────────────────────
        title = QLabel("VcaniTrade AI")
        title.setStyleSheet(
            f"color: {CYAN}; font-size: 20px; font-weight: bold; "
            f"font-family: 'Segoe UI', sans-serif; padding: 4px 0;"
        )
        root.addWidget(title)

        # ── Master Operating Switch ────────────────────────────────
        root.addWidget(self._build_mode_switch())

        # ── Paper / Live Toggle ────────────────────────────────────
        root.addWidget(self._build_paper_toggle())

        # ── System Status Bar ──────────────────────────────────────
        root.addWidget(self._build_status_bar())

        # ── Calibration & Debug Tools ──────────────────────────────
        root.addWidget(self._build_tools_panel())

        # ── Prop Firm Selection Board (NEW) ────────────────────────
        self.selection_board = self._build_selection_board()
        root.addWidget(self.selection_board)

        # ── Swarm Terminal ─────────────────────────────────────────
        self.terminal = SwarmTerminal()
        root.addWidget(self.terminal, stretch=1)

        # ── CEO Verdict Banner (hidden until verdict arrives) ──────
        self.ceo_banner = self._build_ceo_banner()
        self.ceo_banner.hide()
        root.addWidget(self.ceo_banner)

        # ── Emergency Kill Switch ──────────────────────────────────
        root.addWidget(self._build_kill_switch())

    def _build_mode_switch(self) -> QWidget:
        """Teacher Mode <---> Autonomous Mode toggle."""
        container = QFrame()
        container.setStyleSheet(
            f"background-color: {BG_PANEL}; border: 1px solid {BORDER}; "
            f"border-radius: 8px; padding: 10px;"
        )
        layout = QHBoxLayout(container)
        layout.setContentsMargins(10, 6, 10, 6)

        label = QLabel("MODE:")
        label.setStyleSheet(
            f"color: {GRAY}; font-size: 12px; font-weight: bold; font-family: 'Consolas', monospace;"
        )
        layout.addWidget(label)

        self.btn_teacher = QPushButton("TEACHER (Manual)")
        self.btn_teacher.setCheckable(True)
        self.btn_teacher.setChecked(True)
        self.btn_teacher.setStyleSheet(self._btn_teacher_style(True))
        self.btn_teacher.clicked.connect(self._set_teacher_mode)
        self.btn_teacher.setMinimumHeight(32)
        layout.addWidget(self.btn_teacher)

        self.btn_auto = QPushButton("AUTONOMOUS (Auto)")
        self.btn_auto.setCheckable(True)
        self.btn_auto.setChecked(False)
        self.btn_auto.setStyleSheet(self._btn_auto_style(False))
        self.btn_auto.clicked.connect(self._set_auto_mode)
        self.btn_auto.setMinimumHeight(32)
        layout.addWidget(self.btn_auto)

        layout.addStretch()

        self.mode_badge = QLabel("TEACHER")
        self.mode_badge.setStyleSheet(
            f"color: {CYAN}; font-size: 11px; font-weight: bold; "
            f"font-family: 'Consolas', monospace;"
        )
        layout.addWidget(self.mode_badge)

        return container

    def _build_paper_toggle(self) -> QWidget:
        """Paper (safe) / Live (real money) toggle."""
        container = QFrame()
        container.setStyleSheet(
            f"background-color: {BG_PANEL}; border: 1px solid {BORDER}; "
            f"border-radius: 8px; padding: 8px 10px;"
        )
        layout = QHBoxLayout(container)
        layout.setContentsMargins(10, 4, 10, 4)

        label = QLabel("TRADING:")
        label.setStyleSheet(
            f"color: {GRAY}; font-size: 11px; font-family: 'Consolas', monospace;"
        )
        layout.addWidget(label)

        self.paper_btn = QPushButton("PAPER (Safe)")
        self.paper_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {GREEN}; color: #000; font-weight: bold;
                font-size: 12px; border-radius: 4px; padding: 4px 14px;
                font-family: 'Consolas', monospace;
            }}
        """)
        self.paper_btn.clicked.connect(self._toggle_paper)
        layout.addWidget(self.paper_btn)

        layout.addStretch()

        self.paper_label = QLabel("Dry-run active — no real money")
        self.paper_label.setStyleSheet(
            f"color: {GREEN}; font-size: 10px; font-family: 'Consolas', monospace;"
        )
        layout.addWidget(self.paper_label)

        return container

    def _build_status_bar(self) -> QWidget:
        """System status indicators: Watchtower, Vision, RPA."""
        container = QFrame()
        container.setStyleSheet(
            f"background-color: {BG_PANEL}; border: 1px solid {BORDER}; "
            f"border-radius: 8px; padding: 8px 10px;"
        )
        layout = QHBoxLayout(container)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(16)

        self.status_watchtower = StatusIndicator("WATCHTOWER", False)
        layout.addWidget(self.status_watchtower)

        self.status_vision = StatusIndicator("VISION", False)
        layout.addWidget(self.status_vision)

        self.status_rpa = StatusIndicator("RPA", False)
        layout.addWidget(self.status_rpa)

        return container

    def _build_tools_panel(self) -> QWidget:
        """Calibration, Vision Test, and debug tools."""
        container = QFrame()
        container.setStyleSheet(
            f"background-color: {BG_PANEL}; border: 1px solid {BORDER}; "
            f"border-radius: 8px; padding: 8px 10px;"
        )
        layout = QHBoxLayout(container)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        label = QLabel("TOOLS:")
        label.setStyleSheet(
            f"color: {GRAY}; font-size: 11px; font-weight: bold; font-family: 'Consolas', monospace;"
        )
        layout.addWidget(label)

        self.btn_calibrate = QPushButton("Calibrate RPA")
        self.btn_calibrate.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_INPUT}; color: {CYAN};
                font-size: 11px; border: 1px solid {BORDER};
                border-radius: 4px; padding: 4px 10px;
                font-family: 'Consolas', monospace;
            }}
            QPushButton:hover {{ background-color: {BORDER}; }}
        """)
        self.btn_calibrate.clicked.connect(self._on_calibrate)
        layout.addWidget(self.btn_calibrate)

        self.btn_vision_test = QPushButton("Test Vision")
        self.btn_vision_test.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_INPUT}; color: {ORANGE};
                font-size: 11px; border: 1px solid {BORDER};
                border-radius: 4px; padding: 4px 10px;
                font-family: 'Consolas', monospace;
            }}
            QPushButton:hover {{ background-color: {BORDER}; }}
        """)
        self.btn_vision_test.clicked.connect(self._on_test_vision)
        layout.addWidget(self.btn_vision_test)

        self.btn_reset_cal = QPushButton("Reset Cal.")
        self.btn_reset_cal.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_INPUT}; color: {GRAY};
                font-size: 11px; border: 1px solid {BORDER};
                border-radius: 4px; padding: 4px 10px;
                font-family: 'Consolas', monospace;
            }}
            QPushButton:hover {{ background-color: {BORDER}; }}
        """)
        self.btn_reset_cal.clicked.connect(self._on_reset_calibration)
        layout.addWidget(self.btn_reset_cal)

        self.btn_eod = QPushButton("EOD Report")
        self.btn_eod.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_INPUT}; color: {GREEN};
                font-size: 11px; border: 1px solid {BORDER};
                border-radius: 4px; padding: 4px 10px;
                font-family: 'Consolas', monospace;
            }}
            QPushButton:hover {{ background-color: {BORDER}; }}
        """)
        self.btn_eod.clicked.connect(self._on_eod_report)
        layout.addWidget(self.btn_eod)

        layout.addStretch()

        self.cal_status = QLabel("RPA: Not calibrated")
        self.cal_status.setStyleSheet(
            f"color: {ORANGE}; font-size: 10px; font-family: 'Consolas', monospace;"
        )
        layout.addWidget(self.cal_status)

        return container

    def _on_calibrate(self):
        self.calibration_requested.emit()

    def _on_test_vision(self):
        self.vision_test_requested.emit()

    def _on_reset_calibration(self):
        self.calibration_reset_requested.emit()

    def _on_eod_report(self):
        self.eod_report_requested.emit()

    def _build_selection_board(self) -> QWidget:
        """Prop Firm Selection Board - checklist for asset selection."""
        container = QFrame()
        container.setStyleSheet(
            f"background-color: {BG_PANEL}; border: 1px solid {BORDER}; "
            f"border-radius: 8px; padding: 10px;"
        )
        
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)
        
        # Header
        header_layout = QHBoxLayout()
        title = QLabel("PROP FIRM SELECTION BOARD")
        title.setStyleSheet(
            f"color: {CYAN}; font-size: 12px; font-weight: bold; "
            f"font-family: 'Consolas', monospace;"
        )
        header_layout.addWidget(title)
        header_layout.addStretch()
        
        count_label = QLabel(f"Selected: {len(self._selected_assets)}")
        count_label.setObjectName("selection_count")
        count_label.setStyleSheet(
            f"color: {GREEN}; font-size: 10px; font-family: 'Consolas', monospace;"
        )
        header_layout.addWidget(count_label)
        
        layout.addLayout(header_layout)
        
        # Scrollable area for checkboxes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(180)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: {BG_INPUT};
                border: 1px solid {BORDER};
                border-radius: 4px;
            }}
            QScrollBar:vertical {{
                background-color: {BG_DARK};
                width: 10px;
                border-radius: 5px;
            }}
            QScrollBar::handle:vertical {{
                background-color: {DIM};
                border-radius: 5px;
                min-height: 20px;
            }}
        """)
        
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(8, 8, 8, 8)
        scroll_layout.setSpacing(6)
        
        # Store checkbox references
        self.asset_checkboxes = {}
        
        # Build category sections
        for category, assets in config.ASSETS_BY_CATEGORY.items():
            # Category label
            cat_label = QLabel(f"▼ {category.upper()}")
            cat_label.setStyleSheet(
                f"color: {ORANGE}; font-size: 11px; font-weight: bold; "
                f"font-family: 'Consolas', monospace; padding: 4px 0;"
            )
            scroll_layout.addWidget(cat_label)
            
            # Checkboxes for assets in this category
            cat_layout = QHBoxLayout()
            cat_layout.setSpacing(12)
            
            for asset in assets:
                cb = QPushButton(asset)
                cb.setCheckable(True)
                cb.setChecked(asset in self._selected_assets)
                cb.setStyleSheet(self._asset_button_style(cb.isChecked()))
                cb.clicked.connect(lambda checked, a=asset: self._on_asset_toggled(a, checked))
                cb.setMinimumHeight(28)
                cb.setMinimumWidth(70)
                cat_layout.addWidget(cb)
                self.asset_checkboxes[asset] = cb
            
            cat_layout.addStretch()
            scroll_layout.addLayout(cat_layout)
            
            # Separator line between categories
            separator = QFrame()
            separator.setFrameShape(QFrame.Shape.HLine)
            separator.setStyleSheet(f"background-color: {BORDER}; max-height: 1px;")
            separator.setMaximumHeight(1)
            scroll_layout.addWidget(separator)
        
        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)
        
        # Info footer
        info = QLabel("Select 3-5 assets for the bot to analyze. Unchecked assets will be ignored.")
        info.setStyleSheet(
            f"color: {DIM}; font-size: 9px; font-family: 'Consolas', monospace;"
        )
        layout.addWidget(info)
        
        return container
    
    def _asset_button_style(self, active: bool) -> str:
        if active:
            return f"""
                QPushButton {{
                    background-color: {GREEN}; color: #000; font-weight: bold;
                    font-size: 10px; border-radius: 4px; padding: 4px 8px;
                    font-family: 'Consolas', monospace;
                }}
                QPushButton:hover {{ background-color: #2EA043; }}
            """
        return f"""
            QPushButton {{
                background-color: {BG_INPUT}; color: {GRAY};
                font-size: 10px; border: 1px solid {BORDER};
                border-radius: 4px; padding: 4px 8px;
                font-family: 'Consolas', monospace;
            }}
            QPushButton:hover {{ background-color: {BORDER}; color: {WHITE}; }}
        """
    
    def _on_asset_toggled(self, asset: str, checked: bool):
        """Handle asset checkbox toggle."""
        if checked:
            if asset not in self._selected_assets:
                self._selected_assets.append(asset)
        else:
            if asset in self._selected_assets:
                self._selected_assets.remove(asset)
        
        # Update the count label - find it by traversing children
        count_label = None
        for child in self.selection_board.children():
            if isinstance(child, QLabel) and child.objectName() == "selection_count":
                count_label = child
                break
        
        if count_label:
            count_label.setText(f"Selected: {len(self._selected_assets)}")
        
        # Emit signal to update backend
        self.selection_changed.emit(self._selected_assets.copy())
        
        status = "added" if checked else "removed"
        self.terminal.log(f"Asset {status}: {asset} (now monitoring {len(self._selected_assets)} assets)")

    def update_calibration_status(self, calibrated: bool, points_done: int, total: int):
        if calibrated:
            self.cal_status.setText(f"RPA: Calibrated ({points_done}/{total})")
            self.cal_status.setStyleSheet(
                f"color: {GREEN}; font-size: 10px; font-family: 'Consolas', monospace;"
            )
        else:
            self.cal_status.setText(f"RPA: {points_done}/{total} calibrated")
            self.cal_status.setStyleSheet(
                f"color: {ORANGE}; font-size: 10px; font-family: 'Consolas', monospace;"
            )

    def _build_ceo_banner(self) -> QFrame:
        """Prominent CEO verdict display."""
        banner = QFrame()
        banner.setStyleSheet(
            f"background-color: rgba(0, 255, 136, 0.06); "
            f"border: 1px solid {CEO_GREEN}; border-left: 4px solid {CEO_GREEN}; "
            f"border-radius: 8px; padding: 12px;"
        )
        layout = QVBoxLayout(banner)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        header = QLabel("CEO VERDICT")
        header.setStyleSheet(
            f"color: {CEO_GREEN}; font-size: 11px; font-weight: bold; "
            f"font-family: 'Consolas', monospace; letter-spacing: 2px;"
        )
        layout.addWidget(header)

        self.ceo_text = QLabel("")
        self.ceo_text.setWordWrap(True)
        self.ceo_text.setStyleSheet(
            f"color: {WHITE}; font-size: 14px; font-weight: bold; "
            f"font-family: 'Segoe UI', sans-serif;"
        )
        layout.addWidget(self.ceo_text)

        self.ceo_details = QLabel("")
        self.ceo_details.setWordWrap(True)
        self.ceo_details.setStyleSheet(
            f"color: {GRAY}; font-size: 11px; font-family: 'Consolas', monospace;"
        )
        layout.addWidget(self.ceo_details)

        return banner

    def _build_kill_switch(self) -> QWidget:
        """Massive red Emergency Kill Switch button."""
        container = QFrame()
        container.setStyleSheet(
            f"background-color: {BG_PANEL}; border: 1px solid {RED}; "
            f"border-radius: 8px; padding: 8px;"
        )
        layout = QHBoxLayout(container)
        layout.setContentsMargins(10, 6, 10, 6)

        self.kill_btn = QPushButton("EMERGENCY KILL SWITCH")
        self.kill_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {RED};
                color: #FFF;
                font-size: 14px;
                font-weight: bold;
                font-family: 'Consolas', monospace;
                border-radius: 6px;
                padding: 12px;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{
                background-color: #DA3633;
            }}
            QPushButton:pressed {{
                background-color: #A02020;
            }}
            QPushButton:disabled {{
                background-color: #4A1C1C;
                color: {GRAY};
            }}
        """)
        self.kill_btn.setMinimumHeight(44)
        self.kill_btn.clicked.connect(self._activate_kill_switch)
        layout.addWidget(self.kill_btn)

        return container

    # -- style helpers -------------------------------------------------------

    def _btn_teacher_style(self, active: bool) -> str:
        if active:
            return f"""
                QPushButton {{
                    background-color: {CYAN}; color: #000; font-weight: bold;
                    font-size: 12px; border-radius: 4px; padding: 4px 14px;
                    font-family: 'Consolas', monospace;
                }}
            """
        return f"""
            QPushButton {{
                background-color: {BG_INPUT}; color: {GRAY};
                font-size: 12px; border: 1px solid {BORDER};
                border-radius: 4px; padding: 4px 14px;
                font-family: 'Consolas', monospace;
            }}
        """

    def _btn_auto_style(self, active: bool) -> str:
        if active:
            return f"""
                QPushButton {{
                    background-color: {ORANGE}; color: #000; font-weight: bold;
                    font-size: 12px; border-radius: 4px; padding: 4px 14px;
                    font-family: 'Consolas', monospace;
                }}
            """
        return f"""
            QPushButton {{
                background-color: {BG_INPUT}; color: {GRAY};
                font-size: 12px; border: 1px solid {BORDER};
                border-radius: 4px; padding: 4px 14px;
                font-family: 'Consolas', monospace;
            }}
        """

    # -- mode handlers -------------------------------------------------------

    def _set_teacher_mode(self):
        if self._killed:
            return
        self._mode = "TEACHER"
        self.btn_teacher.setChecked(True)
        self.btn_auto.setChecked(False)
        self.btn_teacher.setStyleSheet(self._btn_teacher_style(True))
        self.btn_auto.setStyleSheet(self._btn_auto_style(False))
        self.mode_badge.setText("TEACHER")
        self.mode_badge.setStyleSheet(
            f"color: {CYAN}; font-size: 11px; font-weight: bold; font-family: 'Consolas', monospace;"
        )
        self.mode_changed.emit("TEACHER")
        self.terminal.log(
            f"Mode switched to TEACHER — RPA disarmed, awaiting manual trades"
        )
        self.status_rpa.set_active(False, "Disarmed")

    def _set_auto_mode(self):
        if self._killed:
            return

        # Safe Boot warning — one-time confirmation before arming RPA
        from PyQt6.QtWidgets import QMessageBox

        reply = QMessageBox.warning(
            self,
            "WARNING: RPA Armed",
            "RPA Armed. VcanTrade will take control of your mouse.\n\n"
            "Press [ESC] at any time to trigger the Emergency Kill Switch.\n\n"
            "Do you wish to proceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
            self.terminal.log("Autonomous mode cancelled by user")
            # Revert UI back to Teacher
            self.btn_teacher.setChecked(True)
            self.btn_auto.setChecked(False)
            self.btn_teacher.setStyleSheet(self._btn_teacher_style(True))
            self.btn_auto.setStyleSheet(self._btn_auto_style(False))
            self.mode_badge.setText("TEACHER")
            self.mode_badge.setStyleSheet(
                f"color: {CYAN}; font-size: 11px; font-weight: bold; font-family: 'Consolas', monospace;"
            )
            return

        self._mode = "AUTONOMOUS"
        self.btn_auto.setChecked(True)
        self.btn_teacher.setChecked(False)
        self.btn_auto.setStyleSheet(self._btn_auto_style(True))
        self.btn_teacher.setStyleSheet(self._btn_teacher_style(False))
        self.mode_badge.setText("AUTONOMOUS")
        self.mode_badge.setStyleSheet(
            f"color: {ORANGE}; font-size: 11px; font-weight: bold; font-family: 'Consolas', monospace;"
        )
        self.mode_changed.emit("AUTONOMOUS")
        self.terminal.log(
            f"Mode switched to AUTONOMOUS — RPA armed, CEO can execute trades"
        )
        self.status_rpa.set_active(True, "Armed")

    def _toggle_paper(self):
        self._paper = not self._paper
        config.DRY_RUN = self._paper
        self.paper_toggled.emit(self._paper)

        if self._paper:
            self.paper_btn.setText("PAPER (Safe)")
            self.paper_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {GREEN}; color: #000; font-weight: bold;
                    font-size: 12px; border-radius: 4px; padding: 4px 14px;
                    font-family: 'Consolas', monospace;
                }}
            """)
            self.paper_label.setText("Dry-run active — no real money")
            self.paper_label.setStyleSheet(
                f"color: {GREEN}; font-size: 10px; font-family: 'Consolas', monospace;"
            )
            self.terminal.log("Switched to PAPER mode (safe)")
        else:
            self.paper_btn.setText("LIVE (Real Money)")
            self.paper_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {RED}; color: #FFF; font-weight: bold;
                    font-size: 12px; border-radius: 4px; padding: 4px 14px;
                    font-family: 'Consolas', monospace;
                }}
            """)
            self.paper_label.setText("LIVE TRADING — real money at risk")
            self.paper_label.setStyleSheet(
                f"color: {RED}; font-size: 10px; font-family: 'Consolas', monospace;"
            )
            self.terminal.log("WARNING: Switched to LIVE mode — real money at risk!")

    # -- kill switch ---------------------------------------------------------

    def _activate_kill_switch(self):
        self._killed = True
        self.kill_btn.setText("SYSTEM HALTED")
        self.kill_btn.setEnabled(False)
        self.kill_switch_triggered.emit()
        self.terminal.log("KILL SWITCH ACTIVATED — all systems halted")
        self.status_watchtower.set_active(False, "Stopped")
        self.status_vision.set_active(False, "Stopped")
        self.status_rpa.set_active(False, "Stopped")

    # -- public API for backend threads --------------------------------------

    def log(self, message: str):
        """Thread-safe log to Swarm Terminal."""
        self.terminal.log(message)

    def set_watchtower_status(self, active: bool, text: str = ""):
        """Update Watchtower status indicator."""
        self.status_watchtower.set_active(active, text)

    def set_vision_status(self, active: bool, text: str = ""):
        """Update Vision Engine status indicator."""
        self.status_vision.set_active(active, text)

    def set_rpa_status(self, active: bool, text: str = ""):
        """Update RPA Engine status indicator."""
        self.status_rpa.set_active(active, text)

    def display_ceo_verdict(self, transcript: DebateTranscript):
        """Display the CEO's final verdict prominently."""
        risk_ok = transcript.risk_manager.verdict == "APPROVE"
        color = CEO_GREEN if risk_ok else CEO_RED

        self.ceo_banner.setStyleSheet(
            f"background-color: rgba(0, 255, 136, 0.06); "
            f"border: 1px solid {color}; border-left: 4px solid {color}; "
            f"border-radius: 8px; padding: 12px;"
        )
        self.ceo_text.setStyleSheet(
            f"color: {color}; font-size: 14px; font-weight: bold; "
            f"font-family: 'Segoe UI', sans-serif;"
        )
        self.ceo_text.setText(transcript.ceo_verdict)

        sniper = transcript.technical_sniper
        macro = transcript.macro_analyst
        risk = transcript.risk_manager
        self.ceo_details.setText(
            f"Sniper: [{sniper.action}] {sniper.conviction}  |  "
            f"Macro: [{macro.action}] {macro.conviction}  |  "
            f"Risk: [{risk.verdict}] {risk.conviction}"
        )
        self.ceo_banner.show()

    def display_signal(self, signal: OverlaySignal):
        """Display latest trading signal in terminal."""
        color = {
            "BUY": GREEN,
            "SELL": RED,
            "HOLD": ORANGE,
            "CLOSE": CYAN,
        }.get(signal.action.value, WHITE)

        self.terminal.log(
            f'<span style="color:{color};font-weight:bold">{signal.action.value}</span> '
            f"{signal.asset} — Confidence: {signal.confidence.value}"
        )
        if signal.entry_price:
            levels = f"Entry: {signal.entry_price:.5f}"
            if signal.stop_loss:
                levels += f" | SL: {signal.stop_loss:.5f}"
            if signal.take_profit:
                levels += f" | TP: {signal.take_profit:.5f}"
            self.terminal.log(f'<span style="color:{DIM}">{levels}</span>')
        if signal.reason:
            self.terminal.log(f'<span style="color:{GRAY}">{signal.reason}</span>')

    # -- close event ---------------------------------------------------------

    def closeEvent(self, event):
        from PyQt6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self,
            "Confirm Exit",
            "Are you sure you want to exit VcanTrade AI?\nAll trading will be stopped.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self._activate_kill_switch()
            event.accept()
        else:
            event.ignore()


# ---------------------------------------------------------------------------
# CalibrationWizardDialog — step-by-step RPA coordinate mapper
# ---------------------------------------------------------------------------


class CalibrationWizardDialog(QWidget):
    """
    Interactive calibration dialog.
    Guides user through clicking each broker UI element to record coordinates.
    """

    calibration_complete = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        from core.calibration import CalibrationManager, CalibrationWizard

        self.manager = CalibrationManager()
        self.wizard = CalibrationWizard(self.manager)
        self.current_point = None

        self._setup_window()
        self._build_ui()
        self._load_next_step()

    def _setup_window(self):
        self.setWindowTitle("RPA Coordinate Mapper — Calibration")
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.resize(480, 320)
        self.setStyleSheet(f"background-color: {BG_DARK};")

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("RPA Coordinate Mapper")
        title.setStyleSheet(
            f"color: {CYAN}; font-size: 18px; font-weight: bold; "
            f"font-family: 'Segoe UI', sans-serif;"
        )
        layout.addWidget(title)

        self.instruction = QLabel("")
        self.instruction.setStyleSheet(
            f"color: {WHITE}; font-size: 14px; font-family: 'Segoe UI', sans-serif; "
            f"padding: 12px; background-color: {BG_PANEL}; border-radius: 6px;"
        )
        self.instruction.setWordWrap(True)
        layout.addWidget(self.instruction)

        self.coords_display = QLabel("")
        self.coords_display.setStyleSheet(
            f"color: {GREEN}; font-size: 12px; font-family: 'Consolas', monospace; "
            f"padding: 8px;"
        )
        layout.addWidget(self.coords_display)

        # Progress bar
        self.progress = QLabel("")
        self.progress.setStyleSheet(
            f"color: {GRAY}; font-size: 11px; font-family: 'Consolas', monospace;"
        )
        layout.addWidget(self.progress)

        # Buttons
        btn_layout = QHBoxLayout()

        self.btn_capture = QPushButton("Capture Position (Space)")
        self.btn_capture.setStyleSheet(f"""
            QPushButton {{
                background-color: {CYAN}; color: #000; font-weight: bold;
                font-size: 13px; border-radius: 4px; padding: 8px 20px;
                font-family: 'Consolas', monospace;
            }}
            QPushButton:hover {{ background-color: #00B8D4; }}
        """)
        self.btn_capture.clicked.connect(self._capture_position)
        btn_layout.addWidget(self.btn_capture)

        self.btn_skip = QPushButton("Skip")
        self.btn_skip.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_INPUT}; color: {GRAY};
                font-size: 12px; border: 1px solid {BORDER};
                border-radius: 4px; padding: 8px 16px;
                font-family: 'Consolas', monospace;
            }}
        """)
        self.btn_skip.clicked.connect(self._skip_point)
        btn_layout.addWidget(self.btn_skip)

        btn_layout.addStretch()

        self.btn_done = QPushButton("Done")
        self.btn_done.setStyleSheet(f"""
            QPushButton {{
                background-color: {GREEN}; color: #000; font-weight: bold;
                font-size: 13px; border-radius: 4px; padding: 8px 20px;
                font-family: 'Consolas', monospace;
            }}
        """)
        self.btn_done.clicked.connect(self._finish)
        self.btn_done.hide()
        btn_layout.addWidget(self.btn_done)

        layout.addLayout(btn_layout)

    def _load_next_step(self):
        """Load the next uncalibrated point."""
        next_point = self.wizard.get_next_uncalibrated()
        if next_point is None:
            self._show_complete()
            return

        self.current_point = next_point
        label = self.wizard.POINT_LABELS.get(next_point, next_point)
        done = sum(
            1 for p in self.wizard.manager.get_calibration_status().values() if p
        )
        total = len(self.wizard.manager.get_calibration_status())

        self.instruction.setText(
            f"Step {done + 1}/{total}\n\n"
            f"Move your mouse over the element below, then click Capture.\n\n"
            f"→ {label}"
        )
        self.progress.setText(f"Calibrated: {done}/{total} points")

    def _capture_position(self):
        """Capture current mouse position for the current point."""
        if not self.current_point:
            return

        x, y = self.wizard.capture_current_position(self.current_point)
        self.coords_display.setText(f"Captured: {self.current_point} = ({x}, {y})")
        self.wizard.manager.save()

        # Brief visual feedback
        self.btn_capture.setText("Captured!")
        self.btn_capture.setStyleSheet(f"""
            QPushButton {{
                background-color: {GREEN}; color: #000; font-weight: bold;
                font-size: 13px; border-radius: 4px; padding: 8px 20px;
                font-family: 'Consolas', monospace;
            }}
        """)

        # Auto-advance after short delay
        QTimer.singleShot(800, self._advance)

    def _skip_point(self):
        """Skip the current point (leave at 0,0)."""
        self.coords_display.setText(f"Skipped: {self.current_point}")
        QTimer.singleShot(500, self._advance)

    def _advance(self):
        """Reset button style and load next step."""
        self.btn_capture.setText("Capture Position (Space)")
        self.btn_capture.setStyleSheet(f"""
            QPushButton {{
                background-color: {CYAN}; color: #000; font-weight: bold;
                font-size: 13px; border-radius: 4px; padding: 8px 20px;
                font-family: 'Consolas', monospace;
            }}
            QPushButton:hover {{ background-color: #00B8D4; }}
        """)
        self.coords_display.setText("")
        self._load_next_step()

    def _show_complete(self):
        """Show calibration complete message."""
        self.current_point = None
        calibrated = self.wizard.manager.is_calibrated()

        self.instruction.setText(
            "Calibration Complete!\n\n"
            f"All coordinates saved to calibration.json\n"
            f"RPA Executor will use these positions for mouse-based execution."
            if calibrated
            else "Calibration finished with some skipped points.\n"
            f"You can re-run calibration later to fill in gaps."
        )
        self.progress.setText("")
        self.coords_display.setText("")
        self.btn_capture.hide()
        self.btn_skip.hide()
        self.btn_done.show()

    def _finish(self):
        self.calibration_complete.emit()
        self.close()

    def keyPressEvent(self, event):
        """Space bar = capture position."""
        if event.key() == Qt.Key.Key_Space:
            self._capture_position()
        elif event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# VisionTestDialog — displays captured screenshot for sanity check
# ---------------------------------------------------------------------------


class VisionTestDialog(QWidget):
    """
    Shows the exact 640x480 screenshot that would be sent to the VLM.
    Lets the user verify the chart isn't cut off or obscured.
    """

    def __init__(self, image, parent=None):
        super().__init__(parent)
        self._setup_window(image)

    def _setup_window(self, image):
        self.setWindowTitle("Vision Engine — Screenshot Preview")
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )

        from PyQt6.QtGui import QPixmap
        from PyQt6.QtCore import QByteArray
        import io

        # Convert PIL Image to QPixmap
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        pixmap = QPixmap()
        pixmap.loadFromData(QByteArray(buffer.getvalue()))

        # Label to display image
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        img_label = QLabel()
        img_label.setPixmap(pixmap)
        img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(img_label)

        info = QLabel(
            f"Size: {image.size[0]}x{image.size[1]}  |  "
            f"This is exactly what moondream will see."
        )
        info.setStyleSheet(
            f"color: {GRAY}; font-size: 11px; font-family: 'Consolas', monospace; "
            f"padding: 6px; text-align: center;"
        )
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info)

        self.resize(image.size[0] + 16, image.size[1] + 60)


class TradingOverlay(QWidget):
    """
    Transparent overlay that floats above your trading platform.
    - Always on top
    - Click-through (doesn't block mouse or clicks)
    - Shows Entry/SL/TP zones with color coding
    - Displays Swarm debate + CEO verdict
    """

    update_signal = pyqtSignal(object)
    update_debate = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_signal = None
        self.current_transcript = None

        self._setup_window()
        self._setup_ui()

        self.update_signal.connect(self._update_display)
        self.update_debate.connect(self._update_debate_display)

        logger.info("Trading overlay initialized")

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(100, 100, 400, 300)
        self.setWindowOpacity(config.OVERLAY_ALPHA + 0.2)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        self.title_label = QLabel("VcaniTrade AI")
        self.title_label.setStyleSheet(
            f"color: {CYAN}; font-size: 18px; font-weight: bold; "
            f"font-family: 'Consolas', monospace;"
        )
        layout.addWidget(self.title_label)

        self.asset_label = QLabel("Waiting for signals...")
        self.asset_label.setStyleSheet(
            f"color: {WHITE}; font-size: 24px; font-weight: bold; "
            f"font-family: 'Consolas', monospace; margin-top: 10px;"
        )
        layout.addWidget(self.asset_label)

        self.action_label = QLabel("")
        self.action_label.setStyleSheet(
            f"color: {WHITE}; font-size: 28px; font-weight: bold; "
            f"font-family: 'Consolas', monospace;"
        )
        layout.addWidget(self.action_label)

        self.levels_label = QLabel("")
        self.levels_label.setStyleSheet(
            f"color: #DDDDDD; font-size: 14px; font-family: 'Consolas', monospace; "
            f"margin-top: 10px;"
        )
        layout.addWidget(self.levels_label)

        self.confidence_label = QLabel("")
        self.confidence_label.setStyleSheet(
            f"color: {GRAY}; font-size: 12px; font-family: 'Consolas', monospace;"
        )
        layout.addWidget(self.confidence_label)

        if config.SHOW_REASONING:
            self.reason_label = QLabel("")
            self.reason_label.setStyleSheet(
                f"color: #CCCCCC; font-size: 11px; font-family: 'Arial', sans-serif; "
                f"margin-top: 15px; padding: 8px; "
                f"background-color: rgba(50, 50, 50, 0.5); border-radius: 5px;"
            )
            self.reason_label.setWordWrap(True)
            layout.addWidget(self.reason_label)

        self.debate_label = QLabel("")
        self.debate_label.setStyleSheet(
            f"color: #999999; font-size: 10px; font-family: 'Consolas', monospace; "
            f"margin-top: 10px; padding: 8px; "
            f"background-color: rgba(30, 30, 40, 0.6); border-radius: 5px; "
            f"border-left: 3px solid #555555;"
        )
        self.debate_label.setWordWrap(True)
        self.debate_label.hide()
        layout.addWidget(self.debate_label)

        self.ceo_verdict_label = QLabel("")
        self.ceo_verdict_label.setStyleSheet(
            f"color: {CEO_GREEN}; font-size: 12px; font-weight: bold; "
            f"font-family: 'Consolas', monospace; margin-top: 8px; padding: 10px; "
            f"background-color: rgba(0, 255, 136, 0.08); border-radius: 5px; "
            f"border-left: 3px solid {CEO_GREEN};"
        )
        self.ceo_verdict_label.setWordWrap(True)
        self.ceo_verdict_label.hide()
        layout.addWidget(self.ceo_verdict_label)

    def update_signal_handler(self, signal: OverlaySignal):
        self.update_signal.emit(signal)

    def update_debate_transcript(self, transcript: DebateTranscript):
        self.update_debate.emit(transcript)

    def _update_debate_display(self, transcript: DebateTranscript):
        self.current_transcript = transcript
        sniper = transcript.technical_sniper
        macro = transcript.macro_analyst
        risk = transcript.risk_manager

        debate_text = (
            f"SWARM DEBATE\n"
            f"  Sniper:  [{sniper.action}] {sniper.conviction} — {sniper.brief[:100]}\n"
            f"  Macro:   [{macro.action}] {macro.conviction} — {macro.brief[:100]}\n"
            f"  Risk:    [{risk.verdict}] {risk.conviction} — {risk.brief[:100]}"
        )
        self.debate_label.setText(debate_text)
        self.debate_label.show()

        verdict_color = (
            CEO_GREEN if transcript.risk_manager.verdict == "APPROVE" else CEO_RED
        )
        self.ceo_verdict_label.setStyleSheet(f"""
            QLabel {{
                color: {verdict_color}; font-size: 12px; font-weight: bold;
                font-family: 'Consolas', monospace; margin-top: 8px; padding: 10px;
                background-color: rgba(0, 255, 136, 0.08); border-radius: 5px;
                border-left: 3px solid {verdict_color};
            }}
        """)
        self.ceo_verdict_label.setText(f"CEO: {transcript.ceo_verdict}")
        self.ceo_verdict_label.show()
        self.setFixedHeight(520)

    def _update_display(self, signal: OverlaySignal):
        self.current_signal = signal
        self.asset_label.setText(signal.asset)

        action_color = signal.get_color_code()
        self.action_label.setStyleSheet(f"""
            QLabel {{
                color: {action_color}; font-size: 28px; font-weight: bold;
                font-family: 'Consolas', monospace;
            }}
        """)
        self.action_label.setText(signal.action.value)

        levels_text = ""
        if signal.entry_price:
            levels_text += f"Entry: {signal.entry_price:.5f}"
        if signal.stop_loss:
            levels_text += f"\nStop Loss: {signal.stop_loss:.5f}"
        if signal.take_profit:
            levels_text += f"\nTake Profit: {signal.take_profit:.5f}"
        self.levels_label.setText(levels_text)

        confidence_emoji = {
            "LOW": "🔵",
            "MEDIUM": "🟡",
            "HIGH": "🟠",
            "VERY_HIGH": "🔴",
        }
        conf = confidence_emoji.get(signal.confidence.value, "⚪")
        self.confidence_label.setText(f"Confidence: {signal.confidence.value} {conf}")

        if config.SHOW_REASONING and hasattr(self, "reason_label"):
            self.reason_label.setText(signal.reason)

        self.show()

    def clear_display(self):
        self.asset_label.setText("Waiting for signals...")
        self.action_label.setText("")
        self.levels_label.setText("")
        self.confidence_label.setText("")
        if hasattr(self, "reason_label"):
            self.reason_label.setText("")
        if hasattr(self, "debate_label"):
            self.debate_label.setText("")
            self.debate_label.hide()
        if hasattr(self, "ceo_verdict_label"):
            self.ceo_verdict_label.setText("")
            self.ceo_verdict_label.hide()
        self.setFixedHeight(300)
        self.current_transcript = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg_color = QColor(20, 20, 30, 180)
        painter.fillRect(self.rect(), bg_color)

        if self.current_signal:
            border_color = QColor(self.current_signal.get_color_code())
            border_color.setAlpha(200)
        else:
            border_color = QColor(100, 100, 100, 150)

        pen = QPen(border_color, 2)
        painter.setPen(pen)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)
