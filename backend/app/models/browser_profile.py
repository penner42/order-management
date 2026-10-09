"""Browser automation profiles — persistent Camoufox sessions per store account."""
from sqlalchemy import Boolean, Column, Integer, String, Text, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class BrowserProfile(Base):
    """One automated browser profile tied to a store account (retailer login identity)."""

    __tablename__ = "browser_profiles"
    __table_args__ = (UniqueConstraint("store_account_id", name="uq_browser_profiles_store_account_id"),)

    id = Column(Integer, primary_key=True, index=True)
    store_account_id = Column(
        Integer,
        ForeignKey("store_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Retailer key for the scraper (amazon | walmart). Independent of stores.name.
    retailer = Column(String(64), nullable=False, default="walmart")
    # logged_out | ready | login_required | login_in_progress | importing | error
    status = Column(String(32), nullable=False, default="logged_out")
    last_error = Column(Text, nullable=True)
    last_import_at = Column(DateTime(timezone=True), nullable=True)

    # Full check: scan first N order-history pages on an interval.
    full_check_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    full_check_interval_hours = Column(Integer, nullable=False, default=24, server_default="24")
    full_check_max_pages = Column(Integer, nullable=False, default=3, server_default="3")
    full_check_last_run_at = Column(DateTime(timezone=True), nullable=True)

    # Unshipped check: re-fetch detail for all account orders with unshipped items.
    unshipped_check_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    unshipped_check_interval_hours = Column(Integer, nullable=False, default=6, server_default="6")
    unshipped_check_last_run_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    store_account = relationship("StoreAccount", back_populates="browser_profile")
