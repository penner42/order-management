"""Payment method model - free-form label (credit card, PayPal, etc.)."""
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class PaymentMethod(Base):
    """Payment method - single label, e.g. "Visa ****1234", "PayPal - Amex".
    Can have a parent; sub-methods inherit user_id and reward_id from the parent.
    """

    __tablename__ = "payment_methods"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    reward_id = Column(Integer, ForeignKey("rewards.id", ondelete="SET NULL"), nullable=True)
    parent_id = Column(Integer, ForeignKey("payment_methods.id", ondelete="CASCADE"), nullable=True)
    label = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="payment_methods")
    reward = relationship("Reward", back_populates="payment_methods")
    parent = relationship("PaymentMethod", remote_side=[id], back_populates="sub_methods")
    sub_methods = relationship("PaymentMethod", back_populates="parent", cascade="all, delete-orphan")
    order_payments = relationship("OrderPaymentMethod", back_populates="payment_method")
    store_earnings = relationship(
        "PaymentMethodStoreEarnings",
        back_populates="payment_method",
        cascade="all, delete-orphan",
    )
