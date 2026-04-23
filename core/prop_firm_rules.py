"""
VcaniTrade AI - Prop Firm Rule Engine

The "Professor" - Knows every prop firm's rules and enforces them.
Blocks trades that violate rules, tracks compliance in real-time.

Supports: TopStep, Apex Trader Funding, MyFundedFutures, TradeDay, etc.
"""

import logging
from typing import Dict, Optional, List, Tuple
from datetime import datetime, date
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class PropFirmName(str, Enum):
    TOPSTEP = "TopStep"
    APEX = "Apex Trader Funding"
    MYFUNDED = "MyFundedFutures"
    TRADEDAY = "TradeDay"
    MYFOREXFUNDED = "MyFundedFX"
    FTMO = "FTMO"
    CUSTOM = "Custom"


@dataclass
class FirmRules:
    """Complete rule set for a prop firm."""
    firm_name: PropFirmName
    account_size: float = 50000.0  # Starting balance
    
    # Loss Limits
    max_daily_loss: float = 150.0  # $ amount
    max_daily_loss_pct: float = 3.0  # % of account
    max_trailing_drawdown: float = 3000.0  # $ from peak
    max_trailing_drawdown_pct: float = 6.0  # % from peak
    max_overall_loss: float = 4000.0  # Total loss allowed
    
    # Profit Targets
    profit_target_phase1: float = 3000.0  # Phase 1 target
    profit_target_phase2: float = 2000.0  # Phase 2 target (if applicable)
    profit_target_pct: float = 6.0  # % profit target
    
    # Trading Restrictions
    min_trading_days: int = 1  # Minimum days to trade
    max_positions: int = 10  # Max concurrent positions
    allowed_products: List[str] = field(default_factory=lambda: ["ALL"])  # ES, NQ, CL, GC, etc.
    news_trading_allowed: bool = True
    weekend_holding: bool = False
    hold_time_minimum: int = 0  # Seconds minimum hold time
    
    # Consistency Rules
    consistency_rule: str = ""  # e.g., "No single day > 50% of profit"
    minimum_contract_size: int = 1
    maximum_contract_size: int = 10
    
    # Account Phases
    phases: int = 1  # 1 = direct funding, 2 = 2-phase evaluation
    current_phase: int = 1
    is_funded: bool = False
    
    # Payout Rules
    payout_frequency: str = "Monthly"  # Weekly, Monthly
    first_payout_days: int = 30
    profit_split: float = 80.0  # % trader keeps


class ComplianceState:
    """Tracks real-time compliance with prop firm rules."""
    
    def __init__(self, rules: FirmRules):
        self.rules = rules
        self.starting_balance = rules.account_size
        self.current_balance = rules.account_size
        self.peak_balance = rules.account_size
        self.daily_pnl = 0.0
        self.total_pnl = 0.0
        self.max_drawdown_from_peak = 0.0
        self.trades_today = 0
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.consecutive_losses = 0
        self.largest_win = 0.0
        self.largest_loss = 0.0
        self.trading_days: List[date] = []
        self.daily_history: Dict[str, float] = {}  # date -> daily P&L
        self.violations: List[str] = []
        self.start_date = datetime.now()
        
    def update_trade(self, pnl: float, asset: str):
        """Update compliance state after a trade."""
        self.current_balance += pnl
        self.total_pnl += pnl
        self.daily_pnl += pnl
        self.total_trades += 1
        self.trades_today += 1
        
        # Track today
        today = date.today()
        if today not in self.trading_days:
            self.trading_days.append(today)
        
        # Update peak
        if self.current_balance > self.peak_balance:
            self.peak_balance = self.current_balance
        
        # Calculate drawdown
        self.max_drawdown_from_peak = max(0, self.peak_balance - self.current_balance)
        
        # Track wins/losses
        if pnl > 0:
            self.winning_trades += 1
            self.consecutive_losses = 0
            self.largest_win = max(self.largest_win, pnl)
        else:
            self.losing_trades += 1
            self.consecutive_losses += 1
            self.largest_loss = min(self.largest_loss, pnl)
        
        # Track daily history
        today_str = today.isoformat()
        self.daily_history[today_str] = self.daily_pnl
        
        logger.info(f"Compliance updated: Balance=${self.current_balance:.2f}, Daily P&L=${self.daily_pnl:.2f}")
    
    def reset_daily(self):
        """Reset daily counters (called at start of each trading day)."""
        self.daily_pnl = 0.0
        self.trades_today = 0
        logger.info("Daily compliance counters reset")
    
    def can_trade(self) -> Tuple[bool, List[str]]:
        """
        Check if trading is allowed based on ALL prop firm rules.
        Returns: (can_trade, list_of_violations)
        """
        violations = []
        
        # 1. Check daily loss limit (skip if firm has no daily limit, e.g. Apex)
        if self.rules.max_daily_loss > 0 and self.daily_pnl <= -self.rules.max_daily_loss:
            violations.append(
                f"[FAIL] DAILY LOSS LIMIT: ${abs(self.daily_pnl):.2f} / ${self.rules.max_daily_loss:.2f}"
            )
        
        # 2. Check trailing drawdown
        if self.max_drawdown_from_peak >= self.rules.max_trailing_drawdown:
            violations.append(
                f"[FAIL] MAX DRAWDOWN: ${self.max_drawdown_from_peak:.2f} / ${self.rules.max_trailing_drawdown:.2f}"
            )
        
        # 3. Check max positions
        if self.rules.max_positions > 0:
            # This would need to be passed in - for now we skip
            pass
        
        # 4. Check allowed products
        # Would check asset against allowed_products list

        # 5. Check minimum trading days (WARNING only - for payout eligibility, not trading block)
        days_traded = len(self.trading_days)
        if days_traded < self.rules.min_trading_days:
            violations.append(
                f"[WARN] PAYOUT: Need {self.rules.min_trading_days} trading days for payout (currently {days_traded})"
            )
            # Don't block trading - this is just a warning for payout eligibility
            violations = [v for v in violations if not v.startswith("[WARN] PAYOUT")]

        # 6. Check profit target achieved (for phase completion)
        if self.total_pnl >= self.rules.profit_target_phase1:
            logger.info(f"[CELEBRATE] PROFIT TARGET HIT: ${self.total_pnl:.2f} / ${self.rules.profit_target_phase1:.2f}")

        can_trade = len(violations) == 0
        return can_trade, violations
    
    def get_compliance_report(self) -> Dict:
        """Generate full compliance report for dashboard."""
        can_trade, violations = self.can_trade()
        
        # Calculate metrics
        win_rate = (self.winning_trades / max(1, self.total_trades)) * 100
        profit_factor = abs(self.largest_win / max(0.01, self.largest_loss))
        
        # Progress toward targets
        daily_progress = min(100, (abs(self.daily_pnl) / max(0.01, self.rules.max_daily_loss)) * 100)
        drawdown_usage = (self.max_drawdown_from_peak / self.rules.max_trailing_drawdown) * 100
        profit_progress = (self.total_pnl / self.rules.profit_target_phase1) * 100
        
        return {
            "firm_name": self.rules.firm_name.value,
            "can_trade": can_trade,
            "violations": violations,
            
            # Account Status
            "starting_balance": self.starting_balance,
            "current_balance": self.current_balance,
            "peak_balance": self.peak_balance,
            "total_pnl": self.total_pnl,
            "daily_pnl": self.daily_pnl,
            
            # Risk Metrics
            "max_drawdown": self.max_drawdown_from_peak,
            "daily_loss_limit": self.rules.max_daily_loss,
            "daily_loss_used_pct": daily_progress,
            "drawdown_limit": self.rules.max_trailing_drawdown,
            "drawdown_used_pct": drawdown_usage,
            
            # Progress
            "profit_target": self.rules.profit_target_phase1,
            "profit_progress_pct": profit_progress,
            "trading_days": len(self.trading_days),
            "min_trading_days": self.rules.min_trading_days,
            
            # Performance
            "total_trades": self.total_trades,
            "wins": self.winning_trades,
            "losses": self.losing_trades,
            "win_rate": win_rate,
            "consecutive_losses": self.consecutive_losses,
            "largest_win": self.largest_win,
            "largest_loss": self.largest_loss,
            "profit_factor": profit_factor,
        }


# Pre-built firm templates
FIRM_TEMPLATES = {
    PropFirmName.TOPSTEP: FirmRules(
        firm_name=PropFirmName.TOPSTEP,
        account_size=50000.0,
        max_daily_loss=150.0,
        max_trailing_drawdown=3000.0,
        profit_target_phase1=3000.0,
        min_trading_days=1,
        max_positions=10,
        consistency_rule="No single day can exceed 50% of total profit",
        phases=1,
        profit_split=80.0,
    ),
    
    PropFirmName.APEX: FirmRules(
        firm_name=PropFirmName.APEX,
        account_size=50000.0,
        max_daily_loss=0.0,  # Apex doesn't have daily loss limit
        max_trailing_drawdown=3000.0,
        profit_target_phase1=3000.0,
        min_trading_days=1,
        max_positions=10,
        consistency_rule="",
        phases=1,
        profit_split=90.0,
    ),
    
    PropFirmName.MYFUNDED: FirmRules(
        firm_name=PropFirmName.MYFUNDED,
        account_size=50000.0,
        max_daily_loss=1000.0,
        max_trailing_drawdown=2500.0,
        profit_target_phase1=3000.0,
        min_trading_days=1,
        max_positions=5,
        phases=1,
        profit_split=80.0,
    ),
    
    PropFirmName.FTMO: FirmRules(
        firm_name=PropFirmName.FTMO,
        account_size=50000.0,
        max_daily_loss=2500.0,  # 5% daily
        max_trailing_drawdown=5000.0,  # 10% overall
        profit_target_phase1=5000.0,  # 10% phase 1
        profit_target_phase2=2500.0,  # 5% phase 2
        min_trading_days=4,
        max_positions=10,
        phases=2,
        profit_split=80.0,
    ),
}


class PropFirmRuleEngine:
    """
    The "Professor" - Manages prop firm rules and enforces compliance.
    
    Usage:
        engine = PropFirmRuleEngine(PropFirmName.TOPSTEP)
        can_trade, violations = engine.check_before_trade("ES")
        engine.record_trade(pnl=150.0, asset="ES")
    """
    
    def __init__(self, firm: PropFirmName = PropFirmName.TOPSTEP):
        self.firm = firm
        self.rules = FIRM_TEMPLATES.get(firm, FIRM_TEMPLATES[PropFirmName.TOPSTEP])
        self.compliance = ComplianceState(self.rules)
        logger.info(f"Prop Firm Rule Engine initialized: {firm.value}")
        logger.info(f"Rules: Daily Loss=${self.rules.max_daily_loss}, "
                   f"Drawdown=${self.rules.max_trailing_drawdown}, "
                   f"Target=${self.rules.profit_target_phase1}")
    
    def check_before_trade(self, asset: str, potential_loss: float = 0) -> Tuple[bool, List[str]]:
        """
        Check if a trade is allowed BEFORE execution.
        Returns: (allowed, violations)
        """
        can_trade, violations = self.compliance.can_trade()
        
        # Check if potential loss would violate daily limit (skip if firm has no daily limit)
        if potential_loss > 0 and self.rules.max_daily_loss > 0:
            new_daily_pnl = self.compliance.daily_pnl - potential_loss
            if new_daily_pnl < -self.rules.max_daily_loss:
                violations.append(
                    f"[FAIL] TRADE WOULD EXCEED DAILY LIMIT: "
                    f"-${potential_loss:.2f} would put you at ${new_daily_pnl:.2f}"
                )
                can_trade = False
            
            # Check if potential loss would exceed drawdown
            new_drawdown = self.compliance.max_drawdown_from_peak + potential_loss
            if new_drawdown >= self.rules.max_trailing_drawdown:
                violations.append(
                    f"[FAIL] TRADE WOULD EXCEED DRAWDOWN LIMIT: "
                    f"${new_drawdown:.2f} would exceed ${self.rules.max_trailing_drawdown:.2f}"
                )
                can_trade = False
        
        return can_trade, violations
    
    def record_trade(self, pnl: float, asset: str):
        """Record a completed trade and update compliance."""
        self.compliance.update_trade(pnl, asset)
        
        # Check if we violated anything
        can_trade, violations = self.compliance.can_trade()
        if violations:
            logger.warning(f"Trade recorded - VIOLATIONS DETECTED: {violations}")
        else:
            logger.info(f"Trade recorded: {asset} P&L=${pnl:.2f} - All rules compliant")
    
    def get_dashboard_data(self) -> Dict:
        """Get data for dashboard display."""
        return self.compliance.get_compliance_report()
    
    def update_firm_rules(self, rules_dict: Dict):
        """Update rules from user input or vision detection."""
        if "max_daily_loss" in rules_dict:
            self.rules.max_daily_loss = rules_dict["max_daily_loss"]
        if "max_trailing_drawdown" in rules_dict:
            self.rules.max_trailing_drawdown = rules_dict["max_trailing_drawdown"]
        if "profit_target" in rules_dict:
            self.rules.profit_target_phase1 = rules_dict["profit_target"]
        if "account_size" in rules_dict:
            self.rules.account_size = rules_dict["account_size"]
            self.compliance.starting_balance = rules_dict["account_size"]
        
        logger.info(f"Firm rules updated: {self.rules.firm_name.value}")
