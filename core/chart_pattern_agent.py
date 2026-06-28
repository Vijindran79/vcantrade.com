"""
Chart Pattern Confirmation Agent — 3rd Swarm Brain
====================================================

The "Professor Chartist" — a dedicated vision-language agent that:
1. Captures the TradingView chart when a signal fires (85% confidence)
2. Detects chart patterns via Ollama vision model
3. Confirms or rejects signals based on pattern matching
4. Boosts confidence from 85% → 100% when pattern aligns with direction
5. Rejects signal when pattern contradicts (reduces to 50%)

Chart patterns detected:
- Double Top / Double Bottom
- Head & Shoulders / Inverse H&S
- Ascending / Descending / Symmetrical Triangles
- Bull Flag / Bear Flag
- Rising/ Falling Wedge
- Support / Resistance breaks
- Engulfing candles / Doji / Hammer / Shooting Star

Architecture:
    Technical Signal (85%) → Chart Pattern Agent → Confirmed (100%) or Rejected (50%)
                                          ↓
    Ollama Vision Model (moondream / llava) reads chart screenshot
                                          ↓
    Natural language prompt: "Is this chart showing a [BUY/SELL] setup?"
"""

import base64
import io
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import requests
from PIL import Image

import config
from core.ollama_utils import build_ollama_url, normalize_ollama_base_url

logger = logging.getLogger(__name__)

# Patterns and their directional bias
PATTERN_DIRECTION = {
    "double_top": "SELL",
    "double_bottom": "BUY",
    "head_and_shoulders": "SELL",
    "inverse_head_and_shoulders": "BUY",
    "ascending_triangle": "BUY",
    "descending_triangle": "SELL",
    "symmetrical_triangle": "NEUTRAL",
    "bull_flag": "BUY",
    "bear_flag": "SELL",
    "rising_wedge": "SELL",
    "falling_wedge": "BUY",
    "support_break": "SELL",
    "resistance_break": "BUY",
    "bullish_engulfing": "BUY",
    "bearish_engulfing": "SELL",
    "hammer": "BUY",
    "shooting_star": "SELL",
    "doji": "NEUTRAL",
    "morning_star": "BUY",
    "evening_star": "SELL",
}

# Vision analysis prompt template
CHART_ANALYSIS_PROMPT = """You are a professional chart pattern analyst. Analyze this TradingView chart screenshot.

Expected trade: {action} {ticker}
Current confidence: {confidence}%

Examine the chart for:
1. Candlestick patterns (engulfing, doji, hammer, shooting star, morning/evening star)
2. Chart formations (double top/bottom, head and shoulders, triangles, flags, wedges)
3. Support/resistance levels and breakouts
4. Trend direction (is the overall trend aligned with the trade direction?)
5. Volume confirmation

Return ONLY a JSON object with this format:
{{
  "pattern_found": true/false,
  "pattern_name": "name of pattern or 'none'",
  "direction": "BUY"/"SELL"/"NEUTRAL",
  "confidence_boost": 0 to 15 (add this to current confidence),
  "reason": "brief explanation"
}}"""

QUICK_CHECK_PROMPT = """Quick check: Chart for {ticker}. Is there a valid {action} setup visible? Reply with ONLY: YES|NO|UNCLEAR and one brief reason."""


class ChartPatternAgent:
    """The 'Professor Chartist' — vision-language chart pattern confirmation."""

    def __init__(
        self,
        vision_model: str = "moondream:latest",
        fallback_model: str = "llava:7b-q4_K_M",
        ollama_base_url: Optional[str] = None,
        timeout: int = 20,
    ):
        self.vision_model = vision_model
        self.fallback_model = fallback_model
        self.ollama_base_url = normalize_ollama_base_url(ollama_base_url or getattr(config, "OLLAMA_BASE_URL", "http://localhost:11434"))
        self.timeout = timeout
        self._last_analysis: Dict = {}
        self._analysis_count: int = 0
        self._success_count: int = 0
        self._fail_count: int = 0

        logger.info(
            "[CHARTIST] Agent initialized | model=%s fallback=%s timeout=%ds",
            vision_model, fallback_model, timeout,
        )

    def analyze_signal(
        self,
        ticker: str,
        action: str,
        confidence: float,
        screenshot_base64: Optional[str] = None,
        screenshot_path: Optional[str] = None,
    ) -> Dict:
        """
        Analyze a chart screenshot and confirm or reject the signal.

        Args:
            ticker: Trading symbol (e.g., 'MES1!', 'MNQ1!')
            action: 'BUY' or 'SELL'
            confidence: Current confidence percentage (0-100)
            screenshot_base64: Base64-encoded screenshot (optional)
            screenshot_path: Path to screenshot file (optional)

        Returns:
            {
                'confirmed': True/False,
                'adjusted_confidence': 0-100,
                'pattern_found': True/False,
                'pattern_name': str,
                'reason': str,
                'analysis_time_ms': int,
            }
        """
        start_time = time.time()
        self._analysis_count += 1

        # Build the prompt
        prompt = CHART_ANALYSIS_PROMPT.format(
            action=action,
            ticker=ticker,
            confidence=int(confidence),
        )

        # Get image data
        image_b64 = screenshot_base64 or self._load_screenshot_to_b64(screenshot_path)
        if not image_b64:
            logger.warning("[CHARTIST] No screenshot available for %s", ticker)
            return self._quick_heuristic(ticker, action, confidence)

        # Call vision model
        logger.info("[CHARTIST] Analyzing %s %s chart (conf=%.0f%%)", action, ticker, confidence)
        result = self._call_vision_model(prompt, image_b64)

        elapsed_ms = int((time.time() - start_time) * 1000)

        if not result:
            self._fail_count += 1
            logger.warning("[CHARTIST] Vision model call failed for %s", ticker)
            return {
                "confirmed": True,  # Don't block on vision failure
                "adjusted_confidence": min(confidence + 5, 100),
                "pattern_found": False,
                "pattern_name": "vision_model_failed",
                "reason": "Vision model unavailable — passing with slight boost",
                "analysis_time_ms": elapsed_ms,
            }

        self._success_count += 1

        # Parse the response
        parsed = self._parse_vision_response(result)
        pattern_name = parsed.get("pattern_name", "unknown")
        pattern_direction = parsed.get("direction", "NEUTRAL")
        confidence_boost = parsed.get("confidence_boost", 0)
        reason = parsed.get("reason", "")

        # Determine if pattern confirms the trade direction
        action_upper = action.upper()
        pattern_aligned = pattern_direction == action_upper
        pattern_neutral = pattern_direction == "NEUTRAL"

        if pattern_aligned:
            # Pattern CONFIRMS trade direction → big boost
            adjusted = min(confidence + max(confidence_boost, 10), 100)
            confirmed = True
            logger.info(
                "[CHARTIST] ✅ CONFIRMED: %s %s | pattern=%s | %.0f%% → %.0f%%",
                action, ticker, pattern_name, confidence, adjusted,
            )
        elif pattern_neutral:
            # Neutral pattern → slight boost (maybe there's structure)
            adjusted = min(confidence + 5, 100)
            confirmed = confidence >= 80  # Pass through if already high
            logger.info(
                "[CHARTIST] ⚠️ NEUTRAL: %s %s | pattern=%s | keeping at %.0f%%",
                action, ticker, pattern_name, adjusted,
            )
        else:
            # Pattern CONTRADICTS trade direction → reject or reduce
            adjusted = max(confidence - 20, 50)  # Minimum 50% (not pure rejection)
            confirmed = False
            logger.warning(
                "[CHARTIST] ❌ REJECTED: %s %s | pattern=%s (%s) | %.0f%% → %.0f%% | reason=%s",
                action, ticker, pattern_name, pattern_direction, confidence, adjusted, reason,
            )

        self._last_analysis = {
            "ticker": ticker,
            "action": action,
            "pattern_name": pattern_name,
            "confirmed": confirmed,
            "adjusted_confidence": adjusted,
            "elapsed_ms": elapsed_ms,
        }

        return {
            "confirmed": confirmed,
            "adjusted_confidence": adjusted,
            "pattern_found": parsed.get("pattern_found", False),
            "pattern_name": pattern_name,
            "reason": reason,
            "analysis_time_ms": elapsed_ms,
        }

    def quick_check(
        self,
        ticker: str,
        action: str,
        screenshot_base64: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Fast binary check: is there a valid setup? Returns (YES|NO|UNCLEAR, reason).
        Uses a shorter prompt for speed (<5 seconds).
        """
        if not screenshot_base64:
            return "UNCLEAR", "No screenshot available"

        prompt = QUICK_CHECK_PROMPT.format(ticker=ticker, action=action)
        response = self._call_vision_model(prompt, screenshot_base64, fast=True)

        if not response:
            return "UNCLEAR", "Vision model failed"

        # Parse the response
        upper = response.strip().upper()
        if upper.startswith("YES"):
            return "YES", response
        elif upper.startswith("NO"):
            return "NO", response
        else:
            return "UNCLEAR", response

    def get_stats(self) -> Dict:
        """Return agent statistics."""
        return {
            "analyses": self._analysis_count,
            "successes": self._success_count,
            "failures": self._fail_count,
            "success_rate": round(self._success_count / max(1, self._analysis_count) * 100, 1),
            "last_analysis": self._last_analysis,
        }

    # --- Internal Methods ---

    def _call_vision_model(
        self,
        prompt: str,
        image_b64: str,
        fast: bool = False,
    ) -> Optional[str]:
        """
        Call the Ollama vision model with a prompt and image.

        Tries primary model first, falls back to secondary on failure.
        """
        models = [self.vision_model]
        if not fast and self.fallback_model != self.vision_model:
            models.append(self.fallback_model)

        for model in models:
            try:
                url = f"{self.ollama_base_url}/api/generate"
                payload = {
                    "model": model,
                    "prompt": prompt,
                    "images": [image_b64],
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "num_predict": 256 if not fast else 64,
                    },
                    "keep_alive": "30s",
                }

                timeout = 8 if fast else self.timeout
                resp = requests.post(
                    url,
                    json=payload,
                    timeout=timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                response_text = data.get("response", "").strip()

                if response_text:
                    logger.debug("[CHARTIST] %s responded: %s", model, response_text[:120])
                    return response_text
                else:
                    logger.warning("[CHARTIST] %s returned empty response", model)

            except requests.exceptions.Timeout:
                logger.warning("[CHARTIST] %s timed out after %ds", model, timeout)
            except Exception as e:
                logger.warning("[CHARTIST] %s call failed: %s", model, e)

        return None

    def _parse_vision_response(self, text: str) -> Dict:
        """Parse vision model response into structured data."""
        # Try to extract JSON
        json_str = text

        # Find JSON block if wrapped in markdown
        if "```json" in text:
            json_str = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            for part in text.split("```"):
                part = part.strip()
                if "{" in part and "}" in part:
                    json_str = part
                    break

        # Find JSON object
        start = json_str.find("{")
        end = json_str.rfind("}")
        if start >= 0 and end > start:
            json_str = json_str[start:end + 1]

        try:
            parsed = json.loads(json_str)
            return {
                "pattern_found": bool(parsed.get("pattern_found", False)),
                "pattern_name": str(parsed.get("pattern_name", "none")).lower(),
                "direction": str(parsed.get("direction", "NEUTRAL")).upper(),
                "confidence_boost": min(max(int(parsed.get("confidence_boost", 0)), 0), 15),
                "reason": str(parsed.get("reason", "")),
            }
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: parse from raw text
        result = {
            "pattern_found": False,
            "pattern_name": "unknown",
            "direction": "NEUTRAL",
            "confidence_boost": 0,
            "reason": text[:200],
        }

        text_upper = text.upper()

        # Direction detection
        if "BUY" in text_upper and "SELL" not in text_upper:
            result["direction"] = "BUY"
        elif "SELL" in text_upper and "BUY" not in text_upper:
            result["direction"] = "SELL"

        # Known patterns
        for pattern, direction in PATTERN_DIRECTION.items():
            if pattern.replace("_", " ") in text.lower():
                result["pattern_found"] = True
                result["pattern_name"] = pattern
                result["direction"] = direction
                result["confidence_boost"] = 10
                break

        return result

    def _load_screenshot_to_b64(self, path: Optional[str]) -> Optional[str]:
        """Load a screenshot file to base64."""
        if not path:
            return None
        try:
            img = Image.open(path)
            # Resize to 640x480 for VRAM efficiency
            img = img.resize((640, 480), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=75)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception as e:
            logger.warning("[CHARTIST] Screenshot load failed: %s", e)
            return None

    def _quick_heuristic(self, ticker: str, action: str, confidence: float) -> Dict:
        """Fallback heuristic when vision model is unavailable.
        
        Passes through signals above 70% confidence with a slight boost.
        Only rejects truly marginal signals (< 60%).
        """
        boost = 5 if confidence >= 80 else 0
        return {
            "confirmed": confidence >= 70,
            "adjusted_confidence": min(confidence + boost, 100),
            "pattern_found": False,
            "pattern_name": "heuristic_fallback",
            "reason": "No vision input — heuristic pass-through (conf >= 70%)",
            "analysis_time_ms": 0,
        }


# ---------------------------------------------------------------------------
# Integration helper — called from main.py signal handler
# ---------------------------------------------------------------------------

def confirm_with_chartist(
    ticker: str,
    action: str,
    confidence: float,
    screenshot_base64: Optional[str] = None,
) -> Tuple[bool, float, Dict]:
    """
    Convenience function to run chart pattern confirmation.
    
    Returns (confirmed, adjusted_confidence, pattern_info).
    """
    agent = _get_global_agent()
    result = agent.analyze_signal(
        ticker=ticker,
        action=action,
        confidence=confidence,
        screenshot_base64=screenshot_base64,
    )
    return (
        result["confirmed"],
        result["adjusted_confidence"],
        {
            "pattern_name": result.get("pattern_name", "none"),
            "reason": result.get("reason", ""),
            "analysis_time_ms": result.get("analysis_time_ms", 0),
        },
    )


_global_chartist: Optional[ChartPatternAgent] = None


def _get_global_agent() -> ChartPatternAgent:
    """Get or create the global ChartPatternAgent singleton."""
    global _global_chartist
    if _global_chartist is None:
        _global_chartist = ChartPatternAgent()
    return _global_chartist


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    print("=" * 60)
    print("Chart Pattern Agent — smoke test")
    print("=" * 60)

    agent = ChartPatternAgent()

    # Test without a real screenshot (heuristic fallback)
    result = agent.analyze_signal("MES1!", "BUY", 85)
    print(f"\nBUY MES1! @ 85% → confirmed={result['confirmed']}, adjusted={result['adjusted_confidence']}%")
    print(f"  Pattern: {result['pattern_name']}, Reason: {result['reason']}")

    result2 = agent.analyze_signal("MNQ1!", "SELL", 85, screenshot_path="test_chart.png")
    print(f"\nSELL MNQ1! @ 85% → confirmed={result2['confirmed']}, adjusted={result2['adjusted_confidence']}%")

    print(f"\nStats: {agent.get_stats()}")
    print("\n[OK] Chart Pattern Agent ready.")
