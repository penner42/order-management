"""User model - for future multi-user support."""
from __future__ import annotations

from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import Session, relationship
from sqlalchemy.sql import func
from app.database import Base

USER_ROLE_ADMIN = "admin"
USER_ROLE_USER = "user"


def get_default_app_user_id(db: Session) -> int | None:
    """First non-admin user (for orders, etc.); admin has no orders."""
    u = db.query(User).filter(User.role != USER_ROLE_ADMIN).first()
    return u.id if u else None


class User(Base):
    """User account. Admin has no orders; regular users own data."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(255), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=True)
    name = Column(String(255), nullable=True)
    role = Column(String(50), nullable=False, default=USER_ROLE_ADMIN, server_default="admin")
    hashed_password = Column(String(255), nullable=True)  # for future auth
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships (for future multi-user)
    buying_groups = relationship("BuyingGroup", back_populates="user")
    rewards = relationship("Reward", back_populates="user")
    payment_methods = relationship("PaymentMethod", back_populates="user")
    stores = relationship("Store", back_populates="user")
    orders = relationship("Order", back_populates="user")
    shipments = relationship("Shipment", back_populates="user")
