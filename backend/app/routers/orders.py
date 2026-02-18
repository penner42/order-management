"""Orders API."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.auth import get_current_user
from app.database import get_db
from app.models import User, Order, Item, OrderPaymentMethod
from app.models.user import get_default_app_user_id
from app.schemas.order import OrderRead, OrderCreate, OrderUpdate, OrderPaymentMethodCreate
from app.schemas.item import ItemCreateNested

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("", response_model=list[OrderRead])
def list_orders(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    order_status: str | None = Query(default=None, alias="order_status"),  # when "imported", only imported; else exclude imported
    status: list[str] = Query(default=[], alias="status"),
    buying_group_id: list[int] = Query(default=[], alias="buying_group_id"),
    date_from: str | None = None,
    date_to: str | None = None,
):
    # Order-level filters: which orders to include
    q = db.query(Order).order_by(Order.created_at.desc())
    if order_status == "imported":
        q = q.filter(Order.status == "imported")
    else:
        # Default: exclude imported orders from main list
        q = q.filter(Order.status != "imported")
    if status:
        # Only orders that have at least one item with one of these statuses
        q = q.join(Item).filter(Item.status.in_(status)).distinct()
    if buying_group_id:
        q = q.filter(Order.buying_group_id.in_(buying_group_id))
    if date_from:
        try:
            d = date.fromisoformat(date_from)
            q = q.filter(func.date(Order.purchase_date) >= d)
        except ValueError:
            pass
    if date_to:
        try:
            d = date.fromisoformat(date_to)
            q = q.filter(func.date(Order.purchase_date) <= d)
        except ValueError:
            pass
    orders = q.all()
    # Line-item filter: within each order, only include items matching status (keeps order grouping)
    if status:
        status_set = set(status)
        result = []
        for o in orders:
            read = OrderRead.model_validate(o)
            filtered_items = [i for i in read.items if i.status in status_set]
            result.append(read.model_copy(update={"items": filtered_items}))
        return result
    return orders


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
        status=getattr(data, "status", "active") or "active",
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
