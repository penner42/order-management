"""Payment method per-store earnings (e.g. points per dollar)."""
from sqlalchemy import Column, Integer, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base


class PaymentMethodStoreEarnings(Base):
    """Earnings rate for a payment method at a specific store (e.g. points per dollar)."""

    __tablename__ = "payment_method_store_earnings"

    payment_method_id = Column(
        Integer,
        ForeignKey("payment_methods.id", ondelete="CASCADE"),
        nullable=False,
        primary_key=True,
    )
    store_id = Column(
        Integer,
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=False,
        primary_key=True,
    )
    points_per_dollar = Column(Numeric(10, 4), nullable=False, default=0)

    payment_method = relationship("PaymentMethod", back_populates="store_earnings")
    store = relationship("Store", back_populates="payment_method_earnings")
