"""
VcanTrade AI - Devil's Advocate Agent

The Skeptic - Specifically tries to find reasons NOT to take a trade.
Acts as a contrarian voice in the Swarm debate to prevent bad trades.

Role:
- Challenge every trade signal from the opposite perspective
- Find hidden risks the other agents missed
- Identify market conditions that invalidate the setup
- Protect capital by being hyper-conservative
"""

import json
import logging
from typing import Dict, Optional
from datetime import datetime

import requests

import config
from core.ollama_utils import build_ollama_url, normalize_ollama_base_url
from core.models import MarketDataPoint, SignalAction

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Devil's Advocate Prompt Template
# ---------------------------------------------------------------------------

PROMPT_DEVILS_ADVOCATE = """\
You are the DEVIL'S ADVOCATE - your ONLY job is to find reasons NOT to take
this trade. You are the skeptical voice that challenges every assumption.

Trade Signal to Challenge:
- Asset: {asset}
- Runtime Mode: {runtime_mode}
- Session Context: {session_context}
- Suggested Action: {suggested_action}
- Entry Price: ${entry_price:.2f}
- Stop Loss: ${stop_loss:.2f}
- Take Profit: ${take_profit:.2f}
- Confidence: {confidence}
- Signal Type: {signal_type}
- RSI: {rsi:.1f}
- Signal Strength: {signal_strength:.2f}
- Liquidity Sweep: {liquidity_sweep}

Your job is to:
1. Find 2-3 specific reasons why this trade should be AVOIDED
2. Identify what the other analysts might have missed
3. Point out market conditions that make this setup dangerous
4. Suggest when would be a BETTER time to enter (if ever)

CRITICAL RULES:
- You MUST be contrarian - even if the trade looks good, find weaknesses
- Be specific about technical reasons (RSI divergence, low volume, news risk, etc.)
- In AUTONOMOUS mode, do NOT penalize trades just because RSI is neutral; liquidity sweep context should dominate neutral-RSI hesitation
- Do NOT downgrade trades solely because it is Saturday, Sunday, or the weekend. Crypto and futures markets operate through weekends.
- If the setup is genuinely terrible, give it a "STRONG_AVOID" rating
- If it has minor issues, give it "CAUTIOUS" rating with warnings
- Only give "NEUTRAL" if you truly can't find major flaws (rare!)

Respond in STRICT JSON only:
{{
  "rating": "STRONG_AVOID|CAUTIOUS|NEUTRAL",
  "rejection_reasons": [
    "reason 1 - be specific",
    "reason 2 - be specific",
    "reason 3 - optional"
  ],
  "hidden_risks": "what other agents missed - 1 sentence",
  "better_entry_timing": "when would be safer - 1 sentence",
  "override_conditions": "what would change your mind - 1 sentence",
    "confidence_penalty": -0.15
}}
"""


# ---------------------------------------------------------------------------
# Devil's Advocate Agent
# ---------------------------------------------------------------------------

class DevilsAdvocate:
    """
    The Skeptic - Finds reasons NOT to take trades.
    Reduces confidence score when risks are identified.
    """

    def __init__(self):
        self.base_url = normalize_ollama_base_url(config.OLLAMA_BASE_URL)
        self.model = config.OLLAMA_MODEL
        self.timeout = config.LLM_TIMEOUT
        self.challenge_count = 0
        self.total_challenges = 0
        logger.info("[DEVIL] Devil's Advocate agent initialized")

    def challenge_trade(
        self,
        market_data: MarketDataPoint,
        suggested_action: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        confidence: str,
        session_context: Optional[Dict] = None,
    ) -> Dict:
        """
        Challenge a proposed trade signal.
        Returns dict with rejection reasons and confidence penalty.
        """
        self.total_challenges += 1

        try:
            runtime_mode = str(market_data.indicators.get("RUNTIME_MODE", "TEACHER") or "TEACHER").upper()
            liquidity_sweep = market_data.indicators.get("LIQUIDITY_SWEEP") or {}
            prompt = PROMPT_DEVILS_ADVOCATE.format(
                asset=market_data.asset,
                runtime_mode=runtime_mode,
                session_context=json.dumps(session_context or {}, ensure_ascii=False),
                suggested_action=suggested_action,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=confidence,
                signal_type=market_data.indicators.get("SIGNAL_TYPE", "Unknown"),
                rsi=market_data.indicators.get("RSI", 50.0),
                signal_strength=market_data.indicators.get("SIGNAL_STRENGTH", 0.5),
                liquidity_sweep=json.dumps(liquidity_sweep) if liquidity_sweep else "none",
            )

            result = self._call_llm(prompt)

            if "error" in result:
                logger.warning(f"Devil's Advocate failed: {result['error']}")
                return self._default_challenge(suggested_action)

            result = self._apply_temporal_guardrails(result, session_context=session_context)
            result = self._apply_weekend_relaxation(result, session_context=session_context)
            result = self._apply_autonomous_liquidity_override(
                result=result,
                market_data=market_data,
                runtime_mode=runtime_mode,
            )

            self.challenge_count += 1
            logger.info(
                f"[DEVIL] Devil's Advocate: {result.get('rating', 'NEUTRAL')} - "
                f"{len(result.get('rejection_reasons', []))} reasons found"
            )

            return result

        except Exception as e:
            logger.error(f"Devil's Advocate error: {e}")
            return self._default_challenge(suggested_action)

    def _apply_autonomous_liquidity_override(self, result: Dict, market_data: MarketDataPoint, runtime_mode: str) -> Dict:
        """Prevent neutral RSI from downgrading autonomous liquidity-sweep setups."""
        if runtime_mode != "AUTONOMOUS":
            return result

        rsi_value = float(market_data.indicators.get("RSI", 50.0) or 50.0)
        liquidity_sweep = market_data.indicators.get("LIQUIDITY_SWEEP") or {}
        signal_type = str(market_data.indicators.get("SIGNAL_TYPE", "") or "").upper()
        has_liquidity_driver = bool(liquidity_sweep) or signal_type.startswith("LIQUIDITY_SWEEP")
        neutral_rsi = 40.0 <= rsi_value <= 60.0

        if not has_liquidity_driver or not neutral_rsi:
            return result

        rating = str(result.get("rating", "NEUTRAL") or "NEUTRAL").upper()
        reasons = [str(reason) for reason in result.get("rejection_reasons", [])]
        neutral_rsi_reason = any("rsi" in reason.lower() for reason in reasons)

        if neutral_rsi_reason:
            logger.info(
                "[DEVIL] AUTONOMOUS OVERRIDE: clearing neutral-RSI Devil's Advocate penalty for %s because liquidity sweep is the primary driver",
                market_data.asset,
            )
            result["rating"] = "NEUTRAL"
            result["confidence_penalty"] = 0.0
            result["rejection_reasons"] = [
                reason for reason in reasons if "rsi" not in reason.lower()
            ]
            if not result["rejection_reasons"]:
                result["rejection_reasons"] = [
                    "Autonomous liquidity-sweep setup retained; neutral RSI alone is not a blocker."
                ]

        return result

    def _apply_weekend_relaxation(self, result: Dict, session_context: Optional[Dict]) -> Dict:
        """Slightly reduce Devil's Advocate penalties during weekend sessions."""
        context = session_context or {}
        if not context.get("is_weekend"):
            return result
        rating = str(result.get("rating", "NEUTRAL") or "NEUTRAL").upper()
        current_penalty = float(result.get("confidence_penalty", -0.15) or -0.15)
        # Weekend: downgrade STRONG_AVOID to CAUTIOUS, cap penalty at -0.10
        if rating == "STRONG_AVOID":
            result["rating"] = "CAUTIOUS"
            result["confidence_penalty"] = max(current_penalty, -0.10)
            logger.info("[DEVIL] WEEKEND: STRONG_AVOID downgraded to CAUTIOUS")
        elif current_penalty < -0.10:
            result["confidence_penalty"] = max(current_penalty, -0.10)
            logger.info("[DEVIL] WEEKEND: penalty capped at -0.10")
        return result

    def _apply_temporal_guardrails(self, result: Dict, session_context: Optional[Dict]) -> Dict:
        """Raise stronger objections during market holidays and late Friday trade windows."""
        context = session_context or {}
        reasons = [str(reason) for reason in result.get("rejection_reasons", [])]

        if context.get("is_holiday_us") or context.get("is_holiday_hk"):
            holiday_name = str(context.get("holiday_name") or "Market holiday")
            reasons.insert(0, f"{holiday_name} reduces liquidity and follow-through for fresh entries.")
            result["rating"] = "STRONG_AVOID"
            result["confidence_penalty"] = min(float(result.get("confidence_penalty", -0.15)), -0.25)
            result["better_entry_timing"] = "Wait for the next full market session after the holiday."
            result["hidden_risks"] = "Holiday conditions can distort price discovery and widen slippage."

        if context.get("is_friday_close_window"):
            cutoff = int(context.get("friday_close_cutoff_utc", getattr(config, "FRIDAY_CLOSE_CUTOFF_UTC", 18)) or 18)
            reasons.insert(
                0,
                f"Friday after {cutoff:02d}:00 UTC is a close-risk window with reduced follow-through and gap risk."
            )
            current_penalty = float(result.get("confidence_penalty", -0.15) or -0.15)
            result["rating"] = "STRONG_AVOID" if str(result.get("rating", "")).upper() != "STRONG_AVOID" else result["rating"]
            result["confidence_penalty"] = min(current_penalty, -0.25)
            result["better_entry_timing"] = "Stand down into the weekend or wait for Monday liquidity."
            result["override_conditions"] = "Only reconsider if this is an active position-management action, not a fresh entry."

        result["rejection_reasons"] = reasons[:3] if reasons else result.get("rejection_reasons", [])
        return result

    def _call_llm(self, prompt: str) -> Dict:
        """Call local Ollama for Devil's Advocate analysis."""
        try:
            resp = requests.post(
                build_ollama_url(self.base_url, "api/generate"),
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": 0.3,  # Slightly higher for creative skepticism
                        "num_predict": 512,
                    }
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "{}")
            return json.loads(raw)
        except Exception as e:
            return {"error": str(e)}

    def _default_challenge(self, suggested_action: str) -> Dict:
        """Default fallback when LLM fails — conservative by design."""
        return {
            "rating": "CAUTIOUS",
            "rejection_reasons": ["Devil's Advocate analysis unavailable - unknown risks remain"],
            "hidden_risks": "Analysis failed - cannot verify hidden dangers",
            "better_entry_timing": "Wait for full analysis confirmation",
            "override_conditions": "Successful Devil's Advocate analysis required",
            "confidence_penalty": -0.10,
        }

    def get_challenge_stats(self) -> Dict:
        """Get statistics about Devil's Advocate performance."""
        return {
            "total_challenges": self.total_challenges,
            "successful_challenges": self.challenge_count,
            "success_rate": (
                self.challenge_count / max(1, self.total_challenges) * 100
            ),
        }
