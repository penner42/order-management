"""Shipment and shipment-item junction - items/orders can ship together."""
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Shipment(Base):
    """Shipment - can contain items from multiple orders."""

    __tablename__ = "shipments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    tracking_number = Column(String(255), nullable=True)
    shipped_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(String(1000), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="shipments")
    shipment_items = relationship("ShipmentItem", back_populates="shipment", cascade="all, delete-orphan")


class ShipmentItem(Base):
    """Junction: which items are in which shipment. Each item can only be in one shipment."""

    __tablename__ = "shipment_items"

    id = Column(Integer, primary_key=True, index=True)
    shipment_id = Column(Integer, ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False)
    item_id = Column(
        Integer,
        ForeignKey("items.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    shipment = relationship("Shipment", back_populates="shipment_items")
    item = relationship("Item", back_populates="shipment_items")
