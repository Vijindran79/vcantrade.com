"""Thin adapter around the optional Vibe-Trading CLI."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from typing import Any, Optional


logger = logging.getLogger(__name__)

DEFAULT_VIBE_TIMEOUT_SECONDS = 10


class VibeTradingAdapter:
    """Call the installed vibe-trading CLI without coupling the app to its internals."""

    def __init__(self, command: str = "vibe-trading") -> None:
        self.command = command
        self.command_path = shutil.which(command) or (command if os.path.exists(command) else None)

    def is_available(self) -> bool:
        return bool(self.command_path)

    def availability_status(self) -> dict[str, Any]:
        return {
            "available": self.is_available(),
            "command": self.command_path or self.command,
            "shielded": True,
            "timeout_seconds": DEFAULT_VIBE_TIMEOUT_SECONDS,
        }

    def generate_strategy(
        self,
        prompt: str,
        *,
        timeout: int = DEFAULT_VIBE_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Generate a strategy run and fetch Pine output when possible."""
        if not self.is_available():
            return {
                "ok": False,
                "error": "vibe-trading CLI not installed",
                "fallback": True,
            }

        started = time.monotonic()

        run_result = self._run_command(
            [self.command, "run", "-p", prompt, "--json", "--no-rich"],
            timeout=timeout,
        )
        if not run_result["ok"]:
            return run_result

        payload = self._extract_json_payload(run_result.get("stdout", ""))
        run_id = self._find_run_id(payload)
        pine_script = None
        pine_error = None

        if run_id:
            remaining = max(0.0, float(timeout) - (time.monotonic() - started))
            if remaining <= 0:
                return {
                    "ok": False,
                    "error": f"Vibe shield timeout after {timeout}s",
                    "fallback": True,
                    "timed_out": True,
                }
            pine_result = self._run_command(
                [self.command, "--pine", run_id, "--no-rich"],
                timeout=max(1, int(remaining)),
            )
            if pine_result["ok"]:
                pine_script = self._extract_pine_script(pine_result.get("stdout", ""))
            else:
                return {
                    "ok": False,
                    "error": pine_result.get("error", "Vibe pine export failed"),
                    "fallback": True,
                    "timed_out": bool(pine_result.get("timed_out")),
                }

        return {
            "ok": True,
            "run_id": run_id,
            "report": payload,
            "pine_script": pine_script,
            "pine_error": pine_error,
            "raw_stdout": run_result.get("stdout", ""),
        }

    def _run_command(self, args: list[str], *, timeout: int) -> dict[str, Any]:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired:
            logger.warning("Vibe CLI timed out after %ss: %s", timeout, " ".join(args))
            return {
                "ok": False,
                "error": f"Vibe shield timeout after {timeout}s",
                "fallback": True,
                "timed_out": True,
            }
        except Exception as exc:
            logger.warning("Vibe CLI execution failed: %s", exc)
            return {
                "ok": False,
                "error": str(exc),
                "fallback": True,
            }

        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        ok = completed.returncode == 0
        if not ok:
            logger.warning("Vibe CLI returned %s: %s", completed.returncode, stderr or stdout)
        return {
            "ok": ok,
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "error": stderr or stdout or f"exit code {completed.returncode}",
            "fallback": not ok,
        }

    def _extract_json_payload(self, raw: str) -> dict[str, Any]:
        text = (raw or "").strip()
        if not text:
            return {}

        try:
            payload = json.loads(text)
            return payload if isinstance(payload, dict) else {"result": payload}
        except json.JSONDecodeError:
            pass

        match = re.search(r"(\{[\s\S]*\})", text)
        if match:
            try:
                payload = json.loads(match.group(1))
                return payload if isinstance(payload, dict) else {"result": payload}
            except json.JSONDecodeError:
                logger.warning("Unable to parse Vibe JSON payload")
        return {"raw": text}

    def _find_run_id(self, payload: Any) -> Optional[str]:
        if isinstance(payload, dict):
            for key in ("run_id", "id"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for value in payload.values():
                nested = self._find_run_id(value)
                if nested:
                    return nested
        elif isinstance(payload, list):
            for item in payload:
                nested = self._find_run_id(item)
                if nested:
                    return nested
        return None

    def _extract_pine_script(self, raw: str) -> Optional[str]:
        text = (raw or "").strip()
        if not text:
            return None

        marker = text.find("//@version")
        if marker >= 0:
            return text[marker:].strip()

        fence_match = re.search(r"```(?:pinescript|pine|javascript)?\s*([\s\S]*?)```", text, re.IGNORECASE)
        if fence_match:
            fenced = fence_match.group(1).strip()
            if fenced:
                return fenced

        return text if "strategy(" in text or "indicator(" in text else None