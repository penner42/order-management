"""Complete receipt inputs, retained independently of pending suggestions."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String

from app.database import Base


class ReceivingReceiptCache(Base):
    __tablename__ = "receiving_receipt_cache"

    buying_group_id = Column(Integer, ForeignKey("buying_groups.id", ondelete="CASCADE"), primary_key=True)
    provider_identity = Column(String(1024), nullable=False)
    payload = Column(JSON, nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False)
