"""
VcanTrade AI - Visual Chart Confirmation (Stage 2)

Uses VLM (Vision Language Model) to "read" the chart every 60 seconds.
Verifies that candles are moving toward Interest Areas (Demand/Supply zones).
"""

import logging
import time
from typing import Dict, List, Optional, Tuple
from datetime import datetime

import config
from core.vision_engine import VisionCapture
from core.swarm_consensus import call_local_brain

logger = logging.getLogger(__name__)


class VisualChartConfirmation:
    """
    Visual Chart Confirmation System.
    
    Uses Gemma 4 Vision (or LLaVA) to:
    1. Capture chart screenshot every 60 seconds
    2. Read candle positions vs. Interest Areas
    3. Verify price movement toward Demand/Supply zones
    4. Alert if candles approaching zones
    """

    def __init__(self, check_interval: int = 60):
        """
        Initialize visual confirmation system.
        
        Args:
            check_interval: Seconds between chart checks (default 60)
        """
        self.check_interval = check_interval
        self.vision = VisionCapture(
            chart_region=(
                config.CHART_REGION_X,
                config.CHART_REGION_Y,
                config.CHART_REGION_W,
                config.CHART_REGION_H,
            ),
            save_debug=config.SAVE_DEBUG_SCREENSHOTS,
        ) if config.USE_VISION else None
        
        self.last_check = 0
        self.consecutive_failures = 0
        self.zone_approach_history = []  # Track approach patterns
        
        logger.info(f"👁️ Visual Chart Confirmation initialized (Interval: {check_interval}s)")

    def should_check(self) -> bool:
        """Check if it's time for next visual confirmation."""
        current_time = time.time()
        if current_time - self.last_check >= self.check_interval:
            return True
        return False

    def capture_and_analyze(
        self,
        asset: str,
        demand_zones: List[Dict],
        supply_zones: List[Dict],
    ) -> Dict:
        """
        Capture chart and analyze candle position vs. zones.
        
        Args:
            asset: Current trading asset
            demand_zones: List of demand zones to monitor
            supply_zones: List of supply zones to monitor
            
        Returns:
            Analysis result dict
        """
        if not self.vision:
            logger.warning("Vision not enabled, skipping chart confirmation")
            return {"status": "VISION_DISABLED"}
        
        try:
            # Capture chart screenshot
            screenshot = self.vision.capture_chart(asset=asset)
            if not screenshot:
                logger.error("Chart screenshot failed")
                self.consecutive_failures += 1
                return {"status": "CAPTURE_FAILED"}
            
            # Convert to base64
            chart_base64 = screenshot.to_base64()
            
            # Analyze with VLM
            analysis = self._analyze_chart_vlm(
                chart_base64=chart_base64,
                asset=asset,
                demand_zones=demand_zones,
                supply_zones=supply_zones,
            )
            
            # Reset failure counter on success
            self.consecutive_failures = 0
            self.last_check = time.time()
            
            return analysis
            
        except Exception as e:
            logger.error(f"Visual confirmation error: {e}")
            self.consecutive_failures += 1
            return {"status": "ERROR", "error": str(e)}

    def _analyze_chart_vlm(
        self,
        chart_base64: str,
        asset: str,
        demand_zones: List[Dict],
        supply_zones: List[Dict],
    ) -> Dict:
        """
        Use VLM to analyze chart screenshot.
        
        Args:
            chart_base64: Screenshot in base64
            asset: Trading asset
            demand_zones: Active demand zones
            supply_zones: Active supply zones
            
        Returns:
            VLM analysis result
        """
        # Build zone context
        zone_context = ""
        for i, zone in enumerate(demand_zones):
            zone_context += f"- Demand Zone {i+1}: ${zone.get('low', 0):.2f} - ${zone.get('high', 0):.2f}\n"
        for i, zone in enumerate(supply_zones):
            zone_context += f"- Supply Zone {i+1}: ${zone.get('low', 0):.2f} - ${zone.get('high', 0):.2f}\n"
        
        # VLM prompt
        prompt = f"""You are analyzing a TradingView chart screenshot.

ASSET: {asset}

ACTIVE INTEREST AREAS:
{zone_context}

ANALYSIS TASKS:
1. Where is current price relative to the zones above?
2. Are candles moving TOWARD or AWAY from any zone?
3. How far (in % or $) is price from nearest zone?
4. Is price approaching a Demand zone (bullish) or Supply zone (bearish)?
5. What's the current candle pattern? (trending, consolidating, reversing)

Respond with JSON:
{{
  "current_price": 0.00,
  "nearest_zone": "Demand 1 or Supply 2 or NONE",
  "distance_to_zone_percent": 0.0,
  "distance_to_zone_dollars": 0.0,
  "direction": "APPROACHING or AWAY or NEUTRAL",
  "candle_pattern": "trending_up or trending_down or consolidating or reversing",
  "zone_approach_confidence": 0.0-1.0,
  "alert_needed": true or false,
  "reasoning": "Brief explanation"
}}"""

        # Call VLM (using local brain with vision if available)
        try:
            result = call_local_brain(prompt, model=config.VLM_MODEL)
            
            if "error" in result:
                logger.warning(f"VLM analysis failed: {result['error']}")
                return {"status": "VLM_FAILED", "error": result["error"]}
            
            # Parse result
            analysis = {
                "status": "SUCCESS",
                "current_price": result.get("current_price", 0.0),
                "nearest_zone": result.get("nearest_zone", "NONE"),
                "distance_to_zone_percent": result.get("distance_to_zone_percent", 0.0),
                "distance_to_zone_dollars": result.get("distance_to_zone_dollars", 0.0),
                "direction": result.get("direction", "NEUTRAL"),
                "candle_pattern": result.get("candle_pattern", "consolidating"),
                "zone_approach_confidence": result.get("zone_approach_confidence", 0.0),
                "alert_needed": result.get("alert_needed", False),
                "reasoning": result.get("reasoning", ""),
                "timestamp": datetime.now().isoformat()
            }
            
            # Track approach history
            self.zone_approach_history.append(analysis)
            if len(self.zone_approach_history) > 10:
                self.zone_approach_history = self.zone_approach_history[-10:]
            
            logger.info(
                f"👁️ Visual Confirmation: Price ${analysis['current_price']:.2f} | "
                f"Nearest: {analysis['nearest_zone']} | "
                f"Direction: {analysis['direction']}"
            )
            
            return analysis
            
        except Exception as e:
            logger.error(f"VLM analysis error: {e}")
            return {"status": "VLM_ERROR", "error": str(e)}

    def check_zone_approach(self) -> Dict:
        """
        Check if price is approaching any Interest Area.
        
        Returns:
            Dict with approach status
        """
        if not self.zone_approach_history:
            return {"status": "NO_DATA"}
        
        latest = self.zone_approach_history[-1]
        
        if latest.get("alert_needed", False):
            return {
                "status": "ZONE_APPROACHING",
                "asset": latest.get("asset", "UNKNOWN"),
                "zone": latest.get("nearest_zone", "NONE"),
                "distance": latest.get("distance_to_zone_percent", 0.0),
                "confidence": latest.get("zone_approach_confidence", 0.0),
                "reasoning": latest.get("reasoning", ""),
            }
        
        return {"status": "NO_APPROACH"}

    def get_approach_trend(self, last_n: int = 5) -> str:
        """
        Get trend of zone approach over last N checks.
        
        Returns:
            "APPROACHING", "MOVING_AWAY", or "UNCLEAR"
        """
        if len(self.zone_approach_history) < last_n:
            return "INSUFFICIENT_DATA"
        
        recent = self.zone_approach_history[-last_n:]
        
        approaching_count = sum(1 for x in recent if x.get("direction") == "APPROACHING")
        away_count = sum(1 for x in recent if x.get("direction") == "AWAY")
        
        if approaching_count >= last_n * 0.6:
            return "APPROACHING"
        elif away_count >= last_n * 0.6:
            return "MOVING_AWAY"
        else:
            return "UNCLEAR"
