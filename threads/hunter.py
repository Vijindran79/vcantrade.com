"""
VcanTrade AI — Multi-Asset Hunter QThread
Cycles through configured tickers, captures chart screenshots,
sends them to the Cloud Brain for vision analysis, and emits
trade signals when BUY/SELL is detected.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

from PyQt6.QtCore import QThread, pyqtSignal

import config
from core.market_sessions import is_crypto_ticker, is_weekend_closed
from threads._utils import _is_passive_visual_mode, _wait_for_vision_analysis_slot

logger = logging.getLogger(__name__)


class MultiAssetHunterThread(QThread):
    """
    Cycles through watchlist tickers at a fixed interval.
    Navigates TradingView, screenshots, sends to Cloud Brain (Ollama v1),
    and emits trade signals when BUY/SELL is detected.
    """

    status_update = pyqtSignal(str, str, str)   # symbol, status, message
    trade_signal = pyqtSignal(str, str, str)    # symbol, action, reason
    narrator_update = pyqtSignal(str, str)       # icon, message

    def __init__(self, app, symbols=None, interval_sec=None):
        super().__init__()
        self.app = app
        self.symbols = symbols or config.MULTI_ASSET_TICKERS
        self.interval_sec = interval_sec or config.MULTI_ASSET_CYCLE_SECONDS
        self.running = True
        self.index = 0

    def _get_ready_browser_agent(self, symbol: str, require_page: bool = True):
        """Return BrowserAgent only when the async startup path is ready."""
        agent = getattr(self.app, "browser_agent", None)
        status = str(
            getattr(self.app, "browser_agent_status", "unknown") or "unknown"
        )
        if agent is None:
            logger.debug(
                "[HUNTER] Browser agent unavailable for %s (status=%s)",
                symbol,
                status,
            )
            self.status_update.emit(
                symbol,
                "WAITING",
                f"Browser agent {status}; retrying next cycle",
            )
            return None
        if require_page and getattr(agent, "page", None) is None:
            logger.debug(
                "[HUNTER] Browser page not ready for %s (status=%s)",
                symbol,
                status,
            )
            self.status_update.emit(
                symbol,
                "WAITING",
                f"Browser page not ready ({status})",
            )
            return None
        return agent

    def run(self):
        logger.info("[HUNTER] Multi-Asset Hunter thread started")
        while self.running:
            now_utc = datetime.now(timezone.utc)
            weekday = now_utc.weekday()
            hour_utc = now_utc.hour

            # Automatic Switchboard Flip
            is_weekend = (weekday == 5) or (weekday == 6 and hour_utc < 22)

            watchlist = getattr(self.app, "current_watchlist", [])
            if is_weekend:
                active_symbols = [s for s in watchlist if is_crypto_ticker(s)]
                if not active_symbols:
                    active_symbols = ["BTC-USD"]
                logger.debug("[HUNTER] Weekend mode: only crypto: %s", active_symbols)
            else:
                active_symbols = list(watchlist) if watchlist else []

            # Monday State Re-Sync (Anti-Ghosting)
            if not is_weekend and not getattr(self.app, "_monday_resync_done", False):
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self._perform_monday_resync())
                    loop.close()
                except Exception as resync_err:
                    logger.warning(
                        "[RESYNC] Monday resync failed (non-fatal): %s",
                        resync_err,
                    )

            if not active_symbols:
                # Sleep a cycle if there is genuinely nothing to scan
                for _ in range(self.interval_sec):
                    if not self.running:
                        break
                    time.sleep(1)
                continue

            symbol = active_symbols[self.index % len(active_symbols)]
            self._cycle_symbol(symbol)
            self.index = (self.index + 1) % len(active_symbols)

            for _ in range(self.interval_sec):
                if not self.running:
                    break
                time.sleep(1)

        logger.info("[HUNTER] Multi-Asset Hunter stopped.")

    def _cycle_symbol(self, symbol: str):
        try:
            now_utc = datetime.now(timezone.utc)
            is_weekend_now = (
                now_utc.weekday() == 5
            ) or (now_utc.weekday() == 6 and now_utc.hour < 22)
            if is_weekend_now and is_weekend_closed(symbol):
                logger.debug("[HUNTER] Skipping weekend-closed symbol: %s", symbol)
                return

            agent = self._get_ready_browser_agent(symbol, require_page=True)
            if agent is None:
                return

            # STURDY BRIDGE: Skip if browser is busy
            if hasattr(agent, "is_browser_busy") and agent.is_browser_busy():
                logger.debug(
                    "[BRIDGE] Browser busy — skipping %s cycle", symbol
                )
                self.status_update.emit(
                    symbol, "WAITING", "Browser busy — will retry next cycle"
                )
                return

            self.status_update.emit(
                symbol, "OBSERVING", f"Monitoring active tab for {symbol}"
            )

            # Passive observer sync
            loop = (
                self.app._browser_loop
                if hasattr(self.app, "_browser_loop")
                else None
            )
            if not loop or loop.is_closed():
                self.status_update.emit(symbol, "ERROR", "Browser loop not available")
                return

            navigate_to_symbol = getattr(agent, "navigate_to_symbol", None)
            if not callable(navigate_to_symbol):
                logger.warning(
                    "[HUNTER] Browser agent has no navigate_to_symbol method for %s",
                    symbol,
                )
                self.status_update.emit(
                    symbol, "WAITING", "Browser navigation unavailable"
                )
                return

            future = asyncio.run_coroutine_threadsafe(
                navigate_to_symbol(symbol),
                loop,
            )
            nav_ok = future.result(timeout=35)
            if not nav_ok:
                self.status_update.emit(symbol, "ERROR", "Navigation failed")
                if hasattr(agent, "record_error"):
                    agent.record_error("Navigation failed for " + symbol)
                if getattr(agent, "error_count", 0) >= getattr(
                    agent, "error_threshold", 999999
                ):
                    logger.warning(
                        "[WRENCH] Navigation failures reached threshold — triggering browser self-heal"
                    )
                    try:
                        self_heal_restart = getattr(agent, "self_heal_restart", None)
                        if not callable(self_heal_restart):
                            return
                        heal_future = asyncio.run_coroutine_threadsafe(
                            self_heal_restart(), loop
                        )
                        heal_future.result(timeout=30)
                        logger.info(
                            "[WRENCH] Browser self-heal completed after navigation failure"
                        )
                    except Exception as heal_err:
                        logger.error("[WRENCH] Browser self-heal failed: %s", heal_err)
                return

            # Sync scanner to the symbol now visible in the browser
            if hasattr(self.app, "cloud_scanner") and self.app.cloud_scanner:
                try:
                    self.app.cloud_scanner.scanner.set_eye_symbol(symbol)
                except Exception:
                    pass

            # Force exit before screenshot triggers in passive modes
            browser_passive = False
            if getattr(self.app, "browser_agent", None):
                browser_passive = bool(
                    getattr(
                        self.app.browser_agent,
                        "_is_passive_observer_mode",
                        lambda: False,
                    )()
                )
            passive_visual = _is_passive_visual_mode() or browser_passive
            if passive_visual:
                logger.debug("[VISION] Skipping visual screenshot in passive mode.")
                return

            self.status_update.emit(symbol, "SCREENSHOT", "Capturing chart...")

            agent = self._get_ready_browser_agent(symbol, require_page=True)
            if agent is None:
                return
            take_screenshot = getattr(agent, "take_screenshot", None)
            if not callable(take_screenshot):
                logger.warning(
                    "[HUNTER] Browser agent has no take_screenshot method for %s",
                    symbol,
                )
                self.status_update.emit(
                    symbol, "WAITING", "Screenshot unavailable"
                )
                return

            future = asyncio.run_coroutine_threadsafe(
                take_screenshot(),
                loop,
            )
            screenshot_b64 = future.result(timeout=15)
            if not screenshot_b64:
                self.status_update.emit(symbol, "ERROR", "Screenshot failed")
                if hasattr(agent, "record_error"):
                    agent.record_error("Screenshot failed for " + symbol)
                if getattr(agent, "error_count", 0) >= getattr(
                    agent, "error_threshold", 999999
                ):
                    logger.warning(
                        "[WRENCH] Screenshot failures reached threshold — triggering browser self-heal"
                    )
                    try:
                        self_heal_restart = getattr(agent, "self_heal_restart", None)
                        if not callable(self_heal_restart):
                            return
                        heal_future = asyncio.run_coroutine_threadsafe(
                            self_heal_restart(), loop
                        )
                        heal_future.result(timeout=30)
                        logger.info(
                            "[WRENCH] Browser self-heal completed after screenshot failure"
                        )
                    except Exception as heal_err:
                        logger.error("[WRENCH] Browser self-heal failed: %s", heal_err)
                return

            self.status_update.emit(
                symbol, "ANALYZING", "Sending to Cloud Brain..."
            )

            from core.brain_swarm import analyze_chart_with_vision

            _wait_for_vision_analysis_slot(f"hunter:{symbol}")
            result = analyze_chart_with_vision(screenshot_b64, symbol)
            signal = result.get("signal", "NONE")
            confidence = result.get("confidence", 50)
            threat = result.get("threat", "MEDIUM")
            reason = result.get("reason", "No reason")

            self.status_update.emit(symbol, f"SIGNAL_{signal}", f"{confidence}% | {reason}")
            self._emit_hunter_intelligence(symbol, signal, confidence, threat, reason)

            # Sunday Gap Guard
            if self._is_sunday_gap_window():
                logger.warning(
                    "[SUNDAY-GAP] %s %s signal BLOCKED: Sunday gap window active "
                    "(22:00-22:15 UTC). Waiting for spreads to stabilize.",
                    symbol,
                    signal,
                )
                self.status_update.emit(
                    symbol, "SUNDAY_GAP_BLOCKED", "Gap guard active - no execution"
                )
                self.narrator_update.emit(
                    "[STOP]",
                    f"SUNDAY GAP GUARD: {symbol} {signal} blocked (22:00-22:15 UTC)",
                )
                return

            # Execute if BUY/SELL AND confidence meets threshold
            if signal in ("BUY", "SELL"):
                if confidence >= config.MIN_CONFIDENCE_THRESHOLD:
                    logger.critical(
                        "[HUNTER] %s SIGNAL: %s | Confidence: %d%% | Threat: %s | %s",
                        symbol,
                        signal,
                        confidence,
                        threat,
                        reason,
                    )
                    self.trade_signal.emit(symbol, signal, reason)
                else:
                    logger.info(
                        "[HUNTER] %s %s signal REJECTED | Confidence %d%% < threshold %d%% | %s",
                        symbol,
                        signal,
                        confidence,
                        config.MIN_CONFIDENCE_THRESHOLD,
                        reason,
                    )
                    self.status_update.emit(
                        symbol,
                        "SKIPPED_LOW_CONFIDENCE",
                        f"Confidence {confidence}% below {config.MIN_CONFIDENCE_THRESHOLD}%",
                    )
            else:
                logger.info("[HUNTER] %s no trade setup | %s", symbol, reason)

        except Exception as e:
            logger.error("[HUNTER] Cycle error for %s: %s", symbol, e)
            self.status_update.emit(symbol, "ERROR", str(e)[:100])

    def _emit_hunter_intelligence(
        self, symbol: str, signal: str, confidence: int, threat: str, reason: str
    ):
        """Emit rich trade intelligence to the Activity Feed."""
        app = self.app
        if not hasattr(app, "ai_narrator") or not app.ai_narrator:
            return

        self.narrator_update.emit("[BRAIN]", f"Analyzing {symbol} chart...")

        if threat == "HIGH":
            threat_icon = "[STOP]"
            threat_msg = "Threat Level: HIGH | Chop/uncertain conditions detected"
        elif threat == "MEDIUM":
            threat_icon = "[YELLOW]"
            threat_msg = "Threat Level: MEDIUM | Caution advised"
        else:
            threat_icon = "[GREEN]"
            threat_msg = "Threat Level: LOW | Clean setup"
        self.narrator_update.emit(threat_icon, threat_msg)

        if confidence >= 85:
            conv_icon = "[TARGET]"
            conv_msg = f"Conviction: {confidence}% | HIGH CONFIDENCE setup"
        elif confidence >= 70:
            conv_icon = "[COMPASS]"
            conv_msg = f"Conviction: {confidence}% | Moderate confidence"
        elif confidence >= 50:
            conv_icon = "[YELLOW]"
            conv_msg = f"Conviction: {confidence}% | Weak edge"
        else:
            conv_icon = "[RED]"
            conv_msg = f"Conviction: {confidence}% | Low probability"
        self.narrator_update.emit(conv_icon, conv_msg)

        self.narrator_update.emit("[CHART]", f"Setup: {reason}")

        if signal in ("BUY", "SELL"):
            if confidence >= config.MIN_CONFIDENCE_THRESHOLD:
                verdict_icon = "[BOLT]" if confidence >= 80 else "[OK]"
                verdict_msg = (
                    f"VERDICT: {signal} {symbol} | Passing to execution gate"
                )
            else:
                verdict_icon = "[PAUSE]"
                verdict_msg = (
                    f"VERDICT: {signal} {symbol} | BLOCKED by confidence gate "
                    f"(< {config.MIN_CONFIDENCE_THRESHOLD}%)"
                )
            self.narrator_update.emit(verdict_icon, verdict_msg)
        else:
            self.narrator_update.emit(
                "[PAUSE]", f"VERDICT: NO TRADE | {reason[:60]}"
            )

    async def _perform_monday_resync(self):
        """
        Monday State Re-Sync (Anti-Ghosting).
        Clears stale weekend signals and pulls a fresh account summary.
        """
        logger.info(
            "[RESYNC] Monday state re-sync initiated. Clearing weekend ghosts..."
        )
        self.narrator_update.emit(
            "[BROOM]", "Monday Re-Sync: Clearing weekend stale state..."
        )

        app = self.app

        if hasattr(app, "cloud_scanner") and app.cloud_scanner:
            try:
                scanner = app.cloud_scanner.scanner
                scanner.eye_symbol = None
                scanner.eye_symbol_at = None
                scanner.priority_scan_list = []
                logger.info("[RESYNC] Scanner eye symbol and priority list cleared")
            except Exception as e:
                logger.warning("[RESYNC] Scanner clear failed: %s", e)

        try:
            if hasattr(app, "_sync_live_balance"):
                await app._sync_live_balance()
                logger.info("[RESYNC] Live balance re-synced")
            if hasattr(app, "_update_institutional_governor_ui"):
                app._update_institutional_governor_ui()
                logger.info("[RESYNC] Governor UI refreshed")
        except Exception as e:
            logger.warning("[RESYNC] Account re-sync failed: %s", e)

        self.app._monday_resync_done = True
        logger.info("[RESYNC] Monday state re-sync COMPLETE. Clean slate for the new week.")
        self.narrator_update.emit(
            "[OK]", "Monday Re-Sync COMPLETE. Fresh week, fresh slate."
        )

    def _is_sunday_gap_window(self) -> bool:
        """Return True if we are in the Sunday gap guard window (22:00-22:15 UTC)."""
        now = datetime.now(timezone.utc)
        return now.weekday() == 6 and now.hour == 22 and now.minute < 15

    def stop(self):
        self.running = False
