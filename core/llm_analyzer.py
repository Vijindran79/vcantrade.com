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
)
from core.swarm_consensus import OllamaSwarmConsensus as SwarmConsensus

logger = logging.getLogger(__name__)


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
            output, transcript = asyncio.run(
                self.swarm.run(market_data, news_context, chart_image_base64)
            )
            logger.info(
                f"Swarm decision: {output.action.value} {market_data.asset} "
                f"({output.confidence.value})"
            )
            return output, transcript
        except Exception as e:
            logger.error(f"Swarm consensus failed: {e}")
            output = LLMAnalysisOutput(
                action=SignalAction.HOLD,  # Bug fix: use enum
                asset=market_data.asset,
                confidence=ConfidenceLevel.LOW,  # Bug fix: use enum
                reason="All analysis pipelines failed. Standing aside.",
            )
            return output, None
