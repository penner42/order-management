"""Ignored shipping zip codes — skipped on automated import of new orders."""
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from app.database import Base


class IgnoredZipCode(Base):
    """Postal code whose shipping addresses should be ignored on import."""

    __tablename__ = "ignored_zip_codes"

    id = Column(Integer, primary_key=True, index=True)
    zip_code = Column(String(32), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
