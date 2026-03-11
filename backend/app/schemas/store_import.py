"""Store order import schemas."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StoreOrderImportCreate(BaseModel):
    """Payload from browser extension for a store order import.

    This is intentionally flexible: we persist the full body as normalized_payload_json,
    and only pull out the fields we need for matching and display.
    """

    store: str = Field(..., description="External store identifier, e.g. 'walmart'.")
    source: str | None = Field(
        default=None,
        description="Source of the import, e.g. 'browser-extension'.",
    )
    capturedAt: datetime | None = Field(
        default=None,
        description="When the snapshot was captured on the client (optional).",
    )

    externalOrder: dict[str, Any]
    customer: dict[str, Any] | None = None
    shippingAddress: dict[str, Any] | None = None
    shipments: list[dict[str, Any]] | None = None
    items: list[dict[str, Any]] | None = None
    cancellations: dict[str, Any] | None = None
    totals: dict[str, Any] | None = None
    rawPayload: dict[str, Any] | None = None


class StoreOrderImportRead(BaseModel):
    """Store order import record for UI / API clients."""

    id: int
    store: str
    external_order_id: str
    external_order_url: str | None = None
    status: str
    linked_order_id: int | None = None
    normalized_payload_json: dict[str, Any]
    diff_json: dict[str, Any] | None = None
    created_at: datetime
    applied_at: datetime | None = None
    discarded_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class StoreOrderImportListResponse(BaseModel):
    imports: list[StoreOrderImportRead]


class StoreOrderImportApplyBody(BaseModel):
    """Optional body when applying an import."""

    store_account_id: int | None = None
    buying_group_id: int | None = None
    item_payouts: list[float | None] | None = None


class StoreOrderImportApplyResponse(BaseModel):
    """Response when an import has been applied."""

    import_record: StoreOrderImportRead
    order_id: int

