"""
VcanTrade AI - Vision Capture Module

Captures screenshots of active trading windows (TradingView, MetaTrader, etc.)
and preprocesses them for the Vision-Language Model (VLM) pipeline.

Supports:
- Full desktop screenshot (quick, no window targeting)
- Window-specific capture (targets a specific trading app)
- Region-of-interest cropping (focuses on chart area only)
- Image compression/resize for efficient VLM processing
"""

import io
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Default capture region (x, y, width, height) — full HD chart area
DEFAULT_CHART_REGION = (100, 100, 1280, 720)

# VLM-friendly image settings
VLM_MAX_WIDTH = 1024
VLM_MAX_HEIGHT = 768
VLM_QUALITY = 85  # JPEG quality (0-100)
VLM_FORMAT = "JPEG"  # JPEG is smaller and VLMs handle it fine

# Screenshot save directory (for debugging)
SCREENSHOT_DIR = Path("screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# ChartScreenshot Model
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

    def to_base64(self, format: str = VLM_FORMAT, quality: int = VLM_QUALITY) -> str:
        """
        Encode image as base64 string for VLM API.
        Returns data URI format: data:image/jpeg;base64,<encoded>
        """
        import base64

        buffer = io.BytesIO()
        resized = self._resize_for_vlm()
        resized.save(buffer, format=format, quality=quality)
        encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
        return f"data:image/{format.lower()};base64,{encoded}"

    def to_bytes(self, format: str = VLM_FORMAT, quality: int = VLM_QUALITY) -> bytes:
        """Return raw image bytes for local processing."""
        buffer = io.BytesIO()
        self._resize_for_vlm().save(buffer, format=format, quality=quality)
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
        """Resize image to VLM-friendly dimensions while preserving aspect ratio."""
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


# ---------------------------------------------------------------------------
# Vision Capture Engine
# ---------------------------------------------------------------------------


class VisionCapture:
    """
    Captures and preprocesses trading chart screenshots for VLM analysis.
    """

    def __init__(
        self,
        chart_region: Optional[Tuple[int, int, int, int]] = None,
        auto_resize: bool = True,
        save_debug: bool = False,
    ):
        self.chart_region = chart_region or DEFAULT_CHART_REGION
        self.auto_resize = auto_resize
        self.save_debug_screenshots = save_debug
        self._pyautogui = None
        self._mss_available = False

        # Try mss first (faster than pyautogui for screenshots)
        try:
            import mss

            self._mss = mss
            self._mss_available = True
            logger.info("mss screenshot library loaded (fast mode)")
        except ImportError:
            logger.debug("mss not available, falling back to pyautogui")

        # Fallback to pyautogui
        if not self._mss_available:
            try:
                import pyautogui

                self._pyautogui = pyautogui
                pyautogui.FAILSAFE = True
                logger.info("pyautogui loaded for screenshots")
            except ImportError:
                logger.error(
                    "Neither mss nor pyautogui available — vision capture disabled"
                )

    def capture_chart(
        self,
        asset: str = "UNKNOWN",
        region: Optional[Tuple[int, int, int, int]] = None,
    ) -> Optional[ChartScreenshot]:
        """
        Capture a screenshot of the trading chart.

        Args:
            asset: Asset symbol for labeling
            region: (x, y, width, height) — overrides default chart_region

        Returns:
            ChartScreenshot or None if capture failed
        """
        target_region = region or self.chart_region

        if self._mss_available:
            img = self._capture_mss(target_region)
        elif self._pyautogui:
            img = self._capture_pyautogui(target_region)
        else:
            logger.error("No screenshot library available")
            return None

        if img is None:
            return None

        screenshot = ChartScreenshot(
            image=img,
            asset=asset,
            source="screenshot",
            region=target_region,
        )

        if self.save_debug_screenshots:
            screenshot.save_debug()

        return screenshot

    def capture_full_desktop(self, asset: str = "UNKNOWN") -> Optional[ChartScreenshot]:
        """Capture the entire desktop — useful when chart region is unknown."""
        if self._mss_available:
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

    def _capture_mss(self, region: Tuple[int, int, int, int]) -> Optional[Image.Image]:
        """Fast screenshot capture via mss."""
        try:
            x, y, w, h = region
            with self._mss.mss() as sct:
                monitor = {"top": y, "left": x, "width": w, "height": h}
                sct_img = sct.grab(monitor)
                return Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        except Exception as e:
            logger.error(f"mss capture failed: {e}")
            return None

    def _capture_mss_full(self) -> Optional[Image.Image]:
        """Full desktop capture via mss."""
        try:
            with self._mss.mss() as sct:
                sct_img = sct.grab(sct.monitors[0])
                return Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        except Exception as e:
            logger.error(f"mss full capture failed: {e}")
            return None

    def _capture_pyautogui(
        self, region: Tuple[int, int, int, int]
    ) -> Optional[Image.Image]:
        """Screenshot capture via pyautogui."""
        try:
            return self._pyautogui.screenshot(region=region)
        except Exception as e:
            logger.error(f"pyautogui capture failed: {e}")
            return None

    def find_trading_window(
        self, window_title: str
    ) -> Optional[Tuple[int, int, int, int]]:
        """
        Attempt to find a trading application window by title.
        Returns (x, y, width, height) or None.

        Uses pygetwindow on Windows, or falls back to default region.
        """
        try:
            import pygetwindow as gw

            windows = gw.getWindowsWithTitle(window_title)
            if windows:
                win = windows[0]
                logger.info(
                    f"Found window '{window_title}': {win.left},{win.top},{win.width},{win.height}"
                )
                return (win.left, win.top, win.width, win.height)
        except ImportError:
            logger.debug("pygetwindow not available — using default region")
        except Exception as e:
            logger.error(f"Window search failed: {e}")

        return None


# ---------------------------------------------------------------------------
# VLM Client for Ollama
# ---------------------------------------------------------------------------


class VLMClient:
    """
    Client for local Vision-Language Models via Ollama.
    Supports llava, llama3.2-vision, qwen2.5-vl, and other multimodal models.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.2-vision",
        timeout: int = 30,
    ):
        self.base_url = base_url
        self.model = model
        self.timeout = timeout

    def analyze_chart(
        self,
        screenshot: ChartScreenshot,
        prompt: str,
    ) -> Optional[str]:
        """
        Send chart screenshot to VLM for analysis.
        Returns the VLM's text response.
        """
        import base64
        import requests

        # Encode image
        img_base64 = screenshot.to_base64()

        payload = {
            "model": self.model,
            "prompt": prompt,
            "images": [img_base64],
            "stream": False,
        }

        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            result = resp.json()
            response_text = result.get("response", "")
            logger.info(f"VLM analysis complete ({len(response_text)} chars)")
            return response_text
        except requests.exceptions.ConnectionError:
            logger.warning(f"VLM model '{self.model}' not available in Ollama")
            return None
        except Exception as e:
            logger.error(f"VLM analysis failed: {e}")
            return None

    def is_available(self) -> bool:
        """Check if the VLM model is loaded in Ollama."""
        import requests

        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=3)
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                return any(self.model in m.get("name", "") for m in models)
            return False
        except Exception:
            return False

    def pull_model(self) -> bool:
        """
        Trigger Ollama to pull the VLM model.
        This is a blocking call that may take several minutes.
        """
        import requests

        try:
            resp = requests.post(
                f"{self.base_url}/api/pull",
                json={"name": self.model},
                timeout=600,  # 10 min timeout for model download
            )
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Failed to pull VLM model: {e}")
            return False
