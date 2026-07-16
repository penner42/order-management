"""Order schemas."""
from datetime import datetime
from decimal import Decimal
from app.schemas.common import TimestampsMixin
from pydantic import BaseModel, ConfigDict, Field, computed_field
from app.schemas.item import ItemRead
from app.schemas.payment_method import PaymentMethodRead
from app.schemas.store import StoreRead, StoreAccountRead
from app.schemas.buying_group import BuyingGroupRead


class OrderPaymentMethodBase(BaseModel):
    payment_method_id: int
    amount: Decimal | None = None


class OrderPaymentMethodCreate(OrderPaymentMethodBase):
    pass


class OrderPaymentMethodRead(OrderPaymentMethodBase, TimestampsMixin):
    id: int
    order_id: int
    payment_method: PaymentMethodRead | None = None

    model_config = ConfigDict(from_attributes=True)


class OrderBase(BaseModel):
    status: str = "active"  # active | imported
    purchase_date: datetime | None = None
    notes: str | None = None
    buying_group_id: int | None = None
    order_discount: Decimal = Decimal("0")
    insurance_cost: Decimal = Decimal("0")


class OrderCreate(OrderBase):
    user_id: int | None = None
    store_id: int
    store_account_id: int | None = None
    store_order_number: str | None = None
    payment_methods: list[OrderPaymentMethodCreate] = []
    items: list["ItemCreateNested"] = []


class OrderUpdate(BaseModel):
    store_id: int | None = None
    store_account_id: int | None = None
    store_order_number: str | None = None
    status: str | None = None
    purchase_date: datetime | None = None
    notes: str | None = None
    buying_group_id: int | None = None
    order_discount: Decimal | None = None
    insurance_cost: Decimal | None = None
    payment_methods: list[OrderPaymentMethodCreate] | None = None


class OrderRead(OrderBase, TimestampsMixin):
    id: int
    user_id: int | None = None
    store_id: int
    store_account_id: int | None = None
    store_order_number: str | None = None
    store: StoreRead | None = None
    store_account: StoreAccountRead | None = None
    buying_group: BuyingGroupRead | None = None
    items: list[ItemRead] = []
    order_payments: list[OrderPaymentMethodRead] = []
    # Internal: read from the ORM object, excluded from responses (has_invoice is exposed instead).
    invoice_pdf_path: str | None = Field(default=None, exclude=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_invoice(self) -> bool:
        return bool(self.invoice_pdf_path)

    model_config = ConfigDict(from_attributes=True)


class OrderListPage(BaseModel):
    items: list[OrderRead]
    page: int
    per_page: int
    total: int
    pages: int


from app.schemas.item import ItemCreateNested  # noqa: E402
OrderCreate.model_rebuild()
