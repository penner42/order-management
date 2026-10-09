"""Ignored zip code schemas."""
from pydantic import BaseModel, ConfigDict, field_validator


class IgnoredZipCodeCreate(BaseModel):
    zip_code: str

    @field_validator("zip_code")
    @classmethod
    def strip_zip(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("zip_code must not be empty")
        return cleaned


class IgnoredZipCodeRead(BaseModel):
    id: int
    zip_code: str

    model_config = ConfigDict(from_attributes=True)
