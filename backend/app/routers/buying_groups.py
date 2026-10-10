"""Buying groups API."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import SessionLocal, get_db
from app.models import BrowserImportLog, BuyingGroup, User
from app.models.user import get_default_app_user_id
from app.schemas.buying_group import (
    DEFAULT_TRACKING_SUBMIT_CRON,
    BuyingGroupCreate,
    BuyingGroupRead,
    BuyingGroupSubmitTrackingResponse,
    BuyingGroupUpdate,
)
from app.utils.buying_group_apis.submit_tracking import (
    BatchSubmitResult,
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


def _add_group_tracking_log(
    db: Session,
    *,
    group: BuyingGroup | None,
    group_name: str | None,
    scheduled: bool,
    level: str,
    event_type: str,
    tracking_numbers: list[str] | None = None,
    message: str | None = None,
) -> None:
    """Write a Groups-category event into the shared browser_import_logs table."""
    name = (group_name or (group.name if group else None) or "").strip() or None
    framework = ""
    if group and (group.api_framework or "").strip():
        framework = str(group.api_framework).strip()
    db.add(
        BrowserImportLog(
            browser_profile_id=None,
            job_id=None,
            category="groups",
            level=level,
            event_type=event_type,
            mode="tracking_submit",
            scheduled=scheduled,
            retailer=framework or "",
            store_order_number=None,
            order_id=None,
            tracking_numbers=json.dumps(tracking_numbers) if tracking_numbers else None,
            message=message,
            store_name=name,
            store_account_name=None,
        )
    )


def _log_tracking_submit_result(
    db: Session,
    *,
    group: BuyingGroup | None,
    group_name: str,
    scheduled: bool,
    result: BatchSubmitResult,
) -> None:
    if result.error:
        _add_group_tracking_log(
            db,
            group=group,
            group_name=group_name,
            scheduled=scheduled,
            level="error",
            event_type="tracking_submit_error",
            tracking_numbers=result.tracking_numbers or None,
            message=result.error,
        )
        return
    if result.submitted_count > 0:
        msg = result.message or f"Submitted {result.submitted_count} tracking number(s)."
        _add_group_tracking_log(
            db,
            group=group,
            group_name=group_name,
            scheduled=scheduled,
            level="updates",
            event_type="tracking_submitted",
            tracking_numbers=result.tracking_numbers or None,
            message=msg,
        )
    else:
        _add_group_tracking_log(
            db,
            group=group,
            group_name=group_name,
            scheduled=scheduled,
            level="info",
            event_type="tracking_submitted",
            message="No pending tracking numbers to submit.",
        )


def _run_tracking_submit(
    db: Session,
    group: BuyingGroup,
    *,
    scheduled: bool = False,
) -> BuyingGroupSubmitTrackingResponse:
    """Batch-submit pending trackings, stamp last_run_at, and log the result."""
    ready, reason = parsefile_group_ready(group)
    if not ready:
        raise HTTPException(status_code=400, detail=reason or "Buying group API is not configured")

    group_name = (group.name or "").strip() or f"group {group.id}"
    result = batch_submit_pending_trackings(db, buying_group_id=group.id)
    now = datetime.now(timezone.utc)
    # Re-load after batch_submit may have committed/rolled back.
    group = db.query(BuyingGroup).filter(BuyingGroup.id == group.id).first()
    if group:
        group.tracking_submit_last_run_at = now
    _log_tracking_submit_result(
        db,
        group=group,
        group_name=group_name,
        scheduled=scheduled,
        result=result,
    )
    db.commit()
    if group:
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
    return _run_tracking_submit(db, group, scheduled=False)


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
        group_name = (group.name or "").strip() or f"group {group_id}"
        result = batch_submit_pending_trackings(db, buying_group_id=group_id)
        now = datetime.now(timezone.utc)
        group = db.query(BuyingGroup).filter(BuyingGroup.id == group_id).first()
        if group:
            group.tracking_submit_last_run_at = now
        _log_tracking_submit_result(
            db,
            group=group,
            group_name=group_name,
            scheduled=True,
            result=result,
        )
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
    except Exception as exc:
        logger.exception("Scheduled tracking submit failed for buying group %s", group_id)
        try:
            db.rollback()
        except Exception:
            pass
        try:
            _add_group_tracking_log(
                db,
                group=None,
                group_name=f"group {group_id}",
                scheduled=True,
                level="error",
                event_type="tracking_submit_error",
                message=str(exc).strip() or exc.__class__.__name__,
            )
            db.commit()
        except Exception:
            logger.exception(
                "Failed to write tracking_submit_error log for buying group %s", group_id
            )
            try:
                db.rollback()
            except Exception:
                pass
        return False
    finally:
        db.close()
