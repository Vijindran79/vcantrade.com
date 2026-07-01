"""
VcaniTrade AI - Headmaster Supervisor Agent (REFLEX EDITION)
============================================================
ULTRA-LOW-LATENCY exit engine. NO LLM CALLS WHEN POSITIONS ARE OPEN.

Architecture:
1. HIBERNATES when no position is open (zero GPU/CPU usage)
2. WAKES on position entry, switches to a NATIVE 500ms Python loop
3. ZERO Ollama/qwen HTTP traffic while in-position — exits are pure-Python math
4. Implements Oliver Velez "4-LEG" momentum engine:
   - RULE A: 4 consecutive 2-min candles in trade direction away from 20 EMA
             OR 60 pips / +$200.00 profit → activate HYPER-TIGHT TRAILING CLAMP
   - RULE B: $40 retracement from peak OR micro reversal wick → MICRO U-TURN KILL
5. On trip: async Playwright flatten + lock reset + scanner rearms for next wave

The class can be run in two modes:
  * `evaluate(...)` — legacy 5-second path (still used for non-critical checks)
  * `evaluate_native(candles, ...)` — 500ms path; THIS is the one used in AUTONOMOUS

The native path is the only one allowed to call exit decisions while a position is open.
"""

import logging
import time
import threading
from collections import deque
from typing import Optional, Dict, Any, Deque, Tuple

import config

logger = logging.getLogger(__name__)


# ============================================================================
# SPEED-OPTIMIZED CONSTANTS (NO LLM, NO HTTP, PURE PYTHON)
# ============================================================================

# --- VELEZ 4-LEG ENGINE ---
VELEZ_LEG_COUNT = 4                  # Consecutive 2-min candles required
VELEZ_CANDLE_TIMEFRAME = "2m"        # 2-minute bars
EMA_PERIOD = 20                      # 20 EMA on 2-min chart

# --- PROFIT APEX THRESHOLDS ---
APEX_PIPS = 60.0                     # 60 pips triggers hyper-tight clamp
APEX_DOLLARS = 200.0                 # ...or +$200.00 (whichever first)
HARD_TARGET_DOLLARS = 200.0          # Mirror for compatibility

# --- MICRO U-TURN KILL SWITCH ---
UTR_GIVEBACK_DOLLARS = 40.0          # $40 giveback from peak → instant exit
MICRO_WICK_REVERSAL_RATIO = 0.35     # Wick > 35% of candle body = reversal wick

# --- LATENCY TARGETS ---
NATIVE_LOOP_INTERVAL_SEC = 0.5       # 500ms — matches required cadence


class HeadmasterSupervisor:
    """
    REFLEX exit engine. Decoupled from Ollama when active_positions > 0.

    When a position is open the supervisor runs a NATIVE 500ms Python loop that
    uses only the in-memory candle deque (fed by Chrome CDP websocket agent).
    NO HTTP API calls to qwen / Ollama are issued while in-position.
    """

    def __init__(self):
        # --- LIFECYCLE ---
        self._active = False
        self._last_check = 0.0
        self._check_interval = 5              # Legacy 5s path
        self._native_loop_interval = NATIVE_LOOP_INTERVAL_SEC
        self._last_native_check = 0.0

        # --- ENTRY STATE ---
        self._position_entry_graded = False
        self._entry_grade = "UNGRADED"
        self._entry_price = 0.0
        self._entry_time = 0.0
        self._ticker = ""
        self._point_value = 2.0
        self._thesis_direction = ""

        # --- PROFIT / TRAIL STATE ---
        self._peak_profit_dollars = 0.0
        self._peak_profit_price = 0.0       # Highest favorable price seen
        self._milestone_1_hit = False        # $100
        self._milestone_2_hit = False        # $250 (legacy)
        self._floor_dollars = 0.0
        self._close_command: Optional[str] = None

        # --- VELEZ 4-LEG STATE ---
        self._velez_leg_count = 0
        self._velez_clamp_active = False
        self._velez_clamp_armed_at = 0.0     # When clamp activated
        self._velez_leg_directions: Deque[int] = deque(maxlen=VELEZ_LEG_COUNT + 1)

        # --- NATIVE 2-MIN CANDLE DEQUE (filled by Chrome CDP websocket agent) ---
        # Each entry: dict with keys: o, h, l, c, v, ts
        self._candle_deque: Deque[Dict[str, float]] = deque(maxlen=64)
        self._ema20_value: Optional[float] = None
        self._last_candle_update_ts: float = 0.0

        # --- EXECUTION BRIDGE (set by engine) ---
        # These are injected by the engine after construction so the headmaster
        # never has to import the executor/scanner — keeps decoupling clean.
        self._flatten_callable = None         # async-friendly callable(ticker, reason)
        self._state_reset_callable = None     # callable(ticker) → resets engine locks
        self._scanner_rearm_callable = None   # callable() → rearm scanner for next wave

        # --- THREAD SAFETY ---
        self._lock = threading.RLock()
        self._native_thread: Optional[threading.Thread] = None
        self._native_stop_flag = threading.Event()

        logger.info(
            "[HEADMASTER-REFLEX] Initialized — hibernation mode, 500ms native loop armed, "
            "NO LLM CALLS WHILE IN-POSITION"
        )

    # ====================================================================
    # PUBLIC API — INJECTION POINTS
    # ====================================================================

    def set_execution_bridge(self, flatten_callable, state_reset_callable, scanner_rearm_callable):
        """Engine injects the async flatten / lock-reset / scanner-rearm callables.
        Keeps the headmaster decoupled from RPA / engine imports."""
        with self._lock:
            self._flatten_callable = flatten_callable
            self._state_reset_callable = state_reset_callable
            self._scanner_rearm_callable = scanner_rearm_callable
        logger.info("[HEADMASTER-REFLEX] Execution bridge injected — flatten/lock/scan wired")

    @property
    def should_close(self) -> bool:
        return self._close_command is not None

    @property
    def close_reason(self) -> str:
        return self._close_command or ""

    def consume_close_command(self) -> str:
        """One-shot read of the close command. Also runs the full handshake
        (flatten → reset locks → rearm scanner) so callers just need to log
        the returned reason. Designed for the 500ms native path AND legacy."""
        with self._lock:
            reason = self._close_command or ""
            self._close_command = None
        if reason:
            logger.info("[HEADMASTER] Dynamic U-Turn Exit executed! Taken the profit! Thank you so much!")
            self._run_velez_handshake(reason)
        return reason

    # ====================================================================
    # LIFECYCLE
    # ====================================================================

    def on_position_opened(self, ticker: str, action: str, entry_price: float, indicators: Dict[str, Any]):
        """Wake the headmaster. Starts the native 500ms Python thread.
        CRITICAL: This method does NOT call Ollama. No HTTP. No LLM."""
        with self._lock:
            self._active = True
            self._position_entry_graded = False
            self._entry_grade = "UNGRADED"
            self._peak_profit_dollars = 0.0
            self._peak_profit_price = entry_price
            self._thesis_direction = action.upper()
            self._entry_price = entry_price
            self._entry_time = time.time()
            self._ticker = ticker
            self._close_command = None

            # Reset ladder + Velez state
            self._milestone_1_hit = False
            self._milestone_2_hit = False
            self._floor_dollars = 0.0
            self._velez_leg_count = 0
            self._velez_clamp_active = False
            self._velez_clamp_armed_at = 0.0
            self._velez_leg_directions.clear()
            self._candle_deque.clear()
            self._ema20_value = None

            # Instrument point value
            ticker_upper = ticker.upper()
            if "MNQ" in ticker_upper or "NQ" in ticker_upper:
                self._point_value = 2.0
            elif "MES" in ticker_upper or "ES" in ticker_upper:
                self._point_value = 5.0
            elif "MGC" in ticker_upper or "GC" in ticker_upper or "GOLD" in ticker_upper:
                self._point_value = 10.0
            elif "MCL" in ticker_upper or "CL" in ticker_upper or "OIL" in ticker_upper:
                self._point_value = 10.0
            elif "BTC" in ticker_upper:
                self._point_value = 1.0
            else:
                self._point_value = 2.0

            # Grade entry (still pure math, no LLM)
            rsi = float(indicators.get("RSI", 50) or 50)
            ema9 = float(indicators.get("ema9", 0) or 0)
            ema21 = float(indicators.get("ema21", 0) or 0)
            macd = float(indicators.get("macd_hist", 0) or 0)
            self._entry_grade = self._grade_entry(action, rsi, ema9, ema21, macd)
            self._position_entry_graded = True

        # Start the native 500ms thread — outside the lock
        self._start_native_thread()
        logger.info(
            "[HEADMASTER-REFLEX] WAKE %s %s @ %.2f | Grade: %s | PointVal=$%.1f | 500ms NATIVE LOOP ACTIVE (NO LLM)",
            action, ticker, entry_price, self._entry_grade, self._point_value,
        )

    def on_position_closed(self):
        """Hibernate. Stops the native thread and clears all state."""
        with self._lock:
            self._active = False
            self._close_command = None
            self._peak_profit_dollars = 0.0
            self._peak_profit_price = 0.0
            self._milestone_1_hit = False
            self._milestone_2_hit = False
            self._floor_dollars = 0.0
            self._velez_leg_count = 0
            self._velez_clamp_active = False
            self._velez_clamp_armed_at = 0.0
            self._velez_leg_directions.clear()
            self._candle_deque.clear()
            self._ema20_value = None
        self._stop_native_thread()
        logger.info("[HEADMASTER-REFLEX] HIBERNATION — all state cleared, 500ms thread stopped")

    # ====================================================================
    # NATIVE 500ms LOOP — THE REFLEX ENGINE
    # ====================================================================

    def _start_native_thread(self):
        """Boot the in-position 500ms thread. Pure Python. NO HTTP. NO LLM."""
        if self._native_thread and self._native_thread.is_alive():
            return  # Already running
        self._native_stop_flag.clear()
        self._native_thread = threading.Thread(
            target=self._native_loop,
            name="HeadmasterReflex-500ms",
            daemon=True,
        )
        self._native_thread.start()
        logger.info("[HEADMASTER-REFLEX] Native 500ms loop thread started")

    def _stop_native_thread(self):
        if self._native_thread and self._native_thread.is_alive():
            self._native_stop_flag.set()
            self._native_thread.join(timeout=1.0)
        self._native_thread = None
        logger.info("[HEADMASTER-REFLEX] Native 500ms loop thread stopped")

    def _native_loop(self):
        """The reflex loop. Runs at 500ms cadence while a position is open.
        ABSOLUTELY NO OLLAMA / QWEN CALLS. Pure numerical state machine."""
        next_run = time.monotonic()
        while not self._native_stop_flag.is_set():
            now = time.monotonic()
            if now < next_run:
                # Sleep exactly the remaining interval (no busy-wait)
                self._native_stop_flag.wait(next_run - now)
                continue
            next_run = now + self._native_loop_interval

            # Snapshot under lock; do work outside it
            with self._lock:
                if not self._active:
                    return
                ticker = self._ticker
                direction = self._thesis_direction
                entry = self._entry_price
                pv = self._point_value
                candle_snapshot = list(self._candle_deque)
                ema20 = self._ema20_value
                peak_price = self._peak_profit_price
                last_price = self._extract_last_price(candle_snapshot) if candle_snapshot else entry

            if not ticker or last_price <= 0:
                continue

            # P&L in dollars (sign-aware)
            if direction == "BUY":
                pnl_pts = last_price - entry
            else:
                pnl_pts = entry - last_price
            pnl_dollars = pnl_pts * pv

            # Track peak price for the Velez micro-u-turn math
            with self._lock:
                if direction == "BUY" and last_price > self._peak_profit_price:
                    self._peak_profit_price = last_price
                    peak_price = last_price
                elif direction == "SELL" and last_price < self._peak_profit_price:
                    self._peak_profit_price = last_price
                    peak_price = last_price
                if pnl_dollars > self._peak_profit_dollars:
                    self._peak_profit_dollars = pnl_dollars

            # Update the 2-min candle deque + EMA20 in-place
            self._update_candle_state(candle_snapshot, last_price)

            # ============================================================
            # VELEZ 4-LEG ENGINE (RULE A)
            # ============================================================
            self._evaluate_velez_4_leg(direction, last_price, ema20, candle_snapshot)

            # ============================================================
            # PROFIT APEX → HYPER-TIGHT TRAILING CLAMP
            # ============================================================
            self._evaluate_apex_clamp(direction, last_price, pnl_dollars)

            # ============================================================
            # RULE B: MICRO U-TURN KILL SWITCH
            # ============================================================
            self._evaluate_micro_u_turn(direction, last_price, pnl_dollars, peak_price, candle_snapshot)

    def _extract_last_price(self, candles) -> float:
        if not candles:
            return 0.0
        last = candles[-1]
        return float(last.get("c", 0.0) or 0.0)

    def _update_candle_state(self, candles, current_price: float):
        """Append a tick to the deque and recompute EMA20 (in-place, pure math)."""
        if not candles:
            # First tick — seed the deque with a synthetic 2-min candle
            now_ts = time.time()
            new_candle = {"o": current_price, "h": current_price, "l": current_price, "c": current_price, "v": 0.0, "ts": now_ts}
            with self._lock:
                self._candle_deque.append(new_candle)
                self._ema20_value = current_price
            return

        last = candles[-1]
        ts_now = time.time()
        # If more than 2 minutes have passed since the last candle's timestamp, roll over
        last_ts = float(last.get("ts", 0.0) or 0.0)
        if (ts_now - last_ts) >= 120.0:
            with self._lock:
                self._candle_deque.append({
                    "o": current_price, "h": current_price,
                    "l": current_price, "c": current_price, "v": 0.0, "ts": ts_now,
                })
        else:
            # Update the running candle in-place
            with self._lock:
                last["c"] = current_price
                if current_price > last.get("h", current_price):
                    last["h"] = current_price
                if current_price < last.get("l", current_price):
                    last["l"] = current_price
                # Truncate deque head if it grew past maxlen
                if len(self._candle_deque) == self._candle_deque.maxlen:
                    pass  # deque auto-evicts
                self._candle_deque[-1] = last

        # Recompute EMA20 on the close series
        closes = [c.get("c", 0.0) for c in self._candle_deque]
        if len(closes) >= EMA_PERIOD:
            ema = closes[0]
            k = 2.0 / (EMA_PERIOD + 1.0)
            for px in closes[1:]:
                ema = px * k + ema * (1.0 - k)
            with self._lock:
                self._ema20_value = ema
        else:
            with self._lock:
                self._ema20_value = sum(closes) / max(len(closes), 1)

    # ====================================================================
    # VELEZ 4-LEG MOMENTUM ENGINE
    # ====================================================================

    def _evaluate_velez_4_leg(self, direction: str, last_price: float, ema20: Optional[float], candles):
        """RULE A: Count consecutive 2-min candles moving in our direction away
        from the 20 EMA. The moment the 4th leg completes, OR profit apex
        (60 pips / +$200.00) is touched, activate the Hyper-Tight Trailing Clamp.
        """
        if self._velez_clamp_active or not candles or ema20 is None:
            return

        # We need 4 complete 2-min candles beyond the entry
        if len(candles) < VELEZ_LEG_COUNT:
            return

        # Look at the last 4 closed candles
        recent = list(candles)[-VELEZ_LEG_COUNT:]
        if direction == "BUY":
            # Each leg must be a green candle (close > open) AND close > ema20
            legs_ok = all(
                (c.get("c", 0.0) > c.get("o", 0.0)) and (c.get("c", 0.0) > ema20)
                for c in recent
            )
        else:  # SELL
            # Each leg must be a red candle (close < open) AND close < ema20
            legs_ok = all(
                (c.get("c", 0.0) < c.get("o", 0.0)) and (c.get("c", 0.0) < ema20)
                for c in recent
            )

        if legs_ok:
            self._velez_leg_count += 1
        else:
            self._velez_leg_count = 0

        if self._velez_leg_count >= VELEZ_LEG_COUNT:
            self._activate_velez_clamp(
                reason=f"VELEZ_4_LEG: {VELEZ_LEG_COUNT} consecutive 2-min candles in {direction} direction, "
                       f"each beyond 20 EMA — momentum confirmed"
            )

    def _evaluate_apex_clamp(self, direction: str, last_price: float, pnl_dollars: float):
        """Activate the clamp when open trade equity touches the profit apex
        push of 60 pips OR the hard target threshold of +$200.00."""
        if self._velez_clamp_active:
            return

        # 60-pip check (assuming a "pip" = $0.01 on MNQ/MES; full point = $1 on futures)
        # We measure pips in POINTS (1.0 per $1 move on a $1/pt contract). On MNQ that's
        # actually 0.5 points per "pip" per industry convention. To be safe we measure
        # BOTH: absolute dollar threshold AND a points threshold.
        pips_pts = abs(pnl_dollars) / max(self._point_value, 1e-9)
        if pips_pts >= APEX_PIPS or pnl_dollars >= APEX_DOLLARS or pnl_dollars >= HARD_TARGET_DOLLARS:
            self._activate_velez_clamp(
                reason=f"APEX_PUSH: profit hit {pips_pts:.1f} pips / ${pnl_dollars:.0f} — "
                       f"hyper-tight trailing clamp engaging"
            )

    def _activate_velez_clamp(self, reason: str):
        """Flip the clamp on. From this point RULE B (Micro U-Turn Kill) becomes armed."""
        with self._lock:
            if self._velez_clamp_active:
                return
            self._velez_clamp_active = True
            self._velez_clamp_armed_at = time.time()
        logger.warning(
            "[VELEZ-CLAMP] ARMED: %s | 4-leg momentum OR apex confirmed. "
            "Micro U-Turn Kill Switch now ACTIVE — $40 giveback triggers instant exit.",
            reason,
        )

    def _evaluate_micro_u_turn(self, direction: str, last_price: float, pnl_dollars: float,
                                peak_price: float, candles):
        """RULE B: Once the clamp is active, trip an instantaneous exit if:
        (a) price retraces by more than $40.00 from absolute recorded peak profit apex, OR
        (b) the current candle prints a micro reversal wick.
        """
        if not self._velez_clamp_active:
            return

        # --- (a) Dollar giveback from peak ---
        peak_pnl_dollars = self._peak_profit_dollars
        if peak_pnl_dollars > 0:
            giveback = peak_pnl_dollars - pnl_dollars
            if giveback >= UTR_GIVEBACK_DOLLARS:
                self._issue_velez_exit(
                    f"VELEZ_MICRO_UTURN: peak ${peak_pnl_dollars:.0f} → now ${pnl_dollars:.0f} "
                    f"(gave back ${giveback:.0f} ≥ $40). Hard U-Turn Detected!"
                )
                return

        # --- (b) Micro reversal wick on the current candle ---
        if not candles:
            return
        cur = candles[-1]
        o = float(cur.get("o", 0.0) or 0.0)
        h = float(cur.get("h", 0.0) or 0.0)
        l = float(cur.get("l", 0.0) or 0.0)
        c = float(cur.get("c", 0.0) or 0.0)
        body = abs(c - o)
        if body <= 0:
            return
        if direction == "BUY":
            # A long reversal wick (lower wick) shows the bid is drying up
            lower_wick = min(o, c) - l
            if lower_wick / body >= MICRO_WICK_REVERSAL_RATIO and pnl_dollars > 0:
                self._issue_velez_exit(
                    f"VELEZ_MICRO_WICK: BUY reversal wick {lower_wick:.2f} pts "
                    f"({lower_wick/body*100:.0f}% of body). Hard U-Turn Detected!"
                )
                return
        else:  # SELL
            upper_wick = h - max(o, c)
            if upper_wick / body >= MICRO_WICK_REVERSAL_RATIO and pnl_dollars > 0:
                self._issue_velez_exit(
                    f"VELEZ_MICRO_WICK: SELL reversal wick {upper_wick:.2f} pts "
                    f"({upper_wick/body*100:.0f}% of body). Hard U-Turn Detected!"
                )
                return

    def _issue_velez_exit(self, reason: str):
        """Set the close command. The handshake will be run by consume_close_command()
        or by the native loop's next iteration that sees should_close=True."""
        with self._lock:
            if self._close_command is not None:
                return
            self._close_command = reason
        logger.warning("[VELEZ-REFLEX] %s", reason)

    def _run_velez_handshake(self, reason: str):
        """The 4-step handshake from the spec:
          1) Async Playwright flatten click via rpa_executor
          2) Clear internal asset execution tracking state + single-asset locks
          3) Print the exact required status sequence
          4) Rearm scanner for next opportunity
        """
        ticker = self._ticker

        # STEP 1: Async flatten click (fire-and-forget; do not block the loop)
        if self._flatten_callable is not None:
            try:
                self._flatten_callable(ticker, reason)
            except Exception as exc:
                logger.error("[VELEZ-REFLEX] flatten_callable failed: %s", exc)
        else:
            logger.warning("[VELEZ-REFLEX] No flatten_callable injected — skipping click (dev mode)")

        # STEP 2: Clear internal state + asset locks
        if self._state_reset_callable is not None:
            try:
                self._state_reset_callable(ticker)
            except Exception as exc:
                logger.error("[VELEZ-REFLEX] state_reset_callable failed: %s", exc)

        # STEP 3: The exact required console output
        print("[VELEZ-REFLEX] Hard U-Turn Detected! Profit secured instantly via script. Resetting engine locks. Thank you!")
        logger.info("[VELEZ-REFLEX] Handshake complete — locks released, scanner rearming")

        # STEP 4: Open filters + rearm scanner
        if self._scanner_rearm_callable is not None:
            try:
                self._scanner_rearm_callable()
            except Exception as exc:
                logger.error("[VELEZ-REFLEX] scanner_rearm_callable failed: %s", exc)

    # ====================================================================
    # LEGACY 5-SECOND PATH (kept for non-critical checks; not used for exits)
    # ====================================================================

    def evaluate(self, ticker: str, current_price: float, indicators: Dict[str, Any]):
        """Legacy 5-second path. Used by the dashboard's slow path / debug views.
        The reflex engine in the native 500ms thread is the AUTHORITATIVE source
        of exit decisions. This method only sets informational _close_command
        when the legacy ladder is breached."""
        if not self._active:
            return

        now = time.time()
        if (now - self._last_check) < self._check_interval:
            return
        self._last_check = now

        # P&L
        if self._thesis_direction == "BUY":
            pnl_pts = current_price - self._entry_price
        else:
            pnl_pts = self._entry_price - current_price
        pnl_dollars = pnl_pts * self._point_value

        if pnl_dollars > self._peak_profit_dollars:
            self._peak_profit_dollars = pnl_dollars

        atr = float(indicators.get("ATR", 1.0) or 1.0)
        rsi = float(indicators.get("RSI", 50) or 50)
        macd_hist = float(indicators.get("MACD_HIST", 0) or 0)
        ema9 = float(indicators.get("EMA9", 0) or 0)

        # Milestone 1: $100 → $30 floor
        if not self._milestone_1_hit and self._peak_profit_dollars >= 100.0:
            self._milestone_1_hit = True
            self._floor_dollars = 30.0
            logger.info("[HEADMASTER] MILESTONE 1 HIT! Peak $%.0f → Floor $%.0f",
                        self._peak_profit_dollars, self._floor_dollars)

        # Milestone 2: $250
        if not self._milestone_2_hit and self._peak_profit_dollars >= 250.0:
            self._milestone_2_hit = True
            logger.info("[HEADMASTER] MILESTONE 2 HIT! Peak $%.0f — max giveback $40",
                        self._peak_profit_dollars)

        # Micro U-Turn clamp (legacy — superseded by Velez 4-Leg but kept as safety net)
        if self._milestone_2_hit:
            giveback = self._peak_profit_dollars - pnl_dollars
            if giveback >= UTR_GIVEBACK_DOLLARS:
                with self._lock:
                    if self._close_command is None:
                        self._close_command = (
                            f"MICRO_UTURN_CLAMP: Peak ${self._peak_profit_dollars:.0f}, "
                            f"gave back ${giveback:.0f} (>$40)."
                        )
                return

        # Floor breach
        if self._milestone_1_hit and pnl_dollars <= self._floor_dollars:
            with self._lock:
                if self._close_command is None:
                    self._close_command = (
                        f"FLOOR_BREACH: P&L ${pnl_dollars:.0f} hit floor ${self._floor_dollars:.0f}"
                    )
            return

        # Progressive floor
        if self._milestone_1_hit and not self._milestone_2_hit:
            new_floor = self._peak_profit_dollars * 0.30
            if new_floor > self._floor_dollars:
                self._floor_dollars = new_floor

        # Thesis broken
        if pnl_dollars < -20:
            if self._thesis_direction == "BUY":
                if macd_hist < 0 and current_price < ema9 and rsi < 38:
                    with self._lock:
                        if self._close_command is None:
                            self._close_command = f"THESIS_BROKEN: Long invalidated (RSI={rsi:.0f})"
                    return
            else:
                if macd_hist > 0 and current_price > ema9 and rsi > 62:
                    with self._lock:
                        if self._close_command is None:
                            self._close_command = f"THESIS_BROKEN: Short invalidated (RSI={rsi:.0f})"
                    return

        # Stale
        if not self._milestone_1_hit:
            time_in_trade = now - self._entry_time
            if time_in_trade > 300 and abs(pnl_dollars) < 15:
                with self._lock:
                    if self._close_command is None:
                        self._close_command = f"STALE_TRADE: {int(time_in_trade)}s, P&L ${pnl_dollars:.0f}"

    # ====================================================================
    # PURE-PYTHON HELPERS
    # ====================================================================

    def _grade_entry(self, action: str, rsi: float, ema9: float, ema21: float, macd: float) -> str:
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

    # ====================================================================
    # DIAGNOSTICS
    # ====================================================================

    def get_native_state(self) -> Dict[str, Any]:
        """Snapshot for the dashboard / health endpoint."""
        with self._lock:
            return {
                "active": self._active,
                "ticker": self._ticker,
                "direction": self._thesis_direction,
                "entry_price": self._entry_price,
                "peak_profit_dollars": self._peak_profit_dollars,
                "velez_leg_count": self._velez_leg_count,
                "velez_clamp_active": self._velez_clamp_active,
                "candles_in_deque": len(self._candle_deque),
                "ema20": self._ema20_value,
                "native_thread_alive": bool(self._native_thread and self._native_thread.is_alive()),
                "should_close": self.should_close,
            }
