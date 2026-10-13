"""Cached USABG purchase-order detail payloads (avoid re-fetching GET /pos/{po})."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class UsabgPoCache(Base):
    """Durable cache of one USABG PO detail response per buying group + PO number."""

    __tablename__ = "usabg_po_cache"
    __table_args__ = (
        UniqueConstraint("buying_group_id", "po_number", name="uq_usabg_po_cache_bg_po"),
    )

    id = Column(Integer, primary_key=True, index=True)
    buying_group_id = Column(
        Integer,
        ForeignKey("buying_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    po_number = Column(String(64), nullable=False)
    receiving_status = Column(String(64), nullable=True)
    detail_json = Column(JSON, nullable=False)
    fetched_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    buying_group = relationship("BuyingGroup", back_populates="usabg_po_cache")
