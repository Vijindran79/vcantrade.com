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
from core.ollama_utils import (
    build_image_data_uri,
    build_ollama_url,
    normalize_base64_image,
    normalize_ollama_base_url,
)
from core.symbol_mapper import normalize_to_analysis_symbol, translate_chart_symbol
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

# Cache for available Ollama models to avoid repeated /api/tags calls
_OLLAMA_MODEL_CACHE: Dict[str, bool] = {}


def _validate_vision_model(model_name: str) -> Tuple[bool, str]:
    """
    Check whether the requested vision model exists in the local Ollama registry.
    Returns (is_valid, error_message).
    """
    if not model_name:
        return False, "VISION_MODEL is empty"

    # Use cached result if we already checked this model
    cached = _OLLAMA_MODEL_CACHE.get(model_name)
    if cached is not None:
        if cached:
            return True, ""
        return False, f"Model '{model_name}' not found in Ollama. Run: ollama pull {model_name}"

    tags_url = build_ollama_url(config.OLLAMA_BASE_URL, "api/tags")
    try:
        response = requests.get(tags_url, timeout=10)
        response.raise_for_status()
        data = response.json()
        models = {m.get("name", m.get("model", "")) for m in data.get("models", [])}
        # Ollama sometimes reports models as 'llava:7b' and sometimes just 'llava'
        model_stripped = model_name.split(":")[0]
        available = any(
            model_name == avail or model_stripped == avail or avail.startswith(model_name + "-")
            for avail in models
        )
        _OLLAMA_MODEL_CACHE[model_name] = available
        if available:
            return True, ""
        return False, (
            f"Model '{model_name}' not found in Ollama. "
            f"Available: {', '.join(sorted(models)[:8])}. "
            f"Run: ollama pull {model_name}"
        )
    except Exception as exc:
        logger.warning("[VISION] Could not query Ollama model list (%s): %s", tags_url, exc)
        # If we can't check, allow the request to proceed (fail later with clearer error)
        return True, ""


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
    url = build_ollama_url(config.OLLAMA_BASE_URL, "api/generate")

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
        logger.info(f"[BRAIN] Calling local brain: {config.OLLAMA_MODEL} (timeout={request_timeout}s)")
        response = requests.post(url, json=payload, headers=headers, timeout=request_timeout)
        response.raise_for_status()
        data = response.json()
        raw_response = data.get('response', '{}')
        
        logger.info(f"[OK] Local brain responded successfully")
        return parse_json_response(raw_response)
    except requests.exceptions.ConnectionError:
        logger.error("[FAIL] Cannot connect to Ollama! Is Ollama running on localhost:11434?")
        logger.error("   Run: ollama serve")
        return {"error": "Ollama not running"}
    except Exception as e:
        logger.error(f"[FAIL] Local AI Error: {e}")
        return {"error": str(e)}


def analyze_chart_with_vision(
    screenshot_base64: str,
    symbol: str,
    model: str = None,
    timeout: Optional[int] = None,
) -> dict:
    """
    Send a chart screenshot to the Ollama v1 endpoint for visual trade analysis.

    Uses OpenAI-compatible /v1/chat/completions format so vision models
    (llava, qwen2.5-vl, moondream) can analyze the screenshot.

    Returns:
        {"signal": "BUY|SELL|NONE", "reason": "...", "raw": "..."}
    """
    try:
        clean_b64 = normalize_base64_image(screenshot_base64)
    except ValueError as exc:
        logger.error("[VISION] Refusing malformed screenshot payload for %s: %s", symbol, exc)
        return {"signal": "NONE", "reason": f"Malformed screenshot payload: {exc}", "raw": ""}

    v1_url = build_ollama_url(config.OLLAMA_V1_URL, "v1/chat/completions")
    native_url = build_ollama_url(config.OLLAMA_BASE_URL, "api/chat")
    url = v1_url
    headers = {"Content-Type": "application/json"}
    if getattr(config, "OLLAMA_API_KEY", ""):
        headers["Authorization"] = f"Bearer {config.OLLAMA_API_KEY}"

    chosen_model = model or config.MULTI_ASSET_VISION_MODEL
    is_valid, model_err = _validate_vision_model(chosen_model)
    if not is_valid:
        logger.error("[VISION] %s", model_err)
        return {"signal": "NONE", "reason": model_err, "raw": ""}

    prompt = (
        f"You are a professional futures trader analyzing a 5-minute {symbol} chart. "
        "Give a 1-sentence verdict ONLY. Be strict.\n\n"
        "Check: trend direction, support/resistance rejection, candle pattern, 1:2 R:R. "
        "If any is weak, answer NONE.\n\n"
        "Output EXACTLY (no extra text):\n"
        "SIGNAL: [BUY/SELL/NONE] | CONFIDENCE: [0-100] | THREAT: [LOW/MEDIUM/HIGH] | REASON: [1 sentence only]"
    )

    payload = {
        "model": chosen_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": build_image_data_uri(clean_b64)},
                    },
                ],
            }
        ],
        "stream": False,
        "temperature": 0.1,
        "max_tokens": 128,
        "top_p": 0.9,
    }

    # Native fallback payload (uses stripped base64, no data URI prefix)
    native_payload = {
        "model": chosen_model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [clean_b64],
            }
        ],
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 128,
            "top_p": 0.9,
        },
    }

    def _parse_response(data: dict) -> dict:
        """Extract content from either OpenAI or native Ollama response."""
        # OpenAI-compatible format
        choices = data.get("choices", [])
        if choices:
            return {"content": choices[0].get("message", {}).get("content", "")}
        # Native Ollama format
        msg = data.get("message", {})
        if msg:
            return {"content": msg.get("content", "")}
        return {"content": ""}

    def _call_ollama_v1() -> dict:
        """Try OpenAI-compatible /v1/chat/completions endpoint."""
        response = requests.post(url, json=payload, headers=headers, timeout=request_timeout)
        response.raise_for_status()
        return response.json()

    def _call_ollama_native() -> dict:
        """Fallback to native /api/chat endpoint."""
        logger.info("[VISION] Falling back to native Ollama endpoint: %s", native_url)
        response = requests.post(native_url, json=native_payload, headers=headers, timeout=request_timeout)
        response.raise_for_status()
        return response.json()

    try:
        request_timeout = max(int(timeout or config.LLM_TIMEOUT), 90)
        logger.info(
            "[VISION] Sending %s chart to %s (timeout=%ss)",
            symbol,
            v1_url,
            request_timeout,
        )

        # Try OpenAI-compatible endpoint first
        try:
            data = _call_ollama_v1()
        except requests.exceptions.HTTPError as http_err:
            if http_err.response is not None and http_err.response.status_code == 404:
                logger.warning("[VISION] /v1/chat/completions returned 404, trying native /api/chat")
                data = _call_ollama_native()
            else:
                raise

        parsed = _parse_response(data)
        content = parsed["content"]
        if not content:
            logger.warning("[VISION] Empty content in response for %s", symbol)
            return {"signal": "NONE", "reason": "Empty model response", "raw": str(data)}

        logger.info("[VISION] %s analysis received (%s chars)", symbol, len(content))

        # Parse SIGNAL, CONFIDENCE, THREAT, and REASON
        signal_match = re.search(r"SIGNAL:\s*(BUY|SELL|NONE)", content, re.IGNORECASE)
        signal = signal_match.group(1).upper() if signal_match else "NONE"

        confidence_match = re.search(r"CONFIDENCE:\s*(\d{1,3})", content, re.IGNORECASE)
        confidence = int(confidence_match.group(1)) if confidence_match else 50
        confidence = max(0, min(100, confidence))

        threat_match = re.search(r"THREAT:\s*(LOW|MEDIUM|HIGH)", content, re.IGNORECASE)
        threat = threat_match.group(1).upper() if threat_match else "MEDIUM"

        reason_match = re.search(r"REASON:\s*(.+?)(?:\n|$)", content, re.IGNORECASE)
        reason = reason_match.group(1).strip() if reason_match else content[:240].strip()

        return {"signal": signal, "confidence": confidence, "threat": threat, "reason": reason, "raw": content}

    except requests.exceptions.ConnectionError:
        logger.error(
            "[VISION] Cannot connect to Ollama at %s",
            normalize_ollama_base_url(config.OLLAMA_V1_URL),
        )
        return {"signal": "NONE", "reason": "Ollama connection failed", "raw": ""}
    except Exception as e:
        logger.error("[VISION] Error analyzing %s: %s", symbol, e)
        return {"signal": "NONE", "reason": f"Vision analysis error: {e}", "raw": ""}


def detect_symbol_from_chart(
    screenshot_base64: str,
    model: str = None,
    timeout: Optional[int] = None,
) -> str:
    """
    Ask the vision model to read the chart header and identify the symbol.

    This gives MT5 mode a lightweight symbol readback path so the operator
    does not have to keep maintaining brittle broker-specific symbol maps.
    """
    try:
        clean_b64 = normalize_base64_image(screenshot_base64)
    except ValueError as exc:
        logger.warning("[VISION] Symbol detection skipped due to malformed image payload: %s", exc)
        return ""

    headers = {"Content-Type": "application/json"}
    if getattr(config, "OLLAMA_API_KEY", ""):
        headers["Authorization"] = f"Bearer {config.OLLAMA_API_KEY}"

    chosen_model = model or config.MULTI_ASSET_VISION_MODEL
    is_valid, model_err = _validate_vision_model(chosen_model)
    if not is_valid:
        logger.error("[VISION] %s", model_err)
        return ""

    prompt = (
        "Read the chart header and identify the trading symbol. "
        "Understand broker names and futures contracts. Examples: MNQ-JUN26, MNQM26, MNQ.micro, "
        "or Micro Nasdaq all mean Micro Nasdaq 100; MES variants mean Micro S&P 500; MCL variants mean Micro Crude Oil. "
        "Return JSON only with keys symbol, exchange, normalized_symbol, instrument_name, confidence. "
        "If unreadable, return symbol as an empty string."
    )
    request_timeout = max(int(timeout or config.LLM_TIMEOUT), 45)
    v1_url = build_ollama_url(config.OLLAMA_V1_URL, "v1/chat/completions")
    native_url = build_ollama_url(config.OLLAMA_BASE_URL, "api/chat")

    payload = {
        "model": chosen_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": build_image_data_uri(clean_b64)}},
                ],
            }
        ],
        "stream": False,
        "temperature": 0.0,
        "max_tokens": 128,
    }

    native_payload = {
        "model": chosen_model,
        "messages": [{"role": "user", "content": prompt, "images": [clean_b64]}],
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 128},
    }

    def _parse_content(data: dict) -> str:
        choices = data.get("choices", [])
        if choices:
            return str(choices[0].get("message", {}).get("content", "") or "")
        msg = data.get("message", {})
        return str(msg.get("content", "") or "")

    try:
        try:
            response = requests.post(v1_url, json=payload, headers=headers, timeout=request_timeout)
            response.raise_for_status()
            raw = _parse_content(response.json())
        except requests.exceptions.HTTPError as http_err:
            if http_err.response is None or http_err.response.status_code != 404:
                raise
            response = requests.post(native_url, json=native_payload, headers=headers, timeout=request_timeout)
            response.raise_for_status()
            raw = _parse_content(response.json())

        parsed = parse_json_response(raw)
        symbol = str(
            parsed.get("normalized_symbol")
            or parsed.get("symbol")
            or ""
        ).strip().upper()
        if not symbol:
            match = re.search(r"\b[A-Z0-9:_.=!-]{2,20}\b", raw.upper())
            symbol = match.group(0) if match else ""
        return symbol
    except Exception as exc:
        logger.warning("[VISION] Symbol detection failed: %s", exc)
        return ""


def detect_symbol_details_from_chart(
    screenshot_base64: str,
    model: str = None,
    timeout: Optional[int] = None,
) -> dict:
    """Return raw and translated symbol details from the visible chart."""
    detected = detect_symbol_from_chart(screenshot_base64, model=model, timeout=timeout)
    if not detected:
        return {}
    translation = translate_chart_symbol(detected)
    if translation:
        payload = translation.to_dict()
        payload["analysis_symbol"] = translation.tradingview_symbol
        return payload
    return {
        "raw_symbol": detected,
        "analysis_symbol": normalize_to_analysis_symbol(detected),
        "instrument_name": detected,
        "family": "UNKNOWN",
        "mt5_symbol": detected,
        "confidence": 0.35,
    }


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
        self.base_url = normalize_ollama_base_url(config.OLLAMA_BASE_URL)
        self.model = config.OLLAMA_MODEL
        self.timeout = max(int(config.LLM_TIMEOUT), 180)
        self.devils_advocate = DevilsAdvocate()
        logger.info(f"[BRAIN] Local Brain initialized: {self.model} at {self.base_url}")

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
        logger.info(f"[BRAIN] Analyzing {market_data.asset} with {self.model}")
        if user_suggestion:
            logger.info(f"[SUCCESS] User suggestion received: {user_suggestion}")

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

        vision_result = None
        detected_symbol = ""
        vision_summary = "Vision agent: no chart image supplied."
        if chart_image_base64:
            vision_result = analyze_chart_with_vision(
                chart_image_base64,
                market_data.asset,
                model=config.MULTI_ASSET_VISION_MODEL,
                timeout=self.timeout,
            )
            detected_symbol = detect_symbol_from_chart(
                chart_image_base64,
                model=config.MULTI_ASSET_VISION_MODEL,
                timeout=min(self.timeout, 60),
            )
            if detected_symbol:
                market_data.indicators["VISION_DETECTED_SYMBOL"] = detected_symbol
                translation = translate_chart_symbol(detected_symbol)
                if translation:
                    market_data.indicators["VISION_SYMBOL_TRANSLATION"] = translation.to_dict()
                    market_data.indicators["VISION_ANALYSIS_SYMBOL"] = translation.tradingview_symbol
            vision_summary = self._format_vision_summary(vision_result, detected_symbol, market_data.asset)

        closer_prompt = self._build_analysis_prompt(
            market_data,
            news_context,
            session_context,
            user_suggestion,
            vibe_result=vibe_result,
            liquidity_result=liquidity_result,
            memory_summary=memory_summary,
            skip_reason=skip_reason,
            vision_summary=vision_summary,
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
        )

        output = self._apply_vision_guardrails(output, vision_result)
        output = self._apply_temporal_guardrails(output, session_context)
        output = self._apply_sentiment_guardrails(output, news_context)

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

        # [DEVIL] Devil's Advocate Challenge - Find reasons NOT to take this trade
        devils_challenge = self.devils_advocate.challenge_trade(
            market_data=market_data,
            suggested_action=output.action.value,
            entry_price=output.entry_price or market_data.price,
            stop_loss=output.stop_loss or market_data.price * 0.99,
            take_profit=output.take_profit or market_data.price * 1.01,
            confidence=output.confidence.value,
            session_context=session_context,
        )

        # Apply confidence penalty if Devil's Advocate found issues
        if devils_challenge.get("rating") in ["STRONG_AVOID", "CAUTIOUS"]:
            penalty = devils_challenge.get("confidence_penalty", -0.10)
            logger.warning(
                f"[DEVIL] Devil's Advocate PENALTY: {penalty:.2f} | "
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

        logger.info(f"[OK] Analysis complete: {output.action.value} {market_data.asset}")
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
        vision_summary: str = "",
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

VISION AGENT:
{vision_summary or 'Vision agent unavailable.'}

Rules:
- Output action BUY, SELL, or HOLD.
- Prefix reason with [SIGNAL] BUY, [SIGNAL] SELL, or [SIGNAL] WAIT.
- Respect VIBE MEMORY. If a similar losing pattern is present, downgrade confidence or WAIT unless the setup is clearly stronger now.
- If the vision agent disagrees with the setup or cannot confirm the chart, reduce aggression and prefer HOLD.
- If session context warns about a holiday or Friday close-risk window, avoid fresh entries and prefer WAIT.
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
            f"Day: {session_context.get('day_of_week', 'Unknown')} | "
            f"UTC: {session_context.get('current_time_utc', 'Unknown')} | "
            f"FridayCloseRisk: {session_context.get('is_friday_close_window', False)} | "
            f"HolidayUS: {session_context.get('is_holiday_us', False)} | "
            f"HolidayHK: {session_context.get('is_holiday_hk', False)} | "
            f"Note: {session_context.get('session_note', '')}"
        )

    def _format_vision_summary(
        self,
        vision_result: Optional[Dict[str, Any]],
        detected_symbol: str,
        expected_symbol: str,
    ) -> str:
        if not vision_result:
            return "Vision agent unavailable."
        signal = str(vision_result.get("signal") or "NONE").upper()
        reason = str(vision_result.get("reason") or "No visual reasoning provided.").strip()
        detected = detected_symbol or "UNREADABLE"
        return (
            f"Detected symbol: {detected} | Expected symbol: {expected_symbol} | "
            f"Visual signal: {signal} | Reason: {reason}"
        )

    def _apply_sentiment_guardrails(
        self,
        output: LLMAnalysisOutput,
        news_context: Optional[Dict[str, Any]],
    ) -> LLMAnalysisOutput:
        """Conflict Resolution: If Vision (BUY) conflicts with Sentiment (bearish / Red Folder),
        default to the safer side (HOLD or confidence downgrade)."""
        if not news_context or output.action == SignalAction.HOLD:
            return output

        # Red Folder kill switch: any active high-impact event blocks fresh entries
        red_folder_active = bool(news_context.get("red_folder_active", False))
        if red_folder_active:
            proposed = output.action.value
            output.action = SignalAction.HOLD
            output.confidence = ConfidenceLevel.LOW
            output.reason = (
                f"[SIGNAL] WAIT Sentiment conflict: Red Folder event active. "
                f"Vision proposed {proposed} but news kills the entry. Standing aside."
            )
            logger.warning("[CRO] Sentiment guardrail: Red Folder blocked %s signal", proposed)
            return output

        # Softer conflict: negative sentiment disagrees with Vision direction
        sentiment_bias = str(news_context.get("sentiment_bias", "")).upper()
        if sentiment_bias and sentiment_bias != output.action.value:
            # Vision says BUY but news is bearish (or vice versa) — downgrade confidence
            if output.confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.VERY_HIGH):
                prev = output.confidence
                output.confidence = ConfidenceLevel.MEDIUM
                output.reason = (
                    f"{output.reason} | Sentiment conflict: news bias is {sentiment_bias}, "
                    f"downgraded from {prev.value} to MEDIUM."
                )
                logger.info(
                    "[CRO] Sentiment/Vision conflict: news=%s vs signal=%s — confidence downgraded",
                    sentiment_bias, output.action.value,
                )
        return output

    def _apply_vision_guardrails(
        self,
        output: LLMAnalysisOutput,
        vision_result: Optional[Dict[str, Any]],
    ) -> LLMAnalysisOutput:
        if not vision_result:
            return output

        vision_signal = str(vision_result.get("signal") or "NONE").upper()
        if output.action == SignalAction.HOLD:
            return output
        if vision_signal in {"BUY", "SELL"} and vision_signal != output.action.value:
            proposed_action = output.action.value
            output.action = SignalAction.HOLD
            output.confidence = ConfidenceLevel.LOW
            output.reason = (
                f"[SIGNAL] WAIT Vision disagreement: chart shows {vision_signal} while text agents proposed "
                f"{proposed_action}. Standing aside."
            )
            return output
        if vision_signal == "NONE":
            output.reason = f"{output.reason} | Vision could not confirm the setup."
            if output.confidence == ConfidenceLevel.HIGH:
                output.confidence = ConfidenceLevel.MEDIUM
            elif output.confidence == ConfidenceLevel.VERY_HIGH:
                output.confidence = ConfidenceLevel.HIGH
        return output

    def _apply_temporal_guardrails(
        self,
        output: LLMAnalysisOutput,
        session_context: Optional[Dict[str, Any]],
    ) -> LLMAnalysisOutput:
        context = session_context or {}
        if output.action == SignalAction.HOLD:
            return output
        if context.get("is_holiday_us") or context.get("is_holiday_hk"):
            holiday_name = str(context.get("holiday_name") or "Market holiday")
            output.action = SignalAction.HOLD
            output.confidence = ConfidenceLevel.LOW
            output.reason = f"[SIGNAL] WAIT {holiday_name}. Fresh entries are blocked by holiday conditions."
            return output
        if context.get("is_friday_close_window"):
            cutoff = int(context.get("friday_close_cutoff_utc", getattr(config, "FRIDAY_CLOSE_CUTOFF_UTC", 18)) or 18)
            output.action = SignalAction.HOLD
            output.confidence = ConfidenceLevel.LOW
            output.reason = (
                f"[SIGNAL] WAIT Friday close-risk window after {cutoff:02d}:00 UTC. "
                "Avoiding fresh entries into the weekend."
            )
        return output

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
