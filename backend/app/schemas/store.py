"""Store schemas."""
from pydantic import BaseModel, ConfigDict


class StoreBase(BaseModel):
    name: str


class StoreCreate(StoreBase):
    user_id: int | None = None


class StoreUpdate(BaseModel):
    name: str | None = None


class StoreRead(StoreBase):
    id: int
    user_id: int | None = None

    model_config = ConfigDict(from_attributes=True)


class StoreAccountBase(BaseModel):
    name: str


class StoreAccountCreate(StoreAccountBase):
    store_id: int | None = None  # optional when creating via POST /stores/{id}/accounts


class StoreAccountUpdate(BaseModel):
    name: str | None = None


class StoreAccountRead(StoreAccountBase):
    id: int
    store_id: int

    model_config = ConfigDict(from_attributes=True)
