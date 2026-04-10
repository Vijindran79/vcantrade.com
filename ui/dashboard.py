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

    def __init__(self):
        super().__init__()
        self._mode = "TEACHER"
        self._killed = False
        self.positions = {}  # Live positions tracking
        self.watchlist = config.CLOUD_TICKERS.copy()

        self._setup_window()
        self._build_ui()
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

        title = QLabel("🚀 VcaniTrade AI")
        title.setStyleSheet(f"color: {CYAN}; font-size: 16px; font-weight: bold; font-family: 'Segoe UI';")
        layout.addWidget(title)

        # Transparency slider
        trans_label = QLabel("🔍")
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
        self.pin_btn = QPushButton("📌 PIN")
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

        return container

    def _toggle_always_on_top(self):
        """Toggle window between always-on-top and normal."""
        self._always_on_top = not self._always_on_top
        
        if self._always_on_top:
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
            self.pin_btn.setText("📌 PIN")
            self.pin_btn.setStyleSheet(f"""
                QPushButton {{ background: {CYAN}; color: {BG_DARK}; border: none; border-radius: 4px;
                             font-size: 11px; font-weight: bold; font-family: 'Consolas'; padding: 4px 8px; }}
            """)
            self.log("📌 Dashboard pinned to front")
        else:
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint)
            self.pin_btn.setText("📌 UNPIN")
            self.pin_btn.setStyleSheet(f"""
                QPushButton {{ background: {ORANGE}; color: {BG_DARK}; border: none; border-radius: 4px;
                             font-size: 11px; font-weight: bold; font-family: 'Consolas'; padding: 4px 8px; }}
            """)
            self.log("📌 Dashboard unpinned - can now go behind other windows")
        
        self.show()  # Re-show to apply flag changes

    def _update_transparency(self, value: int):
        """Update window opacity based on slider value."""
        opacity = value / 100.0
        self.setWindowOpacity(opacity)
        if value < 80:
            self.log(f"🔍 Transparency: {100 - value}% (you can see through the dashboard)")

    # =================== ACCOUNT PANEL ===================
    def _build_account_panel(self) -> QWidget:
        """Account Balance, Equity, Daily P&L, Drawdown Tracking"""
        panel = QGroupBox("💰 ACCOUNT (Top Step Funding)")
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
        panel = QGroupBox("🎓 PROP FIRM RULES (The Professor)")
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
        firm_row.addWidget(self.firm_selector)

        self.firm_status = QLabel("✅ COMPLIANT")
        self.firm_status.setStyleSheet(f"color: {GREEN}; font-size: 12px; font-weight: bold; font-family: 'Consolas';")
        firm_row.addWidget(self.firm_status)

        firm_row.addStretch()
        layout.addLayout(firm_row)

        # Compliance bars
        self._add_compliance_bar(layout, "Daily Loss Used", 0.0, 150.0, GREEN, RED)
        self._add_compliance_bar(layout, "Drawdown Used", 0.0, 3000.0, GREEN, RED)
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
        bar_frame.setStyleSheet(f"background: {BG_INPUT}; border: 1px solid {BORDER}; border-radius: 7px;")
        bar_layout = QHBoxLayout(bar_frame)
        bar_layout.setContentsMargins(2, 2, 2, 2)
        bar_layout.setSpacing(0)

        # Progress fill (will be updated dynamically)
        bar = QFrame()
        bar.setFixedHeight(10)
        pct = min(100, (current / max(0.01, limit)) * 100)
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

    def update_prop_firm_compliance(self, data: dict):
        """Update prop firm panel with compliance data."""
        # Update status
        can_trade = data.get("can_trade", True)
        self.firm_status.setText("✅ COMPLIANT" if can_trade else "🛑 BLOCKED")
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
            self.violations_label.setText("⚠️ " + "\n".join(violations))
        else:
            self.violations_label.setText("")

    # =================== CONTROL PANEL ===================
    def _build_control_panel(self) -> QWidget:
        """Trading Controls: Mode, Investment, Risk Settings"""
        panel = QGroupBox("⚙️ TRADING CONTROLS")
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

        self.btn_teacher = QPushButton("👨‍🏫 TEACHER (Approve Each)")
        self.btn_teacher.setCheckable(True)
        self.btn_teacher.setChecked(True)
        self.btn_teacher.setMinimumHeight(36)
        self.btn_teacher.setStyleSheet(f"""
            QPushButton {{ background: {CYAN}; color: {BG_DARK}; border: none; border-radius: 6px;
                         font-size: 11px; font-weight: bold; font-family: 'Consolas'; padding: 8px; }}
        """)
        self.btn_teacher.clicked.connect(self._set_teacher_mode)
        mode_row.addWidget(self.btn_teacher)

        self.btn_auto = QPushButton("🤖 AUTONOMOUS (Auto)")
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

        inv_label = QLabel("💵 Default Investment ($):")
        inv_label.setStyleSheet(f"color: {WHITE}; font-size: 12px; font-weight: bold; font-family: 'Consolas';")
        invest_row.addWidget(inv_label)

        self.investment_input = QLineEdit("10")
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
        
        self.tp_input = QDoubleSpinBox()
        self.tp_input.setRange(0.1, 50.0)
        self.tp_input.setValue(2.0)
        self.tp_input.setSuffix("%")
        self.tp_input.setStyleSheet(f"""
            QDoubleSpinBox {{ background: {BG_INPUT}; color: {GREEN}; border: 1px solid {BORDER};
                           border-radius: 6px; padding: 6px; font-size: 13px; font-weight: bold;
                           font-family: 'Consolas'; }}
        """)
        tp_layout.addWidget(self.tp_input)
        risk_row.addLayout(tp_layout)

        # Stop Loss %
        sl_layout = QVBoxLayout()
        sl_label = QLabel("Stop Loss (%)")
        sl_label.setStyleSheet(f"color: {RED}; font-size: 11px; font-family: 'Consolas';")
        sl_layout.addWidget(sl_label)
        
        self.sl_input = QDoubleSpinBox()
        self.sl_input.setRange(0.1, 20.0)
        self.sl_input.setValue(1.0)
        self.sl_input.setSuffix("%")
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
        self.max_loss_input.setValue(500)
        self.max_loss_input.setPrefix("$")
        self.max_loss_input.setStyleSheet(f"""
            QDoubleSpinBox {{ background: {BG_INPUT}; color: {ORANGE}; border: 1px solid {BORDER};
                           border-radius: 6px; padding: 6px; font-size: 13px; font-weight: bold;
                           font-family: 'Consolas'; }}
        """)
        msl_layout.addWidget(self.max_loss_input)
        risk_row.addLayout(msl_layout)

        layout.addLayout(risk_row)

        # Save Settings Button
        save_btn = QPushButton("💾 Save Settings")
        save_btn.setMinimumHeight(36)
        save_btn.setStyleSheet(f"""
            QPushButton {{ background: {GREEN}; color: {BG_DARK}; border: none; border-radius: 6px;
                         font-size: 13px; font-weight: bold; font-family: 'Consolas'; padding: 8px; }}
            QPushButton:hover {{ background: #2ea043; }}
        """)
        save_btn.clicked.connect(self._save_settings)
        layout.addWidget(save_btn)

        return panel

    # =================== WATCHLIST PANEL ===================
    def _build_watchlist_panel(self) -> QWidget:
        """Watchlist Management - Add/Remove Tickers to Monitor"""
        panel = QGroupBox("📊 WATCHLIST (Monitored Instruments)")
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

        add_btn = QPushButton("➕ Add")
        add_btn.setMinimumHeight(34)
        add_btn.setStyleSheet(f"""
            QPushButton {{ background: {CYAN}; color: {BG_DARK}; border: none; border-radius: 6px;
                         font-size: 12px; font-weight: bold; font-family: 'Consolas'; padding: 6px 16px; }}
            QPushButton:hover {{ background: #00b8e6; }}
        """)
        add_btn.clicked.connect(self._add_ticker)
        add_row.addWidget(add_btn)

        remove_btn = QPushButton("➖ Remove Selected")
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

        # Watchlist table
        self.watchlist_table = QTableWidget()
        self.watchlist_table.setColumnCount(4)
        self.watchlist_table.setHorizontalHeaderLabels(["✓", "Ticker", "Last Signal", "Status"])
        self.watchlist_table.horizontalHeader().setStretchLastSection(True)
        self.watchlist_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
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
        panel = QGroupBox("📈 LIVE POSITIONS (Auto-Monitored)")
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
        self.positions_table.horizontalHeader().setStretchLastSection(True)
        self.positions_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
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
        panel = QGroupBox("📜 TRADE LOG & ACTIVITY")
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
        self.trade_log_table.horizontalHeader().setStretchLastSection(True)
        self.trade_log_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
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

        return panel

    # =================== KILL SWITCH ===================
    def _build_kill_switch(self) -> QWidget:
        """Emergency Stop Button"""
        panel = QFrame()
        panel.setStyleSheet(f"background: {BG_PANEL}; border: 2px solid {RED}; border-radius: 8px; padding: 12px;")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(12, 8, 12, 8)

        warning = QLabel("⚠️ EMERGENCY:")
        warning.setStyleSheet(f"color: {RED}; font-size: 14px; font-weight: bold; font-family: 'Consolas';")
        layout.addWidget(warning)

        kill_btn = QPushButton("🛑 KILL SWITCH - STOP ALL TRADING")
        kill_btn.setMinimumHeight(44)
        kill_btn.setStyleSheet(f"""
            QPushButton {{ background: {RED}; color: {WHITE}; border: none; border-radius: 8px;
                         font-size: 14px; font-weight: bold; font-family: 'Consolas'; padding: 10px; }}
            QPushButton:hover {{ background: #ff4444; }}
            QPushButton:pressed {{ background: #cc0000; }}
        """)
        kill_btn.clicked.connect(self._on_kill_switch)
        layout.addWidget(kill_btn)

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

    def update_balance(self, balance: float, equity: float, daily_pnl: float, total_pnl: float):
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

    def update_positions(self, positions: List[Dict]):
        """Update live positions table."""
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
                
                # Color P&L columns
                if j == 4 or j == 5:
                    pnl = pos.get('pnl', 0)
                    item.setForeground(QColor(GREEN if pnl >= 0 else RED))
                
                self.positions_table.setItem(i, j, item)

    def add_trade_log(self, asset: str, action: str, amount: float, pnl: float = 0, status: str = "Open"):
        """Add entry to trade log."""
        row = self.trade_log_table.rowCount()
        self.trade_log_table.insertRow(row)
        
        time_str = datetime.now().strftime("%H:%M:%S")
        pnl_str = f"${pnl:.2f}" if pnl != 0 else "-"
        
        items = [time_str, asset, action, f"${amount:.2f}", pnl_str, status]
        for j, text in enumerate(items):
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            
            if j == 5:  # Status column
                if status == "Closed - Profit":
                    item.setForeground(QColor(GREEN))
                elif status == "Closed - Loss":
                    item.setForeground(QColor(RED))
            
            self.trade_log_table.setItem(row, j, item)

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
        self.log("👨‍🏫 Switched to TEACHER MODE - Manual approval required")

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
        self.log("🤖 Switched to AUTONOMOUS MODE - Auto-execution enabled")

    def _add_ticker(self):
        ticker = self.ticker_input.text().strip().upper()
        if ticker and ticker not in self.watchlist:
            self.watchlist.append(ticker)
            self._refresh_watchlist()
            self.watchlist_updated.emit(self.watchlist)
            self.log(f"➕ Added {ticker} to watchlist")
            self.ticker_input.clear()
        elif ticker in self.watchlist:
            self.log(f"⚠️ {ticker} already in watchlist")

    def _remove_ticker(self):
        selected = self.watchlist_table.selectedItems()
        if not selected:
            self.log("⚠️ Select a ticker to remove")
            return
        
        row = selected[0].row()
        ticker = self.watchlist_table.item(row, 1).text()
        
        if ticker in self.watchlist:
            self.watchlist.remove(ticker)
            self._refresh_watchlist()
            self.watchlist_updated.emit(self.watchlist)
            self.log(f"➖ Removed {ticker} from watchlist")

    def _refresh_watchlist(self):
        self.watchlist_table.setRowCount(len(self.watchlist))
        for i, ticker in enumerate(self.watchlist):
            # Checkbox
            check = QTableWidgetItem("✅")
            check.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.watchlist_table.setItem(i, 0, check)
            
            # Ticker
            ticker_item = QTableWidgetItem(ticker)
            ticker_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.watchlist_table.setItem(i, 1, ticker_item)
            
            # Last Signal (default)
            signal_item = QTableWidgetItem("-")
            signal_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            signal_item.setForeground(QColor(GRAY))
            self.watchlist_table.setItem(i, 2, signal_item)
            
            # Status
            status_item = QTableWidgetItem("Monitoring")
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_item.setForeground(QColor(CYAN))
            self.watchlist_table.setItem(i, 3, status_item)

    def _save_settings(self):
        settings = {
            "investment": float(self.investment_input.text() or 10),
            "take_profit_pct": self.tp_input.value(),
            "stop_loss_pct": self.sl_input.value(),
            "max_daily_loss": self.max_loss_input.value(),
        }
        self.settings_changed.emit(settings)
        self.log(f"💾 Settings saved: ${settings['investment']}/trade, TP={settings['take_profit_pct']}%, SL={settings['stop_loss_pct']}%")

    def _on_kill_switch(self):
        self._killed = True
        self.kill_switch_triggered.emit()
        self.log("🛑 KILL SWITCH ACTIVATED - ALL TRADING STOPPED")
        self.setEnabled(False)
