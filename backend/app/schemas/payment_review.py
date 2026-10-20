"""Provider-neutral, versioned payment review edits."""
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field, ConfigDict


class Allocation(BaseModel):
    receipt_source_id: int
    item_id: int
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2, allow_inf_nan=False)
    quantity: int = Field(gt=0, strict=True)


class Correction(BaseModel):
    receipt_source_id: int
    item_id: int
    amount: Decimal = Field(max_digits=12, decimal_places=2, allow_inf_nan=False)
    explanation: str = Field(min_length=1, max_length=2000)


class ReviewContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receipt_ids: list[int] = Field(default_factory=list)
    allocations: list[Allocation] = Field(default_factory=list)
    corrections: list[Correction] = Field(default_factory=list)
    window_start: datetime | None = None
    window_end: datetime | None = None
    existing_payment_id: int | None = None
    payment_bonus: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2, allow_inf_nan=False)
    note: str = Field(default="", max_length=2000)
    manual: bool = True


class ReviewEdit(BaseModel):
    version: int
    content: ReviewContent
    rebuild_window: bool = False


class ReviewConfirm(BaseModel):
    version: int
    fingerprint: str
