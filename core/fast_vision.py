"""
Low-latency chart capture adapters.

The fastest Windows path available from Python is Desktop Duplication through
dxcam. When dxcam is not installed, this falls back to mss. This is not a CUDA
framebuffer tap, but it gives the rest of the bot a clean interface for that
future native backend without tying execution to slow saved screenshots.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from PIL import Image

import config

logger = logging.getLogger(__name__)

Region = Tuple[int, int, int, int]


@dataclass
class FastFrame:
    image: Image.Image
    backend: str
    captured_at: float
    latency_ms: float
    region: Optional[Region] = None


class FastFrameCapture:
    """Capture chart frames using the lowest-latency backend available."""

    def __init__(self, backend: str = "auto") -> None:
        self.requested_backend = (backend or "auto").lower().strip()
        self.backend = "unavailable"
        self._camera = None
        self._mss = None
        self._init_backend()

    def _init_backend(self) -> None:
        if self.requested_backend in {"auto", "dxcam", "desktop_duplication"}:
            try:
                import dxcam  # type: ignore

                self._camera = dxcam.create(output_color="RGB")
                if self._camera:
                    self.backend = "dxcam"
                    logger.info("[FAST_VISION] dxcam Desktop Duplication enabled")
                    return
            except Exception as exc:
                if self.requested_backend != "auto":
                    logger.warning("[FAST_VISION] dxcam unavailable: %s", exc)
                else:
                    logger.debug("[FAST_VISION] dxcam unavailable: %s", exc)

        if self.requested_backend in {"auto", "mss"}:
            try:
                import mss  # type: ignore

                self._mss = mss
                self.backend = "mss"
                logger.info("[FAST_VISION] mss fallback enabled")
                return
            except Exception as exc:
                logger.warning("[FAST_VISION] mss unavailable: %s", exc)

        logger.error("[FAST_VISION] No fast capture backend available")

    @property
    def available(self) -> bool:
        return self.backend in {"dxcam", "mss"}

    def capture(self, region: Optional[Region] = None) -> Optional[FastFrame]:
        if not self.available:
            return None

        started = time.perf_counter()
        image = None

        if self.backend == "dxcam":
            image = self._capture_dxcam(region)
        elif self.backend == "mss":
            image = self._capture_mss(region)

        if image is None:
            return None

        latency_ms = (time.perf_counter() - started) * 1000.0
        return FastFrame(
            image=image,
            backend=self.backend,
            captured_at=time.time(),
            latency_ms=latency_ms,
            region=region,
        )

    def _capture_dxcam(self, region: Optional[Region]) -> Optional[Image.Image]:
        try:
            dx_region = None
            if region:
                x, y, w, h = region
                dx_region = (x, y, x + w, y + h)
            frame = self._camera.grab(region=dx_region)
            if frame is None:
                return None
            return Image.fromarray(frame)
        except Exception as exc:
            logger.warning("[FAST_VISION] dxcam capture failed: %s", exc)
            return None

    def _capture_mss(self, region: Optional[Region]) -> Optional[Image.Image]:
        try:
            with self._mss.mss() as sct:
                if region:
                    x, y, w, h = region
                    monitor = {"left": x, "top": y, "width": w, "height": h}
                else:
                    monitor = sct.monitors[0]
                shot = sct.grab(monitor)
            return Image.frombytes("RGB", shot.size, shot.rgb)
        except Exception as exc:
            logger.warning("[FAST_VISION] mss capture failed: %s", exc)
            return None


def build_fast_capture() -> Optional[FastFrameCapture]:
    if not bool(getattr(config, "FAST_VISION_ENABLED", False)):
        return None
    capture = FastFrameCapture(str(getattr(config, "FAST_VISION_BACKEND", "auto")))
    if not capture.available:
        return None
    return capture
