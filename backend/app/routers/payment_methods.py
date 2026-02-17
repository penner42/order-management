"""Payment methods API."""
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from app.auth import get_current_user
from app.database import get_db
from app.models import User, PaymentMethod, PaymentMethodStoreEarnings, Reward, Store
from app.models.user import get_default_app_user_id
from app.schemas.payment_method import (
    PaymentMethodRead,
    PaymentMethodCreate,
    PaymentMethodUpdate,
    StoreEarningsEntry,
    StoreEarningsBulk,
    StoreEarningsBulkEntry,
)
from app.schemas.store import StoreRead

router = APIRouter(prefix="/payment-methods", tags=["payment-methods"])


def _validate_reward_id(db: Session, reward_id: int | None) -> None:
    if reward_id is not None:
        if not db.query(Reward).filter(Reward.id == reward_id).first():
            raise HTTPException(status_code=400, detail="Reward not found")


@router.get("", response_model=list[PaymentMethodRead])
def list_payment_methods(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return (
        db.query(PaymentMethod)
        .filter(PaymentMethod.parent_id.is_(None))
        .options(
            joinedload(PaymentMethod.reward),
            joinedload(PaymentMethod.sub_methods).joinedload(PaymentMethod.reward),
        )
        .order_by(PaymentMethod.id)
        .all()
    )


@router.post("", response_model=PaymentMethodRead)
def create_payment_method(data: PaymentMethodCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _validate_reward_id(db, data.reward_id)
    parent = None
    if data.parent_id is not None:
        parent = db.query(PaymentMethod).filter(PaymentMethod.id == data.parent_id).first()
        if not parent:
            raise HTTPException(status_code=400, detail="Parent payment method not found")
        if parent.parent_id is not None:
            raise HTTPException(status_code=400, detail="Sub-methods cannot have their own sub-methods")
    user_id = data.user_id or (current_user.id if current_user.role != "admin" else None) or get_default_app_user_id(db)
    reward_id = data.reward_id
    if parent is not None:
        user_id = parent.user_id
        if reward_id is None:
            reward_id = parent.reward_id
    payload = data.model_dump(exclude={"user_id", "reward_id"})
    pm = PaymentMethod(**payload, user_id=user_id, reward_id=reward_id)
    db.add(pm)
    db.commit()
    db.refresh(pm)
    q = db.query(PaymentMethod).options(joinedload(PaymentMethod.reward)).filter(PaymentMethod.id == pm.id)
    if pm.parent_id is None:
        q = q.options(joinedload(PaymentMethod.sub_methods).joinedload(PaymentMethod.reward))
    return q.first()


def _load_payment_method_query(db: Session, pm_id: int):
    return (
        db.query(PaymentMethod)
        .options(
            joinedload(PaymentMethod.reward),
            joinedload(PaymentMethod.sub_methods).joinedload(PaymentMethod.reward),
        )
        .filter(PaymentMethod.id == pm_id)
    )


@router.get("/{pm_id}", response_model=PaymentMethodRead)
def get_payment_method(pm_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    pm = _load_payment_method_query(db, pm_id).first()
    if not pm:
        raise HTTPException(status_code=404, detail="Payment method not found")
    return pm


@router.patch("/{pm_id}", response_model=PaymentMethodRead)
def update_payment_method(pm_id: int, data: PaymentMethodUpdate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    payload = data.model_dump(exclude_unset=True)
    if "reward_id" in payload and payload["reward_id"] is not None:
        _validate_reward_id(db, payload["reward_id"])
    pm = _load_payment_method_query(db, pm_id).first()
    if not pm:
        raise HTTPException(status_code=404, detail="Payment method not found")
    for k, v in payload.items():
        setattr(pm, k, v)
    db.commit()
    db.refresh(pm)
    return _load_payment_method_query(db, pm_id).first()


@router.delete("/{pm_id}", status_code=204)
def delete_payment_method(pm_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    pm = db.query(PaymentMethod).filter(PaymentMethod.id == pm_id).first()
    if not pm:
        raise HTTPException(status_code=404, detail="Payment method not found")
    if pm.order_payments:
        raise HTTPException(
            status_code=400,
            detail="This payment method is in use on one or more orders and cannot be removed.",
        )
    db.delete(pm)
    db.commit()
    return None


@router.get("/{pm_id}/store-earnings", response_model=list[StoreEarningsEntry])
def list_payment_method_store_earnings(
    pm_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    pm = db.query(PaymentMethod).filter(PaymentMethod.id == pm_id).first()
    if not pm:
        raise HTTPException(status_code=404, detail="Payment method not found")
    stores = db.query(Store).order_by(Store.name).all()
    earnings_map = {
        row.store_id: row.points_per_dollar
        for row in db.query(PaymentMethodStoreEarnings)
        .filter(PaymentMethodStoreEarnings.payment_method_id == pm_id)
        .all()
    }
    return [
        StoreEarningsEntry(
            store_id=s.id,
            store=StoreRead.model_validate(s),
            points_per_dollar=earnings_map.get(s.id, Decimal("0")),
        )
        for s in stores
    ]


@router.put("/{pm_id}/store-earnings", response_model=list[StoreEarningsEntry])
def update_payment_method_store_earnings(
    pm_id: int,
    data: StoreEarningsBulk,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    pm = db.query(PaymentMethod).filter(PaymentMethod.id == pm_id).first()
    if not pm:
        raise HTTPException(status_code=404, detail="Payment method not found")
    db.query(PaymentMethodStoreEarnings).filter(
        PaymentMethodStoreEarnings.payment_method_id == pm_id
    ).delete()
    for entry in data.store_earnings:
        db.add(
            PaymentMethodStoreEarnings(
                payment_method_id=pm_id,
                store_id=entry.store_id,
                points_per_dollar=entry.points_per_dollar,
            )
        )
    db.commit()
    stores = db.query(Store).order_by(Store.name).all()
    earnings_map = {
        row.store_id: row.points_per_dollar
        for row in db.query(PaymentMethodStoreEarnings)
        .filter(PaymentMethodStoreEarnings.payment_method_id == pm_id)
        .all()
    }
    return [
        StoreEarningsEntry(
            store_id=s.id,
            store=StoreRead.model_validate(s),
            points_per_dollar=earnings_map.get(s.id, Decimal("0")),
        )
        for s in stores
    ]
