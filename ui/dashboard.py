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
    - Emergency Kill Switch
    """

    mode_changed = pyqtSignal(str)  # "TEACHER" or "AUTONOMOUS"
    paper_toggled = pyqtSignal(bool)  # True = paper, False = live
    kill_switch_triggered = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._mode = "TEACHER"
        self._paper = True
        self._killed = False

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
