"""Item schemas."""
from datetime import datetime
from decimal import Decimal
from app.schemas.common import TimestampsMixin
from pydantic import BaseModel, ConfigDict, field_validator
from app.models.item import ItemStatus


def _coerce_price_sold(v: object) -> object:
    """Treat missing/null payout as 0 (column is NOT NULL)."""
    if v is None or v == "":
        return Decimal("0")
    return v


class ItemBase(BaseModel):
    price_paid: Decimal | None = None
    price_sold: Decimal = Decimal("0")
    status: ItemStatus = ItemStatus.PURCHASED
    quantity: int = 1
    description: str | None = None
    shipping: Decimal | None = None
    sales_tax: Decimal | None = None
    submission_id: str | None = None
    submission_api_id: str | None = None
    receipt_id: str | None = None
    # Datetime when each status was (last) set (shipped_at/delivered_at on Shipment; payment dates on Payment)
    purchased_at: datetime | None = None
    submitted_at: datetime | None = None
    scanned_at: datetime | None = None
    canceled_at: datetime | None = None
    needs_review_at: datetime | None = None
    needs_return_at: datetime | None = None
    return_started_at: datetime | None = None
    return_sent_at: datetime | None = None
    return_received_at: datetime | None = None
    return_refunded_at: datetime | None = None
    lost_package_at: datetime | None = None

    @field_validator("price_sold", mode="before")
    @classmethod
    def price_sold_not_null(cls, v: object) -> object:
        return _coerce_price_sold(v)


class ItemCreate(ItemBase):
    order_id: int


class ItemCreateNested(ItemBase):
    """Item payload when creating via Order (order_id set by server)."""
    order_id: int | None = None


class ItemUpdate(BaseModel):
    price_paid: Decimal | None = None
    price_sold: Decimal | None = None
    status: ItemStatus | None = None
    quantity: int | None = None
    description: str | None = None
    shipping: Decimal | None = None
    sales_tax: Decimal | None = None
    submission_id: str | None = None
    submission_api_id: str | None = None
    receipt_id: str | None = None
    purchased_at: datetime | None = None
    submitted_at: datetime | None = None
    scanned_at: datetime | None = None
    canceled_at: datetime | None = None
    needs_review_at: datetime | None = None
    needs_return_at: datetime | None = None
    return_started_at: datetime | None = None
    return_sent_at: datetime | None = None
    return_received_at: datetime | None = None
    return_refunded_at: datetime | None = None
    lost_package_at: datetime | None = None

    @field_validator("price_sold", mode="before")
    @classmethod
    def price_sold_not_null(cls, v: object) -> object:
        return _coerce_price_sold(v)


class ItemRead(ItemBase, TimestampsMixin):
    id: int
    order_id: int
    # From Payment when item is on a payment (read-only); payment_id for PATCH payment
    payment_id: int | None = None
    payment_requested_at: datetime | None = None
    payment_sent_at: datetime | None = None
    payment_received_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ItemReadWithRelations(ItemRead):
    shipment_id: int | None = None


class ItemSplitRequest(BaseModel):
    keep_quantity: int  # Quantity to keep on original item; remainder becomes new item
    # When True, link the split-off item to the same shipment (tracking) as the original.
    copy_tracking: bool = False
    # Optional status for the new line (e.g. needs_review for under-received remainders).
    split_off_status: ItemStatus | None = None


class ItemBulkUpdateEntry(BaseModel):
    """Single item update in a bulk request. delivered_at applies to the item's shipment."""
    item_id: int
    price_paid: Decimal | None = None
    price_sold: Decimal | None = None
    status: ItemStatus | None = None
    quantity: int | None = None
    description: str | None = None
    shipping: Decimal | None = None
    sales_tax: Decimal | None = None
    submission_id: str | None = None
    submission_api_id: str | None = None
    receipt_id: str | None = None
    purchased_at: datetime | None = None
    submitted_at: datetime | None = None
    delivered_at: datetime | None = None  # applied to shipment
    scanned_at: datetime | None = None
    canceled_at: datetime | None = None
    needs_review_at: datetime | None = None
    needs_return_at: datetime | None = None
    return_started_at: datetime | None = None
    return_sent_at: datetime | None = None
    return_received_at: datetime | None = None
    return_refunded_at: datetime | None = None
    lost_package_at: datetime | None = None

    @field_validator("price_sold", mode="before")
    @classmethod
    def price_sold_not_null(cls, v: object) -> object:
        return _coerce_price_sold(v)


class ItemBulkUpdateRequest(BaseModel):
    updates: list[ItemBulkUpdateEntry]


class ItemBulkDeleteRequest(BaseModel):
    item_ids: list[int]


class ItemBulkUpdateResponse(BaseModel):
    items: list[ItemRead]


class ItemSplitResponse(BaseModel):
    kept: ItemRead
    split_off: ItemRead


class ItemSubmitTrackingResponse(BaseModel):
    item: ItemRead
    """All items marked submitted (includes siblings that shared the tracking)."""
    items: list[ItemRead] = []
    message: str
    affected: int | None = None
