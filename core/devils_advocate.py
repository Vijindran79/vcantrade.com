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
- Suggested Action: {suggested_action}
- Entry Price: ${entry_price:.2f}
- Stop Loss: ${stop_loss:.2f}
- Take Profit: ${take_profit:.2f}
- Confidence: {confidence}
- Signal Type: {signal_type}
- RSI: {rsi:.1f}
- Signal Strength: {signal_strength:.2f}

Your job is to:
1. Find 2-3 specific reasons why this trade should be AVOIDED
2. Identify what the other analysts might have missed
3. Point out market conditions that make this setup dangerous
4. Suggest when would be a BETTER time to enter (if ever)

CRITICAL RULES:
- You MUST be contrarian - even if the trade looks good, find weaknesses
- Be specific about technical reasons (RSI divergence, low volume, news risk, etc.)
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
  "confidence_penalty": -0.05
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
        self.base_url = config.OLLAMA_BASE_URL
        self.model = config.OLLAMA_MODEL
        self.timeout = config.LLM_TIMEOUT
        self.challenge_count = 0
        self.total_challenges = 0
        logger.info("😈 Devil's Advocate agent initialized")

    def challenge_trade(
        self,
        market_data: MarketDataPoint,
        suggested_action: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        confidence: str,
    ) -> Dict:
        """
        Challenge a proposed trade signal.
        Returns dict with rejection reasons and confidence penalty.
        """
        self.total_challenges += 0

        try:
            prompt = PROMPT_DEVILS_ADVOCATE.format(
                asset=market_data.asset,
                suggested_action=suggested_action,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=confidence,
                signal_type=market_data.indicators.get("SIGNAL_TYPE", "Unknown"),
                rsi=market_data.indicators.get("RSI", 50.0),
                signal_strength=market_data.indicators.get("SIGNAL_STRENGTH", 0.5),
            )

            result = self._call_llm(prompt)

            if "error" in result:
                logger.warning(f"Devil's Advocate failed: {result['error']}")
                return self._default_challenge(suggested_action)

            self.challenge_count += 1
            logger.info(
                f"😈 Devil's Advocate: {result.get('rating', 'NEUTRAL')} - "
                f"{len(result.get('rejection_reasons', []))} reasons found"
            )

            return result

        except Exception as e:
            logger.error(f"Devil's Advocate error: {e}")
            return self._default_challenge(suggested_action)

    def _call_llm(self, prompt: str) -> Dict:
        """Call local Ollama for Devil's Advocate analysis."""
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
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
        """Default fallback when LLM fails."""
        return {
            "rating": "NEUTRAL",
            "rejection_reasons": ["Unable to perform deep analysis - proceed with caution"],
            "hidden_risks": "Analysis failed - unknown risks",
            "better_entry_timing": "Wait for clearer confirmation",
            "override_conditions": "Higher volume and stronger technical setup",
            "confidence_penalty": -0.05,
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
