"""
Startup switchboard for choosing the live execution surface.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

import config


@dataclass(frozen=True)
class LaunchProfile:
    execution_mode: str
    trading_surface: str
    headline: str
    smart_eye_enabled: bool = True
    auto_symbol_detection: bool = True
    detected_window_title: str = ""
    detection_reason: str = ""

    def apply(self) -> None:
        config.EXECUTION_MODE = self.execution_mode
        config.TRADING_SURFACE = self.trading_surface
        config.SMART_EYE_ENABLED = self.smart_eye_enabled
        config.AUTO_SYMBOL_DETECTION = self.auto_symbol_detection
        config.DETECTED_TRADING_WINDOW_TITLE = self.detected_window_title
        os.environ["EXECUTION_MODE"] = self.execution_mode
        os.environ["TRADING_SURFACE"] = self.trading_surface
        os.environ["SMART_EYE_ENABLED"] = "true" if self.smart_eye_enabled else "false"
        os.environ["AUTO_SYMBOL_DETECTION"] = "true" if self.auto_symbol_detection else "false"
        os.environ["ACTIVE_EXECUTION_SURFACE"] = "TRADINGVIEW"


def default_launch_profile() -> LaunchProfile:
    # Nuclear fix: respect ACTIVE_EXECUTION_SURFACE first
    surface = str(getattr(config, "ACTIVE_EXECUTION_SURFACE", "") or "").upper().strip()
    if surface == "TRADINGVIEW":
        return LaunchProfile(
            execution_mode="UI",
            trading_surface="TRADINGVIEW_DESKTOP",
            headline="TradingView active execution armed",
        )
    if surface == "MT5":
        return LaunchProfile(
            execution_mode="MT5",
            trading_surface="METATRADER_5",
            headline="MetaTrader 5 mode armed",
        )

    execution_mode = str(getattr(config, "EXECUTION_MODE", "UI") or "UI").upper()
    trading_surface = str(
        getattr(config, "TRADING_SURFACE", "TRADINGVIEW_TRADOVATE") or "TRADINGVIEW_TRADOVATE"
    ).upper()
    if execution_mode == "MT5" or trading_surface == "METATRADER_5":
        return LaunchProfile(
            execution_mode="MT5",
            trading_surface="METATRADER_5",
            headline="MetaTrader 5 mode armed",
        )
    return LaunchProfile(
        execution_mode="UI",
        trading_surface="TRADINGVIEW_TRADOVATE",
        headline="TradingView / Tradovate mode armed",
    )


def scan_desktop_launch_profile() -> LaunchProfile:
    """Scan open desktop windows and choose the best eye automatically.
    Nuclear fix: if ACTIVE_EXECUTION_SURFACE=TRADINGVIEW, always arm active mode."""
    surface = str(getattr(config, "ACTIVE_EXECUTION_SURFACE", "") or "").upper().strip()
    if surface == "TRADINGVIEW":
        return LaunchProfile(
            execution_mode="UI",
            trading_surface="TRADINGVIEW_DESKTOP",
            headline="TradingView active execution armed",
            smart_eye_enabled=True,
            auto_symbol_detection=True,
            detection_reason="ACTIVE_EXECUTION_SURFACE=TRADINGVIEW — forcing active clicks",
        )

    try:
        import pygetwindow as gw
    except Exception:
        profile = default_launch_profile()
        return LaunchProfile(
            execution_mode=profile.execution_mode,
            trading_surface=profile.trading_surface,
            headline=f"{profile.headline} (window scan unavailable)",
            smart_eye_enabled=profile.smart_eye_enabled,
            auto_symbol_detection=True,
            detection_reason="Could not load desktop window scanner.",
        )

    blacklist = [
        term.lower()
        for term in getattr(config, "WINDOW_TITLE_BLACKLIST", [])
        if str(term).strip()
    ]
    mt5_terms = [
        term.lower()
        for term in getattr(config, "MT5_WINDOW_HINTS", [])
        if str(term).strip()
    ] + ["metatrader", "meta trader"]
    browser_terms = [
        term.lower()
        for term in getattr(
            config,
            "BROWSER_WINDOW_HINTS",
            ["TradingView", "Google Chrome", "Brave", "Microsoft Edge"],
        )
        if str(term).strip()
    ]

    windows = _visible_windows(gw)
    active_title = ""
    try:
        active = gw.getActiveWindow()
        active_title = str(getattr(active, "title", "") or "").strip()
    except Exception:
        active_title = ""

    active_profile = _profile_from_title(active_title, mt5_terms, browser_terms, blacklist, active=True)
    if active_profile:
        return active_profile

    mt5_window = _find_window(windows, mt5_terms, blacklist)
    if mt5_window:
        title = str(getattr(mt5_window, "title", "") or "").strip()
        return LaunchProfile(
            execution_mode="MT5",
            trading_surface="METATRADER_5",
            headline="MT5 Eye auto-detected",
            detected_window_title=title,
            detection_reason=f"Found MetaTrader window: {title}",
        )

    browser_window = _find_window(windows, browser_terms, blacklist, require_tradingview=True)
    if browser_window:
        title = str(getattr(browser_window, "title", "") or "").strip()
        return LaunchProfile(
            execution_mode="UI",
            trading_surface="TRADINGVIEW_TRADOVATE",
            headline="Browser Eye auto-detected",
            detected_window_title=title,
            detection_reason=f"Found TradingView browser window: {title}",
        )

    profile = default_launch_profile()
    return LaunchProfile(
        execution_mode=profile.execution_mode,
        trading_surface=profile.trading_surface,
        headline=f"{profile.headline} (no chart window detected)",
        smart_eye_enabled=profile.smart_eye_enabled,
        auto_symbol_detection=True,
        detection_reason="No MT5 or TradingView browser window was visible during startup scan.",
    )


def _visible_windows(gw) -> list:
    windows = []
    try:
        raw_windows = gw.getAllWindows()
    except Exception:
        return windows
    for window in raw_windows:
        title = str(getattr(window, "title", "") or "").strip()
        if not title:
            continue
        if int(getattr(window, "width", 0) or 0) <= 0 or int(getattr(window, "height", 0) or 0) <= 0:
            continue
        windows.append(window)
    return windows


def _profile_from_title(
    title: str,
    mt5_terms: list[str],
    browser_terms: list[str],
    blacklist: list[str],
    *,
    active: bool = False,
) -> LaunchProfile | None:
    lowered = title.lower()
    if not title or any(term in lowered for term in blacklist):
        return None
    if any(term in lowered for term in mt5_terms):
        return LaunchProfile(
            execution_mode="MT5",
            trading_surface="METATRADER_5",
            headline="MT5 Eye auto-detected",
            detected_window_title=title,
            detection_reason=f"{'Active' if active else 'Visible'} MetaTrader window: {title}",
        )
    if "tradingview" in lowered and any(term in lowered for term in browser_terms):
        return LaunchProfile(
            execution_mode="UI",
            trading_surface="TRADINGVIEW_TRADOVATE",
            headline="Browser Eye auto-detected",
            detected_window_title=title,
            detection_reason=f"{'Active' if active else 'Visible'} TradingView browser window: {title}",
        )
    return None


def _find_window(
    windows: list,
    terms: list[str],
    blacklist: list[str],
    *,
    require_tradingview: bool = False,
):
    for window in windows:
        title = str(getattr(window, "title", "") or "").strip()
        lowered = title.lower()
        if any(term in lowered for term in blacklist):
            continue
        if require_tradingview and "tradingview" not in lowered:
            continue
        if any(term in lowered for term in terms):
            return window
    return None


class LionSwitchboardDialog(QDialog):
    """One-button startup remote control."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selection = default_launch_profile()
        self.setWindowTitle("Lion Switchboard")
        self.setModal(True)
        
        # 2. Dynamic Desktop Screen Detection Architecture
        from PyQt6.QtWidgets import QApplication
        primary_screen = QApplication.primaryScreen()
        if primary_screen:
            available_geometry = primary_screen.availableGeometry()
            target_width = min(520, int(available_geometry.width() * 0.45))
            target_height = min(400, int(available_geometry.height() * 0.50))
            self.resize(target_width, target_height)
        else:
            self.setMinimumWidth(520)

        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(18)
        root.setContentsMargins(24, 24, 24, 24)

        title = QLabel("The Lion Remote Control")
        title.setFont(QFont("Segoe UI", 18, 700))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #E6EDF3;")

        subtitle = QLabel(
            "Press START. The bot scans the desktop and chooses MT5 Eye or Browser Eye automatically."
        )
        subtitle.setWordWrap(True)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #8B949E;")

        remote = self._make_start_card()

        status = QLabel(
            "Auto-detect checks the active window first, then visible MT5 and TradingView browser windows."
        )
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status.setStyleSheet("color: #9FB3C8;")
        status.setWordWrap(True)

        root.addWidget(title)
        root.addWidget(subtitle)
        root.addWidget(remote)
        root.addWidget(status)
        self.setStyleSheet("QDialog { background: #0D1117; }")

    def _make_start_card(self):
        accent = "#0E7490"
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ background: #161B22; border: 2px solid {accent}; border-radius: 14px; }}"
        )
        layout = QVBoxLayout(frame)
        layout.setSpacing(12)

        title_label = QLabel("START")
        title_label.setFont(QFont("Segoe UI", 16, 700))
        title_label.setStyleSheet("color: #F0F6FC; border: none;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        badge_label = QLabel("AUTO")
        badge_label.setStyleSheet(
            f"color: white; background: {accent}; padding: 6px 10px; border-radius: 10px; border: none;"
        )
        badge_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        body_label = QLabel("MT5 users and TradingView users launch the same way.")
        body_label.setWordWrap(True)
        body_label.setStyleSheet("color: #C9D1D9; border: none;")
        body_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        button = QPushButton("START")
        button.setMinimumHeight(76)
        button.setFont(QFont("Segoe UI", 20, 800))
        button.setStyleSheet(
            f"""
            QPushButton {{
                background: {accent};
                color: white;
                border: none;
                border-radius: 12px;
                padding: 10px 16px;
            }}
            QPushButton:hover {{ background: {accent}; opacity: 0.92; }}
            """
        )
        button.clicked.connect(self._auto_start)

        layout.addWidget(title_label)
        layout.addWidget(badge_label)
        layout.addWidget(body_label)
        layout.addStretch(1)
        layout.addWidget(button)
        return frame

    def _auto_start(self) -> None:
        self.selection = scan_desktop_launch_profile()
        self.accept()


def choose_launch_profile(parent=None) -> LaunchProfile:
    if not getattr(config, "SHOW_STARTUP_SWITCHBOARD", True):
        profile = scan_desktop_launch_profile()
        profile.apply()
        return profile

    dialog = LionSwitchboardDialog(parent=parent)
    if dialog.exec():
        dialog.selection.apply()
        return dialog.selection

    profile = default_launch_profile()
    profile.apply()
    return profile
