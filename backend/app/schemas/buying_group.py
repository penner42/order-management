"""Buying group schemas."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ApiFramework = Literal["parsefile", "usabg", "buyinggroup", "dealandrunner", "bfmr"]

ALLOWED_API_FRAMEWORKS = frozenset({"parsefile", "usabg", "buyinggroup", "dealandrunner", "bfmr"})

DEFAULT_INTERVAL_MINUTES = 360


def _normalize_optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _normalize_api_framework(value: object) -> str | None:
    normalized = _normalize_optional_str(value)
    if normalized is None:
        return None
    key = normalized.casefold()
    if key not in ALLOWED_API_FRAMEWORKS:
        raise ValueError(f"Unsupported api_framework: {normalized}")
    return key


class BuyingGroupBase(BaseModel):
    name: str
    aliases: list[str] = []
    api_framework: ApiFramework | None = None
    base_url: str | None = None
    api_url: str | None = None
    bearer_token: str | None = None
    api_user_id: int | None = None
    api_email: str | None = None
    api_username: str | None = None
    api_password: str | None = None
    tracking_submit_enabled: bool = False
    order_number_submit_enabled: bool = False
    schedule_interval_minutes: int = Field(default=DEFAULT_INTERVAL_MINUTES, ge=1, le=525600, strict=True)
    payment_sync_enabled: bool = True
    received_check_enabled: bool = False

    @field_validator("aliases", mode="before")
    @classmethod
    def normalize_aliases(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            return []
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            alias = item.strip()
            if not alias:
                continue
            key = alias.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(alias)
        return normalized

    @field_validator("api_framework", mode="before")
    @classmethod
    def normalize_api_framework(cls, value: object) -> str | None:
        return _normalize_api_framework(value)

    @field_validator(
        "base_url",
        "api_url",
        "bearer_token",
        "api_email",
        "api_username",
        "api_password",
        mode="before",
    )
    @classmethod
    def normalize_optional_str(cls, value: object) -> str | None:
        return _normalize_optional_str(value)



class BuyingGroupCreate(BuyingGroupBase):
    user_id: int | None = None


class BuyingGroupUpdate(BaseModel):
    order_number_submit_enabled: bool = False
    name: str | None = None
    aliases: list[str] | None = None
    api_framework: ApiFramework | None = None
    base_url: str | None = None
    api_url: str | None = None
    bearer_token: str | None = None
    api_user_id: int | None = None
    api_email: str | None = None
    api_username: str | None = None
    api_password: str | None = None
    tracking_submit_enabled: bool = False
    schedule_interval_minutes: int = Field(default=DEFAULT_INTERVAL_MINUTES, ge=1, le=525600, strict=True)
    payment_sync_enabled: bool = True
    received_check_enabled: bool = False

    @field_validator("aliases", mode="before")
    @classmethod
    def normalize_aliases(cls, value: object) -> list[str] | None:
        if value is None:
            return None
        return BuyingGroupBase.normalize_aliases(value)

    @field_validator("api_framework", mode="before")
    @classmethod
    def normalize_api_framework(cls, value: object) -> str | None:
        return _normalize_api_framework(value)

    @field_validator(
        "base_url",
        "api_url",
        "bearer_token",
        "api_email",
        "api_username",
        "api_password",
        mode="before",
    )
    @classmethod
    def normalize_optional_str(cls, value: object) -> str | None:
        return _normalize_optional_str(value)



class BuyingGroupRead(BuyingGroupBase):
    id: int
    user_id: int | None = None
    payment_sync_last_run_at: datetime | None = None
    payment_sync_error: str | None = None
    schedule_next_run_at: datetime | None = None
    tracking_submit_last_run_at: datetime | None = None
    received_check_last_run_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class BuyingGroupSummary(BaseModel):
    """Nested on orders/payments — omits API credentials."""

    id: int
    user_id: int | None = None
    name: str
    aliases: list[str] = []
    api_framework: ApiFramework | None = None

    model_config = ConfigDict(from_attributes=True)


class BuyingGroupSubmitTrackingResponse(BaseModel):
    buying_group_id: int
    submitted_count: int
    message: str | None = None
    tracking_numbers: list[str] = []
    tracking_submit_last_run_at: datetime | None = None


class BuyingGroupCheckReceivingResponse(BaseModel):
    buying_group_id: int
    buying_group_name: str
    token: str | None = None
    matched_count: int = 0
    not_found_count: int = 0
    void_skipped_count: int = 0
    discrepancy_count: int = 0
    submitted_count: int = 0
    message: str | None = None
    review_path: str | None = None


class ReceivingReviewMatch(BaseModel):
    receipt_tracking_number: str | None = None
    manual_receipt_key: str | None = None
    item_id: int
    order_id: int
    store_order_number: str | None = None
    description: str | None = None
    quantity: int = 1
    tracking_number: str
    price_sold: str = "0"
    expected_amount: str | None = None
    receipt_id: str | None = None
    scanned_at: str | None = None
    received_item: str | None = None
    receipt_upc: str | None = None
    received_qty: int | None = None
    received_price: str | None = None
    received_ext_total: str | None = None
    reward: str | None = None
    received_status: str | None = None
    amount_discrepancy: bool = False
    selected: bool = True
    row_id: str | None = None
    match_kind: str = "received"  # received | not_received
    original_quantity: int | None = None
    split_keep_quantity: int | None = None
    update_receipt_only: bool = False


class ReceivingReviewUnmatched(BaseModel):
    item_id: int
    order_id: int
    store_order_number: str | None = None
    description: str | None = None
    quantity: int = 1
    status: str | None = None
    tracking_numbers: list[str] = []
    expected_amount: str | None = None
    submitted_at: str | None = None
    void_matched: bool = False


class ReceivingReviewUnknownReceipt(BaseModel):
    """BG receipt whose tracking is not present on any OM shipment."""

    receipt_id: str | None = None
    receipt_key: str | None = None
    tracking_number: str
    tracking_numbers: list[str] = Field(default_factory=list)
    source_data: dict = Field(default_factory=dict)
    receipt_upc: str | None = None
    linkable: bool = True
    bg_order_id: str | None = None
    description: str | None = None
    quantity: int | None = None
    received_price: str | None = None
    received_ext_total: str | None = None
    reward: str | None = None
    received_status: str | None = None
    note: str | None = None
    created_dt: str | None = None
    scanned_at: str | None = None
    delivery: str | None = None


class ReceivingReviewSession(BaseModel):
    rematched_at: str | None = None
    buying_group_id: int
    buying_group_name: str
    created_at: str | None = None
    not_found_count: int = 0
    void_skipped_count: int = 0
    searched_trackings: list[str] = []
    raw_csv: str = ""
    csv_row_count: int = 0
    unmatched: list[ReceivingReviewUnmatched] = []
    unknown_receipts: list[ReceivingReviewUnknownReceipt] = []
    matches: list[ReceivingReviewMatch] = []


class ReceivingReviewPruneRequest(BaseModel):
    buying_group_id: int
    item_ids: list[int] = Field(default_factory=list)


class ReceivingReviewPruneResponse(BaseModel):
    buying_group_id: int
    remaining_matched_count: int = 0
    deleted: bool = False


class ReceivingReceiptLinkRequest(BaseModel):
    buying_group_id: int
    receipt_key: str
    item_ids: list[int] = Field(default_factory=list)
    item_id: int | None = None  # Older clients may still select one whole line.
    receipt_tracking_number: str
    confirm_full_quantity: bool = False
    split_quantity: int | None = Field(default=None, gt=0)


class ReceivingReceiptUnlinkRequest(BaseModel):
    buying_group_id: int
    receipt_key: str
