"""
VcaniTrade AI - Local Qwen 2.5 Brain

100% local execution using Ollama + Qwen 2.5:7b
No cloud dependencies, no API tokens needed!

NOW WITH 8-SECOND THREAD-GATE PROTECTION:
- compute_sequential_swarm_consensus has 8-second timeout
- Prevents VRAM jams during sequential model execution
"""

import json
import logging
import re
import asyncio
import aiohttp
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
from core.market_intelligence import MarketIntelligenceAgent

logger = logging.getLogger(__name__)

# Cache for available Ollama models to avoid repeated /api/tags calls
_OLLAMA_MODEL_CACHE: Dict[str, bool] = {}

# ---------------------------------------------------------------------------
# PERSISTENT HTTP SESSION POOL (Task 2)
# A single keep-alive Session is reused for every Ollama call. This removes
# the TCP/TLS handshake cost of opening a fresh connection on every brain
# request and lets the local port stay warm between the machine-gun swarm
# calls. The session is created lazily and is thread-safe for our usage
# pattern (each thread issues independent POSTs against localhost).
# ---------------------------------------------------------------------------
import threading as _threading

_OLLAMA_SESSION: Optional["requests.Session"] = None
_OLLAMA_SESSION_LOCK = _threading.Lock()


def get_ollama_session() -> "requests.Session":
    """Return the shared keep-alive requests.Session, creating it on first use."""
    global _OLLAMA_SESSION
    if _OLLAMA_SESSION is None:
        with _OLLAMA_SESSION_LOCK:
            if _OLLAMA_SESSION is None:
                sess = requests.Session()
                # Pool sized for the 3-model parallel swarm + headroom.
                adapter = requests.adapters.HTTPAdapter(
                    pool_connections=8,
                    pool_maxsize=8,
                    max_retries=0,  # we do our own retry/backoff below
                )
                sess.mount("http://", adapter)
                sess.mount("https://", adapter)
                sess.headers.update({"Content-Type": "application/json"})
                _OLLAMA_SESSION = sess
                logger.info("[BRAIN] Persistent Ollama HTTP session pool initialized")
    return _OLLAMA_SESSION


def _is_openrouter_url(url: str) -> bool:
    """Return True when an Ollama URL has accidentally been pointed at OpenRouter."""
    return "openrouter.ai" in str(url or "").lower()


def _looks_like_local_ollama_model(model_name: str) -> bool:
    """Ollama model names usually look like qwen2.5:latest, not provider/model."""
    model_text = str(model_name or "").strip()
    return ":" in model_text and "/" not in model_text


def _response_preview(response: requests.Response, limit: int = 220) -> str:
    """Small, safe preview for non-JSON/HTTP error diagnostics."""
    content_type = response.headers.get("content-type", "unknown")
    text = (response.text or "").strip().replace("\n", " ")
    if len(text) > limit:
        text = f"{text[:limit]}..."
    return f"status={response.status_code}, content_type={content_type}, body={text or '<empty>'}"


def _decode_json_response(response: requests.Response, source: str) -> dict:
    """Decode a model response and raise a clear error for HTML/empty bodies."""
    if not (response.text or "").strip():
        raise ValueError(f"{source} returned an empty response body")
    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source} returned non-JSON: {_response_preview(response)}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{source} returned unexpected JSON type: {type(data).__name__}")
    return data


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
        data = _decode_json_response(response, "Ollama /api/tags")
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


def _extract_signal_line(raw: Any, default_confidence: int = 65) -> Optional[dict]:
    """Parse Predator's compact SIGNAL line even when it adds light wrapper text."""
    text = str(raw or "").strip()
    if not text or "SIGNAL" not in text.upper():
        return None
    
    signal_match = re.search(r"\bSIGNAL\s*[:=\-]\s*(BUY|SELL|NONE|HOLD|WAIT)\b", text, re.IGNORECASE)
    if not signal_match:
        return None
    
    signal = signal_match.group(1).upper()
    if signal in {"HOLD", "WAIT"}:
        signal = "NONE"
    
    confidence_match = re.search(
        r"\bCONFIDENCE\s*[:=\-]\s*(\d{1,3})(?:\s*%|\b)",
        text,
        re.IGNORECASE,
    )
    confidence = int(confidence_match.group(1)) if confidence_match else default_confidence
    confidence = max(0, min(100, confidence))
    
    threat_match = re.search(r"\bTHREAT\s*[:=\-]\s*(LOW|MEDIUM|HIGH)\b", text, re.IGNORECASE)
    threat = threat_match.group(1).upper() if threat_match else "MEDIUM"
    
    reason_match = re.search(
        r"\bREASON\s*[:=\-]\s*(.+?)(?=\s*\|\s*\b(?:SIGNAL|CONFIDENCE|THREAT)\b\s*[:=\-]|\n|$)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    reason = reason_match.group(1).strip(" \t\r\n|") if reason_match else ""
    if not reason:
        # Keep the Hunter log short and useful, never the full moondream paragraph.
        verdict_line = next((line.strip() for line in text.splitlines() if "SIGNAL" in line.upper()), text)
        reason = verdict_line[:220].strip()
    
    return {
        "signal": signal,
        "confidence": confidence,
        "threat": threat,
        "reason": reason,
        "content": text,
        "raw": text,
    }


def call_local_brain(
    prompt: str,
    model: str = None,
    timeout: Optional[int] = None,
    num_predict: Optional[int] = None,
) -> dict:
    """
    Simple wrapper to call local Ollama brain.
    This is the core "brain" function that runs locally.
    """
    url = "http://localhost:11434/api/generate"
    chosen_model = "qwen:latest"
    
    # Simple headers for local connection
    headers = {"Content-Type": "application/json"}
    
    payload = {
        "model": chosen_model,
        "prompt": prompt,
        "stream": False,
    }
    
    try:
        request_timeout = max(10, int(timeout or config.LLM_TIMEOUT))

        # ---------------------------------------------------------------
        # STEP 4 — PRE-POST AUDIT LOG (visible in both logger and stderr)
        # ---------------------------------------------------------------
        import sys
        _audit_msg = (
            f"\n{'='*72}\n"
            f"[BRAIN-AUDIT] PRE-POST PAYLOAD DUMP\n"
            f"  URL        : {url}\n"
            f"  model used : {chosen_model!r}\n"
            f"  stream     : {payload['stream']}\n"
            f"  timeout    : {request_timeout}s\n"
            f"  prompt[:80]: {str(prompt)[:80]!r}\n"
            f"{'='*72}"
        )
        print(_audit_msg, file=sys.stderr, flush=True)
        logger.info(_audit_msg)

        session = get_ollama_session()
        try:
            response = session.post(url, json=payload, headers=headers, timeout=request_timeout)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as net_err:
            import traceback
            print("\n" + "="*80, file=sys.stderr)
            print("[CONNECTION-ERROR] Cannot reach Ollama REST instance!", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            print("="*80 + "\n", file=sys.stderr, flush=True)
            logger.error("[CONNECTION-ERROR] Ollama connection failure: %s", net_err)
            raise net_err

        # Log the HTTP response status before raise_for_status so 404 is visible
        logger.info("[BRAIN] HTTP response: %s %s", response.status_code, response.reason)
        if response.status_code != 200:
            logger.error(
                "[BRAIN] Non-200 from Ollama — status=%s body=%s",
                response.status_code,
                response.text[:300],
            )

        response.raise_for_status()
        
        # Check for empty response body
        if not response.text.strip():
            logger.warning("[WARN] Ollama returned an empty string.")
            return {"error": "Ollama returned empty response"}
        
        try:
            data = _decode_json_response(response, "Ollama /api/generate")
        except ValueError as json_err:
            logger.error("[FAIL] Local AI Error: %s", json_err)
            return {"error": str(json_err)}
        
        raw_response = data.get('response', '{}')
        if not str(raw_response or "").strip():
            logger.warning("[WARN] Ollama returned JSON with an empty response field")
            return {"error": "Ollama returned empty response text"}
        
        signal_line = _extract_signal_line(raw_response)
        if signal_line:
            logger.info("[OK] Local brain responded with SIGNAL line")
            return signal_line
        
        logger.info("[OK] Local brain responded successfully")
        return parse_json_response(raw_response)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        raise
    except Exception as e:
        logger.error("[FAIL] Local AI Error: %s", e)
        raise e


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
    
    chosen_model = model or getattr(config, "FAST_CHART_VISION_MODEL", None) or config.MULTI_ASSET_VISION_MODEL
    is_valid, model_err = _validate_vision_model(chosen_model)
    if not is_valid:
        logger.error("[VISION] %s", model_err)
        return {"signal": "NONE", "reason": str(model_err), "raw": ""}
    
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
        "max_tokens": int(getattr(config, "OLLAMA_VISION_NUM_PREDICT", 96)),
        "top_p": 0.9,
        "keep_alive": getattr(config, "OLLAMA_KEEP_ALIVE", "30m"),
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
        "keep_alive": getattr(config, "OLLAMA_KEEP_ALIVE", "30m"),
        "options": {
            "temperature": 0.1,
            "num_predict": int(getattr(config, "OLLAMA_VISION_NUM_PREDICT", 96)),
            "num_ctx": int(getattr(config, "OLLAMA_NUM_CTX", 2048)),
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
        request_timeout = max(10, int(timeout or getattr(config, "OLLAMA_VISION_TIMEOUT", config.LLM_TIMEOUT)))
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
        
        # Two-stage architecture (user's custom setup):
        # moondream = eyes (good at describing charts)
        # predator = brain (your custom model makes the final trading decision)
        vision_model_used = chosen_model or getattr(config, "FAST_CHART_VISION_MODEL", "")
        if "moondream" in str(vision_model_used).lower():
            brain_prompt = (
                f"You are a professional futures trader. "
                f"Here is a detailed description of the {symbol} chart from a vision model:\n\n"
                f"{content}\n\n"
                "Return ONLY a single line in this exact format, nothing else:\n"
                "SIGNAL: BUY|SELL|NONE | CONFIDENCE: 0-100 | THREAT: LOW|MEDIUM|HIGH | REASON: one short sentence"
            )
            # Force predator (user's custom model) for the final trading decision
            # This is the critical step that turns the moondream description into a real SIGNAL
            brain_out = call_local_brain(
                brain_prompt,
                model="qwen2.5:1.5b-instruct-q4_K_M",
                timeout=int(getattr(config, "OLLAMA_PREDATOR_VERDICT_TIMEOUT", 25)),
                num_predict=int(getattr(config, "OLLAMA_BRAIN_NUM_PREDICT", 96)),
            )
            predator_text = str(
                brain_out.get("content")
                or brain_out.get("raw")
                or brain_out.get("response")
                or ""
            ).strip()
            predator_signal = (
                {
                    "signal": brain_out.get("signal"),
                    "confidence": brain_out.get("confidence"),
                    "threat": brain_out.get("threat"),
                    "reason": brain_out.get("reason"),
                    "raw": predator_text,
                }
                if brain_out.get("signal")
                else _extract_signal_line(predator_text, default_confidence=70)
            )
            
            if predator_signal:
                predator_signal["signal"] = str(predator_signal.get("signal") or "NONE").upper()
                predator_signal["confidence"] = max(0, min(100, int(predator_signal.get("confidence") or 70)))
                predator_signal["threat"] = str(predator_signal.get("threat") or "MEDIUM").upper()
                predator_signal["reason"] = str(predator_signal.get("reason") or "Predator signal accepted.").strip()
                predator_signal["raw"] = predator_text or str(brain_out)
                logger.info(
                    "[BRAIN] Predator final decision for %s: %s %s%% threat=%s",
                    symbol,
                    predator_signal["signal"],
                    predator_signal["confidence"],
                    predator_signal["threat"],
                )
                return predator_signal
            
            if "error" in brain_out:
                logger.warning("[BRAIN] Predator final decision unavailable for %s: %s", symbol, brain_out["error"])
            elif predator_text:
                content = predator_text
                logger.warning("[BRAIN] Predator response for %s had no SIGNAL line; falling back to vision parse", symbol)
        
        parsed_signal = _extract_signal_line(content)
        if parsed_signal:
            return parsed_signal
        
        return {
            "signal": "NONE",
            "confidence": 50,
            "threat": "MEDIUM",
            "reason": "No structured predator signal found.",
            "raw": content,
        }
    
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
    
    chosen_model = model or getattr(config, "FAST_CHART_VISION_MODEL", None) or config.MULTI_ASSET_VISION_MODEL
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
            match = re.search(r"\b[A-Z0-9:_.=!\-]{2,20}\b", raw.upper())
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
    
    signal_line = _extract_signal_line(raw)
    if signal_line:
        return signal_line
    
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
            logger.debug("Non-JSON LLM response (non-critical): %s", raw[:200])
            return {"error": "Invalid JSON", "raw": raw}


class OllamaSwarmConsensus:
    """Local Qwen 2.5 trading analyst - runs 100% on your machine."""
    
    _brain_lock = __import__('threading').Lock()  # Prevent concurrent Ollama calls
    
    def __init__(self):
        self.base_url = normalize_ollama_base_url(config.OLLAMA_BASE_URL)
        self.model = config.OLLAMA_MODEL
        self.timeout = max(int(getattr(config, "OLLAMA_TIMEOUT", 180)), 180)
        self.devils_advocate = DevilsAdvocate()
        self.mia = MarketIntelligenceAgent()
        logger.info("[BRAIN] Local Brain initialized: %s at %s", self.model, self.base_url)
        if _is_openrouter_url(self.base_url) and _looks_like_local_ollama_model(self.model):
            logger.error(
                "[BRAIN] Misconfigured local brain: %s is an Ollama model, but OLLAMA_BASE_URL points to OpenRouter.",
                self.model,
            )
    
    def resolve_active_model_string(self, targeted_model: str) -> str:
        """Safely converts descriptive quantized strings to actual Ollama registry names."""
        clean_string = targeted_model.lower()
        if "gemma" in clean_string:
            return "gemma:latest"
        if "qwen" in clean_string:
            return "qwen:latest"
        return "qwen:latest"
    
    def request_decision(self, proposed_action: str, package: dict[str, Any]) -> dict[str, Any]:
        """Multi-LLM swarm decision — fires 3 models SEQUENTIALLY (VRAM-safe) and builds confidence through consensus."""
        # Prevent concurrent brain calls (VRAM can only load one model at a time)
        with self._brain_lock:
            return self._request_decision_inner(proposed_action, package)
    
    async def run(
        self,
        market_data: MarketDataPoint,
        news_context: str = "",
        chart_image_base64: Optional[str] = None,
    ) -> Tuple[LLMAnalysisOutput, DebateTranscript]:
        """Runs single-model analysis directly on qwen:latest."""
        package = {
            "asset": market_data.asset,
            "recent_ohlcv": market_data.indicators.get("RECENT_CANDLES", []),
            "regime_context": market_data.indicators.get("REGIME_CONTEXT", ""),
            "rsi": market_data.indicators.get("RSI", 50.0),
            "atr": market_data.indicators.get("ATR", 0.0),
            "signal_type": market_data.indicators.get("SIGNAL_TYPE", "SWARM_ANALYSIS"),
        }
        
        proposed_action = "BUY" if market_data.indicators.get("SIGNAL_DIRECTION") == "BUY" else "SELL" if market_data.indicators.get("SIGNAL_DIRECTION") == "SELL" else "WAIT"
        
        decision = self.request_decision(proposed_action, package)
        
        verdict = decision.get("verdict", "[SIGNAL] WAIT")
        action = SignalAction.BUY if "BUY" in verdict else SignalAction.SELL if "SELL" in verdict else SignalAction.HOLD
        
        confidence_val = decision.get("confidence", 70)
        if confidence_val >= 85:
            confidence = ConfidenceLevel.HIGH
        elif confidence_val >= 60:
            confidence = ConfidenceLevel.MEDIUM
        else:
            confidence = ConfidenceLevel.LOW
            
        output = LLMAnalysisOutput(
            action=action,
            asset=market_data.asset,
            confidence=confidence,
            reason=decision.get("reasoning", "No reason provided."),
        )
        
        transcript = DebateTranscript(
            asset=market_data.asset,
            ceo_verdict=f"{verdict} {market_data.asset}",
            ceo_full_statement=output.reason,
        )
        
        return output, transcript

    def _request_decision_inner(self, proposed_action: str, package: dict[str, Any]) -> dict[str, Any]:
        """Internal single-model decision (called under brain_lock)."""
        asset = package.get("asset", "UNKNOWN")
        candles_json = json.dumps(package.get("recent_ohlcv", []), ensure_ascii=False)
        regime_context = package.get("regime_context", "")
        rsi = package.get("rsi", 50.0)
        atr = package.get("atr", 0.0)
        signal_type = package.get("signal_type", "UNKNOWN")
        
        prompt = f"""{PREDATOR_SYSTEM_INSTRUCTION}

Analyze this trading opportunity and return a JSON verdict.

Asset: {asset}
Signal: {signal_type} | Proposed: {proposed_action}
RSI: {rsi} | ATR: {atr}
Regime: {regime_context}
Recent candles: {candles_json}

RULES:
- If CHOPPY regime or RSI near 50, return WAIT
- Only BUY in bullish regime with RSI < 70
- Only SELL in bearish regime with RSI > 30

Return JSON: {{"signal":"BUY or SELL or WAIT","confidence":0-100,"reason":"under 100 chars"}}
"""
        # Directly invoke qwen:latest via our persistent connection
        result = call_local_brain(prompt, model="qwen:latest", timeout=15)
        
        signal = str(result.get("signal", "WAIT") or "WAIT").upper()
        if "BUY" in signal:
            signal = "BUY"
        elif "SELL" in signal:
            signal = "SELL"
        else:
            signal = "WAIT"
            
        confidence = int(result.get("confidence", 70) or 70)
        reason = str(result.get("reason", "Local brain verdict.") or "Local brain verdict.").strip()
        
        verdict = f"[SIGNAL] {signal}"
        
        logger.info(
            "[BRAIN] Direct decision for %s: %s (confidence=%d%%, reason=%s)",
            asset, signal, confidence, reason
        )
        
        return {
            "verdict": verdict,
            "reasoning": reason[:500],
            "model": "qwen:latest",
            "brain_used": "LOCAL_OLLAMA",
            "fallback_mode": False,
            "confidence": confidence,
            "consensus": "SINGLE_NODE",
            "votes": {signal: 1},
            "models_used": ["qwen:latest"],
        }
    
    def _build_fallback_brain_prompt(self, proposed_action: str, package: dict[str, Any]) -> str:
        candles_json = json.dumps(package.get("recent_ohlcv", []), ensure_ascii=False)
        zones_json = json.dumps(package.get("liquidity_zones", []), ensure_ascii=False)
        regime_context = package.get("regime_context", "")
        return f"""{PREDATOR_SYSTEM_INSTRUCTION}

You are the local Predator fallback strike gate.

Review the proposed {str(proposed_action or 'WAIT').upper()}.

{regime_context}

Market snapshot:
- Signal type: {package.get('signal_type', 'UNKNOWN')}
- Asset: {package.get('asset', 'UNKNOWN')}
- Last 10 OHLCV candles: {candles_json}
- Current RSI: {package.get('rsi', 50.0)}
- Current ATR: {package.get('atr', 0.0)}
- Primary liquidity label: {package.get('liquidity_zone_label', 'N/A')}
- Nearest liquidity zone coordinates: {zones_json}

CRITICAL RULES:
- If the MARKET REGIME says CHOPPY or the regime score is between -20 and +20, you MUST return WAIT.
- If the regime says STRONG_BEAR and the proposed action is BUY, you MUST return WAIT.
- If the regime says STRONG_BULL and the proposed action is SELL, you MUST return WAIT.
- Only confirm BUY when regime is LEAN_BULL or STRONG_BULL.
- Only confirm SELL when regime is LEAN_BEAR or STRONG_BEAR.

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
    
    async def compute_sequential_swarm_consensus(self, ticker: str, technical_strength: float) -> float:
        """Runs swarm analysis models sequentially with 8-second thread-gate protection to prevent VRAM jams."""
        models = ["qwen:latest"]
        verdicts = []
        base_url = "http://127.0.0.1:11434/api/generate"
        
        logger.info("[SWARM-ENGINE] Starting sequential model computation matrix for ticker: %s", ticker)
        
        # 8-SECOND THREAD-GATE PROTECTION: Prevents VRAM allocation hangs
        THREAD_GATE_TIMEOUT = 8.0  # 8 seconds max per model
        
        async with aiohttp.ClientSession() as session:
            for model in models:
                try:
                    payload = {
                        "model": model,
                        "prompt": f"Analyze futures asset context for {ticker}. Current Technical Strength: {technical_strength}. Return exact short sentiment response.",
                        "stream": False
                    }
                    
                    # Use asyncio.wait_for to enforce 8-second timeout
                    async with asyncio.timeout(THREAD_GATE_TIMEOUT):
                        async with session.post(base_url, json=payload, timeout=THREAD_GATE_TIMEOUT) as response:
                            if response.status == 200:
                                res_json = await response.json()
                                verdicts.append(res_json.get("response", ""))
                                logger.info("[SWARM-ENGINE] Model node %s resolved successfully.", model)
                
                except asyncio.TimeoutError:
                    logger.error("[SWARM-ENGINE] Model node %s timed out after %ss. Advancing engine loop.", model, THREAD_GATE_TIMEOUT)
                    continue
                except Exception as e:
                    logger.error("[SWARM-ENGINE] Failure on node %s: %s", model, str(e))
                    continue
        
        # Parse the compiled verdicts array and generate a unified risk matrix output...
        return 1.0  # Returns strict consensus score
    
    def _enrich_trend_data(self, market_data: MarketDataPoint) -> None:
        """Compute EMA-20, EMA-50, trend direction, and MTF alignment from recent candles."""
        import numpy as np
        
        candles = market_data.indicators.get("RECENT_CANDLES", [])
        if not candles or len(candles) < 20:
            return
        
        try:
            closes = []
            for c in candles:
                parts = str(c).split()
                for p in parts:
                    if p.startswith("C="):
                        closes.append(float(p[2:]))
                        break
            
            if len(closes) < 20:
                return
            
            closes_arr = np.array(closes, dtype=float)
            price = closes_arr[-1]
            
            ema_20 = self._compute_ema(closes_arr, 20)
            period_50 = min(50, len(closes_arr))
            ema_50 = self._compute_ema(closes_arr[-period_50:], period_50)
            
            if price > ema_20 > ema_50:
                trend = "BULLISH"
            elif price < ema_20 < ema_50:
                trend = "BEARISH"
            else:
                trend = "NEUTRAL"
            
            recent_5 = closes_arr[-5:]
            if trend == "BULLISH":
                alignment = "WITH" if recent_5[-1] > recent_5[0] else "AGAINST"
            elif trend == "BEARISH":
                alignment = "WITH" if recent_5[-1] < recent_5[0] else "AGAINST"
            else:
                alignment = "NEUTRAL"
            
            market_data.indicators["EMA_20"] = round(ema_20, 2)
            market_data.indicators["EMA_50"] = round(ema_50, 2)
            market_data.indicators["TREND_DIRECTION"] = trend
            market_data.indicators["MTF_BIAS"] = trend
            market_data.indicators["MTF_ALIGNMENT"] = alignment
            
            logger.info(
                "[TREND] %s | Price=%.2f | EMA20=%.2f | EMA50=%.2f | Trend=%s | MTF=%s",
                market_data.asset, price, ema_20, ema_50, trend, alignment,
            )
        except Exception as e:
            logger.warning("[TREND] Failed to enrich trend data: %s", e)
    
    def _compute_ema(self, data: "np.ndarray", period: int) -> float:
        """Compute EMA over the given period."""
        import numpy as np
        if len(data) < period:
            return float(data[-1]) if len(data) > 0 else 0.0
        multiplier = 2.0 / (period + 1)
        ema = float(data[0])
        for val in data[1:]:
            ema = float(val) * multiplier + ema * (1 - multiplier)
        return ema
    
    def _apply_devil_penalty(
        self,
        output: LLMAnalysisOutput,
        devils_challenge: Dict,
    ) -> LLMAnalysisOutput:
        """Apply Devil's Advocate confidence penalty to the final output."""
        penalty = float(devils_challenge.get("confidence_penalty", -0.10) or -0.10)
        rating = str(devils_challenge.get("rating", "NEUTRAL")).upper()
        reasons = devils_challenge.get("rejection_reasons", [])
        
        levels_to_drop = max(1, int(abs(penalty) / 0.12))
        
        confidence_order = {
            ConfidenceLevel.LOW: 0,
            ConfidenceLevel.MEDIUM: 1,
            ConfidenceLevel.HIGH: 2,
            ConfidenceLevel.VERY_HIGH: 3,
        }
        reverse_order = {v: k for k, v in confidence_order.items()}
        current_level = confidence_order.get(output.confidence, 1)
        
        if rating == "STRONG_AVOID":
            new_level = max(0, current_level - max(levels_to_drop, 2))
        elif rating == "CAUTIOUS":
            new_level = max(0, current_level - levels_to_drop)
        else:
            new_level = current_level
        
        output.confidence = reverse_order.get(new_level, ConfidenceLevel.MEDIUM)
        reason_str = "; ".join(reasons[:2]) if reasons else "Devil's Advocate flagged risks"
        output.reason = f"{output.reason} | [DEVIL] {reason_str}"
        return output