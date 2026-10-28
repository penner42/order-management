"""Payment schemas."""
from datetime import datetime

from app.schemas.common import TimestampsMixin
from app.schemas.buying_group import BuyingGroupSummary
from app.schemas.item import ItemRead
from pydantic import BaseModel, ConfigDict, model_validator


class PaymentBase(BaseModel):
    buying_group_id: int
    payment_id: str | None = None
    payment_bonus: float = 0
    payment_requested_at: datetime  # Required on payment; backfilled from created_at if missing
    payment_sent_at: datetime | None = None
    payment_received_at: datetime | None = None


class PaymentCreate(BaseModel):
    buying_group_id: int
    payment_id: str | None = None
    payment_bonus: float = 0
    payment_requested_at: datetime | None = None  # Defaults to now() if not provided
    payment_sent_at: datetime | None = None
    payment_received_at: datetime | None = None


class PaymentUpdate(BaseModel):
    payment_id: str | None = None
    payment_bonus: float | None = None
    payment_requested_at: datetime | None = None
    payment_sent_at: datetime | None = None
    payment_received_at: datetime | None = None


class PaymentLineItemBase(BaseModel):
    item_id: int
    amount: float


class PaymentLineItemCreate(BaseModel):
    item_id: int
    amount: float | None = None


class PaymentLineItemRead(BaseModel):
    id: int
    payment_id: int
    item_id: int
    amount: float
    item: ItemRead | None = None

    model_config = ConfigDict(from_attributes=True)


class PaymentLineItemUpdate(BaseModel):
    amount: float | None = None


class PaymentRead(PaymentBase, TimestampsMixin):
    id: int
    source_amount: float | None = None
    ledger_state: str = "legacy"
    allocation_manual: bool = False
    allocation_version: int = 1
    allocation_details: dict = {}
    allocation_breakdown: dict = {}
    sync_issues: list[str] = []
    receipt_conflict: bool = False
    total_amount: float = 0
    source_details: dict | None = None
    corrections: list["PaymentCorrectionRead"] = []
    monarch_receipt: dict | None = None
    buying_group: BuyingGroupSummary | None = None
    line_items: list[PaymentLineItemRead] = []

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def bank_receipt_requires_current_deposit(self):
        if not self.monarch_receipt or self.receipt_conflict:
            self.payment_received_at = None
        return self


class PaymentCorrectionRead(BaseModel):
    id: int
    receipt_source_id: int
    origin_payment_id: int
    item_id: int
    amount: float
    explanation: str
    model_config = ConfigDict(from_attributes=True)


PaymentRead.model_rebuild()
