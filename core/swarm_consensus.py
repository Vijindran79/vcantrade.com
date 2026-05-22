"""
VcaniTrade AI - Local Qwen 2.5 Brain

100% local execution using Ollama + Qwen 2.5:7b
No cloud dependencies, no API tokens needed!
"""

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple
import requests

import config
from core.models import (
    ConfidenceLevel,
    DebateTranscript,
    LLMAnalysisOutput,
    MarketDataPoint,
    SignalAction,
    SwarmAgentBrief,
)
from core.devils_advocate import DevilsAdvocate

logger = logging.getLogger(__name__)

PREDATOR_SYSTEM_INSTRUCTION = (
    "System instruction: You are an elite trader in April 2026. "
    "If the 1m and 5m charts are Bullish, you MUST signal BUY. "
    "Do not mention 2021 data."
)


def call_local_brain(prompt: str, model: str = None, timeout: Optional[int] = None) -> dict:
    """
    Simple wrapper to call local Ollama brain.
    This is the core "brain" function that runs locally.
    """
    url = f"{config.OLLAMA_BASE_URL}/api/generate"

    # Simple headers for local connection
    headers = {"Content-Type": "application/json"}

    payload = {
        "model": model or config.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,  # Very low = consistent JSON, no rambling
            "num_predict": 256,  # Reduced from 512 for faster responses
            "top_p": 0.9,
            "top_k": 40,
        }
    }

    try:
        request_timeout = max(int(timeout or config.LLM_TIMEOUT), 90)
        logger.info(f"🧠 Calling local brain: {config.OLLAMA_MODEL} (timeout={request_timeout}s)")
        response = requests.post(url, json=payload, headers=headers, timeout=request_timeout)
        response.raise_for_status()
        data = response.json()
        raw_response = data.get('response', '{}')
        
        logger.info(f"✅ Local brain responded successfully")
        return parse_json_response(raw_response)
    except requests.exceptions.ConnectionError:
        logger.error("❌ Cannot connect to Ollama! Is Ollama running on localhost:11434?")
        logger.error("   Run: ollama serve")
        return {"error": "Ollama not running"}
    except Exception as e:
        logger.error(f"❌ Local AI Error: {e}")
        return {"error": str(e)}


def parse_json_response(raw: str) -> dict:
    """Clean and parse JSON from LLM response."""
    if not raw:
        return {"error": "Empty response"}
    
    # Clean the response - remove markdown code blocks
    raw = raw.strip()
    
    # Remove ```json ... ``` wrapper if present
    if raw.startswith("```"):
        lines = raw.split("\n")
        clean_lines = []
        in_json = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```"):
                in_json = not in_json
                continue
            if not stripped.startswith("```"):
                clean_lines.append(line)
        raw = "\n".join(clean_lines).strip()

    # Remove comments (// style)
    raw = re.sub(r'//.*$', '', raw, flags=re.MULTILINE)

    # Remove dollar signs from numbers
    raw = raw.replace('$', '')

    # Try to parse JSON
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON from text
        try:
            start = raw.index('{')
            end = raw.rindex('}') + 1
            json_str = raw[start:end]
            return json.loads(json_str)
        except (ValueError, json.JSONDecodeError):
            logger.warning(f"Invalid JSON from LLM: {raw[:300]}")
            return {"error": "Invalid JSON", "raw": raw}


class OllamaSwarmConsensus:
    """Local Qwen 2.5 trading analyst - runs 100% on your machine."""

    def __init__(self):
        self.base_url = config.OLLAMA_BASE_URL
        self.model = config.OLLAMA_MODEL
        self.timeout = max(int(config.LLM_TIMEOUT), 90)
        self.devils_advocate = DevilsAdvocate()
        logger.info(f"🧠 Local Brain initialized: {self.model} at {self.base_url}")

    def request_decision(self, proposed_action: str, package: dict[str, Any]) -> dict[str, Any]:
        """Fallback strike gate used when the cloud brain is unavailable."""
        prompt = self._build_fallback_brain_prompt(proposed_action, package)
        result = call_local_brain(prompt, model=self.model, timeout=self.timeout)
        if "error" in result:
            return {
                "verdict": "[SIGNAL] WAIT",
                "reasoning": str(result.get("error") or "Local Predator unavailable.")[:240],
                "model": self.model,
                "brain_used": "OLLAMA_PREDATOR",
                "fallback_mode": True,
                "raw_text": str(result),
            }

        verdict = self._normalize_fallback_verdict(
            result.get("verdict") or result.get("signal") or result.get("action"),
            proposed_action,
        )
        reasoning = str(result.get("reasoning") or result.get("reason") or "Local Predator strike gate engaged.").strip()[:240]
        return {
            "verdict": verdict,
            "reasoning": reasoning,
            "model": self.model,
            "brain_used": "OLLAMA_PREDATOR",
            "fallback_mode": True,
            "raw_text": json.dumps(result, ensure_ascii=False),
        }

    def _build_fallback_brain_prompt(self, proposed_action: str, package: dict[str, Any]) -> str:
        candles_json = json.dumps(package.get("recent_ohlcv", []), ensure_ascii=False)
        zones_json = json.dumps(package.get("liquidity_zones", []), ensure_ascii=False)
        return f"""{PREDATOR_SYSTEM_INSTRUCTION}

You are the local Predator fallback strike gate.

Review the proposed {str(proposed_action or 'WAIT').upper()}.

Market snapshot:
- Signal type: {package.get('signal_type', 'UNKNOWN')}
- Asset: {package.get('asset', 'UNKNOWN')}
- Last 10 OHLCV candles: {candles_json}
- Current RSI: {package.get('rsi', 50.0)}
- Current ATR: {package.get('atr', 0.0)}
- Primary liquidity label: {package.get('liquidity_zone_label', 'N/A')}
- Nearest liquidity zone coordinates: {zones_json}

Return JSON only:
{{"verdict":"[SIGNAL] BUY or [SIGNAL] SELL or [SIGNAL] WAIT","reasoning":"one short execution reason under 240 chars"}}
"""

    def _normalize_fallback_verdict(self, value: Any, proposed_action: str) -> str:
        normalized = str(value or "").strip().upper()
        if normalized in {"[SIGNAL] BUY", "[SIGNAL] SELL", "[SIGNAL] WAIT"}:
            return normalized
        if "BUY" in normalized:
            return "[SIGNAL] BUY"
        if "SELL" in normalized:
            return "[SIGNAL] SELL"
        if "WAIT" in normalized or "HOLD" in normalized:
            return "[SIGNAL] WAIT"

        action = str(proposed_action or "WAIT").strip().upper()
        if action in {"BUY", "SELL"}:
            return f"[SIGNAL] {action}"
        return "[SIGNAL] WAIT"

    async def run(
        self,
        market_data: MarketDataPoint,
        news_context: str = "",
        chart_image_base64: Optional[str] = None,
        user_suggestion: str = "",  # NEW: Co-Pilot Command Bridge
        skip_vibe_debate: bool = False,
    ) -> Tuple[LLMAnalysisOutput, DebateTranscript]:
        """Execute a 3-step Vibe -> Liquidity -> Closer analysis flow."""
        logger.info(f"🧠 Analyzing {market_data.asset} with {self.model}")
        if user_suggestion:
            logger.info(f"🚀 User suggestion received: {user_suggestion}")

        from core.market_sessions import MarketSessionDetector

        session_detector = MarketSessionDetector()
        session_context = session_detector.get_session_context()

        memory_summary = str(market_data.indicators.get("VIBE_MEMORY_SUMMARY", "") or "").strip()
        skip_reason = ""

        if skip_vibe_debate:
            skip_reason = "FORCE ACTION armed - skipped Vibe and Liquidity debate before strike."
            vibe_result = self._default_vibe_result(market_data, skipped=True)
            liquidity_result = self._default_liquidity_result(market_data, skipped=True)
        else:
            vibe_prompt = self._build_vibe_prompt(market_data, session_context, user_suggestion, memory_summary)
            vibe_result = call_local_brain(vibe_prompt, model=self.model, timeout=self.timeout)
            if "error" in vibe_result:
                logger.error("Vibe agent failed: %s", vibe_result["error"])
                vibe_result = self._default_vibe_result(market_data)

            liquidity_prompt = self._build_liquidity_prompt(
                market_data,
                session_context,
                user_suggestion,
                vibe_result,
                memory_summary,
            )
            liquidity_result = call_local_brain(liquidity_prompt, model=self.model, timeout=self.timeout)
            if "error" in liquidity_result:
                logger.error("Liquidity agent failed: %s", liquidity_result["error"])
                liquidity_result = self._default_liquidity_result(market_data)

        closer_prompt = self._build_analysis_prompt(
            market_data,
            news_context,
            session_context,
            user_suggestion,
            vibe_result=vibe_result,
            liquidity_result=liquidity_result,
            memory_summary=memory_summary,
            skip_reason=skip_reason,
        )
        result = call_local_brain(closer_prompt, model=self.model, timeout=self.timeout)
        if "error" in result:
            logger.error(f"Local brain failed: {result['error']}")
            result = self._default_result(market_data)

        output = LLMAnalysisOutput(
            action=SignalAction(result.get("action", "HOLD")),
            asset=market_data.asset,
            confidence=self._map_confidence(result.get("confidence", "MEDIUM")),
            entry_price=result.get("entry_price", market_data.price),
            stop_loss=result.get("stop_loss", market_data.price * 0.99),
            take_profit=result.get("take_profit", market_data.price * 1.01),
            reason=result.get("reason", "Qwen 2.5 analysis based on technical indicators"),
            market_regime=vibe_result.get("market_regime"),
            volatility_state=vibe_result.get("volatility_state"),
        )

        signal_label = "WAIT" if output.action == SignalAction.HOLD else output.action.value
        if "[SIGNAL]" not in output.reason.upper():
            output.reason = f"[SIGNAL] {signal_label} {output.reason}"

        vibe_action = self._normalize_action(vibe_result.get("bias") or vibe_result.get("action"))
        liquidity_action = self._normalize_action(
            liquidity_result.get("action_bias") or liquidity_result.get("action")
        )
        liquidity_verdict = str(
            liquidity_result.get("liquidity_verdict") or liquidity_result.get("zone_status") or "UNCONFIRMED"
        ).upper()
        mood = str(vibe_result.get("mood") or "NEUTRAL").upper()
        market_regime = str(
            vibe_result.get("market_regime") or self._infer_market_regime(market_data)
        ).upper()
        volatility_state = str(
            vibe_result.get("volatility_state") or self._infer_volatility_state(market_data)
        ).upper()

        vibe_context = {
            "mood": mood,
            "mood_bias": vibe_action,
            "liquidity_verdict": liquidity_verdict,
            "closer_action": signal_label,
            "market_regime": market_regime,
            "volatility_state": volatility_state,
            "liquidity_zone": market_data.indicators.get("LIQUIDITY_ZONE", "N/A"),
            "memory_summary": memory_summary,
            "force_action": skip_vibe_debate,
            "aggression_mode": skip_vibe_debate,
            "prompt_context": user_suggestion[:500],
        }

        # 😈 Devil's Advocate Challenge - Find reasons NOT to take this trade
        devils_challenge = self.devils_advocate.challenge_trade(
            market_data=market_data,
            suggested_action=output.action.value,
            entry_price=output.entry_price or market_data.price,
            stop_loss=output.stop_loss or market_data.price * 0.99,
            take_profit=output.take_profit or market_data.price * 1.01,
            confidence=output.confidence.value,
        )

        # Apply confidence penalty if Devil's Advocate found issues
        if devils_challenge.get("rating") in ["STRONG_AVOID", "CAUTIOUS"]:
            penalty = devils_challenge.get("confidence_penalty", -0.10)
            logger.warning(
                f"😈 Devil's Advocate PENALTY: {penalty:.2f} | "
                f"Reasons: {devils_challenge.get('rejection_reasons', [])}"
            )

        vibe_brief = SwarmAgentBrief(
            agent="Vibe",
            action=vibe_action,
            conviction=str(vibe_result.get("confidence") or "LOW").upper(),
            entry_price=output.entry_price,
            stop_loss=output.stop_loss,
            take_profit=output.take_profit,
            brief=str(vibe_result.get("reason") or vibe_result.get("brief") or "Mood agent neutral."),
        )
        liquidity_brief = SwarmAgentBrief(
            agent="Liquidity",
            action=liquidity_action,
            conviction=str(liquidity_result.get("confidence") or "LOW").upper(),
            entry_price=output.entry_price,
            stop_loss=output.stop_loss,
            take_profit=output.take_profit,
            brief=str(
                liquidity_result.get("reason")
                or liquidity_result.get("brief")
                or "Liquidity boxes not confirmed."
            ),
        )
        closer_brief = SwarmAgentBrief(
            agent="The Closer",
            action=output.action.value,
            conviction=output.confidence.value,
            entry_price=output.entry_price,
            stop_loss=output.stop_loss,
            take_profit=output.take_profit,
            brief=output.reason,
            verdict="APPROVE" if output.action != SignalAction.HOLD else "ABORT",
        )

        transcript = DebateTranscript(
            asset=market_data.asset,
            technical_sniper=vibe_brief,
            macro_analyst=liquidity_brief,
            risk_manager=closer_brief,
            vibe_agent=vibe_brief,
            liquidity_agent=liquidity_brief,
            closer_agent=closer_brief,
            devils_advocate=devils_challenge,
            ceo_verdict=f"[SIGNAL] {signal_label} {market_data.asset} - {output.confidence.value} confidence",
            ceo_full_statement=output.reason,
            cto_full_statement=vibe_brief.brief,
            cfo_full_statement=liquidity_brief.brief,
            vibe_context=vibe_context,
            skip_reason=skip_reason,
        )

        logger.info(f"✅ Analysis complete: {output.action.value} {market_data.asset}")
        return output, transcript

    def _build_analysis_prompt(
        self,
        market_data: MarketDataPoint,
        news: str,
        session_context: dict = None,
        user_suggestion: str = "",
        *,
        vibe_result: Optional[Dict[str, Any]] = None,
        liquidity_result: Optional[Dict[str, Any]] = None,
        memory_summary: str = "",
        skip_reason: str = "",
    ) -> str:
        """Build the closer prompt that merges Vibe + Liquidity into a strike decision."""
        snapshot = self._market_snapshot(market_data)
        session_summary = self._session_summary(session_context)
        user_summary = f"User request: {user_suggestion}" if user_suggestion else "User request: none"
        vibe_summary = self._format_agent_summary(vibe_result, fallback="Mood agent unavailable")
        liquidity_summary = self._format_agent_summary(liquidity_result, fallback="Liquidity agent unavailable")
        memory_block = memory_summary or "No prior losing Vibe pattern recorded for this asset."
        skip_block = skip_reason or "None"

        return f"""You are The Closer. Return JSON only.

{PREDATOR_SYSTEM_INSTRUCTION}

{user_summary}
{session_summary}

ASSET: {market_data.asset}
PRICE: {market_data.price:.2f}
RSI: {market_data.indicators.get('RSI', 50):.1f}
LIQUIDITY: {market_data.indicators.get('LIQUIDITY_ZONE', 'N/A')}
LAST 10 CANDLES:
{snapshot['candles_block']}

VIBE AGENT:
{vibe_summary}

LIQUIDITY AGENT:
{liquidity_summary}

VIBE MEMORY:
{memory_block}

DEBATE SKIP STATUS:
{skip_block}

Rules:
- Output action BUY, SELL, or HOLD.
- Prefix reason with [SIGNAL] BUY, [SIGNAL] SELL, or [SIGNAL] WAIT.
- Respect VIBE MEMORY. If a similar losing pattern is present, downgrade confidence or WAIT unless the setup is clearly stronger now.
- Include entry_price, stop_loss, and take_profit.
- Keep reason concise and actionable.

JSON schema only:
{{"action":"BUY or SELL or HOLD","confidence":"LOW or MEDIUM or HIGH","entry_price":0.00,"stop_loss":0.00,"take_profit":0.00,"reason":"[SIGNAL] BUY/SELL/WAIT short verdict","user_verdict":"AGREE or DISAGREE or STRATEGY_REJECTED or FORCE_WITH_WARNING","user_explanation":"short explanation","multi_tf_analysis":"short summary","pine_script_requested":true or false}}"""

    def _build_vibe_prompt(
        self,
        market_data: MarketDataPoint,
        session_context: dict,
        user_suggestion: str,
        memory_summary: str,
    ) -> str:
        snapshot = self._market_snapshot(market_data)
        session_summary = self._session_summary(session_context)
        return f"""You are Agent 1: Vibe. Return JSON only.

{PREDATOR_SYSTEM_INSTRUCTION}

{session_summary}
User request: {user_suggestion or 'none'}
Asset: {market_data.asset}
Price: {market_data.price:.2f}
RSI: {market_data.indicators.get('RSI', 50):.1f}
Signal type: {market_data.indicators.get('SIGNAL_TYPE', 'N/A')}
Signal strength: {market_data.indicators.get('SIGNAL_STRENGTH', 'N/A')}
Last 10 candles:
{snapshot['candles_block']}
Memory note: {memory_summary or 'none'}

Decide the market mood and directional bias.

JSON schema only:
{{"mood":"GREED or FEAR or NEUTRAL","bias":"BUY or SELL or HOLD","confidence":"LOW or MEDIUM or HIGH","market_regime":"TREND or BREAKOUT or CHOP or MEAN_REVERT","volatility_state":"CALM or NORMAL or HOT","reason":"short mood explanation"}}"""

    def _build_liquidity_prompt(
        self,
        market_data: MarketDataPoint,
        session_context: dict,
        user_suggestion: str,
        vibe_result: Dict[str, Any],
        memory_summary: str,
    ) -> str:
        snapshot = self._market_snapshot(market_data)
        session_summary = self._session_summary(session_context)
        return f"""You are Agent 2: Liquidity. Return JSON only.

{PREDATOR_SYSTEM_INSTRUCTION}

{session_summary}
User request: {user_suggestion or 'none'}
Asset: {market_data.asset}
Price: {market_data.price:.2f}
Nearest liquidity box: {market_data.indicators.get('LIQUIDITY_ZONE', 'N/A')}
Mood agent: {self._format_agent_summary(vibe_result, fallback='none')}
Last 10 candles:
{snapshot['candles_block']}
Memory note: {memory_summary or 'none'}

Confirm whether the liquidity boxes support a strike now.

JSON schema only:
{{"liquidity_verdict":"CONFIRMED or WEAK or TRAP or UNCONFIRMED","action_bias":"BUY or SELL or HOLD","confidence":"LOW or MEDIUM or HIGH","reason":"short liquidity explanation"}}"""

    def _market_snapshot(self, market_data: MarketDataPoint) -> Dict[str, str]:
        recent_candles = market_data.indicators.get("RECENT_CANDLES", [])[:10]
        candles_block = "\n".join(f"- {candle}" for candle in recent_candles) if recent_candles else "- N/A"
        return {"candles_block": candles_block}

    def _session_summary(self, session_context: Optional[dict]) -> str:
        if not session_context:
            return "Session: Unknown"
        return (
            f"Session: {session_context.get('primary_session', 'Unknown')} | "
            f"Note: {session_context.get('session_note', '')}"
        )

    def _format_agent_summary(self, result: Optional[Dict[str, Any]], fallback: str) -> str:
        if not result:
            return fallback
        if isinstance(result, dict):
            parts = []
            for key in ("mood", "bias", "liquidity_verdict", "action_bias", "market_regime", "volatility_state", "confidence", "reason"):
                value = result.get(key)
                if value:
                    parts.append(f"{key}={value}")
            return "; ".join(parts) if parts else fallback
        return str(result)

    def _normalize_action(self, raw: Optional[str]) -> str:
        action = str(raw or "HOLD").upper().strip()
        if action in {"BUY", "SELL"}:
            return action
        return "HOLD"

    def _infer_market_regime(self, market_data: MarketDataPoint) -> str:
        signal_type = str(market_data.indicators.get("SIGNAL_TYPE", "")).upper()
        if "BREAK" in signal_type or "SPIKE" in signal_type:
            return "BREAKOUT"
        signal_strength = float(market_data.indicators.get("SIGNAL_STRENGTH", 0.0) or 0.0)
        if signal_strength >= 0.75:
            return "TREND"
        if signal_strength <= 0.3:
            return "CHOP"
        return "MEAN_REVERT"

    def _infer_volatility_state(self, market_data: MarketDataPoint) -> str:
        signal_strength = float(market_data.indicators.get("SIGNAL_STRENGTH", 0.0) or 0.0)
        if signal_strength >= 0.8:
            return "HOT"
        if signal_strength <= 0.3:
            return "CALM"
        return "NORMAL"

    def _default_vibe_result(self, market_data: MarketDataPoint, skipped: bool = False) -> dict:
        return {
            "mood": "NEUTRAL",
            "bias": "HOLD",
            "confidence": "LOW",
            "market_regime": self._infer_market_regime(market_data),
            "volatility_state": self._infer_volatility_state(market_data),
            "reason": "Skipped Vibe agent." if skipped else "Vibe agent unavailable - neutral bias.",
        }

    def _default_liquidity_result(self, market_data: MarketDataPoint, skipped: bool = False) -> dict:
        return {
            "liquidity_verdict": "UNCONFIRMED",
            "action_bias": "HOLD",
            "confidence": "LOW",
            "reason": (
                "Skipped Liquidity agent."
                if skipped
                else f"Liquidity confirmation unavailable near {market_data.indicators.get('LIQUIDITY_ZONE', 'N/A')}."
            ),
        }

    def _map_confidence(self, raw: str) -> ConfidenceLevel:
        """Map string to ConfidenceLevel enum."""
        mapping = {
            "LOW": ConfidenceLevel.LOW,
            "MEDIUM": ConfidenceLevel.MEDIUM,
            "HIGH": ConfidenceLevel.HIGH,
            "VERY_HIGH": ConfidenceLevel.VERY_HIGH,
        }
        return mapping.get(str(raw).upper(), ConfidenceLevel.MEDIUM)

    def _default_result(self, market_data: MarketDataPoint) -> dict:
        """Fallback when local brain fails."""
        return {
            "action": "HOLD",
            "confidence": "LOW",
            "entry_price": market_data.price,
            "stop_loss": market_data.price * 0.98,
            "take_profit": market_data.price * 1.02,
            "reason": "[SIGNAL] WAIT Local Qwen 2.5 analysis failed - defaulting to HOLD for safety",
        }
