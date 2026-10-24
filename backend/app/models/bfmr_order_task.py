"""Durable BFMR purchase registration and its remote-write journal."""
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, JSON, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base


class BfmrOrderTask(Base):
    __tablename__ = "bfmr_order_tasks"
    __table_args__ = (UniqueConstraint("order_id", "buying_group_id", "product_key"),)

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    buying_group_id = Column(Integer, ForeignKey("buying_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    product_key = Column(String(500), nullable=False)
    snapshot = Column(JSON, nullable=False)
    # Allocations journal each intent before POST, including pre-existing PIDs.
    allocations = Column(JSON, nullable=False, default=list)
    status = Column(String(30), nullable=False, default="pending", index=True)
    message = Column(Text, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
