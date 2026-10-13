"""Persisted pending receiving-review snapshot per buying group."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class ReceivingReviewPending(Base):
    """Latest warehouse match snapshot awaiting user apply (one row per buying group)."""

    __tablename__ = "receiving_review_pending"

    buying_group_id = Column(
        Integer,
        ForeignKey("buying_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    payload = Column(JSON, nullable=False)
    matched_count = Column(Integer, nullable=False, default=0, server_default="0")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    buying_group = relationship("BuyingGroup", back_populates="receiving_review_pending")
