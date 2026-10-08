"""Schemas for browser automation profiles and jobs."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Retailer = Literal["amazon", "walmart"]
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


class BrowserProfileRead(BaseModel):
    id: int
    store_account_id: int
    retailer: str
    status: str
    last_error: str | None = None
    last_import_at: datetime | None = None
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
    max_pages: int = Field(default=3, ge=1, le=50)


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
