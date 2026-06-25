"""Buying group model - group items are sold to."""
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class BuyingGroup(Base):
    """Buying group that items are sold to."""

    __tablename__ = "buying_groups"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)  # for multi-user
    name = Column(String(255), nullable=False)
    aliases = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="buying_groups")
    orders = relationship("Order", back_populates="buying_group")
    payments = relationship("Payment", back_populates="buying_group", cascade="all, delete-orphan")
