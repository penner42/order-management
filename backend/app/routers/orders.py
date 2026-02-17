"""Orders API."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.auth import get_current_user
from app.database import get_db
from app.models import User, Order, Item, OrderPaymentMethod
from app.models.user import get_default_app_user_id
from app.schemas.order import OrderRead, OrderCreate, OrderUpdate, OrderPaymentMethodCreate
from app.schemas.item import ItemCreateNested

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("", response_model=list[OrderRead])
def list_orders(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Order).order_by(Order.created_at.desc()).all()


@router.post("", response_model=OrderRead)
def create_order(data: OrderCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    user_id = data.user_id or (current_user.id if current_user.role != "admin" else None) or get_default_app_user_id(db)
    order = Order(
        notes=data.notes,
        purchase_date=data.purchase_date,
        user_id=user_id,
        store_id=data.store_id,
        store_account_id=data.store_account_id,
        buying_group_id=data.buying_group_id,
        store_order_number=data.store_order_number,
    )
    db.add(order)
    db.flush()
    if data.payment_methods:
        seen = set()
        for pm in data.payment_methods:
            if pm.payment_method_id in seen:
                raise HTTPException(
                    status_code=400,
                    detail="Each payment method can only be used once per order.",
                )
            seen.add(pm.payment_method_id)
    for pm in data.payment_methods or []:
        opm = OrderPaymentMethod(
            order_id=order.id,
            payment_method_id=pm.payment_method_id,
            amount=pm.amount,
        )
        db.add(opm)
    for it in data.items or []:
        item = Item(
            order_id=order.id,
            **it.model_dump(exclude={"order_id"}),
        )
        db.add(item)
    db.commit()
    db.refresh(order)
    return order


@router.get("/{order_id}", response_model=OrderRead)
def get_order(order_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.patch("/{order_id}", response_model=OrderRead)
def update_order(order_id: int, data: OrderUpdate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    payload = data.model_dump(exclude_unset=True)
    payment_methods = payload.pop("payment_methods", None)
    for k, v in payload.items():
        setattr(order, k, v)
    if payment_methods is not None:
        seen = set()
        for pm in payment_methods:
            pid = pm["payment_method_id"]
            if pid in seen:
                raise HTTPException(
                    status_code=400,
                    detail="Each payment method can only be used once per order.",
                )
            seen.add(pid)
        for opm in order.order_payments:
            db.delete(opm)
        for pm in payment_methods:
            opm = OrderPaymentMethod(
                order_id=order.id,
                payment_method_id=pm["payment_method_id"],
                amount=pm.get("amount"),
            )
            db.add(opm)
    db.commit()
    db.refresh(order)
    return order


@router.delete("/{order_id}", status_code=204)
def delete_order(order_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    db.delete(order)
    db.commit()
    return None
