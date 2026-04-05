"""
VcaniTrade AI - Core Models
Pydantic schemas for LLM output validation and trade tracking
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime
from enum import Enum


class SignalAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    CLOSE = "CLOSE"


class ConfidenceLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


class LLMAnalysisOutput(BaseModel):
    """Strict JSON schema for LLM analysis output"""

    action: SignalAction = Field(
        ..., description="Trading action: BUY, SELL, HOLD, or CLOSE"
    )
    asset: str = Field(..., description="Asset symbol being analyzed")
    confidence: ConfidenceLevel = Field(
        ..., description="Confidence level of the signal"
    )
    entry_price: Optional[float] = Field(None, description="Recommended entry price")
    stop_loss: Optional[float] = Field(None, description="Stop loss price")
    take_profit: Optional[float] = Field(None, description="Take profit price")
    reason: str = Field(
        ..., max_length=200, description="Brief explanation of the signal"
    )
    timestamp: Optional[str] = Field(None, description="ISO timestamp of analysis")


class TradeRecord(BaseModel):
    """Trade ledger record for performance tracking"""

    id: str = Field(default_factory=lambda: f"trade_{datetime.utcnow().timestamp()}")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    asset: str
    action: SignalAction
    entry_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    confidence: ConfidenceLevel
    ai_reason: str
    mode: str = "TEACHER"  # TEACHER or AUTO
    status: str = "OPEN"  # OPEN, CLOSED, STOPPED
    closed_at: Optional[datetime] = None


class SafetyState(BaseModel):
    """Current safety control state"""

    kill_switch_active: bool = False
    daily_pnl: float = 0.0
    daily_loss_limit_hit: bool = False
    open_positions: int = 0
    cooldown_remaining_seconds: int = 0
    can_trade: bool = True

    def update_trade_ability(self):
        """Update whether trading is allowed based on safety rules"""
        import config

        self.can_trade = (
            not self.kill_switch_active
            and not self.daily_loss_limit_hit
            and self.cooldown_remaining_seconds == 0
            and self.open_positions < config.MAX_OPEN_POSITIONS
            and abs(self.daily_pnl) < config.MAX_DAILY_LOSS
        )
        return self.can_trade


class MarketDataPoint(BaseModel):
    """Market data for analysis"""

    asset: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    price: float
    volume: float
    price_change_1h: float = 0.0
    price_change_24h: float = 0.0
    high_24h: Optional[float] = None
    low_24h: Optional[float] = None
    indicators: dict = {}  # RSI, MACD, etc.


class OverlaySignal(BaseModel):
    """Data to display on transparent overlay"""

    asset: str
    action: SignalAction
    confidence: ConfidenceLevel
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reason: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    def get_color_code(self) -> str:
        """Get color based on action"""
        if self.action == SignalAction.BUY:
            return "#00FF00"  # Green
        elif self.action == SignalAction.SELL:
            return "#FF0000"  # Red
        else:
            return "#FFA500"  # Orange for HOLD/CLOSE


class SwarmAgentBrief(BaseModel):
    """Brief output from a single swarm agent"""

    agent: str  # "Technical Sniper" | "Macro Analyst" | "Risk Manager"
    action: Optional[str] = Field(
        None,
        description="BUY/SELL/HOLD for Sniper, BULLISH/BEARISH/NEUTRAL for Macro",
    )
    conviction: str = Field(..., description="LOW|MEDIUM|HIGH|VERY_HIGH")
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    brief: str = Field(
        ..., max_length=300, description="Agent's analysis in under 80 words"
    )
    risk_events: list[str] = Field(
        default_factory=list, description="Upcoming risk events (Macro Analyst only)"
    )
    verdict: Optional[str] = Field(
        None, description="APPROVE|ABORT (Risk Manager only)"
    )
    max_risk_pct: Optional[float] = Field(
        None, description="Max position risk % (Risk Manager only)"
    )


class DebateTranscript(BaseModel):
    """Full debate transcript for UI display"""

    asset: str
    technical_sniper: SwarmAgentBrief
    macro_analyst: SwarmAgentBrief
    risk_manager: SwarmAgentBrief
    ceo_verdict: str = Field(..., description="CEO's final one-line verdict")
    ceo_full_statement: str = Field(..., description="CEO's full reasoning")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class WatchlistAlert(BaseModel):
    """Alert from the Watchtower scanner when anomaly detected"""

    asset: str
    alert_type: str = Field(
        ..., description="VOLUME_SPIKE | VOLATILITY_BREAKOUT | TREND_REVERSAL"
    )
    severity: str = Field(..., description="LOW|MEDIUM|HIGH|CRITICAL")
    current_price: float
    volume_ratio: float = Field(
        ..., description="Current volume vs baseline (e.g. 3.5 = 350%)"
    )
    price_change_pct: float
    reason: str = Field(..., max_length=200)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    triggered_swarm: bool = False  # Whether this alert was sent to Swarm for analysis


class TradeAutopsy(BaseModel):
    """Post-trade analysis result with letter grade and explanation"""

    trade_id: str
    asset: str
    action: str  # BUY or SELL
    entry_price: float
    exit_price: float
    pnl: float
    grade: str = Field(..., description="A|B|C|D|F")
    technical_grade: str = Field(..., description="Sniper's grade of the entry")
    risk_grade: str = Field(..., description="Risk Manager's grade of the decision")
    explanation: str = Field(
        ...,
        max_length=500,
        description="Plain English explanation of why trade won/lost",
    )
    lessons: list[str] = Field(default_factory=list, description="Actionable takeaways")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
