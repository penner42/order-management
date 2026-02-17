"""Shipment schemas."""
from datetime import datetime
from app.schemas.common import TimestampsMixin
from app.schemas.item import ItemRead
from pydantic import BaseModel, ConfigDict


class ShipmentItemBase(BaseModel):
    item_id: int


class ShipmentItemCreate(ShipmentItemBase):
    shipment_id: int


class ShipmentItemRead(ShipmentItemBase):
    id: int
    shipment_id: int
    item: ItemRead | None = None

    model_config = ConfigDict(from_attributes=True)


class ShipmentBase(BaseModel):
    tracking_number: str | None = None
    shipped_at: datetime | None = None
    notes: str | None = None


class ShipmentCreate(ShipmentBase):
    user_id: int | None = None
    item_ids: list[int] = []


class ShipmentUpdate(BaseModel):
    tracking_number: str | None = None
    shipped_at: datetime | None = None
    notes: str | None = None
    item_ids: list[int] | None = None


class ShipmentRead(ShipmentBase, TimestampsMixin):
    id: int
    user_id: int | None = None
    shipment_items: list[ShipmentItemRead] = []

    model_config = ConfigDict(from_attributes=True)
