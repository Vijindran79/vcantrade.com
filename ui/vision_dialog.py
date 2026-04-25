"""
VcanTrade AI - Vision Confirmation Dialog

Uses Browser Agent to capture 15-minute chart screenshot.
Analyzes RSI and MACD visually using multimodal LLM (Qwen-VL or LLaVA).
Shows visual confirmation before trade approval.

Features:
- Captures chart screenshot via Browser Agent
- Sends to vision model for RSI/MACD confirmation
- Shows visual analysis results with confidence score
- Highlights if visual analysis contradicts signal
"""

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QFont, QPixmap, QImage
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QScrollArea,
    QFrame,
    QTextEdit,
)
import base64
import logging
from datetime import datetime

import config
from core.ollama_utils import build_ollama_url, normalize_base64_image

logger = logging.getLogger(__name__)

# Color constants
BG_DARK = "#0D1117"
BG_PANEL = "#161B22"
BORDER = "#30363D"
CYAN = "#00D4FF"
GREEN = "#3FB950"
RED = "#F85149"
ORANGE = "#D29922"
GRAY = "#8B949E"
WHITE = "#E6EDF3"
DIM = "#484F58"


class VisionConfirmationDialog(QDialog):
    """
    Dialog that captures chart screenshot and analyzes it visually.
    Confirms RSI and MACD patterns before allowing trade approval.
    """

    confirmed = pyqtSignal(dict)  # Emits signal_data with vision confirmation
    rejected = pyqtSignal(dict)  # Emits signal_data (vision failed or user rejected)

    def __init__(self, signal_data: dict, browser_agent=None, parent=None):
        super().__init__(parent)
        self.signal_data = signal_data
        self.browser_agent = browser_agent
        self.vision_result = None
        self.screenshot_base64 = None
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle(f"[EYE] Vision Confirmation: {self.signal_data['ticker']}")
        self.setModal(True)
        self.setFixedSize(900, 700)

        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Title
        ticker = self.signal_data["ticker"]
        action = self.signal_data["action"]
        title = QLabel(f"[EYE] Visual Chart Analysis: {action} {ticker}")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {CYAN};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Screenshot area
        self.screenshot_label = QLabel("[CAMERA] Capturing chart screenshot...")
        self.screenshot_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.screenshot_label.setMinimumHeight(300)
        self.screenshot_label.setStyleSheet(f"""
            background: {BG_PANEL};
            border: 2px dashed {BORDER};
            border-radius: 8px;
            color: {GRAY};
            font-size: 14px;
            padding: 20px;
        """)
        layout.addWidget(self.screenshot_label)

        # Vision analysis panel
        self.vision_panel = QWidget()
        self.vision_panel.setStyleSheet(f"""
            background: {BG_PANEL};
            border: 1px solid {BORDER};
            border-radius: 8px;
            padding: 12px;
        """)
        vision_layout = QVBoxLayout(self.vision_panel)
        vision_layout.setSpacing(8)

        # Status label
        self.vision_status = QLabel("[MAGNIFY] Analyzing chart patterns with vision model...")
        self.vision_status.setFont(QFont("Consolas", 11))
        self.vision_status.setStyleSheet(f"color: {ORANGE};")
        vision_layout.addWidget(self.vision_status)

        # Results area (scrollable)
        self.vision_results = QTextEdit()
        self.vision_results.setReadOnly(True)
        self.vision_results.setMaximumHeight(200)
        self.vision_results.setStyleSheet(f"""
            QTextEdit {{
                background: {BG_DARK};
                color: {WHITE};
                border: 1px solid {BORDER};
                border-radius: 6px;
                font-family: 'Consolas';
                font-size: 11px;
                padding: 8px;
            }}
        """)
        vision_layout.addWidget(self.vision_results)

        layout.addWidget(self.vision_panel)

        # Verdict section
        self.verdict_box = QWidget()
        self.verdict_box.setStyleSheet(f"""
            background: rgba(0, 212, 255, 0.1);
            border: 2px solid {CYAN};
            border-radius: 8px;
            padding: 10px;
        """)
        verdict_layout = QVBoxLayout(self.verdict_box)

        self.verdict_label = QLabel("[WAIT] Waiting for vision analysis...")
        self.verdict_label.setFont(QFont("Consolas", 12, QFont.Weight.Bold))
        self.verdict_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.verdict_label.setStyleSheet(f"color: {CYAN};")
        verdict_layout.addWidget(self.verdict_label)

        layout.addWidget(self.verdict_box)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)

        self.approve_btn = QPushButton("[OK] CONFIRM & APPROVE")
        self.approve_btn.setMinimumHeight(40)
        self.approve_btn.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        self.approve_btn.setStyleSheet(f"""
            QPushButton {{
                background: {GREEN};
                color: {BG_DARK};
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
            }}
            QPushButton:hover {{ background: #2ea043; }}
            QPushButton:disabled {{
                background: {GRAY};
                color: {DIM};
            }}
        """)
        self.approve_btn.clicked.connect(self._on_approve)
        self.approve_btn.setEnabled(False)
        button_layout.addWidget(self.approve_btn)

        self.skip_btn = QPushButton("[SKIP] SKIP VISION & APPROVE")
        self.skip_btn.setMinimumHeight(40)
        self.skip_btn.setFont(QFont("Consolas", 11))
        self.skip_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ORANGE};
                color: {BG_DARK};
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
            }}
            QPushButton:hover {{ background: #b8860b; }}
        """)
        self.skip_btn.clicked.connect(self._on_skip_vision)
        button_layout.addWidget(self.skip_btn)

        self.reject_btn = QPushButton("[FAIL] REJECT")
        self.reject_btn.setMinimumHeight(40)
        self.reject_btn.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        self.reject_btn.setStyleSheet(f"""
            QPushButton {{
                background: {RED};
                color: {BG_DARK};
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
            }}
            QPushButton:hover {{ background: #da3633; }}
        """)
        self.reject_btn.clicked.connect(self._on_reject)
        button_layout.addWidget(self.reject_btn)

        layout.addLayout(button_layout)

        # Start vision analysis after UI is ready
        QTimer.singleShot(100, self._start_vision_analysis)

    async def _start_vision_analysis(self):
        """Capture screenshot and analyze with vision model."""
        try:
            # Step 1: Capture screenshot via Browser Agent
            self.vision_results.append("[CAMERA] Step 1: Capturing chart screenshot...")
            
            if self.browser_agent and self.browser_agent.is_running:
                ticker = self.signal_data["ticker"]
                result = await self.browser_agent.execute_autonomous_task(ticker, "screenshot")
                
                if result.get("screenshot"):
                    self.screenshot_base64 = result["screenshot"]
                    self._display_screenshot()
                    self.vision_results.append("[OK] Screenshot captured successfully")
                else:
                    self.vision_results.append("[WARN] Screenshot capture failed - proceeding with text analysis only")
                    self.screenshot_base64 = None
            else:
                self.vision_results.append("[WARN] Browser agent not available - using text-only analysis")
                self.screenshot_base64 = None

            # Step 2: Analyze with vision model
            self.vision_results.append("\n[MAGNIFY] Step 2: Analyzing RSI and MACD patterns...")
            
            await self._analyze_with_vision()

        except Exception as e:
            logger.error(f"Vision analysis failed: {e}")
            self.vision_results.append(f"[FAIL] Vision analysis failed: {e}")
            self._show_verdict("ERROR", "Vision analysis failed - manual confirmation required")

    async def _analyze_with_vision(self):
        """Send screenshot to vision model for RSI/MACD analysis."""
        try:
            # Build vision prompt
            prompt = self._build_vision_prompt()
            
            # If we have screenshot, use vision model
            if self.screenshot_base64:
                self.vision_results.append("[BRAIN] Sending to vision model for analysis...")
                
                # Call vision model (Qwen-VL or LLaVA)
                result = await self._call_vision_model(prompt, self.screenshot_base64)
                
                if result and "error" not in result:
                    self.vision_result = result
                    self._display_vision_results(result)
                    
                    # Determine verdict
                    rsi_confirmed = result.get("rsi_confirmed", False)
                    macd_confirmed = result.get("macd_confirmed", False)
                    vision_confidence = result.get("confidence", 0.5)
                    
                    if rsi_confirmed and macd_confirmed and vision_confidence > 0.7:
                        self._show_verdict("CONFIRMED", f"Vision confirms setup (confidence: {vision_confidence:.0%})")
                        self.approve_btn.setEnabled(True)
                    elif vision_confidence > 0.5:
                        self._show_verdict("PARTIAL", f"Partial confirmation (confidence: {vision_confidence:.0%})")
                        self.approve_btn.setEnabled(True)
                    else:
                        self._show_verdict("WEAK", f"Weak visual confirmation (confidence: {vision_confidence:.0%})")
                        self.approve_btn.setEnabled(False)
                else:
                    self.vision_results.append(f"[WARN] Vision model failed: {result.get('error', 'Unknown error')}")
                    self._show_verdict("FAILED", "Vision analysis failed - use manual judgment")
            else:
                # No screenshot - text-only analysis
                self.vision_results.append("[WARN] No screenshot available - using text analysis only")
                self._show_verdict("TEXT_ONLY", "Text-only analysis - no visual confirmation")
                self.approve_btn.setEnabled(True)

        except Exception as e:
            logger.error(f"Vision model analysis failed: {e}")
            self.vision_results.append(f"[FAIL] Vision model error: {e}")
            self._show_verdict("ERROR", "Vision model error - use manual judgment")

    def _build_vision_prompt(self) -> str:
        """Build prompt for vision model to analyze chart."""
        ticker = self.signal_data["ticker"]
        action = self.signal_data["action"]
        
        return f"""Analyze this 15-minute chart for {ticker} and provide STRICT JSON:

{{
  "rsi_value": 45.2,
  "rsi_confirmed": true,
  "rsi_signal": "oversold or overbought or neutral",
  "macd_confirmed": true,
  "macd_signal": "bullish or bearish or neutral",
  "trend": "uptrend or downtrend or sideways",
  "support_level": 180.50,
  "resistance_level": 185.20,
  "confidence": 0.85,
  "notes": "Brief analysis in 1-2 sentences"
}}

Check:
1. What is the current RSI value? Is it oversold (<30) or overbought (>70)?
2. Is MACD showing bullish or bearish divergence?
3. What is the overall trend direction?
4. Are there clear support/resistance levels?
5. Does the visual setup confirm a {action} signal?

Respond with ONLY JSON - no markdown, no explanations."""

    async def _call_vision_model(self, prompt: str, image_base64: str):
        """Call vision language model for chart analysis."""
        import requests
        import json
        
        try:
            # Try Qwen-VL or LLaVA via Ollama
            vision_model = config.VLM_MODEL if config.USE_VISION else config.OLLAMA_MODEL
            clean_image = normalize_base64_image(image_base64)
            url = build_ollama_url(config.OLLAMA_BASE_URL, "api/generate")
            
            payload = {
                "model": vision_model,
                "prompt": prompt,
                "stream": False,
                "images": [clean_image],
                "options": {
                    "temperature": 0.1,
                    "num_predict": 512,
                }
            }
            
            response = requests.post(url, json=payload, timeout=config.VISION_TIMEOUT)
            response.raise_for_status()
            
            raw = response.json().get("response", "{}")
            
            # Parse JSON from response
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                # Try to extract JSON
                start = raw.find('{')
                end = raw.rfind('}') + 1
                if start >= 0 and end > start:
                    return json.loads(raw[start:end])
                return {"error": "Invalid JSON from vision model"}
                
        except Exception as e:
            logger.error(f"Vision model call failed: {e}")
            return {"error": str(e)}

    def _display_screenshot(self):
        """Display captured screenshot in UI."""
        try:
            if self.screenshot_base64:
                image_data = base64.b64decode(self.screenshot_base64)
                pixmap = QPixmap()
                pixmap.loadFromData(image_data)
                
                # Scale to fit
                scaled = pixmap.scaled(
                    QSize(850, 280),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                
                self.screenshot_label.setPixmap(scaled)
                self.screenshot_label.setStyleSheet("""
                    background: #0D1117;
                    border: 2px solid #30363D;
                    border-radius: 8px;
                """)
        except Exception as e:
            logger.error(f"Failed to display screenshot: {e}")
            self.screenshot_label.setText(f"[FAIL] Failed to display screenshot: {e}")

    def _display_vision_results(self, result: dict):
        """Display vision analysis results."""
        self.vision_results.clear()
        
        # RSI Analysis
        rsi_val = result.get("rsi_value", "N/A")
        rsi_signal = result.get("rsi_signal", "N/A")
        rsi_confirmed = result.get("rsi_confirmed", False)
        
        rsi_color = GREEN if rsi_confirmed else RED
        self.vision_results.append(f"""
<b>[CHART] RSI Analysis:</b>
  Value: {rsi_val}
  Signal: <span style="color: {rsi_color}">{rsi_signal.upper()}</span>
  Confirmed: {'[OK]' if rsi_confirmed else '[FAIL]'}
""")
        
        # MACD Analysis
        macd_signal = result.get("macd_signal", "N/A")
        macd_confirmed = result.get("macd_confirmed", False)
        
        macd_color = GREEN if macd_confirmed else RED
        self.vision_results.append(f"""
<b>[UP] MACD Analysis:</b>
  Signal: <span style="color: {macd_color}">{macd_signal.upper()}</span>
  Confirmed: {'[OK]' if macd_confirmed else '[FAIL]'}
""")
        
        # Trend & Levels
        trend = result.get("trend", "N/A")
        support = result.get("support_level", "N/A")
        resistance = result.get("resistance_level", "N/A")
        
        self.vision_results.append(f"""
<b>[TARGET] Key Levels:</b>
  Trend: {trend}
  Support: ${support if isinstance(support, str) else f'{support:.2f}'}
  Resistance: ${resistance if isinstance(resistance, str) else f'{resistance:.2f}'}
""")
        
        # Notes
        notes = result.get("notes", "")
        if notes:
            self.vision_results.append(f"\n<b>[NOTE] Notes:</b> {notes}")
        
        # Confidence
        confidence = result.get("confidence", 0.5)
        conf_color = GREEN if confidence > 0.7 else ORANGE if confidence > 0.5 else RED
        self.vision_results.append(f'\n<b>[TARGET] Vision Confidence:</b> <span style="color: {conf_color}">{confidence:.0%}</span>')

    def _show_verdict(self, verdict: str, message: str):
        """Show final verdict from vision analysis."""
        verdict_colors = {
            "CONFIRMED": GREEN,
            "PARTIAL": ORANGE,
            "WEAK": RED,
            "FAILED": RED,
            "ERROR": RED,
            "TEXT_ONLY": ORANGE,
        }
        
        color = verdict_colors.get(verdict, GRAY)
        self.verdict_label.setText(f"[WAIT] {message}")
        self.verdict_label.setStyleSheet(f"color: {color};")
        self.verdict_box.setStyleSheet(f"""
            background: rgba({', '.join(str(int(color[i:i+2], 16)) for i in (1, 3, 5))}, 0.1);
            border: 2px solid {color};
            border-radius: 8px;
            padding: 10px;
        """)

    def _on_approve(self):
        """User confirmed trade with vision analysis."""
        self.signal_data["vision_confirmed"] = True
        self.signal_data["vision_result"] = self.vision_result
        self.confirmed.emit(self.signal_data)
        self.accept()

    def _on_skip_vision(self):
        """User skipped vision analysis but still approving."""
        self.signal_data["vision_confirmed"] = False
        self.signal_data["vision_skipped"] = True
        self.confirmed.emit(self.signal_data)
        self.accept()

    def _on_reject(self):
        """User rejected trade."""
        self.rejected.emit(self.signal_data)
        self.reject()


class VisionTestDialog(QDialog):
    """Simple dialog to preview a captured chart screenshot for sanity-checking."""

    def __init__(self, image, parent=None):
        super().__init__(parent)
        self._image = image
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Vision Test - Screenshot Preview")
        self.setModal(False)
        self.setStyleSheet(f"background: {BG_DARK}; color: {WHITE};")
        self.resize(720, 560)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel("[CAMERA] Chart Screenshot Capture Test")
        title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {CYAN};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self._img_label = QLabel()
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.setStyleSheet(f"""
            background: {BG_PANEL}; border: 1px solid {BORDER}; border-radius: 6px;
            padding: 6px;
        """)
        self._render_image()
        layout.addWidget(self._img_label)

        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(34)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: {BG_PANEL}; color: {WHITE}; border: 1px solid {BORDER};
                           border-radius: 6px; padding: 0 20px; }}
            QPushButton:hover {{ border-color: {CYAN}; color: {CYAN}; }}
        """)
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignCenter)

    def _render_image(self):
        """Convert PIL Image to QPixmap and display it."""
        try:
            import io
            buf = io.BytesIO()
            self._image.save(buf, format="PNG")
            buf.seek(0)
            pixmap = QPixmap()
            pixmap.loadFromData(buf.read())
            scaled = pixmap.scaled(
                680, 480,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_label.setPixmap(scaled)
        except Exception as exc:
            self._img_label.setText(f"Could not render image: {exc}")

