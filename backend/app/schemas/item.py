"""Item schemas."""
from datetime import datetime
from decimal import Decimal
from app.schemas.common import TimestampsMixin
from pydantic import BaseModel, ConfigDict
from app.models.item import ItemStatus


class ItemBase(BaseModel):
    price_paid: Decimal | None = None
    price_sold: Decimal | None = None
    status: ItemStatus = ItemStatus.PURCHASED
    quantity: int = 1
    description: str | None = None
    submission_id: str | None = None
    receipt_id: str | None = None
    # Datetime when each status was (last) set
    purchased_at: datetime | None = None
    shipped_at: datetime | None = None
    submitted_at: datetime | None = None
    delivered_at: datetime | None = None
    scanned_at: datetime | None = None
    payment_requested_at: datetime | None = None
    payment_sent_at: datetime | None = None
    payment_received_at: datetime | None = None
    canceled_at: datetime | None = None
    needs_return_at: datetime | None = None
    return_started_at: datetime | None = None
    return_sent_at: datetime | None = None
    return_received_at: datetime | None = None
    return_refunded_at: datetime | None = None


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
    submission_id: str | None = None
    receipt_id: str | None = None
    purchased_at: datetime | None = None
    shipped_at: datetime | None = None
    submitted_at: datetime | None = None
    delivered_at: datetime | None = None
    scanned_at: datetime | None = None
    payment_requested_at: datetime | None = None
    payment_sent_at: datetime | None = None
    payment_received_at: datetime | None = None
    canceled_at: datetime | None = None
    needs_return_at: datetime | None = None
    return_started_at: datetime | None = None
    return_sent_at: datetime | None = None
    return_received_at: datetime | None = None
    return_refunded_at: datetime | None = None


class ItemRead(ItemBase, TimestampsMixin):
    id: int
    order_id: int

    model_config = ConfigDict(from_attributes=True)


class ItemReadWithRelations(ItemRead):
    shipment_id: int | None = None


class ItemSplitRequest(BaseModel):
    keep_quantity: int  # Quantity to keep on original item; remainder becomes new item


class ItemBulkUpdateEntry(BaseModel):
    """Single item update in a bulk request."""
    item_id: int
    price_paid: Decimal | None = None
    price_sold: Decimal | None = None
    status: ItemStatus | None = None
    quantity: int | None = None
    description: str | None = None
    submission_id: str | None = None
    receipt_id: str | None = None
    purchased_at: datetime | None = None
    shipped_at: datetime | None = None
    submitted_at: datetime | None = None
    delivered_at: datetime | None = None
    scanned_at: datetime | None = None
    payment_requested_at: datetime | None = None
    payment_sent_at: datetime | None = None
    payment_received_at: datetime | None = None
    canceled_at: datetime | None = None
    needs_return_at: datetime | None = None
    return_started_at: datetime | None = None
    return_sent_at: datetime | None = None
    return_received_at: datetime | None = None
    return_refunded_at: datetime | None = None


class ItemBulkUpdateRequest(BaseModel):
    updates: list[ItemBulkUpdateEntry]


class ItemBulkUpdateResponse(BaseModel):
    items: list[ItemRead]


class ItemSplitResponse(BaseModel):
    kept: ItemRead
    split_off: ItemRead
