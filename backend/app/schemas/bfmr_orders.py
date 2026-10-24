from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class BfmrOrderTaskRead(BaseModel):
    id: int
    order_id: int
    buying_group_id: int
    product_key: str
    snapshot: dict[str, Any]
    allocations: list[dict[str, Any]]
    status: str
    message: str | None = None
    attempts: int
    next_attempt_at: datetime | None = None
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("snapshot")
    def public_snapshot(self, value):
        return {key: item for key, item in value.items() if key != "account"}

    @field_serializer("allocations")
    def public_allocations(self, value):
        return [{key: item for key, item in allocation.items() if key in {"reserve_id", "qty", "purchase_id", "status"}}
                for allocation in value]


class BfmrAllocation(BaseModel):
    reserve_id: str = Field(min_length=1, max_length=255)
    qty: int = Field(gt=0, strict=True)


class BfmrAllocateRequest(BaseModel):
    allocations: list[BfmrAllocation] = Field(min_length=1, max_length=100)
