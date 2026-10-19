"""Buying groups API."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.buying_group_schedules import SCHEDULE_FIELDS, rebuild_schedules, claim_group_job
from app.submission_policy import require_group_submissions_enabled
from app.auth import get_current_user
from app.database import SessionLocal, get_db
from app.models import BrowserImportLog, BuyingGroup, ReceivingReviewPending, User
from app.models.user import get_default_app_user_id
from app.schemas.buying_group import (
    BuyingGroupCheckReceivingResponse,
    BuyingGroupCreate,
    BuyingGroupRead,
    BuyingGroupSubmitTrackingResponse,
    BuyingGroupUpdate,
    ReceivingReviewPruneRequest,
    ReceivingReviewPruneResponse,
    ReceivingReviewSession,
    ReceivingReceiptLinkRequest,
    ReceivingReceiptUnlinkRequest,
)
from app.utils.buying_group_apis.check_receiving import (
    CheckReceivingResult,
    check_receiving_for_group,
    list_receiving_review_pending_payloads,
    prune_receiving_review_pending,
    receiving_check_group_ready,
    save_receiving_review_pending,
)
from app.utils.buying_group_apis.submit_tracking import (
    BatchSubmitResult,
    batch_submit_pending_trackings,
    group_ready,
)

from app.utils.receiving_links import candidate_lines, link_receipt, unlink_receipt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/buying-groups", tags=["buying-groups"])


@router.get("", response_model=list[BuyingGroupRead])
def list_buying_groups(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(BuyingGroup).order_by(func.lower(BuyingGroup.name)).all()


@router.post("", response_model=BuyingGroupRead)
def create_buying_group(data: BuyingGroupCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    user_id = (current_user.id if current_user.role != "admin" else None) or get_default_app_user_id(db)
    group = BuyingGroup(**data.model_dump(exclude={"user_id"}), user_id=user_id)
    db.add(group)
    db.flush()
    rebuild_schedules(db)
    db.commit()
    db.refresh(group)
    return group


@router.get("/receiving-review", response_model=list[ReceivingReviewSession])
def list_receiving_review(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """All buying groups with pending warehouse matches awaiting apply."""
    payloads = list_receiving_review_pending_payloads(db)
    return [ReceivingReviewSession.model_validate(p) for p in payloads]


@router.post("/receiving-review/prune", response_model=ReceivingReviewPruneResponse)
def prune_receiving_review(
    body: ReceivingReviewPruneRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Remove applied items from a group's pending receiving snapshot."""
    remaining, deleted = prune_receiving_review_pending(
        db,
        buying_group_id=body.buying_group_id,
        item_ids=body.item_ids,
    )
    return ReceivingReviewPruneResponse(
        buying_group_id=body.buying_group_id,
        remaining_matched_count=remaining,
        deleted=deleted,
    )


@router.get("/receiving-review/candidates")
def receiving_candidates(
    buying_group_id: int, q: str = "", page: int = Query(default=1, ge=1),
    include_received: bool = False,
    db: Session = Depends(get_db), _: User = Depends(get_current_user),
):
    return candidate_lines(db, buying_group_id, q, page, include_received=include_received)


@router.post("/receiving-review/link", response_model=ReceivingReviewSession)
def link_unknown_receipt(
    body: ReceivingReceiptLinkRequest, db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return link_receipt(db, body)


@router.post("/receiving-review/unlink", response_model=ReceivingReviewSession)
def unlink_unknown_receipt(
    body: ReceivingReceiptUnlinkRequest, db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return unlink_receipt(db, body.buying_group_id, body.receipt_key)


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
    updates = data.model_dump(exclude_unset=True)
    changed = any(k in SCHEDULE_FIELDS and getattr(group, k) != v for k, v in updates.items())
    for k, v in updates.items():
        setattr(group, k, v)
    if changed:
        db.flush()
        rebuild_schedules(db)
    db.commit()
    db.refresh(group)
    return group


@router.delete("/{group_id}", status_code=204)
def delete_buying_group(group_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    group = db.query(BuyingGroup).filter(BuyingGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Buying group not found")
    db.delete(group)
    db.flush()
    rebuild_schedules(db)
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
    mode: str = "tracking_submit",
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
                mode=mode,
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
    from app.utils.buying_group_apis.submit_tracking import append_submission_ids_to_message

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
        msg = append_submission_ids_to_message(
            result.message or f"Submitted {result.submitted_count} tracking number(s).",
            result.submission_ids_by_tracking,
            missing_note=(
                "Submission ID not retrieved after submit."
                if not result.submission_ids_by_tracking
                else None
            ),
        )
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
    require_group_submissions_enabled()
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
    require_group_submissions_enabled()
    group = db.query(BuyingGroup).filter(BuyingGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Buying group not found")
    return _run_tracking_submit(db, group, scheduled=False)


@router.post("/{group_id}/check-receiving", response_model=BuyingGroupCheckReceivingResponse)
def check_buying_group_receiving(
    group_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Search warehouse receipts and open review for matches or unknown trackings."""
    group = db.query(BuyingGroup).filter(BuyingGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Buying group not found")
    ready, reason = receiving_check_group_ready(group)
    if not ready:
        raise HTTPException(
            status_code=400,
            detail=reason
            or "Receiving check is only available for Parsefile, BuyingGroup.com, BFMR, or USABG buying groups",
        )

    result, actionable = _execute_received_check(db, group, scheduled=False)
    if result.error:
        raise HTTPException(status_code=502, detail=result.error)

    review_path = (
        f"/receiving-review?highlight={group_id}"
        if db.get(ReceivingReviewPending, group_id) is not None else None
    )
    return BuyingGroupCheckReceivingResponse(
        buying_group_id=result.buying_group_id,
        buying_group_name=result.buying_group_name,
        matched_count=actionable or result.matched_count,
        not_found_count=result.not_found_count,
        void_skipped_count=result.void_skipped_count,
        discrepancy_count=result.discrepancy_count,
        submitted_count=result.submitted_count,
        message=result.message,
        review_path=review_path,
    )


def run_scheduled_tracking_submit(group_id: int) -> bool:
    """Run a scheduled tracking submit for one buying group. Returns True if started."""
    if not settings.group_submissions_enabled:
        return False
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


def _stamp_received_check_last_run(db: Session, group_id: int) -> datetime:
    now = datetime.now(timezone.utc)
    group = db.query(BuyingGroup).filter(BuyingGroup.id == group_id).first()
    if group:
        group.received_check_last_run_at = now
        db.commit()
        db.refresh(group)
        return group.received_check_last_run_at or now
    return now


def _matched_trackings_from_result(result: CheckReceivingResult) -> list[str] | None:
    """Distinct tracking numbers from receiving matches (for import log)."""
    seen: set[str] = set()
    out: list[str] = []
    for match in result.matches or []:
        raw = (match.tracking_number or "").strip()
        if not raw:
            continue
        for chunk in raw.replace(";", ",").split(","):
            tn = chunk.strip()
            if not tn:
                continue
            key = tn.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(tn)
    return out or None


def _execute_received_check(
    db: Session,
    group: BuyingGroup,
    *,
    scheduled: bool,
) -> tuple[CheckReceivingResult, int]:
    """Run warehouse received check, persist review pending, stamp last_run, and log.

    Returns ``(CheckReceivingResult, actionable_count)``. On unexpected exceptions,
    logs an error event, stamps last_run, and re-raises.
    """
    group_id = group.id
    group_name = (group.name or "").strip() or f"group {group_id}"
    source = "scheduled" if scheduled else "manual"

    write_group_tracking_log(
        group=group,
        group_name=group_name,
        scheduled=scheduled,
        level="info",
        event_type="received_check_started",
        message=f"Started received check ({source}).",
        mode="received_check",
    )

    try:
        result = check_receiving_for_group(db, buying_group_id=group_id)
        actionable = 0
        if result.session_payload and not result.error:
            actionable = save_receiving_review_pending(db, result.session_payload)

        _stamp_received_check_last_run(db, group_id)
        trackings = _matched_trackings_from_result(result)

        if result.error:
            write_group_tracking_log(
                group=group,
                group_name=group_name,
                scheduled=scheduled,
                level="error",
                event_type="received_check_error",
                tracking_numbers=trackings,
                message=result.error,
                mode="received_check",
            )
            logger.warning(
                "Received check (%s) for buying group %s failed: %s",
                source,
                group_id,
                result.error,
            )
            return result, 0

        summary = result.message or (
            f"Found {actionable} item(s) ready to mark scanned."
            if actionable
            else "No received items to apply."
        )
        write_group_tracking_log(
            group=group,
            group_name=group_name,
            scheduled=scheduled,
            level="updates" if actionable else "info",
            event_type="received_check_finished",
            tracking_numbers=trackings,
            message=summary,
            mode="received_check",
        )
        logger.info(
            "Received check (%s) for buying group %s: %s actionable match(es)",
            source,
            group_id,
            actionable,
        )
        return result, actionable
    except Exception as exc:
        logger.exception(
            "Received check (%s) failed for buying group %s", source, group_id
        )
        try:
            db.rollback()
        except Exception:
            pass
        write_group_tracking_log(
            group=group,
            group_name=group_name,
            scheduled=scheduled,
            level="error",
            event_type="received_check_error",
            message=str(exc).strip() or exc.__class__.__name__,
            mode="received_check",
        )
        try:
            _stamp_received_check_last_run(db, group_id)
        except Exception:
            logger.exception(
                "Failed to stamp received_check_last_run_at for buying group %s", group_id
            )
        raise


def run_scheduled_received_check(group_id: int) -> bool:
    db = SessionLocal()
    try:
        group = db.query(BuyingGroup).filter(BuyingGroup.id == group_id).first()
        if not group or not group.received_check_enabled:
            return False
        ready, reason = receiving_check_group_ready(group)
        if not ready:
            write_group_tracking_log(
                group=group, scheduled=True, level="error",
                event_type="received_check_error", mode="received_check",
                message=reason or "Buying group API is not configured",
            )
            _stamp_received_check_last_run(db, group_id)
            return False
        _execute_received_check(db, group, scheduled=True)
        return True
    except Exception:
        logger.exception("Scheduled receiving check failed for buying group %s", group_id)
        return False
    finally:
        db.close()


def run_scheduled_group(group_id: int) -> bool:
    """One interval per group: submit first, then receive, when each is enabled."""
    db = SessionLocal()
    try:
        group = claim_group_job(db, group_id)
        if not group:
            return False
        submit = group.tracking_submit_enabled and settings.group_submissions_enabled
        receive = group.received_check_enabled
    finally:
        db.close()
    if submit:
        run_scheduled_tracking_submit(group_id)
    if receive:
        run_scheduled_received_check(group_id)
    return True
