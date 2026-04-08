"""
VcaniTrade AI - Swarm Consensus Orchestrator
Multi-Agent Board of Directors with PARALLEL debate architecture.

Three specialized agents (Technical Sniper, Macro Analyst, Risk Manager)
produce independent analyses SIMULTANEOUSLY via asyncio.gather().
A CEO Agent then synthesizes their outputs into a single high-conviction trade decision.

Migrated to Groq API for ultra-fast inference.
"""

import asyncio
import json
import logging
from typing import Optional, Tuple, List, Dict, Any
from pydantic import BaseModel, Field
from openai import AsyncOpenAI

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


class GroqSwarmConsensus:
    """Swarm Consensus using Groq API for fast parallel inference."""

    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=config.GROQ_API_KEY, base_url="https://api.groq.com/openai/v1"
        )
        self.model = config.LLM_MODEL

    async def _call_agent_async(
        self, system_prompt: str, user_prompt: str, agent_name: str
    ) -> SwarmAgentBrief:
        """Call a single agent asynchronously using Groq."""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=config.LLM_TEMPERATURE,
                max_tokens=config.LLM_MAX_TOKENS,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content.strip()

            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            data = json.loads(content)

            # Map Sniper/Macro actions to standard format
            raw_action = data.get("action", "HOLD")
            if raw_action in ["BUY", "SELL", "HOLD"]:
                action = raw_action
            elif raw_action in ["BULLISH", "BEARISH", "NEUTRAL"]:
                action = (
                    "BUY"
                    if raw_action == "BULLISH"
                    else "SELL"
                    if raw_action == "BEARISH"
                    else "HOLD"
                )
            else:
                action = "HOLD"

            # Validate conviction/confidence
            raw_conviction = data.get(
                "conviction", data.get("confidence", "MEDIUM")
            ).upper()
            if raw_conviction not in ["LOW", "MEDIUM", "HIGH", "VERY_HIGH"]:
                raw_conviction = "MEDIUM"

            brief_text = data.get("brief", "")[:1500]

            # Build response based on agent type
            if agent_name == "Risk Manager":
                return SwarmAgentBrief(
                    agent=agent_name,
                    verdict=data.get("verdict", "APPROVE"),
                    conviction=raw_conviction,
                    brief=brief_text,
                    max_risk_pct=data.get("max_risk_pct"),
                )
            else:
                return SwarmAgentBrief(
                    agent=agent_name,
                    action=action,
                    conviction=raw_conviction,
                    entry_price=data.get("entry_price"),
                    stop_loss=data.get("stop_loss"),
                    take_profit=data.get("take_profit"),
                    brief=brief_text,
                    risk_events=data.get("risk_events", []),
                )

        except Exception as e:
            logger.error(f"[{agent_name}] LLM call failed: {e}")
            if agent_name == "Risk Manager":
                return SwarmAgentBrief(
                    agent=agent_name,
                    verdict="ABORT",
                    conviction="LOW",
                    brief=f"{agent_name} analysis failed — defaulting to cautious stance.",
                )
            return SwarmAgentBrief(
                agent=agent_name,
                action="HOLD",
                conviction="LOW",
                brief=f"{agent_name} analysis failed — defaulting to cautious stance.",
            )

    async def run(
        self,
        market_data: MarketDataPoint,
        news_context: str = "",
        chart_image_base64: Optional[str] = None,
    ) -> Tuple[LLMAnalysisOutput, DebateTranscript]:
        """Execute the full swarm pipeline with PARALLEL agent execution."""
        logger.info("Swarm Consensus: Starting PARALLEL multi-agent debate (Groq)")

        # Build prompts
        sniper_prompt = self._build_sniper_prompt(market_data)
        macro_prompt = self._build_macro_prompt(market_data, news_context)
        risk_prompt = None  # Will be built after sniper/macro complete

        # Run Sniper and Macro in PARALLEL
        sniper, macro = await asyncio.gather(
            self._call_agent_async(
                sniper_prompt["system"], sniper_prompt["user"], "Technical Sniper"
            ),
            self._call_agent_async(
                macro_prompt["system"], macro_prompt["user"], "Macro Analyst"
            ),
        )

        # Risk Manager (depends on sniper + macro)
        risk_prompt = self._build_risk_prompt(sniper.brief, macro.brief)
        risk = await self._call_agent_async(
            risk_prompt["system"], risk_prompt["user"], "Risk Manager"
        )

        # CEO Synthesis
        ceo_output = await self._call_ceo(
            market_data, sniper.brief, macro.brief, risk.brief
        )

        transcript = DebateTranscript(
            asset=market_data.asset,
            technical_sniper=sniper,
            macro_analyst=macro,
            risk_manager=risk,
            ceo_verdict=ceo_output.reason,
            ceo_full_statement=ceo_output.reason,
        )

        logger.info(
            f"Swarm complete: {ceo_output.action.value} {market_data.asset} ({ceo_output.confidence.value})"
        )
        return ceo_output, transcript

    def _build_sniper_prompt(self, market_data: MarketDataPoint) -> dict:
        system_prompt = """You are the TECHNICAL SNIPER on a trading board of directors. You ONLY look at
price action, volume, momentum, and chart-pattern geometry. You do NOT care
about news, macroeconomics, or sentiment.

CRITICAL FORMATTING:
1. You must output ONLY valid JSON.
2. For the 'action' field, output EXACTLY ONE: "BUY", "SELL", or "HOLD".
3. For 'conviction', output EXACTLY ONE: "LOW", "MEDIUM", "HIGH", or "VERY_HIGH".
4. Do NOT output the literal schema strings like "BUY|SELL|HOLD".
5. Keep your brief under 120 words (max 1500 chars).
6. Output raw JSON only - no markdown code blocks."""

        user_prompt = f"""Market Data:
- Asset: {market_data.asset}
- Current Price: {market_data.price}
- 1h Change: {market_data.price_change_1h}%
- 24h Change: {market_data.price_change_24h}%
- Volume: {market_data.volume}
- Indicators: {json.dumps(market_data.indicators, default=str)}

Respond with JSON:
{{
  "agent": "Technical Sniper",
  "action": "BUY|SELL|HOLD",
  "conviction": "LOW|MEDIUM|HIGH|VERY_HIGH",
  "entry_price": <float>,
  "stop_loss": <float>,
  "take_profit": <float>,
  "brief": "<analysis under 120 words>"
}}"""

        return {"system": system_prompt, "user": user_prompt}

    def _build_macro_prompt(
        self, market_data: MarketDataPoint, news_context: str
    ) -> dict:
        system_prompt = """You are the MACRO / NEWS ANALYST on a trading board of directors. You ONLY
look at macroeconomic sentiment, news flow, and geopolitical winds. You do
NOT look at chart patterns or technical indicators.

CRITICAL FORMATTING:
1. You must output ONLY valid JSON.
2. For 'action', output EXACTLY ONE: "BULLISH", "BEARISH", or "NEUTRAL".
3. For 'conviction', output EXACTLY ONE: "LOW", "MEDIUM", "HIGH", or "VERY_HIGH".
4. Keep your brief under 120 words (max 1500 chars).
5. Output raw JSON only."""

        user_prompt = f"""Market Data:
- Asset: {market_data.asset}
- Current Price: {market_data.price}
- 1h Change: {market_data.price_change_1h}%
- 24h Change: {market_data.price_change_24h}%

News Context:
{news_context or "No significant news"}

Respond with JSON:
{{
  "agent": "Macro Analyst",
  "action": "BULLISH|BEARISH|NEUTRAL",
  "conviction": "LOW|MEDIUM|HIGH|VERY_HIGH",
  "brief": "<analysis under 120 words>",
  "risk_events": ["event1", "event2"]
}}"""

        return {"system": system_prompt, "user": user_prompt}

    def _build_risk_prompt(self, sniper_brief: str, macro_brief: str) -> dict:
        system_prompt = """You are the RISK MANAGER (Devil's Advocate) on a trading board of directors.
Your ONLY job is to find reasons NOT to trade. You are paranoid, conservative,
and deeply skeptical.

CRITICAL FORMATTING:
1. You must output ONLY valid JSON.
2. For 'verdict', output EXACTLY ONE: "APPROVE" or "ABORT".
3. For 'conviction', output EXACTLY ONE: "LOW", "MEDIUM", "HIGH", or "VERY_HIGH".
4. Keep your brief under 120 words (max 1500 chars).
5. Output raw JSON only."""

        user_prompt = f"""Technical Sniper: {sniper_brief}
Macro Analyst: {macro_brief}

Your job: Identify contradictions, flag hidden risks, recommend ABORT if setup is dangerous.

Respond with JSON:
{{
  "agent": "Risk Manager",
  "verdict": "APPROVE|ABORT",
  "conviction": "LOW|MEDIUM|HIGH|VERY_HIGH",
  "brief": "<risk analysis under 120 words>",
  "max_risk_pct": <float>
}}"""

        return {"system": system_prompt, "user": user_prompt}

    async def _call_ceo(
        self,
        market_data: MarketDataPoint,
        sniper_brief: str,
        macro_brief: str,
        risk_brief: str,
    ) -> LLMAnalysisOutput:
        """CEO agent synthesizes all agent responses."""
        system_prompt = """You are the CHIEF EXECUTION OFFICER — a brave, fearless, and deeply
knowledgeable trading commander. You have just heard the debate from your
three specialists.

CRITICAL FORMATTING:
1. You must output ONLY valid JSON.
2. For 'action', output EXACTLY ONE: "BUY", "SELL", "HOLD", or "CLOSE".
3. For 'confidence', output EXACTLY ONE: "LOW", "MEDIUM", "HIGH", or "VERY_HIGH".
4. Do NOT output the literal schema strings.
5. Your final reason must be a single punchy sentence (max 150 chars).
6. Output raw JSON only."""

        user_prompt = f"""Technical Sniper: {sniper_brief}
Macro Analyst: {macro_brief}
Risk Manager: {risk_brief}

YOUR RULES:
1. Make a decisive call: BUY, SELL, HOLD, or CLOSE.
2. If Risk Manager says ABORT, respect it unless conviction is VERY_HIGH.
3. If all three align, strike with maximum conviction.
4. Final reason: single punchy sentence (max 150 chars).

Market Data:
- Asset: {market_data.asset}
- Current Price: {market_data.price}

Respond with JSON:
{{
  "action": "BUY|SELL|HOLD|CLOSE",
  "asset": "{market_data.asset}",
  "confidence": "LOW|MEDIUM|HIGH|VERY_HIGH",
  "entry_price": <float or null>,
  "stop_loss": <float or null>,
  "take_profit": <float or null>,
  "reason": "<max 150 chars>"
}}"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=config.LLM_TEMPERATURE,
                max_tokens=config.LLM_MAX_TOKENS,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content.strip()

            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            data = json.loads(content)

            raw_action = data.get("action", "HOLD").upper()
            if raw_action not in ["BUY", "SELL", "HOLD", "CLOSE"]:
                raw_action = "HOLD"

            raw_confidence = data.get("confidence", "MEDIUM").upper()
            if raw_confidence not in ["LOW", "MEDIUM", "HIGH", "VERY_HIGH"]:
                raw_confidence = "MEDIUM"

            return LLMAnalysisOutput(
                action=SignalAction(raw_action),
                asset=data.get("asset", market_data.asset),
                confidence=ConfidenceLevel(raw_confidence),
                entry_price=data.get("entry_price"),
                stop_loss=data.get("stop_loss"),
                take_profit=data.get("take_profit"),
                reason=data.get("reason", "CEO synthesis complete")[:150],
            )

        except Exception as e:
            logger.error(f"[CEO Agent] LLM call failed: {e}")
            return LLMAnalysisOutput(
                action=SignalAction.HOLD,
                asset=market_data.asset,
                confidence=ConfidenceLevel.LOW,
                reason="CEO synthesis failed — defaulting to HOLD.",
            )


# Legacy compatibility - keep original class name
SwarmConsensus = GroqSwarmConsensus


class AgentResponse(BaseModel):
    """Response from a single agent in the swarm."""

    agent_name: str
    brief: str = Field(..., max_length=1500)
    confidence: str
    reasoning: str


class GeneralSwarmConsensus:
    """General-purpose swarm for any query (not just trading)."""

    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=config.GROQ_API_KEY, base_url="https://api.groq.com/openai/v1"
        )
        self.model = config.LLM_MODEL

    async def _call_agent_async(
        self, agent_name: str, system_prompt: str, user_prompt: str
    ) -> AgentResponse:
        """Call a single agent asynchronously using Groq."""
        full_system = f"""{system_prompt}

CRITICAL FORMATTING - You MUST follow this JSON schema exactly:
{{
    "agent_name": "string",
    "brief": "string (your analysis, max 1500 chars)",
    "confidence": "LOW" | "MEDIUM" | "HIGH" | "VERY_HIGH",
    "reasoning": "string (your reasoning)"
}}

IMPORTANT: 
- Output ONLY valid JSON, no markdown code blocks
- Do NOT leave any field empty
- All fields are required
- brief must contain actual content (not blank)"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": full_system},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=config.LLM_TEMPERATURE,
                max_tokens=config.LLM_MAX_TOKENS,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            content = content.strip()

            data = json.loads(content)
            confidence = data.get("confidence", "MEDIUM").upper()
            if confidence not in ["LOW", "MEDIUM", "HIGH", "VERY_HIGH"]:
                confidence = "MEDIUM"

            brief = data.get("brief", "")
            reasoning = data.get("reasoning", "")

            # If fields are empty, provide defaults
            if not brief:
                brief = f"{agent_name} completed analysis of the query."
            if not reasoning:
                reasoning = f"Analysis based on provided context and system prompt."

            return AgentResponse(
                agent_name=data.get("agent_name", agent_name),
                brief=brief[:1500],
                confidence=confidence,
                reasoning=reasoning,
            )

            content = response.choices[0].message.content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            content = content.strip()

            data = json.loads(content)
            confidence = data.get("confidence", "MEDIUM").upper()
            if confidence not in ["LOW", "MEDIUM", "HIGH", "VERY_HIGH"]:
                confidence = "MEDIUM"

            return AgentResponse(
                agent_name=data.get("agent_name", agent_name),
                brief=data.get("brief", "")[:1500],
                confidence=confidence,
                reasoning=data.get("reasoning", ""),
            )
        except Exception as e:
            logger.error(f"[{agent_name}] LLM call failed: {e}")
            return AgentResponse(
                agent_name=agent_name,
                brief="Error during analysis",
                confidence="MEDIUM",
                reasoning=f"Failed: {str(e)}",
            )

    async def run_swarm_parallel(
        self, agents_config: List[Dict[str, Any]], user_query: str
    ) -> List[AgentResponse]:
        """Run all agents in parallel using asyncio.gather."""
        tasks = [
            self._call_agent_async(agent["name"], agent["system_prompt"], user_query)
            for agent in agents_config
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, AgentResponse)]

    async def ceo_synthesis(
        self, agent_responses: List[AgentResponse], original_query: str
    ) -> Dict[str, Any]:
        """CEO agent synthesizes all agent responses."""
        agents_summary = "\n\n".join(
            [
                f"**{resp.agent_name}** (Confidence: {resp.confidence}):\n{resp.brief}\nReasoning: {resp.reasoning}"
                for resp in agent_responses
            ]
        )

        ceo_system = """You are the CEO Agent, the final decision maker.
Synthesize inputs from all specialist agents and provide a final, actionable conclusion.

CRITICAL FORMATTING - You MUST follow this JSON schema exactly:
{
    "final_decision": "string (your final decision, required)",
    "final_confidence": "LOW" | "MEDIUM" | "HIGH" | "VERY_HIGH",
    "synthesis_reasoning": "string (how you weighed the inputs, required)",
    "recommended_actions": ["list of action items", "can be empty array"]
}

IMPORTANT:
- Output ONLY valid JSON, no markdown code blocks
- Do NOT leave any field empty - provide actual content
- final_decidence must be a clear actionable statement"""

        ceo_user = f"""Original Query: {original_query}

--- Agent Inputs ---
{agents_summary}

--- Task ---
Synthesize these inputs and provide a final decision. Be decisive and provide actual content in all fields."""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": ceo_system},
                    {"role": "user", "content": ceo_user},
                ],
                temperature=config.LLM_TEMPERATURE,
                max_tokens=config.LLM_MAX_TOKENS,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            content = content.strip()

            data = json.loads(content)
            confidence = data.get("final_confidence", "MEDIUM").upper()
            if confidence not in ["LOW", "MEDIUM", "HIGH", "VERY_HIGH"]:
                confidence = "MEDIUM"

            return {
                "status": "success",
                "final_decision": data.get("final_decision", "No decision reached"),
                "final_confidence": confidence,
                "synthesis_reasoning": data.get("synthesis_reasoning", ""),
                "recommended_actions": data.get("recommended_actions", []),
                "agent_count": len(agent_responses),
            }
        except Exception as e:
            logger.error(f"[CEO] Synthesis failed: {e}")
            return {
                "status": "error",
                "final_decision": "System error",
                "final_confidence": "LOW",
                "synthesis_reasoning": str(e),
                "recommended_actions": [],
                "agent_count": len(agent_responses),
            }

    async def execute_swarm(
        self, agents_config: List[Dict[str, Any]], query: str
    ) -> Dict[str, Any]:
        """Main entry point: Run swarm and CEO synthesis."""
        agent_responses = await self.run_swarm_parallel(agents_config, query)
        result = await self.ceo_synthesis(agent_responses, query)
        result["agent_responses"] = [
            {
                "name": r.agent_name,
                "brief": r.brief,
                "confidence": r.confidence,
                "reasoning": r.reasoning,
            }
            for r in agent_responses
        ]
        return result
