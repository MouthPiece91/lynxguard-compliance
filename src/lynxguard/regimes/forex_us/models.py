"""Input events for the US retail forex regime.

All timestamps must be timezone-aware. Money and quantities use Decimal.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def _aware(v: datetime | None) -> datetime | None:
    if v is not None and v.tzinfo is None:
        raise ValueError("timestamp must include a timezone offset")
    return v


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


class OpenLot(BaseModel):
    """One open position lot in a customer account."""

    lot_id: str
    pair: str = Field(examples=["EUR/USD"])
    side: Side
    quantity: Decimal = Field(gt=0, description="Base-currency units")
    price: Decimal = Field(gt=0)
    opened_at: datetime
    notional_usd: Decimal = Field(ge=0)

    _tz = field_validator("opened_at")(_aware)


class AccountSnapshot(BaseModel):
    account_id: str
    security_deposit_usd: Decimal = Field(
        description="Customer funds held as security deposit (after any haircuts)"
    )
    open_lots: list[OpenLot] = []


class Trade(BaseModel):
    """A proposed or executed customer transaction."""

    trade_id: str
    pair: str
    side: Side
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    executed_at: datetime
    notional_usd: Decimal = Field(ge=0)
    position_effect: Literal["open", "close"]
    closes_lot_ids: list[str] = Field(
        default=[], description="For closes: the lots this trade offsets, in order"
    )
    customer_directed_offset: bool = Field(
        default=False,
        description="Customer asked to offset a same-size lot instead of strict FIFO",
    )

    _tz = field_validator("executed_at")(_aware)


class TradeCheckRequest(BaseModel):
    account: AccountSnapshot
    trade: Trade

    @model_validator(mode="after")
    def _known_lots(self) -> "TradeCheckRequest":
        ids = {lot.lot_id for lot in self.account.open_lots}
        missing = [i for i in self.trade.closes_lot_ids if i not in ids]
        if missing:
            raise ValueError(f"closes_lot_ids not found in open_lots: {missing}")
        return self


class AdjustmentReason(str, Enum):
    CUSTOMER_COMPLAINT = "customer_complaint"
    PLATFORM_ERROR = "platform_error"
    COUNTERPARTY_STP = "counterparty_stp"
    OTHER = "other"


class WindowTrade(BaseModel):
    trade_id: str
    side: Side


class PriceAdjustmentCheckRequest(BaseModel):
    adjustment_id: str
    trade_id: str
    pair: str
    executed_at: datetime
    adjusted_at: datetime
    reason: AdjustmentReason
    favorable_to_customer: bool
    customer_notified_at: datetime | None = None
    counterparty_documentation_provided: bool = False
    dealer_uses_stp_exclusively: bool = False
    counterparty_unaffiliated: bool = False
    principal_approval_documented: bool = False
    window_trades: list[WindowTrade] = Field(
        default=[],
        description="All customer trades in the same time period and pair (counterparty adjustments)",
    )
    adjusted_trade_ids: list[str] = []

    _tz = field_validator("executed_at", "adjusted_at", "customer_notified_at")(_aware)


class FundingMethod(str, Enum):
    WIRE = "wire"
    ACH = "ach"
    CHECK = "check"
    CREDIT_CARD = "credit_card"
    CREDIT_CARD_LINKED = "credit_card_linked"  # e.g. a wallet funded by a credit card
    OTHER = "other"


class DepositCheckRequest(BaseModel):
    deposit_id: str
    account_id: str
    amount_usd: Decimal = Field(gt=0)
    funding_method: FundingMethod
    received_at: datetime
    dealer_can_distinguish_credit_cards: bool | None = Field(
        default=None,
        description="For ACH/electronic debits: can the dealer tell them apart from card funding?",
    )

    _tz = field_validator("received_at")(_aware)


class ConfirmationCheckRequest(BaseModel):
    trade_id: str
    executed_at: datetime
    confirmation_sent_at: datetime | None = None
    as_of: datetime = Field(description="When this check is being run")

    _tz = field_validator("executed_at", "confirmation_sent_at", "as_of")(_aware)
