"""Reward schemas."""
from pydantic import BaseModel, ConfigDict


class RewardBase(BaseModel):
    name: str


class RewardCreate(RewardBase):
    user_id: int | None = None


class RewardUpdate(BaseModel):
    name: str | None = None


class RewardRead(RewardBase):
    id: int
    user_id: int | None = None

    model_config = ConfigDict(from_attributes=True)
