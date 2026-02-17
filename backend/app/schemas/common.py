"""Common schema types."""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class TimestampsMixin(BaseModel):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class DecimalStr(str):
    """Use for JSON-friendly decimal fields."""

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if isinstance(v, Decimal):
            return str(v)
        if isinstance(v, (int, float, str)):
            return str(v)
        raise ValueError("decimal or number expected")
