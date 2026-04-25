"""
VcanTrade AI - Professional Trading Dashboard

Complete command center with:
- Watchlist management (add/remove tickers to monitor)
- Investment amount & risk settings
- Auto-execution with Take Profit / Stop Loss
- Live position monitoring with real-time P&L
- Prop firm account tracking (Top Step Funding)
- Trade history & performance metrics
- System status indicators
"""

import logging
from datetime import datetime
from typing import Dict, List

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QGroupBox,
    QHeaderView,
)

import config
from core.models import SignalAction
from core.settings import settings_manager

logger = logging.getLogger(__name__)

# Color Palette
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
YELLOW = "#F0E68C"
BRIDGE_RED = "#FF4D4F"

# HIGH-CONTRAST TRADING COLORS (Neon for visibility across room)
NEON_GREEN = "#00FF41"  # Bright neon green for BUY signals
BRIGHT_RED = "#FF003C"  # Bright red for SELL signals


class StatusDot(QWidget):
    """Small colored status indicator dot."""
    def __init__(self, active=False, parent=None):
        super().__init__(parent)
        self._active = active
        self.setFixedSize(10, 10)
        self._update_style()

    def set_active(self, active):
        self._active = active
        self._update_style()

    def _update_style(self):
        color = GREEN if self._active else RED
        self.setStyleSheet(f"""
            background-color: {color};
            border-radius: 5px;
            border: 1px solid {BORDER};
        """)


class CommandCenter(QWidget):
    """Main Trading Dashboard - Professional Prop Firm Command Center"""

    # Signals
    mode_changed = pyqtSignal(str)
    kill_switch_triggered = pyqtSignal()
    calibration_requested = pyqtSignal()
    watchlist_updated = pyqtSignal(list)
    settings_changed = pyqtSignal(dict)
    ticker_changed = pyqtSignal(str)
    test_browser_requested = pyqtSignal()
    force_test_trade_requested = pyqtSignal()
    user_command_sent = pyqtSignal(str)  # NEW: Co-Pilot Command Bridge

    def __init__(self):
        super().__init__()
        self._mode = "TEACHER"
        self._killed = False
        self.positions = {}  # Live positions tracking
        saved_watchlist = settings_manager.get("session_watchlist", [])
        if not isinstance(saved_watchlist, list):
            saved_watchlist = []
        self.watchlist = settings_manager.normalize_watchlist(saved_watchlist)
        if not self.watchlist:
            self.watchlist = config.CLOUD_TICKERS.copy()
        self.watchlist_slots: List[QLineEdit] = []
        self.prop_firm_mode_enabled = bool(settings_manager.get("prop_firm_mode", False))
        self._manual_max_loss_value = float(settings_manager.get("max_daily_loss", 500.0) or 500.0)
        self.auto_risk_enabled = bool(settings_manager.get("auto_risk_enabled", True))
        self.watchlist_row_state: Dict[str, Dict[str, object]] = {}
        self._confidence_glow_on = False
        self.watchlist_sync_timer = QTimer(self)
        self.watchlist_sync_timer.setSingleShot(True)
        self.watchlist_sync_timer.timeout.connect(self._sync_watchlist_from_inputs)
        self.confidence_glow_timer = QTimer(self)
        self.confidence_glow_timer.timeout.connect(self._pulse_confidence_rows)
        self.confidence_glow_timer.start(650)

        self._setup_window()
        self._build_ui()
        self._update_analysis_option_visibility()
        logger.info("Command Center initialized - Professional Trading Mode")

    def _setup_window(self):
        self.setWindowTitle("VcaniTrade AI - Prop Trading Command Center")
        # Start with always-on-top but user can toggle it off
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        self._always_on_top = True  # Track current state

        # Get screen size and use 85% of height
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen().geometry()
        width = 900
        height = int(screen.height() * 0.85)

        self.setMinimumSize(800, 700)
        self.resize(width, height)
        self.move(screen.width() - width - 20, 20)  # Right side of screen
        self.setStyleSheet(f"background-color: {BG_DARK};")
        self.setWindowOpacity(0.92)  # Glass effect - 92% opacity

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Main scroll area for all controls
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"background-color: {BG_DARK}; border: none;")

        content = QWidget()
        content.setStyleSheet(f"background-color: {BG_DARK};")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Build all sections
        layout.addWidget(self._build_title_bar())
        layout.addWidget(self._build_account_panel())
        layout.addWidget(self._build_prop_firm_panel())  # NEW: Prop Firm Compliance
        layout.addWidget(self._build_control_panel())
        layout.addWidget(self._build_watchlist_panel())
        layout.addWidget(self._build_positions_panel())
        layout.addWidget(self._build_trade_log_panel())
        layout.addWidget(self._build_copilot_chat_panel())  # NEW: Co-Pilot Command Bridge
        layout.addWidget(self._build_institutional_governor_panel())  # NEW: Stage 3 Risk Governor
        layout.addWidget(self._build_meta_cognition_panel())  # NEW: Stage 4 Meta-Cognition
        layout.addWidget(self._build_kill_switch())

        scroll.setWidget(content)
        root.addWidget(scroll)

    # =================== TITLE BAR ===================
    def _build_title_bar(self) -> QWidget:
        container = QFrame()
        container.setStyleSheet(
            f"background-color: {BG_PANEL}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px;"
        )
        layout = QHBoxLayout(container)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        title = QLabel("[SUCCESS] VcaniTrade AI")
        title.setStyleSheet(f"color: {CYAN}; font-size: 16px; font-weight: bold; font-family: 'Segoe UI';")
        layout.addWidget(title)

        # Transparency slider
        trans_label = QLabel("[MAGNIFY]")
        trans_label.setStyleSheet(f"color: {GRAY}; font-size: 12px;")
        layout.addWidget(trans_label)

        self.transparency_slider = QSpinBox()
        self.transparency_slider.setRange(50, 100)
        self.transparency_slider.setValue(92)
        self.transparency_slider.setSuffix("%")
        self.transparency_slider.setFixedWidth(60)
        self.transparency_slider.setStyleSheet(f"""
            QSpinBox {{ background: {BG_INPUT}; color: {WHITE}; border: 1px solid {BORDER};
                       border-radius: 4px; padding: 4px; font-size: 11px; font-family: 'Consolas'; }}
        """)
        self.transparency_slider.valueChanged.connect(self._update_transparency)
        layout.addWidget(self.transparency_slider)

        # Pin/Unpin button (toggle always on top)
        self.pin_btn = QPushButton("[PIN] PIN")
        self.pin_btn.setFixedWidth(65)
        self.pin_btn.setStyleSheet(f"""
            QPushButton {{ background: {CYAN}; color: {BG_DARK}; border: none; border-radius: 4px;
                         font-size: 11px; font-weight: bold; font-family: 'Consolas'; padding: 4px 8px; }}
            QPushButton:hover {{ background: #00b8e6; }}
        """)
        self.pin_btn.clicked.connect(self._toggle_always_on_top)
        layout.addWidget(self.pin_btn)

        layout.addStretch()

        self.mode_badge = QLabel("TEACHER MODE")
        self.mode_badge.setStyleSheet(
            f"color: {CYAN}; background: {BG_INPUT}; padding: 6px 14px; border-radius: 6px; "
            f"font-size: 12px; font-weight: bold; font-family: 'Consolas';"
        )
        layout.addWidget(self.mode_badge)

        self.bridge_status_widget = QFrame()
        self.bridge_status_widget.setStyleSheet(
            f"background: {BG_INPUT}; border: 1px solid {BORDER}; border-radius: 6px;"
        )
        bridge_layout = QHBoxLayout(self.bridge_status_widget)
        bridge_layout.setContentsMargins(10, 6, 10, 6)
        bridge_layout.setSpacing(8)

        self.bridge_status_dot = QLabel()
        self.bridge_status_dot.setFixedSize(10, 10)
        bridge_layout.addWidget(self.bridge_status_dot)

        self.bridge_status_label = QLabel()
        self.bridge_status_label.setStyleSheet(
            f"font-size: 11px; font-weight: bold; font-family: 'Consolas'; border: none;"
        )
        bridge_layout.addWidget(self.bridge_status_label)

        layout.addWidget(self.bridge_status_widget)
        self.set_bridge_status("disconnected")

        return container

    def _toggle_always_on_top(self):
        """Toggle window between always-on-top and normal."""
        self._always_on_top = not self._always_on_top
        
        if self._always_on_top:
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
            self.pin_btn.setText("[PIN] PIN")
            self.pin_btn.setStyleSheet(f"""
                QPushButton {{ background: {CYAN}; color: {BG_DARK}; border: none; border-radius: 4px;
                             font-size: 11px; font-weight: bold; font-family: 'Consolas'; padding: 4px 8px; }}
            """)
            self.log("[PIN] Dashboard pinned to front")
        else:
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint)
            self.pin_btn.setText("[PIN] UNPIN")
            self.pin_btn.setStyleSheet(f"""
                QPushButton {{ background: {ORANGE}; color: {BG_DARK}; border: none; border-radius: 4px;
                             font-size: 11px; font-weight: bold; font-family: 'Consolas'; padding: 4px 8px; }}
            """)
            self.log("[PIN] Dashboard unpinned - can now go behind other windows")
        
        self.show()  # Re-show to apply flag changes

    def _update_transparency(self, value: int):
        """Update window opacity based on slider value."""
        opacity = value / 100.0
        self.setWindowOpacity(opacity)
        if value < 80:
            self.log(f"[MAGNIFY] Transparency: {100 - value}% (you can see through the dashboard)")

    def set_bridge_status(self, status: str):
        """Update external brain bridge indicator in the header."""
        normalized = str(status or "disconnected").strip().lower()

        if normalized == "online":
            dot_color = NEON_GREEN
            border_color = NEON_GREEN
            text_color = NEON_GREEN
            text = "[GREEN] BRIDGE ONLINE"
            shadow = "0 0 12px rgba(0,255,65,0.35)"
        elif normalized in {"lost", "heartbeat_lost", "warning"}:
            dot_color = BRIDGE_RED
            border_color = BRIDGE_RED
            text_color = BRIDGE_RED
            text = "[RED] BRIDGE HEARTBEAT LOST"
            shadow = "0 0 10px rgba(255,77,79,0.20)"
        else:
            dot_color = GRAY
            border_color = BORDER
            text_color = GRAY
            text = "BRIDGE DISCONNECTED"
            shadow = "none"

        self.bridge_status_dot.setStyleSheet(
            f"background: {dot_color}; border-radius: 5px; border: 1px solid {dot_color};"
        )
        self.bridge_status_label.setText(text)
        self.bridge_status_label.setStyleSheet(
            f"color: {text_color}; font-size: 11px; font-weight: bold; font-family: 'Consolas'; border: none;"
        )
        self.bridge_status_widget.setStyleSheet(
            f"background: {BG_INPUT}; border: 1px solid {border_color}; border-radius: 6px;"
            f" box-shadow: {shadow};"
        )

    # =================== ACCOUNT PANEL ===================
    def _build_account_panel(self) -> QWidget:
        """Account Balance, Equity, Daily P&L, Drawdown Tracking"""
        panel = QGroupBox("[MONEY] ACCOUNT (Top Step Funding)")
        panel.setStyleSheet(f"""
            QGroupBox {{ color: {CYAN}; font-size: 14px; font-weight: bold; font-family: 'Segoe UI';
                        border: 1px solid {BORDER}; border-radius: 8px; margin-top: 8px; padding-top: 12px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; }}
        """)
        
        layout = QVBoxLayout(panel)
        layout.setSpacing(10)

        # Top row: Balance, Equity, Daily P&L
        top_row = QHBoxLayout()
        top_row.setSpacing(15)

        # Balance
        bal_layout = QVBoxLayout()
        bal_label = QLabel("Balance")
        bal_label.setStyleSheet(f"color: {GRAY}; font-size: 11px; font-family: 'Consolas';")
        bal_layout.addWidget(bal_label)
        
        self.balance_label = QLabel("$10,000.00")
        self.balance_label.setStyleSheet(f"color: {WHITE}; font-size: 22px; font-weight: bold; font-family: 'Consolas';")
        bal_layout.addWidget(self.balance_label)
        top_row.addLayout(bal_layout)

        # Equity
        eq_layout = QVBoxLayout()
        eq_label = QLabel("Equity")
        eq_label.setStyleSheet(f"color: {GRAY}; font-size: 11px; font-family: 'Consolas';")
        eq_layout.addWidget(eq_label)
        
        self.equity_label = QLabel("$10,000.00")
        self.equity_label.setStyleSheet(f"color: {WHITE}; font-size: 22px; font-weight: bold; font-family: 'Consolas';")
        eq_layout.addWidget(self.equity_label)
        top_row.addLayout(eq_layout)

        # Daily P&L
        pnl_layout = QVBoxLayout()
        pnl_label = QLabel("Daily P&L")
        pnl_label.setStyleSheet(f"color: {GRAY}; font-size: 11px; font-family: 'Consolas';")
        pnl_layout.addWidget(pnl_label)
        
        self.daily_pnl_label = QLabel("$0.00")
        self.daily_pnl_label.setStyleSheet(f"color: {GREEN}; font-size: 22px; font-weight: bold; font-family: 'Consolas';")
        pnl_layout.addWidget(self.daily_pnl_label)
        top_row.addLayout(pnl_layout)

        layout.addLayout(top_row)

        # Second row: Total P&L, Drawdown, Trades Today
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(15)

        # Total P&L
        total_pnl_layout = QVBoxLayout()
        tp_label = QLabel("Total P&L")
        tp_label.setStyleSheet(f"color: {GRAY}; font-size: 11px; font-family: 'Consolas';")
        total_pnl_layout.addWidget(tp_label)
        
        self.total_pnl_label = QLabel("$0.00")
        self.total_pnl_label.setStyleSheet(f"color: {GREEN}; font-size: 16px; font-weight: bold; font-family: 'Consolas';")
        total_pnl_layout.addWidget(self.total_pnl_label)
        bottom_row.addLayout(total_pnl_layout)

        # Max Drawdown
        dd_layout = QVBoxLayout()
        dd_label = QLabel("Max Drawdown")
        dd_label.setStyleSheet(f"color: {GRAY}; font-size: 11px; font-family: 'Consolas';")
        dd_layout.addWidget(dd_label)
        
        self.drawdown_label = QLabel("$0.00 (0.0%)")
        self.drawdown_label.setStyleSheet(f"color: {RED}; font-size: 16px; font-weight: bold; font-family: 'Consolas';")
        dd_layout.addWidget(self.drawdown_label)
        bottom_row.addLayout(dd_layout)

        # Trades Today
        trades_layout = QVBoxLayout()
        tr_label = QLabel("Trades Today")
        tr_label.setStyleSheet(f"color: {GRAY}; font-size: 11px; font-family: 'Consolas';")
        trades_layout.addWidget(tr_label)
        
        self.trades_today_label = QLabel("0")
        self.trades_today_label.setStyleSheet(f"color: {CYAN}; font-size: 16px; font-weight: bold; font-family: 'Consolas';")
        trades_layout.addWidget(self.trades_today_label)
        bottom_row.addLayout(trades_layout)

        layout.addLayout(bottom_row)

        return panel

    # =================== PROP FIRM COMPLIANCE PANEL ===================
    def _build_prop_firm_panel(self) -> QWidget:
        """Prop Firm Rule Compliance Panel (The "Professor")"""
        panel = QGroupBox("[GRADUATE] PROP FIRM RULES (The Professor)")
        panel.setStyleSheet(f"""
            QGroupBox {{ color: {CYAN}; font-size: 14px; font-weight: bold; font-family: 'Segoe UI';
                        border: 1px solid {BORDER}; border-radius: 8px; margin-top: 8px; padding-top: 12px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; }}
        """)
        
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)

        # Firm selector row
        firm_row = QHBoxLayout()
        firm_row.setSpacing(10)

        firm_label = QLabel("Firm:")
        firm_label.setStyleSheet(f"color: {WHITE}; font-size: 12px; font-weight: bold; font-family: 'Consolas';")
        firm_row.addWidget(firm_label)

        self.firm_selector = QComboBox()
        self.firm_selector.addItems(["TopStep", "Apex Trader Funding", "MyFundedFutures", "FTMO", "TradeDay"])
        self.firm_selector.setStyleSheet(f"""
            QComboBox {{ background: {BG_INPUT}; color: {WHITE}; border: 1px solid {BORDER};
                       border-radius: 6px; padding: 6px; font-size: 12px; font-family: 'Consolas'; }}
        """)
        saved_firm_name = str(settings_manager.get("prop_firm_name", "TopStep") or "TopStep")
        selector_index = self.firm_selector.findText(saved_firm_name)
        if selector_index >= 0:
            self.firm_selector.setCurrentIndex(selector_index)
        firm_row.addWidget(self.firm_selector)

        self.firm_status = QLabel("[OK] COMPLIANT")
        self.firm_status.setStyleSheet(f"color: {GREEN}; font-size: 12px; font-weight: bold; font-family: 'Consolas';")
        firm_row.addWidget(self.firm_status)

        firm_row.addStretch()
        layout.addLayout(firm_row)

        # Compliance bars
        self._add_compliance_bar(layout, "Daily Loss Used", 0.0, 150.0, GREEN, RED)
        self._add_compliance_bar(layout, "Trailing Drawdown", 0.0, 3000.0, GREEN, RED)
        self._add_compliance_bar(layout, "Profit Progress", 0.0, 3000.0, CYAN, CYAN)

        # Key metrics row
        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(15)

        self.prop_balance = QLabel("Balance: $50,000.00")
        self.prop_balance.setStyleSheet(f"color: {WHITE}; font-size: 11px; font-family: 'Consolas';")
        metrics_row.addWidget(self.prop_balance)

        self.prop_daily = QLabel("Daily: $0.00")
        self.prop_daily.setStyleSheet(f"color: {GREEN}; font-size: 11px; font-family: 'Consolas';")
        metrics_row.addWidget(self.prop_daily)

        self.prop_trades = QLabel("Trades: 0")
        self.prop_trades.setStyleSheet(f"color: {CYAN}; font-size: 11px; font-family: 'Consolas';")
        metrics_row.addWidget(self.prop_trades)

        metrics_row.addStretch()
        layout.addLayout(metrics_row)

        # Violations display
        self.violations_label = QLabel("")
        self.violations_label.setStyleSheet(f"color: {RED}; font-size: 11px; font-family: 'Consolas';")
        self.violations_label.setWordWrap(True)
        layout.addWidget(self.violations_label)

        return panel

    def _add_compliance_bar(self, layout, label, current, limit, good_color, bad_color):
        """Add a progress bar showing compliance usage."""
        row = QHBoxLayout()
        row.setSpacing(8)

        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {GRAY}; font-size: 10px; font-family: 'Consolas';")
        lbl.setFixedWidth(110)
        row.addWidget(lbl)

        # Progress bar frame
        bar_frame = QFrame()
        bar_frame.setFixedHeight(14)
        frame_border = RED if label == "Trailing Drawdown" else BORDER
        frame_bg = "#090C10" if label == "Trailing Drawdown" else BG_INPUT
        bar_frame.setStyleSheet(
            f"background: {frame_bg}; border: 1px solid {frame_border}; border-radius: 7px;"
        )
        bar_layout = QHBoxLayout(bar_frame)
        bar_layout.setContentsMargins(2, 2, 2, 2)
        bar_layout.setSpacing(0)

        # Progress fill (will be updated dynamically)
        bar = QFrame()
        bar.setFixedHeight(10)
        pct = min(100, (current / max(0.01, limit)) * 100)
        if label == "Trailing Drawdown":
            bar_color = RED if pct >= 70 else ORANGE if pct >= 45 else good_color
        else:
            bar_color = bad_color if pct > 80 else good_color
        bar.setStyleSheet(f"background: {bar_color}; border-radius: 5px;")
        bar.setMinimumWidth(int(pct * 2))
        bar_layout.addWidget(bar)

        row.addWidget(bar_frame, stretch=1)

        # Value label
        val = QLabel(f"${current:.0f} / ${limit:.0f}")
        val.setStyleSheet(f"color: {WHITE}; font-size: 10px; font-family: 'Consolas';")
        val.setFixedWidth(120)
        val.setAlignment(Qt.AlignmentFlag.AlignRight)
        row.addWidget(val)

        layout.addLayout(row)
        
        # Store references for dynamic updates
        if not hasattr(self, 'compliance_bars'):
            self.compliance_bars = {}
        
        self.compliance_bars[label] = {
            'label': label,
            'bar': bar,
            'bar_frame': bar_frame,
            'value_label': val,
            'limit': limit,
            'good_color': good_color,
            'bad_color': bad_color
        }

    def update_prop_firm_compliance(self, data: dict):
        """Update prop firm panel with compliance data."""
        # Update status
        can_trade = data.get("can_trade", True)
        self.firm_status.setText("[OK] COMPLIANT" if can_trade else "[STOP] BLOCKED")
        self.firm_status.setStyleSheet(f"""
            color: {GREEN if can_trade else RED}; font-size: 12px; font-weight: bold; font-family: 'Consolas';
        """)

        # Update metrics
        self.prop_balance.setText(f"Balance: ${data.get('current_balance', 0):,.2f}")
        daily_pnl = data.get("daily_pnl", 0)
        self.prop_daily.setText(f"Daily: ${daily_pnl:,.2f}")
        self.prop_daily.setStyleSheet(f"""
            color: {GREEN if daily_pnl >= 0 else RED}; font-size: 11px; font-family: 'Consolas';
        """)
        self.prop_trades.setText(f"Trades: {data.get('total_trades', 0)} (W:{data.get('wins', 0)} L:{data.get('losses', 0)})")

        # Update violations
        violations = data.get("violations", [])
        if violations:
            self.violations_label.setText("[WARN] " + "\n".join(violations))
        else:
            self.violations_label.setText("")

        # Update compliance bars dynamically
        if hasattr(self, 'compliance_bars'):
            # Update Daily Loss Used bar
            if "Daily Loss Used" in self.compliance_bars:
                bar_data = self.compliance_bars["Daily Loss Used"]
                current = abs(data.get("daily_pnl", 0))
                limit = data.get("daily_loss_limit", 150.0)
                self._update_compliance_bar(bar_data, current, limit)

            # Update Drawdown Used bar
            if "Trailing Drawdown" in self.compliance_bars:
                bar_data = self.compliance_bars["Trailing Drawdown"]
                current = data.get("max_drawdown", 0)
                limit = data.get("drawdown_limit", 3000.0)
                self._update_compliance_bar(bar_data, current, limit)

            # Update Profit Progress bar
            if "Profit Progress" in self.compliance_bars:
                bar_data = self.compliance_bars["Profit Progress"]
                current = max(0, data.get("total_pnl", 0))
                limit = data.get("profit_target", 3000.0)
                self._update_compliance_bar(bar_data, current, limit)

    def _update_compliance_bar(self, bar_data, current, limit):
        """Update a single compliance bar with new values."""
        pct = min(100, (current / max(0.01, limit)) * 100)
        if bar_data.get('label') == "Trailing Drawdown":
            bar_color = RED if pct >= 70 else ORANGE if pct >= 45 else bar_data['good_color']
            bar_data['bar_frame'].setStyleSheet(
                f"background: #090C10; border: 1px solid {RED if pct >= 70 else ORANGE}; border-radius: 7px;"
            )
        else:
            bar_color = bar_data['bad_color'] if pct > 80 else bar_data['good_color']
        
        # Update bar width
        bar_data['bar'].setStyleSheet(f"background: {bar_color}; border-radius: 5px;")
        bar_data['bar'].setMinimumWidth(max(4, int(pct * 2)))
        
        # Update value label
        suffix = f" ({pct:.0f}%)" if bar_data.get('label') == "Trailing Drawdown" else ""
        bar_data['value_label'].setText(f"${current:.0f} / ${limit:.0f}{suffix}")

    # =================== CONTROL PANEL ===================
    def _build_control_panel(self) -> QWidget:
        """Trading Controls: Mode, Investment, Risk Settings"""
        panel = QGroupBox("[GEAR] TRADING CONTROLS")
        panel.setStyleSheet(f"""
            QGroupBox {{ color: {CYAN}; font-size: 14px; font-weight: bold; font-family: 'Segoe UI';
                        border: 1px solid {BORDER}; border-radius: 8px; margin-top: 8px; padding-top: 12px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; }}
        """)
        
        layout = QVBoxLayout(panel)
        layout.setSpacing(10)

        # Row 1: Mode buttons (exclusive toggle)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        mode_label = QLabel("Mode:")
        mode_label.setStyleSheet(f"color: {GRAY}; font-size: 12px; font-weight: bold; font-family: 'Consolas';")
        mode_row.addWidget(mode_label)

        self.btn_teacher = QPushButton("[EMOJI] TEACHER (Approve Each)")
        self.btn_teacher.setCheckable(True)
        self.btn_teacher.setChecked(True)
        self.btn_teacher.setMinimumHeight(36)
        self.btn_teacher.setStyleSheet(f"""
            QPushButton {{ background: {CYAN}; color: {BG_DARK}; border: none; border-radius: 6px;
                         font-size: 11px; font-weight: bold; font-family: 'Consolas'; padding: 8px; }}
        """)
        self.btn_teacher.clicked.connect(self._set_teacher_mode)
        mode_row.addWidget(self.btn_teacher)

        self.btn_auto = QPushButton("[ROBOT] AUTONOMOUS (Auto)")
        self.btn_auto.setCheckable(True)
        self.btn_auto.setChecked(False)
        self.btn_auto.setMinimumHeight(36)
        self.btn_auto.setStyleSheet(f"""
            QPushButton {{ background: {BG_INPUT}; color: {GRAY}; border: 1px solid {BORDER}; border-radius: 6px;
                         font-size: 11px; font-weight: bold; font-family: 'Consolas'; padding: 8px; }}
        """)
        self.btn_auto.clicked.connect(self._set_autonomous_mode)
        mode_row.addWidget(self.btn_auto)
        
        layout.addLayout(mode_row)

        # Row 2: Default Investment Amount
        invest_row = QHBoxLayout()
        invest_row.setSpacing(10)

        inv_label = QLabel("[CASH] Default Investment ($):")
        inv_label.setStyleSheet(f"color: {WHITE}; font-size: 12px; font-weight: bold; font-family: 'Consolas';")
        invest_row.addWidget(inv_label)

        saved_investment = float(settings_manager.get("investment_amount", 10.0) or 10.0)
        self.investment_input = QLineEdit(f"{saved_investment:g}")
        self.investment_input.setPlaceholderText("Amount per trade")
        self.investment_input.setStyleSheet(f"""
            QLineEdit {{ background: {BG_INPUT}; color: {WHITE}; border: 1px solid {BORDER};
                       border-radius: 6px; padding: 8px; font-size: 14px; font-weight: bold;
                       font-family: 'Consolas'; }}
        """)
        self.investment_input.setFixedWidth(120)
        invest_row.addWidget(self.investment_input)
        invest_row.addStretch()
        layout.addLayout(invest_row)

        # Row 3: Risk Settings
        risk_row = QHBoxLayout()
        risk_row.setSpacing(15)

        # Take Profit %
        tp_layout = QVBoxLayout()
        tp_label = QLabel("Take Profit (%)")
        tp_label.setStyleSheet(f"color: {GREEN}; font-size: 11px; font-family: 'Consolas';")
        tp_layout.addWidget(tp_label)
        
        saved_take_profit_pct = float(settings_manager.get("take_profit_pct", 2.0) or 2.0)
        self.tp_input = QDoubleSpinBox()
        self.tp_input.setRange(0.1, 50.0)
        self.tp_input.setValue(saved_take_profit_pct)
        self.tp_input.setSuffix("%")
        self.tp_input.setEnabled(not self.auto_risk_enabled)
        self.tp_input.setToolTip("Manual risk target used when AUTO-RISK is off.")
        self.tp_input.setStyleSheet(f"""
            QDoubleSpinBox {{ background: {BG_INPUT}; color: {GREEN}; border: 1px solid {BORDER};
                           border-radius: 6px; padding: 6px; font-size: 13px; font-weight: bold;
                           font-family: 'Consolas'; }}
        """)
        tp_layout.addWidget(self.tp_input)
        risk_row.addLayout(tp_layout)

        # Stop Loss %
        sl_layout = QVBoxLayout()
        sl_header = QHBoxLayout()
        sl_header.setSpacing(6)

        sl_label = QLabel("Stop Loss (%)")
        sl_label.setStyleSheet(f"color: {RED}; font-size: 11px; font-family: 'Consolas';")
        sl_header.addWidget(sl_label)

        self.auto_risk_btn = QPushButton("AUTO-RISK")
        self.auto_risk_btn.setCheckable(True)
        self.auto_risk_btn.setChecked(self.auto_risk_enabled)
        self.auto_risk_btn.setFixedHeight(22)
        self.auto_risk_btn.setToolTip("Use structural stop logic from Profit Lock instead of fixed % inputs.")
        self.auto_risk_btn.clicked.connect(self._toggle_auto_risk)
        sl_header.addWidget(self.auto_risk_btn)
        sl_header.addStretch()
        sl_layout.addLayout(sl_header)
        
        saved_stop_loss_pct = float(settings_manager.get("stop_loss_pct", 1.0) or 1.0)
        self.sl_input = QDoubleSpinBox()
        self.sl_input.setRange(0.1, 20.0)
        self.sl_input.setValue(saved_stop_loss_pct)
        self.sl_input.setSuffix("%")
        self.sl_input.setEnabled(not self.auto_risk_enabled)
        self.sl_input.setToolTip("Manual stop used when AUTO-RISK is off.")
        self.sl_input.setStyleSheet(f"""
            QDoubleSpinBox {{ background: {BG_INPUT}; color: {RED}; border: 1px solid {BORDER};
                           border-radius: 6px; padding: 6px; font-size: 13px; font-weight: bold;
                           font-family: 'Consolas'; }}
        """)
        sl_layout.addWidget(self.sl_input)
        risk_row.addLayout(sl_layout)

        # Max Daily Loss
        msl_layout = QVBoxLayout()
        msl_label = QLabel("Max Daily Loss ($)")
        msl_label.setStyleSheet(f"color: {ORANGE}; font-size: 11px; font-family: 'Consolas';")
        msl_layout.addWidget(msl_label)
        
        self.max_loss_input = QDoubleSpinBox()
        self.max_loss_input.setRange(10, 10000)
        self.max_loss_input.setValue(float(settings_manager.get("max_daily_loss", 500.0) or 500.0))
        self.max_loss_input.setPrefix("$")
        self.max_loss_input.setStyleSheet(f"""
            QDoubleSpinBox {{ background: {BG_INPUT}; color: {ORANGE}; border: 1px solid {BORDER};
                           border-radius: 6px; padding: 6px; font-size: 13px; font-weight: bold;
                           font-family: 'Consolas'; }}
        """)
        msl_layout.addWidget(self.max_loss_input)
        risk_row.addLayout(msl_layout)

        layout.addLayout(risk_row)
        self._apply_auto_risk_state(initial=True)

        prop_row = QHBoxLayout()
        prop_row.setSpacing(8)

        self.prop_firm_mode_btn = QPushButton("PROP FIRM MODE")
        self.prop_firm_mode_btn.setCheckable(True)
        self.prop_firm_mode_btn.setChecked(self.prop_firm_mode_enabled)
        self.prop_firm_mode_btn.setMinimumHeight(34)
        self.prop_firm_mode_btn.clicked.connect(self._toggle_prop_firm_mode)
        prop_row.addWidget(self.prop_firm_mode_btn)

        self.prop_firm_mode_status = QLabel("")
        self.prop_firm_mode_status.setStyleSheet(f"color: {GRAY}; font-size: 11px; font-family: 'Consolas';")
        prop_row.addWidget(self.prop_firm_mode_status)
        prop_row.addStretch()
        layout.addLayout(prop_row)
        self._apply_prop_firm_mode_state(initial=True)

        # Save Settings Button
        save_btn = QPushButton("[EMOJI] Save Settings")
        save_btn.setMinimumHeight(36)
        save_btn.setStyleSheet(f"""
            QPushButton {{ background: {GREEN}; color: {BG_DARK}; border: none; border-radius: 6px;
                         font-size: 13px; font-weight: bold; font-family: 'Consolas'; padding: 8px; }}
            QPushButton:hover {{ background: #2ea043; }}
        """)
        save_btn.clicked.connect(self._save_settings)
        layout.addWidget(save_btn)

        analysis_frame = QFrame()
        analysis_frame.setStyleSheet(f"background: {BG_INPUT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 8px;")
        analysis_layout = QHBoxLayout(analysis_frame)
        analysis_layout.setContentsMargins(8, 6, 8, 6)
        analysis_layout.setSpacing(10)

        analysis_label = QLabel("Analysis Tools:")
        analysis_label.setStyleSheet(f"color: {GRAY}; font-size: 11px; font-weight: bold; font-family: 'Consolas';")
        analysis_layout.addWidget(analysis_label)

        self.analysis_option_labels = {}
        option_styles = {
            "Liquidity": ORANGE,
            "MTF": CYAN,
            "Risk": GREEN,
        }
        for name, color in option_styles.items():
            chip = QLabel(f"{name}: ON")
            chip.setStyleSheet(
                f"color: {color}; background: {BG_PANEL}; border: 1px solid {BORDER}; "
                f"border-radius: 5px; padding: 4px 8px; font-size: 11px; font-weight: bold; font-family: 'Consolas';"
            )
            analysis_layout.addWidget(chip)
            self.analysis_option_labels[name] = chip

        analysis_layout.addStretch()
        self.analysis_visibility_label = QLabel("Waiting for watchlist")
        self.analysis_visibility_label.setStyleSheet(f"color: {GRAY}; font-size: 10px; font-family: 'Consolas';")
        analysis_layout.addWidget(self.analysis_visibility_label)
        layout.addWidget(analysis_frame)

        # Test Execution Section
        test_section = self._build_test_execution_section()
        layout.addWidget(test_section)

        return panel

    def _build_test_execution_section(self) -> QWidget:
        """Test Execution controls for verifying browser clicking."""
        panel = QFrame()
        panel.setStyleSheet(f"""
            background: {BG_PANEL};
            border: 2px solid {CYAN};
            border-radius: 8px;
            padding: 10px;
        """)
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)

        # Header
        header = QLabel("[TEST] TEST EXECUTION (Verify Browser Clicking)")
        header.setStyleSheet(f"color: {CYAN}; font-size: 12px; font-weight: bold; font-family: 'Consolas';")
        layout.addWidget(header)

        # Test buttons row
        test_row = QHBoxLayout()
        test_row.setSpacing(8)

        # Test Browser Click button
        self.test_browser_btn = QPushButton("[GLOBE] Test Browser")
        self.test_browser_btn.setMinimumHeight(32)
        self.test_browser_btn.setStyleSheet(f"""
            QPushButton {{ background: {CYAN}; color: {BG_DARK}; border: none; border-radius: 6px;
                         font-size: 11px; font-weight: bold; font-family: 'Consolas'; padding: 6px 12px; }}
            QPushButton:hover {{ background: #00b8e6; }}
        """)
        self.test_browser_btn.clicked.connect(self._test_browser_click)
        test_row.addWidget(self.test_browser_btn)

        # Force Test Trade button
        self.force_test_btn = QPushButton("[BOLT] FORCE HAND TEST")
        self.force_test_btn.setMinimumHeight(32)
        self.force_test_btn.setStyleSheet(f"""
            QPushButton {{ background: {ORANGE}; color: {BG_DARK}; border: none; border-radius: 6px;
                         font-size: 11px; font-weight: bold; font-family: 'Consolas'; padding: 6px 12px; }}
            QPushButton:hover {{ background: #b8860b; }}
        """)
        self.force_test_btn.clicked.connect(self._force_test_trade)
        test_row.addWidget(self.force_test_btn)

        # Dry Run toggle
        self.dry_run_btn = QPushButton("DRY RUN: ON")
        self.dry_run_btn.setMinimumHeight(32)
        self.dry_run_btn.setCheckable(True)
        self.dry_run_btn.setChecked(True)
        self.dry_run_btn.setStyleSheet(f"""
            QPushButton {{ background: {GREEN}; color: {BG_DARK}; border: none; border-radius: 6px;
                         font-size: 11px; font-weight: bold; font-family: 'Consolas'; padding: 6px 12px; }}
        """)
        self.dry_run_btn.clicked.connect(self._toggle_dry_run)
        test_row.addWidget(self.dry_run_btn)

        test_row.addStretch()
        layout.addLayout(test_row)

        # Status label
        self.test_status = QLabel("Status: Ready for testing")
        self.test_status.setStyleSheet(f"color: {GRAY}; font-size: 10px; font-family: 'Consolas';")
        layout.addWidget(self.test_status)

        return panel

    def _test_browser_click(self):
        """Test browser agent navigation and clicking."""
        self.test_status.setText("Status: Testing browser agent...")
        self.test_status.setStyleSheet(f"color: {ORANGE}; font-size: 10px; font-family: 'Consolas';")
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(100, lambda: self.test_browser_requested.emit())
        self.log("[TEST] TEST: Browser agent click test requested")

    def _force_test_trade(self):
        """Force a visible RPA hand-move diagnostic against the active TradingView chart."""
        self.test_status.setText("Status: Force hand move diagnostic initiated...")
        self.test_status.setStyleSheet(f"color: {ORANGE}; font-size: 10px; font-family: 'Consolas';")
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(100, lambda: self.force_test_trade_requested.emit())
        self.log("[BOLT] FORCE HAND TEST: visible TradingView hand-move requested")

    def _toggle_dry_run(self):
        """Toggle dry run mode."""
        is_dry_run = self.dry_run_btn.isChecked()
        if is_dry_run:
            self.dry_run_btn.setText("DRY RUN: ON")
            self.dry_run_btn.setStyleSheet(f"""
                QPushButton {{ background: {GREEN}; color: {BG_DARK}; border: none; border-radius: 6px;
                             font-size: 11px; font-weight: bold; font-family: 'Consolas'; padding: 6px 12px; }}
            """)
            self.log("[OK] DRY RUN: ON - Paper trading mode")
        else:
            self.dry_run_btn.setText("DRY RUN: OFF")
            self.dry_run_btn.setStyleSheet(f"""
                QPushButton {{ background: {RED}; color: {WHITE}; border: none; border-radius: 6px;
                             font-size: 11px; font-weight: bold; font-family: 'Consolas'; padding: 6px 12px; }}
            """)
            self.log("[WARN] DRY RUN: OFF - Live trading mode (CAUTION!)")

    def _apply_auto_risk_state(self, initial: bool = False):
        """Toggle manual TP/SL inputs against structural Profit Lock risk logic."""
        self.tp_input.setEnabled(not self.auto_risk_enabled)
        self.sl_input.setEnabled(not self.auto_risk_enabled)

        if self.auto_risk_enabled:
            self.auto_risk_btn.setStyleSheet(f"""
                QPushButton {{ background: {GREEN}; color: {BG_DARK}; border: none; border-radius: 6px;
                             font-size: 10px; font-weight: bold; font-family: 'Consolas'; padding: 4px 8px; }}
                QPushButton:hover {{ background: #2ea043; }}
            """)
            if not initial:
                self.log("[BRAIN] AUTO-RISK engaged - fixed TP/SL ignored, structural Profit Lock logic in command")
        else:
            self.auto_risk_btn.setStyleSheet(f"""
                QPushButton {{ background: {BG_INPUT}; color: {ORANGE}; border: 1px solid {ORANGE}; border-radius: 6px;
                             font-size: 10px; font-weight: bold; font-family: 'Consolas'; padding: 4px 8px; }}
                QPushButton:hover {{ border-color: {WHITE}; }}
            """)
            if not initial:
                self.log(
                    f"[TARGET] AUTO-RISK disabled - using fixed TP {self.tp_input.value():.1f}% / SL {self.sl_input.value():.1f}%"
                )

    def _toggle_auto_risk(self):
        """Switch between structural risk and fixed-percent scalping mode."""
        self.auto_risk_enabled = self.auto_risk_btn.isChecked()
        self._apply_auto_risk_state(initial=False)

    def _apply_prop_firm_mode_state(self, initial: bool = False):
        """Reflect prop-firm enforcement in the dashboard controls."""
        if self.prop_firm_mode_enabled:
            self.max_loss_input.setValue(150.0)
            self.max_loss_input.setEnabled(False)
            self.prop_firm_mode_btn.setStyleSheet(f"""
                QPushButton {{ background: {ORANGE}; color: {BG_DARK}; border: none; border-radius: 6px;
                             font-size: 12px; font-weight: bold; font-family: 'Consolas'; padding: 8px 12px; }}
                QPushButton:hover {{ background: #b8860b; }}
            """)
            self.prop_firm_mode_status.setText("Max Lots: 1 | Human Latency: ON | Daily Loss: $150")
            self.prop_firm_mode_status.setStyleSheet(f"color: {ORANGE}; font-size: 11px; font-family: 'Consolas';")
            if not initial:
                self.log("[GRADUATE] Prop Firm Mode ON - Max lots 1, human latency enabled, daily loss capped at $150")
        else:
            restore_value = self._manual_max_loss_value if self._manual_max_loss_value > 0 else 500.0
            self.max_loss_input.setEnabled(True)
            if not initial:
                self.max_loss_input.setValue(restore_value)
            self.prop_firm_mode_btn.setStyleSheet(f"""
                QPushButton {{ background: {BG_INPUT}; color: {CYAN}; border: 1px solid {BORDER}; border-radius: 6px;
                             font-size: 12px; font-weight: bold; font-family: 'Consolas'; padding: 8px 12px; }}
                QPushButton:hover {{ border-color: {CYAN}; }}
            """)
            self.prop_firm_mode_status.setText("Manual limits active")
            self.prop_firm_mode_status.setStyleSheet(f"color: {GRAY}; font-size: 11px; font-family: 'Consolas';")
            if not initial:
                self.log("[GRADUATE] Prop Firm Mode OFF - manual limits restored")

    def _toggle_prop_firm_mode(self):
        """Force strict prop-firm-safe execution settings when enabled."""
        checked = self.prop_firm_mode_btn.isChecked()
        if checked and float(self.max_loss_input.value()) != 150.0:
            self._manual_max_loss_value = float(self.max_loss_input.value())
        self.prop_firm_mode_enabled = checked
        self._apply_prop_firm_mode_state(initial=False)
        self._save_settings()

    # =================== WATCHLIST PANEL ===================
    def _build_watchlist_panel(self) -> QWidget:
        """Watchlist Management - Add/Remove Tickers to Monitor"""
        panel = QGroupBox("[CHART] WATCHLIST (Monitored Instruments)")
        panel.setStyleSheet(f"""
            QGroupBox {{ color: {CYAN}; font-size: 14px; font-weight: bold; font-family: 'Segoe UI';
                        border: 1px solid {BORDER}; border-radius: 8px; margin-top: 8px; padding-top: 12px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; }}
        """)
        
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)

        # Add ticker row
        add_row = QHBoxLayout()
        add_row.setSpacing(8)

        add_label = QLabel("Add:")
        add_label.setStyleSheet(f"color: {WHITE}; font-size: 12px; font-weight: bold; font-family: 'Consolas';")
        add_row.addWidget(add_label)

        self.ticker_input = QLineEdit()
        self.ticker_input.setPlaceholderText("e.g., BTC-USD, AAPL, XAUUSD")
        self.ticker_input.setStyleSheet(f"""
            QLineEdit {{ background: {BG_INPUT}; color: {WHITE}; border: 1px solid {BORDER};
                       border-radius: 6px; padding: 8px; font-size: 12px; font-family: 'Consolas'; }}
        """)
        self.ticker_input.setFixedWidth(200)
        self.ticker_input.returnPressed.connect(self._add_ticker)
        add_row.addWidget(self.ticker_input)

        add_btn = QPushButton("[EMOJI] Add")
        add_btn.setMinimumHeight(34)
        add_btn.setStyleSheet(f"""
            QPushButton {{ background: {CYAN}; color: {BG_DARK}; border: none; border-radius: 6px;
                         font-size: 12px; font-weight: bold; font-family: 'Consolas'; padding: 6px 16px; }}
            QPushButton:hover {{ background: #00b8e6; }}
        """)
        add_btn.clicked.connect(self._add_ticker)
        add_row.addWidget(add_btn)

        remove_btn = QPushButton("[EMOJI] Remove Selected")
        remove_btn.setMinimumHeight(34)
        remove_btn.setStyleSheet(f"""
            QPushButton {{ background: {RED}; color: {WHITE}; border: none; border-radius: 6px;
                         font-size: 12px; font-weight: bold; font-family: 'Consolas'; padding: 6px 16px; }}
            QPushButton:hover {{ background: #da3633; }}
        """)
        remove_btn.clicked.connect(self._remove_ticker)
        add_row.addWidget(remove_btn)

        add_row.addStretch()
        layout.addLayout(add_row)

        # 10 live dashboard slots that drive the scanner in real time.
        slots_frame = QFrame()
        slots_layout = QVBoxLayout(slots_frame)
        slots_layout.setContentsMargins(0, 0, 0, 0)
        slots_layout.setSpacing(6)

        slot_label = QLabel("Live Watchlist Slots (scanner follows these instantly)")
        slot_label.setStyleSheet(f"color: {GRAY}; font-size: 11px; font-family: 'Consolas';")
        slots_layout.addWidget(slot_label)

        self.watchlist_slots = []
        for row_index in range(2):
            row = QHBoxLayout()
            row.setSpacing(8)
            for col_index in range(5):
                slot_index = row_index * 5 + col_index
                slot = QLineEdit()
                slot.setPlaceholderText(f"Slot {slot_index + 1}")
                if slot_index < len(self.watchlist):
                    slot.setText(self.watchlist[slot_index])
                slot.setStyleSheet(f"""
                    QLineEdit {{ background: {BG_INPUT}; color: {WHITE}; border: 1px solid {BORDER};
                               border-radius: 6px; padding: 6px; font-size: 11px; font-family: 'Consolas'; }}
                """)
                slot.textChanged.connect(self._queue_watchlist_sync)
                row.addWidget(slot)
                self.watchlist_slots.append(slot)
            slots_layout.addLayout(row)

        layout.addWidget(slots_frame)

        # Watchlist table
        self.watchlist_table = QTableWidget()
        self.watchlist_table.setColumnCount(4)
        self.watchlist_table.setHorizontalHeaderLabels(["[OK]", "Ticker", "Last Signal", "Status"])
        # Column width optimization: Slim Ticker/Status, wide Logic/Reasoning
        self.watchlist_table.setColumnWidth(0, 30)   # Checkmark - very slim
        self.watchlist_table.setColumnWidth(1, 80)   # Ticker - slim
        self.watchlist_table.setColumnWidth(2, 200)  # Last Signal - medium
        self.watchlist_table.horizontalHeader().setStretchLastSection(True)  # Status stretches
        self.watchlist_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)  # Status column stretches to fill
        self.watchlist_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.watchlist_table.setAlternatingRowColors(True)
        self.watchlist_table.setMaximumHeight(200)
        self.watchlist_table.setStyleSheet(f"""
            QTableWidget {{ background: {BG_INPUT}; color: {WHITE}; border: 1px solid {BORDER};
                          border-radius: 6px; font-family: 'Consolas'; font-size: 12px; }}
            QHeaderView::section {{ background: {BG_PANEL}; color: {CYAN}; border: none;
                                   padding: 6px; font-weight: bold; }}
        """)
        self._refresh_watchlist()
        layout.addWidget(self.watchlist_table)

        return panel

    # =================== POSITIONS PANEL ===================
    def _build_positions_panel(self) -> QWidget:
        """Live Positions with Real-Time P&L"""
        panel = QGroupBox("[UP] LIVE POSITIONS (Auto-Monitored)")
        panel.setStyleSheet(f"""
            QGroupBox {{ color: {CYAN}; font-size: 14px; font-weight: bold; font-family: 'Segoe UI';
                        border: 1px solid {BORDER}; border-radius: 8px; margin-top: 8px; padding-top: 12px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; }}
        """)
        
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)

        # Positions table
        self.positions_table = QTableWidget()
        self.positions_table.setColumnCount(8)
        self.positions_table.setHorizontalHeaderLabels([
            "Asset", "Side", "Entry", "Current", "P&L ($)", "P&L (%)", "TP", "SL"
        ])
        # Column width optimization: Slim Side/Entry/Current, wide Asset for readability
        self.positions_table.setColumnWidth(0, 100)  # Asset - medium
        self.positions_table.setColumnWidth(1, 50)   # Side - slim (BUY/SELL)
        self.positions_table.setColumnWidth(2, 70)   # Entry - slim
        self.positions_table.setColumnWidth(3, 70)   # Current - slim
        self.positions_table.setColumnWidth(4, 80)   # P&L ($) - medium
        self.positions_table.setColumnWidth(5, 70)   # P&L (%) - medium
        self.positions_table.setColumnWidth(6, 70)   # TP - slim
        self.positions_table.setColumnWidth(7, 70)   # SL - slim
        self.positions_table.horizontalHeader().setStretchLastSection(True)
        self.positions_table.setAlternatingRowColors(True)
        self.positions_table.setMaximumHeight(200)
        self.positions_table.setStyleSheet(f"""
            QTableWidget {{ background: {BG_INPUT}; color: {WHITE}; border: 1px solid {BORDER};
                          border-radius: 6px; font-family: 'Consolas'; font-size: 12px; }}
            QHeaderView::section {{ background: {BG_PANEL}; color: {CYAN}; border: none;
                                   padding: 6px; font-weight: bold; }}
        """)
        layout.addWidget(self.positions_table)

        # Stats row
        stats_row = QHBoxLayout()
        stats_row.setSpacing(20)

        open_label = QLabel(f"Open Positions: 0")
        open_label.setStyleSheet(f"color: {CYAN}; font-size: 12px; font-weight: bold; font-family: 'Consolas';")
        stats_row.addWidget(open_label)

        unrealized_label = QLabel(f"Unrealized P&L: $0.00")
        unrealized_label.setStyleSheet(f"color: {GREEN}; font-size: 12px; font-weight: bold; font-family: 'Consolas';")
        stats_row.addWidget(unrealized_label)

        stats_row.addStretch()
        layout.addLayout(stats_row)

        return panel

    # =================== TRADE LOG PANEL ===================
    def _build_trade_log_panel(self) -> QWidget:
        """Trade History & Activity Log"""
        panel = QGroupBox("[EMOJI] TRADE LOG & ACTIVITY")
        panel.setStyleSheet(f"""
            QGroupBox {{ color: {CYAN}; font-size: 14px; font-weight: bold; font-family: 'Segoe UI';
                        border: 1px solid {BORDER}; border-radius: 8px; margin-top: 8px; padding-top: 12px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; }}
        """)
        
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)

        # Trade history table
        self.trade_log_table = QTableWidget()
        self.trade_log_table.setColumnCount(6)
        self.trade_log_table.setHorizontalHeaderLabels([
            "Time", "Asset", "Action", "Amount", "P&L", "Status"
        ])
        # Column width optimization: Slim Time/Action/Amount, wide Asset for readability
        self.trade_log_table.setColumnWidth(0, 90)   # Time - slim
        self.trade_log_table.setColumnWidth(1, 80)   # Asset - slim
        self.trade_log_table.setColumnWidth(2, 60)   # Action - very slim (BUY/SELL)
        self.trade_log_table.setColumnWidth(3, 70)   # Amount - slim
        self.trade_log_table.setColumnWidth(4, 80)   # P&L - medium
        self.trade_log_table.horizontalHeader().setStretchLastSection(True)  # Status stretches
        self.trade_log_table.setAlternatingRowColors(True)
        self.trade_log_table.setMaximumHeight(180)
        self.trade_log_table.setStyleSheet(f"""
            QTableWidget {{ background: {BG_INPUT}; color: {WHITE}; border: 1px solid {BORDER};
                          border-radius: 6px; font-family: 'Consolas'; font-size: 11px; }}
            QHeaderView::section {{ background: {BG_PANEL}; color: {CYAN}; border: none;
                                   padding: 6px; font-weight: bold; }}
        """)
        layout.addWidget(self.trade_log_table)

        # Activity log
        log_label = QLabel("Activity Log:")
        log_label.setStyleSheet(f"color: {GRAY}; font-size: 11px; font-weight: bold; font-family: 'Consolas';")
        layout.addWidget(log_label)

        self.activity_log = QTextEdit()
        self.activity_log.setReadOnly(True)
        self.activity_log.setMaximumHeight(150)
        self.activity_log.setStyleSheet(f"""
            QTextEdit {{ background: {BG_INPUT}; color: {GREEN}; border: 1px solid {BORDER};
                      border-radius: 6px; font-family: 'Consolas'; font-size: 11px; padding: 8px; }}
        """)
        layout.addWidget(self.activity_log)

        # Action buttons row
        action_btn_row = QHBoxLayout()
        action_btn_row.setSpacing(8)

        clear_log_btn = QPushButton("[EMOJI] Clear Log")
        clear_log_btn.setMinimumHeight(32)
        clear_log_btn.setStyleSheet(f"""
            QPushButton {{ background: {BG_INPUT}; color: {GRAY}; border: 1px solid {BORDER}; border-radius: 6px;
                         font-size: 11px; font-weight: bold; font-family: 'Consolas'; padding: 6px 12px; }}
            QPushButton:hover {{ background: {BORDER}; color: {WHITE}; }}
        """)
        clear_log_btn.clicked.connect(self._clear_activity_log)
        action_btn_row.addWidget(clear_log_btn)

        export_btn = QPushButton("[CHART] Export Trade History")
        export_btn.setMinimumHeight(32)
        export_btn.setStyleSheet(f"""
            QPushButton {{ background: {CYAN}; color: {BG_DARK}; border: none; border-radius: 6px;
                         font-size: 11px; font-weight: bold; font-family: 'Consolas'; padding: 6px 12px; }}
            QPushButton:hover {{ background: #00b8e6; }}
        """)
        export_btn.clicked.connect(self._export_trade_history)
        action_btn_row.addWidget(export_btn)

        action_btn_row.addStretch()
        layout.addLayout(action_btn_row)

        return panel

    # =================== CO-PILOT COMMAND BRIDGE ===================
    def _build_copilot_chat_panel(self) -> QWidget:
        """Co-Pilot Command Bridge - Human-AI Collaborative Trading"""
        panel = QGroupBox("[SUCCESS] CO-PILOT COMMAND BRIDGE (Talk to Your AI)")
        panel.setStyleSheet(f"""
            QGroupBox {{ color: {CYAN}; font-size: 14px; font-weight: bold; font-family: 'Segoe UI';
                        border: 2px solid {CYAN}; border-radius: 8px; margin-top: 8px; padding-top: 12px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; }}
        """)

        layout = QVBoxLayout(panel)
        layout.setSpacing(8)

        # Instruction text
        instruction = QLabel("[IDEA] Examples: 'Switch to 2H BTC longs' | 'News just dropped for ETH, analyze' | 'Force buy BTC now'")
        instruction.setStyleSheet(f"color: {GRAY}; font-size: 10px; font-family: 'Consolas'; font-style: italic;")
        instruction.setWordWrap(True)
        layout.addWidget(instruction)

        # Market Sentiment Banner (Global Context)
        self.market_sentiment_banner = QLabel("[SAT] Market Sentiment: Scanning headlines...")
        self.market_sentiment_banner.setStyleSheet(f"""
            QLabel {{ color: {CYAN}; font-size: 11px; font-weight: bold; font-family: 'Consolas';
                     background: {BG_INPUT}; border: 1px solid {CYAN}; border-radius: 6px; padding: 6px; }}
        """)
        self.market_sentiment_banner.setWordWrap(True)
        layout.addWidget(self.market_sentiment_banner)

        # Chat display (AI responses + user messages)
        self.copilot_chat = QTextEdit()
        self.copilot_chat.setReadOnly(True)
        self.copilot_chat.setMaximumHeight(250)
        self.copilot_chat.setStyleSheet(f"""
            QTextEdit {{ background: {BG_INPUT}; color: {WHITE}; border: 1px solid {BORDER};
                      border-radius: 6px; font-family: 'Consolas'; font-size: 11px; padding: 8px; }}
        """)
        # Add welcome message
        self.copilot_chat.append(f'<span style="color:{CYAN}; font-weight:bold;">[ROBOT] AI Co-Pilot:</span> <span style="color:{GREEN};">Ready for your commands. I\'m your Strict Boss - I\'ll push back if your idea conflicts with the data.</span>')
        layout.addWidget(self.copilot_chat)

        # Input row
        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        self.copilot_input = QLineEdit()
        self.copilot_input.setPlaceholderText("Type your command or suggestion here...")
        self.copilot_input.setStyleSheet(f"""
            QLineEdit {{ background: {BG_INPUT}; color: {WHITE}; border: 1px solid {CYAN};
                       border-radius: 6px; padding: 10px; font-size: 12px; font-family: 'Consolas'; }}
        """)
        self.copilot_input.returnPressed.connect(self._send_copilot_command)
        input_row.addWidget(self.copilot_input, stretch=1)

        # Send button
        send_btn = QPushButton("[EMOJI] Send")
        send_btn.setMinimumHeight(40)
        send_btn.setFixedWidth(80)
        send_btn.setStyleSheet(f"""
            QPushButton {{ background: {CYAN}; color: {BG_DARK}; border: none; border-radius: 6px;
                         font-size: 12px; font-weight: bold; font-family: 'Consolas'; padding: 8px; }}
            QPushButton:hover {{ background: #00b8e6; }}
            QPushButton:pressed {{ background: #0099cc; }}
        """)
        send_btn.clicked.connect(self._send_copilot_command)
        input_row.addWidget(send_btn)

        layout.addLayout(input_row)

        # Status indicators row
        status_row = QHBoxLayout()
        status_row.setSpacing(15)

        self.copilot_status = QLabel("[GREEN] AI Status: Ready")
        self.copilot_status.setStyleSheet(f"color: {GREEN}; font-size: 11px; font-weight: bold; font-family: 'Consolas';")
        status_row.addWidget(self.copilot_status)

        self.copilot_mode = QLabel("Current Mode: SCANNING")
        self.copilot_mode.setStyleSheet(f"color: {CYAN}; font-size: 11px; font-family: 'Consolas';")
        status_row.addWidget(self.copilot_mode)

        self.vibe_status = QLabel("[YELLOW] Vibe Status: Standby")
        self.vibe_status.setStyleSheet(f"color: {ORANGE}; font-size: 11px; font-weight: bold; font-family: 'Consolas';")
        status_row.addWidget(self.vibe_status)

        status_row.addStretch()
        layout.addLayout(status_row)

        return panel

    def _send_copilot_command(self):
        """Send user command to AI Co-Pilot"""
        command = self.copilot_input.text().strip()
        if not command:
            return

        # Display user message
        self.copilot_chat.append(f'<span style="color:{ORANGE}; font-weight:bold;">[EMOJI] You:</span> {command}')
        
        # Emit signal to main app for processing
        self.user_command_sent.emit(command)
        
        # Clear input
        self.copilot_input.clear()
        
        # Log
        self.log(f"[SUCCESS] Co-Pilot command sent: {command}")

    def add_copilot_response(self, thoughts: str, verdict: str, adjustment: str = ""):
        """Add AI response to copilot chat in [THOUGHTS], [VERDICT], [ADJUSTMENT] format"""
        self.copilot_chat.append(f'<span style="color:{CYAN}; font-weight:bold;">[ROBOT] AI Co-Pilot:</span>')
        self.copilot_chat.append(f'<span style="color:{YELLOW};">[THOUGHTS]</span> {thoughts}')
        self.copilot_chat.append(f'<span style="color:{GREEN};">[VERDICT]</span> {verdict}')
        if adjustment:
            self.copilot_chat.append(f'<span style="color:{ORANGE};">[ADJUSTMENT]</span> {adjustment}')
        self.copilot_chat.append("")  # Empty line for spacing
        
        # Auto-scroll
        cursor = self.copilot_chat.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.copilot_chat.setTextCursor(cursor)

    def update_copilot_status(self, status: str):
        """Update AI Co-Pilot status indicator"""
        self.copilot_status.setText(f"[GREEN] AI Status: {status}")

    def update_copilot_mode(self, mode: str, color: str = CYAN):
        """Update the active command posture shown in the Co-Pilot bridge."""
        self.copilot_mode.setText(f"Current Mode: {mode}")
        self.copilot_mode.setStyleSheet(f"color: {color}; font-size: 11px; font-family: 'Consolas';")

    def update_vibe_status(self, status: str, mode: str = "standby"):
        """Update Vibe shield status indicator."""
        normalized = str(mode or "standby").lower()
        icon = "[YELLOW]"
        color = ORANGE
        if normalized == "active":
            icon = "[GREEN]"
            color = GREEN
        elif normalized == "fallback":
            icon = "[YELLOW]"
            color = YELLOW
        elif normalized == "standby":
            icon = "[YELLOW]"
            color = ORANGE
        elif normalized == "offline":
            icon = "[WHITE]"
            color = GRAY

        self.vibe_status.setText(f"{icon} Vibe Status: {status}")
        self.vibe_status.setStyleSheet(
            f"color: {color}; font-size: 11px; font-weight: bold; font-family: 'Consolas';"
        )

    # =================== INSTITUTIONAL GOVERNOR (STAGE 3) ===================
    def _build_institutional_governor_panel(self) -> QWidget:
        """Stage 3: Institutional Governor & Risk Architect"""
        panel = QGroupBox("[GOVERN] INSTITUTIONAL GOVERNOR (Stage 3 Risk Management)")
        panel.setStyleSheet(f"""
            QGroupBox {{ color: {CYAN}; font-size: 14px; font-weight: bold; font-family: 'Segoe UI';
                        border: 2px solid {ORANGE}; border-radius: 8px; margin-top: 8px; padding-top: 12px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; }}
        """)

        layout = QVBoxLayout(panel)
        layout.setSpacing(8)

        # Row 1: Total Exposure, Correlation Risk, News Timer
        top_row = QHBoxLayout()
        top_row.setSpacing(15)

        # Total Exposure
        exp_layout = QVBoxLayout()
        exp_label = QLabel("Total Exposure")
        exp_label.setStyleSheet(f"color: {GRAY}; font-size: 10px; font-family: 'Consolas';")
        exp_layout.addWidget(exp_label)

        self.total_exposure_label = QLabel("0.0% / 15.0%")
        self.total_exposure_label.setStyleSheet(f"color: {GREEN}; font-size: 16px; font-weight: bold; font-family: 'Consolas';")
        exp_layout.addWidget(self.total_exposure_label)
        top_row.addLayout(exp_layout)

        # Correlation Risk
        corr_layout = QVBoxLayout()
        corr_label = QLabel("Correlation Risk")
        corr_label.setStyleSheet(f"color: {GRAY}; font-size: 10px; font-family: 'Consolas';")
        corr_layout.addWidget(corr_label)

        self.correlation_risk_label = QLabel("0.00 (SAFE)")
        self.correlation_risk_label.setStyleSheet(f"color: {GREEN}; font-size: 16px; font-weight: bold; font-family: 'Consolas';")
        corr_layout.addWidget(self.correlation_risk_label)
        top_row.addLayout(corr_layout)

        # Time Until Next News Event
        news_layout = QVBoxLayout()
        news_label = QLabel("Next News Event")
        news_label.setStyleSheet(f"color: {GRAY}; font-size: 10px; font-family: 'Consolas';")
        news_layout.addWidget(news_label)

        self.next_news_label = QLabel("None")
        self.next_news_label.setStyleSheet(f"color: {CYAN}; font-size: 16px; font-weight: bold; font-family: 'Consolas';")
        news_layout.addWidget(self.next_news_label)
        top_row.addLayout(news_layout)

        layout.addLayout(top_row)

        # Row 2: RPA Status, Walk Away Status, Profit Lock
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(15)

        # RPA Hand Status
        rpa_layout = QVBoxLayout()
        rpa_label = QLabel("RPA Hand")
        rpa_label.setStyleSheet(f"color: {GRAY}; font-size: 10px; font-family: 'Consolas';")
        rpa_layout.addWidget(rpa_label)

        self.rpa_status_label = QLabel("[OK] ENABLED")
        self.rpa_status_label.setStyleSheet(f"color: {GREEN}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")
        rpa_layout.addWidget(self.rpa_status_label)
        bottom_row.addLayout(rpa_layout)

        # Walk Away Protocol
        walk_layout = QVBoxLayout()
        walk_label = QLabel("Walk Away Protocol")
        walk_label.setStyleSheet(f"color: {GRAY}; font-size: 10px; font-family: 'Consolas';")
        walk_layout.addWidget(walk_label)

        self.walk_away_label = QLabel("[OK] ACTIVE")
        self.walk_away_label.setStyleSheet(f"color: {GREEN}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")
        walk_layout.addWidget(self.walk_away_label)
        bottom_row.addLayout(walk_layout)

        # Profit Lock Status
        lock_layout = QVBoxLayout()
        lock_label = QLabel("Profit Lock")
        lock_label.setStyleSheet(f"color: {GRAY}; font-size: 10px; font-family: 'Consolas';")
        lock_layout.addWidget(lock_label)

        self.profit_lock_label = QLabel("UNLOCKED")
        self.profit_lock_label.setStyleSheet(f"color: {GRAY}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")
        lock_layout.addWidget(self.profit_lock_label)
        bottom_row.addLayout(lock_layout)

        layout.addLayout(bottom_row)

        # Row 3: Daily P&L Progress Bar
        progress_row = QHBoxLayout()
        progress_row.setSpacing(8)

        progress_label = QLabel("Daily P&L Progress:")
        progress_label.setStyleSheet(f"color: {WHITE}; font-size: 11px; font-weight: bold; font-family: 'Consolas';")
        progress_row.addWidget(progress_label)

        # Progress bar frame
        bar_frame = QFrame()
        bar_frame.setFixedHeight(18)
        bar_frame.setStyleSheet(f"background: {BG_INPUT}; border: 1px solid {BORDER}; border-radius: 9px;")
        bar_layout = QHBoxLayout(bar_frame)
        bar_layout.setContentsMargins(2, 2, 2, 2)
        bar_layout.setSpacing(0)

        # Progress fill
        self.daily_pnl_bar = QFrame()
        self.daily_pnl_bar.setFixedHeight(14)
        self.daily_pnl_bar.setStyleSheet(f"background: {GREEN}; border-radius: 7px;")
        self.daily_pnl_bar.setMinimumWidth(4)
        bar_layout.addWidget(self.daily_pnl_bar, stretch=1)

        progress_row.addWidget(bar_frame, stretch=1)

        # P&L value label
        self.daily_pnl_value_label = QLabel("$0.00 (0.0%)")
        self.daily_pnl_value_label.setStyleSheet(f"color: {WHITE}; font-size: 11px; font-family: 'Consolas';")
        self.daily_pnl_value_label.setFixedWidth(140)
        self.daily_pnl_value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        progress_row.addWidget(self.daily_pnl_value_label)

        layout.addLayout(progress_row)

        return panel

    # =================== META-COGNITION (STAGE 4) ===================
    def _build_meta_cognition_panel(self) -> QWidget:
        """Stage 4: Meta-Cognition & Alpha Hunter - Learning Progress & Alpha Score"""
        panel = QGroupBox("[BRAIN] META-COGNITION (Stage 4 Self-Learning)")
        panel.setStyleSheet(f"""
            QGroupBox {{ color: {CYAN}; font-size: 14px; font-weight: bold; font-family: 'Segoe UI';
                        border: 2px solid {CYAN}; border-radius: 8px; margin-top: 8px; padding-top: 12px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; }}
        """)

        layout = QVBoxLayout(panel)
        layout.setSpacing(10)

        # Row 1: Alpha Score (Big Display)
        alpha_row = QHBoxLayout()
        alpha_row.setSpacing(15)

        # Alpha Score Display
        alpha_layout = QVBoxLayout()
        alpha_label = QLabel("Alpha Score (Learning Progress)")
        alpha_label.setStyleSheet(f"color: {GRAY}; font-size: 10px; font-family: 'Consolas';")
        alpha_layout.addWidget(alpha_label)

        self.alpha_score_label = QLabel("50.0 / 100")
        self.alpha_score_label.setStyleSheet(f"color: {ORANGE}; font-size: 24px; font-weight: bold; font-family: 'Consolas';")
        alpha_layout.addWidget(self.alpha_score_label)
        alpha_row.addLayout(alpha_layout)

        # Alpha Score Progress Bar
        alpha_bar_layout = QVBoxLayout()
        alpha_bar_layout.setSpacing(4)

        # Progress bar frame
        alpha_bar_frame = QFrame()
        alpha_bar_frame.setFixedHeight(22)
        alpha_bar_frame.setStyleSheet(f"background: {BG_INPUT}; border: 1px solid {BORDER}; border-radius: 11px;")
        alpha_bar_layout_inner = QHBoxLayout(alpha_bar_frame)
        alpha_bar_layout_inner.setContentsMargins(2, 2, 2, 2)
        alpha_bar_layout_inner.setSpacing(0)

        # Progress fill
        self.alpha_bar = QFrame()
        self.alpha_bar.setFixedHeight(18)
        self.alpha_bar.setStyleSheet(f"background: {ORANGE}; border-radius: 9px;")
        self.alpha_bar.setMinimumWidth(4)
        alpha_bar_layout_inner.addWidget(self.alpha_bar, stretch=1)

        alpha_bar_layout.addWidget(alpha_bar_frame)

        # Score description
        self.alpha_desc_label = QLabel("Learning in progress...")
        self.alpha_desc_label.setStyleSheet(f"color: {GRAY}; font-size: 10px; font-family: 'Consolas';")
        self.alpha_desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        alpha_bar_layout.addWidget(self.alpha_desc_label)

        alpha_row.addLayout(alpha_bar_layout, stretch=1)
        layout.addLayout(alpha_row)

        # Row 2: Best/Worst Asset, Best Timeframe
        stats_row = QHBoxLayout()
        stats_row.setSpacing(15)

        # Best Asset
        best_asset_layout = QVBoxLayout()
        best_asset_label = QLabel("Best Performer")
        best_asset_label.setStyleSheet(f"color: {GRAY}; font-size: 10px; font-family: 'Consolas';")
        best_asset_layout.addWidget(best_asset_label)

        self.best_asset_label = QLabel("N/A")
        self.best_asset_label.setStyleSheet(f"color: {GREEN}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")
        best_asset_layout.addWidget(self.best_asset_label)
        stats_row.addLayout(best_asset_layout)

        # Worst Asset
        worst_asset_layout = QVBoxLayout()
        worst_asset_label = QLabel("Worst Performer")
        worst_asset_label.setStyleSheet(f"color: {GRAY}; font-size: 10px; font-family: 'Consolas';")
        worst_asset_layout.addWidget(worst_asset_label)

        self.worst_asset_label = QLabel("N/A")
        self.worst_asset_label.setStyleSheet(f"color: {RED}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")
        worst_asset_layout.addWidget(self.worst_asset_label)
        stats_row.addLayout(worst_asset_layout)

        # Best Timeframe
        tf_layout = QVBoxLayout()
        tf_label = QLabel("Best Timeframe")
        tf_label.setStyleSheet(f"color: {GRAY}; font-size: 10px; font-family: 'Consolas';")
        tf_layout.addWidget(tf_label)

        self.best_timeframe_label = QLabel("N/A")
        self.best_timeframe_label.setStyleSheet(f"color: {CYAN}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")
        tf_layout.addWidget(self.best_timeframe_label)
        stats_row.addLayout(tf_layout)

        layout.addLayout(stats_row)

        # Row 3: Review Stats
        review_row = QHBoxLayout()
        review_row.setSpacing(15)

        # Total Reviews
        reviews_layout = QVBoxLayout()
        reviews_label = QLabel("Total Reviews")
        reviews_label.setStyleSheet(f"color: {GRAY}; font-size: 10px; font-family: 'Consolas';")
        reviews_layout.addWidget(reviews_label)

        self.total_reviews_label = QLabel("0")
        self.total_reviews_label.setStyleSheet(f"color: {WHITE}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")
        reviews_layout.addWidget(self.total_reviews_label)
        review_row.addLayout(reviews_layout)

        # Adjustments Made
        adjustments_layout = QVBoxLayout()
        adjustments_label = QLabel("Adjustments Made")
        adjustments_label.setStyleSheet(f"color: {GRAY}; font-size: 10px; font-family: 'Consolas';")
        adjustments_layout.addWidget(adjustments_label)

        self.total_adjustments_label = QLabel("0")
        self.total_adjustments_label.setStyleSheet(f"color: {WHITE}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")
        adjustments_layout.addWidget(self.total_adjustments_label)
        review_row.addLayout(adjustments_layout)

        # Success Rate
        success_layout = QVBoxLayout()
        success_label = QLabel("Adjustment Success Rate")
        success_label.setStyleSheet(f"color: {GRAY}; font-size: 10px; font-family: 'Consolas';")
        success_layout.addWidget(success_label)

        self.adjustment_success_rate_label = QLabel("0.0%")
        self.adjustment_success_rate_label.setStyleSheet(f"color: {GREEN}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")
        success_layout.addWidget(self.adjustment_success_rate_label)
        review_row.addLayout(success_layout)

        # Next Review
        next_review_layout = QVBoxLayout()
        next_review_label = QLabel("Next Review In")
        next_review_label.setStyleSheet(f"color: {GRAY}; font-size: 10px; font-family: 'Consolas';")
        next_review_layout.addWidget(next_review_label)

        self.next_review_label = QLabel("24.0h")
        self.next_review_label.setStyleSheet(f"color: {CYAN}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")
        next_review_layout.addWidget(self.next_review_label)
        review_row.addLayout(next_review_layout)

        layout.addLayout(review_row)

        return panel

    def update_meta_cognition(self, data: Dict):
        """Update Meta-Cognition panel with learning progress data."""
        # Alpha Score
        alpha_score = data.get("alpha_score", 50.0)
        self.alpha_score_label.setText(f"{alpha_score:.1f} / 100")

        # Color based on alpha score
        if alpha_score >= 70:
            alpha_color = GREEN
            alpha_desc = "Excellent learning [SUCCESS]"
        elif alpha_score >= 50:
            alpha_color = ORANGE
            alpha_desc = "Learning in progress..."
        else:
            alpha_color = RED
            alpha_desc = "Needs improvement [WARN]"

        self.alpha_score_label.setStyleSheet(f"color: {alpha_color}; font-size: 24px; font-weight: bold; font-family: 'Consolas';")
        self.alpha_desc_label.setText(alpha_desc)

        # Update alpha bar width
        bar_width = int(alpha_score * 3)  # Max 300px
        self.alpha_bar.setMinimumWidth(max(4, bar_width))
        self.alpha_bar.setStyleSheet(f"background: {alpha_color}; border-radius: 9px;")

        # Best/Worst Assets
        best_asset = data.get("best_asset", "N/A")
        worst_asset = data.get("worst_asset", "N/A")
        best_timeframe = data.get("best_timeframe", "N/A")

        self.best_asset_label.setText(best_asset)
        self.worst_asset_label.setText(worst_asset)
        self.best_timeframe_label.setText(best_timeframe)

        # Review Stats
        total_reviews = data.get("total_reviews", 0)
        total_adjustments = data.get("total_adjustments", 0)
        adjustment_success_rate = data.get("adjustment_success_rate", 0.0)
        next_review_hours = data.get("next_review_in_hours", 24.0)

        self.total_reviews_label.setText(str(total_reviews))
        self.total_adjustments_label.setText(str(total_adjustments))
        self.adjustment_success_rate_label.setText(f"{adjustment_success_rate:.1%}")
        self.next_review_label.setText(f"{next_review_hours:.1f}h")

        # Color the adjustment success rate
        if adjustment_success_rate >= 0.7:
            self.adjustment_success_rate_label.setStyleSheet(f"color: {GREEN}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")
        elif adjustment_success_rate >= 0.5:
            self.adjustment_success_rate_label.setStyleSheet(f"color: {ORANGE}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")
        else:
            self.adjustment_success_rate_label.setStyleSheet(f"color: {RED}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")

    def update_institutional_governor(self, data: Dict):
        """Update Institutional Governor panel with risk data."""
        # Total Exposure
        total_exposure = data.get("total_exposure_pct", 0.0)
        max_exposure = data.get("max_total_exposure_pct", 15.0)
        self.total_exposure_label.setText(f"{total_exposure:.1f}% / {max_exposure:.1f}%")
        
        # Color based on exposure level
        if total_exposure > max_exposure * 0.8:
            self.total_exposure_label.setStyleSheet(f"color: {RED}; font-size: 16px; font-weight: bold; font-family: 'Consolas';")
        elif total_exposure > max_exposure * 0.5:
            self.total_exposure_label.setStyleSheet(f"color: {ORANGE}; font-size: 16px; font-weight: bold; font-family: 'Consolas';")
        else:
            self.total_exposure_label.setStyleSheet(f"color: {GREEN}; font-size: 16px; font-weight: bold; font-family: 'Consolas';")

        # Correlation Risk
        avg_corr = data.get("avg_correlation", 0.0)
        if avg_corr > 0.85:
            corr_status = "HIGH"
            corr_color = RED
        elif avg_corr > 0.70:
            corr_status = "MEDIUM"
            corr_color = ORANGE
        else:
            corr_status = "SAFE"
            corr_color = GREEN
        
        self.correlation_risk_label.setText(f"{avg_corr:.2f} ({corr_status})")
        self.correlation_risk_label.setStyleSheet(f"color: {corr_color}; font-size: 16px; font-weight: bold; font-family: 'Consolas';")

        # Next News Event
        next_event = data.get("next_event", "None")
        time_to_event = data.get("time_to_event", "N/A")
        if next_event and next_event != "None":
            self.next_news_label.setText(f"{next_event}\n({time_to_event})")
        else:
            self.next_news_label.setText("None")

        # RPA Status
        rpa_enabled = data.get("rpa_enabled", True)
        if rpa_enabled:
            self.rpa_status_label.setText("[OK] ENABLED")
            self.rpa_status_label.setStyleSheet(f"color: {GREEN}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")
        else:
            self.rpa_status_label.setText("[STOP] PAUSED")
            self.rpa_status_label.setStyleSheet(f"color: {RED}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")

        # Market Sentiment Banner
        sentiment_label = data.get("sentiment_label", "NEUTRAL - Mixed Signals")
        market_context = data.get("market_context", "")
        headline_count = data.get("headline_count", 0)
        if hasattr(self, "market_sentiment_banner"):
            banner_text = f"[SAT] {sentiment_label}"
            if market_context:
                banner_text += f" | {market_context}"
            if headline_count > 0:
                banner_text += f" ({headline_count} headlines)"
            self.market_sentiment_banner.setText(banner_text)
            # Color based on sentiment
            if "CAUTION" in sentiment_label:
                banner_color = RED
            elif "BEARISH" in sentiment_label:
                banner_color = ORANGE
            elif "BULLISH" in sentiment_label:
                banner_color = GREEN
            else:
                banner_color = CYAN
            self.market_sentiment_banner.setStyleSheet(
                f"QLabel {{ color: {banner_color}; font-size: 11px; font-weight: bold; font-family: 'Consolas';"
                f"background: {BG_INPUT}; border: 1px solid {banner_color}; border-radius: 6px; padding: 6px; }}"
            )

        # Walk Away Protocol
        walk_away_active = data.get("walk_away_can_trade", True)
        if walk_away_active:
            self.walk_away_label.setText("[OK] ACTIVE")
            self.walk_away_label.setStyleSheet(f"color: {GREEN}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")
        else:
            remaining = data.get("walk_away_remaining_hours", 0)
            self.walk_away_label.setText(f"[WALK] SHUTDOWN ({remaining:.1f}h)")
            self.walk_away_label.setStyleSheet(f"color: {RED}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")

        # Profit Lock Status
        stops_locked = data.get("stops_locked", False)
        if stops_locked:
            self.profit_lock_label.setText("[LOCK] LOCKED")
            self.profit_lock_label.setStyleSheet(f"color: {GREEN}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")
        else:
            self.profit_lock_label.setText("UNLOCKED")
            self.profit_lock_label.setStyleSheet(f"color: {GRAY}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")

        # Daily P&L Progress Bar
        daily_pnl_pct = data.get("daily_pnl_pct", 0.0)
        daily_pnl_dollars = data.get("daily_pnl_dollars", 0.0)
        progress_to_target = min(100, max(0, data.get("progress_to_target", 0)))
        
        # Update bar width
        bar_width = int(progress_to_target * 3)  # Max 300px
        self.daily_pnl_bar.setMinimumWidth(max(4, bar_width))
        
        # Color based on P&L
        if daily_pnl_pct >= 0:
            self.daily_pnl_bar.setStyleSheet(f"background: {GREEN}; border-radius: 7px;")
            self.daily_pnl_value_label.setText(f"+${daily_pnl_dollars:.2f} (+{daily_pnl_pct:.2f}%)")
            self.daily_pnl_value_label.setStyleSheet(f"color: {GREEN}; font-size: 11px; font-family: 'Consolas';")
        else:
            self.daily_pnl_bar.setStyleSheet(f"background: {RED}; border-radius: 7px;")
            self.daily_pnl_value_label.setText(f"-${abs(daily_pnl_dollars):.2f} ({daily_pnl_pct:.2f}%)")
            self.daily_pnl_value_label.setStyleSheet(f"color: {RED}; font-size: 11px; font-family: 'Consolas';")

    # =================== KILL SWITCH ===================
    def _build_kill_switch(self) -> QWidget:
        """Emergency Stop Button with Reset"""
        panel = QFrame()
        panel.setStyleSheet(f"background: {BG_PANEL}; border: 2px solid {RED}; border-radius: 8px; padding: 12px;")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        warning = QLabel("[WARN] EMERGENCY:")
        warning.setStyleSheet(f"color: {RED}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")
        layout.addWidget(warning)

        kill_btn = QPushButton("[STOP] KILL SWITCH - STOP ALL TRADING")
        kill_btn.setMinimumHeight(44)
        kill_btn.setStyleSheet(f"""
            QPushButton {{ background: {RED}; color: {WHITE}; border: none; border-radius: 8px;
                         font-size: 14px; font-weight: bold; font-family: 'Consolas'; padding: 10px; }}
            QPushButton:hover {{ background: #ff4444; }}
            QPushButton:pressed {{ background: #cc0000; }}
        """)
        kill_btn.clicked.connect(self._on_kill_switch)
        layout.addWidget(kill_btn, stretch=1)

        # Reset button
        self.reset_kill_btn = QPushButton("[REFRESH] RESET")
        self.reset_kill_btn.setMinimumHeight(44)
        self.reset_kill_btn.setFixedWidth(120)
        self.reset_kill_btn.setStyleSheet(f"""
            QPushButton {{ background: {GREEN}; color: {BG_DARK}; border: none; border-radius: 8px;
                         font-size: 14px; font-weight: bold; font-family: 'Consolas'; padding: 10px; }}
            QPushButton:hover {{ background: #2ea043; }}
            QPushButton:disabled {{ background: {GRAY}; color: {DIM}; }}
        """)
        self.reset_kill_btn.clicked.connect(self._on_reset_kill_switch)
        self.reset_kill_btn.setEnabled(False)  # Disabled until kill switch is triggered
        layout.addWidget(self.reset_kill_btn)

        return panel

    # =================== HELPER METHODS ===================

    def log(self, message: str):
        """Add message to activity log."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.activity_log.append(f'<span style="color:{DIM}">[{timestamp}]</span> {message}')
        # Auto-scroll
        cursor = self.activity_log.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.activity_log.setTextCursor(cursor)

    def update_balance(
        self,
        balance: float,
        equity: float,
        daily_pnl: float,
        total_pnl: float,
        drawdown: float | None = None,
        drawdown_pct: float | None = None,
        trades_today: int | None = None,
    ):
        """Update account balance dashboard."""
        self.balance_label.setText(f"${balance:,.2f}")
        self.equity_label.setText(f"${equity:,.2f}")

        # Daily P&L with color
        self.daily_pnl_label.setText(f"${daily_pnl:,.2f}")
        self.daily_pnl_label.setStyleSheet(f"""
            color: {GREEN if daily_pnl >= 0 else RED}; font-size: 22px; font-weight: bold;
            font-family: 'Consolas';
        """)

        # Total P&L with color
        self.total_pnl_label.setText(f"${total_pnl:,.2f}")
        self.total_pnl_label.setStyleSheet(f"""
            color: {GREEN if total_pnl >= 0 else RED}; font-size: 16px; font-weight: bold;
            font-family: 'Consolas';
        """)

        if drawdown is not None:
            dd_pct = float(drawdown_pct or 0.0)
            self.drawdown_label.setText(f"${abs(drawdown):,.2f} ({dd_pct:.1f}%)")

        if trades_today is not None:
            self.trades_today_label.setText(str(int(trades_today)))

    def update_positions(self, positions: List[Dict]):
        """Update live positions table with HIGH-CONTRAST BUY/SELL colors."""
        self.positions_table.setRowCount(len(positions))
        
        for i, pos in enumerate(positions):
            items = [
                pos.get("asset", ""),
                pos.get("side", ""),
                f"${pos.get('entry', 0):.2f}",
                f"${pos.get('current', 0):.2f}",
                f"${pos.get('pnl', 0):.2f}",
                f"{pos.get('pnl_pct', 0):.2f}%",
                f"${pos.get('tp', 0):.2f}",
                f"${pos.get('sl', 0):.2f}",
            ]
            
            for j, text in enumerate(items):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                
                # HIGH-CONTRAST: Side column (BUY=Neon Green, SELL=Bright Red)
                if j == 1:
                    side = pos.get("side", "").upper()
                    if "BUY" in side or "LONG" in side:
                        item.setForeground(QColor(NEON_GREEN))
                        item.setFont(QFont("Consolas", 12, QFont.Weight.Bold))
                    elif "SELL" in side or "SHORT" in side:
                        item.setForeground(QColor(BRIGHT_RED))
                        item.setFont(QFont("Consolas", 12, QFont.Weight.Bold))
                
                # Color P&L columns with high contrast
                elif j == 4 or j == 5:
                    pnl = pos.get('pnl', 0)
                    if pnl > 0:
                        item.setForeground(QColor(NEON_GREEN))
                    elif pnl < 0:
                        item.setForeground(QColor(BRIGHT_RED))
                
                self.positions_table.setItem(i, j, item)

    def add_trade_log(self, asset: str, action: str, amount: float, pnl: float = 0, status: str = "Open"):
        """Add entry to trade log with HIGH-CONTRAST BUY/SELL colors."""
        row = self.trade_log_table.rowCount()
        self.trade_log_table.insertRow(row)

        time_str = datetime.now().strftime("%H:%M:%S")
        pnl_str = f"${pnl:.2f}" if pnl != 0 else "-"

        items = [time_str, asset, action, f"${amount:.2f}", pnl_str, status]
        for j, text in enumerate(items):
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            # HIGH-CONTRAST: Action column (BUY=Neon Green, SELL=Bright Red)
            if j == 2:
                action_upper = action.upper()
                if "BUY" in action_upper or "LONG" in action_upper:
                    item.setForeground(QColor(NEON_GREEN))
                    item.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
                elif "SELL" in action_upper or "SHORT" in action_upper:
                    item.setForeground(QColor(BRIGHT_RED))
                    item.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
            
            # Status column with high contrast
            elif j == 5:
                if status == "Closed - Profit":
                    item.setForeground(QColor(NEON_GREEN))
                    item.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
                elif status == "Closed - Loss":
                    item.setForeground(QColor(BRIGHT_RED))
                    item.setFont(QFont("Consolas", 11, QFont.Weight.Bold))

            self.trade_log_table.setItem(row, j, item)

    def update_safety_status(self, safety_data: dict):
        """Update safety status indicator in dashboard."""
        # Log safety status updates
        mode = safety_data.get("position_mode", "normal")
        paused = safety_data.get("trading_paused", False)
        pause_reason = safety_data.get("pause_reason", "")
        
        if paused:
            self.log(f"[STOP] Trading paused: {pause_reason}")
        elif mode != "normal":
            multiplier = safety_data.get("position_multiplier", 1.0)
            self.log(f"[RULER] Position mode: {mode} ({multiplier:.0%} size)")

    # =================== EVENT HANDLERS ===================

    def _set_teacher_mode(self):
        self._mode = "TEACHER"
        self.btn_teacher.setChecked(True)
        self.btn_auto.setChecked(False)
        # Update button styles to show active state
        self.btn_teacher.setStyleSheet(f"""
            QPushButton {{ background: {CYAN}; color: {BG_DARK}; border: none; border-radius: 6px;
                         font-size: 11px; font-weight: bold; font-family: 'Consolas'; padding: 8px; }}
        """)
        self.btn_auto.setStyleSheet(f"""
            QPushButton {{ background: {BG_INPUT}; color: {GRAY}; border: 1px solid {BORDER}; border-radius: 6px;
                         font-size: 11px; font-weight: bold; font-family: 'Consolas'; padding: 8px; }}
        """)
        self.mode_badge.setText("TEACHER MODE")
        self.mode_badge.setStyleSheet(
            f"color: {CYAN}; background: {BG_INPUT}; padding: 6px 14px; border-radius: 6px; "
            f"font-size: 12px; font-weight: bold; font-family: 'Consolas';"
        )
        self.mode_changed.emit("TEACHER")
        self.log("[EMOJI] Switched to TEACHER MODE - Manual approval required")

    def _set_autonomous_mode(self):
        self._mode = "AUTONOMOUS"
        self.btn_auto.setChecked(True)
        self.btn_teacher.setChecked(False)
        # Update button styles to show active state
        self.btn_auto.setStyleSheet(f"""
            QPushButton {{ background: {GREEN}; color: {BG_DARK}; border: none; border-radius: 6px;
                         font-size: 11px; font-weight: bold; font-family: 'Consolas'; padding: 8px; }}
        """)
        self.btn_teacher.setStyleSheet(f"""
            QPushButton {{ background: {BG_INPUT}; color: {GRAY}; border: 1px solid {BORDER}; border-radius: 6px;
                         font-size: 11px; font-weight: bold; font-family: 'Consolas'; padding: 8px; }}
        """)
        self.mode_badge.setText("AUTONOMOUS MODE")
        self.mode_badge.setStyleSheet(
            f"color: {GREEN}; background: {BG_INPUT}; padding: 6px 14px; border-radius: 6px; "
            f"font-size: 12px; font-weight: bold; font-family: 'Consolas';"
        )
        self.mode_changed.emit("AUTONOMOUS")
        self.log("[ROBOT] Switched to AUTONOMOUS MODE - Auto-execution enabled")

    def _add_ticker(self):
        ticker = settings_manager.normalize_ticker(self.ticker_input.text())
        
        # Validate ticker is not empty
        if not ticker:
            self.log("[WARN] Please enter a valid ticker symbol")
            return
            
        current_watchlist = self._collect_watchlist_from_inputs()

        if ticker not in current_watchlist:
            empty_slot = next((slot for slot in self.watchlist_slots if not slot.text().strip()), None)
            if empty_slot is None:
                self.log("[WARN] All 10 watchlist slots are full")
                return
            empty_slot.setText(ticker)
            self._sync_watchlist_from_inputs()
            self.ticker_input.clear()
            self.log(f"[EMOJI] Added {ticker} to watchlist")
        elif ticker in current_watchlist:
            self.log(f"[WARN] {ticker} already in watchlist")

    def _remove_ticker(self):
        selected = self.watchlist_table.selectedItems()
        if not selected:
            self.log("[WARN] Select a ticker to remove")
            return
        
        row = selected[0].row()
        ticker = self.watchlist_table.item(row, 1).text()
        
        if ticker in self._collect_watchlist_from_inputs():
            for slot in self.watchlist_slots:
                if slot.text().strip().upper() == ticker:
                    slot.clear()
                    break
            self._sync_watchlist_from_inputs()
            self.log(f"[EMOJI] Removed {ticker} from watchlist")

    def _queue_watchlist_sync(self):
        """Debounce rapid text edits from the 10 dashboard slots."""
        self.watchlist_sync_timer.start(150)

    def _apply_watchlist_to_inputs(self, watchlist: List[str]):
        """Normalize slot contents so active tickers are packed from top to bottom."""
        for index, slot in enumerate(self.watchlist_slots):
            new_value = watchlist[index] if index < len(watchlist) else ""
            if slot.text() == new_value:
                continue
            was_blocked = slot.blockSignals(True)
            slot.setText(new_value)
            slot.blockSignals(was_blocked)

    def _collect_watchlist_from_inputs(self) -> List[str]:
        """Read the 10 dashboard slots into a normalized watchlist."""
        watchlist = []
        seen = set()
        for slot in self.watchlist_slots:
            ticker = settings_manager.normalize_ticker(slot.text())
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            watchlist.append(ticker)
        return watchlist

    def _sync_watchlist_from_inputs(self):
        """Push live slot contents into current dashboard watchlist immediately."""
        self.watchlist = self._collect_watchlist_from_inputs()
        self.watchlist_row_state = {
            ticker: state for ticker, state in self.watchlist_row_state.items() if ticker in self.watchlist
        }
        settings_manager.update({"session_watchlist": list(self.watchlist)})
        self._apply_watchlist_to_inputs(self.watchlist)
        self._refresh_watchlist()
        self._update_analysis_option_visibility()
        self.watchlist_updated.emit(self.watchlist)

    def _refresh_watchlist(self):
        self.watchlist_table.setRowCount(len(self.watchlist))
        for i, ticker in enumerate(self.watchlist):
            row_state = self.watchlist_row_state.get(ticker, {})
            confidence = float(row_state.get("confidence", 0.0) or 0.0)
            last_signal = str(row_state.get("last_signal", "-") or "-")
            if confidence > 0 and last_signal != "-":
                signal_text = f"{last_signal} ({confidence:.0%})"
                signal_color = GREEN if confidence >= 0.90 else CYAN if confidence >= 0.75 else ORANGE
            else:
                signal_text = last_signal
                signal_color = GRAY
            status_text = str(row_state.get("status_text", "Monitoring") or "Monitoring")
            status_color = QColor(str(row_state.get("status_color", CYAN)))

            # Checkbox
            check = QTableWidgetItem("[OK]")
            check.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.watchlist_table.setItem(i, 0, check)
            
            # Ticker
            ticker_item = QTableWidgetItem(ticker)
            ticker_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.watchlist_table.setItem(i, 1, ticker_item)
            
            # Last Signal (default)
            signal_item = QTableWidgetItem(signal_text)
            signal_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            signal_item.setForeground(QColor(signal_color))
            self.watchlist_table.setItem(i, 2, signal_item)
            
            # Status
            status_item = QTableWidgetItem(status_text)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_item.setForeground(status_color)
            self.watchlist_table.setItem(i, 3, status_item)

            self._apply_watchlist_row_glow(i, confidence >= 0.90, pulse_now=self._confidence_glow_on)

    def _pulse_confidence_rows(self):
        """Pulse high-confidence rows in neon green for fast visual triage."""
        self._confidence_glow_on = not self._confidence_glow_on
        for row, ticker in enumerate(self.watchlist):
            confidence = float(self.watchlist_row_state.get(ticker, {}).get("confidence", 0.0) or 0.0)
            self._apply_watchlist_row_glow(row, confidence >= 0.90, pulse_now=self._confidence_glow_on)

    def _apply_watchlist_row_glow(self, row: int, high_confidence: bool, pulse_now: bool):
        """Apply or clear the neon row highlight for a watchlist entry."""
        active_bg = QColor(57, 255, 20, 92 if pulse_now else 36)
        resting_bg = QColor(0, 0, 0, 0)
        for column in range(self.watchlist_table.columnCount()):
            item = self.watchlist_table.item(row, column)
            if item is None:
                continue
            item.setBackground(active_bg if high_confidence else resting_bg)

    def _update_analysis_option_visibility(self):
        """Keep the analysis option strip visible whenever dashboard slots contain tickers."""
        has_watchlist = bool(self.watchlist)
        for name, label in self.analysis_option_labels.items():
            state = "ON" if has_watchlist else "STANDBY"
            label.setText(f"{name}: {state}")
            label.setVisible(True)
        if has_watchlist:
            self.analysis_visibility_label.setText(f"Dashboard live: {len(self.watchlist)} ticker(s)")
            self.analysis_visibility_label.setStyleSheet(f"color: {CYAN}; font-size: 10px; font-family: 'Consolas';")
        else:
            self.analysis_visibility_label.setText("Waiting for watchlist")
            self.analysis_visibility_label.setStyleSheet(f"color: {GRAY}; font-size: 10px; font-family: 'Consolas';")

    def update_watchlist_status(
        self,
        ticker: str,
        status: str,
        confidence: float | None = None,
        last_signal: str | None = None,
    ):
        """Update the watchlist table row with live status text/icon."""
        status_map = {
            "scanning": ("[GREEN] Scanning", QColor(GREEN)),
            "analyzing_liquidity": ("[YELLOW] Analyzing Liquidity", QColor(ORANGE)),
            "trade_rejected": ("[RED] Trade Rejected", QColor(RED)),
            "rsi_veto_overbought": ("[VETO] RSI OVERBOUGHT", QColor(RED)),
            "rsi_veto_oversold": ("[VETO] RSI OVERSOLD", QColor(RED)),
            "awaiting_strike": ("[BOLT] Strike Ready", QColor(GREEN)),
            "executing": ("[LION] Striking", QColor(GREEN)),
            "monitoring": ("Monitoring", QColor(CYAN)),
        }
        label, color = status_map.get(status, (status, QColor(CYAN)))
        state = self.watchlist_row_state.setdefault(ticker, {})
        state["status_text"] = label
        state["status_color"] = color.name()
        if confidence is not None:
            state["confidence"] = float(confidence)
        if last_signal is not None:
            state["last_signal"] = str(last_signal).upper()
        for row in range(self.watchlist_table.rowCount()):
            item = self.watchlist_table.item(row, 1)
            if item and item.text() == ticker:
                signal_item = self.watchlist_table.item(row, 2)
                if signal_item is None:
                    signal_item = QTableWidgetItem("-")
                    signal_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.watchlist_table.setItem(row, 2, signal_item)

                row_confidence = float(state.get("confidence", 0.0) or 0.0)
                row_signal = str(state.get("last_signal", "-") or "-")
                if row_signal != "-" and row_confidence > 0:
                    signal_item.setText(f"{row_signal} ({row_confidence:.0%})")
                    signal_item.setForeground(
                        QColor(GREEN if row_confidence >= 0.90 else CYAN if row_confidence >= 0.75 else ORANGE)
                    )
                else:
                    signal_item.setText(row_signal)
                    signal_item.setForeground(QColor(GRAY))

                status_item = self.watchlist_table.item(row, 3)
                if status_item is None:
                    status_item = QTableWidgetItem()
                    self.watchlist_table.setItem(row, 3, status_item)
                status_item.setText(label)
                status_item.setForeground(color)
                status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._apply_watchlist_row_glow(row, row_confidence >= 0.90, pulse_now=self._confidence_glow_on)
                break

    def update_watchlist_signal(self, ticker: str, signal: str, confidence: float):
        """Update just the signal/confidence portion of a watchlist row."""
        current_status = self.watchlist_row_state.get(ticker, {}).get("status_text", "Monitoring")
        self.update_watchlist_status(ticker, current_status, confidence=confidence, last_signal=signal)

    def _save_settings(self):
        investment = float(self.investment_input.text() or 10)
        max_daily_loss = 150.0 if self.prop_firm_mode_enabled else self.max_loss_input.value()
        manual_max_lots = float(settings_manager.get("lot_size", settings_manager.get("max_lots", 2.0)) or 2.0)
        settings = {
            "investment": investment,
            "investment_amount": investment,
            "take_profit_pct": float(self.tp_input.value()),
            "stop_loss_pct": float(self.sl_input.value()),
            "auto_risk_enabled": self.auto_risk_enabled,
            "max_daily_loss": max_daily_loss,
            "prop_firm_mode": self.prop_firm_mode_enabled,
            "prop_firm_name": self.firm_selector.currentText(),
            "max_lots": 1.0 if self.prop_firm_mode_enabled else manual_max_lots,
            "human_latency": True if self.prop_firm_mode_enabled else bool(settings_manager.get("human_latency", True)),
        }
        settings_manager.update(settings)
        self.settings_changed.emit(settings)
        suffix = " | Prop Firm Mode enforced" if self.prop_firm_mode_enabled else ""
        risk_mode = "AUTO-RISK (structure)" if self.auto_risk_enabled else (
            f"manual TP {self.tp_input.value():.1f}% / SL {self.sl_input.value():.1f}%"
        )
        self.log(f"[EMOJI] Settings saved: ${investment}/trade, {risk_mode}{suffix}")

    def _on_kill_switch(self):
        self._killed = True
        self.kill_switch_triggered.emit()
        self.log("[STOP] KILL SWITCH ACTIVATED - ALL TRADING STOPPED")
        # Don't disable the entire UI - just show reset button
        self.reset_kill_btn.setEnabled(True)
        self.log("[WARN] Use the RESET button to resume trading")

    def _on_reset_kill_switch(self):
        """Reset kill switch and re-enable trading."""
        self._killed = False
        self.reset_kill_btn.setEnabled(False)
        self.log("[OK] Kill switch reset - Trading can resume")

    def _clear_activity_log(self):
        """Clear the activity log."""
        self.activity_log.clear()
        self.log("[EMOJI] Activity log cleared")

    def _export_trade_history(self):
        """Export trade history to CSV file."""
        from PyQt6.QtWidgets import QFileDialog
        import csv

        # Get save file path
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Trade History",
            "trade_history.csv",
            "CSV Files (*.csv);;All Files (*)"
        )

        if not file_path:
            return

        try:
            with open(file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                # Write headers
                headers = []
                for col in range(self.trade_log_table.columnCount()):
                    headers.append(self.trade_log_table.horizontalHeaderItem(col).text())
                writer.writerow(headers)

                # Write data
                for row in range(self.trade_log_table.rowCount()):
                    row_data = []
                    for col in range(self.trade_log_table.columnCount()):
                        item = self.trade_log_table.item(row, col)
                        row_data.append(item.text() if item else "")
                    writer.writerow(row_data)

            self.log(f"[CHART] Trade history exported to: {file_path}")
        except Exception as e:
            self.log(f"[FAIL] Export failed: {e}")
