"""Buying group model - group items are sold to."""
from sqlalchemy import Boolean, Column, Integer, String, ForeignKey, DateTime, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class BuyingGroup(Base):
    """Buying group that items are sold to."""

    __tablename__ = "buying_groups"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)  # for multi-user
    name = Column(String(255), nullable=False)
    aliases = Column(JSON, nullable=False, default=list)
    api_framework = Column(String(50), nullable=True)  # e.g. "parsefile", "usabg", "buyinggroup", "bfmr"
    base_url = Column(String(500), nullable=True)
    api_url = Column(String(500), nullable=True)  # optional path relative to base_url
    bearer_token = Column(String(2000), nullable=True)
    api_user_id = Column(Integer, nullable=True)  # Parsefile account user id
    api_email = Column(String(255), nullable=True)  # Parsefile account email
    api_username = Column(String(255), nullable=True)  # USABG / BuyingGroup.com login / BFMR API-KEY
    api_password = Column(String(500), nullable=True)  # login password / BFMR API-SECRET
    # Batch-submit shipped tracking numbers to the buying-group API on a cron (local time).
    tracking_submit_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    tracking_submit_cron = Column(
        String(64), nullable=False, default="0 */6 * * *", server_default="0 */6 * * *"
    )
    tracking_submit_last_run_at = Column(DateTime(timezone=True), nullable=True)
    # Legacy schedule columns (unused): receiving now runs as a follow-up after tracking submit.
    received_check_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    received_check_cron = Column(
        String(64), nullable=False, default="0 */6 * * *", server_default="0 */6 * * *"
    )
    received_check_last_run_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="buying_groups")
    receiving_review_pending = relationship(
        "ReceivingReviewPending",
        back_populates="buying_group",
        uselist=False,
        cascade="all, delete-orphan",
    )
    usabg_po_cache = relationship(
        "UsabgPoCache",
        back_populates="buying_group",
        cascade="all, delete-orphan",
    )
    orders = relationship("Order", back_populates="buying_group")
    payments = relationship("Payment", back_populates="buying_group", cascade="all, delete-orphan")
