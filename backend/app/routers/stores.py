"""Stores and store accounts API."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.auth import get_current_user
from app.database import get_db
from app.models import User, Store, StoreAccount
from app.models.user import get_default_app_user_id
from app.schemas.store import (
    StoreRead,
    StoreCreate,
    StoreUpdate,
    StoreAccountRead,
    StoreAccountCreate,
    StoreAccountUpdate,
)

router = APIRouter(prefix="/stores", tags=["stores"])


# --- Stores ---


@router.get("", response_model=list[StoreRead])
def list_stores(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Store).order_by(Store.name).all()


@router.post("", response_model=StoreRead)
def create_store(data: StoreCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    user_id = data.user_id or (current_user.id if current_user.role != "admin" else None) or get_default_app_user_id(db)
    store = Store(**data.model_dump(exclude={"user_id"}), user_id=user_id)
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


@router.get("/{store_id}", response_model=StoreRead)
def get_store(store_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    return store


@router.patch("/{store_id}", response_model=StoreRead)
def update_store(store_id: int, data: StoreUpdate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(store, k, v)
    db.commit()
    db.refresh(store)
    return store


@router.delete("/{store_id}", status_code=204)
def delete_store(store_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    db.delete(store)
    db.commit()
    return None


# --- Store accounts ---


@router.get("/{store_id}/accounts", response_model=list[StoreAccountRead])
def list_store_accounts(store_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    return db.query(StoreAccount).filter(StoreAccount.store_id == store_id).order_by(StoreAccount.name).all()


@router.post("/{store_id}/accounts", response_model=StoreAccountRead)
def create_store_account(store_id: int, data: StoreAccountCreate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    account = StoreAccount(store_id=store_id, name=data.name)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.get("/{store_id}/accounts/{account_id}", response_model=StoreAccountRead)
def get_store_account(store_id: int, account_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    account = db.query(StoreAccount).filter(
        StoreAccount.id == account_id,
        StoreAccount.store_id == store_id,
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="Store account not found")
    return account


@router.patch("/{store_id}/accounts/{account_id}", response_model=StoreAccountRead)
def update_store_account(
    store_id: int, account_id: int, data: StoreAccountUpdate, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    account = db.query(StoreAccount).filter(
        StoreAccount.id == account_id,
        StoreAccount.store_id == store_id,
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="Store account not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(account, k, v)
    db.commit()
    db.refresh(account)
    return account


@router.delete("/{store_id}/accounts/{account_id}", status_code=204)
def delete_store_account(store_id: int, account_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    account = db.query(StoreAccount).filter(
        StoreAccount.id == account_id,
        StoreAccount.store_id == store_id,
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="Store account not found")
    db.delete(account)
    db.commit()
    return None
