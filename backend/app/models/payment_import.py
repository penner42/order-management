"""Provider-neutral payment evidence and reviewed receipt coverage."""
from sqlalchemy import Column, Integer, String, ForeignKey, JSON, Boolean, Numeric, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base


class PaymentSource(Base):
    __tablename__ = "payment_sources"
    id = Column(Integer, primary_key=True)
    buying_group_id = Column(Integer, ForeignKey("buying_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String(64), nullable=False)
    kind = Column(String(16), nullable=False)
    external_id = Column(String(255), nullable=False)
    payload = Column(JSON, nullable=False)
    revision = Column(String(64), nullable=False)
    present = Column(Boolean, nullable=False, default=True)
    revisions = Column(JSON, nullable=False, default=list, server_default="[]")
    __table_args__ = (UniqueConstraint("buying_group_id", "provider", "kind", "external_id", name="uq_payment_source_identity"),)


class PaymentReview(Base):
    __tablename__ = "payment_reviews"
    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("payment_sources.id", ondelete="CASCADE"), nullable=False, unique=True)
    version = Column(Integer, nullable=False, default=1)
    content = Column(JSON, nullable=False, default=dict)
    payment_id = Column(Integer, ForeignKey("payments.id", ondelete="RESTRICT"), nullable=True, unique=True)
    confirmed_revision = Column(String(64), nullable=True)
    source = relationship("PaymentSource")


class PaymentCoverage(Base):
    __tablename__ = "payment_receipt_coverage"
    id = Column(Integer, primary_key=True)
    receipt_source_id = Column(Integer, ForeignKey("payment_sources.id", ondelete="RESTRICT"), nullable=False, index=True)
    payment_id = Column(Integer, ForeignKey("payments.id", ondelete="RESTRICT"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("items.id", ondelete="RESTRICT"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    quantity = Column(Integer, nullable=False)
    snapshot = Column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint("receipt_source_id", "payment_id", "item_id", name="uq_receipt_payment_coverage_item"),)


class PaymentCorrection(Base):
    __tablename__ = "payment_corrections"
    id = Column(Integer, primary_key=True)
    receipt_source_id = Column(Integer, ForeignKey("payment_sources.id", ondelete="RESTRICT"), nullable=False, index=True)
    payment_id = Column(Integer, ForeignKey("payments.id", ondelete="RESTRICT"), nullable=False, index=True)
    origin_payment_id = Column(Integer, ForeignKey("payments.id", ondelete="RESTRICT"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id", ondelete="RESTRICT"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    explanation = Column(String(2000), nullable=False)
    source_revision = Column(String(64), nullable=False)
    payment = relationship("Payment", foreign_keys=[payment_id], back_populates="corrections")
