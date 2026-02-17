"""Buying group schemas."""
from pydantic import BaseModel, ConfigDict


class BuyingGroupBase(BaseModel):
    name: str


class BuyingGroupCreate(BuyingGroupBase):
    user_id: int | None = None


class BuyingGroupUpdate(BaseModel):
    name: str | None = None


class BuyingGroupRead(BuyingGroupBase):
    id: int
    user_id: int | None = None

    model_config = ConfigDict(from_attributes=True)
