"""Store and store account models - where orders are purchased from."""
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Store(Base):
    """Store where orders are purchased from (e.g. Amazon, Target)."""

    __tablename__ = "stores"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="stores")
    accounts = relationship("StoreAccount", back_populates="store", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="store")
    payment_method_earnings = relationship(
        "PaymentMethodStoreEarnings",
        back_populates="store",
        cascade="all, delete-orphan",
    )


class StoreAccount(Base):
    """Account at a store - each store can have multiple accounts (e.g. Personal, Business)."""

    __tablename__ = "store_accounts"

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)  # e.g. "Personal", "Business"
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    store = relationship("Store", back_populates="accounts")
    orders = relationship("Order", back_populates="store_account")
