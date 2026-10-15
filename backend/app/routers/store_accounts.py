"""Flat store accounts API - list all accounts across stores."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.auth import get_current_user
from app.models import StoreAccount, User
from app.schemas.store import StoreAccountRead

router = APIRouter(prefix="/store-accounts", tags=["store-accounts"])


@router.get("", response_model=list[StoreAccountRead])
def list_store_accounts(store_id: int | None = Query(None), db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    q = db.query(StoreAccount).order_by(StoreAccount.store_id, StoreAccount.name)
    if store_id is not None:
        q = q.filter(StoreAccount.store_id == store_id)
    return q.all()
