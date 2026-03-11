"""Store order import model - normalized payloads from external stores (e.g. Walmart)."""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class StoreOrderImport(Base):
    """Snapshot of an external store order payload, pending review/apply in the app."""

    __tablename__ = "store_order_imports"
    __table_args__ = (
        UniqueConstraint(
            "store",
            "external_order_id",
            name="uq_store_order_imports_store_external_order_id",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)

    # External store identifier (e.g. "walmart", "costco")
    store = Column(String(50), nullable=False)

    # Raw external order id from the store (e.g. Walmart order.id) - primary match key
    external_order_id = Column(String(255), nullable=False)
    external_order_url = Column(String(1000), nullable=True)

    # pending | applied | discarded | failed
    status = Column(String(20), nullable=False, default="pending", server_default="pending")

    # Optional link to an internal order, if matched or created
    linked_order_id = Column(
        Integer,
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Full external payloads (raw from store; normalized shape used by app)
    raw_payload_json = Column(JSON, nullable=False)
    normalized_payload_json = Column(JSON, nullable=False)

    # Structured diff between current internal state and normalized payload (optional)
    diff_json = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    applied_at = Column(DateTime(timezone=True), nullable=True)
    discarded_at = Column(DateTime(timezone=True), nullable=True)

    applied_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    order = relationship("Order", backref="store_order_imports")
    applied_by_user = relationship("User")

