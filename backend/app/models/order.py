"""Order and order-payment junction models."""
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Numeric
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Order(Base):
    """Order - can have multiple items and multiple payment methods."""

    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    store_id = Column(Integer, ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False)
    store_account_id = Column(Integer, ForeignKey("store_accounts.id", ondelete="SET NULL"), nullable=True)
    buying_group_id = Column(Integer, ForeignKey("buying_groups.id", ondelete="SET NULL"), nullable=True)
    store_order_number = Column(String(255), nullable=True)  # order number from the store (e.g. Amazon order #)
    purchase_date = Column(DateTime(timezone=True), nullable=True)  # when the order was placed (order-level)
    notes = Column(String(2000), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="orders")
    store = relationship("Store", back_populates="orders")
    store_account = relationship("StoreAccount", back_populates="orders")
    buying_group = relationship("BuyingGroup", back_populates="orders")
    items = relationship("Item", back_populates="order", cascade="all, delete-orphan")
    order_payments = relationship("OrderPaymentMethod", back_populates="order", cascade="all, delete-orphan")


class OrderPaymentMethod(Base):
    """Which payment method(s) were used for an order, and optional amount."""

    __tablename__ = "order_payment_methods"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    payment_method_id = Column(Integer, ForeignKey("payment_methods.id", ondelete="CASCADE"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=True)  # amount charged to this method
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    order = relationship("Order", back_populates="order_payments")
    payment_method = relationship("PaymentMethod", back_populates="order_payments")
