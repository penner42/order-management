"""Shared helpers for splitting item quantities."""
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.models import Item, PaymentLineItem


_CENT = Decimal("0.01")


def redistribute_item_payment_allocations(
    db: Session,
    *,
    original_item: Item,
    split_off_item: Item,
    original_quantity: int,
    kept_quantity: int,
) -> None:
    """Split every payment allocation in proportion to the retained quantity.

    The rounded retained and split-off amounts always add back to the original
    allocation. Zero-value fragments are omitted because payment line items are
    expected to represent positive allocations.
    """
    allocations = (
        db.query(PaymentLineItem)
        .filter(PaymentLineItem.item_id == original_item.id)
        .all()
    )
    if not allocations:
        return

    quantity = Decimal(original_quantity)
    kept = Decimal(kept_quantity)
    for allocation in allocations:
        original_amount = Decimal(allocation.amount)
        kept_amount = (original_amount * kept / quantity).quantize(
            _CENT,
            rounding=ROUND_HALF_UP,
        )
        split_off_amount = original_amount - kept_amount

        if kept_amount > 0:
            allocation.amount = kept_amount
        else:
            db.delete(allocation)

        if split_off_amount > 0:
            db.add(
                PaymentLineItem(
                    payment_id=allocation.payment_id,
                    item_id=split_off_item.id,
                    amount=split_off_amount,
                )
            )
