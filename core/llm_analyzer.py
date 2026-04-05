"""
VcaniTrade AI - LLM Analyzer
Connects to Ollama local LLM for trade signal analysis
Forces strict JSON output validated with Pydantic
"""

import requests
import json
import logging
from typing import Optional
from datetime import datetime

import config
from core.models import LLMAnalysisOutput, MarketDataPoint, SignalAction, ConfidenceLevel

logger = logging.getLogger(__name__)


class LLMAnalyzer:
    """Analyzes market data using local Ollama LLM"""
    
    def __init__(self):
        self.base_url = config.OLLAMA_BASE_URL
        self.model = config.OLLAMA_MODEL
        self.timeout = config.LLM_TIMEOUT
        
    def analyze_market(self, market_data: MarketDataPoint, news_context: str = "") -> LLMAnalysisOutput:
        """
        Send market data to LLM and get trading signal
        Returns strictly validated JSON output
        """
        if not self._is_ollama_available():
            logger.warning("Ollama not available, using mock analysis")
            return self._mock_analysis(market_data)
        
        prompt = self._build_prompt(market_data, news_context)
        
        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json"  # Force JSON output
                },
                timeout=self.timeout
            )
            response.raise_for_status()
            
            result = response.json()
            llm_output = json.loads(result.get("response", "{}"))
            
            # Validate output with Pydantic
            validated_output = LLMAnalysisOutput(**llm_output)
            validated_output.timestamp = datetime.utcnow().isoformat()
            
            logger.info(f"LLM Analysis: {validated_output.action} {market_data.asset} (Confidence: {validated_output.confidence})")
            return validated_output
            
        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")
            return self._mock_analysis(market_data)
    
    def _build_prompt(self, market_data: MarketDataPoint, news_context: str) -> str:
        """Build structured prompt for LLM"""
        return f"""You are a professional trading analyst. Analyze the following market data and provide a trading signal in STRICT JSON FORMAT.

Market Data:
- Asset: {market_data.asset}
- Current Price: {market_data.price}
- 1h Change: {market_data.price_change_1h}%
- 24h Change: {market_data.price_change_24h}%
- Volume: {market_data.volume}
- Indicators: {json.dumps(market_data.indicators, default=str)}

News Context:
{news_context if news_context else "No significant news"}

You MUST respond in this exact JSON format with no additional text:
{{
    "action": "BUY or SELL or HOLD or CLOSE",
    "asset": "{market_data.asset}",
    "confidence": "LOW or MEDIUM or HIGH or VERY_HIGH",
    "entry_price": {market_data.price},
    "stop_loss": [calculate appropriate stop loss],
    "take_profit": [calculate appropriate take profit],
    "reason": "Brief explanation of your analysis",
    "timestamp": null
}}

Rules:
- Stop loss should be 1-2% from entry for high confidence, 2-3% for medium
- Take profit should be 2-3x the stop loss distance (risk/reward ratio)
- Consider trend direction from price changes
- Higher volume = higher confidence
- Be conservative - only signal BUY/SELL when confident
"""
    
    def _is_ollama_available(self) -> bool:
        """Check if Ollama is running locally"""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=2)
            return response.status_code == 200
        except:
            return False
    
    def _mock_analysis(self, market_data: MarketDataPoint) -> LLMAnalysisOutput:
        """Mock analysis for testing without Ollama"""
        import random
        
        # Simulate realistic trading signals
        price = market_data.price
        trend = market_data.price_change_1h
        
        if trend > 0.5:
            action = SignalAction.BUY
            confidence = ConfidenceLevel.HIGH if trend > 1.0 else ConfidenceLevel.MEDIUM
            sl = price * 0.995  # 0.5% stop loss
            tp = price * 1.015  # 1.5% take profit
            reason = f"Uptrend detected (+{trend:.2f}% in 1h). Volume: {market_data.volume:.0f}. Support holding."
        elif trend < -0.5:
            action = SignalAction.SELL
            confidence = ConfidenceLevel.HIGH if trend < -1.0 else ConfidenceLevel.MEDIUM
            sl = price * 1.005  # 0.5% stop loss
            tp = price * 0.985  # 1.5% take profit
            reason = f"Downtrend detected ({trend:.2f}% in 1h). Volume: {market_data.volume:.0f}. Resistance holding."
        else:
            action = SignalAction.HOLD
            confidence = ConfidenceLevel.LOW
            sl = None
            tp = None
            reason = f"Consolidation phase. Waiting for clearer direction. Volume: {market_data.volume:.0f}"
        
        return LLMAnalysisOutput(
            action=action,
            asset=market_data.asset,
            confidence=confidence,
            entry_price=price,
            stop_loss=sl,
            take_profit=tp,
            reason=reason,
            timestamp=datetime.utcnow().isoformat()
        )
