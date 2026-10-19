"""Persistent browser sessions owned by stores or store accounts."""
from sqlalchemy import Boolean, Column, Integer, String, Text, ForeignKey, DateTime, UniqueConstraint, CheckConstraint, Index, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class BrowserProfile(Base):
    """One automated browser profile per store or retailer login account."""

    __tablename__ = "browser_profiles"
    __table_args__ = (
        CheckConstraint("(integration IS NOT NULL AND integration = 'monarch' AND retailer = 'monarch' AND store_id IS NULL AND store_account_id IS NULL) OR (integration IS NULL AND store_id IS NOT NULL AND retailer <> 'monarch')", name="ck_browser_profile_owner"),
        UniqueConstraint("store_account_id", name="uq_browser_profiles_store_account_id"),
        Index("uq_browser_profiles_direct_store", "store_id", unique=True,
              postgresql_where=text("store_account_id IS NULL"),
              sqlite_where=text("store_account_id IS NULL")),
    )

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id", ondelete="CASCADE"), nullable=True, index=True)
    integration = Column(String(64), nullable=True, unique=True)
    store_account_id = Column(
        Integer,
        ForeignKey("store_accounts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # Retailer key for the scraper (amazon | walmart | costco). Independent of stores.name.
    retailer = Column(String(64), nullable=False, default="walmart")
    # logged_out | ready | login_required | login_in_progress | importing | error
    status = Column(String(32), nullable=False, default="logged_out")
    last_error = Column(Text, nullable=True)
    last_import_at = Column(DateTime(timezone=True), nullable=True)

    # Full check: scan first N order-history pages on a cron schedule (local time).
    full_check_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    full_check_cron = Column(String(64), nullable=False, default="0 0 * * *", server_default="0 0 * * *")
    full_check_max_pages = Column(Integer, nullable=False, default=3, server_default="3")
    full_check_last_run_at = Column(DateTime(timezone=True), nullable=True)

    # Unshipped check: re-fetch detail for all account orders with unshipped items.
    unshipped_check_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    unshipped_check_cron = Column(String(64), nullable=False, default="0 */6 * * *", server_default="0 */6 * * *")
    unshipped_check_last_run_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    store_account = relationship("StoreAccount", back_populates="browser_profile")
    store = relationship("Store", back_populates="browser_profiles")
