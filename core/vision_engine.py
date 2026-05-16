"""
VcanTrade AI - Vision Engine

Hardware-optimized screen capture and vision-language model pipeline
for RTX 4050 Laptop GPU (6GB VRAM / 16GB RAM).

Design decisions for 6GB VRAM constraint:
- Primary model: moondream (1.8B params, ~1.5GB VRAM, blazing fast)
- Fallback model: llava:7b-q4_K_M (4-bit quantized, ~4GB VRAM)
- Max image resolution: 640x480 (minimizes VRAM during inference)
- JPEG quality: 75 (smaller payload, still readable by VLM)
- Timeout: 15 seconds (prevents hung inference blocking the pipeline)
- Ollama keep_alive: 30s (unloads model after use to free VRAM)
"""

import base64
import io
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import requests
from PIL import Image

import config
from core.fast_vision import build_fast_capture
from core.ollama_utils import build_ollama_url, normalize_ollama_base_url

logger = logging.getLogger(__name__)


def _is_passive_visual_mode() -> bool:
    """Return True when screenshot capture should be bypassed entirely."""
    if bool(getattr(config, "FAST_VISION_ENABLED", False)):
        return False
    # Passive modes are legacy TV desktop modes — not MT5 or main TradingView
    exec_mode = str(getattr(config, "EXECUTION_MODE", "")).upper().strip()
    surface = str(getattr(config, "ACTIVE_EXECUTION_SURFACE", "") or "").upper().strip()
    if surface == "MT5" or exec_mode == "MT5":
        return False
    return exec_mode in {"TV_DESKTOP", "TRADOVATE"}


# ---------------------------------------------------------------------------
# Hardware-optimized image settings for RTX 4050 6GB VRAM
# ---------------------------------------------------------------------------

# Default capture region (x, y, width, height)
DEFAULT_CHART_REGION = (
    config.CHART_REGION_X,
    config.CHART_REGION_Y,
    config.CHART_REGION_W,
    config.CHART_REGION_H,
)

# VLM image constraints [DASH] keep small to save VRAM during inference
VLM_MAX_WIDTH = 640  # 640px wide (not 720p [DASH] saves significant VRAM)
VLM_MAX_HEIGHT = 480  # 480px tall
VLM_QUALITY = 75  # JPEG quality (lower = smaller file = faster decode)
VLM_FORMAT = "JPEG"

# Inference timeout [DASH] 15s max before graceful degradation
VLM_TIMEOUT = 15

# Ollama keep_alive in seconds [DASH] unload model after this to free VRAM
OLLAMA_KEEP_ALIVE = "30s"

# Screenshot save directory (for debugging)
SCREENSHOT_DIR = Path("screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)

# Recommended lightweight vision models for 6GB VRAM
RECOMMENDED_MODELS = {
    "primary": "moondream",  # 1.8B, ~1.5GB VRAM, fastest
    "fallback": "llava:7b-v1.5-q4_K_M",  # 4-bit quantized, ~4GB VRAM
}


# ---------------------------------------------------------------------------
# ChartScreenshot
# ---------------------------------------------------------------------------


class ChartScreenshot:
    """
    Container for a captured chart image with metadata.
    Encodes to base64 for VLM API transmission.
    """

    def __init__(
        self,
        image: Image.Image,
        asset: str = "UNKNOWN",
        source: str = "screenshot",
        timestamp: Optional[datetime] = None,
        region: Optional[Tuple[int, int, int, int]] = None,
    ):
        self.image = image
        self.asset = asset
        self.source = source
        self.timestamp = timestamp or datetime.utcnow()
        self.region = region

    def to_base64(self, fmt: str = VLM_FORMAT, quality: int = VLM_QUALITY) -> str:
        """
        Encode image as base64 string for VLM API.
        Returns raw base64 (no data URI prefix) [DASH] Ollama expects plain base64.
        """
        buffer = io.BytesIO()
        resized = self._resize_for_vlm()
        resized.save(buffer, format=fmt, quality=quality)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def to_bytes(self, fmt: str = VLM_FORMAT, quality: int = VLM_QUALITY) -> bytes:
        """Return raw image bytes."""
        buffer = io.BytesIO()
        self._resize_for_vlm().save(buffer, format=fmt, quality=quality)
        return buffer.getvalue()

    def save_debug(self, filename: Optional[str] = None) -> Path:
        """Save screenshot to disk for debugging."""
        if not filename:
            filename = f"{self.asset}_{self.timestamp.strftime('%Y%m%d_%H%M%S')}.png"
        path = SCREENSHOT_DIR / filename
        self.image.save(path)
        logger.debug(f"Screenshot saved: {path}")
        return path

    def _resize_for_vlm(self) -> Image.Image:
        """
        Resize image to VRAM-friendly dimensions.
        640x480 max [DASH] moondream and llava-q4 handle this easily on 6GB VRAM.
        """
        w, h = self.image.size
        if w <= VLM_MAX_WIDTH and h <= VLM_MAX_HEIGHT:
            return self.image

        ratio = min(VLM_MAX_WIDTH / w, VLM_MAX_HEIGHT / h)
        new_w = int(w * ratio)
        new_h = int(h * ratio)
        return self.image.resize((new_w, new_h), Image.Resampling.LANCZOS)

    @property
    def dimensions(self) -> Tuple[int, int]:
        return self.image.size

    @property
    def file_size_estimate_kb(self) -> float:
        """Estimate compressed JPEG size in KB."""
        return len(self.to_bytes()) / 1024


# ---------------------------------------------------------------------------
# VisionCapture [DASH] Screen capture with library fallback chain
# ---------------------------------------------------------------------------


class VisionCapture:
    """
    Captures and preprocesses trading chart screenshots.
    Library priority: mss (fastest) > pyautogui (fallback).
    """

    def __init__(
        self,
        chart_region: Optional[Tuple[int, int, int, int]] = None,
        save_debug: bool = False,
    ):
        self.chart_region = chart_region or DEFAULT_CHART_REGION
        self.save_debug_screenshots = save_debug
        self._mss = None
        self._pyautogui = None
        self._fast_capture = build_fast_capture()

        # Try mss first (C-based, much faster than pyautogui)
        try:
            import mss

            self._mss = mss
            logger.info("VisionCapture: mss loaded (fast mode)")
        except ImportError:
            logger.debug("VisionCapture: mss not available")

        # Fallback to pyautogui
        if not self._mss:
            try:
                import pyautogui

                self._pyautogui = pyautogui
                pyautogui.FAILSAFE = True
                logger.info("VisionCapture: pyautogui loaded (fallback mode)")
            except ImportError:
                logger.error("VisionCapture: no screenshot library available")

    def capture_chart(
        self,
        asset: str = "UNKNOWN",
        region: Optional[Tuple[int, int, int, int]] = None,
    ) -> Optional[ChartScreenshot]:
        """
        Capture a screenshot of the trading chart region.

        Returns ChartScreenshot or None if capture failed.
        """
        if _is_passive_visual_mode():
            logger.debug("[VISION] Skipping visual screenshot in passive mode.")
            return None

        target_region = region or self.chart_region

        if self._fast_capture:
            frame = self._fast_capture.capture(target_region)
            img = frame.image if frame else None
            source = f"fast:{frame.backend}:{frame.latency_ms:.2f}ms" if frame else "fast"
        elif self._mss:
            img = self._capture_mss(target_region)
            source = "screenshot:mss"
        elif self._pyautogui:
            img = self._capture_pyautogui(target_region)
            source = "screenshot:pyautogui"
        else:
            logger.error("VisionCapture: no screenshot library available")
            return None

        if img is None:
            return None

        screenshot = ChartScreenshot(
            image=img,
            asset=asset,
            source=source,
            region=target_region,
        )

        if self.save_debug_screenshots:
            screenshot.save_debug()

        return screenshot

    def capture_full_desktop(self, asset: str = "UNKNOWN") -> Optional[ChartScreenshot]:
        """Capture the entire desktop."""
        if _is_passive_visual_mode():
            logger.debug("[VISION] Skipping visual screenshot in passive mode.")
            return None

        if self._mss:
            img = self._capture_mss_full()
        elif self._pyautogui:
            img = self._pyautogui.screenshot()
        else:
            return None

        if img is None:
            return None

        return ChartScreenshot(
            image=img,
            asset=asset,
            source="full_desktop",
        )

    def find_trading_window(
        self, window_title: str
    ) -> Optional[Tuple[int, int, int, int]]:
        """
        Find a trading application window by title.
        Returns (x, y, width, height) or None.
        """
        try:
            import pygetwindow as gw

            windows = gw.getWindowsWithTitle(window_title)
            if windows:
                win = windows[0]
                logger.info(
                    f"VisionCapture: found window '{window_title}' at {win.left},{win.top},{win.width},{win.height}"
                )
                return (win.left, win.top, win.width, win.height)
        except ImportError:
            logger.debug("VisionCapture: pygetwindow not available")
        except Exception as e:
            logger.error(f"VisionCapture: window search failed: {e}")
        return None

    def find_best_window(
        self,
        title_candidates: list[str],
        blacklist: Optional[list[str]] = None,
    ):
        """
        Find the best visible window matching candidate titles while skipping
        unsafe windows like terminals and code editors.
        """
        try:
            import pygetwindow as gw
        except ImportError:
            logger.debug("VisionCapture: pygetwindow not available for smart window search")
            return None
        except Exception as exc:
            logger.error("VisionCapture: window library failed to load: %s", exc)
            return None

        blacklist_terms = [
            term.lower()
            for term in (blacklist or getattr(config, "WINDOW_TITLE_BLACKLIST", []))
            if str(term).strip()
        ]
        candidate_terms = [
            term.lower()
            for term in (title_candidates or [])
            if str(term).strip()
        ]

        best_match = None
        best_score = float("-inf")
        for win in gw.getAllWindows():
            title = str(getattr(win, "title", "") or "").strip()
            if not title or getattr(win, "width", 0) <= 0 or getattr(win, "height", 0) <= 0:
                continue

            lowered = title.lower()
            if any(term in lowered for term in blacklist_terms):
                logger.debug("VisionCapture: skipping blacklisted window '%s'", title)
                continue

            score = 0
            for index, token in enumerate(candidate_terms):
                if token and token in lowered:
                    score += max(100 - (index * 10), 10)
            if score <= 0:
                continue

            if score > best_score:
                best_score = score
                best_match = win

        if best_match:
            logger.info(
                "VisionCapture: smart-eye locked '%s' at %s,%s %sx%s",
                best_match.title,
                best_match.left,
                best_match.top,
                best_match.width,
                best_match.height,
            )
        return best_match

    def capture_window_chart(
        self,
        asset: str,
        title_candidates: list[str],
        blacklist: Optional[list[str]] = None,
        crop: Optional[Tuple[float, float, float, float]] = None,
    ) -> Optional[ChartScreenshot]:
        """Capture a cropped chart region from a detected application window."""
        if _is_passive_visual_mode():
            logger.debug("[VISION] Skipping visual screenshot in passive mode.")
            return None

        window = self.find_best_window(title_candidates, blacklist=blacklist)
        if not window:
            logger.warning("VisionCapture: no matching window found for %s", title_candidates)
            return None

        region = self._crop_window_region(
            int(window.left),
            int(window.top),
            int(window.width),
            int(window.height),
            crop or (
                float(getattr(config, "MT5_CHART_CROP_LEFT_PCT", 0.04)),
                float(getattr(config, "MT5_CHART_CROP_TOP_PCT", 0.11)),
                float(getattr(config, "MT5_CHART_CROP_WIDTH_PCT", 0.92)),
                float(getattr(config, "MT5_CHART_CROP_HEIGHT_PCT", 0.78)),
            ),
        )

        image = self._capture_region(region)
        if image is None:
            return None

        screenshot = ChartScreenshot(
            image=image,
            asset=asset,
            source=f"window:{getattr(window, 'title', 'unknown')}",
            region=region,
        )
        if self.save_debug_screenshots:
            screenshot.save_debug()
        return screenshot

    def capture_active_chart(self, asset: str = "UNKNOWN") -> Optional[ChartScreenshot]:
        """
        Capture the active chart source based on launch mode.

        MT5 mode uses Smart Eye window detection and MT5 chart cropping.
        UI/browser mode keeps the existing fixed-region capture behavior.
        """
        if _is_passive_visual_mode():
            logger.debug("[VISION] Skipping visual screenshot in passive mode.")
            return None

        active_mode = config.get_active_mode()
        smart_eye_enabled = bool(getattr(config, "SMART_EYE_ENABLED", True))

        if active_mode == "MT5" and smart_eye_enabled:
            detected_title = str(getattr(config, "DETECTED_TRADING_WINDOW_TITLE", "") or "").strip()
            candidates = []
            if detected_title:
                candidates.append(detected_title)
            candidates.extend(list(getattr(config, "MT5_WINDOW_HINTS", [])))
            return self.capture_window_chart(
                asset=asset,
                title_candidates=candidates,
                blacklist=list(getattr(config, "WINDOW_TITLE_BLACKLIST", [])),
            )

        return self.capture_chart(asset=asset)

    def _capture_region(self, region: Tuple[int, int, int, int]) -> Optional[Image.Image]:
        if self._fast_capture:
            frame = self._fast_capture.capture(region)
            if frame:
                return frame.image
        if self._mss:
            return self._capture_mss(region)
        if self._pyautogui:
            return self._capture_pyautogui(region)
        logger.error("VisionCapture: no screenshot library available")
        return None

    def _crop_window_region(
        self,
        left: int,
        top: int,
        width: int,
        height: int,
        crop: Tuple[float, float, float, float],
    ) -> Tuple[int, int, int, int]:
        crop_left_pct, crop_top_pct, crop_width_pct, crop_height_pct = crop
        crop_left = max(0, min(int(width * crop_left_pct), max(width - 1, 0)))
        crop_top = max(0, min(int(height * crop_top_pct), max(height - 1, 0)))
        crop_width = max(50, int(width * crop_width_pct))
        crop_height = max(50, int(height * crop_height_pct))
        crop_width = min(crop_width, max(width - crop_left, 50))
        crop_height = min(crop_height, max(height - crop_top, 50))
        return (left + crop_left, top + crop_top, crop_width, crop_height)

    def _capture_mss(self, region: Tuple[int, int, int, int]) -> Optional[Image.Image]:
        """Fast screenshot via mss."""
        try:
            x, y, w, h = region
            with self._mss.mss() as sct:
                monitor = {"top": y, "left": x, "width": w, "height": h}
                sct_img = sct.grab(monitor)
                return Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        except Exception as e:
            logger.error(f"VisionCapture: mss capture failed: {e}")
            return None

    def _capture_mss_full(self) -> Optional[Image.Image]:
        """Full desktop capture via mss."""
        try:
            with self._mss.mss() as sct:
                sct_img = sct.grab(sct.monitors[0])
                return Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        except Exception as e:
            logger.error(f"VisionCapture: mss full capture failed: {e}")
            return None

    def _capture_pyautogui(
        self, region: Tuple[int, int, int, int]
    ) -> Optional[Image.Image]:
        """Screenshot via pyautogui."""
        try:
            return self._pyautogui.screenshot(region=region)
        except Exception as e:
            logger.error(f"VisionCapture: pyautogui capture failed: {e}")
            return None


# ---------------------------------------------------------------------------
# VLMClient [DASH] Hardware-optimized for RTX 4050 6GB VRAM
# ---------------------------------------------------------------------------


class VLMClient:
    """
    Client for local Vision-Language Models via Ollama.

    Optimized for RTX 4050 Laptop GPU (6GB VRAM):
    - Primary model: moondream (1.8B params, ~1.5GB VRAM)
    - Fallback model: llava:7b-v1.5-q4_K_M (~4GB VRAM)
    - 15-second timeout prevents hung inference
    - keep_alive=30s unloads model after use to free VRAM
    - Images resized to 640x480 max before sending
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: Optional[str] = None,
        timeout: int = VLM_TIMEOUT,
    ):
        self.base_url = normalize_ollama_base_url(base_url)
        self.model = model or config.VLM_MODEL
        self.timeout = timeout
        self._active_model = None  # Track which model is actually working

    def analyze_chart(
        self,
        screenshot: ChartScreenshot,
        prompt: str,
    ) -> Optional[str]:
        """
        Send chart screenshot to VLM for analysis.

        Handles VRAM errors and timeouts gracefully:
        - If primary model fails, tries fallback model
        - If all models fail, returns None (caller falls back to text-only)
        """
        img_base64 = screenshot.to_base64()

        # Try primary model first
        result = self._inference(img_base64, prompt, self.model)
        if result is not None:
            self._active_model = self.model
            return result

        # If primary is the fallback already, give up
        if self.model == RECOMMENDED_MODELS["fallback"]:
            return None

        # Try fallback model
        logger.warning(
            f"VLM: Primary model '{self.model}' failed, trying fallback '{RECOMMENDED_MODELS['fallback']}'"
        )
        result = self._inference(img_base64, prompt, RECOMMENDED_MODELS["fallback"])
        if result is not None:
            self._active_model = RECOMMENDED_MODELS["fallback"]
            return result

        logger.error("VLM: All models failed [DASH] vision analysis unavailable")
        return None

    def _inference(self, image_base64: str, prompt: str, model: str) -> Optional[str]:
        """
        Single inference call to Ollama with timeout and error handling.

        Catches:
        - Timeout (15s) [DASH] model too slow or VRAM thrashing
        - ConnectionError [DASH] model not loaded
        - HTTP 500 [DASH] OOM / VRAM error
        - Any other exception
        """
        try:
            resp = requests.post(
                build_ollama_url(self.base_url, "api/generate"),
                json={
                    "model": model,
                    "prompt": prompt,
                    "images": [image_base64],
                    "stream": False,
                    "keep_alive": OLLAMA_KEEP_ALIVE,
                    "format": "json",
                },
                timeout=self.timeout,
            )

            # HTTP 500 from Ollama usually means OOM / VRAM error
            if resp.status_code == 500:
                logger.error(
                    f"VLM: Ollama returned 500 for '{model}' [DASH] "
                    f"likely VRAM/OOM error. Model may need to be reloaded."
                )
                return None

            resp.raise_for_status()
            result = resp.json()
            response_text = result.get("response", "")

            if not response_text:
                logger.warning(f"VLM: Empty response from '{model}'")
                return None

            logger.info(
                f"VLM: '{model}' analysis complete "
                f"({len(response_text)} chars, {len(image_base64) * 3 // 4 // 1024}KB image)"
            )
            return response_text

        except requests.exceptions.Timeout:
            logger.warning(
                f"VLM: '{model}' timed out after {self.timeout}s [DASH] "
                f"model may be too large for 6GB VRAM or still loading"
            )
            return None

        except requests.exceptions.ConnectionError:
            logger.warning(f"VLM: Cannot connect to Ollama at {self.base_url}")
            return None

        except Exception as e:
            error_str = str(e).lower()
            if "out of memory" in error_str or "cuda" in error_str or "vr" in error_str:
                logger.error(
                    f"VLM: VRAM/OOM error with '{model}' [DASH] "
                    f"try a smaller model like moondream"
                )
            else:
                logger.error(f"VLM: Unexpected error with '{model}': {e}")
            return None

    def is_available(self, model: Optional[str] = None) -> bool:
        """Check if a VLM model is loaded in Ollama."""
        target = model or self.model
        try:
            resp = requests.get(build_ollama_url(self.base_url, "api/tags"), timeout=3)
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                return any(target in m.get("name", "") for m in models)
            return False
        except Exception:
            return False

    def get_available_vlm_models(self) -> list[str]:
        """List all vision-capable models available in Ollama."""
        try:
            resp = requests.get(build_ollama_url(self.base_url, "api/tags"), timeout=3)
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                # Filter for known vision models
                vision_keywords = [
                    "llava",
                    "moondream",
                    "vision",
                    "qwen2.5-vl",
                    "bakllava",
                ]
                return [
                    m["name"]
                    for m in models
                    if any(kw in m["name"].lower() for kw in vision_keywords)
                ]
            return []
        except Exception:
            return []

    def pull_model(self, model: Optional[str] = None) -> bool:
        """
        Trigger Ollama to pull a VLM model.
        Blocking call [DASH] may take several minutes depending on model size.
        """
        target = model or self.model
        logger.info(f"VLM: Pulling model '{target}' [DASH] this may take a few minutes...")
        try:
            resp = requests.post(
                build_ollama_url(self.base_url, "api/pull"),
                json={"name": target},
                timeout=600,
            )
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"VLM: Failed to pull model '{target}': {e}")
            return False

    def unload_model(self, model: Optional[str] = None) -> bool:
        """
        Force Ollama to unload a model from VRAM immediately.
        Useful before switching models or freeing VRAM for other tasks.
        """
        target = model or self.model
        try:
            resp = requests.post(
                build_ollama_url(self.base_url, "api/generate"),
                json={
                    "model": target,
                    "prompt": "",
                    "keep_alive": "0",
                },
                timeout=5,
            )
            logger.info(f"VLM: Unloaded '{target}' from VRAM")
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"VLM: Failed to unload '{target}': {e}")
            return False

    def get_vram_status(self) -> dict:
        """
        Get Ollama's current VRAM usage (if psutil is available).
        Returns dict with GPU memory info.
        """
        try:
            resp = requests.get(build_ollama_url(self.base_url, "api/ps"), timeout=3)
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("models", [])
                return {
                    "loaded_models": [m.get("name", "unknown") for m in models],
                    "model_count": len(models),
                }
            return {"loaded_models": [], "model_count": 0}
        except Exception:
            return {
                "loaded_models": [],
                "model_count": 0,
                "error": "Cannot reach Ollama",
            }


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------


def setup_vlm_for_hardware(
    base_url: str = "http://localhost:11434",
) -> VLMClient:
    """
    Configure VLM client optimally for RTX 4050 6GB VRAM.

    Strategy:
    1. Check if moondream is available [DASH] use it (fastest, lowest VRAM)
    2. If not, check for llava:7b-q4 [DASH] use it (acceptable)
    3. If neither, pull moondream automatically
    4. Return configured VLMClient
    """
    client = VLMClient(base_url=base_url, model=RECOMMENDED_MODELS["primary"])

    # Check what's available
    available = client.get_available_vlm_models()
    logger.info(f"VLM: Available vision models: {available}")

    if RECOMMENDED_MODELS["primary"] in available:
        logger.info(
            f"VLM: Using '{RECOMMENDED_MODELS['primary']}' (optimal for 6GB VRAM)"
        )
        return client

    if RECOMMENDED_MODELS["fallback"] in available:
        logger.warning(
            f"VLM: '{RECOMMENDED_MODELS['primary']}' not found, "
            f"using '{RECOMMENDED_MODELS['fallback']}' (higher VRAM usage)"
        )
        client.model = RECOMMENDED_MODELS["fallback"]
        return client

    # Neither available [DASH] pull moondream (small, fast download ~1GB)
    logger.info(
        f"VLM: No vision models found. Pulling '{RECOMMENDED_MODELS['primary']}' "
        f"(~1GB download, optimal for 6GB VRAM)..."
    )
    print(f"\n  Pulling moondream model (~1GB) [DASH] this takes 1-3 minutes...")
    print(
        f"  Run 'ollama pull moondream' manually in another terminal if this hangs.\n"
    )

    success = client.pull_model(RECOMMENDED_MODELS["primary"])
    if success:
        logger.info(f"VLM: '{RECOMMENDED_MODELS['primary']}' pulled successfully")
    else:
        logger.warning(
            f"VLM: Failed to pull '{RECOMMENDED_MODELS['primary']}'. "
            f"Run 'ollama pull moondream' manually."
        )

    return client
