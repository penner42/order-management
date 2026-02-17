"""Buying groups API."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.auth import get_current_user
from app.database import get_db
from app.models import User, BuyingGroup
from app.models.user import get_default_app_user_id
from app.schemas.buying_group import BuyingGroupRead, BuyingGroupCreate, BuyingGroupUpdate

router = APIRouter(prefix="/buying-groups", tags=["buying-groups"])


@router.get("", response_model=list[BuyingGroupRead])
def list_buying_groups(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(BuyingGroup).all()


@router.post("", response_model=BuyingGroupRead)
def create_buying_group(data: BuyingGroupCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    user_id = (current_user.id if current_user.role != "admin" else None) or get_default_app_user_id(db)
    group = BuyingGroup(**data.model_dump(exclude={"user_id"}), user_id=user_id)
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


@router.get("/{group_id}", response_model=BuyingGroupRead)
def get_buying_group(group_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    group = db.query(BuyingGroup).filter(BuyingGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Buying group not found")
    return group


@router.patch("/{group_id}", response_model=BuyingGroupRead)
def update_buying_group(group_id: int, data: BuyingGroupUpdate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    group = db.query(BuyingGroup).filter(BuyingGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Buying group not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(group, k, v)
    db.commit()
    db.refresh(group)
    return group


@router.delete("/{group_id}", status_code=204)
def delete_buying_group(group_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    group = db.query(BuyingGroup).filter(BuyingGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Buying group not found")
    db.delete(group)
    db.commit()
    return None
