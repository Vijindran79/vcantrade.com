"""
VcanTrade AI - Risk Governor (Stage 3)

Institutional Governor & Risk Architect.
Implements correlation awareness, exposure limits, and portfolio-level risk management.

Features:
1. Asset Correlation Engine (Don't Double Down Rule)
2. Risk Unit Management (Single exposure limit)
3. Multi-Asset Portfolio Analysis
4. Dynamic Position Sizing based on correlation
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import numpy as np

logger = logging.getLogger(__name__)


# Pre-defined correlation matrix for major assets
# Values represent historical correlation coefficients (0.0 to 1.0)
DEFAULT_CORRELATION_MATRIX = {
    # Crypto cluster (highly correlated)
    ("BTC-USD", "ETH-USD"): 0.92,
    ("BTC-USD", "SOL-USD"): 0.88,
    ("BTC-USD", "BNB-USD"): 0.85,
    ("ETH-USD", "SOL-USD"): 0.90,
    ("ETH-USD", "BNB-USD"): 0.87,
    ("SOL-USD", "BNB-USD"): 0.86,
    
    # Forex pairs (moderate correlation)
    ("EURUSD=X", "GBPUSD=X"): 0.78,
    ("EURUSD=X", "AUDUSD=X"): 0.72,
    ("GBPUSD=X", "AUDUSD=X"): 0.75,
    
    # Safe havens (low/negative correlation to risk assets)
    ("GC=F", "BTC-USD"): 0.15,  # Gold vs Bitcoin
    ("GC=F", "SPY"): 0.25,      # Gold vs S&P 500
    ("GC=F", "EURUSD=X"): 0.30, # Gold vs Euro
    
    # Stock indices (high correlation)
    ("SPY", "QQQ"): 0.95,
    ("SPY", "AAPL"): 0.70,
    ("SPY", "NVDA"): 0.72,
    ("QQQ", "AAPL"): 0.75,
    ("QQQ", "NVDA"): 0.78,
}


class RiskUnit:
    """
    Represents a single Risk Unit in the portfolio.
    All positions within a Risk Unit are correlated.
    """
    def __init__(self, unit_id: int, max_exposure_pct: float = 5.0):
        self.unit_id = unit_id
        self.max_exposure_pct = max_exposure_pct  # Max % of account per unit
        self.positions: List[Dict] = []
        self.assets: List[str] = []
        self.total_exposure = 0.0
        self.current_pnl = 0.0
        
    def add_position(self, asset: str, exposure_pct: float, pnl: float = 0.0):
        """Add a position to this risk unit."""
        self.positions.append({
            "asset": asset,
            "exposure_pct": exposure_pct,
            "pnl": pnl,
            "timestamp": datetime.now().isoformat()
        })
        self.assets.append(asset)
        self.total_exposure += exposure_pct
        self.current_pnl += pnl
        
    def can_add(self, exposure_pct: float) -> bool:
        """Check if we can add more exposure to this unit."""
        return (self.total_exposure + exposure_pct) <= self.max_exposure_pct
    
    def get_correlation_score(self) -> float:
        """Get average correlation within this unit."""
        if len(self.assets) < 2:
            return 0.0
        
        total_corr = 0.0
        count = 0
        for i, asset1 in enumerate(self.assets):
            for asset2 in self.assets[i+1:]:
                corr = get_correlation(asset1, asset2)
                total_corr += corr
                count += 1
        
        return total_corr / max(1, count)


class RiskGovernor:
    """
    Institutional Governor & Risk Architect.
    
    Responsibilities:
    1. Check asset correlation before allowing new positions
    2. Limit total exposure to correlated "Risk Units"
    3. Prevent double-downing on same risk
    4. Provide portfolio-level risk metrics
    """

    def __init__(
        self,
        max_risk_units: int = 3,
        max_exposure_per_unit_pct: float = 5.0,
        max_total_exposure_pct: float = 15.0,
        correlation_threshold: float = 0.85,
    ):
        """
        Initialize Risk Governor.
        
        Args:
            max_risk_units: Maximum concurrent risk units (default 3)
            max_exposure_per_unit_pct: Max % per correlated unit (default 5%)
            max_total_exposure_pct: Max total portfolio exposure (default 15%)
            correlation_threshold: Assets correlated above this are grouped (default 0.85)
        """
        self.max_risk_units = max_risk_units
        self.max_exposure_per_unit_pct = max_exposure_per_unit_pct
        self.max_total_exposure_pct = max_total_exposure_pct
        self.correlation_threshold = correlation_threshold
        
        self.risk_units: List[RiskUnit] = []
        self.position_history: List[Dict] = []
        self.correlation_cache: Dict[str, float] = DEFAULT_CORRELATION_MATRIX.copy()
        
        # Statistics
        self.total_signals_processed = 0
        self.total_signals_rejected = 0
        self.total_exposure_current = 0.0
        
        logger.info(
            f"[GOVERN] Risk Governor initialized: "
            f"Max Units={max_risk_units}, "
            f"Max/Unit={max_exposure_per_unit_pct}%, "
            f"Corr Threshold={correlation_threshold}"
        )

    def evaluate_signal(
        self,
        new_signal: Dict,
        existing_positions: List[Dict] = None,
    ) -> Dict:
        """
        Evaluate whether to allow a new trade signal based on correlation & exposure.
        
        Args:
            new_signal: Dict with asset, action, exposure_pct, confidence
            existing_positions: Current open positions
            
        Returns:
            Decision dict with verdict, reasoning, adjustments
        """
        self.total_signals_processed += 1
        
        new_asset = new_signal.get("asset", "UNKNOWN")
        new_exposure_pct = new_signal.get("exposure_pct", 1.0)
        new_action = new_signal.get("action", "HOLD")
        
        # Skip if HOLD
        if new_action == "HOLD":
            return {
                "verdict": "ALLOW",
                "reason": "HOLD signal, no action needed",
                "risk_unit_id": None
            }
        
        # Check total exposure limit
        total_exposure = self._calculate_total_exposure(existing_positions)
        if (total_exposure + new_exposure_pct) > self.max_total_exposure_pct:
            self.total_signals_rejected += 1
            return {
                "verdict": "REJECT",
                "reason": f"Total exposure would exceed {self.max_total_exposure_pct}% "
                         f"(Current: {total_exposure:.1f}%, New: {new_exposure_pct:.1f}%)",
                "adjustment": f"Reduce exposure to {self.max_total_exposure_pct - total_exposure:.1f}%",
                "risk_unit_id": None
            }
        
        # Find correlated risk unit
        matching_unit, highest_corr = self._find_correlated_unit(
            new_asset, existing_positions
        )
        
        if highest_corr >= self.correlation_threshold:
            # HIGH CORRELATION DETECTED
            logger.warning(
                f"[WARN] High correlation detected: {new_asset} vs "
                f"{matching_unit.assets} (corr: {highest_corr:.2f})"
            )
            
            if matching_unit.can_add(new_exposure_pct):
                # Can add to existing unit (within limits)
                matching_unit.add_position(new_asset, new_exposure_pct)
                return {
                    "verdict": "ALLOW_WITH_WARNING",
                    "reason": f"High correlation ({highest_corr:.2f}) with existing positions "
                             f"in Risk Unit {matching_unit.unit_id}",
                    "adjustment": f"Exposure added to existing unit. "
                                 f"Unit total: {matching_unit.total_exposure:.1f}%",
                    "risk_unit_id": matching_unit.unit_id,
                    "correlation_warning": True,
                    "correlated_assets": matching_unit.assets
                }
            else:
                # Would exceed unit limit - REJECT
                self.total_signals_rejected += 1
                return {
                    "verdict": "REJECT",
                    "reason": f"Don't Double Down Rule: {new_asset} is {highest_corr:.0%} correlated "
                             f"with Risk Unit {matching_unit.unit_id} "
                             f"(Assets: {', '.join(matching_unit.assets)}). "
                             f"Unit already at {matching_unit.total_exposure:.1f}% exposure.",
                    "adjustment": "Pick the strongest signal from this cluster, or wait for unit to clear",
                    "risk_unit_id": None,
                    "correlation_warning": True,
                    "correlated_assets": matching_unit.assets
                }
        else:
            # LOW CORRELATION - Create new risk unit or add to uncategorized
            new_unit = RiskUnit(
                unit_id=len(self.risk_units) + 1,
                max_exposure_pct=self.max_exposure_per_unit_pct
            )
            new_unit.add_position(new_asset, new_exposure_pct)
            self.risk_units.append(new_unit)
            
            return {
                "verdict": "ALLOW",
                "reason": f"Low correlation ({highest_corr:.2f}) with existing positions. "
                         f"New Risk Unit {new_unit.unit_id} created.",
                "risk_unit_id": new_unit.unit_id
            }

    def _find_correlated_unit(
        self,
        new_asset: str,
        existing_positions: List[Dict] = None,
    ) -> Tuple[Optional[RiskUnit], float]:
        """
        Find which risk unit has highest correlation with new asset.
        
        Returns:
            Tuple of (matching_unit, highest_correlation)
        """
        highest_corr = 0.0
        matching_unit = None
        
        # Check against existing risk units
        for unit in self.risk_units:
            for unit_asset in unit.assets:
                corr = get_correlation(new_asset, unit_asset, self.correlation_cache)
                if corr > highest_corr:
                    highest_corr = corr
                    matching_unit = unit
        
        # Also check against existing positions directly
        if existing_positions:
            for pos in existing_positions:
                pos_asset = pos.get("asset", "")
                corr = get_correlation(new_asset, pos_asset, self.correlation_cache)
                if corr > highest_corr:
                    highest_corr = corr
                    # Find which unit this position belongs to
                    for unit in self.risk_units:
                        if pos_asset in unit.assets:
                            matching_unit = unit
                            break
        
        return matching_unit, highest_corr

    def _calculate_total_exposure(self, existing_positions: List[Dict] = None) -> float:
        """Calculate total portfolio exposure."""
        if not existing_positions:
            return sum(unit.total_exposure for unit in self.risk_units)
        
        total = 0.0
        for pos in existing_positions:
            total += pos.get("exposure_pct", 0.0)
        
        return total

    def update_position_pnl(self, asset: str, pnl: float):
        """Update PnL for a position."""
        for unit in self.risk_units:
            for pos in unit.positions:
                if pos["asset"] == asset:
                    pos["pnl"] = pnl
                    unit.current_pnl += pnl
                    break

    def close_position(self, asset: str):
        """Remove a closed position from risk tracking."""
        for unit in self.risk_units:
            unit.positions = [p for p in unit.positions if p["asset"] != asset]
            unit.assets = [a for a in unit.assets if a != asset]
            unit.total_exposure = sum(p["exposure_pct"] for p in unit.positions)

    def get_portfolio_summary(self) -> Dict:
        """Get full portfolio risk summary."""
        total_exposure = sum(unit.total_exposure for unit in self.risk_units)
        total_pnl = sum(unit.current_pnl for unit in self.risk_units)
        avg_correlation = np.mean([
            unit.get_correlation_score() 
            for unit in self.risk_units 
            if len(unit.assets) >= 2
        ]) if self.risk_units else 0.0
        
        return {
            "total_exposure_pct": total_exposure,
            "remaining_exposure_pct": self.max_total_exposure_pct - total_exposure,
            "total_pnl": total_pnl,
            "active_risk_units": len(self.risk_units),
            "max_risk_units": self.max_risk_units,
            "avg_correlation": avg_correlation,
            "signals_processed": self.total_signals_processed,
            "signals_rejected": self.total_signals_rejected,
            "rejection_rate": (
                self.total_signals_rejected / max(1, self.total_signals_processed)
            ),
            "risk_units_detail": [
                {
                    "unit_id": unit.unit_id,
                    "assets": unit.assets,
                    "total_exposure": unit.total_exposure,
                    "pnl": unit.current_pnl,
                    "avg_correlation": unit.get_correlation_score()
                }
                for unit in self.risk_units
            ]
        }

    def validate_bracket_risk_symmetry(self, entry: float, sl: float, tp: float) -> bool:
        """Enforces an institutional minimum 1:1.5 Risk-to-Reward profile gate."""
        try:
            risk_distance = abs(entry - sl)
            reward_distance = abs(tp - entry)
            if risk_distance == 0: return False
            
            rr_ratio = reward_distance / risk_distance
            if rr_ratio < 1.5:
                logger.warning(f"[RISK-REJECT] Asymmetric profile detected: {round(rr_ratio, 2)}x below 1.5x minimum baseline.")
                return False
                
            logger.info(f"[RISK-PASS] Parameter symmetry checked clean at {round(rr_ratio, 2)}x.")
            return True
        except Exception as e:
            logger.error(f"[RISK-ERR] Failed to compute risk matrix boundaries: {str(e)}")
            return False
    
    def get_strongest_signal(self, signals: List[Dict]) -> Optional[Dict]:
        """
        From multiple correlated signals, pick the strongest one.
        This is the "Don't Double Down" enforcer.
        
        Args:
            signals: List of trade signals
            
        Returns:
            Single strongest signal (or None)
        """
        if not signals:
            return None
        
        # Group by correlation cluster
        clusters = []
        for signal in signals:
            asset = signal.get("asset", "")
            placed = False
            
            for cluster in clusters:
                # Check if this asset correlates with cluster
                max_corr = 0.0
                for cluster_asset in cluster["assets"]:
                    corr = get_correlation(asset, cluster_asset, self.correlation_cache)
                    max_corr = max(max_corr, corr)
                
                if max_corr >= self.correlation_threshold:
                    cluster["signals"].append(signal)
                    cluster["assets"].append(asset)
                    placed = True
                    break
            
            if not placed:
                clusters.append({
                    "assets": [asset],
                    "signals": [signal]
                })
        
        # Pick strongest signal from each cluster (by confidence)
        best_signals = []
        for cluster in clusters:
            cluster_signals = cluster["signals"]
            # Sort by confidence (assuming confidence is in signal)
            cluster_signals.sort(
                key=lambda s: self._confidence_to_float(s.get("confidence", "LOW")),
                reverse=True
            )
            best_signals.append(cluster_signals[0])
        
        # Return overall strongest
        best_signals.sort(
            key=lambda s: self._confidence_to_float(s.get("confidence", "LOW")),
            reverse=True
        )
        
        return best_signals[0] if best_signals else None
    
    def _confidence_to_float(self, confidence: str) -> float:
        """Convert confidence string to numeric."""
        mapping = {
            "LOW": 0.3,
            "MEDIUM": 0.5,
            "HIGH": 0.7,
            "VERY_HIGH": 0.9
        }
        return mapping.get(confidence.upper(), 0.5)


def get_correlation(asset1: str, asset2: str, cache: Dict = None) -> float:
    """
    Get correlation between two assets.
    
    Args:
        asset1: First asset symbol
        asset2: Second asset symbol
        cache: Correlation cache dict
        
    Returns:
        Correlation coefficient (0.0 to 1.0)
    """
    if asset1 == asset2:
        return 1.0
    
    # Try both orderings
    key1 = (asset1, asset2)
    key2 = (asset2, asset1)
    
    cache_source = cache or DEFAULT_CORRELATION_MATRIX
    
    if key1 in cache_source:
        return cache_source[key1]
    if key2 in cache_source:
        return cache_source[key2]
    
    # Default: moderate correlation for unknown pairs
    return 0.5


# Asset cluster definitions for quick lookup
ASSET_CLUSTERS = {
    "CRYPTO": ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "ADA-USD", "DOGE-USD"],
    "FOREX_MAJOR": ["EURUSD=X", "GBPUSD=X", "AUDUSD=X", "NZDUSD=X"],
    "FOREX_JPY": ["USDJPY=X", "EURJPY=X", "GBPJPY=X"],
    "US_STOCKS": ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "MSFT", "AMZN"],
    "COMMODITIES": ["GC=F", "CL=F", "SI=F"],  # Gold, Oil, Silver
    "SAFE_HAVEN": ["GC=F", "TLT", "USD-Index"],  # Gold, Bonds, Dollar
}


def check_cluster_conflict(asset: str, cluster_name: str) -> bool:
    """Check if asset belongs to a specific cluster."""
    cluster = ASSET_CLUSTERS.get(cluster_name, [])
    return asset.upper() in [a.upper() for a in cluster]
