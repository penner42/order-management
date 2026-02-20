"""Item model - line items on orders with status and resale info."""
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Numeric, Enum as SQLEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base


class ItemStatus(str, enum.Enum):
    PURCHASED = "purchased"
    SHIPPED = "shipped"
    SUBMITTED = "submitted"
    DELIVERED = "delivered"
    SCANNED = "scanned"
    PAYMENT_REQUESTED = "payment_requested"
    PAYMENT_SENT = "payment_sent"
    PAYMENT_RECEIVED = "payment_received"
    CANCELED = "canceled"
    NEEDS_RETURN = "needs_return"
    RETURN_STARTED = "return_started"
    RETURN_SENT = "return_sent"
    RETURN_RECEIVED = "return_received"
    RETURN_REFUNDED = "return_refunded"


class Item(Base):
    """Order item - resold with purchase/sell price and buying group."""

    __tablename__ = "items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    price_paid = Column(Numeric(12, 2), nullable=True)
    price_sold = Column(Numeric(12, 2), nullable=True)
    status = Column(
        SQLEnum(ItemStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=ItemStatus.PURCHASED,
    )
    quantity = Column(Integer, nullable=False, default=1)
    description = Column(String(500), nullable=True)
    shipping = Column(Numeric(12, 2), nullable=True)
    sales_tax = Column(Numeric(12, 2), nullable=True)
    submission_id = Column(String(255), nullable=True)
    receipt_id = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Datetime when each status was (last) set; not populated automatically yet
    purchased_at = Column(DateTime(timezone=True), nullable=True)
    shipped_at = Column(DateTime(timezone=True), nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    scanned_at = Column(DateTime(timezone=True), nullable=True)
    payment_requested_at = Column(DateTime(timezone=True), nullable=True)
    payment_sent_at = Column(DateTime(timezone=True), nullable=True)
    payment_received_at = Column(DateTime(timezone=True), nullable=True)
    canceled_at = Column(DateTime(timezone=True), nullable=True)
    needs_return_at = Column(DateTime(timezone=True), nullable=True)
    return_started_at = Column(DateTime(timezone=True), nullable=True)
    return_sent_at = Column(DateTime(timezone=True), nullable=True)
    return_received_at = Column(DateTime(timezone=True), nullable=True)
    return_refunded_at = Column(DateTime(timezone=True), nullable=True)

    order = relationship("Order", back_populates="items")
    shipment_items = relationship("ShipmentItem", back_populates="item", cascade="all, delete-orphan")
    payment_line_items = relationship("PaymentLineItem", back_populates="item", cascade="all, delete-orphan")