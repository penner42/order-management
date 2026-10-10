"""Persisted log of browser-automation import / tracking updates."""
from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class BrowserImportLog(Base):
    """One row per browser-automation or buying-group log event."""

    __tablename__ = "browser_import_logs"

    id = Column(Integer, primary_key=True, index=True)
    browser_profile_id = Column(
        Integer,
        ForeignKey("browser_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    job_id = Column(String(64), nullable=True, index=True)
    # stores | groups
    category = Column(
        String(16), nullable=False, default="stores", server_default="stores", index=True
    )
    # updates | info | error
    level = Column(String(16), nullable=False, default="info", server_default="info", index=True)
    # stores: order_imported | tracking_updated | order_checked | order_marked_personal |
    #   order_skipped_ignored_zip (legacy) | order_error | check_started | check_finished
    # groups: tracking_submitted | tracking_submit_error
    event_type = Column(String(32), nullable=False, index=True)
    # stores: full | unshipped ; groups: tracking_submit
    mode = Column(String(32), nullable=False)
    scheduled = Column(Boolean, nullable=False, default=False, server_default="false")
    retailer = Column(String(64), nullable=False)
    store_order_number = Column(String(255), nullable=True, index=True)
    order_id = Column(
        Integer,
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # JSON list of tracking numbers added (or present on import).
    tracking_numbers = Column(Text, nullable=True)
    message = Column(Text, nullable=True)
    store_name = Column(String(255), nullable=True)
    store_account_name = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    browser_profile = relationship("BrowserProfile")
    order = relationship("Order")
