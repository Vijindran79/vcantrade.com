"""
VcanTrade AI - Code Architect (Stage 2)

Generates Pine Script (v6) and MQL5 code based on multi-timeframe analysis.
Plots "Institutional Demand" and "Retail Supply" zones on TradingView charts.
Autonomous adaptation via BrowserAgent (Pine Editor integration).
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class CodeArchitect:
    """
    AI Code Generator - Translates trading analysis into Pine Script/MQL5 code.

    Responsibilities:
    1. Generate Pine Script v6 code for custom indicators
    2. Generate MQL5 code for MetaTrader integration
    3. Plot Institutional Demand/Retail Supply zones
    4. Auto-adapt via BrowserAgent (paste code into Pine Editor)
    5. Detect Liquidity Sweeps (long wicks into demand zones)
    """

    def __init__(self):
        self.generated_scripts = {}  # Store generated scripts
        self.last_generation = None
        self.liquidity_sweep_alerts = []  # Store recent sweep detections

        logger.info("[EMOJI] Code Architect initialized")

    def detect_liquidity_sweep(
        self,
        candle: Dict,
        demand_zones: List[Dict],
        supply_zones: List[Dict],
        rsi_value: float = None,
        rsi_divergence: bool = False,
    ) -> Optional[Dict]:
        """
        Detect Liquidity Sweep patterns.

        A liquidity sweep occurs when price wicks into a demand/supply zone
        and quickly reverses, indicating institutional manipulation.

        Args:
            candle: OHLCV candle data {"open", "high", "low", "close", "volume"}
            demand_zones: List of demand zones [{"low", "high", "strength"}]
            supply_zones: List of supply zones [{"low", "high", "strength"}]
            rsi_value: Current RSI value (optional)
            rsi_divergence: Whether RSI divergence is present

        Returns:
            Sweep detection result or None
        """
        if not candle:
            return None

        high = candle.get("high", 0)
        low = candle.get("low", 0)
        close = candle.get("close", 0)
        open_price = candle.get("open", 0)

        # Calculate wick sizes
        upper_wick = high - max(open_price, close)
        lower_wick = min(open_price, close) - low
        body_size = abs(close - open_price)

        # A long wick is at least 2x the body size
        is_long_upper_wick = upper_wick > (body_size * 2) if body_size > 0 else upper_wick > 0.001
        is_long_lower_wick = lower_wick > (body_size * 2) if body_size > 0 else lower_wick > 0.001

        if not (is_long_upper_wick or is_long_lower_wick):
            return None

        # Check if wick sweeps into a zone
        swept_zone = None
        sweep_type = None

        # Check supply zone sweep (upper wick)
        if is_long_upper_wick:
            for zone in supply_zones:
                zone_low = zone.get("low", 0)
                zone_high = zone.get("high", 0)
                if zone_low <= high <= zone_high:
                    swept_zone = zone
                    sweep_type = "SUPPLY_SWEEP"
                    break

        # Check demand zone sweep (lower wick)
        if is_long_lower_wick and not swept_zone:
            for zone in demand_zones:
                zone_low = zone.get("low", 0)
                zone_high = zone.get("high", 0)
                if zone_low <= low <= zone_high:
                    swept_zone = zone
                    sweep_type = "DEMAND_SWEEP"
                    break

        if not swept_zone:
            return None

        # Determine signal direction
        # Supply sweep + long upper wick = BEARISH (rejection from supply)
        # Demand sweep + long lower wick = BULLISH (rejection from demand)
        signal_direction = "BEARISH" if sweep_type == "SUPPLY_SWEEP" else "BULLISH"

        # Calculate conviction score
        conviction = 0.5  # Base conviction

        # Increase conviction for strong wicks
        wick_to_body_ratio = (upper_wick if is_long_upper_wick else lower_wick) / max(body_size, 0.001)
        conviction += min(0.2, wick_to_body_ratio * 0.1)

        # Increase conviction if RSI divergence present
        if rsi_divergence:
            conviction += 0.15
            sweep_type = "ALPHA_TRADE"  # High conviction alpha trade

        # Check RSI overbought/oversold for extra conviction
        if rsi_value is not None:
            if signal_direction == "BEARISH" and rsi_value > 70:
                conviction += 0.1  # Overbought confirmation
            elif signal_direction == "BULLISH" and rsi_value < 30:
                conviction += 0.1  # Oversold confirmation

        conviction = min(1.0, conviction)

        sweep_detection = {
            "type": sweep_type,
            "direction": signal_direction,
            "swept_zone": swept_zone,
            "wick_size": upper_wick if is_long_upper_wick else lower_wick,
            "body_size": body_size,
            "wick_to_body_ratio": wick_to_body_ratio,
            "conviction": conviction,
            "rsi_divergence": rsi_divergence,
            "rsi_value": rsi_value,
            "timestamp": datetime.now().isoformat()
        }

        # Store alert
        self.liquidity_sweep_alerts.append(sweep_detection)

        # Keep only last 50 alerts
        if len(self.liquidity_sweep_alerts) > 50:
            self.liquidity_sweep_alerts = self.liquidity_sweep_alerts[-50:]

        logger.info(
            f"[TARGET] Liquidity Sweep Detected: {sweep_type} | "
            f"Direction: {signal_direction} | Conviction: {conviction:.2f} | "
            f"{'ALPHA TRADE' if rsi_divergence else 'Normal'}"
        )

        return sweep_detection

    def generate_pine_script_zones(
        self,
        asset: str,
        demand_zones: List[Dict],
        supply_zones: List[Dict],
        timeframe: str = "2H",
        include_labels: bool = True,
    ) -> str:
        """
        Generate Pine Script v6 code that plots Institutional Demand and Retail Supply zones.
        
        Args:
            asset: Trading pair (e.g., "BTCUSD")
            demand_zones: List of {"low": float, "high": float, "strength": float}
            supply_zones: List of {"low": float, "high": float, "strength": float}
            timeframe: Current analysis timeframe
            include_labels: Include text labels on chart
            
        Returns:
            Complete Pine Script v6 code
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Build zone arrays
        demand_lines = ""
        for i, zone in enumerate(demand_zones):
            strength_color = "#3FB950" if zone.get("strength", 0.5) > 0.7 else "#D29922"
            demand_lines += f"""
// Institutional Demand Zone {i+1}
demandTop_{i} = {zone.get('high', 0):.2f}
demandBot_{i} = {zone.get('low', 0):.2f}
box.new(bar_index[100], demandTop_{i}, bar_index, demandBot_{i}, 
    border_color=color.new({strength_color}, 0), 
    bgcolor=color.new({strength_color}, 85), 
    text="Demand {i+1}\\nStrength: {zone.get('strength', 0):.2f}")
"""
        
        supply_lines = ""
        for i, zone in enumerate(supply_zones):
            strength_color = "#F85149" if zone.get("strength", 0.5) > 0.7 else "#D29922"
            supply_lines += f"""
// Retail Supply Zone {i+1}
supplyTop_{i} = {zone.get('high', 0):.2f}
supplyBot_{i} = {zone.get('low', 0):.2f}
box.new(bar_index[100], supplyTop_{i}, bar_index, supplyBot_{i}, 
    border_color=color.new({strength_color}, 0), 
    bgcolor=color.new({strength_color}, 85), 
    text="Supply {i+1}\\nStrength: {zone.get('strength', 0):.2f}")
"""
        
        labels_code = ""
        if include_labels:
            labels_code = f"""
// AI Analysis Labels
var label ai_header = label.new(bar_index, high, 
    text="[ROBOT] AI Co-Pilot Analysis\\nTimeframe: {timeframe}\\nGenerated: {timestamp}\\nAsset: {asset}", 
    color=color.new(#000000, 80), 
    style=label.style_label_left, 
    textcolor=#00D4FF, 
    size=size.small)
label.set_xy(ai_header, bar_index, high)
"""
        
        # Complete Pine Script v6
        pine_script = f"""//@version=6
indicator("[ROBOT] AI Co-Pilot - Institutional Zones [{timeframe}]", overlay=true, max_boxes_count=500)

// ============================================================
// AI-Generated Institutional Demand & Retail Supply Zones
// Generated: {timestamp}
// Asset: {asset}
// Timeframe: {timeframe}
// Strict Boss Protocol: ACTIVE
// ============================================================

// --- INSTITUTIONAL DEMAND ZONES (Buy Areas) ---
{demand_lines}

// --- RETAIL SUPPLY ZONES (Sell Areas) ---
{supply_lines}

// --- AI LABELS ---
{labels_code}

// --- ALERT CONDITIONS ---
alertcondition(close > demandTop_0 and close[1] <= demandTop_0, 
    title="Entered Demand Zone", 
    message="Price entered Institutional Demand Zone")
alertcondition(close < supplyBot_0 and close[1] >= supplyBot_0, 
    title="Entered Supply Zone", 
    message="Price entered Retail Supply Zone")

// ============================================================
// END OF AI-GENERATED SCRIPT
// ============================================================
"""
        
        # Store generation
        script_id = f"{asset}_{timeframe}_{int(datetime.now().timestamp())}"
        self.generated_scripts[script_id] = {
            "code": pine_script,
            "asset": asset,
            "timeframe": timeframe,
            "timestamp": timestamp,
            "type": "pine_script_v6"
        }
        self.last_generation = script_id
        
        logger.info(f"[OK] Pine Script generated: {script_id}")
        return pine_script

    def generate_mql5_zones(
        self,
        asset: str,
        demand_zones: List[Dict],
        supply_zones: List[Dict],
        timeframe: str = "H2",
    ) -> str:
        """
        Generate MQL5 code that draws zones on MetaTrader charts.
        
        Args:
            asset: Trading pair
            demand_zones: List of zone data
            supply_zones: List of zone data
            timeframe: MQL5 timeframe string
            
        Returns:
            Complete MQL5 indicator code
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Build zone drawing code
        demand_draw = ""
        for i, zone in enumerate(demand_zones):
            demand_draw += f"""
   // Demand Zone {i+1}
   double demand_top_{i} = {zone.get('high', 0):.5f};
   double demand_bot_{i} = {zone.get('low', 0):.5f};
   ObjectCreate(0, "Demand_{i}", OBJ_RECTANGLE, 0, 
       TimeCurrent() - 100*PeriodSeconds(PERIOD_{timeframe}), demand_top_{i},
       TimeCurrent(), demand_bot_{i});
   ObjectSetInteger(0, "Demand_{i}", OBJPROP_COLOR, clrGreen);
   ObjectSetInteger(0, "Demand_{i}", OBJPROP_BACK, true);
   ObjectSetInteger(0, "Demand_{i}", OBJPROP_STYLE, STYLE_SOLID);
"""
        
        supply_draw = ""
        for i, zone in enumerate(supply_zones):
            supply_draw += f"""
   // Supply Zone {i+1}
   double supply_top_{i} = {zone.get('high', 0):.5f};
   double supply_bot_{i} = {zone.get('low', 0):.5f};
   ObjectCreate(0, "Supply_{i}", OBJ_RECTANGLE, 0, 
       TimeCurrent() - 100*PeriodSeconds(PERIOD_{timeframe}), supply_top_{i},
       TimeCurrent(), supply_bot_{i});
   ObjectSetInteger(0, "Supply_{i}", OBJPROP_COLOR, clrRed);
   ObjectSetInteger(0, "Supply_{i}", OBJPROP_BACK, true);
   ObjectSetInteger(0, "Supply_{i}", OBJPROP_STYLE, STYLE_SOLID);
"""
        
        mql5_code = f"""//+------------------------------------------------------------------+
//|                                     AI_Copilot_Zones.mq5         |
//|                                   Generated: {timestamp} |
//|                                   Asset: {asset}                 |
//+------------------------------------------------------------------+
#property copyright "AI Co-Pilot - Strict Boss Protocol"
#property version   "1.00"
#property indicator_chart_window
#property indicator_buffers 0

//+------------------------------------------------------------------+
//| Custom indicator initialization function                         |
//+------------------------------------------------------------------+
int OnInit()
  {{
   // --- indicator short name
   IndicatorSetString(INDICATOR_SHORTNAME, "[ROBOT] AI Zones [{timeframe}]");
   
   // --- Delete old objects
   ObjectsDeleteAll(0, "Demand_");
   ObjectsDeleteAll(0, "Supply_");
   
   return(INIT_SUCCEEDED);
  }}

//+------------------------------------------------------------------+
//| Custom indicator iteration function                              |
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
  {{
{demand_draw}
{supply_draw}

   return(rates_total);
  }}
//+------------------------------------------------------------------+
"""
        
        # Store generation
        script_id = f"{asset}_{timeframe}_mql5_{int(datetime.now().timestamp())}"
        self.generated_scripts[script_id] = {
            "code": mql5_code,
            "asset": asset,
            "timeframe": timeframe,
            "timestamp": timestamp,
            "type": "mql5"
        }
        self.last_generation = script_id
        
        logger.info(f"[OK] MQL5 code generated: {script_id}")
        return mql5_code

    def generate_multi_timeframe_analysis(
        self,
        asset: str,
        analysis: Dict[str, Dict],
        primary_timeframe: str = "2H",
    ) -> str:
        """
        Generate Pine Script that shows analysis from multiple timeframes (2H, 3H, 4H, 1D).
        
        Args:
            asset: Trading pair
            analysis: Dict with timeframe as key, zone data as value
            primary_timeframe: Main timeframe to display
            
        Returns:
            Multi-timeframe Pine Script
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Build multi-TF zone code
        all_zones = ""
        for tf, zones in analysis.items():
            demand = zones.get("demand_zones", [])
            supply = zones.get("supply_zones", [])
            
            for i, zone in enumerate(demand):
                all_zones += f"""
// {tf} Demand Zone {i+1}
tf_{tf.replace('H', 'h').replace('D', 'd')}_dem_{i}_top = {zone.get('high', 0):.2f}
tf_{tf.replace('H', 'h').replace('D', 'd')}_dem_{i}_bot = {zone.get('low', 0):.2f}
box.new(bar_index[100], tf_{tf.replace('H', 'h').replace('D', 'd')}_dem_{i}_top, 
    bar_index, tf_{tf.replace('H', 'h').replace('D', 'd')}_dem_{i}_bot, 
    border_color=color.new(#3FB950, 0), 
    bgcolor=color.new(#3FB950, 90), 
    text="{tf} Dem {i+1}")
"""
            
            for i, zone in enumerate(supply):
                all_zones += f"""
// {tf} Supply Zone {i+1}
tf_{tf.replace('H', 'h').replace('D', 'd')}_sup_{i}_top = {zone.get('high', 0):.2f}
tf_{tf.replace('H', 'h').replace('D', 'd')}_sup_{i}_bot = {zone.get('low', 0):.2f}
box.new(bar_index[100], tf_{tf.replace('H', 'h').replace('D', 'd')}_sup_{i}_top, 
    bar_index, tf_{tf.replace('H', 'h').replace('D', 'd')}_sup_{i}_bot, 
    border_color=color.new(#F85149, 0), 
    bgcolor=color.new(#F85149, 90), 
    text="{tf} Sup {i+1}")
"""
        
        pine_script = f"""//@version=6
indicator("[ROBOT] AI Multi-TF Analysis [{primary_timeframe}]", overlay=true, max_boxes_count=500)

// ============================================================
// Multi-Timeframe Institutional Zones
// Generated: {timestamp}
// Asset: {asset}
// Timeframes: {', '.join(analysis.keys())}
// ============================================================

{all_zones}

// Multi-TF Summary Label
var label mt_summary = label.new(bar_index, high, 
    text="[ROBOT] Multi-TF Analysis\\n" + 
    "Primary: {primary_timeframe}\\n" + 
    "Analyzed: {', '.join(analysis.keys())}\\n" +
    "Status: Strict Boss ACTIVE", 
    color=color.new(#000000, 80), 
    style=label.style_label_left, 
    textcolor=#00D4FF, 
    size=size.small)
label.set_xy(mt_summary, bar_index, high)
"""
        
        script_id = f"{asset}_multiTF_{int(datetime.now().timestamp())}"
        self.generated_scripts[script_id] = {
            "code": pine_script,
            "asset": asset,
            "timeframe": "multi",
            "timestamp": timestamp,
            "type": "pine_script_v6_multi_tf"
        }
        self.last_generation = script_id
        
        logger.info(f"[OK] Multi-timeframe script generated: {script_id}")
        return pine_script

    def get_last_script(self) -> Optional[Dict]:
        """Get the last generated script."""
        if self.last_generation:
            return self.generated_scripts.get(self.last_generation)
        return None
