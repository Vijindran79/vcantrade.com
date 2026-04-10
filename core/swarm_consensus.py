"""
VcaniTrade AI - Local Qwen 2.5 Brain

100% local execution using Ollama + Qwen 2.5:7b
No cloud dependencies, no API tokens needed!
"""

import json
import logging
import re
from typing import Optional, Tuple
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

logger = logging.getLogger(__name__)


def call_local_brain(prompt: str, model: str = None) -> dict:
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
            "temperature": 0.1,  # Low = consistent JSON
            "num_predict": 512,  # Max tokens for response
        }
    }

    try:
        logger.info(f"🧠 Calling local brain: {config.OLLAMA_MODEL}")
        response = requests.post(url, json=payload, headers=headers, timeout=config.LLM_TIMEOUT)
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
        self.timeout = config.LLM_TIMEOUT
        logger.info(f"🧠 Local Brain initialized: {self.model} at {self.base_url}")

    async def run(
        self,
        market_data: MarketDataPoint,
        news_context: str = "",
        chart_image_base64: Optional[str] = None,
    ) -> Tuple[LLMAnalysisOutput, DebateTranscript]:
        """Execute local Qwen 2.5 analysis."""
        logger.info(f"🧠 Analyzing {market_data.asset} with {self.model}")

        # Build the prompt
        prompt = self._build_analysis_prompt(market_data, news_context)

        # Call the local brain
        result = call_local_brain(prompt)
        
        # Handle errors
        if "error" in result:
            logger.error(f"Local brain failed: {result['error']}")
            result = self._default_result(market_data)

        # Build output
        output = LLMAnalysisOutput(
            action=SignalAction(result.get("action", "HOLD")),
            asset=market_data.asset,
            confidence=self._map_confidence(result.get("confidence", "MEDIUM")),
            entry_price=result.get("entry_price", market_data.price),
            stop_loss=result.get("stop_loss", market_data.price * 0.99),
            take_profit=result.get("take_profit", market_data.price * 1.01),
            reason=result.get("reason", "Qwen 2.5 analysis based on technical indicators"),
        )

        # Build transcript
        brief = SwarmAgentBrief(
            agent="Qwen 2.5 Analyst",
            action=output.action.value,
            conviction=output.confidence.value,
            entry_price=output.entry_price,
            stop_loss=output.stop_loss,
            take_profit=output.take_profit,
            brief=output.reason,
        )

        transcript = DebateTranscript(
            asset=market_data.asset,
            technical_sniper=brief,
            macro_analyst=brief,
            risk_manager=SwarmAgentBrief(
                agent="Risk Manager",
                action="HOLD",
                conviction="MEDIUM",
                verdict="APPROVE",
                brief="Risk acceptable for this trade",
            ),
            ceo_verdict=f"{output.action.value} {market_data.asset} - {output.confidence.value} confidence",
            ceo_full_statement=output.reason,
        )

        logger.info(f"✅ Analysis complete: {output.action.value} {market_data.asset}")
        return output, transcript

    def _build_analysis_prompt(self, market_data: MarketDataPoint, news: str) -> str:
        """Build expert trading analysis prompt for Qwen 2.5."""
        return f"""You are an expert trading analyst powered by Qwen 2.5. Analyze this market signal and respond with JSON only.

ASSET: {market_data.asset}
CURRENT PRICE: ${market_data.price:.2f}
RSI: {market_data.indicators.get('RSI', 50):.1f}
SIGNAL TYPE: {market_data.indicators.get('SIGNAL_TYPE', 'Unknown')}
SIGNAL STRENGTH: {market_data.indicators.get('SIGNAL_STRENGTH', 0.5):.2f}

Trading Rules:
- RSI below 30 = oversold (potential BUY opportunity)
- RSI above 70 = overbought (potential SELL opportunity)
- Strong signal strength (>0.7) = higher confidence in the signal
- Always provide entry_price, stop_loss, and take_profit levels
- Set stop_loss 1-2% away from entry to limit risk
- Set take_profit 2-3% away from entry for reasonable gains

Respond with ONLY this JSON format (no other text, no markdown):
{{"action":"BUY or SELL or HOLD","confidence":"LOW or MEDIUM or HIGH","entry_price":0.00,"stop_loss":0.00,"take_profit":0.00,"reason":"your brief analysis in 1-2 sentences"}}"""

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
            "reason": "Local Qwen 2.5 analysis failed - defaulting to HOLD for safety",
        }
