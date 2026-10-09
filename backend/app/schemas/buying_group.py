"""Buying group schemas."""
from pydantic import BaseModel, ConfigDict, field_validator


def _normalize_optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


class BuyingGroupBase(BaseModel):
    name: str
    aliases: list[str] = []
    base_url: str | None = None
    api_url: str | None = None
    bearer_token: str | None = None

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

    @field_validator("base_url", "api_url", "bearer_token", mode="before")
    @classmethod
    def normalize_optional_str(cls, value: object) -> str | None:
        return _normalize_optional_str(value)


class BuyingGroupCreate(BuyingGroupBase):
    user_id: int | None = None


class BuyingGroupUpdate(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None
    base_url: str | None = None
    api_url: str | None = None
    bearer_token: str | None = None

    @field_validator("aliases", mode="before")
    @classmethod
    def normalize_aliases(cls, value: object) -> list[str] | None:
        if value is None:
            return None
        return BuyingGroupBase.normalize_aliases(value)

    @field_validator("base_url", "api_url", "bearer_token", mode="before")
    @classmethod
    def normalize_optional_str(cls, value: object) -> str | None:
        return _normalize_optional_str(value)


class BuyingGroupRead(BuyingGroupBase):
    id: int
    user_id: int | None = None

    model_config = ConfigDict(from_attributes=True)


class BuyingGroupSummary(BaseModel):
    """Nested on orders/payments — omits API credentials."""

    id: int
    user_id: int | None = None
    name: str
    aliases: list[str] = []

    model_config = ConfigDict(from_attributes=True)
