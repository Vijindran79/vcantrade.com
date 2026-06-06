"""
VcaniTrade AI - LLM Analyzer
Orchestrates the Swarm Consensus multi-agent debate for trade analysis.

Dual-Vision Support:
    Accepts optional chart screenshot (base64) that gets passed to the
    Technical Sniper for visual analysis via a local VLM.
"""

import logging
import asyncio
from typing import Optional, Tuple

import config
from core.models import (
    DebateTranscript,
    LLMAnalysisOutput,
    MarketDataPoint,
    SignalAction,
    ConfidenceLevel,
)
from core.brain_swarm import OllamaSwarmConsensus as SwarmConsensus

logger = logging.getLogger(__name__)

# Cloud fallback brain for when local Ollama is unavailable
_cloud_brain = None


def _get_cloud_brain():
    """Lazy initializer for cloud fallback brain to avoid circular imports."""
    global _cloud_brain
    if _cloud_brain is None:
        try:
            from core.brain import GeminiBrain
            _cloud_brain = GeminiBrain()
        except Exception as exc:
            logger.warning("Cloud fallback brain unavailable: %s", exc)
    return _cloud_brain


def _get_or_create_event_loop():
    """Get existing event loop or create a new one safely."""
    try:
        loop = asyncio.get_running_loop()
        return loop, True  # Loop was already running
    except RuntimeError:
        return asyncio.new_event_loop(), False  # New loop created


class LLMAnalyzer:
    """Analyzes market data using the Swarm Consensus multi-agent architecture."""

    def __init__(self):
        self.swarm = SwarmConsensus()

    def analyze_market(
        self,
        market_data: MarketDataPoint,
        news_context: str = "",
        chart_image_base64: Optional[str] = None,
    ) -> Tuple[LLMAnalysisOutput, Optional[DebateTranscript]]:
        """
        Run the full swarm debate and return the CEO's decision plus transcript.

        Args:
            market_data: Numeric market data (price, volume, indicators)
            news_context: Optional news/sentiment context for Macro Analyst
            chart_image_base64: Optional base64-encoded chart screenshot for
                                VLM-enhanced Technical Sniper analysis
        """
        try:
            # FIX: request_decision() is SYNC (not async), call it directly!
            # Build package for swarm decision
            package = {
                "recent_ohlcv": [],
                "liquidity_zones": [],
                "signal_type": "TECHNICAL",
                "rsi": market_data.indicators.get("RSI", 50.0),
                "atr": market_data.indicators.get("ATR", 0.0),
                "asset": market_data.asset,
            }
            
            # Call request_decision directly (it's a sync method!)
            decision = self.swarm.request_decision("BUY", package)
            
            # Convert decision to LLMAnalysisOutput
            from core.models import LLMAnalysisOutput, SignalAction, ConfidenceLevel
            action_str = decision.get("verdict", "HOLD")
            action = SignalAction.BUY if "BUY" in action_str.upper() else SignalAction.SELL if "SELL" in action_str.upper() else SignalAction.HOLD
            
            # Extract confidence from decision if available
            confidence_str = decision.get("confidence", "MEDIUM")
            confidence_map = {
                "LOW": ConfidenceLevel.LOW,
                "MEDIUM": ConfidenceLevel.MEDIUM,
                "HIGH": ConfidenceLevel.HIGH,
            }
            confidence = confidence_map.get(confidence_str.upper(), ConfidenceLevel.MEDIUM)
            
            output = LLMAnalysisOutput(
                action=action,
                asset=market_data.asset,
                confidence=confidence,
                reason=decision.get("reasoning", "Swarm decision")
            )
            transcript = None

            logger.info(
                f"Swarm decision: {output.action.value} {market_data.asset} "
                f"({output.confidence.value})"
            )
            return output, transcript

        except Exception as e:
            logger.error(f"Swarm consensus failed: {e}")
            # -- Cloud Fallback: if local Ollama fails, try OpenRouter once --
            cloud_brain = _get_cloud_brain()
            if cloud_brain and cloud_brain.is_available():
                try:
                    logger.warning("[SYSTEM] Local Predator down. Attempting cloud fallback for analysis.")
                    package = {
                        "recent_ohlcv": [],
                        "liquidity_zones": [],
                        "liquidity_zone_label": "N/A",
                        "signal_type": "SWARM_FALLBACK",
                        "rsi": market_data.indicators.get("RSI", 50.0),
                        "atr": market_data.indicators.get("ATR", 0.0),
                        "asset": market_data.asset,
                    }
                    decision = cloud_brain.request_decision("BUY", package)
                    verdict = decision.get("verdict", "[SIGNAL] WAIT")
                    action = SignalAction.BUY if "BUY" in verdict else SignalAction.SELL if "SELL" in verdict else SignalAction.HOLD
                    output = LLMAnalysisOutput(
                        action=action,
                        asset=market_data.asset,
                        confidence=ConfidenceLevel.MEDIUM,
                        reason=f"[CLOUD FALLBACK] {decision.get('reasoning', 'Cloud brain override')} (model={decision.get('model', 'unknown')})",
                    )
                    transcript = DebateTranscript(
                        asset=market_data.asset,
                        ceo_verdict=f"[CLOUD FALLBACK] {verdict} {market_data.asset}",
                        ceo_full_statement=output.reason,
                    )
                    logger.info("Cloud fallback analysis succeeded: %s %s", action.value, market_data.asset)
                    return output, transcript
                except Exception as cloud_exc:
                    logger.error("Cloud fallback also failed: %s", cloud_exc)

            output = LLMAnalysisOutput(
                action=SignalAction.HOLD,
                asset=market_data.asset,
                confidence=ConfidenceLevel.LOW,
                reason="All analysis pipelines failed. Standing aside.",
            )
            return output, None
