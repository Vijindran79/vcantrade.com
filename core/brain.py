"""External live brain connector for final trade verdicts."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import config

try:
    from google import genai as google_genai
except ImportError:  # pragma: no cover - optional dependency at runtime
    google_genai = None

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency at runtime
    OpenAI = None


logger = logging.getLogger(__name__)


class GeminiBrain:
    """Provider-agnostic final execution gate for external model verdicts."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self.provider = str(config.BRAIN_PROVIDER or "gemini").strip().lower()
        self.api_keys = self._resolve_api_keys(api_key)
        self._key_index = 0
        self.api_key = self.api_keys[0] if self.api_keys else ""
        self.model = model or self._resolve_model()
        self.timeout = int(config.GEMINI_TIMEOUT)
        self._client = None

        if self.provider == "openrouter":
            self._client = self._build_openrouter_client()
        else:
            self._client = self._build_gemini_client()

    def is_available(self) -> bool:
        return bool(self.api_key and self._client is not None)

    def request_verdict(self, proposed_action: str, package: dict[str, Any]) -> str:
        """Return the external brain's exact trade approval string."""
        if not self.is_available():
            logger.warning("External brain unavailable (%s) - defaulting to WAIT", self.provider)
            return "[SIGNAL] WAIT"

        prompt = self._build_prompt(proposed_action, package)
        text = self._request_text(prompt).strip()
        normalized = text.upper()
        if normalized in {"[SIGNAL] BUY", "[SIGNAL] SELL", "[SIGNAL] WAIT"}:
            return normalized
        return text or "[SIGNAL] WAIT"

    def _resolve_api_key(self) -> str:
        if self.provider == "openrouter":
            return str(config.OPENROUTER_API_KEY or "").strip()
        return str(config.GEMINI_API_KEY or "").strip()

    def _resolve_api_keys(self, api_key: Optional[str]) -> list[str]:
        if api_key:
            return [str(api_key).strip()]
        if self.provider == "openrouter":
            keys = [key for key in config.OPENROUTER_API_KEYS if key]
            if keys:
                return keys
            single_key = str(config.OPENROUTER_API_KEY or "").strip()
            return [single_key] if single_key else []
        single_key = self._resolve_api_key()
        return [single_key] if single_key else []

    def _resolve_model(self) -> str:
        if self.provider == "openrouter":
            return str(config.OPENROUTER_MODEL or "deepseek/deepseek-chat").strip()
        return str(config.GEMINI_MODEL or "gemini-2.5-flash").strip()

    def _build_openrouter_client(self):
        if not self.api_key or OpenAI is None:
            return None
        try:
            default_headers = {}
            if config.OPENROUTER_SITE_URL:
                default_headers["HTTP-Referer"] = config.OPENROUTER_SITE_URL
            if config.OPENROUTER_APP_NAME:
                default_headers["X-Title"] = config.OPENROUTER_APP_NAME
            return OpenAI(
                api_key=self.api_key,
                base_url=config.OPENROUTER_BASE_URL,
                default_headers=default_headers or None,
            )
        except Exception as exc:  # pragma: no cover - client init failure
            logger.warning("OpenRouter client init failed: %s", exc)
            return None

    def _build_gemini_client(self):
        if not config.GEMINI_ENABLED or not self.api_key or google_genai is None:
            return None
        try:
            return google_genai.Client(api_key=self.api_key)
        except Exception as exc:  # pragma: no cover - network/client init failure
            logger.warning("Gemini client init failed: %s", exc)
            return None

    def _request_text(self, prompt: str) -> str:
        if self.provider == "openrouter":
            return self._request_openrouter_text(prompt)

        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            return self._extract_text(response)
        except Exception as exc:  # pragma: no cover - SDK/network failure
            logger.warning("External brain request failed (%s): %s", self.provider, exc)
            return ""

    def _request_openrouter_text(self, prompt: str) -> str:
        if not self.api_keys:
            return ""

        attempts = len(self.api_keys)
        for _ in range(attempts):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are the final trade gate. Reply with exactly one signal string.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                )
                choice = (response.choices or [None])[0]
                message = getattr(choice, "message", None)
                return str(getattr(message, "content", "") or "")
            except Exception as exc:  # pragma: no cover - SDK/network failure
                if self._should_rotate_key(exc) and self._rotate_openrouter_key():
                    logger.warning(
                        "OpenRouter key rotated after request failure: %s",
                        self._describe_exception(exc),
                    )
                    continue
                logger.warning("External brain request failed (%s): %s", self.provider, exc)
                return ""
        return ""

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
        provider_name = "OpenRouter" if self.provider == "openrouter" else "GeminiBrain"
        return f"""You are {provider_name}, the final execution gate for an automated trading bot.

You are reviewing a proposed {str(proposed_action or 'WAIT').upper()} after a confirmed 5m/3m/1m triple alignment.

Data package:
- Last 10 OHLCV candles: {candles_json}
- Current RSI: {package.get('rsi', 50.0)}
- Current ATR: {package.get('atr', 0.0)}
- Nearest liquidity zone coordinates: {zones_json}

Rules:
- Return EXACTLY one of these strings and nothing else: [SIGNAL] {str(proposed_action or 'WAIT').upper()} or [SIGNAL] WAIT.
- Approve only if the provided data supports the proposed action.
- If the data is unclear, conflicting, or weak, return [SIGNAL] WAIT.
"""

    def _extract_text(self, response: Any) -> str:
        text = getattr(response, "text", None)
        if isinstance(text, str) and text.strip():
            return text

        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            texts = []
            for part in parts:
                part_text = getattr(part, "text", None)
                if part_text:
                    texts.append(str(part_text))
            if texts:
                return "\n".join(texts)
        return ""