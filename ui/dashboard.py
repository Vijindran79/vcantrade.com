"""
VcaniTrade AI - Transparent Overlay HUD
Glass-like always-on-top overlay that floats above trading platform
Shows Entry/SL/TP zones with AI reasoning - completely click-through
"""

import logging
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QApplication
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QColor, QPainter, QPen, QFont

import config
from core.models import OverlaySignal, SignalAction, DebateTranscript

logger = logging.getLogger(__name__)


class TradingOverlay(QWidget):
    """
    Transparent overlay that displays trading signals
    - Always on top
    - Click-through (doesn't block mouse)
    - Shows Entry/SL/TP zones
    - Updates in real-time
    """

    update_signal = pyqtSignal(OverlaySignal)
    update_debate = pyqtSignal(object)  # DebateTranscript

    def __init__(self):
        super().__init__()
        self.current_signal = None
        self.current_transcript = None

        self._setup_window()
        self._setup_ui()

        # Connect update signals
        self.update_signal.connect(self._update_display)
        self.update_debate.connect(self._update_debate_display)

        logger.info("Trading overlay initialized")

    def _setup_window(self):
        """Configure transparent always-on-top window"""
        # Window flags for overlay behavior
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint  # No border
            | Qt.WindowType.WindowStaysOnTopHint  # Always on top
            | Qt.WindowType.WindowTransparentForInput  # Click-through
        )

        # Transparency
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Position and size
        self.setGeometry(100, 100, 400, 300)

        # Opacity
        self.setWindowOpacity(
            config.OVERLAY_ALPHA + 0.2
        )  # Slightly more opaque for visibility

    def _setup_ui(self):
        """Setup overlay UI elements"""
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        # Title
        self.title_label = QLabel("VcaniTrade AI")
        self.title_label.setStyleSheet("""
            QLabel {
                color: #00FFFF;
                font-size: 18px;
                font-weight: bold;
                font-family: 'Consolas', 'Courier New', monospace;
            }
        """)
        layout.addWidget(self.title_label)

        # Asset
        self.asset_label = QLabel("Waiting for signals...")
        self.asset_label.setStyleSheet("""
            QLabel {
                color: #FFFFFF;
                font-size: 24px;
                font-weight: bold;
                font-family: 'Consolas', 'Courier New', monospace;
                margin-top: 10px;
            }
        """)
        layout.addWidget(self.asset_label)

        # Action
        self.action_label = QLabel("")
        self.action_label.setStyleSheet("""
            QLabel {
                color: #FFFFFF;
                font-size: 28px;
                font-weight: bold;
                font-family: 'Consolas', 'Courier New', monospace;
            }
        """)
        layout.addWidget(self.action_label)

        # Entry/SL/TP
        self.levels_label = QLabel("")
        self.levels_label.setStyleSheet("""
            QLabel {
                color: #DDDDDD;
                font-size: 14px;
                font-family: 'Consolas', 'Courier New', monospace;
                margin-top: 10px;
            }
        """)
        layout.addWidget(self.levels_label)

        # Confidence
        self.confidence_label = QLabel("")
        self.confidence_label.setStyleSheet("""
            QLabel {
                color: #AAAAAA;
                font-size: 12px;
                font-family: 'Consolas', 'Courier New', monospace;
            }
        """)
        layout.addWidget(self.confidence_label)

        # AI Reason
        if config.SHOW_REASONING:
            self.reason_label = QLabel("")
            self.reason_label.setStyleSheet("""
                QLabel {
                    color: #CCCCCC;
                    font-size: 11px;
                    font-family: 'Arial', sans-serif;
                    margin-top: 15px;
                    padding: 8px;
                    background-color: rgba(50, 50, 50, 0.5);
                    border-radius: 5px;
                }
            """)
            self.reason_label.setWordWrap(True)
            layout.addWidget(self.reason_label)

        # Swarm Debate Section
        self.debate_label = QLabel("")
        self.debate_label.setStyleSheet("""
            QLabel {
                color: #999999;
                font-size: 10px;
                font-family: 'Consolas', 'Courier New', monospace;
                margin-top: 10px;
                padding: 8px;
                background-color: rgba(30, 30, 40, 0.6);
                border-radius: 5px;
                border-left: 3px solid #555555;
            }
        """)
        self.debate_label.setWordWrap(True)
        self.debate_label.hide()  # Hidden until debate data arrives
        layout.addWidget(self.debate_label)

        # CEO Verdict Banner
        self.ceo_verdict_label = QLabel("")
        self.ceo_verdict_label.setStyleSheet("""
            QLabel {
                color: #00FF88;
                font-size: 12px;
                font-weight: bold;
                font-family: 'Consolas', 'Courier New', monospace;
                margin-top: 8px;
                padding: 10px;
                background-color: rgba(0, 255, 136, 0.08);
                border-radius: 5px;
                border-left: 3px solid #00FF88;
            }
        """)
        self.ceo_verdict_label.setWordWrap(True)
        self.ceo_verdict_label.hide()
        layout.addWidget(self.ceo_verdict_label)

        self.setLayout(layout)

    def update_signal_handler(self, signal: OverlaySignal):
        """Thread-safe signal update"""
        self.update_signal.emit(signal)

    def update_debate_transcript(self, transcript: DebateTranscript):
        """Thread-safe debate transcript update"""
        self.update_debate.emit(transcript)

    def _update_debate_display(self, transcript: DebateTranscript):
        """Update overlay with swarm debate transcript"""
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

        # CEO verdict
        verdict_color = (
            "#00FF88" if transcript.risk_manager.verdict == "APPROVE" else "#FF6644"
        )
        self.ceo_verdict_label.setStyleSheet(f"""
            QLabel {{
                color: {verdict_color};
                font-size: 12px;
                font-weight: bold;
                font-family: 'Consolas', 'Courier New', monospace;
                margin-top: 8px;
                padding: 10px;
                background-color: rgba(0, 255, 136, 0.08);
                border-radius: 5px;
                border-left: 3px solid {verdict_color};
            }}
        """)
        self.ceo_verdict_label.setText(f"CEO: {transcript.ceo_verdict}")
        self.ceo_verdict_label.show()

        # Resize overlay to fit new content
        self.setFixedHeight(520)

    def _update_display(self, signal: OverlaySignal):
        """Update overlay with new signal"""
        self.current_signal = signal

        # Update labels
        self.asset_label.setText(signal.asset)

        # Action with color
        action_color = signal.get_color_code()
        self.action_label.setStyleSheet(f"""
            QLabel {{
                color: {action_color};
                font-size: 28px;
                font-weight: bold;
                font-family: 'Consolas', 'Courier New', monospace;
            }}
        """)
        self.action_label.setText(signal.action.value)

        # Price levels
        levels_text = ""
        if signal.entry_price:
            levels_text += f"Entry: {signal.entry_price:.5f}"
        if signal.stop_loss:
            levels_text += f"\nStop Loss: {signal.stop_loss:.5f}  🔴"
        if signal.take_profit:
            levels_text += f"\nTake Profit: {signal.take_profit:.5f}  🟢"

        self.levels_label.setText(levels_text)

        # Confidence
        confidence_emoji = {
            "LOW": "🔵",
            "MEDIUM": "🟡",
            "HIGH": "🟠",
            "VERY_HIGH": "🔴",
        }
        conf = confidence_emoji.get(signal.confidence.value, "⚪")
        self.confidence_label.setText(f"Confidence: {signal.confidence.value} {conf}")

        # Reason
        if config.SHOW_REASONING and hasattr(self, "reason_label"):
            self.reason_label.setText(signal.reason)

        # Show overlay
        self.show()
        logger.debug(f"Overlay updated: {signal.action.value} {signal.asset}")

    def clear_display(self):
        """Clear overlay display"""
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
        """Custom paint for border and background"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Semi-transparent background
        bg_color = QColor(20, 20, 30, 180)  # Dark blue-black
        painter.fillRect(self.rect(), bg_color)

        # Border
        if self.current_signal:
            border_color = QColor(self.current_signal.get_color_code())
            border_color.setAlpha(200)
        else:
            border_color = QColor(100, 100, 100, 150)

        pen = QPen(border_color, 2)
        painter.setPen(pen)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)


class ControlWindow(QWidget):
    """
    Main control window for VcaniTrade AI
    - Teacher/Auto mode toggle
    - Paper/Live mode toggle
    - Kill switch
    - Live log display
    """

    mode_changed = pyqtSignal(str)  # TEACHER or AUTO
    kill_switch_triggered = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.current_mode = "TEACHER"
        self.paper_mode = True
        self.log_messages = []

        self._setup_window()
        self._setup_ui()

        logger.info("Control window initialized")

    def _setup_window(self):
        """Configure control window"""
        self.setWindowTitle("VcaniTrade AI - Teacher / Trader")
        self.setGeometry(100, 100, 500, 600)

    def _setup_ui(self):
        """Setup control window UI"""
        from PyQt6.QtWidgets import QPushButton, QTextEdit, QComboBox, QHBoxLayout

        layout = QVBoxLayout()
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        # Title
        title = QLabel("🎯 VcaniTrade AI Control Panel")
        title.setStyleSheet("""
            QLabel {
                color: #00FFFF;
                font-size: 20px;
                font-weight: bold;
                padding: 10px;
            }
        """)
        layout.addWidget(title)

        # Mode Selection
        mode_layout = QHBoxLayout()
        mode_label = QLabel("Mode:")
        mode_label.setStyleSheet("color: white; font-size: 14px;")
        mode_layout.addWidget(mode_label)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["TEACHER MODE", "AUTO MODE"])
        self.mode_combo.setStyleSheet("""
            QComboBox {
                background-color: #2B2B2B;
                color: white;
                padding: 8px;
                font-size: 13px;
            }
        """)
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        mode_layout.addWidget(self.mode_combo)
        layout.addLayout(mode_layout)

        # Paper/Live toggle
        paper_layout = QHBoxLayout()
        paper_label = QLabel("Trading:")
        paper_label.setStyleSheet("color: white; font-size: 14px;")
        paper_layout.addWidget(paper_label)

        self.paper_button = QPushButton("📝 PAPER MODE (Safe)")
        self.paper_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                padding: 10px;
                font-size: 13px;
                font-weight: bold;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        self.paper_button.clicked.connect(self._toggle_paper_mode)
        paper_layout.addWidget(self.paper_button)
        layout.addLayout(paper_layout)

        # Kill Switch
        self.kill_button = QPushButton("🛑 KILL SWITCH")
        self.kill_button.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                padding: 12px;
                font-size: 14px;
                font-weight: bold;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #da190b;
            }
        """)
        self.kill_button.clicked.connect(self._on_kill_switch)
        layout.addWidget(self.kill_button)

        # Status
        self.status_label = QLabel("Status: ✅ Safe - Paper Trading - Teacher Mode")
        self.status_label.setStyleSheet("""
            QLabel {
                color: #4CAF50;
                font-size: 12px;
                padding: 8px;
                background-color: rgba(50, 50, 50, 0.5);
                border-radius: 5px;
            }
        """)
        layout.addWidget(self.status_label)

        # Log
        log_label = QLabel("📜 Activity Log:")
        log_label.setStyleSheet("color: white; font-size: 13px; font-weight: bold;")
        layout.addWidget(log_label)

        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setStyleSheet("""
            QTextEdit {
                background-color: #1E1E1E;
                color: #00FF00;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px;
                padding: 10px;
            }
        """)
        layout.addWidget(self.log_display)

        self.setLayout(layout)

    def _on_mode_changed(self, mode: str):
        """Handle mode change"""
        self.current_mode = mode.replace(" MODE", "")
        self._update_status()
        self.add_log(f"Mode changed to: {self.current_mode}")
        self.mode_changed.emit(self.current_mode)

    def _toggle_paper_mode(self):
        """Toggle between paper and live trading"""
        self.paper_mode = not self.paper_mode

        if self.paper_mode:
            self.paper_button.setText("📝 PAPER_MODE (Safe)")
            self.paper_button.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    padding: 10px;
                    font-size: 13px;
                    font-weight: bold;
                    border-radius: 5px;
                }
            """)
            config.DRY_RUN = True
            self.add_log("Switched to PAPER mode (safe)")
        else:
            self.paper_button.setText("💰 LIVE TRADING (Risk!)")
            self.paper_button.setStyleSheet("""
                QPushButton {
                    background-color: #FF9800;
                    color: white;
                    padding: 10px;
                    font-size: 13px;
                    font-weight: bold;
                    border-radius: 5px;
                }
            """)
            config.DRY_RUN = False
            self.add_log("⚠️ Switched to LIVE mode (real money at risk!)")

        self._update_status()

    def _on_kill_switch(self):
        """Activate kill switch"""
        self.kill_switch_triggered.emit()
        self.add_log("🛑 KILL SWITCH ACTIVATED - All trading halted")

        self.kill_button.setStyleSheet("""
            QPushButton {
                background-color: #8B0000;
                color: white;
                padding: 12px;
                font-size: 14px;
                font-weight: bold;
                border-radius: 5px;
            }
        """)
        self.kill_button.setText("🛑 TRADING HALTED")
        self.kill_button.setEnabled(False)

        self._update_status()

    def _update_status(self):
        """Update status display"""
        mode_text = self.current_mode
        paper_text = "Paper" if self.paper_mode else "LIVE ⚠️"
        kill_text = "Active" if not config.KILL_SWITCH else "HALTED"

        if config.KILL_SWITCH:
            color = "#F44336"
            status = f"Status: 🛑 Kill Switch {kill_text}"
        else:
            color = "#4CAF50"
            status = f"Status: ✅ {mode_text} - {paper_text} Trading"

        self.status_label.setStyleSheet(f"""
            QLabel {{
                color: {color};
                font-size: 12px;
                padding: 8px;
                background-color: rgba(50, 50, 50, 0.5);
                border-radius: 5px;
            }}
        """)
        self.status_label.setText(status)

    def add_log(self, message: str):
        """Add message to log display"""
        from datetime import datetime

        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}"

        self.log_messages.append(log_entry)
        self.log_display.append(log_entry)

        # Keep only last 100 messages
        if len(self.log_messages) > 100:
            self.log_messages = self.log_messages[-100:]

    def closeEvent(self, event):
        """Handle window close event - emit signal to shutdown app"""
        from PyQt6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self,
            "Confirm Exit",
            "Are you sure you want to exit VcaniTrade AI?\nAll trading will be stopped.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.kill_switch_triggered.emit()
            event.accept()
        else:
            event.ignore()
