"""Portal schemas."""
from pydantic import BaseModel, ConfigDict


class PortalBase(BaseModel):
    name: str


class PortalCreate(PortalBase):
    pass


class PortalUpdate(BaseModel):
    name: str | None = None


class PortalRead(PortalBase):
    id: int

    model_config = ConfigDict(from_attributes=True)
