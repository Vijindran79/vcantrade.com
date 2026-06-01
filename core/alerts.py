"""
VcanTrade AI - Institutional Alert System
==========================================
Multi-channel alerts for critical events: drawdown, targets, errors.
"""
import logging
import os
import smtplib
from email.mime.text import MIMEText
from typing import Dict, List, Optional, Callable
from datetime import datetime
import json
from pathlib import Path
from collections import deque

logger = logging.getLogger(__name__)

ALERT_LOG = Path("alerts.jsonl")
ALERT_CONFIG = Path("alert_config.json")


class AlertLevel:
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    EMERGENCY = "EMERGENCY"


class AlertSystem:
    """
    Multi-channel alert system. Email, desktop, and log alerts.
    Critical for institutional risk management.
    """

    def __init__(self):
        self.history: deque = deque(maxlen=500)
        self._cooldowns: Dict[str, datetime] = {}
        self._cooldown_seconds = 300  # 5 min between duplicate alerts
        self._load_config()

    def _load_config(self):
        try:
            if ALERT_CONFIG.exists():
                cfg = json.loads(ALERT_CONFIG.read_text())
                self.email_enabled = cfg.get("email_enabled", False)
                self.email_to = cfg.get("email_to", "")
                self.smtp_server = cfg.get("smtp_server", "")
                self.smtp_port = int(cfg.get("smtp_port", 587))
                self.smtp_user = cfg.get("smtp_user", "")
                self.smtp_password = os.getenv("ALERT_SMTP_PASSWORD", "")
                self.desktop_enabled = cfg.get("desktop_enabled", True)
            else:
                self.email_enabled = False
                self.email_to = ""
                self.smtp_server = ""
                self.smtp_port = 587
                self.smtp_user = ""
                self.smtp_password = ""
                self.desktop_enabled = True
        except Exception as e:
            logger.warning("[ALERT] Config load error: %s", e)
            self.email_enabled = False
            self.desktop_enabled = True

    def configure(self, email_to: str = "", smtp_server: str = "",
                  smtp_port: int = 587, smtp_user: str = "",
                  desktop_enabled: bool = True, email_enabled: bool = False):
        """Configure alert channels."""
        self.email_to = email_to
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.desktop_enabled = desktop_enabled
        self.email_enabled = email_enabled
        try:
            ALERT_CONFIG.write_text(json.dumps({
                "email_to": email_to, "smtp_server": smtp_server,
                "smtp_port": smtp_port, "smtp_user": smtp_user,
                "desktop_enabled": desktop_enabled, "email_enabled": email_enabled,
            }, indent=2))
        except Exception as e:
            logger.warning("[ALERT] Config save error: %s", e)

    # ------------------------------------------------------------------
    # Alert Triggers
    # ------------------------------------------------------------------
    def drawdown_alert(self, current_dd_pct: float, max_allowed_pct: float = 5.0):
        if current_dd_pct >= max_allowed_pct * 1.5:
            self.fire(AlertLevel.EMERGENCY, "MAX_DRAWDOWN",
                      f"Drawdown {current_dd_pct:.2f}% exceeds limit {max_allowed_pct * 1.5:.1f}%")
        elif current_dd_pct >= max_allowed_pct:
            self.fire(AlertLevel.CRITICAL, "DRAWDOWN_BREACH",
                      f"Drawdown {current_dd_pct:.2f}% breached {max_allowed_pct:.1f}% limit")

    def daily_target_hit(self, daily_pnl_pct: float, target_pct: float = 3.0):
        if daily_pnl_pct >= target_pct:
            self.fire(AlertLevel.INFO, "DAILY_TARGET",
                      f"Daily target hit! P&L: {daily_pnl_pct:.2f}% (target: {target_pct:.1f}%)")

    def daily_loss_breach(self, daily_pnl_pct: float, max_loss_pct: float = 2.0):
        if daily_pnl_pct <= -max_loss_pct:
            self.fire(AlertLevel.EMERGENCY, "DAILY_LOSS_LIMIT",
                      f"Daily loss {daily_pnl_pct:.2f}% breached {max_loss_pct:.1f}% — Walk Away triggered")

    def execution_failure(self, symbol: str, error: str):
        self.fire(AlertLevel.WARNING, "EXEC_FAILURE",
                  f"Execution failed for {symbol}: {error}")

    def bot_stopped(self, reason: str):
        self.fire(AlertLevel.EMERGENCY, "BOT_STOPPED", f"Bot stopped: {reason}")

    def regime_shift(self, old_regime: str, new_regime: str):
        if new_regime in ("CRISIS", "STRONG_BEAR", "STRONG_BULL"):
            self.fire(AlertLevel.WARNING, "REGIME_SHIFT",
                      f"Regime shift: {old_regime} -> {new_regime}")

    def custom(self, level: str, title: str, message: str):
        self.fire(level, title, message)

    # ------------------------------------------------------------------
    # Core Fire
    # ------------------------------------------------------------------
    def fire(self, level: str, title: str, message: str):
        """Fire an alert through all enabled channels."""
        # Cooldown check (no spam)
        key = f"{level}:{title}"
        now = datetime.utcnow()
        if key in self._cooldowns:
            if (now - self._cooldowns[key]).total_seconds() < self._cooldown_seconds:
                return
        self._cooldowns[key] = now

        record = {
            "timestamp": now.isoformat(),
            "level": level,
            "title": title,
            "message": message,
        }
        self.history.append(record)
        self._log(record)

        # Log to console
        log_fn = {
            AlertLevel.INFO: logger.info,
            AlertLevel.WARNING: logger.warning,
            AlertLevel.CRITICAL: logger.critical,
            AlertLevel.EMERGENCY: logger.critical,
        }.get(level, logger.info)
        log_fn("[ALERT][%s] %s: %s", level, title, message)

        # Desktop notification
        if self.desktop_enabled and level in (AlertLevel.CRITICAL, AlertLevel.EMERGENCY):
            self._desktop_notify(title, message)

        # Email
        if self.email_enabled and level in (AlertLevel.WARNING, AlertLevel.CRITICAL, AlertLevel.EMERGENCY):
            self._send_email(title, message)

    def _desktop_notify(self, title: str, message: str):
        try:
            from win10toast import ToastNotifier
            toaster = ToastNotifier()
            toaster.show_toast(title, message, duration=10, threaded=True)
        except ImportError:
            try:
                import subprocess
                ps_cmd = f"""
                Add-Type -AssemblyName System.Windows.Forms
                $notify = New-Object System.Windows.Forms.NotifyIcon
                $notify.Icon = [System.Drawing.SystemIcons]::Warning
                $notify.Visible = $true
                $notify.ShowBalloonTip(10000, "{title}", "{message}", [System.Windows.Forms.ToolTipIcon]::Warning)
                Start-Sleep -Seconds 11
                $notify.Dispose()
                """
                subprocess.Popen(["powershell", "-Command", ps_cmd], shell=False)
            except Exception as e:
                logger.debug("[ALERT] Desktop notify failed: %s", e)
        except Exception as e:
            logger.debug("[ALERT] Desktop notify failed: %s", e)

    def _send_email(self, title: str, message: str):
        if not all([self.smtp_server, self.email_to, self.smtp_user, self.smtp_password]):
            return
        try:
            msg = MIMEText(f"{title}\n\n{message}\n\nTime: {datetime.utcnow().isoformat()}")
            msg["Subject"] = f"[VcanTrade {title}]"
            msg["From"] = self.smtp_user
            msg["To"] = self.email_to
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)
        except Exception as e:
            logger.warning("[ALERT] Email send failed: %s", e)

    def _log(self, record: Dict):
        try:
            with ALERT_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as e:
            logger.debug("[ALERT] Log write failed: %s", e)

    def recent(self, count: int = 20) -> List[Dict]:
        return list(self.history)[-count:]

    def summary(self) -> Dict:
        return {
            "total_alerts": len(self.history),
            "critical_count": sum(1 for a in self.history if a["level"] in (AlertLevel.CRITICAL, AlertLevel.EMERGENCY)),
            "warning_count": sum(1 for a in self.history if a["level"] == AlertLevel.WARNING),
            "recent": self.recent(5),
        }


alerts = AlertSystem()
