"""User API: current user (auth required)."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user, hash_password, verify_password
from app.database import get_db
from app.models import User, Order, OrderPaymentMethod
from app.schemas.user import UserRead
from app.schemas.order import OrderRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
def get_me(user: User = Depends(get_current_user)):
    """Return the currently authenticated user."""
    return user


@router.get("/me/orders", response_model=list[OrderRead])
def get_my_orders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all orders for the current user (for export)."""
    orders = (
        db.query(Order)
        .options(
            joinedload(Order.store),
            joinedload(Order.store_account),
            joinedload(Order.buying_group),
            joinedload(Order.items),
            joinedload(Order.order_payments).joinedload(OrderPaymentMethod.payment_method),
        )
        .filter(Order.user_id == current_user.id)
        .order_by(Order.created_at.desc())
        .all()
    )
    return orders


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.patch("/me/password", status_code=204)
def change_password(
    data: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change the current user's password. Requires current password."""
    if not user.hashed_password or not verify_password(data.current_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    user.hashed_password = hash_password(data.new_password)
    db.commit()
