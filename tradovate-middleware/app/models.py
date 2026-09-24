from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


Action = Literal["BUY", "SELL", "FLATTEN"]


class TradingViewSignal(BaseModel):
    """Schema for the JSON body TradingView alerts POST to the middleware.

    TradingView alert payloads are free-form; the Pine script must emit exactly
    these fields. Unknown fields are ignored so the schema can evolve.
    """

    signal_id: str = Field(..., description="Idempotency key, e.g. '{{strategy.order.id}}-{{timenow}}'")
    action: Action
    symbol: str = Field(..., description="TradingView symbol, e.g. NQ1!, MNQ1!, ES1!")
    quantity: Optional[int] = Field(None, ge=1, description="Contracts; capped by MAX_CONTRACTS_PER_ORDER")
    stop_loss: Optional[float] = Field(None, gt=0)
    take_profit: Optional[float] = Field(None, gt=0)
    entry_price: Optional[float] = Field(None, gt=0)
    timestamp_ms: Optional[int] = Field(None, description="Alert fire time, epoch ms")

    @field_validator("action", mode="before")
    @classmethod
    def _upper_action(cls, v: object) -> object:
        return v.upper() if isinstance(v, str) else v

    @field_validator("symbol", mode="before")
    @classmethod
    def _strip_symbol(cls, v: object) -> object:
        return v.strip().upper() if isinstance(v, str) else v


class OrderResult(BaseModel):
    accepted: bool
    signal_id: str
    symbol: str
    action: Action
    quantity: int = 0
    order_id: Optional[str] = None
    broker_status: Optional[str] = None
    reason: Optional[str] = None
    latency_ms: float = 0.0
