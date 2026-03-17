"""Payment schemas."""
from datetime import datetime

from app.schemas.common import TimestampsMixin
from app.schemas.buying_group import BuyingGroupRead
from app.schemas.item import ItemRead
from pydantic import BaseModel, ConfigDict


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
    buying_group: BuyingGroupRead | None = None
    line_items: list[PaymentLineItemRead] = []

    model_config = ConfigDict(from_attributes=True)
