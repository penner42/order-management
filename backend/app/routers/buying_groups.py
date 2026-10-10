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
    group_ready,
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


def write_group_tracking_log(
    *,
    group: BuyingGroup | None = None,
    group_name: str | None = None,
    scheduled: bool,
    level: str,
    event_type: str,
    tracking_numbers: list[str] | None = None,
    message: str | None = None,
) -> bool:
    """Persist a Groups-category import-log row in its own DB session.

    Uses a dedicated session so batch-submit commit/rollback cannot drop the log.
    Returns True if the row was committed.
    """
    name = (group_name or (group.name if group else None) or "").strip() or None
    framework = ""
    if group and (group.api_framework or "").strip():
        framework = str(group.api_framework).strip()
    db = SessionLocal()
    try:
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
        db.commit()
        return True
    except Exception:
        logger.exception(
            "Failed to write Groups import log event_type=%s group=%s",
            event_type,
            name or group_name,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return False
    finally:
        db.close()


def _log_tracking_submit_result(
    *,
    group: BuyingGroup | None,
    group_name: str,
    scheduled: bool,
    result: BatchSubmitResult,
) -> None:
    if result.error:
        write_group_tracking_log(
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
        write_group_tracking_log(
            group=group,
            group_name=group_name,
            scheduled=scheduled,
            level="updates",
            event_type="tracking_submitted",
            tracking_numbers=result.tracking_numbers or None,
            message=msg,
        )
        return
    write_group_tracking_log(
        group=group,
        group_name=group_name,
        scheduled=scheduled,
        level="info",
        event_type="tracking_submitted",
        message=result.message or "No pending tracking numbers to submit.",
    )


def _stamp_tracking_submit_last_run(db: Session, group_id: int) -> datetime:
    now = datetime.now(timezone.utc)
    group = db.query(BuyingGroup).filter(BuyingGroup.id == group_id).first()
    if group:
        group.tracking_submit_last_run_at = now
        db.commit()
        db.refresh(group)
        return group.tracking_submit_last_run_at or now
    return now


def _run_tracking_submit(
    db: Session,
    group: BuyingGroup,
    *,
    scheduled: bool = False,
) -> BuyingGroupSubmitTrackingResponse:
    """Batch-submit pending trackings, stamp last_run_at, and log start/result."""
    ready, reason = group_ready(group)
    if not ready:
        raise HTTPException(status_code=400, detail=reason or "Buying group API is not configured")

    group_id = group.id
    group_name = (group.name or "").strip() or f"group {group_id}"
    write_group_tracking_log(
        group=group,
        group_name=group_name,
        scheduled=scheduled,
        level="info",
        event_type="tracking_submit_started",
        message=f"Started tracking submit ({'scheduled' if scheduled else 'manual'}).",
    )

    result = batch_submit_pending_trackings(db, buying_group_id=group_id)
    # Re-load after batch_submit may have committed/rolled back.
    group = db.query(BuyingGroup).filter(BuyingGroup.id == group_id).first()
    last_run = _stamp_tracking_submit_last_run(db, group_id)
    _log_tracking_submit_result(
        group=group,
        group_name=group_name,
        scheduled=scheduled,
        result=result,
    )

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
        tracking_submit_last_run_at=last_run,
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
    """Return buying group ids whose tracking-submit schedule is due.

    Groups that are due but not API-ready are logged and stamped so we do not
    silently no-op every scheduler tick.
    """
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
        if not schedule_due(
            enabled=True,
            cron_expr=str(group.tracking_submit_cron or DEFAULT_TRACKING_SUBMIT_CRON),
            last_run_at=group.tracking_submit_last_run_at,
            now=now,
        ):
            continue
        ready, reason = group_ready(group)
        if not ready:
            group_name = (group.name or "").strip() or f"group {group.id}"
            write_group_tracking_log(
                group=group,
                group_name=group_name,
                scheduled=True,
                level="error",
                event_type="tracking_submit_error",
                message=reason or "Buying group API is not configured",
            )
            group.tracking_submit_last_run_at = now
            db.commit()
            continue
        out.append(group.id)
    return out


def run_scheduled_tracking_submit(group_id: int) -> bool:
    """Run a scheduled tracking submit for one buying group. Returns True if started."""
    db = SessionLocal()
    try:
        group = db.query(BuyingGroup).filter(BuyingGroup.id == group_id).first()
        if not group or not group.tracking_submit_enabled:
            return False
        ready, reason = group_ready(group)
        group_name = (group.name or "").strip() or f"group {group_id}"
        if not ready:
            write_group_tracking_log(
                group=group,
                group_name=group_name,
                scheduled=True,
                level="error",
                event_type="tracking_submit_error",
                message=reason or "Buying group API is not configured",
            )
            _stamp_tracking_submit_last_run(db, group_id)
            return False

        write_group_tracking_log(
            group=group,
            group_name=group_name,
            scheduled=True,
            level="info",
            event_type="tracking_submit_started",
            message="Started tracking submit (scheduled).",
        )
        result = batch_submit_pending_trackings(db, buying_group_id=group_id)
        group = db.query(BuyingGroup).filter(BuyingGroup.id == group_id).first()
        _stamp_tracking_submit_last_run(db, group_id)
        _log_tracking_submit_result(
            group=group,
            group_name=group_name,
            scheduled=True,
            result=result,
        )
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
        write_group_tracking_log(
            group=None,
            group_name=f"group {group_id}",
            scheduled=True,
            level="error",
            event_type="tracking_submit_error",
            message=str(exc).strip() or exc.__class__.__name__,
        )
        try:
            _stamp_tracking_submit_last_run(db, group_id)
        except Exception:
            logger.exception(
                "Failed to stamp tracking_submit_last_run_at for buying group %s", group_id
            )
        return False
    finally:
        db.close()
