"""Buying groups API."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import SessionLocal, get_db
from app.models import User, BuyingGroup
from app.models.user import get_default_app_user_id
from app.schemas.buying_group import (
    DEFAULT_TRACKING_SUBMIT_CRON,
    BuyingGroupCreate,
    BuyingGroupRead,
    BuyingGroupSubmitTrackingResponse,
    BuyingGroupUpdate,
)
from app.utils.buying_group_apis.submit_tracking import (
    batch_submit_pending_trackings,
    parsefile_group_ready,
)

logger = logging.getLogger(__name__)

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


def _run_tracking_submit(db: Session, group: BuyingGroup) -> BuyingGroupSubmitTrackingResponse:
    """Batch-submit pending trackings and stamp last_run_at."""
    ready, reason = parsefile_group_ready(group)
    if not ready:
        raise HTTPException(status_code=400, detail=reason or "Buying group API is not configured")

    result = batch_submit_pending_trackings(db, buying_group_id=group.id)
    now = datetime.now(timezone.utc)
    # Re-load after batch_submit may have committed/rolled back.
    group = db.query(BuyingGroup).filter(BuyingGroup.id == group.id).first()
    if group:
        group.tracking_submit_last_run_at = now
        db.commit()
        db.refresh(group)

    if result.error:
        raise HTTPException(status_code=502, detail=result.error)

    return BuyingGroupSubmitTrackingResponse(
        buying_group_id=result.buying_group_id,
        submitted_count=result.submitted_count,
        message=result.message
        or (
            f"Submitted {result.submitted_count} tracking number(s)."
            if result.submitted_count
            else "No pending tracking numbers to submit."
        ),
        tracking_numbers=result.tracking_numbers,
        tracking_submit_last_run_at=group.tracking_submit_last_run_at if group else now,
    )


@router.post("/{group_id}/submit-trackings", response_model=BuyingGroupSubmitTrackingResponse)
def submit_buying_group_trackings(
    group_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Batch-submit all shipped items with tracking for this buying group."""
    group = db.query(BuyingGroup).filter(BuyingGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Buying group not found")
    return _run_tracking_submit(db, group)


def list_due_tracking_submit_group_ids(db: Session) -> list[int]:
    """Return buying group ids whose tracking-submit schedule is due."""
    from app.browser_automation.scheduler import schedule_due

    now = datetime.now(timezone.utc)
    rows = (
        db.query(BuyingGroup)
        .filter(BuyingGroup.tracking_submit_enabled.is_(True))
        .order_by(BuyingGroup.id)
        .all()
    )
    out: list[int] = []
    for group in rows:
        ready, _ = parsefile_group_ready(group)
        if not ready:
            continue
        if schedule_due(
            enabled=True,
            cron_expr=str(group.tracking_submit_cron or DEFAULT_TRACKING_SUBMIT_CRON),
            last_run_at=group.tracking_submit_last_run_at,
            now=now,
        ):
            out.append(group.id)
    return out


def run_scheduled_tracking_submit(group_id: int) -> bool:
    """Run a scheduled tracking submit for one buying group. Returns True if started."""
    db = SessionLocal()
    try:
        group = db.query(BuyingGroup).filter(BuyingGroup.id == group_id).first()
        if not group or not group.tracking_submit_enabled:
            return False
        ready, reason = parsefile_group_ready(group)
        if not ready:
            logger.info(
                "Skipping scheduled tracking submit for buying group %s: %s",
                group_id,
                reason,
            )
            return False
        result = batch_submit_pending_trackings(db, buying_group_id=group_id)
        now = datetime.now(timezone.utc)
        group = db.query(BuyingGroup).filter(BuyingGroup.id == group_id).first()
        if group:
            group.tracking_submit_last_run_at = now
            db.commit()
        if result.error:
            logger.warning(
                "Scheduled tracking submit for buying group %s failed: %s",
                group_id,
                result.error,
            )
        else:
            logger.info(
                "Scheduled tracking submit for buying group %s: submitted %s",
                group_id,
                result.submitted_count,
            )
        return True
    except Exception:
        logger.exception("Scheduled tracking submit failed for buying group %s", group_id)
        try:
            db.rollback()
        except Exception:
            pass
        return False
    finally:
        db.close()
