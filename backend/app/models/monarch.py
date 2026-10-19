"""Monarch settings, imported evidence, and reversible receipt confirmations."""
from sqlalchemy import Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class MonarchConnection(Base):
    __tablename__ = "monarch_connections"
    __table_args__ = (CheckConstraint("id = 1", name="ck_monarch_singleton"),)
    id = Column(Integer, primary_key=True)  # singleton, always 1
    browser_profile_id = Column(Integer, ForeignKey("browser_profiles.id", ondelete="RESTRICT"), nullable=False, unique=True)
    selected_account_ids = Column(JSON, nullable=False, default=list)
    sender_rules = Column(JSON, nullable=False, default=dict)
    accounts = Column(JSON, nullable=False, default=list)
    daily_enabled = Column(Boolean, nullable=False, default=False)
    next_check_at = Column(DateTime(timezone=True))
    last_check_at = Column(DateTime(timezone=True))
    last_error = Column(Text)
    profile = relationship("BrowserProfile")


class MonarchDeposit(Base):
    __tablename__ = "monarch_deposits"
    id = Column(Integer, primary_key=True)
    transaction_id = Column(String(255), nullable=False, unique=True)
    account_id = Column(String(255), nullable=False, index=True)
    account_name = Column(String(255), nullable=False)
    posted_date = Column(Date, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    merchant = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    pending = Column(Boolean, nullable=False)
    dismissed = Column(Boolean, nullable=False, default=False)
    revision = Column(Integer, nullable=False, default=1)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class MonarchConfirmation(Base):
    __tablename__ = "monarch_confirmations"
    id = Column(Integer, primary_key=True)
    deposit_id = Column(Integer, ForeignKey("monarch_deposits.id", ondelete="RESTRICT"), nullable=False)
    active_deposit_id = Column(Integer, ForeignKey("monarch_deposits.id", ondelete="RESTRICT"), unique=True)
    reviewer_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    evidence = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    undone_at = Column(DateTime(timezone=True))
    undone_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))


class MonarchPaymentReceipt(Base):
    __tablename__ = "monarch_payment_receipts"
    payment_id = Column(Integer, ForeignKey("payments.id", ondelete="RESTRICT"), primary_key=True)
    confirmation_id = Column(Integer, ForeignKey("monarch_confirmations.id", ondelete="RESTRICT"), nullable=False)
    confirmation = relationship("MonarchConfirmation")

    @property
    def evidence(self):
        return {"confirmation_id": self.confirmation_id, **self.confirmation.evidence["deposit"]}
