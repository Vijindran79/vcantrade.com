"""
VcanTrade AI - Market Intelligence Agent (MIA)
The "Super Intelligent" Coaching Layer.

MIA doesn't just look at candles; it understands:
1. Macro Context (DXY, Yields)
2. News & Sentiment (Red Folders, Headlines)
3. Session Dynamics (Open/Close, Peak Volatility)
4. Historical Lessons (Meta Analyzer feedback)

It acts as the "Brain of the Brain," guiding the Swarm Consensus.
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from core.sentiment_pulse import SentimentPulse
from core.market_sessions import MarketSessionDetector
from core.meta_analyzer import MetaAnalyzer

logger = logging.getLogger(__name__)

class MarketIntelligenceAgent:
    def __init__(self):
        self.sentiment = SentimentPulse()
        self.sessions = MarketSessionDetector()
        self.meta = MetaAnalyzer()
        
        logger.info("[MIA] Market Intelligence Agent activated. Intelligence level: SUPER.")

    async def get_market_wisdom(self, asset: str) -> Dict[str, Any]:
        """
        Gathers holistic market wisdom for a specific asset.
        """
        # 1. Update all components
        try:
            await self.sentiment.check_news()
            await self.sentiment.update_macro_indicators()
        except Exception as e:
            logger.warning(f"[MIA] Failed to update sentiment/macro: {e}")
        
        # 2. Get data
        session_ctx = self.sessions.get_session_context()
        sentiment_ctx = self.sentiment.get_dashboard_summary()
        meta_ctx = self.meta.get_learning_summary()
        
        # 3. Formulate Wisdom
        wisdom = {
            "asset": asset,
            "timestamp": datetime.now().isoformat(),
            "session_context": session_ctx,
            "sentiment_context": sentiment_ctx,
            "historical_context": meta_ctx,
            "coaching_advice": self._generate_coaching_advice(asset, session_ctx, sentiment_ctx, meta_ctx),
            "intelligence_score": self._calculate_intelligence_score(session_ctx, sentiment_ctx)
        }
        
        return wisdom

    def _generate_coaching_advice(self, asset: str, session: Dict, sentiment: Dict, meta: Dict) -> str:
        """
        Generates the 'Expert Coach' advice that makes the bot look super intelligent.
        """
        advice = []
        
        # Session Advice
        if session.get("is_peak_volatility"):
            advice.append(f"We are in peak {session.get('primary_session')} volatility. Moves will be fast, ensure stops are tight.")
        elif session.get("primary_session") == "Closed":
            advice.append("Markets are technically closed. Liquidity might be thin. Watch out for slippage.")
            
        # Sentiment Advice
        if sentiment.get("rpa_status") == "PAUSED":
            advice.append(f"CRITICAL: {sentiment.get('next_event')} is imminent. Standing aside to avoid news-driven chop.")
        elif "BEARISH" in sentiment.get("sentiment_label", ""):
            advice.append(f"Overall sentiment is {sentiment.get('sentiment_label')}. Be cautious with LONGs.")
            
        # Macro Advice
        if sentiment.get("dxy_direction") == "UP":
            advice.append("DXY is trending UP. This usually puts pressure on Crypto and Gold. Tighten Longs.")
            
        # Meta Advice
        if meta.get("worst_asset") == asset:
            advice.append(f"Historical data shows {asset} has been our worst performer lately. I'm being extra picky here.")
            
        if not advice:
            advice.append("Market conditions are standard. Follow the strategy strictly.")
            
        return " | ".join(advice)

    def _calculate_intelligence_score(self, session: Dict, sentiment: Dict) -> float:
        """
        A score (0-100) representing how 'clear' or 'favorable' the market environment is.
        """
        score = 70.0  # Base score
        
        if session.get("is_peak_volatility"):
            score += 10
        if sentiment.get("safe_to_trade"):
            score += 10
        else:
            score -= 30
            
        if sentiment.get("dxy_direction") == "NEUTRAL":
            score += 5
            
        return max(0, min(100, score))

    def get_market_vibe_summary(self) -> str:
        """
        The summary that makes the user feel the bot 'knows' the market.
        Used for logs and UI status.
        """
        sent = self.sentiment.get_global_sentiment_label()
        sess = self.sessions.get_session_status_log()
        ctx = self.sentiment.get_market_context()
        
        return f"MIA STATUS: {sent} | {sess} | Context: {ctx}"
