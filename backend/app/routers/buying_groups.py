"""Buying groups API."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from threading import Event

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.buying_group_schedules import SCHEDULE_FIELDS, rebuild_schedules, claim_group_job
from app.submission_policy import require_group_submissions_enabled
from app.auth import get_current_user
from app.browser_automation.buyinggroup_session import (
    BrowserAccess, LOGIN_URL, browser_access_for_group, supports_browser_session,
)
from app.browser_automation.jobs import create_job, get_job, update_job
from app.schemas.browser_profile import BrowserJobRead
from app.database import SessionLocal, get_db
from app.models import BrowserImportLog, BrowserProfile, BuyingGroup, ReceivingReceiptCache, ReceivingReviewPending, User
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
    ReceivingDamageMatchRequest,
    ReceivingReceiptLinkRequest,
    ReceivingReceiptUnlinkRequest,
)
from app.utils.buying_group_apis.check_receiving import (
    CheckReceivingResult,
    check_receiving_for_group,
    list_receiving_review_pending_payloads,
    match_receiving_return,
    prune_receiving_review_pending,
    receiving_check_group_ready,
    save_receiving_review_pending,
)
from app.utils.buying_group_apis.submit_tracking import (
    BatchSubmitResult,
    batch_submit_pending_trackings,
    group_ready,
)

from app.utils.receiving_links import (
    applied_receipt_pairs, candidate_lines, link_receipt, receipt_is_applied, unlink_receipt,
)
from app.utils.receiving_audit import build_receiving_audit
from app.utils.receiving_cache import provider_identity
from app.utils.buying_group_apis import buyinggroup as buyinggroup_api

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/buying-groups", tags=["buying-groups"])
_receiving_tasks: dict[int, asyncio.Task] = {}
_receiving_jobs: dict[int, str] = {}
GOBUYME_AUTOMATION_DISABLED = {
    "received_check_enabled": False,
    "payment_sync_enabled": False,
}


@router.get("", response_model=list[BuyingGroupRead])
def list_buying_groups(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(BuyingGroup).order_by(func.lower(BuyingGroup.name)).all()


@router.post("", response_model=BuyingGroupRead)
def create_buying_group(data: BuyingGroupCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if data.order_number_submit_enabled and data.api_framework != "bfmr":
        raise HTTPException(400, "Automatic order-number submission is available only for BFMR")
    user_id = (current_user.id if current_user.role != "admin" else None) or get_default_app_user_id(db)
    values = data.model_dump(exclude={"user_id"})
    if data.api_framework == "gobuyme":
        values.update(GOBUYME_AUTOMATION_DISABLED)
    group = BuyingGroup(**values, user_id=user_id)
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


@router.get("/receiving-review/has-pending", response_model=bool)
def has_pending_receiving_matches(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Check the saved match count without rematching warehouse receipts."""
    pending_groups = (
        db.query(
            BuyingGroup.api_framework, BuyingGroup.base_url, BuyingGroup.api_username,
            BuyingGroup.api_user_id, BuyingGroup.api_email, ReceivingReceiptCache.provider_identity,
        )
        .select_from(BuyingGroup)
        .join(ReceivingReviewPending, ReceivingReviewPending.buying_group_id == BuyingGroup.id)
        .outerjoin(ReceivingReceiptCache, ReceivingReceiptCache.buying_group_id == BuyingGroup.id)
        .filter(ReceivingReviewPending.matched_count > 0)
    )
    return any(
        group.provider_identity is None or group.provider_identity == provider_identity(group)
        for group in pending_groups
    )


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
    include_all_groups: bool = False, receipt_key: str | None = None,
    damaged_warehouse_id: str | None = None,
    db: Session = Depends(get_db), _: User = Depends(get_current_user),
):
    return candidate_lines(db, buying_group_id, q, page, include_received=include_received,
                           include_all_groups=include_all_groups, receipt_key_value=receipt_key,
                           damaged_warehouse_id=damaged_warehouse_id)


@router.post("/receiving-review/match-damage", response_model=ReceivingReviewSession)
def match_damaged_warehouse_item(
    body: ReceivingDamageMatchRequest, db: Session = Depends(get_db), _: User = Depends(get_current_user),
):
    return match_receiving_return(db, body)


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


@router.get("/receiving-audit")
def receiving_audit(
    group_id: int | None = None, db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    if group_id is not None:
        group = db.get(BuyingGroup, group_id)
        if group is None:
            raise HTTPException(404, "Buying group not found")
        if (group.api_framework or "").strip().casefold() != "buyinggroup":
            raise HTTPException(400, "This report supports BuyingGroup.com groups.")
    return build_receiving_audit(db, group_id)


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
    framework = updates.get("api_framework", group.api_framework)
    if updates.get("order_number_submit_enabled") and framework != "bfmr":
        raise HTTPException(400, "Automatic order-number submission is available only for BFMR")
    if framework != "bfmr":
        updates["order_number_submit_enabled"] = False
    if framework == "gobuyme":
        updates.update(GOBUYME_AUTOMATION_DISABLED)
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
    from app.models import Payment, MonarchPaymentReceipt, PaymentReview, PaymentSource
    if db.query(PaymentReview).join(PaymentSource).filter(PaymentSource.buying_group_id == group_id, PaymentReview.payment_id.isnot(None)).first():
        raise HTTPException(409, "This buying group has confirmed imported payments; preserve its receipt coverage history.")
    payments = db.query(Payment).filter_by(buying_group_id=group_id).order_by(Payment.id).with_for_update().all()
    if any(db.get(MonarchPaymentReceipt, payment.id) for payment in payments):
        raise HTTPException(status_code=409, detail="Undo Monarch receipt confirmations before deleting this buying group.")
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
async def check_buying_group_receiving_route(
    group_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    job = _queue_browser_receiving(db, group_id)
    if job:
        group = db.get(BuyingGroup, group_id)
        return BuyingGroupCheckReceivingResponse(
            buying_group_id=group_id, buying_group_name=group.name, job_id=job.id,
        )
    return await run_in_threadpool(check_buying_group_receiving, group_id, db, current_user)


def check_buying_group_receiving(
    group_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    *,
    browser_access: BrowserAccess | None = None,
    scheduled: bool = False,
):
    """Search warehouse receipts and open review for matches or unknown trackings."""
    group = db.query(BuyingGroup).filter(BuyingGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Buying group not found")
    ready, reason = receiving_check_group_ready(group)
    if not ready and browser_access is None:
        raise HTTPException(
            status_code=400,
            detail=reason
            or "Receiving check is only available for Parsefile, BuyingGroup.com, BFMR, or USABG buying groups",
        )

    result, actionable = _execute_received_check(db, group, scheduled=scheduled, browser_access=browser_access)
    if result.retry_after is not None:
        raise HTTPException(status_code=429, detail=result.error, headers={"Retry-After": str(result.retry_after)})
    if result.browser_sign_in_required:
        raise HTTPException(409, result.error)
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


@router.get("/{group_id}/receiving-check-job", response_model=BrowserJobRead | None)
def receiving_check_job(
    group_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user),
):
    profile = db.query(BrowserProfile).filter_by(buying_group_id=group_id).first()
    job = get_job(_receiving_jobs.get(group_id, ""))
    return job.to_dict() if profile and job and job.profile_id == profile.id else None


def _queue_browser_receiving(db: Session, group_id: int, *, scheduled: bool = False):
    group = db.get(BuyingGroup, group_id)
    if group is None or not supports_browser_session(group):
        return None
    profile = db.query(BrowserProfile).filter_by(buying_group_id=group_id).first()
    if profile is None:
        return None
    if group_id in _receiving_tasks:
        return get_job(_receiving_jobs[group_id])
    job = create_job(profile.id, "buyinggroup_receiving")
    _receiving_jobs[group_id] = job.id
    task = asyncio.create_task(_run_browser_receiving(group_id, job.id, scheduled=scheduled))
    _receiving_tasks[group_id] = task
    task.add_done_callback(lambda _: _receiving_tasks.pop(group_id, None))
    return job


async def _run_browser_receiving(group_id: int, job_id: str, *, scheduled: bool):
    loop = asyncio.get_running_loop()
    stopped = Event()
    completed = 0

    def show_wait(delay: int):
        update_job(job_id, message="BuyingGroup.com is throttling requests. The check will resume automatically.",
                   progress={"completed_requests": completed,
                             "retry_at": (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()})

    async def read_access(previous_access: str | None = None):
        while True:
            try:
                access = await browser_access_for_group(group_id, previous_access=previous_access)
            except HTTPException as exc:
                if exc.status_code != 429:
                    raise
                delay = int((exc.headers or {}).get("Retry-After", "60"))
                show_wait(delay)
                await asyncio.sleep(delay)
                continue
            if access is None:
                raise HTTPException(409, "Connect the BuyingGroup.com browser before checking receiving.")
            update_job(job_id, message="Checking BuyingGroup.com receipts and returns.",
                       progress={"completed_requests": completed})
            return access

    def run_check(access: BrowserAccess):
        def wait(delay: float, throttled: bool):
            if throttled:
                show_wait(int(delay))
            if stopped.wait(delay):
                raise buyinggroup_api.ReceivingCheckStopped("BuyingGroup.com receiving check stopped.")

        def refresh(previous_access: str):
            future = asyncio.run_coroutine_threadsafe(read_access(previous_access), loop)
            try:
                while not stopped.is_set():
                    try:
                        fresh = future.result(timeout=1)
                        if access.identity != fresh.identity:
                            raise buyinggroup_api.ReceivingCheckStopped("Buying group configuration changed. Check again.")
                        return fresh.token
                    except TimeoutError:
                        if future.done():
                            raise
                        continue
            except HTTPException as exc:
                raise buyinggroup_api.BrowserSessionExpired(str(exc.detail)) from None
            finally:
                if stopped.is_set():
                    future.cancel()
            raise buyinggroup_api.ReceivingCheckStopped("BuyingGroup.com receiving check stopped.")

        def request_completed():
            nonlocal completed
            completed += 1
            update_job(job_id, message="Checking BuyingGroup.com receipts and returns.",
                       progress={"completed_requests": completed})

        with SessionLocal() as db, buyinggroup_api.resumable_browser_reads(wait, refresh, request_completed):
            return check_buying_group_receiving(group_id, db, None, browser_access=access, scheduled=scheduled)

    update_job(job_id, status="running", message="Opening the BuyingGroup.com browser session.")
    try:
        access = await read_access()
        result = await asyncio.to_thread(run_check, access)
        update_job(job_id, status="succeeded", message=result.message, review_url=result.review_path,
                   progress={"completed_requests": completed})
    except HTTPException as exc:
        update_job(job_id, status="failed", error=str(exc.detail))
    except asyncio.CancelledError:
        update_job(job_id, status="cancelled", message="Receiving check interrupted by server shutdown.")
        raise
    except Exception:
        logger.exception("BuyingGroup.com receiving job failed (group=%s)", group_id)
        update_job(job_id, status="failed", error="BuyingGroup.com receiving check failed. Check the application log.")
    finally:
        stopped.set()


async def stop_browser_receiving_checks() -> None:
    tasks = list(_receiving_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _queue_scheduled_browser_receiving(group_id: int) -> bool:
    with SessionLocal() as db:
        return _queue_browser_receiving(db, group_id, scheduled=True) is not None


@router.get("/{group_id}/browser-connection")
@router.post("/{group_id}/browser-connection")
def buyinggroup_browser_connection(
    group_id: int, request: Request, db: Session = Depends(get_db), _: User = Depends(get_current_user),
):
    group = db.query(BuyingGroup).filter_by(id=group_id).with_for_update().first()
    if group is None or not supports_browser_session(group):
        raise HTTPException(404, "BuyingGroup.com browser connection is unavailable for this group.")
    profile = db.query(BrowserProfile).filter_by(buying_group_id=group_id).first()
    if profile is None and request.method == "POST":
        profile = BrowserProfile(
            buying_group_id=group_id, retailer="buyinggroup", integration=f"buyinggroup_receiving:{group_id}",
        )
        db.add(profile)
        db.commit()
    return {"profile_id": profile.id if profile else None,
            "status": profile.status if profile else "logged_out", "login_url": LOGIN_URL}


def run_scheduled_tracking_submit(group_id: int) -> bool:
    """Run a scheduled tracking submit for one buying group. Returns True if started."""
    if not settings.scheduled_jobs_enabled or not settings.group_submissions_enabled:
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
    browser_access: BrowserAccess | None = None,
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
        access_kwargs = {"browser_access": browser_access} if browser_access else {}
        result = check_receiving_for_group(db, buying_group_id=group_id, **access_kwargs)
        actionable = 0
        if result.session_payload and not result.error:
            actionable = save_receiving_review_pending(db, result.session_payload)

        _stamp_received_check_last_run(db, group_id)
        trackings = _matched_trackings_from_result(result)

        if result.error:
            if result.browser_sign_in_required:
                db.query(BrowserProfile).filter_by(buying_group_id=group_id, status="ready").update(
                    {"status": "login_required", "last_error": result.error},
                )
                db.commit()
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
        pending = db.get(ReceivingReviewPending, group_id)
        saved_payload = pending.payload if pending is not None else {}
        unknown_count = len(saved_payload.get("unknown_receipts") or [])
        damaged_count = len(saved_payload.get("damaged_items") or [])
        received_count = actionable - damaged_count
        reported_unknowns = (result.session_payload or {}).get("unknown_receipts") or []
        if reported_unknowns:
            applied_pairs = applied_receipt_pairs(db, group_id)
            already_applied = [r for r in reported_unknowns if receipt_is_applied(r, applied_pairs)]
            # Provider summaries describe shipment-tracking detection, before
            # persistence excludes receipts already applied using warehouse TNs.
            summary = f"Warehouse search: {summary}"
            if already_applied:
                details = "; ".join(
                    f"receipt {r.get('receipt_id') or '(no ID)'} / tracking {r['tracking_number']}"
                    for r in already_applied
                )
                summary += (
                    f" {len(already_applied)} reported receipt(s) already applied in Order Manager"
                    f" and excluded from unknown review: {details}."
                )
        summary += (
            f" Receiving review: {received_count} matched item(s) ready to apply;"
            f" {unknown_count} unknown receipt(s) to review."
        )
        if damaged_count:
            if saved_payload.get("buyinggroup_returns"):
                summary += f" {damaged_count} open BuyingGroup.com return(s) to match."
            else:
                summary += f" {damaged_count} damaged warehouse unit(s) to match."
        result.message = summary
        write_group_tracking_log(
            group=group,
            group_name=group_name,
            scheduled=scheduled,
            level="updates" if actionable or unknown_count else "info",
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
        return result, received_count
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


def run_scheduled_received_check(group_id: int, *, browser_loop=None) -> bool:
    if not settings.scheduled_jobs_enabled:
        return False
    db = SessionLocal()
    try:
        group = db.query(BuyingGroup).filter(BuyingGroup.id == group_id).first()
        if not group or not group.received_check_enabled:
            return False
        if browser_loop is not None and supports_browser_session(group):
            future = asyncio.run_coroutine_threadsafe(_queue_scheduled_browser_receiving(group_id), browser_loop)
            try:
                if future.result(timeout=15):
                    return True
            except TimeoutError:
                future.cancel()
                write_group_tracking_log(
                    group=group, scheduled=True, level="error", event_type="received_check_error",
                    mode="received_check", message="Timed out starting the BuyingGroup.com receiving check.",
                )
                _stamp_received_check_last_run(db, group_id)
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


def run_scheduled_group(group_id: int, *, browser_loop=None) -> bool:
    """One interval per group: submit first, then receive, when each is enabled."""
    if not settings.scheduled_jobs_enabled:
        return False
    db = SessionLocal()
    try:
        group = claim_group_job(db, group_id)
        if not group:
            return False
        submit = group.tracking_submit_enabled and settings.group_submissions_enabled
        receive = group.received_check_enabled
        from app.buying_group_schedules import payment_enabled
        payments = payment_enabled(group)
    finally:
        db.close()
    if submit:
        run_scheduled_tracking_submit(group_id)
    if receive:
        if browser_loop is None:
            run_scheduled_received_check(group_id)
        else:
            run_scheduled_received_check(group_id, browser_loop=browser_loop)
    if payments:
        from app.payment_ledger import sync_group
        with SessionLocal() as payment_db:
            sync_group(payment_db, group_id, scheduled=True)
    return True
