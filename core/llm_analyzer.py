"""
VcaniTrade AI - LLM Analyzer
Orchestrates the Swarm Consensus multi-agent debate for trade analysis.
Falls back to single-agent mock analysis when Ollama is unavailable.
"""

import logging
from datetime import datetime
from typing import Optional, Tuple

import config
from core.models import (
    DebateTranscript,
    LLMAnalysisOutput,
    MarketDataPoint,
)
from core.swarm_consensus import SwarmConsensus

logger = logging.getLogger(__name__)


class LLMAnalyzer:
    """Analyzes market data using the Swarm Consensus multi-agent architecture."""

    def __init__(self):
        self.base_url = config.OLLAMA_BASE_URL
        self.model = config.OLLAMA_MODEL
        self.timeout = config.LLM_TIMEOUT
        self.swarm = SwarmConsensus(self.base_url, self.model, self.timeout)

    def analyze_market(
        self, market_data: MarketDataPoint, news_context: str = ""
    ) -> Tuple[LLMAnalysisOutput, Optional[DebateTranscript]]:
        """
        Run the full swarm debate and return the CEO's decision plus transcript.
        """
        try:
            output, transcript = self.swarm.run(market_data, news_context)
            logger.info(
                f"Swarm decision: {output.action.value} {market_data.asset} "
                f"({output.confidence.value})"
            )
            return output, transcript
        except Exception as e:
            logger.error(f"Swarm consensus failed: {e}")
            # Ultimate fallback
            output = LLMAnalysisOutput(
                action="HOLD",
                asset=market_data.asset,
                confidence="LOW",
                reason="All analysis pipelines failed. Standing aside.",
            )
            return output, None
