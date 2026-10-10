"""Buying group schemas."""
from datetime import datetime
from typing import Literal

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, field_validator


ApiFramework = Literal["parsefile", "usabg"]

ALLOWED_API_FRAMEWORKS = frozenset({"parsefile", "usabg"})

DEFAULT_TRACKING_SUBMIT_CRON = "0 */6 * * *"


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


def _normalize_cron(value: str) -> str:
    expr = (value or "").strip()
    if not expr:
        raise ValueError("Cron expression is required")
    if len(expr) > 64:
        raise ValueError("Cron expression must be at most 64 characters")
    if not croniter.is_valid(expr):
        raise ValueError("Invalid cron expression (expected 5 fields: min hour day month weekday)")
    return expr


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
    tracking_submit_cron: str = DEFAULT_TRACKING_SUBMIT_CRON

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

    @field_validator("tracking_submit_cron")
    @classmethod
    def validate_cron(cls, value: str) -> str:
        return _normalize_cron(value)


class BuyingGroupCreate(BuyingGroupBase):
    user_id: int | None = None


class BuyingGroupUpdate(BaseModel):
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
    tracking_submit_enabled: bool | None = None
    tracking_submit_cron: str | None = Field(default=None, max_length=64)

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

    @field_validator("tracking_submit_cron")
    @classmethod
    def validate_cron(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_cron(value)


class BuyingGroupRead(BuyingGroupBase):
    id: int
    user_id: int | None = None
    tracking_submit_last_run_at: datetime | None = None

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
