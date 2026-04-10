"""
VcanTrade AI - Signal Approval Dialog

When a trading signal is detected in TEACHER mode, this dialog asks:
1. Investment mode: Dollar amount OR Lots/Units
2. Approve or reject the trade?

In AUTONOMOUS mode, this dialog is NEVER shown - trades execute automatically.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QRadioButton,
    QButtonGroup,
)


class SignalApprovalDialog(QDialog):
    """Dialog that shows trade signal and asks for approval + investment amount."""

    approved = pyqtSignal(dict)  # Emits signal_data with amount
    rejected = pyqtSignal(dict)  # Emits signal_data (user said no)

    def __init__(self, signal_data: dict, parent=None):
        super().__init__(parent)
        self.signal_data = signal_data
        self.investment_mode = "dollar"  # "dollar" or "lots"
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle(f"🚀 Trade Signal: {self.signal_data['action']} {self.signal_data['ticker']}")
        self.setModal(True)
        self.setFixedSize(500, 450)

        # Keep on top but don't block chart viewing
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        # Title
        action = self.signal_data["action"]
        ticker = self.signal_data["ticker"]
        confidence = self.signal_data["confidence"]

        color = "#3FB950" if action == "BUY" else "#F85149" if action == "SELL" else "#D29922"

        title = QLabel(f"{action} {ticker}")
        title.setFont(QFont("Consolas", 18, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {color};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Confidence
        conf_label = QLabel(f"Confidence: {confidence:.0%}")
        conf_label.setFont(QFont("Consolas", 12))
        conf_label.setStyleSheet(f"color: {'#3FB950' if confidence > 0.7 else '#D29922' if confidence > 0.5 else '#F85149'};")
        conf_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(conf_label)

        # Details
        details = [
            ("Signal Type:", self.signal_data.get("signal_type", "Unknown")),
            ("Entry Price:", f"${self.signal_data.get('entry_price', 'N/A'):.2f}"),
            ("Stop Loss:", f"${self.signal_data.get('stop_loss', 'N/A'):.2f}"),
            ("Take Profit:", f"${self.signal_data.get('take_profit', 'N/A'):.2f}"),
        ]

        details_widget = QWidget()
        details_layout = QVBoxLayout(details_widget)
        details_layout.setSpacing(5)

        for label_text, value_text in details:
            row = QHBoxLayout()

            label = QLabel(label_text)
            label.setFont(QFont("Consolas", 10))
            label.setStyleSheet("color: #8B949E;")
            label.setFixedWidth(100)
            row.addWidget(label)

            value = QLabel(str(value_text))
            value.setFont(QFont("Consolas", 10))
            value.setStyleSheet("color: #E6EDF3;")
            row.addWidget(value)

            details_layout.addLayout(row)

        layout.addWidget(details_widget)

        # Investment Mode Selection
        mode_label = QLabel("Investment Mode:")
        mode_label.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        mode_label.setStyleSheet("color: #00D4FF;")
        layout.addWidget(mode_label)

        mode_layout = QHBoxLayout()
        
        # Dollar amount radio
        self.dollar_radio = QRadioButton("💵 Dollar Amount ($)")
        self.dollar_radio.setFont(QFont("Consolas", 11))
        self.dollar_radio.setStyleSheet("color: #E6EDF3;")
        self.dollar_radio.setChecked(True)
        self.dollar_radio.toggled.connect(self._on_mode_changed)
        mode_layout.addWidget(self.dollar_radio)
        
        # Lots/Units radio
        self.lots_radio = QRadioButton("📊 Lots/Units (Quantity)")
        self.lots_radio.setFont(QFont("Consolas", 11))
        self.lots_radio.setStyleSheet("color: #E6EDF3;")
        self.lots_radio.toggled.connect(self._on_mode_changed)
        mode_layout.addWidget(self.lots_radio)
        
        layout.addLayout(mode_layout)

        # Amount input with dynamic label
        self.amount_layout = QHBoxLayout()
        self.amount_label = QLabel("Amount ($):")
        self.amount_label.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        self.amount_label.setStyleSheet("color: #00D4FF;")
        self.amount_layout.addWidget(self.amount_label)

        self.amount_input = QLineEdit()
        self.amount_input.setPlaceholderText("e.g., 1000")
        self.amount_input.setFont(QFont("Consolas", 12))
        self.amount_input.setStyleSheet(
            "background-color: #0D1117; color: #E6EDF3; border: 1px solid #30363D; padding: 5px;"
        )
        self.amount_input.setFixedWidth(150)
        self.amount_layout.addWidget(self.amount_input)
        
        # Live calculation display
        self.calc_label = QLabel("")
        self.calc_label.setFont(QFont("Consolas", 10))
        self.calc_label.setStyleSheet("color: #8B949E;")
        self.calc_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.amount_layout.addWidget(self.calc_label)

        layout.addLayout(self.amount_layout)
        
        # Reason (brief)
        reason_label = QLabel(f"📝 {self.signal_data.get('reason', 'No reason provided')[:100]}")
        reason_label.setFont(QFont("Consolas", 9))
        reason_label.setStyleSheet("color: #8B949E;")
        reason_label.setWordWrap(True)
        layout.addWidget(reason_label)

        # Buttons
        button_layout = QHBoxLayout()

        self.approve_btn = QPushButton("✅ APPROVE & EXECUTE")
        self.approve_btn.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        self.approve_btn.setStyleSheet(
            "background-color: #3FB950; color: #0D1117; padding: 10px; border: none; border-radius: 4px;"
        )
        self.approve_btn.clicked.connect(self._on_approve)
        button_layout.addWidget(self.approve_btn)

        self.reject_btn = QPushButton("❌ REJECT")
        self.reject_btn.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        self.reject_btn.setStyleSheet(
            "background-color: #F85149; color: #0D1117; padding: 10px; border: none; border-radius: 4px;"
        )
        self.reject_btn.clicked.connect(self._on_reject)
        button_layout.addWidget(self.reject_btn)

        layout.addLayout(button_layout)
        
        # Keyboard shortcuts hint
        hint = QLabel("💡 Tip: Press ENTER to approve quickly")
        hint.setFont(QFont("Consolas", 9))
        hint.setStyleSheet("color: #8B949E;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        # Focus on amount input
        self.amount_input.setFocus()
        self.amount_input.returnPressed.connect(self._on_approve)
        self.amount_input.textChanged.connect(self._update_calculation)
        
        # Set default value
        self.amount_input.setText("1000")

    def _on_mode_changed(self, checked: bool):
        """Switch between dollar and lots mode."""
        if self.dollar_radio.isChecked():
            self.investment_mode = "dollar"
            self.amount_label.setText("Amount ($):")
            self.amount_input.setPlaceholderText("e.g., 1000")
        else:
            self.investment_mode = "lots"
            self.amount_label.setText("Lots/Units:")
            self.amount_input.setPlaceholderText("e.g., 2")
        
        self._update_calculation()

    def _update_calculation(self):
        """Update live calculation preview."""
        try:
            value = float(self.amount_input.text() or "0")
            entry_price = self.signal_data.get("entry_price", 0)
            
            if entry_price > 0:
                if self.investment_mode == "dollar":
                    # Show quantity
                    quantity = value / entry_price
                    self.calc_label.setText(f"= {quantity:.4f} units")
                else:
                    # Show dollar cost
                    total = value * entry_price
                    self.calc_label.setText(f"= ${total:,.2f}")
        except:
            self.calc_label.setText("")

    def _on_approve(self):
        amount_text = self.amount_input.text().strip()
        if not amount_text:
            self.amount_input.setStyleSheet(
                "background-color: #F85149; color: #E6EDF3; border: 2px solid #FF0000; padding: 5px;"
            )
            return

        try:
            amount = float(amount_text)
            if amount <= 0:
                raise ValueError("Amount must be positive")
        except ValueError:
            self.amount_input.setStyleSheet(
                "background-color: #F85149; color: #E6EDF3; border: 2px solid #FF0000; padding: 5px;"
            )
            return

        # Add investment details to signal data
        entry_price = self.signal_data.get("entry_price", 0)
        
        if self.investment_mode == "lots":
            # User specified lots/units
            self.signal_data["investment_mode"] = "lots"
            self.signal_data["lot_size"] = amount
            self.signal_data["investment_amount"] = amount * entry_price
            self.signal_data["quantity"] = amount
        else:
            # User specified dollar amount
            self.signal_data["investment_mode"] = "dollar"
            self.signal_data["investment_amount"] = amount
            self.signal_data["quantity"] = amount / entry_price if entry_price > 0 else 0

        self.approved.emit(self.signal_data)
        self.accept()

    def _on_reject(self):
        self.rejected.emit(self.signal_data)
        self.reject()
