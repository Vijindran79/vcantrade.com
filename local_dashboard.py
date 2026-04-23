"""
VcaniTrade AI - Local Dashboard Client (Windows 11)
Monitors the Lion Bot running on Vast.ai via REST API.

Usage:
    python local_dashboard.py
    python local_dashboard.py 213.224.31.105 8765

Requirements:
    PyQt6 (already used by main VcaniTrade app)
    No extra pip packages — uses built-in urllib.
"""

import sys
import json
import urllib.request
import urllib.error
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QPlainTextEdit, QGroupBox, QGridLayout, QHeaderView, QMessageBox,
    QLineEdit, QSpinBox, QSizePolicy
)
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFont, QColor


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_PORT = 8765
POLL_INTERVAL_MS = 2000
MAX_LOG_LINES = 500


class LionDashboard(QMainWindow):
    """Real-time remote dashboard for the Vast.ai cloud trading engine."""

    def __init__(self, initial_ip: str = "", initial_port: int = DEFAULT_PORT):
        super().__init__()
        self.server_ip = initial_ip
        self.server_port = initial_port
        self.base_url = ""
        self._update_base_url()

        self.setWindowTitle("Lion Cloud Dashboard")
        self.setMinimumSize(1000, 780)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        # =====================================================================
        # 1. CONNECTION BAR
        # =====================================================================
        conn_group = QGroupBox("Cloud Connection")
        conn_layout = QHBoxLayout(conn_group)
        conn_layout.setSpacing(10)

        conn_layout.addWidget(QLabel("Vast.ai IP:"))
        self.ip_input = QLineEdit(self.server_ip)
        self.ip_input.setPlaceholderText("e.g. 213.224.31.105")
        self.ip_input.setMinimumWidth(180)
        conn_layout.addWidget(self.ip_input)

        conn_layout.addWidget(QLabel("Port:"))
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(self.server_port)
        self.port_input.setMinimumWidth(80)
        conn_layout.addWidget(self.port_input)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setStyleSheet("""
            QPushButton {
                background-color: #2563eb; color: white; font-weight: bold;
                padding: 6px 16px; border-radius: 6px;
            }
            QPushButton:hover { background-color: #1d4ed8; }
        """)
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        conn_layout.addWidget(self.connect_btn)

        conn_layout.addStretch()

        self.status_label = QLabel("Disconnected")
        self.status_label.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        self.status_label.setStyleSheet("color: #dc2626;")
        conn_layout.addWidget(self.status_label)

        self.poll_indicator = QLabel("Poll: --")
        self.poll_indicator.setStyleSheet("color: #6b7280;")
        conn_layout.addWidget(self.poll_indicator)

        layout.addWidget(conn_group)

        # =====================================================================
        # 2. METRICS (LARGE TEXT)
        # =====================================================================
        metrics_group = QGroupBox("Live Account Metrics")
        metrics_layout = QGridLayout(metrics_group)
        metrics_layout.setSpacing(14)
        metrics_layout.setHorizontalSpacing(24)

        self.lbl_account = self._make_metric_label("Account: --", 11)
        self.lbl_balance = self._make_metric_label("Balance: --", 22, bold=True)
        self.lbl_equity = self._make_metric_label("Equity: --", 22, bold=True)
        self.lbl_daily_pnl = self._make_metric_label("Daily P&L: --", 22, bold=True)
        self.lbl_total_pnl = self._make_metric_label("Total P&L: --", 14)
        self.lbl_trades = self._make_metric_label("Trades Today: --", 14)
        self.lbl_mode = self._make_metric_label("Mode: --", 14)
        self.lbl_positions = self._make_metric_label("Active Positions: --", 14)
        self.lbl_ticker = self._make_metric_label("Active Ticker: CME_MINI:MNQ1!", 14)

        metrics_layout.addWidget(self.lbl_account, 0, 0, 1, 4)
        metrics_layout.addWidget(self.lbl_balance, 1, 0)
        metrics_layout.addWidget(self.lbl_equity, 1, 1)
        metrics_layout.addWidget(self.lbl_daily_pnl, 1, 2)
        metrics_layout.addWidget(self.lbl_total_pnl, 2, 0)
        metrics_layout.addWidget(self.lbl_trades, 2, 1)
        metrics_layout.addWidget(self.lbl_mode, 2, 2)
        metrics_layout.addWidget(self.lbl_positions, 3, 0)
        metrics_layout.addWidget(self.lbl_ticker, 3, 1, 1, 2)
        layout.addWidget(metrics_group)

        # =====================================================================
        # 3. ACTIVE POSITIONS TABLE
        # =====================================================================
        positions_group = QGroupBox("Active Positions")
        positions_layout = QVBoxLayout(positions_group)
        self.positions_table = QTableWidget()
        self.positions_table.setColumnCount(6)
        self.positions_table.setHorizontalHeaderLabels(
            ["Asset", "Side", "Entry", "Current", "P&L ($)", "P&L (%)"]
        )
        self.positions_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.positions_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.positions_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.positions_table.setMinimumHeight(120)
        positions_layout.addWidget(self.positions_table)
        layout.addWidget(positions_group)

        # =====================================================================
        # 4. SCROLLING LIVE LOG
        # =====================================================================
        log_group = QGroupBox("Live Cloud Logs")
        log_layout = QVBoxLayout(log_group)
        self.log_display = QPlainTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setMinimumHeight(160)
        self.log_display.setMaximumBlockCount(MAX_LOG_LINES)
        self.log_display.setStyleSheet("""
            QPlainTextEdit {
                background-color: #0f172a;
                color: #e2e8f0;
                font-family: Consolas, "Courier New", monospace;
                font-size: 11px;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 6px;
            }
        """)
        log_layout.addWidget(self.log_display)
        layout.addWidget(log_group)

        # =====================================================================
        # 5. EMERGENCY KILL SWITCH
        # =====================================================================
        kill_layout = QHBoxLayout()
        kill_layout.addStretch()
        self.kill_btn = QPushButton("EMERGENCY KILL SWITCH")
        self.kill_btn.setFixedSize(320, 70)
        self.kill_btn.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        self.kill_btn.setStyleSheet("""
            QPushButton {
                background-color: #dc2626; color: white;
                border: 2px solid #991b1b; border-radius: 10px;
            }
            QPushButton:hover { background-color: #b91c1c; }
            QPushButton:pressed { background-color: #7f1d1d; }
        """)
        self.kill_btn.clicked.connect(self._send_kill)
        kill_layout.addWidget(self.kill_btn)
        kill_layout.addStretch()
        layout.addLayout(kill_layout)

        # =====================================================================
        # Timer & State
        # =====================================================================
        self.poll_timer = QTimer()
        self.poll_timer.timeout.connect(self._poll_status)
        self._poll_count = 0
        self._last_log_raw = ""

        # Auto-connect if IP was provided via CLI
        if self.server_ip:
            self._start_polling()

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------
    def _make_metric_label(self, text: str, size: int, bold: bool = False) -> QLabel:
        lbl = QLabel(text)
        weight = QFont.Weight.Bold if bold else QFont.Weight.Normal
        lbl.setFont(QFont("Segoe UI", size, weight))
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        return lbl

    def _update_base_url(self):
        self.base_url = f"http://{self.server_ip}:{self.server_port}"

    def _on_connect_clicked(self):
        ip = self.ip_input.text().strip()
        if not ip:
            QMessageBox.warning(self, "Missing IP", "Please enter the Vast.ai public IP address.")
            return
        self.server_ip = ip
        self.server_port = self.port_input.value()
        self._update_base_url()
        self._start_polling()

    def _start_polling(self):
        self.poll_timer.stop()
        self.poll_timer.start(POLL_INTERVAL_MS)
        self.status_label.setText("Connecting...")
        self.status_label.setStyleSheet("color: #ca8a04;")
        self._poll_status()

    # -----------------------------------------------------------------------
    # Network
    # -----------------------------------------------------------------------
    def _fetch_json(self, endpoint: str, method: str = "GET", timeout: int = 4) -> dict:
        url = f"{self.base_url}{endpoint}"
        req = urllib.request.Request(url, method=method, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _poll_status(self):
        self._poll_count += 1
        self.poll_indicator.setText(f"Poll: #{self._poll_count}")
        try:
            data = self._fetch_json("/api/status")
            self._update_ui(data)
            self.status_label.setText("Connected")
            self.status_label.setStyleSheet("color: #16a34a;")
        except urllib.error.URLError as e:
            self.status_label.setText(f"Disconnected — {e.reason}")
            self.status_label.setStyleSheet("color: #dc2626;")
        except Exception as e:
            self.status_label.setText(f"Error — {str(e)[:50]}")
            self.status_label.setStyleSheet("color: #dc2626;")

    def _update_ui(self, data: dict):
        # Account
        self.lbl_account.setText(f"Account: {data.get('account_id', 'N/A')}")
        self.lbl_balance.setText(f"Balance: ${data.get('current_balance', 0):,.2f}")
        self.lbl_equity.setText(f"Equity: ${data.get('equity', 0):,.2f}")

        daily_pnl = data.get('daily_pnl', 0)
        d_color = "#16a34a" if daily_pnl >= 0 else "#dc2626"
        self.lbl_daily_pnl.setText(f"Daily P&L: ${daily_pnl:,.2f}")
        self.lbl_daily_pnl.setStyleSheet(f"color: {d_color};")

        total_pnl = data.get('total_pnl', 0)
        t_color = "#16a34a" if total_pnl >= 0 else "#dc2626"
        self.lbl_total_pnl.setText(f"Total P&L: ${total_pnl:,.2f}")
        self.lbl_total_pnl.setStyleSheet(f"color: {t_color};")

        self.lbl_trades.setText(f"Trades Today: {data.get('trades_today', 0)}")
        self.lbl_mode.setText(f"Mode: {data.get('mode', 'UNKNOWN')}")
        self.lbl_positions.setText(f"Active Positions: {data.get('active_positions', 0)}")

        # Positions table
        positions = data.get('positions', [])
        self.positions_table.setRowCount(len(positions))
        for i, pos in enumerate(positions):
            self.positions_table.setItem(i, 0, QTableWidgetItem(str(pos.get('asset', ''))))
            self.positions_table.setItem(i, 1, QTableWidgetItem(str(pos.get('side', ''))))
            self.positions_table.setItem(i, 2, QTableWidgetItem(f"${pos.get('entry', 0):,.2f}"))
            self.positions_table.setItem(i, 3, QTableWidgetItem(f"${pos.get('current', 0):,.2f}"))
            pnl = pos.get('pnl', 0)
            pnl_item = QTableWidgetItem(f"${pnl:,.2f}")
            pnl_item.setForeground(Qt.GlobalColor.green if pnl >= 0 else Qt.GlobalColor.red)
            self.positions_table.setItem(i, 4, pnl_item)
            pnl_pct = pos.get('pnl_pct', 0)
            pct_item = QTableWidgetItem(f"{pnl_pct:,.2f}%")
            pct_item.setForeground(Qt.GlobalColor.green if pnl_pct >= 0 else Qt.GlobalColor.red)
            self.positions_table.setItem(i, 5, pct_item)

        # Live log — append only if new
        last_log = data.get('last_log_message', '')
        if last_log and last_log != self._last_log_raw:
            self._last_log_raw = last_log
            ts = datetime.now().strftime("%H:%M:%S")
            self.log_display.appendPlainText(f"[{ts}] {last_log}")
            # Auto-scroll to bottom
            scrollbar = self.log_display.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _send_kill(self):
        if not self.base_url:
            QMessageBox.warning(self, "Not Connected", "Enter a Vast.ai IP and click Connect first.")
            return

        reply = QMessageBox.question(
            self,
            "CONFIRM EMERGENCY KILL SWITCH",
            "This will IMMEDIATELY HALT ALL TRADING on the cloud server.\n\n"
            "Open positions may remain unmanaged.\n\nAre you absolutely sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            result = self._fetch_json("/api/kill", method="POST", timeout=6)
            QMessageBox.information(
                self,
                "Kill Switch Sent",
                f"Server response: {result.get('status', 'unknown')}",
            )
            self.status_label.setText("KILL SWITCH SENT — Trading Halted")
            self.status_label.setStyleSheet("color: #dc2626;")
            self.log_display.appendPlainText(
                f"[{datetime.now().strftime('%H:%M:%S')}] >>> EMERGENCY KILL SWITCH ACTIVATED <<<")
        except Exception as e:
            QMessageBox.critical(
                self,
                "Kill Switch Failed",
                f"Could not reach cloud server:\n{str(e)[:200]}",
            )


def main():
    initial_ip = sys.argv[1] if len(sys.argv) > 1 else ""
    initial_port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT

    app = QApplication(sys.argv)
    window = LionDashboard(initial_ip, initial_port)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
