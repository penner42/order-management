"""Buying group schemas."""
from pydantic import BaseModel, ConfigDict, field_validator


class BuyingGroupBase(BaseModel):
    name: str
    aliases: list[str] = []

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


class BuyingGroupCreate(BuyingGroupBase):
    user_id: int | None = None


class BuyingGroupUpdate(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None

    @field_validator("aliases", mode="before")
    @classmethod
    def normalize_aliases(cls, value: object) -> list[str] | None:
        if value is None:
            return None
        return BuyingGroupBase.normalize_aliases(value)


class BuyingGroupRead(BuyingGroupBase):
    id: int
    user_id: int | None = None

    model_config = ConfigDict(from_attributes=True)
