"""Payment and payment line item models."""
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Payment(Base):
    """Payment - linked to a single buying group; can include multiple line items (items from that group).
    Status is derived from which of payment_requested_at, payment_sent_at, payment_received_at are set (requested -> sent -> received).
    """

    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    buying_group_id = Column(Integer, ForeignKey("buying_groups.id", ondelete="RESTRICT"), nullable=False)
    payment_id = Column(String(255), nullable=True)  # External reference; not unique
    payment_requested_at = Column(DateTime(timezone=True), nullable=False)
    payment_sent_at = Column(DateTime(timezone=True), nullable=True)
    payment_received_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    buying_group = relationship("BuyingGroup", back_populates="payments")
    line_items = relationship(
        "PaymentLineItem",
        back_populates="payment",
        cascade="all, delete-orphan",
    )


class PaymentLineItem(Base):
    """Line item on a payment - references an Item. Item must belong to an order with same buying_group as the payment."""

    __tablename__ = "payment_line_items"

    id = Column(Integer, primary_key=True, index=True)
    payment_id = Column(Integer, ForeignKey("payments.id", ondelete="CASCADE"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id", ondelete="CASCADE"), nullable=False)

    __table_args__ = (
        UniqueConstraint("payment_id", "item_id", name="uq_payment_line_item_payment_item"),
        UniqueConstraint("item_id", name="uq_payment_line_item_item"),
    )

    payment = relationship("Payment", back_populates="line_items")
    item = relationship("Item", back_populates="payment_line_items")
