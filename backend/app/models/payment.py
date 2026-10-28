"""Payment and payment line item models."""
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, UniqueConstraint, Numeric, JSON, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Payment(Base):
    """Payment - linked to a single buying group; can include multiple line items (items from that group).
    Status is derived from which of payment_requested_at, payment_sent_at, payment_received_at are set (requested -> sent -> received).
    """

    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    buying_group_id = Column(Integer, ForeignKey("buying_groups.id", ondelete="RESTRICT"), nullable=False)
    payment_id = Column(String(255), nullable=True)  # External reference; not unique
    payment_bonus = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    source_amount = Column(Numeric(12, 2), nullable=True)
    ledger_state = Column(String(16), nullable=False, default="legacy", server_default="legacy")
    allocation_manual = Column(Boolean, nullable=False, default=False, server_default="false")
    allocation_version = Column(Integer, nullable=False, default=1, server_default="1")
    allocation_details = Column(JSON, nullable=False, default=dict, server_default="{}")
    sync_issues = Column(JSON, nullable=False, default=list, server_default="[]")
    payment_requested_at = Column(DateTime(timezone=True), nullable=False)
    payment_sent_at = Column(DateTime(timezone=True), nullable=True)
    payment_received_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    buying_group = relationship("BuyingGroup", back_populates="payments")
    corrections = relationship("PaymentCorrection", foreign_keys="PaymentCorrection.payment_id", back_populates="payment")

    @property
    def total_amount(self):
        from app.payment_totals import payment_amount
        return payment_amount(self)

    @property
    def allocation_breakdown(self):
        from app.payment_ledger import breakdown
        return breakdown(self)

    @property
    def receipt_conflict(self):
        from app.payment_ledger import receipt_conflict
        return receipt_conflict(self)

    @property
    def effective_received_at(self):
        return self.payment_received_at if self.monarch_receipt_link and not self.receipt_conflict else None

    @property
    def source_details(self):
        from sqlalchemy.orm import object_session
        from app.models.payment_import import PaymentReview, PaymentCoverage, PaymentSource
        db = object_session(self)
        if not db:
            return None
        review = db.query(PaymentReview).filter_by(payment_id=self.id).first()
        if not review:
            return None
        coverage = db.query(PaymentCoverage).filter_by(payment_id=self.id).all()
        evidence = [db.get(PaymentSource, rid) for rid in review.content.get("receipt_ids", [])]
        return {"provider": review.source.provider, "external_id": review.source.external_id,
                "revision": review.source.revision, "present": review.source.present,
                "source": review.source.payload,
                "receipt_evidence": [{"source_id": r.id, "external_id": r.payload["external_id"],
                    "amount": r.payload["amount"], "quantity": r.payload.get("quantity"),
                    "description": r.payload.get("description", ""), "created_at": r.payload.get("created_at"),
                    "present": r.present, "details": r.payload.get("details", [])} for r in evidence if r],
                "review_id": review.id, "receipts": [{"source_id": c.receipt_source_id,
                "item_id": c.item_id, "amount": str(c.amount), "quantity": c.quantity,
                "snapshot": c.snapshot} for c in coverage]}
    monarch_receipt_link = relationship("MonarchPaymentReceipt", uselist=False)

    @property
    def monarch_receipt(self):
        return self.monarch_receipt_link.evidence if self.monarch_receipt_link else None

    line_items = relationship(
        "PaymentLineItem",
        back_populates="payment",
        cascade="all, delete-orphan",
    )


class PaymentLineItem(Base):
    """Line item on a payment - references an Item. Item must belong to an order with same buying_group as the payment."""

    __tablename__ = "payment_line_items"

    id = Column(Integer, primary_key=True, index=True)
    payment_id = Column(Integer, ForeignKey("payments.id", ondelete="CASCADE"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    # Allocated amount of this item's payout that this payment covers.
    # Multiple PaymentLineItem rows across different payments can sum to the full item total.
    amount = Column(Numeric(12, 2), nullable=False)

    __table_args__ = (
        UniqueConstraint("payment_id", "item_id", name="uq_payment_line_item_payment_item"),
    )

    payment = relationship("Payment", back_populates="line_items")
    item = relationship("Item", back_populates="payment_line_items")
