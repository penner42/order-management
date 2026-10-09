"""Schemas for browser automation profiles and jobs."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class BrowserProfileCreate(BaseModel):
    store_account_id: int
    retailer: Retailer = "walmart"


class BrowserProfileScheduleUpdate(BaseModel):
    """Partial update for per-profile import schedules."""

    full_check_enabled: bool | None = None
    full_check_interval_hours: int | None = Field(default=None, ge=1, le=720)
    full_check_max_pages: int | None = Field(default=None, ge=1, le=50)
    unshipped_check_enabled: bool | None = None
    unshipped_check_interval_hours: int | None = Field(default=None, ge=1, le=720)


class BrowserProfileRead(BaseModel):
    id: int
    store_account_id: int
    retailer: str
    status: str
    last_error: str | None = None
    last_import_at: datetime | None = None
    full_check_enabled: bool = False
    full_check_interval_hours: int = 24
    full_check_max_pages: int = 3
    full_check_last_run_at: datetime | None = None
    unshipped_check_enabled: bool = False
    unshipped_check_interval_hours: int = 6
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


BrowserImportLogEvent = Literal["order_imported", "tracking_updated"]


class BrowserImportLogRead(BaseModel):
    id: int
    browser_profile_id: int | None = None
    job_id: str | None = None
    event_type: BrowserImportLogEvent | str
    mode: ImportMode | str
    scheduled: bool
    retailer: str
    store_order_number: str
    tracking_numbers: list[str] = []
    store_name: str | None = None
    store_account_name: str | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
