"""Typed inputs for the Monarch integration. Monetary outputs use decimal strings."""
from datetime import date
from pydantic import BaseModel, Field, model_validator


class MonarchSettingsUpdate(BaseModel):
    selected_account_ids: list[str] = Field(max_length=100)
    sender_rules: dict[str, list[str]]
    daily_enabled: bool = False


class MonarchCheckRequest(BaseModel):
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def validate_range(self):
        if bool(self.start_date) != bool(self.end_date):
            raise ValueError("Supply both start and end dates")
        if self.start_date and self.start_date > self.end_date:
            raise ValueError("Start date must precede end date")
        return self


class PaymentSelection(BaseModel):
    id: int
    fingerprint: str


class MonarchConfirmRequest(BaseModel):
    revision: int
    payments: list[PaymentSelection] = Field(min_length=1, max_length=200)


class MonarchDismissRequest(BaseModel):
    dismissed: bool
