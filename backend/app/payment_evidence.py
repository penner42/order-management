"""Provider-neutral, sanitized payment and receiving evidence."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


PAYMENT_IMPORT_START_YEAR = 2025


class ReceiptEvidence(BaseModel):
    external_id: str
    identity_key: str | None = None
    amount: Decimal
    currency: str = "USD"
    created_at: datetime | None = None
    sequence: int | None = None
    void: bool = False
    verified: bool = True
    quantity: int | None = None
    tracking_numbers: list[str] = Field(default_factory=list)
    description: str = ""
    details: list[dict] = Field(default_factory=list)


class PaymentEvidence(BaseModel):
    external_id: str
    identity_key: str | None = None
    amount: Decimal
    currency: str = "USD"
    created_at: datetime | None = None
    sent_at: datetime | None = None
    sequence: int | None = None
    status: str
    status_known: bool = True
    void: bool = False
    receipt_ids: list[str] | None = None
    receipt_amounts: dict[str, Decimal] | None = None
    warnings: list[str] = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)
    memo: str = ""


@dataclass
class PaymentFetch:
    payments: list[PaymentEvidence]
    receipts: list[ReceiptEvidence]
    complete: bool = True
    history_complete: bool = True
    warnings: list[str] = field(default_factory=list)
