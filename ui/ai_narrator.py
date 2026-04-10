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
    QScrollArea, QFrame, QGraphicsDropShadowEffect
)
from PyQt6.QtCore import (
    Qt, QTimer, pyqtSignal, QPropertyAnimation, 
    QEasingCurve, QVariantAnimation
)
from PyQt6.QtGui import (
    QFont, QColor, QPainter, QBrush, QPen,
    QLinearGradient, QFontMetrics, QIcon
)
from datetime import datetime
import logging

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
            font-size: 13px;
            padding: 8px;
            background: transparent;
        """)
    
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
            font-size: 12px;
            background: transparent;
        """)
        
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
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        
        # Shadow effect
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(30)
        shadow.setXOffset(0)
        shadow.setYOffset(8)
        shadow.setColor(QColor(0, 0, 0, 100))
        self.setGraphicsEffect(shadow)
    
    def paintEvent(self, event):
        """Draw glassmorphic background."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Create glass gradient
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, QColor(20, 25, 40, 200))      # Top - darker
        gradient.setColorAt(0.5, QColor(15, 20, 35, 190))    # Middle
        gradient.setColorAt(1, QColor(10, 15, 30, 200))      # Bottom - darker
        
        # Draw rounded rectangle
        rect = self.rect().adjusted(2, 2, -2, -2)
        painter.setBrush(QBrush(gradient))
        painter.setPen(QPen(QColor(100, 120, 160, 80), 1.5))
        painter.drawRoundedRect(rect, 16, 16)
        
        # Add subtle top highlight
        highlight = QLinearGradient(0, 0, 0, 40)
        highlight.setColorAt(0, QColor(255, 255, 255, 30))
        highlight.setColorAt(1, QColor(255, 255, 255, 0))
        painter.setBrush(QBrush(highlight))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect.adjusted(2, 2, -2, -30), 14, 14)


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
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.activity_count = 0
        self.init_ui()
        self.start_status_timer()
        
        # Connect signals
        self.activity_added.connect(self.add_activity)
        
        logger.info("AI Narrator Overlay initialized")
    
    def init_ui(self):
        """Initialize the narrator UI."""
        self.setFixedSize(380, 520)
        
        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)
        
        # Header with status indicator
        header = self._create_header()
        main_layout.addWidget(header)
        
        # Current status (typing label)
        self.status_label = TypingLabel()
        self.status_label.setWordWrap(True)
        main_layout.addWidget(self.status_label)
        
        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(100, 120, 160, 0), stop:0.5 rgba(100, 120, 160, 80), stop:1 rgba(100, 120, 160, 0));")
        separator.setFixedHeight(1)
        main_layout.addWidget(separator)
        
        # Activity feed title
        feed_title = QLabel("📋 Activity Feed")
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
        
        self.setLayout(main_layout)
        
        # Set initial status
        self.set_status("idle")
    
    def _create_header(self) -> QWidget:
        """Create header with status indicator."""
        header = QWidget()
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)
        
        # Status indicator (pulsing dot)
        self.status_dot = QLabel("●")
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
            font-size: 16px;
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
    
    def start_status_timer(self):
        """Start timer for status dot pulse animation."""
        self.pulse_timer = QTimer()
        self.pulse_timer.timeout.connect(self._pulse_status_dot)
        self.pulse_timer.start(1500)  # Pulse every 1.5s
    
    def _pulse_status_dot(self):
        """Animate status dot opacity."""
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
            "idle": "💤 Standing by... Waiting for market signals",
            "scanning": f"📡 Scanning markets... Monitoring {message or '10 tickers'} for opportunities",
            "analyzing": "🧠 Analyzing signal... Running multi-agent swarm debate",
            "executing": f"⚡ Executing trade... {message or 'Processing order'}",
            "monitoring": f"👁️ Monitoring positions... Watching {message or 'active trades'}",
            "error": f"❌ Error detected... {message or 'Checking system status'}",
            "success": f"✅ Trade executed successfully! {message or 'Position opened'}",
            "rejected": "🚫 Trade rejected by user. Position not opened.",
        }
        
        msg = status_messages.get(status, status_messages["idle"])
        self.status_label.start_typing(msg)
        
        # Update status dot color
        status_colors = {
            "idle": "#8B949E",
            "scanning": "#58A6FF",
            "analyzing": "#D29922",
            "executing": "#F85149",
            "monitoring": "#3FB950",
            "error": "#F85149",
            "success": "#3FB950",
            "rejected": "#F85149",
        }
        
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
        self.add_activity("📡", f"Started scanning {ticker_count} markets", datetime.now().strftime("%H:%M:%S"))
    
    def notify_signal_detected(self, ticker: str, signal_type: str, confidence: float):
        """Notify that a trading signal was detected."""
        icon = "🔥" if confidence > 0.8 else "⚠️"
        self.set_status("analyzing", f"{signal_type} on {ticker}")
        self.add_activity(
            icon, 
            f"{signal_type} detected on {ticker} ({confidence:.0%} confidence)",
            datetime.now().strftime("%H:%M:%S")
        )
    
    def notify_trade_approved(self, ticker: str, action: str, amount: float):
        """Notify that user approved a trade."""
        self.set_status("executing", f"{action} {ticker} ${amount:.2f}")
        self.add_activity(
            "✅",
            f"APPROVED: {action} {ticker} with ${amount:.2f}",
            datetime.now().strftime("%H:%M:%S")
        )
    
    def notify_trade_rejected(self, ticker: str):
        """Notify that user rejected a trade."""
        self.set_status("rejected")
        self.add_activity(
            "🚫",
            f"REJECTED: User declined trade on {ticker}",
            datetime.now().strftime("%H:%M:%S")
        )
    
    def notify_trade_executed(self, ticker: str, action: str, entry_price: float):
        """Notify that trade was successfully executed."""
        self.set_status("success", f"{action} {ticker} @ ${entry_price:.2f}")
        self.add_activity(
            "💰",
            f"EXECUTED: {action} {ticker} @ ${entry_price:.2f}",
            datetime.now().strftime("%H:%M:%S")
        )
    
    def notify_position_update(self, position_count: int, daily_pnl: float):
        """Notify about position monitoring update."""
        self.set_status("monitoring", f"{position_count} positions")
        pnl_str = f"+${daily_pnl:.2f}" if daily_pnl >= 0 else f"-${abs(daily_pnl):.2f}"
        self.add_activity(
            "📊",
            f"Monitoring {position_count} positions | Daily P&L: {pnl_str}",
            datetime.now().strftime("%H:%M:%S")
        )
    
    def notify_error(self, error_message: str):
        """Notify about an error."""
        self.set_status("error", error_message)
        self.add_activity(
            "❌",
            f"ERROR: {error_message}",
            datetime.now().strftime("%H:%M:%S")
        )
    
    def notify_system_ready(self):
        """Notify that system is ready."""
        self.set_status("idle")
        self.add_activity(
            "🚀",
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
