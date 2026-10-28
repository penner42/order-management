"""One calculation for payment totals and signed item coverage."""
from decimal import Decimal
from sqlalchemy import func


def payment_amount(payment):
    if payment.source_amount is not None:
        return Decimal(payment.source_amount)
    return (sum((Decimal(li.amount) for li in payment.line_items), Decimal(0))
            + Decimal(payment.payment_bonus or 0)
            + sum((Decimal(c.amount) for c in payment.corrections), Decimal(0)))


def item_allocated(db, item_id, exclude_payment_id=None):
    from app.models import PaymentLineItem, PaymentCorrection
    total = Decimal(0)
    for model in (PaymentLineItem, PaymentCorrection):
        q = db.query(func.coalesce(func.sum(model.amount), 0)).filter(model.item_id == item_id)
        if exclude_payment_id is not None:
            q = q.filter(model.payment_id != exclude_payment_id)
        total += Decimal(q.scalar())
    return total
