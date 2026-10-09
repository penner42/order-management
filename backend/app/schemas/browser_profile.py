"""Schemas for browser automation profiles and jobs."""
from datetime import datetime
from typing import Literal

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, field_validator


Retailer = Literal["amazon", "walmart"]
ImportMode = Literal["full", "unshipped"]
ProfileStatus = Literal[
    "logged_out",
    "ready",
    "login_required",
    "login_in_progress",
    "importing",
    "error",
]

DEFAULT_FULL_CHECK_CRON = "0 0 * * *"
DEFAULT_UNSHIPPED_CHECK_CRON = "0 */6 * * *"


def _normalize_cron(value: str) -> str:
    expr = (value or "").strip()
    if not expr:
        raise ValueError("Cron expression is required")
    if len(expr) > 64:
        raise ValueError("Cron expression must be at most 64 characters")
    if not croniter.is_valid(expr):
        raise ValueError("Invalid cron expression (expected 5 fields: min hour day month weekday)")
    return expr


class BrowserProfileCreate(BaseModel):
    store_account_id: int
    retailer: Retailer = "walmart"


class BrowserProfileScheduleUpdate(BaseModel):
    """Partial update for per-profile import schedules."""

    full_check_enabled: bool | None = None
    full_check_cron: str | None = Field(default=None, max_length=64)
    full_check_max_pages: int | None = Field(default=None, ge=1, le=50)
    unshipped_check_enabled: bool | None = None
    unshipped_check_cron: str | None = Field(default=None, max_length=64)

    @field_validator("full_check_cron", "unshipped_check_cron")
    @classmethod
    def validate_cron(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_cron(value)


class BrowserProfileRead(BaseModel):
    id: int
    store_account_id: int
    retailer: str
    status: str
    last_error: str | None = None
    last_import_at: datetime | None = None
    full_check_enabled: bool = False
    full_check_cron: str = DEFAULT_FULL_CHECK_CRON
    full_check_max_pages: int = 3
    full_check_last_run_at: datetime | None = None
    unshipped_check_enabled: bool = False
    unshipped_check_cron: str = DEFAULT_UNSHIPPED_CHECK_CRON
    unshipped_check_last_run_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Nested display helpers (populated by router)
    store_id: int | None = None
    store_name: str | None = None
    store_account_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class LoginStartResponse(BaseModel):
    profile_id: int
    status: str
    ws_path: str
    login_url: str


class ImportStartRequest(BaseModel):
    mode: ImportMode = "full"
    max_pages: int = Field(default=3, ge=1, le=50)
    # When True, apply captures directly to orders (used by schedules / Run now).
    # When False (default for full), create an Import Review bulk session.
    auto_apply: bool = False


class ImportStartResponse(BaseModel):
    job_id: str
    profile_id: int
    status: str


class BrowserJobRead(BaseModel):
    id: str
    profile_id: int
    kind: str
    status: str
    message: str | None = None
    progress: dict | None = None
    review_url: str | None = None
    token: str | None = None
    order_count: int | None = None
    error: str | None = None


BrowserImportLogLevel = Literal["updates", "info"]
BrowserImportLogEvent = Literal[
    "order_imported",
    "tracking_updated",
    "order_checked",
    "check_started",
    "check_finished",
]


class BrowserImportLogRead(BaseModel):
    id: int
    browser_profile_id: int | None = None
    job_id: str | None = None
    level: BrowserImportLogLevel | str
    event_type: BrowserImportLogEvent | str
    mode: ImportMode | str
    scheduled: bool
    retailer: str
    store_order_number: str | None = None
    tracking_numbers: list[str] = []
    message: str | None = None
    store_name: str | None = None
    store_account_name: str | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
