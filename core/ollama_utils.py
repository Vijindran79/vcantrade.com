"""
Shared Ollama transport helpers.

Keeps URL joining and image payload normalization consistent across the bot so
vision requests do not fail because of double slashes, duplicated endpoint
suffixes, or malformed base64 strings.
"""

from __future__ import annotations

import base64
import binascii
import re
from urllib.parse import urlsplit, urlunsplit


_OLLAMA_SUFFIXES = (
    "/api/generate",
    "/api/chat",
    "/api/tags",
    "/api/pull",
    "/api/ps",
    "/v1/chat/completions",
    "/chat/completions",
    "/v1",
)

_DATA_URI_RE = re.compile(r"^data:image/[^;]+;base64,", re.IGNORECASE)


def normalize_ollama_base_url(base_url: str | None, default: str = "http://localhost:11434") -> str:
    """Return a normalized Ollama base URL without API endpoint suffixes."""
    raw = str(base_url or default).strip() or default
    if "://" not in raw:
        raw = f"http://{raw.lstrip('/')}"

    parts = urlsplit(raw)
    path = parts.path.rstrip("/")
    for suffix in _OLLAMA_SUFFIXES:
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break

    clean_path = path.rstrip("/")
    return urlunsplit((parts.scheme or "http", parts.netloc, clean_path, "", ""))


def build_ollama_url(base_url: str | None, path: str, default: str = "http://localhost:11434") -> str:
    """Join a normalized Ollama base URL with the requested endpoint path."""
    base = normalize_ollama_base_url(base_url, default=default).rstrip("/")
    return f"{base}/{str(path or '').lstrip('/')}"


def normalize_base64_image(image_base64: str | None) -> str:
    """
    Normalize an image payload for Ollama/OpenAI-compatible vision endpoints.

    Strips any data-URI prefix, removes whitespace/newlines, normalizes
    urlsafe base64 variants, repairs missing padding, and validates the final
    payload so malformed strings fail fast with a clear error.
    """
    raw = str(image_base64 or "").strip()
    if not raw:
        raise ValueError("image payload is empty")

    raw = _DATA_URI_RE.sub("", raw)
    raw = "".join(raw.split())
    raw = raw.replace("-", "+").replace("_", "/")

    padding = len(raw) % 4
    if padding:
        raw = f"{raw}{'=' * (4 - padding)}"

    try:
        base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"invalid base64 image payload: {exc}") from exc

    return raw


def build_image_data_uri(image_base64: str | None, mime_type: str = "image/jpeg") -> str:
    """Return a safe data URI for OpenAI-compatible image_url payloads."""
    clean = normalize_base64_image(image_base64)
    return f"data:{mime_type};base64,{clean}"
