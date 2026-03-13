"""Payments API."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError
from app.auth import get_current_user
from app.database import get_db
from app.models import User, Payment, PaymentLineItem, Item
from app.schemas.payment import (
    PaymentRead,
    PaymentCreate,
    PaymentUpdate,
    PaymentLineItemRead,
    PaymentLineItemCreate,
    PaymentLineItemUpdate,
)

router = APIRouter(prefix="/payments", tags=["payments"])


def _ensure_item_from_buying_group(item_id: int, buying_group_id: int, db: Session) -> Item:
    """Load item and ensure its order belongs to the given buying group. Returns item or raises 400/404."""
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not item.order:
        raise HTTPException(status_code=400, detail="Item has no order")
    if item.order.buying_group_id != buying_group_id:
        raise HTTPException(
            status_code=400,
            detail="Item must belong to an order in the same buying group as the payment.",
        )
    return item


def _get_item_totals(item: Item) -> tuple[float, float, float]:
    """Return (item_total, already_allocated, remaining) for an item."""
    price = float(item.price_sold or 0)
    qty = item.quantity or 1
    item_total = price * qty
    already_allocated = sum(float(li.amount or 0) for li in item.payment_line_items)
    remaining = max(item_total - already_allocated, 0.0)
    return item_total, already_allocated, remaining


@router.get("", response_model=list[PaymentRead])
def list_payments(
    buying_group_id: int | None = Query(None, description="Filter by buying group"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = (
        db.query(Payment)
        .options(
            joinedload(Payment.line_items).joinedload(PaymentLineItem.item),
        )
    )
    if buying_group_id is not None:
        q = q.filter(Payment.buying_group_id == buying_group_id)
    return q.order_by(Payment.created_at.desc()).all()


@router.post("", response_model=PaymentRead)
def create_payment(data: PaymentCreate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    payload = data.model_dump()
    if payload.get("payment_requested_at") is None:
        payload["payment_requested_at"] = datetime.now(timezone.utc)
    payment = Payment(**payload)
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


@router.get("/{payment_id}", response_model=PaymentRead)
def get_payment(payment_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment


@router.patch("/{payment_id}", response_model=PaymentRead)
def update_payment(
    payment_id: int,
    data: PaymentUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(payment, k, v)
    db.commit()
    db.refresh(payment)
    return payment


@router.delete("/{payment_id}", status_code=204)
def delete_payment(payment_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    db.delete(payment)
    db.commit()
    return None


@router.post("/{payment_id}/line-items", response_model=PaymentLineItemRead)
def add_payment_line_item(
    payment_id: int,
    data: PaymentLineItemCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    item = _ensure_item_from_buying_group(data.item_id, payment.buying_group_id, db)

    # Ensure we don't add the same item twice to the same payment
    existing_same_payment = (
        db.query(PaymentLineItem)
        .filter(
            PaymentLineItem.payment_id == payment_id,
            PaymentLineItem.item_id == data.item_id,
        )
        .first()
    )
    if existing_same_payment:
        raise HTTPException(
            status_code=400,
            detail="This item is already on this payment.",
        )

    # Determine allocation amount; default to remaining amount if not supplied
    _, _, remaining = _get_item_totals(item)
    if remaining <= 0:
        raise HTTPException(status_code=400, detail="This item is already fully paid.")

    amount = data.amount if data.amount is not None else remaining
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero.")
    if amount - remaining > 1e-6:
        raise HTTPException(
            status_code=400,
            detail="Amount exceeds remaining unpaid amount for this item.",
        )

    line_item = PaymentLineItem(payment_id=payment_id, item_id=data.item_id, amount=amount)
    db.add(line_item)
    if payment.payment_requested_at is None:
        payment.payment_requested_at = datetime.now(timezone.utc)
    try:
        db.commit()
        db.refresh(line_item)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="This item is already on a payment.",
        )
    return line_item


@router.delete("/{payment_id}/line-items/{line_item_id}", status_code=204)
def remove_payment_line_item(
    payment_id: int,
    line_item_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    line_item = (
        db.query(PaymentLineItem)
        .filter(PaymentLineItem.id == line_item_id, PaymentLineItem.payment_id == payment_id)
        .first()
    )
    if not line_item:
        raise HTTPException(status_code=404, detail="Line item not found")
    db.delete(line_item)
    db.commit()
    return None


@router.patch("/{payment_id}/line-items/{line_item_id}", response_model=PaymentLineItemRead)
def update_payment_line_item(
    payment_id: int,
    line_item_id: int,
    data: PaymentLineItemUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    line_item = (
        db.query(PaymentLineItem)
        .options(joinedload(PaymentLineItem.item).joinedload(Item.order))
        .filter(PaymentLineItem.id == line_item_id, PaymentLineItem.payment_id == payment_id)
        .first()
    )
    if not line_item:
        raise HTTPException(status_code=404, detail="Line item not found")

    update_data = data.amount
    if update_data is not None:
        item = line_item.item
        if not item:
            # Defensive: should not happen with current schema
            raise HTTPException(status_code=400, detail="Line item has no item")

        price = float(item.price_sold or 0)
        qty = item.quantity or 1
        item_total = price * qty

        # Sum allocations from other line items for this item (all payments)
        other_allocated = (
            db.query(PaymentLineItem)
            .filter(
                PaymentLineItem.item_id == item.id,
                PaymentLineItem.id != line_item.id,
            )
            .with_entities(PaymentLineItem.amount)
            .all()
        )
        already_allocated = sum(float(row[0] or 0) for row in other_allocated)
        remaining_max = max(item_total - already_allocated, 0.0)

        new_amount = float(update_data)
        if new_amount <= 0:
            raise HTTPException(status_code=400, detail="Amount must be greater than zero.")
        if new_amount - remaining_max > 1e-6:
            raise HTTPException(
                status_code=400,
                detail="Amount exceeds remaining unpaid amount for this item.",
            )

        line_item.amount = new_amount

    db.commit()
    db.refresh(line_item)
    return line_item
