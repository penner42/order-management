"""Captures of meaningful retailer order changes, with refreshable image framing."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String
from app.database import Base


class OrderScreenshot(Base):
    __tablename__ = "order_screenshots"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    captured_at = Column(DateTime(timezone=True), nullable=False)
    filename = Column(String(64), nullable=False, unique=True)
    fingerprint = Column(String(64), nullable=False)
    snapshot = Column(JSON, nullable=False)
    summary = Column(String(1000), nullable=False)
    layout_version = Column(Integer, nullable=False, default=1, server_default="1")
