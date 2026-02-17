"""Payments API."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
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


@router.get("", response_model=list[PaymentRead])
def list_payments(
    buying_group_id: int | None = Query(None, description="Filter by buying group"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(Payment)
    if buying_group_id is not None:
        q = q.filter(Payment.buying_group_id == buying_group_id)
    return q.order_by(Payment.created_at.desc()).all()


@router.post("", response_model=PaymentRead)
def create_payment(data: PaymentCreate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    payment = Payment(**data.model_dump())
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
    _ensure_item_from_buying_group(data.item_id, payment.buying_group_id, db)
    line_item = PaymentLineItem(payment_id=payment_id, item_id=data.item_id)
    db.add(line_item)
    try:
        db.commit()
        db.refresh(line_item)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="This item is already on the payment.",
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
