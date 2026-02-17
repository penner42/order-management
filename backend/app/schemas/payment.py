"""Payment schemas."""
from app.schemas.common import TimestampsMixin
from app.schemas.buying_group import BuyingGroupRead
from app.schemas.item import ItemRead
from pydantic import BaseModel, ConfigDict


class PaymentBase(BaseModel):
    buying_group_id: int
    payment_id: str | None = None


class PaymentCreate(PaymentBase):
    pass


class PaymentUpdate(BaseModel):
    payment_id: str | None = None


class PaymentLineItemBase(BaseModel):
    item_id: int


class PaymentLineItemCreate(PaymentLineItemBase):
    pass


class PaymentLineItemRead(PaymentLineItemBase):
    id: int
    payment_id: int
    item: ItemRead | None = None

    model_config = ConfigDict(from_attributes=True)


class PaymentRead(PaymentBase, TimestampsMixin):
    id: int
    buying_group: BuyingGroupRead | None = None
    line_items: list[PaymentLineItemRead] = []

    model_config = ConfigDict(from_attributes=True)
