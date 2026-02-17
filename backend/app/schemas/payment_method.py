"""Payment method schemas."""
from datetime import datetime
from decimal import Decimal

from app.schemas.common import TimestampsMixin
from app.schemas.reward import RewardRead
from app.schemas.store import StoreRead
from pydantic import BaseModel, ConfigDict


class PaymentMethodBase(BaseModel):
    label: str


class PaymentMethodCreate(PaymentMethodBase):
    user_id: int | None = None
    reward_id: int | None = None
    parent_id: int | None = None


class PaymentMethodUpdate(BaseModel):
    label: str | None = None
    reward_id: int | None = None


class PaymentMethodReadNested(BaseModel):
    """Sub-method in list (no further nesting)."""

    id: int
    user_id: int | None = None
    reward_id: int | None = None
    parent_id: int | None = None
    label: str
    reward: RewardRead | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class PaymentMethodRead(PaymentMethodBase, TimestampsMixin):
    id: int
    user_id: int | None = None
    reward_id: int | None = None
    parent_id: int | None = None
    reward: RewardRead | None = None
    sub_methods: list[PaymentMethodReadNested] = []

    model_config = ConfigDict(from_attributes=True)


class StoreEarningsEntry(BaseModel):
    """One store's points-per-dollar for a payment method."""

    store_id: int
    store: StoreRead
    points_per_dollar: Decimal


class StoreEarningsBulkEntry(BaseModel):
    store_id: int
    points_per_dollar: Decimal


class StoreEarningsBulk(BaseModel):
    """Bulk update store earnings for a payment method."""

    store_earnings: list[StoreEarningsBulkEntry]
