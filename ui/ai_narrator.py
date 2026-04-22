"""
VcanTrade AI - AI Narrator Overlay

A sleek glassmorphic assistant that narrates what the bot is doing in real-time.
Like Jarvis from Iron Man - always visible, always informative.

Features:
- Glass-effect floating panel
- Real-time activity feed
- Typing animation for messages
- Status indicators (scanning, analyzing, executing)
- Auto-scrolling activity log
- Minimal, beautiful design
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea, QFrame, QGraphicsDropShadowEffect, QSizeGrip,
    QPushButton, QSlider, QComboBox, QApplication
)
from PyQt6.QtCore import (
    Qt, QTimer, pyqtSignal, QPropertyAnimation, 
    QEasingCurve, QVariantAnimation, QEventLoop
)
from PyQt6.QtGui import (
    QFont, QColor, QPainter, QBrush, QPen,
    QLinearGradient, QFontMetrics, QIcon
)
from datetime import datetime
import logging
import sys
import time

logger = logging.getLogger(__name__)


class TypingLabel(QLabel):
    """Label with typing animation effect."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.full_text = ""
        self.current_index = 0
        self.timer = QTimer()
        self.timer.timeout.connect(self._type_next_char)
        self.setStyleSheet("""
            color: #E6EDF3;
            font-size: 14px;
            padding: 8px;
            background: transparent;
        """)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    
    def start_typing(self, text: str):
        """Start typing animation."""
        self.full_text = text
        self.current_index = 0
        self.setText("")
        self.timer.start(30)  # Type one character every 30ms
    
    def _type_next_char(self):
        if self.current_index < len(self.full_text):
            self.current_index += 1
            self.setText(self.full_text[:self.current_index])
        else:
            self.timer.stop()
    
    def set_text_instant(self, text: str):
        """Set text without animation."""
        self.full_text = text
        self.setText(text)
        self.timer.stop()


class ActivityItem(QWidget):
    """Single activity log item with icon and timestamp."""
    
    def __init__(self, icon: str, message: str, timestamp: str, parent=None):
        super().__init__(parent)
        self.init_ui(icon, message, timestamp)
    
    def init_ui(self, icon: str, message: str, timestamp: str):
        layout = QHBoxLayout()
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(10)
        
        # Icon
        icon_label = QLabel(icon)
        icon_label.setStyleSheet("""
            font-size: 16px;
            padding: 0px;
            background: transparent;
        """)
        icon_label.setFixedWidth(24)
        
        # Message container
        msg_layout = QVBoxLayout()
        msg_layout.setSpacing(2)
        
        # Message
        msg_label = QLabel(message)
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet("""
            color: #E6EDF3;
            font-size: 13px;
            background: transparent;
        """)
        msg_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        
        # Timestamp
        time_label = QLabel(timestamp)
        time_label.setStyleSheet("""
            color: #8B949E;
            font-size: 10px;
            background: transparent;
        """)
        
        msg_layout.addWidget(msg_label)
        msg_layout.addWidget(time_label)
        
        layout.addWidget(icon_label)
        layout.addLayout(msg_layout)
        
        self.setLayout(layout)
        self.setStyleSheet("""
            background: transparent;
        """)


class GlassmorphicPanel(QWidget):
    """Base glassmorphic panel with blur effect."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_windows = sys.platform == "win32"
        self._is_dragging = False
        self._drag_offset = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, not self._is_windows)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMinimumSize(340, 380)

        if self._is_windows:
            self._apply_panel_chrome()
        else:
            shadow = QGraphicsDropShadowEffect()
            shadow.setBlurRadius(30)
            shadow.setXOffset(0)
            shadow.setYOffset(8)
            shadow.setColor(QColor(0, 0, 0, 100))
            self.setGraphicsEffect(shadow)

    def _apply_panel_chrome(self):
        """Apply Windows-safe panel styling, including aggression-mode chrome."""
        if not self._is_windows:
            return

        aggression = getattr(self, "_aggression_mode", False)
        background = "rgba(56, 16, 22, 240)" if aggression else "rgba(16, 22, 36, 235)"
        signal_alert = getattr(self, "_signal_alert_active", False) and getattr(self, "_signal_alert_flash_state", False)
        if signal_alert:
            border = "rgba(57, 255, 20, 235)"
        else:
            border = "rgba(248, 81, 73, 200)" if aggression else "rgba(100, 120, 160, 120)"
        self.setStyleSheet(
            f"background-color: {background};"
            f"border: {'2px' if signal_alert else '1px'} solid {border};"
            "border-radius: 12px;"
        )
    
    def paintEvent(self, event):
        """Draw glassmorphic background with safety checks."""
        if self._is_windows:
            super().paintEvent(event)
            return

        # Safety: Don't paint if widget isn't visible or active
        if not self.isVisible() or not self.isActiveWindow():
            return
            
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            
            # Create glass gradient
            aggression = getattr(self, "_aggression_mode", False)
            gradient = QLinearGradient(0, 0, 0, self.height())
            if aggression:
                gradient.setColorAt(0, QColor(74, 14, 22, 225))
                gradient.setColorAt(0.5, QColor(52, 10, 18, 215))
                gradient.setColorAt(1, QColor(32, 8, 14, 225))
                border_color = QColor(248, 81, 73, 190)
                highlight_top = QColor(255, 120, 120, 42)
            else:
                gradient.setColorAt(0, QColor(20, 25, 40, 200))
                gradient.setColorAt(0.5, QColor(15, 20, 35, 190))
                gradient.setColorAt(1, QColor(10, 15, 30, 200))
                border_color = QColor(100, 120, 160, 80)
                highlight_top = QColor(255, 255, 255, 30)

            if getattr(self, "_signal_alert_active", False) and getattr(self, "_signal_alert_flash_state", False):
                border_color = QColor(57, 255, 20, 225)
                highlight_top = QColor(120, 255, 140, 58)
            
            # Draw rounded rectangle
            rect = self.rect().adjusted(2, 2, -2, -2)
            painter.setBrush(QBrush(gradient))
            painter.setPen(QPen(border_color, 1.5))
            painter.drawRoundedRect(rect, 16, 16)
            
            # Add subtle top highlight
            highlight = QLinearGradient(0, 0, 0, 40)
            highlight.setColorAt(0, highlight_top)
            highlight.setColorAt(1, QColor(255, 255, 255, 0))
            painter.setBrush(QBrush(highlight))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -30), 14, 14)
            
            painter.end()
        except Exception:
            pass  # Silently ignore paint errors

    def mousePressEvent(self, event):
        if getattr(self, "_pinned", False):
            event.ignore()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if getattr(self, "_pinned", False):
            event.ignore()
            return
        if self._is_dragging and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if getattr(self, "_pinned", False):
            event.ignore()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = False
            self._drag_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class AINarratorOverlay(GlassmorphicPanel):
    """
    AI Narrator - Floating glassmorphic overlay that shows bot activity.
    
    Narrates:
    - Scanning markets
    - Analyzing signals
    - Executing trades
    - Monitoring positions
    - System status
    """
    
    # Signals for thread-safe updates
    activity_added = pyqtSignal(str, str, str)  # icon, message, timestamp
    stealth_toggled = pyqtSignal(bool)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.activity_count = 0
        self._font_scale = 1.0
        self._pinned = True
        self._analysis_mode = False
        self._aggression_mode = False
        self._rpa_enabled = True
        self._signal_alert_active = False
        self._signal_alert_flash_state = False
        self._signal_alert_deadline = 0.0
        self._current_opacity = 0.96 if sys.platform == "win32" else 1.0
        self.setWindowOpacity(self._current_opacity)
        self.signal_alert_timer = QTimer(self)
        self.signal_alert_timer.timeout.connect(self._pulse_signal_alert_border)
        self.init_ui()
        self._apply_window_mode()
        self.start_status_timer()
        
        # Connect signals
        self.activity_added.connect(self.add_activity)
        
        logger.info("AI Narrator Overlay initialized")
    
    def init_ui(self):
        """Initialize the narrator UI."""
        self.resize(440, 640)
        
        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)
        
        # Header with status indicator
        header = self._create_header()
        self.header_widget = header
        main_layout.addWidget(header)

        controls = self._create_controls_row()
        main_layout.addWidget(controls)

        self.live_pnl_label = QLabel("Live PnL: $0.00 | Positions: 0")
        self.live_pnl_label.setStyleSheet(
            "color: #3FB950; font-size: 12px; font-weight: bold; background: transparent;"
        )
        main_layout.addWidget(self.live_pnl_label)

        hud_frame = QFrame()
        hud_frame.setStyleSheet(
            "QFrame { background: rgba(12,18,30,210); border: 1px solid rgba(88,166,255,0.22); border-radius: 8px; }"
        )
        hud_layout = QHBoxLayout()
        hud_layout.setContentsMargins(10, 6, 10, 6)
        hud_layout.setSpacing(10)
        self.daily_bullets_label = QLabel("Daily Bullets: 0/30")
        self.daily_bullets_label.setStyleSheet(
            "color: #58A6FF; font-size: 11px; font-weight: bold; background: transparent;"
        )
        self.lockout_timer_label = QLabel("Lockout Timer: READY")
        self.lockout_timer_label.setStyleSheet(
            "color: #F2CC60; font-size: 11px; font-weight: bold; background: transparent;"
        )
        hud_layout.addWidget(self.daily_bullets_label)
        hud_layout.addStretch()
        hud_layout.addWidget(self.lockout_timer_label)
        hud_frame.setLayout(hud_layout)
        main_layout.addWidget(hud_frame)

        self.watchlist_status_frame = QFrame()
        self.watchlist_status_frame.setStyleSheet(
            "QFrame { background: rgba(30,40,60,180); border: 1px solid rgba(88,166,255,0.25); border-radius: 6px; }"
        )
        self.watchlist_status_layout = QVBoxLayout()
        self.watchlist_status_layout.setContentsMargins(10, 8, 10, 8)
        self.watchlist_status_layout.setSpacing(4)
        self.watchlist_status_frame.setLayout(self.watchlist_status_layout)
        watchlist_title = QLabel("Watchlist Radar")
        watchlist_title.setStyleSheet("color: #58A6FF; font-size: 11px; font-weight: bold; background: transparent;")
        self.watchlist_status_layout.addWidget(watchlist_title)
        self.ticker_status_labels = {}
        main_layout.addWidget(self.watchlist_status_frame)

        # [EMOJI] Live Ledger [EMOJI]
        ledger_frame = QFrame()
        ledger_frame.setStyleSheet(
            "QFrame { background: rgba(30,40,60,180); border: 1px solid rgba(88,166,255,0.25);"
            "border-radius: 6px; }"
        )
        ledger_layout = QHBoxLayout()
        ledger_layout.setContentsMargins(10, 6, 10, 6)
        ledger_layout.setSpacing(0)

        def _ledger_col(title: str, value: str, color: str = "#E6EDF3") -> QVBoxLayout:
            col = QVBoxLayout()
            col.setSpacing(2)
            t = QLabel(title)
            t.setStyleSheet("color: #8B949E; font-size: 9px; background: transparent;")
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v = QLabel(value)
            v.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: bold; background: transparent;")
            v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col.addWidget(t)
            col.addWidget(v)
            return col, v

        active_col, self._ledger_active_val = _ledger_col("ACTIVE TRADES", "0", "#58A6FF")
        pnl_col, self._ledger_pnl_val = _ledger_col("UNREALIZED PnL", "$0.00", "#3FB950")
        rate_col, self._ledger_rate_val = _ledger_col("DAILY SUCCESS", "0%", "#D29922")

        sep_style = "color: rgba(88,166,255,0.3); font-size: 18px; background: transparent;"

        ledger_layout.addLayout(active_col)
        sep1 = QLabel("|"); sep1.setStyleSheet(sep_style); sep1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ledger_layout.addWidget(sep1)
        ledger_layout.addLayout(pnl_col)
        sep2 = QLabel("|"); sep2.setStyleSheet(sep_style); sep2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ledger_layout.addWidget(sep2)
        ledger_layout.addLayout(rate_col)

        ledger_frame.setLayout(ledger_layout)
        main_layout.addWidget(ledger_frame)
        # [EMOJI]
        
        # Current status (typing label)
        self.status_label = TypingLabel()
        self.status_label.setWordWrap(True)
        main_layout.addWidget(self.status_label)

        self.levels_frame = QFrame()
        self.levels_frame.setStyleSheet(
            "QFrame { background: rgba(12,18,30,220); border: 1px solid rgba(88,166,255,0.45); border-radius: 10px; }"
        )
        levels_layout = QVBoxLayout()
        levels_layout.setContentsMargins(12, 10, 12, 10)
        levels_layout.setSpacing(6)

        levels_header = QHBoxLayout()
        levels_header.setSpacing(8)

        self.levels_title = QLabel("TEACHER BROADCAST")
        self.levels_title.setStyleSheet(
            "color: #58A6FF; font-size: 13px; font-weight: bold; letter-spacing: 1px; background: transparent;"
        )
        levels_header.addWidget(self.levels_title)
        levels_header.addStretch()

        self.clear_levels_btn = QPushButton("[RELOAD]")
        self.clear_levels_btn.setFixedSize(24, 24)
        self.clear_levels_btn.setToolTip("Reset Teacher Broadcast levels")
        self.clear_levels_btn.setStyleSheet(
            "QPushButton { background: rgba(248,81,73,0.16); color: #FF7B72; border: 1px solid rgba(248,81,73,0.45);"
            "border-radius: 12px; font-size: 12px; font-weight: bold; }"
            "QPushButton:hover { background: rgba(248,81,73,0.28); }"
        )
        self.clear_levels_btn.clicked.connect(lambda: self.clear_trade_levels(announce=True))
        levels_header.addWidget(self.clear_levels_btn)
        levels_layout.addLayout(levels_header)

        self.levels_ticker = QLabel("NO ACTIVE LEVELS")
        self.levels_ticker.setStyleSheet(
            "color: #E6EDF3; font-size: 18px; font-weight: bold; background: transparent;"
        )
        levels_layout.addWidget(self.levels_ticker)

        self.levels_sl_label = QLabel("SL: --")
        self.levels_sl_label.setStyleSheet(
            "color: #FF7B72; font-size: 26px; font-weight: bold; background: transparent;"
        )
        levels_layout.addWidget(self.levels_sl_label)

        self.levels_tp_label = QLabel("TP: --")
        self.levels_tp_label.setStyleSheet(
            "color: #3FB950; font-size: 26px; font-weight: bold; background: transparent;"
        )
        levels_layout.addWidget(self.levels_tp_label)

        self.levels_liquidity_label = QLabel("LIQ: --")
        self.levels_liquidity_label.setWordWrap(True)
        self.levels_liquidity_label.setStyleSheet(
            "color: #F2CC60; font-size: 24px; font-weight: bold; background: transparent;"
        )
        levels_layout.addWidget(self.levels_liquidity_label)

        self.levels_frame.setLayout(levels_layout)
        main_layout.addWidget(self.levels_frame)
        
        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(100, 120, 160, 0), stop:0.5 rgba(100, 120, 160, 80), stop:1 rgba(100, 120, 160, 0));")
        separator.setFixedHeight(1)
        main_layout.addWidget(separator)
        
        # Activity feed title
        feed_title = QLabel("[CLIPBOARD] Activity Feed")
        feed_title.setStyleSheet("""
            color: #58A6FF;
            font-size: 12px;
            font-weight: bold;
            background: transparent;
            padding: 4px 0px;
        """)
        main_layout.addWidget(feed_title)
        
        # Scrollable activity feed
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: rgba(100, 120, 160, 30);
                width: 6px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: rgba(100, 120, 160, 80);
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: none;
            }
        """)
        
        # Activity container
        self.activity_container = QWidget()
        self.activity_layout = QVBoxLayout()
        self.activity_layout.setContentsMargins(0, 0, 0, 0)
        self.activity_layout.setSpacing(4)
        self.activity_container.setLayout(self.activity_layout)
        self.activity_container.setStyleSheet("background: transparent;")
        
        self.scroll_area.setWidget(self.activity_container)
        main_layout.addWidget(self.scroll_area)

        # Bottom bar with resize grip for multi-monitor usability.
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(0, 2, 0, 0)
        self.help_label = QLabel("Drag anywhere to move [BULLET] Resize from bottom-right")
        self.help_label.setStyleSheet("color: #8B949E; font-size: 10px; background: transparent;")
        bottom_bar.addWidget(self.help_label)
        bottom_bar.addStretch()
        self.size_grip = QSizeGrip(self)
        self.size_grip.setStyleSheet("background: transparent;")
        bottom_bar.addWidget(self.size_grip)
        main_layout.addLayout(bottom_bar)
        
        self.setLayout(main_layout)
        
        # Set initial status
        self._refresh_mode_label()
        self._refresh_pin_state(announce=False)
        self.set_status("idle")

    def _create_controls_row(self) -> QWidget:
        """Create mirror operator controls (pin, opacity, font, snap)."""
        row = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.pin_btn = QPushButton("Toggle Pin")
        self.pin_btn.setCheckable(True)
        self.pin_btn.setChecked(True)
        self.pin_btn.setToolTip("Pinned: transparent overlay. Unpinned: movable window.")
        self.pin_btn.setFixedSize(88, 24)
        self.pin_btn.clicked.connect(self._toggle_pin)

        self.font_minus_btn = QPushButton("A-")
        self.font_minus_btn.setFixedSize(30, 24)
        self.font_minus_btn.clicked.connect(lambda: self._change_font_scale(-0.05))

        self.font_plus_btn = QPushButton("A+")
        self.font_plus_btn.setFixedSize(30, 24)
        self.font_plus_btn.clicked.connect(lambda: self._change_font_scale(0.05))

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setMinimum(55)
        self.opacity_slider.setMaximum(100)
        self.opacity_slider.setValue(int(self._current_opacity * 100))
        self.opacity_slider.setFixedWidth(90)
        self.opacity_slider.setToolTip("Mirror opacity")
        self.opacity_slider.valueChanged.connect(self._set_overlay_opacity)

        self.snap_combo = QComboBox()
        self.snap_combo.addItems([
            "Snap",
            "Top-Left",
            "Top-Right",
            "Bottom-Left",
            "Bottom-Right",
            "Center",
        ])
        self.snap_combo.setFixedWidth(108)
        self.snap_combo.currentIndexChanged.connect(self._snap_from_combo)

        self.stealth_btn = QPushButton("[MOUSE]")
        self.stealth_btn.setCheckable(True)
        self.stealth_btn.setChecked(True)
        self.stealth_btn.setFixedSize(32, 24)
        self.stealth_btn.setToolTip("RPA Hand execution enabled")
        self.stealth_btn.clicked.connect(self._toggle_rpa_execution)

        self.mode_label = QLabel()

        for btn in [self.pin_btn, self.font_minus_btn, self.font_plus_btn, self.stealth_btn]:
            btn.setStyleSheet(
                "QPushButton { background: rgba(88,166,255,0.18); color: #E6EDF3;"
                "border: 1px solid rgba(88,166,255,0.5); border-radius: 4px; font-size: 11px; }"
                "QPushButton:checked { background: rgba(63,185,80,0.28); border-color: rgba(63,185,80,0.7); }"
                "QPushButton:hover { background: rgba(88,166,255,0.28); }"
            )

        self.snap_combo.setStyleSheet(
            "QComboBox { background: rgba(22,30,48,0.9); color: #E6EDF3;"
            "border: 1px solid rgba(100,120,160,0.7); border-radius: 4px; padding: 2px 6px; font-size: 10px; }"
        )

        layout.addWidget(self.pin_btn)
        layout.addWidget(self.font_minus_btn)
        layout.addWidget(self.font_plus_btn)
        layout.addWidget(self.opacity_slider)
        layout.addWidget(self.snap_combo)
        layout.addWidget(self.stealth_btn)
        layout.addStretch()
        layout.addWidget(self.mode_label)
        row.setLayout(layout)
        return row

    def _refresh_mode_label(self):
        """Update the mirror mode indicator based on analysis/aggression state."""
        if self._aggression_mode:
            text = "Mode: High Aggression"
            style = "color: #F85149; font-size: 10px; font-weight: bold; background: transparent;"
        elif self._analysis_mode:
            text = "Mode: Analysis"
            style = "color: #58A6FF; font-size: 10px; font-weight: bold; background: transparent;"
        else:
            text = "Mode: Normal"
            style = "color: #8B949E; font-size: 10px; background: transparent;"

        pin_state = "Pinned" if getattr(self, "_pinned", True) else "Unpinned"
        text = f"{text} | {pin_state}"

        self.mode_label.setText(text)
        self.mode_label.setStyleSheet(style)
        self._apply_panel_chrome()
        self.update()
    
    def _create_header(self) -> QWidget:
        """Create header with status indicator."""
        header = QWidget()
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)
        
        # Status indicator (pulsing dot)
        self.status_dot = QLabel("[DOT]")
        self.status_dot.setStyleSheet("""
            color: #3FB950;
            font-size: 14px;
            background: transparent;
        """)
        self.status_dot.setFixedWidth(14)
        
        # Title
        title = QLabel("AI Assistant")
        title.setStyleSheet("""
            color: #E6EDF3;
            font-size: 17px;
            font-weight: bold;
            background: transparent;
        """)
        
        # Activity count
        self.count_label = QLabel("0 activities")
        self.count_label.setStyleSheet("""
            color: #8B949E;
            font-size: 11px;
            background: transparent;
        """)
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        
        header_layout.addWidget(self.status_dot)
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(self.count_label)
        
        header.setLayout(header_layout)
        return header

    def _toggle_pin(self):
        self._pinned = self.pin_btn.isChecked()
        self._apply_window_mode()

    def _apply_window_mode(self):
        geometry = self.geometry()
        if self._pinned:
            flags = (
                Qt.WindowType.FramelessWindowHint |
                Qt.WindowType.WindowStaysOnTopHint |
                Qt.WindowType.Tool |
                Qt.WindowType.WindowTransparentForInput
            )
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        else:
            flags = Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)

        self.setWindowFlags(flags)
        self.show()
        self.setGeometry(geometry)
        self._refresh_pin_state()

    def _refresh_pin_state(self, announce: bool = True):
        state_text = "Pinned" if self._pinned else "Unpinned"
        self.pin_btn.setToolTip(
            "Pinned: transparent, click-through, non-movable mirror"
            if self._pinned
            else "Unpinned: standard movable mirror window"
        )
        if hasattr(self, "help_label"):
            self.help_label.setText(
                "Mirror pinned [BULLET] Click-through overlay active"
                if self._pinned
                else "Mirror unpinned [BULLET] Drag the title bar or frame to reposition"
            )
        self._refresh_mode_label()
        if announce:
            self.add_activity("[PIN]" if self._pinned else "[WINDOW]", f"Mirror {state_text.lower()}")

    def _set_overlay_opacity(self, value: int):
        self._current_opacity = max(0.55, min(1.0, value / 100.0))
        self.setWindowOpacity(self._current_opacity)

    def _toggle_rpa_execution(self):
        self.set_rpa_execution_enabled(self.stealth_btn.isChecked(), announce=True)

    def set_rpa_execution_enabled(self, enabled: bool, announce: bool = False):
        """Sync the manual RPA Hand ON/OFF toggle with runtime execution state."""
        previous = self._rpa_enabled
        self._rpa_enabled = bool(enabled)
        self.stealth_btn.setChecked(self._rpa_enabled)
        self.stealth_btn.setToolTip(
            "RPA Hand execution enabled" if self._rpa_enabled else "RPA Hand execution paused"
        )
        if announce:
            icon = "[MOUSE]" if self._rpa_enabled else "[NO_ENTRY]"
            state = "ON" if self._rpa_enabled else "OFF"
            self.add_activity(icon, f"RPA Hand {state}")
        if previous != self._rpa_enabled:
            self.stealth_toggled.emit(self._rpa_enabled)

    def _change_font_scale(self, delta: float):
        self._font_scale = max(0.85, min(1.35, self._font_scale + delta))
        size_main = int(14 * self._font_scale)
        size_title = int(17 * self._font_scale)
        self.status_label.setStyleSheet(
            f"color: #E6EDF3; font-size: {size_main}px; padding: 8px; background: transparent;"
        )
        self.count_label.setStyleSheet(
            f"color: #8B949E; font-size: {max(10, int(11 * self._font_scale))}px; background: transparent;"
        )
        for lbl in self.findChildren(QLabel):
            if lbl.text() == "AI Assistant":
                lbl.setStyleSheet(
                    f"color: #E6EDF3; font-size: {size_title}px; font-weight: bold; background: transparent;"
                )

    def _snap_from_combo(self, index: int):
        if index == 0:
            return
        mapping = {
            1: "top-left",
            2: "top-right",
            3: "bottom-left",
            4: "bottom-right",
            5: "center",
        }
        self.snap_to(mapping.get(index, "top-right"))
        self.snap_combo.setCurrentIndex(0)

    def snap_to(self, position: str = "top-right", screen_index: int = -1):
        app = self.window().windowHandle().screen().virtualSiblingAt(self.pos()) if self.window().windowHandle() else None
        screens = app.virtualSiblings() if app else []
        if not screens:
            from PyQt6.QtWidgets import QApplication
            screens = QApplication.screens()
        if not screens:
            return

        target = screens[screen_index] if 0 <= screen_index < len(screens) else screens[-1]
        geo = target.availableGeometry()
        margin = 20
        x = geo.left() + margin
        y = geo.top() + margin
        if position == "top-right":
            x = geo.right() - self.width() - margin
        elif position == "bottom-left":
            y = geo.bottom() - self.height() - margin
        elif position == "bottom-right":
            x = geo.right() - self.width() - margin
            y = geo.bottom() - self.height() - margin
        elif position == "center":
            x = geo.left() + (geo.width() - self.width()) // 2
            y = geo.top() + (geo.height() - self.height()) // 2
        self.move(x, y)

    def set_analysis_mode(self, enabled: bool, context: str = ""):
        self._analysis_mode = enabled
        if enabled:
            self._refresh_mode_label()
            self.set_status("analyzing", context or "Chart focus mode")
            self.add_activity("[COMPASS]", f"Analysis Mode ON {('- ' + context) if context else ''}")
        else:
            self._refresh_mode_label()
            self.add_activity("[OK]", "Analysis Mode OFF")

    def set_aggression_mode(self, enabled: bool):
        """Turn the mirror red when FORCE ACTION is armed."""
        self._aggression_mode = bool(enabled)
        self._refresh_mode_label()

    def update_live_pnl(self, pnl: float, positions: int = 0):
        """Update real-time pnl strip on mirror."""
        color = "#3FB950" if pnl >= 0 else "#F85149"
        self.live_pnl_label.setText(f"Live PnL: ${pnl:.2f} | Positions: {positions}")
        self.live_pnl_label.setStyleSheet(
            f"color: {color}; font-size: 12px; font-weight: bold; background: transparent;"
        )

    def set_daily_bullets(self, used: int, limit: int = 30):
        """Update the Lion HUD trade-cap counter."""
        self.daily_bullets_label.setText(f"Daily Bullets: {int(used)}/{int(limit)}")
        color = "#F85149" if used >= limit else "#F2CC60" if used >= max(1, int(limit * 0.7)) else "#58A6FF"
        self.daily_bullets_label.setStyleSheet(
            f"color: {color}; font-size: 11px; font-weight: bold; background: transparent;"
        )

    def update_lockout_timer(self, remaining_seconds: int, ticker: str = ""):
        """Refresh the 5-minute cooldown HUD countdown."""
        remaining = max(0, int(remaining_seconds))
        minutes, seconds = divmod(remaining, 60)
        suffix = f" [{ticker}]" if ticker else ""
        self.lockout_timer_label.setText(f"Lockout Timer: {minutes:02d}:{seconds:02d}{suffix}")
        self.lockout_timer_label.setStyleSheet(
            "color: #FF7B72; font-size: 11px; font-weight: bold; background: transparent;"
        )

    def clear_lockout_timer(self):
        """Return the lockout HUD to its ready state."""
        self.lockout_timer_label.setText("Lockout Timer: READY")
        self.lockout_timer_label.setStyleSheet(
            "color: #F2CC60; font-size: 11px; font-weight: bold; background: transparent;"
        )

    def update_live_ledger(
        self,
        active_trades: int = 0,
        unrealized_pnl: float = 0.0,
        daily_success_rate: float = 0.0,
    ):
        """Refresh the Live Ledger strip (Active Trades | Unrealized PnL | Daily Success Rate)."""
        self._ledger_active_val.setText(str(active_trades))
        self._ledger_active_val.setStyleSheet(
            "color: #58A6FF; font-size: 12px; font-weight: bold; background: transparent;"
        )

        pnl_color = "#3FB950" if unrealized_pnl >= 0 else "#F85149"
        pnl_sign = "+" if unrealized_pnl >= 0 else ""
        self._ledger_pnl_val.setText(f"{pnl_sign}${unrealized_pnl:.2f}")
        self._ledger_pnl_val.setStyleSheet(
            f"color: {pnl_color}; font-size: 12px; font-weight: bold; background: transparent;"
        )

        rate_color = "#3FB950" if daily_success_rate >= 50 else "#D29922"
        self._ledger_rate_val.setText(f"{daily_success_rate:.0f}%")
        self._ledger_rate_val.setStyleSheet(
            f"color: {rate_color}; font-size: 12px; font-weight: bold; background: transparent;"
        )

    def trigger_signal_alert(self, duration_ms: int = 3000):
        """Pulse the mirror border neon green after a fresh signal broadcast."""
        self._signal_alert_active = True
        self._signal_alert_flash_state = True
        self._signal_alert_deadline = time.monotonic() + max(0.5, duration_ms / 1000.0)
        if not self.signal_alert_timer.isActive():
            self.signal_alert_timer.start(150)
        self._apply_panel_chrome()
        self.update()

    def _pulse_signal_alert_border(self):
        if not self._signal_alert_active:
            self.signal_alert_timer.stop()
            return
        if time.monotonic() >= self._signal_alert_deadline:
            self._signal_alert_active = False
            self._signal_alert_flash_state = False
            self.signal_alert_timer.stop()
        else:
            self._signal_alert_flash_state = not self._signal_alert_flash_state
        self._apply_panel_chrome()
        self.update()
    
    def start_status_timer(self):
        """Start timer for status dot pulse animation."""
        self.pulse_timer = QTimer()
        self.pulse_timer.timeout.connect(self._pulse_status_dot)
        self.pulse_timer.start(1500)  # Pulse every 1.5s
    
    def _pulse_status_dot(self):
        """Animate status dot opacity."""
        if self._aggression_mode:
            current_style = self.status_dot.styleSheet()
            next_color = "#FF7B72" if "F85149" in current_style else "#F85149"
            self.status_dot.setStyleSheet(
                f"""
                color: {next_color};
                font-size: 14px;
                background: transparent;
            """
            )
            return

        current_style = self.status_dot.styleSheet()
        if "3FB950" in current_style:  # Green
            self.status_dot.setStyleSheet("""
                color: #2EA043;
                font-size: 14px;
                background: transparent;
            """)
        elif "2EA043" in current_style:
            self.status_dot.setStyleSheet("""
                color: #3FB950;
                font-size: 14px;
                background: transparent;
            """)
        elif "D29922" in current_style:  # Yellow
            self.status_dot.setStyleSheet("""
                color: #F85149;
                font-size: 14px;
                background: transparent;
            """)
        elif "F85149" in current_style:  # Red
            self.status_dot.setStyleSheet("""
                color: #D29922;
                font-size: 14px;
                background: transparent;
            """)
    
    def set_status(self, status: str, message: str = ""):
        """
        Update narrator status with typing animation.
        
        Status types:
        - idle: Waiting for signals
        - scanning: Market scanning in progress
        - analyzing: Running swarm debate
        - executing: Executing trade
        - monitoring: Watching positions
        - error: Something went wrong
        - success: Trade completed
        """
        status_messages = {
            "idle": "[SLEEP] Standing by... Waiting for market signals",
            "scanning": f"[SAT] Scanning markets... Monitoring {message or '10 tickers'} for opportunities",
            "analyzing": "[BRAIN] Analyzing signal... Running multi-agent swarm debate",
            "thinking": f"[BRAIN] AI Reasoning... {message or 'Consulting OpenRouter'}",
            "fallback": f"[YELLOW] [FALLBACK MODE] {message or 'Local Predator intelligence active'}",
            "verdict": f"[BOLT] Brain verdict locked... {message or 'Awaiting execution'}",
            "executing": f"[BOLT] Executing trade... {message or 'Processing order'}",
            "monitoring": f"[EYE] Monitoring positions... Watching {message or 'active trades'}",
            "error": f"[FAIL] Error detected... {message or 'Checking system status'}",
            "success": f"[OK] Trade executed successfully! {message or 'Position opened'}",
            "rejected": "[BLOCK] Trade rejected by user. Position not opened.",
        }
        
        msg = status_messages.get(status, status_messages["idle"])
        self.status_label.start_typing(msg)
        
        # Update status dot color
        status_colors = {
            "idle": "#8B949E",
            "scanning": "#58A6FF",
            "analyzing": "#D29922",
            "thinking": "#D29922",
            "fallback": "#F0E68C",
            "verdict": "#58A6FF",
            "executing": "#F85149",
            "monitoring": "#3FB950",
            "error": "#F85149",
            "success": "#3FB950",
            "rejected": "#F85149",
        }
        
        text_color = "#F0E68C" if status == "fallback" else "#E6EDF3"
        self.status_label.setStyleSheet(
            f"color: {text_color}; font-size: 14px; padding: 8px; background: transparent;"
        )
        color = status_colors.get(status, "#8B949E")
        self.status_dot.setStyleSheet(f"""
            color: {color};
            font-size: 14px;
            background: transparent;
        """)
    
    def add_activity(self, icon: str, message: str, timestamp: str = ""):
        """Add activity to feed."""
        if not timestamp:
            timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Create activity item
        item = ActivityItem(icon, message, timestamp)
        self.activity_layout.addWidget(item)
        
        # Update count
        self.activity_count += 1
        self.count_label.setText(f"{self.activity_count} activities")
        
        # Auto-scroll to bottom
        QTimer.singleShot(100, lambda: self.scroll_area.verticalScrollBar().setValue(
            self.scroll_area.verticalScrollBar().maximum()
        ))
        
        logger.debug(f"Activity added: {icon} {message}")
    
    def notify_scan_start(self, ticker_count: int = 10):
        """Notify that market scanning has started."""
        self.set_status("scanning", f"{ticker_count} tickers")
        self.add_activity("[SAT]", f"Started scanning {ticker_count} markets", datetime.now().strftime("%H:%M:%S"))

    def set_watchlist(self, tickers: list[str]):
        """Replace live watchlist badge rows in the mirror."""
        for label in self.ticker_status_labels.values():
            self.watchlist_status_layout.removeWidget(label)
            label.deleteLater()
        self.ticker_status_labels = {}

        for ticker in tickers:
            label = QLabel(f"[WHITE] {ticker}")
            label.setStyleSheet("color: #E6EDF3; font-size: 11px; background: transparent;")
            self.watchlist_status_layout.addWidget(label)
            self.ticker_status_labels[ticker] = label

    def update_ticker_status(self, ticker: str, status: str):
        """Update a per-ticker badge in the mirror."""
        if ticker not in self.ticker_status_labels:
            label = QLabel(f"[WHITE] {ticker}")
            label.setStyleSheet("color: #E6EDF3; font-size: 11px; background: transparent;")
            self.watchlist_status_layout.addWidget(label)
            self.ticker_status_labels[ticker] = label

        icon_map = {
            "scanning": ("[GREEN]", "Scanning"),
            "analyzing_liquidity": ("[YELLOW]", "Analyzing Liquidity"),
            "trade_rejected": ("[RED]", "Trade Rejected"),
        }
        if status.startswith("brain_reasoning:"):
            icon, text = ("[BRAIN]", f"AI Reasoning {status.split(':', 1)[1]}")
        elif status.startswith("brain_fallback:"):
            brain = status.split(':', 1)[1].strip().upper() or "LOCAL"
            icon, text = ("[YELLOW]", f"[FALLBACK MODE] {brain}")
        elif status.startswith("brain_verdict:"):
            verdict = status.split(':', 1)[1].strip().upper()
            verdict_map = {
                "[SIGNAL] BUY": ("[GREEN_SQ]", "Brain BUY"),
                "[SIGNAL] SELL": ("[RED_SQ]", "Brain SELL"),
                "[SIGNAL] WAIT": ("[PAUSE]", "Brain WAIT"),
            }
            icon, text = verdict_map.get(verdict, ("[WHITE]", verdict or "Brain"))
        else:
            icon, text = icon_map.get(status, ("[WHITE]", status))
        self.ticker_status_labels[ticker].setText(f"{icon} {ticker} | {text}")

    def notify_brain_thinking(self, ticker: str, proposed_action: str = ""):
        action_label = proposed_action.upper() if proposed_action else "SCAN"
        self.set_status("thinking", f"{ticker} -> {action_label}")
        self.add_activity("[BRAIN]", f"AI Reasoning... {ticker} -> {action_label}")

    def notify_fallback_mode(self, brain_used: str = "OLLAMA_PREDATOR"):
        brain_label = str(brain_used or "OLLAMA_PREDATOR").replace("_", " ").title()
        self.set_status("fallback", f"{brain_label} engaged")
        self.add_activity("[YELLOW]", f"[FALLBACK MODE] {brain_label}")

    def flash_brain_verdict(
        self,
        ticker: str,
        verdict: str,
        reasoning: str = "",
        hold_ms: int = 3000,
        fallback_mode: bool = False,
        brain_used: str = "OPENROUTER",
    ):
        clean_verdict = str(verdict or "[SIGNAL] WAIT").strip().upper()
        action = clean_verdict.replace("[SIGNAL]", "").strip() or "WAIT"
        reasoning_text = (reasoning or "OpenRouter approved the trade.").strip()
        if fallback_mode:
            self.set_status("fallback", f"{action} {ticker} via {str(brain_used or 'OLLAMA_PREDATOR').replace('_', ' ')}")
            self.add_activity("[YELLOW]", f"[FALLBACK MODE] {brain_used}")
        else:
            self.set_status("verdict", f"{action} {ticker}")
        self.add_activity("[BOLT]", f"Brain verdict: {action} {ticker}")
        if reasoning_text:
            self.add_activity("[EMOJI]", reasoning_text[:180])
        QApplication.processEvents()
        loop = QEventLoop()
        QTimer.singleShot(max(1, int(hold_ms)), loop.quit)
        loop.exec()

    def update_trade_levels(
        self,
        ticker: str,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        liquidity_label: str = "",
    ):
        """Show SL/TP/liquidity in large type for cross-room teacher mode reading."""
        self.levels_ticker.setText(str(ticker or "NO ACTIVE LEVELS").upper())
        self.levels_sl_label.setText(
            f"SL: {float(stop_loss):.4f}" if stop_loss not in (None, 0, 0.0) else "SL: --"
        )
        self.levels_tp_label.setText(
            f"TP: {float(take_profit):.4f}" if take_profit not in (None, 0, 0.0) else "TP: --"
        )
        self.levels_liquidity_label.setText(f"LIQ: {liquidity_label or '--'}")

    def clear_trade_levels(self, announce: bool = False):
        """Reset large trade-level broadcast when nothing actionable is active."""
        self.update_trade_levels("NO ACTIVE LEVELS", None, None, "")
        if announce:
            self.add_activity("[BROOM]", "Teacher Broadcast reset")

    def set_command_posture(self, posture: str, detail: str = ""):
        """Immediately reflect high-level command posture changes from the dashboard."""
        normalized = str(posture or "").strip().upper()
        if normalized == "PROTECT ACCOUNT":
            self.set_aggression_mode(False)
            self.status_label.set_text_instant(
                f"[SHIELD] Protect Account active... {detail or 'Tightening posture and respecting drawdown.'}"
            )
            self.status_dot.setStyleSheet("color: #58A6FF; font-size: 14px; background: transparent;")
            self.add_activity("[SHIELD]", "Mirror synced: PROTECT ACCOUNT")
            return
        if normalized == "BE AGGRESSIVE":
            self.set_aggression_mode(True)
            self.status_label.set_text_instant(
                f"[FIRE] Aggression active... {detail or 'Lion strike posture armed.'}"
            )
            self.status_dot.setStyleSheet("color: #F85149; font-size: 14px; background: transparent;")
            self.add_activity("[FIRE]", "Mirror synced: BE AGGRESSIVE")
            return
        self.status_label.set_text_instant(detail or normalized or "Standing by")
    
    def notify_signal_detected(self, ticker: str, signal_type: str, confidence: float):
        """Notify that a trading signal was detected."""
        icon = "[FIRE]" if confidence > 0.8 else "[WARN]"
        self.trigger_signal_alert(duration_ms=3000)
        self.set_status("analyzing", f"{signal_type} on {ticker}")
        self.add_activity(
            icon, 
            f"{signal_type} detected on {ticker} ({confidence:.0%} confidence)",
            datetime.now().strftime("%H:%M:%S")
        )
    
    def notify_trade_approved(self, ticker: str, action: str, amount: float):
        """Notify that user approved a trade."""
        action_upper = str(action).upper()
        is_sell = action_upper == "SELL"
        icon = "[RED_SQ]" if is_sell else "[GREEN_SQ]"
        approval_text = "APPROVED: SELL" if is_sell else "APPROVED: BUY"
        self.set_status("executing", f"{approval_text} {ticker} ${amount:.2f}")
        self.add_activity(
            icon,
            f"{approval_text} {ticker} with ${amount:.2f}",
            datetime.now().strftime("%H:%M:%S")
        )
    
    def notify_trade_rejected(self, ticker: str):
        """Notify that user rejected a trade."""
        self.set_status("rejected")
        self.add_activity(
            "[BLOCK]",
            f"REJECTED: User declined trade on {ticker}",
            datetime.now().strftime("%H:%M:%S")
        )
    
    def notify_trade_executed(self, ticker: str, action: str, entry_price: float):
        """Notify that trade was successfully executed."""
        self.set_status("success", f"{action} {ticker} @ ${entry_price:.2f}")
        self.add_activity(
            "[MONEY]",
            f"EXECUTED: {action} {ticker} @ ${entry_price:.2f}",
            datetime.now().strftime("%H:%M:%S")
        )
    
    def notify_position_update(self, position_count: int, daily_pnl: float):
        """Notify about position monitoring update."""
        self.set_status("monitoring", f"{position_count} positions")
        pnl_str = f"+${daily_pnl:.2f}" if daily_pnl >= 0 else f"-${abs(daily_pnl):.2f}"
        self.add_activity(
            "[CHART]",
            f"Monitoring {position_count} positions | Daily P&L: {pnl_str}",
            datetime.now().strftime("%H:%M:%S")
        )
    
    def notify_error(self, error_message: str):
        """Notify about an error."""
        self.set_status("error", error_message)
        self.add_activity(
            "[FAIL]",
            f"ERROR: {error_message}",
            datetime.now().strftime("%H:%M:%S")
        )
    
    def notify_system_ready(self):
        """Notify that system is ready."""
        self.set_status("idle")
        self.add_activity(
            "[SUCCESS]",
            "System initialized - All systems connected",
            datetime.now().strftime("%H:%M:%S")
        )
    
    def clear_activities(self):
        """Clear all activities from feed."""
        while self.activity_layout.count():
            child = self.activity_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self.activity_count = 0
        self.count_label.setText("0 activities")
