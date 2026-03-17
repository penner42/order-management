"""Store order import schemas (in-memory import flow)."""
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.order import OrderPaymentMethodCreate


class StoreOrderImportPayload(BaseModel):
    """Normalized payload from browser extension for a store order.

    Used as input for both the diff (read-only) and apply (write) endpoints.
    """

    store: str = Field(..., description="External store identifier, e.g. 'walmart'.")
    source: str | None = Field(
        default=None,
        description="Source of the import, e.g. 'browser-extension'.",
    )

    externalOrder: dict[str, Any]
    customer: dict[str, Any] | None = None
    shippingAddress: dict[str, Any] | None = None
    shipments: list[dict[str, Any]] | None = None
    items: list[dict[str, Any]] | None = None
    cancellations: dict[str, Any] | None = None
    totals: dict[str, Any] | None = None
    paymentMethods: list[dict[str, Any]] | None = None


class StoreOrderDiffResponse(BaseModel):
    """Read-only diff result comparing an incoming payload against an existing order."""

    diff: dict[str, Any]


class BulkStoreOrderDiffRequest(BaseModel):
    """Compute diffs for multiple normalized payloads in one request."""

    orders: list[StoreOrderImportPayload]


class BulkStoreOrderDiffResponse(BaseModel):
    """Bulk diff results aligned by index to the input payload list."""

    diffs: list[dict[str, Any]]


class DirectApplyBody(BaseModel):
    """Payload + user selections to create/update an order directly (no staging row)."""

    payload: StoreOrderImportPayload
    store_account_id: int | None = None
    buying_group_id: int | None = None
    item_payouts: list[float | None] | None = None
    payment_methods: list[OrderPaymentMethodCreate] | None = None


class DirectApplyResponse(BaseModel):
    """Response after directly applying an import."""

    order_id: int


class BulkImportSessionCreate(BaseModel):
    """Create a short-lived bulk import session from multiple normalized payloads."""

    orders: list[StoreOrderImportPayload]


class BulkImportSessionResponse(BaseModel):
    """Identifier for a bulk import session that the frontend can use to fetch payloads."""

    token: str


class BulkImportSessionPayloads(BaseModel):
    """Resolved bulk session payloads for the frontend review UI."""

    orders: list[StoreOrderImportPayload]
