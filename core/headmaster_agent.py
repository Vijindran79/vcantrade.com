"""
VcaniTrade AI - Headmaster Supervisor Agent
============================================
Post-trade intelligence layer with PROFIT LADDER + MICRO U-TURN CLAMP.

Architecture:
1. HIBERNATES when no position is open (zero GPU/CPU usage)
2. WAKES on position entry, grades the trade
3. Tracks live P&L with dollar-based milestone floors
4. MICRO U-TURN CLAMP: hyper-tight trailing after $250+ profit
5. Forces immediate FLATTEN when floors are breached

Prop Firm Optimized: Never give back gains. Pass evaluation drawdown rules.
"""

import logging
import time
from typing import Optional, Dict, Any

import requests
import config

logger = logging.getLogger(__name__)


class HeadmasterSupervisor:
    """
    Post-trade compliance supervisor with Profit Ladder + Micro U-Turn Clamp.
    
    HIBERNATION: Does NOTHING when no positions are open.
    ACTIVE: Monitors every 5 seconds when a position exists.
    """

    def __init__(self):
        self._active = False
        self._last_check = 0
        self._check_interval = 5  # 5 seconds
        self._position_entry_graded = False
        self._entry_grade = "UNGRADED"
        self._peak_profit_dollars = 0.0
        self._thesis_direction = ""  # BUY or SELL
        self._entry_price = 0.0
        self._entry_time = 0.0
        self._ticker = ""
        self._point_value = 2.0  # Default MNQ ($2/pt). Updated per instrument.
        self._close_command: Optional[str] = None
        
        # PROFIT LADDER STATE
        self._milestone_1_hit = False  # $100 floor activated
        self._milestone_2_hit = False  # $250+ hyper-trail activated
        self._floor_dollars = 0.0      # Current hard floor
        
        logger.info("[HEADMASTER] Supervisor initialized (hibernating until position opens)")

    # === PUBLIC API ===

    @property
    def should_close(self) -> bool:
        """Check if headmaster has issued a close command."""
        return self._close_command is not None

    @property
    def close_reason(self) -> str:
        return self._close_command or ""

    def consume_close_command(self) -> str:
        """Read and clear the close command (one-shot). Prints thank-you handshake."""
        reason = self._close_command or ""
        self._close_command = None
        if reason:
            logger.info("[HEADMASTER] Dynamic U-Turn Exit executed! Taken the profit! Thank you so much!")
        return reason

    def on_position_opened(self, ticker: str, action: str, entry_price: float, indicators: Dict[str, Any]):
        """Called when a new position opens. Wakes the headmaster."""
        self._active = True
        self._position_entry_graded = False
        self._entry_grade = "UNGRADED"
        self._peak_profit_dollars = 0.0
        self._thesis_direction = action.upper()
        self._entry_price = entry_price
        self._entry_time = time.time()
        self._ticker = ticker
        self._close_command = None
        
        # Reset ladder state
        self._milestone_1_hit = False
        self._milestone_2_hit = False
        self._floor_dollars = 0.0
        
        # Determine point value per instrument
        ticker_upper = ticker.upper()
        if "MNQ" in ticker_upper or "NQ" in ticker_upper:
            self._point_value = 2.0    # MNQ = $2/point
        elif "MES" in ticker_upper or "ES" in ticker_upper:
            self._point_value = 5.0    # MES = $5/point
        elif "MGC" in ticker_upper or "GC" in ticker_upper or "GOLD" in ticker_upper:
            self._point_value = 10.0   # MGC = $10/point
        elif "MCL" in ticker_upper or "CL" in ticker_upper or "OIL" in ticker_upper:
            self._point_value = 10.0   # MCL = $10/point
        elif "BTC" in ticker_upper:
            self._point_value = 1.0    # BTC paper = $1/point (adjust per broker)
        else:
            self._point_value = 2.0    # Default

        # Grade the entry
        rsi = float(indicators.get("RSI", 50) or 50)
        ema9 = float(indicators.get("ema9", 0) or 0)
        ema21 = float(indicators.get("ema21", 0) or 0)
        macd = float(indicators.get("macd_hist", 0) or 0)
        grade = self._grade_entry(action, rsi, ema9, ema21, macd)
        self._entry_grade = grade
        self._position_entry_graded = True

        logger.info(
            "[HEADMASTER-GRADE] %s %s @ %.2f | Grade: %s | RSI=%.0f EMA9=%.0f EMA21=%.0f MACD=%.2f | PointVal=$%.1f",
            action, ticker, entry_price, grade, rsi, ema9, ema21, macd, self._point_value
        )

    def on_position_closed(self):
        """Called when position closes. Headmaster hibernates."""
        self._active = False
        self._close_command = None
        self._peak_profit_dollars = 0.0
        self._milestone_1_hit = False
        self._milestone_2_hit = False
        self._floor_dollars = 0.0
        logger.info("[HEADMASTER] Position closed. Returning to hibernation.")

    def evaluate(self, ticker: str, current_price: float, indicators: Dict[str, Any]):
        """
        Main evaluation loop. Called every 5 seconds.
        DOES NOTHING if no position is active (hibernation).
        """
        if not self._active:
            return  # HIBERNATING

        now = time.time()
        if (now - self._last_check) < self._check_interval:
            return
        self._last_check = now

        # Calculate current P&L in dollars
        if self._thesis_direction == "BUY":
            pnl_pts = current_price - self._entry_price
        else:
            pnl_pts = self._entry_price - current_price

        pnl_dollars = pnl_pts * self._point_value

        # Track peak profit in dollars
        if pnl_dollars > self._peak_profit_dollars:
            self._peak_profit_dollars = pnl_dollars

        atr = float(indicators.get("ATR", 1.0) or 1.0)
        rsi = float(indicators.get("RSI", 50) or 50)
        macd_hist = float(indicators.get("MACD_HIST", 0) or 0)
        ema9 = float(indicators.get("EMA9", 0) or 0)

        # ==============================================================
        # PROFIT LADDER + MICRO U-TURN CLAMP
        # ==============================================================

        # --- MILESTONE 1: $100 profit → Clamp floor at $30 ---
        if not self._milestone_1_hit and self._peak_profit_dollars >= 100.0:
            self._milestone_1_hit = True
            self._floor_dollars = 30.0
            logger.info(
                "[HEADMASTER] MILESTONE 1 HIT! Peak $%.0f → Floor clamped at $%.0f. Will NEVER let position go below this.",
                self._peak_profit_dollars, self._floor_dollars
            )

        # --- MILESTONE 2: $250+ profit → Hyper-tight trailing ($40 giveback max) ---
        if not self._milestone_2_hit and self._peak_profit_dollars >= 250.0:
            self._milestone_2_hit = True
            logger.info(
                "[HEADMASTER] MILESTONE 2 HIT! Peak $%.0f → HYPER-TIGHT TRAIL ACTIVE. Max giveback: $40.",
                self._peak_profit_dollars
            )

        # --- ENFORCE MILESTONE 2: Micro U-Turn Clamp ($40 max giveback) ---
        if self._milestone_2_hit:
            giveback = self._peak_profit_dollars - pnl_dollars
            if giveback >= 40.0:
                self._close_command = (
                    f"MICRO_UTURN_CLAMP: Peak ${self._peak_profit_dollars:.0f}, "
                    f"gave back ${giveback:.0f} (>{40}). "
                    f"Closing at ${pnl_dollars:.0f} profit."
                )
                logger.warning(
                    "[HEADMASTER] MICRO U-TURN! Peak $%.0f → now $%.0f (gave back $%.0f) — KILL ORDER",
                    self._peak_profit_dollars, pnl_dollars, giveback
                )
                return

        # --- ENFORCE MILESTONE 1: Hard floor at $30 ---
        if self._milestone_1_hit and pnl_dollars <= self._floor_dollars:
            self._close_command = (
                f"FLOOR_BREACH: Position dropped to ${pnl_dollars:.0f} "
                f"(floor=${self._floor_dollars:.0f}). Protecting capital."
            )
            logger.warning(
                "[HEADMASTER] FLOOR BREACH! P&L $%.0f hit floor $%.0f — CLOSING",
                pnl_dollars, self._floor_dollars
            )
            return

        # --- PROGRESSIVE FLOOR: As profit grows, raise the floor ---
        # Between $100-$250: floor = 30% of peak
        if self._milestone_1_hit and not self._milestone_2_hit:
            new_floor = self._peak_profit_dollars * 0.30
            if new_floor > self._floor_dollars:
                self._floor_dollars = new_floor

        # ==============================================================
        # THESIS BROKEN CHECK (only when underwater)
        # ==============================================================
        if pnl_dollars < -20:
            if self._thesis_direction == "BUY":
                if macd_hist < 0 and current_price < ema9 and rsi < 38:
                    self._close_command = (
                        f"THESIS_BROKEN: Long invalidated (MACD neg, below EMA9, RSI={rsi:.0f}). "
                        f"Loss: ${pnl_dollars:.0f}"
                    )
                    logger.warning("[HEADMASTER] THESIS BROKEN for BUY %s — closing", ticker)
                    return
            else:
                if macd_hist > 0 and current_price > ema9 and rsi > 62:
                    self._close_command = (
                        f"THESIS_BROKEN: Short invalidated (MACD pos, above EMA9, RSI={rsi:.0f}). "
                        f"Loss: ${pnl_dollars:.0f}"
                    )
                    logger.warning("[HEADMASTER] THESIS BROKEN for SELL %s — closing", ticker)
                    return

        # ==============================================================
        # RSI EXHAUSTION (while in profit, before milestones)
        # ==============================================================
        if pnl_dollars > 20 and not self._milestone_1_hit:
            if self._thesis_direction == "BUY" and rsi > 80:
                self._close_command = f"EXHAUSTION: RSI={rsi:.0f} blow-off, taking ${pnl_dollars:.0f}"
                return
            if self._thesis_direction == "SELL" and rsi < 20:
                self._close_command = f"EXHAUSTION: RSI={rsi:.0f} flush, taking ${pnl_dollars:.0f}"
                return

        # ==============================================================
        # STALE TRADE (5+ min, barely moved, no milestone hit)
        # ==============================================================
        if not self._milestone_1_hit:
            time_in_trade = now - self._entry_time
            if time_in_trade > 300 and abs(pnl_dollars) < 15:
                self._close_command = (
                    f"STALE_TRADE: {int(time_in_trade)}s elapsed, P&L only ${pnl_dollars:.0f}. "
                    f"Freeing capital for next opportunity."
                )
                logger.info("[HEADMASTER] STALE TRADE on %s — closing", ticker)
                return

    # === PRIVATE ===

    def _grade_entry(self, action: str, rsi: float, ema9: float, ema21: float, macd: float) -> str:
        """Grade entry quality: A (perfect) → D (risky)."""
        score = 0
        if action == "BUY":
            if ema9 > ema21: score += 3
            if macd > 0: score += 2
            if 40 < rsi < 65: score += 2
            elif rsi > 72: score -= 2
        else:
            if ema9 < ema21: score += 3
            if macd < 0: score += 2
            if 35 < rsi < 60: score += 2
            elif rsi < 28: score -= 2

        if score >= 6: return "A (EXCELLENT)"
        elif score >= 4: return "B (GOOD)"
        elif score >= 2: return "C (ACCEPTABLE)"
        else: return "D (HIGH RISK)"
