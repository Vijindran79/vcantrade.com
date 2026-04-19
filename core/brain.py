"""OpenRouter brain connector for final trade verdicts."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import config
from core.swarm_consensus import OllamaSwarmConsensus

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency at runtime
    OpenAI = None


logger = logging.getLogger(__name__)


class GeminiBrain:
    """OpenRouter-backed final execution gate for external model verdicts."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self.provider = "openrouter"
        self.api_keys = self._resolve_api_keys(api_key)
        self._key_index = 0
        self.api_key = self.api_keys[0] if self.api_keys else ""
        self.models = self._resolve_models(model)
        self.model = self.models[0] if self.models else "anthropic/claude-3.5-sonnet"
        self.timeout = int(getattr(config, "GEMINI_TIMEOUT", 20) or 20)
        self._client = self._build_openrouter_client()
        self.predator = OllamaSwarmConsensus()
        self.last_decision = self._fallback_decision("OpenRouter not queried yet.", self.model)

    def is_available(self) -> bool:
        return bool(self.api_key and self._client is not None)

    def request_verdict(self, proposed_action: str, package: dict[str, Any]) -> str:
        """Return the exact trade approval string used by the scanner."""
        return self.request_decision(proposed_action, package).get("verdict", "[SIGNAL] WAIT")

    def request_decision(self, proposed_action: str, package: dict[str, Any]) -> dict[str, Any]:
        """Return OpenRouter's verdict plus short reasoning for UI/execution use."""
        if not self.is_available():
            logger.warning("OpenRouter brain unavailable - switching to local Predator")
            self.last_decision = self._use_predator_fallback(proposed_action, package, "OpenRouter unavailable.")
            return dict(self.last_decision)

        prompt = self._build_prompt(proposed_action, package)
        decision = self._request_openrouter_decision(prompt, proposed_action, package)
        self.last_decision = decision
        return dict(decision)

    def _resolve_api_keys(self, api_key: Optional[str]) -> list[str]:
        if api_key:
            return [str(api_key).strip()]

        keys = [str(key).strip() for key in getattr(config, "OPENROUTER_API_KEYS", []) if str(key).strip()]
        if keys:
            return keys

        single_key = str(getattr(config, "OPENROUTER_API_KEY", "") or "").strip()
        return [single_key] if single_key else []

    def _resolve_models(self, model: Optional[str]) -> list[str]:
        candidates: list[str] = []
        for candidate in [
            model,
            getattr(config, "OPENROUTER_MODEL", ""),
            "anthropic/claude-3.5-sonnet",
            "meta-llama/llama-3-70b",
        ]:
            value = str(candidate or "").strip()
            if value and value not in candidates:
                candidates.append(value)
        return candidates

    def _build_openrouter_client(self):
        if not self.api_key or OpenAI is None:
            return None
        try:
            default_headers = {}
            if getattr(config, "OPENROUTER_SITE_URL", ""):
                default_headers["HTTP-Referer"] = config.OPENROUTER_SITE_URL
            if getattr(config, "OPENROUTER_APP_NAME", ""):
                default_headers["X-Title"] = config.OPENROUTER_APP_NAME
            return OpenAI(
                api_key=self.api_key,
                base_url=config.OPENROUTER_BASE_URL,
                default_headers=default_headers or None,
            )
        except Exception as exc:  # pragma: no cover - client init failure
            logger.warning("OpenRouter client init failed: %s", exc)
            return None

    def _request_openrouter_decision(
        self,
        prompt: str,
        proposed_action: str,
        package: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.api_keys or self._client is None:
            return self._use_predator_fallback(proposed_action, package, "OpenRouter client unavailable.")

        last_error = "OpenRouter returned no usable response."
        for model_name in self.models:
            attempts = max(1, len(self.api_keys))
            for _ in range(attempts):
                try:
                    response = self._client.chat.completions.create(
                        model=model_name,
                        temperature=0,
                        timeout=self.timeout,
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "You are the final trade gate for a trading bot. "
                                    "Return JSON only with keys verdict and reasoning."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ],
                    )
                    choice = (response.choices or [None])[0]
                    message = getattr(choice, "message", None)
                    content = self._extract_message_content(getattr(message, "content", ""))
                    decision = self._parse_decision_text(content, proposed_action, model_name)
                    if decision:
                        return decision
                    last_error = f"Model {model_name} returned unparsable content."
                    return self._use_predator_fallback(proposed_action, package, last_error)
                except Exception as exc:  # pragma: no cover - SDK/network failure
                    last_error = self._describe_exception(exc)
                    if self._should_rotate_key(exc) and self._rotate_openrouter_key():
                        logger.warning("OpenRouter key rotated after request failure: %s", last_error)
                        continue
                    logger.warning("OpenRouter request failed for %s: %s", model_name, last_error)
                    return self._use_predator_fallback(proposed_action, package, last_error)

        return self._use_predator_fallback(proposed_action, package, last_error)

    def _extract_message_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content") or ""
                    if text:
                        parts.append(str(text))
                elif item:
                    parts.append(str(item))
            return "\n".join(parts)
        return str(content or "")

    def _parse_decision_text(self, text: str, proposed_action: str, model_name: str) -> Optional[dict[str, Any]]:
        raw_text = str(text or "").strip()
        if not raw_text:
            return None

        reasoning = ""
        verdict_source = raw_text
        try:
            payload = json.loads(raw_text)
            if isinstance(payload, dict):
                verdict_source = payload.get("verdict") or payload.get("signal") or payload.get("action") or raw_text
                reasoning = str(payload.get("reasoning") or payload.get("reason") or "").strip()
        except json.JSONDecodeError:
            reasoning = ""

        verdict = self._normalize_verdict(verdict_source, proposed_action)
        return {
            "verdict": verdict,
            "reasoning": reasoning[:240],
            "model": model_name,
            "brain_used": "OPENROUTER",
            "fallback_mode": False,
            "raw_text": raw_text,
        }

    def _normalize_verdict(self, value: Any, proposed_action: str) -> str:
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
        if action in {"BUY", "SELL"} and normalized == action:
            return f"[SIGNAL] {action}"
        return "[SIGNAL] WAIT"

    def _fallback_decision(self, reasoning: str = "", model: Optional[str] = None) -> dict[str, Any]:
        return {
            "verdict": "[SIGNAL] WAIT",
            "reasoning": str(reasoning or "").strip()[:240],
            "model": model or self.model,
            "brain_used": "OPENROUTER",
            "fallback_mode": False,
            "raw_text": "",
        }

    def _use_predator_fallback(
        self,
        proposed_action: str,
        package: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        logger.warning("[FALLBACK MODE] Local Predator engaged: %s", reason)
        fallback_package = dict(package or {})
        fallback_package.setdefault("signal_type", package.get("signal_type") if isinstance(package, dict) else "UNKNOWN")
        decision = self.predator.request_decision(proposed_action, fallback_package)
        if not decision.get("reasoning"):
            decision["reasoning"] = str(reason)[:240]
        return decision

    def _should_rotate_key(self, exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        if status_code in {401, 402, 403, 408, 429}:
            return True

        detail = self._describe_exception(exc).lower()
        return any(
            marker in detail
            for marker in [
                "rate limit",
                "quota",
                "credits",
                "insufficient",
                "authentication",
                "unauthorized",
                "payment required",
                "too many requests",
            ]
        )

    def _describe_exception(self, exc: Exception) -> str:
        body = getattr(exc, "body", None)
        if body:
            return str(body)
        return str(exc)

    def _rotate_openrouter_key(self) -> bool:
        if len(self.api_keys) <= 1:
            return False

        next_index = (self._key_index + 1) % len(self.api_keys)
        if next_index == self._key_index:
            return False

        self._key_index = next_index
        self.api_key = self.api_keys[self._key_index]
        self._client = self._build_openrouter_client()
        return self._client is not None

    def _build_prompt(self, proposed_action: str, package: dict[str, Any]) -> str:
        candles_json = json.dumps(package.get("recent_ohlcv", []), ensure_ascii=False)
        zones_json = json.dumps(package.get("liquidity_zones", []), ensure_ascii=False)
        liquidity_label = str(package.get("liquidity_zone_label", "N/A") or "N/A")
        signal_type = str(package.get("signal_type", "UNKNOWN") or "UNKNOWN")
        return f"""You are OpenRouter, the final execution gate for an automated trading bot.

Review the proposed {str(proposed_action or 'WAIT').upper()} and decide if the bot should strike now.

Market snapshot:
- Signal type: {signal_type}
- Last 10 OHLCV candles: {candles_json}
- Current RSI: {package.get('rsi', 50.0)}
- Current ATR: {package.get('atr', 0.0)}
- Primary liquidity label: {liquidity_label}
- Nearest liquidity zone coordinates: {zones_json}

Return JSON only in this format:
{{"verdict":"[SIGNAL] BUY or [SIGNAL] SELL or [SIGNAL] WAIT","reasoning":"one short trading reason under 240 chars"}}

Rules:
- Approve only if the snapshot supports immediate execution.
- If the setup is weak, conflicting, or unclear, return [SIGNAL] WAIT.
- Do not include any keys other than verdict and reasoning.
"""


OpenRouterBrain = GeminiBrain