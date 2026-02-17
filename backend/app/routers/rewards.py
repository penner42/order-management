"""Rewards API."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.auth import get_current_user
from app.database import get_db
from app.models import User, Reward
from app.models.user import get_default_app_user_id
from app.schemas.reward import RewardRead, RewardCreate, RewardUpdate

router = APIRouter(prefix="/rewards", tags=["rewards"])


@router.get("", response_model=list[RewardRead])
def list_rewards(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Reward).all()


@router.post("", response_model=RewardRead)
def create_reward(data: RewardCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    user_id = (current_user.id if current_user.role != "admin" else None) or get_default_app_user_id(db)
    reward = Reward(**data.model_dump(exclude={"user_id"}), user_id=user_id)
    db.add(reward)
    db.commit()
    db.refresh(reward)
    return reward


@router.get("/{reward_id}", response_model=RewardRead)
def get_reward(reward_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    reward = db.query(Reward).filter(Reward.id == reward_id).first()
    if not reward:
        raise HTTPException(status_code=404, detail="Reward not found")
    return reward


@router.patch("/{reward_id}", response_model=RewardRead)
def update_reward(reward_id: int, data: RewardUpdate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    reward = db.query(Reward).filter(Reward.id == reward_id).first()
    if not reward:
        raise HTTPException(status_code=404, detail="Reward not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(reward, k, v)
    db.commit()
    db.refresh(reward)
    return reward


@router.delete("/{reward_id}", status_code=204)
def delete_reward(reward_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    reward = db.query(Reward).filter(Reward.id == reward_id).first()
    if not reward:
        raise HTTPException(status_code=404, detail="Reward not found")
    db.delete(reward)
    db.commit()
    return None
