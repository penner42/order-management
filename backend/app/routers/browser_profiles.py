"""Browser automation profiles: CRUD, live login WebSocket, import jobs."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session, joinedload

from app.auth import decode_token, get_current_user
from app.browser_automation.amazon_import import run_amazon_import
from app.browser_automation.common import LoginRequiredError, post_bulk_session
from app.browser_automation.jobs import create_job, get_job, update_job
from app.browser_automation.paths import profile_user_data_dir
from app.browser_automation.session_manager import (
    login_home_url_for_retailer,
    retailer_session_logged_in,
    session_manager,
)
from app.browser_automation.walmart_import import run_walmart_import
from app.database import SessionLocal, get_db
from app.models import (
    BrowserImportLog,
    BrowserProfile,
    Item,
    Order,
    Shipment,
    ShipmentItem,
    StoreAccount,
    User,
)
from app.utils.ignored_zip_codes import shipping_postal_code_from_payload
from app.models.item import ItemStatus
from app.models.user import get_default_app_user_id
from app.schemas.browser_profile import (
    DEFAULT_FULL_CHECK_CRON,
    DEFAULT_UNSHIPPED_CHECK_CRON,
    BrowserImportLogRead,
    BrowserJobRead,
    BrowserProfileCreate,
    BrowserProfileRead,
    BrowserProfileScheduleUpdate,
    ImportStartRequest,
    ImportStartResponse,
    LoginStartResponse,
)
from app.schemas.store_import import StoreOrderImportPayload
from app.routers.store_imports import apply_store_order_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/browser-profiles", tags=["browser-profiles"])

SUPPORTED_RETAILERS = {"amazon", "walmart"}


def _profile_read(profile: BrowserProfile) -> BrowserProfileRead:
    account = profile.store_account
    store = account.store if account else None
    return BrowserProfileRead(
        id=profile.id,
        store_account_id=profile.store_account_id,
        retailer=profile.retailer,
        status=profile.status,
        last_error=profile.last_error,
        last_import_at=profile.last_import_at,
        full_check_enabled=bool(profile.full_check_enabled),
        full_check_cron=str(profile.full_check_cron or DEFAULT_FULL_CHECK_CRON),
        full_check_max_pages=int(profile.full_check_max_pages or 3),
        full_check_last_run_at=profile.full_check_last_run_at,
        unshipped_check_enabled=bool(profile.unshipped_check_enabled),
        unshipped_check_cron=str(profile.unshipped_check_cron or DEFAULT_UNSHIPPED_CHECK_CRON),
        unshipped_check_last_run_at=profile.unshipped_check_last_run_at,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        store_id=store.id if store else None,
        store_name=store.name if store else None,
        store_account_name=account.name if account else None,
    )


def _unshipped_store_order_numbers(db: Session, store_account_id: int) -> list[str]:
    """Store order numbers for this account that still need tracking.

    An item counts as unshipped when it is not canceled or lost and has no
    linked shipment with a non-empty tracking number. Amazon (and similar)
    imports often create placeholder shipments before tracking exists; those
    orders must still be included in the unshipped refresh.

    Personal orders (ignored shipping zip) are excluded entirely.
    """
    # Postgres requires ORDER BY cols to appear in the SELECT list when using DISTINCT.
    # Select (id, store_order_number), order by id, then dedupe numbers in Python.
    has_real_tracking = (
        db.query(ShipmentItem.id)
        .join(Shipment, Shipment.id == ShipmentItem.shipment_id)
        .filter(ShipmentItem.item_id == Item.id)
        .filter(Shipment.tracking_number.isnot(None))
        .filter(Shipment.tracking_number != "")
        .correlate(Item)
        .exists()
    )
    rows = (
        db.query(Order.id, Order.store_order_number)
        .join(Item, Item.order_id == Order.id)
        .filter(Order.store_account_id == store_account_id)
        .filter(Order.store_order_number.isnot(None))
        .filter(Order.store_order_number != "")
        .filter(Order.status != "personal")
        .filter(Item.status.notin_((ItemStatus.CANCELED, ItemStatus.LOST_PACKAGE)))
        .filter(~has_real_tracking)
        .order_by(Order.id.desc())
        .distinct()
        .all()
    )
    seen: set[str] = set()
    out: list[str] = []
    for _order_id, raw in rows:
        s = str(raw or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _apply_user(db: Session) -> User:
    """User used when auto-applying scheduled/manual capture results."""
    user_id = get_default_app_user_id(db)
    if user_id is not None:
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            return user
    admin = db.query(User).filter(User.role == "admin").first()
    if admin:
        return admin
    raise RuntimeError("No user available to apply imported orders.")


def _order_tracking_numbers(db: Session, store_order_number: str) -> tuple[int | None, set[str]]:
    """Return (order_id, tracking numbers) for a store order number, if present."""
    order = (
        db.query(Order)
        .filter(Order.store_order_number == store_order_number)
        .first()
    )
    if not order:
        return None, set()
    rows = (
        db.query(Shipment.tracking_number)
        .join(ShipmentItem, ShipmentItem.shipment_id == Shipment.id)
        .join(Item, Item.id == ShipmentItem.item_id)
        .filter(Item.order_id == order.id)
        .all()
    )
    tracking = {
        t.strip()
        for (t,) in rows
        if isinstance(t, str) and t.strip()
    }
    return order.id, tracking


def _external_order_id_from_payload(payload: StoreOrderImportPayload) -> str | None:
    ext = payload.externalOrder or {}
    raw = str(ext.get("id") or "").strip()
    return raw or None


def _profile_account_labels(profile: BrowserProfile) -> tuple[str | None, str | None]:
    account = profile.store_account
    store = account.store if account else None
    return (store.name if store else None, account.name if account else None)


def _add_import_log(
    db: Session,
    *,
    profile: BrowserProfile,
    job_id: str,
    mode: str,
    scheduled: bool,
    level: str,
    event_type: str,
    store_order_number: str | None = None,
    order_id: int | None = None,
    tracking_numbers: list[str] | None = None,
    message: str | None = None,
) -> None:
    store_name, store_account_name = _profile_account_labels(profile)
    db.add(
        BrowserImportLog(
            browser_profile_id=profile.id,
            job_id=job_id,
            level=level,
            event_type=event_type,
            mode=mode,
            scheduled=scheduled,
            retailer=profile.retailer,
            store_order_number=store_order_number,
            order_id=order_id,
            tracking_numbers=json.dumps(tracking_numbers) if tracking_numbers else None,
            message=message,
            store_name=store_name,
            store_account_name=store_account_name,
        )
    )


def _log_auto_apply_result(
    db: Session,
    *,
    profile: BrowserProfile,
    job_id: str,
    mode: str,
    scheduled: bool,
    store_order_number: str,
    existed_before: bool,
    tracking_before: set[str],
    order_id: int,
    tracking_after: set[str],
    marked_personal: bool = False,
    postal_code: str | None = None,
) -> str:
    """Log update or order_checked. Returns the event_type written."""
    added_tracking = sorted(tracking_after - tracking_before)
    if marked_personal:
        event_type = "order_marked_personal"
        level = "updates"
        tracking_for_log = sorted(tracking_after) if not existed_before else added_tracking
        zip_note = f" (ignored zip {postal_code})" if postal_code else ""
        message = (
            f"Imported as personal{zip_note}."
            if not existed_before
            else f"Moved to personal{zip_note}."
        )
    elif not existed_before:
        event_type = "order_imported"
        level = "updates"
        tracking_for_log = sorted(tracking_after)
        message = None
    elif added_tracking:
        event_type = "tracking_updated"
        level = "updates"
        tracking_for_log = added_tracking
        message = None
    else:
        event_type = "order_checked"
        level = "info"
        tracking_for_log = []
        message = None
    _add_import_log(
        db,
        profile=profile,
        job_id=job_id,
        mode=mode,
        scheduled=scheduled,
        level=level,
        event_type=event_type,
        store_order_number=store_order_number,
        order_id=order_id,
        tracking_numbers=tracking_for_log or None,
        message=message,
    )
    return event_type


def _parse_tracking_numbers(raw: str | None) -> list[str]:
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    try:
        parsed: Any = json.loads(text)
        if isinstance(parsed, list):
            return [str(t).strip() for t in parsed if str(t).strip()]
    except json.JSONDecodeError:
        pass
    return [t.strip() for t in text.split(",") if t.strip()]


def _import_log_read(row: BrowserImportLog) -> BrowserImportLogRead:
    tracking = _parse_tracking_numbers(row.tracking_numbers)
    return BrowserImportLogRead(
        id=row.id,
        browser_profile_id=row.browser_profile_id,
        job_id=row.job_id,
        level=row.level or "info",
        event_type=row.event_type,
        mode=row.mode,
        scheduled=bool(row.scheduled),
        retailer=row.retailer,
        store_order_number=row.store_order_number,
        tracking_numbers=tracking,
        message=row.message,
        store_name=row.store_name,
        store_account_name=row.store_account_name,
        created_at=row.created_at,
    )


def _get_profile_or_404(db: Session, profile_id: int) -> BrowserProfile:
    profile = (
        db.query(BrowserProfile)
        .options(joinedload(BrowserProfile.store_account).joinedload(StoreAccount.store))
        .filter(BrowserProfile.id == profile_id)
        .first()
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Browser profile not found.")
    return profile


@router.get("", response_model=list[BrowserProfileRead])
def list_browser_profiles(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    rows = (
        db.query(BrowserProfile)
        .options(joinedload(BrowserProfile.store_account).joinedload(StoreAccount.store))
        .order_by(BrowserProfile.id)
        .all()
    )
    return [_profile_read(p) for p in rows]


@router.get("/import-logs", response_model=list[BrowserImportLogRead])
def list_browser_import_logs(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    limit: int = Query(default=200, ge=1, le=1000),
    level: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    scheduled_only: bool = Query(default=False),
    profile_id: int | None = Query(default=None),
):
    """Recent browser-automation log events (updates + info)."""
    q = db.query(BrowserImportLog)
    if level in ("updates", "info", "error"):
        q = q.filter(BrowserImportLog.level == level)
    if event_type in (
        "order_imported",
        "tracking_updated",
        "order_checked",
        "order_marked_personal",
        "order_skipped_ignored_zip",  # legacy
        "order_error",
        "check_started",
        "check_finished",
    ):
        q = q.filter(BrowserImportLog.event_type == event_type)
    if scheduled_only:
        q = q.filter(BrowserImportLog.scheduled.is_(True))
    if profile_id is not None:
        q = q.filter(BrowserImportLog.browser_profile_id == profile_id)
    rows = q.order_by(BrowserImportLog.created_at.desc(), BrowserImportLog.id.desc()).limit(limit).all()
    return [_import_log_read(r) for r in rows]


@router.post("", response_model=BrowserProfileRead)
def create_browser_profile(
    data: BrowserProfileCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    if data.retailer not in SUPPORTED_RETAILERS:
        raise HTTPException(status_code=400, detail=f"Unsupported retailer: {data.retailer}")
    account = (
        db.query(StoreAccount)
        .options(joinedload(StoreAccount.store))
        .filter(StoreAccount.id == data.store_account_id)
        .first()
    )
    if not account:
        raise HTTPException(status_code=404, detail="Store account not found.")
    existing = (
        db.query(BrowserProfile)
        .filter(BrowserProfile.store_account_id == data.store_account_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="A browser profile already exists for this store account.")
    profile = BrowserProfile(
        store_account_id=data.store_account_id,
        retailer=data.retailer,
        status="logged_out",
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    # Reload relationships for response
    profile = _get_profile_or_404(db, profile.id)
    return _profile_read(profile)


@router.patch("/{profile_id}", response_model=BrowserProfileRead)
def update_browser_profile_schedule(
    profile_id: int,
    data: BrowserProfileScheduleUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    profile = _get_profile_or_404(db, profile_id)
    if data.store_account_id is not None and data.store_account_id != profile.store_account_id:
        account = (
            db.query(StoreAccount)
            .filter(StoreAccount.id == data.store_account_id)
            .first()
        )
        if not account:
            raise HTTPException(status_code=404, detail="Store account not found.")
        taken = (
            db.query(BrowserProfile)
            .filter(
                BrowserProfile.store_account_id == data.store_account_id,
                BrowserProfile.id != profile_id,
            )
            .first()
        )
        if taken:
            raise HTTPException(
                status_code=400,
                detail="A browser profile already exists for this store account.",
            )
        profile.store_account_id = data.store_account_id
    if data.full_check_enabled is not None:
        profile.full_check_enabled = data.full_check_enabled
    if data.full_check_cron is not None:
        profile.full_check_cron = data.full_check_cron
    if data.full_check_max_pages is not None:
        profile.full_check_max_pages = data.full_check_max_pages
    if data.unshipped_check_enabled is not None:
        profile.unshipped_check_enabled = data.unshipped_check_enabled
    if data.unshipped_check_cron is not None:
        profile.unshipped_check_cron = data.unshipped_check_cron
    db.commit()
    return _profile_read(_get_profile_or_404(db, profile_id))


@router.delete("/{profile_id}", status_code=204)
async def delete_browser_profile(
    profile_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    profile = _get_profile_or_404(db, profile_id)
    await session_manager.close_session(profile_id)
    user_data = profile_user_data_dir(profile_id)
    # Also remove legacy profile dirs from earlier Chrome/Firefox engines.
    legacy_dirs = [
        user_data.parent / f"profile-{profile_id}",
        user_data.parent / f"firefox-profile-{profile_id}",
    ]
    db.delete(profile)
    db.commit()
    for path in (user_data, *legacy_dirs):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    return None


@router.post("/{profile_id}/login/start", response_model=LoginStartResponse)
async def start_login(
    profile_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    profile = _get_profile_or_404(db, profile_id)
    if profile.retailer not in SUPPORTED_RETAILERS:
        raise HTTPException(status_code=400, detail=f"Unsupported retailer: {profile.retailer}")

    # Suggested first URL for the live-view address bar. We intentionally do NOT
    # auto-navigate on login — automated goto is a strong Walmart/Amazon bot signal.
    login_url = login_home_url_for_retailer(profile.retailer)
    profile.status = "login_in_progress"
    profile.last_error = None
    db.commit()

    try:
        await session_manager.ensure_session(profile_id, mode="login")
    except Exception as exc:
        profile.status = "error"
        profile.last_error = str(exc)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Failed to start browser: {exc}") from exc

    return LoginStartResponse(
        profile_id=profile_id,
        status=profile.status,
        ws_path=f"/api/browser-profiles/{profile_id}/live",
        login_url=login_url,
    )


@router.post("/{profile_id}/login/done", response_model=BrowserProfileRead)
async def finish_login(
    profile_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    profile = _get_profile_or_404(db, profile_id)
    session = session_manager.get(profile_id)
    if not session:
        profile.status = "login_required"
        profile.last_error = "No active browser session. Start login again."
        db.commit()
        return _profile_read(profile)

    retailer_label = profile.retailer.capitalize()
    try:
        logged_in = await retailer_session_logged_in(session.page, profile.retailer)
    except Exception as exc:
        logged_in = False
        profile.last_error = str(exc)

    if logged_in:
        profile.status = "ready"
        profile.last_error = None
    else:
        profile.status = "login_required"
        if not profile.last_error:
            profile.last_error = (
                f"Still on {retailer_label} sign-in. Complete login in the live view, then click Done."
            )

    db.commit()
    # Persist cookies on disk; free the browser slot until the next import/login.
    await session_manager.close_session(profile_id)
    return _profile_read(_get_profile_or_404(db, profile_id))


@router.post("/{profile_id}/login/cancel", response_model=BrowserProfileRead)
async def cancel_login(
    profile_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    profile = _get_profile_or_404(db, profile_id)
    await session_manager.close_session(profile_id)
    if profile.status == "login_in_progress":
        profile.status = "login_required"
    db.commit()
    return _profile_read(_get_profile_or_404(db, profile_id))


@router.get("/{profile_id}/live")
async def live_view_http_hint(profile_id: int):
    """Plain HTTP hits this when the reverse proxy did not upgrade to WebSocket."""
    raise HTTPException(
        status_code=426,
        detail=(
            "Live view requires a WebSocket connection. Your reverse proxy must forward "
            "HTTP Upgrade to the app for /api/browser-profiles/*/live "
            "(proxy_http_version 1.1; Upgrade + Connection headers). "
            f"profile_id={profile_id}"
        ),
        headers={"Upgrade": "websocket"},
    )


@router.websocket("/{profile_id}/live")
async def live_view(websocket: WebSocket, profile_id: int, token: str | None = Query(None)):
    """Stream live browser screencast frames and accept input events for login."""
    # Accept first so close codes / JSON errors reach the browser (rejecting
    # before accept often surfaces only as a generic client-side failure).
    await websocket.accept()

    if not token:
        await websocket.send_json({"type": "error", "message": "Missing auth token for live view."})
        await websocket.close(code=4401)
        return
    username = decode_token(token)
    if not username:
        await websocket.send_json({"type": "error", "message": "Invalid or expired auth token."})
        await websocket.close(code=4401)
        return

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            await websocket.send_json({"type": "error", "message": "User not found."})
            await websocket.close(code=4401)
            return
        profile = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
        if not profile:
            await websocket.send_json({"type": "error", "message": "Browser profile not found."})
            await websocket.close(code=4404)
            return
    finally:
        db.close()

    session = session_manager.get(profile_id)
    if not session:
        await websocket.send_json(
            {"type": "error", "message": "No active browser session. Start login first."}
        )
        await websocket.close(code=4404)
        return

    logger.info("Live view WebSocket connected profile=%s user=%s", profile_id, username)
    queue: asyncio.Queue = asyncio.Queue(maxsize=3)
    try:
        await session_manager.start_screencast(session, queue)
    except Exception as exc:
        logger.exception("Failed to start screencast for profile %s", profile_id)
        await websocket.send_json({"type": "error", "message": f"Failed to start live view: {exc}"})
        await websocket.close(code=1011)
        return

    async def pump_frames() -> None:
        while True:
            msg = await queue.get()
            await websocket.send_json(msg)

    async def pump_status() -> None:
        while True:
            try:
                await websocket.send_json(
                    {
                        "type": "status",
                        "url": session.page.url,
                        "title": await session.page.title(),
                    }
                )
            except Exception:
                pass
            await asyncio.sleep(2)

    frame_task = asyncio.create_task(pump_frames())
    status_task = asyncio.create_task(pump_status())
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg_type = message.get("type")
            if msg_type in ("mouse", "key", "paste"):
                try:
                    await session_manager.dispatch_input(session, message)
                except Exception as exc:
                    logger.debug("Input dispatch failed: %s", exc)
            elif msg_type == "resize":
                try:
                    await session_manager.resize_viewport(
                        session,
                        int(message.get("width") or 0),
                        int(message.get("height") or 0),
                    )
                except Exception as exc:
                    logger.debug("Resize failed: %s", exc)
            elif msg_type == "navigate":
                url = message.get("url")
                if url:
                    try:
                        await session.page.goto(str(url), wait_until="domcontentloaded", timeout=60_000)
                    except Exception as exc:
                        await websocket.send_json({"type": "error", "message": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        frame_task.cancel()
        status_task.cancel()
        await session_manager.stop_viewer(session, queue)


def _queue_import(
    db: Session,
    profile: BrowserProfile,
    *,
    mode: str,
    max_pages: int,
    auto_apply: bool,
    scheduled: bool,
) -> ImportStartResponse:
    if profile.retailer not in SUPPORTED_RETAILERS:
        raise HTTPException(status_code=400, detail=f"Unsupported retailer: {profile.retailer}")
    if profile.status == "login_in_progress":
        raise HTTPException(status_code=400, detail="Finish or cancel login before importing.")
    if profile.status == "importing":
        raise HTTPException(status_code=400, detail="An import is already running for this profile.")
    if profile.status not in ("ready", "login_required", "error", "logged_out"):
        raise HTTPException(status_code=400, detail=f"Profile is not ready to import (status={profile.status}).")

    kind = "import" if mode == "full" else "unshipped"
    job = create_job(profile.id, kind)
    profile.status = "importing"
    profile.last_error = None
    db.commit()

    asyncio.create_task(
        _run_import_job(
            job.id,
            profile.id,
            profile.retailer,
            mode=mode,
            max_pages=max_pages,
            auto_apply=auto_apply,
            scheduled=scheduled,
            store_account_id=profile.store_account_id,
        )
    )
    return ImportStartResponse(job_id=job.id, profile_id=profile.id, status="queued")


@router.post("/{profile_id}/import", response_model=ImportStartResponse)
async def start_import(
    profile_id: int,
    body: ImportStartRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    profile = _get_profile_or_404(db, profile_id)
    mode = body.mode or "full"
    # Unshipped checks always apply directly; full checks use Import Review unless asked.
    auto_apply = True if mode == "unshipped" else bool(body.auto_apply)
    return _queue_import(
        db,
        profile,
        mode=mode,
        max_pages=body.max_pages,
        auto_apply=auto_apply,
        scheduled=False,
    )


async def start_scheduled_import(profile_id: int, *, mode: str, max_pages: int) -> bool:
    """Start a due scheduled job. Returns False if the profile is busy / missing."""
    db = SessionLocal()
    try:
        profile = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
        if not profile:
            return False
        if profile.status == "importing" or profile.status == "login_in_progress":
            return False
        try:
            _queue_import(
                db,
                profile,
                mode=mode,
                max_pages=max_pages,
                auto_apply=True,
                scheduled=True,
            )
        except HTTPException:
            return False
        return True
    finally:
        db.close()


@router.get("/jobs/{job_id}", response_model=BrowserJobRead)
def get_browser_job(
    job_id: str,
    _: User = Depends(get_current_user),
):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return BrowserJobRead(**job.to_dict())


def _set_profile_status(
    db: Session,
    profile_id: int,
    *,
    status: str,
    last_error: str | None = None,
    last_import_at: datetime | None = None,
    full_check_last_run_at: datetime | None = None,
    unshipped_check_last_run_at: datetime | None = None,
    touch_last_import: bool = False,
) -> None:
    """Persist profile status in a fresh transaction (safe after prior failures)."""
    try:
        db.rollback()
    except Exception:
        pass
    profile = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
    if not profile:
        return
    profile.status = status
    profile.last_error = last_error
    if touch_last_import:
        profile.last_import_at = last_import_at or datetime.now(timezone.utc)
    if full_check_last_run_at is not None:
        profile.full_check_last_run_at = full_check_last_run_at
    if unshipped_check_last_run_at is not None:
        profile.unshipped_check_last_run_at = unshipped_check_last_run_at
    db.commit()


def clear_stale_browser_profile_statuses(db: Session) -> int:
    """Clear in-progress statuses left behind by a process restart (jobs are in-memory)."""
    rows = (
        db.query(BrowserProfile)
        .filter(BrowserProfile.status.in_(("importing", "login_in_progress")))
        .all()
    )
    for profile in rows:
        if profile.status == "importing":
            profile.status = "ready"
            profile.last_error = None
        else:
            profile.status = "login_required"
            profile.last_error = "Login interrupted by server restart."
    if rows:
        db.commit()
    return len(rows)


async def _run_import_job(
    job_id: str,
    profile_id: int,
    retailer: str,
    *,
    mode: str,
    max_pages: int,
    auto_apply: bool,
    scheduled: bool,
    store_account_id: int,
) -> None:
    label = retailer.capitalize()
    mode_label = "full check" if mode == "full" else "unshipped check"
    update_job(job_id, status="running", message=f"Starting {label} {mode_label}…")

    def on_progress(data: dict) -> None:
        update_job(job_id, progress=data, message=data.get("message"))

    finished_ok = False
    finish_message: str | None = None
    db = SessionLocal()
    try:
        profile = (
            db.query(BrowserProfile)
            .options(joinedload(BrowserProfile.store_account).joinedload(StoreAccount.store))
            .filter(BrowserProfile.id == profile_id)
            .first()
        )
        if not profile:
            update_job(job_id, status="failed", error="Profile not found.")
            return

        source = "scheduled" if scheduled else "manual"

        def log_finished(message: str) -> None:
            nonlocal finish_message
            finish_message = message
            _add_import_log(
                db,
                profile=profile,
                job_id=job_id,
                mode=mode,
                scheduled=scheduled,
                level="info",
                event_type="check_finished",
                message=message,
            )
            db.commit()

        try:
            order_ids: list[str] | None = None
            if mode == "unshipped":
                order_ids = _unshipped_store_order_numbers(db, store_account_id)
                if not order_ids:
                    _add_import_log(
                        db,
                        profile=profile,
                        job_id=job_id,
                        mode=mode,
                        scheduled=scheduled,
                        level="info",
                        event_type="check_started",
                        message=f"Started {mode_label} ({source}): 0 order(s) queued.",
                    )
                    db.commit()
                    now = datetime.now(timezone.utc)
                    _set_profile_status(
                        db,
                        profile_id,
                        status="ready",
                        last_error=None,
                        unshipped_check_last_run_at=now,
                    )
                    update_job(
                        job_id,
                        status="succeeded",
                        message="No unshipped orders to refresh.",
                        order_count=0,
                    )
                    log_finished("Finished: no unshipped orders to refresh.")
                    finished_ok = True
                    return

            started_msg = f"Started {mode_label} ({source})."
            if mode == "unshipped" and order_ids is not None:
                started_msg = (
                    f"Started {mode_label} ({source}): "
                    f"{len(order_ids)} order(s) queued."
                )
            _add_import_log(
                db,
                profile=profile,
                job_id=job_id,
                mode=mode,
                scheduled=scheduled,
                level="info",
                event_type="check_started",
                message=started_msg,
            )
            db.commit()

            applied = 0
            imported = 0
            tracking_updated = 0
            checked = 0
            marked_personal = 0
            errors: list[str] = []
            apply_user: User | None = None
            on_order = None

            if auto_apply:
                apply_user = _apply_user(db)

                def on_order(raw: dict) -> None:
                    """Apply + log + commit one captured order immediately."""
                    nonlocal applied, imported, tracking_updated, checked, marked_personal
                    assert apply_user is not None
                    try:
                        payload = StoreOrderImportPayload.model_validate(raw)
                        store_order_number = _external_order_id_from_payload(payload)
                        existed_before = False
                        was_personal = False
                        tracking_before: set[str] = set()
                        if store_order_number:
                            prior_id, tracking_before = _order_tracking_numbers(
                                db, store_order_number
                            )
                            existed_before = prior_id is not None
                            if prior_id is not None:
                                prior = db.query(Order).filter(Order.id == prior_id).first()
                                was_personal = prior is not None and prior.status == "personal"
                        postal = shipping_postal_code_from_payload(payload)
                        order_id = apply_store_order_payload(
                            db,
                            payload,
                            apply_user,
                            store_account_id=store_account_id,
                            commit=False,
                        )
                        order = db.query(Order).filter(Order.id == order_id).first()
                        is_personal = order is not None and order.status == "personal"
                        became_personal = is_personal and not was_personal
                        if store_order_number:
                            _, tracking_after = _order_tracking_numbers(db, store_order_number)
                            event = _log_auto_apply_result(
                                db,
                                profile=profile,
                                job_id=job_id,
                                mode=mode,
                                scheduled=scheduled,
                                store_order_number=store_order_number,
                                existed_before=existed_before,
                                tracking_before=tracking_before,
                                order_id=order_id,
                                tracking_after=tracking_after,
                                marked_personal=became_personal,
                                postal_code=postal if isinstance(postal, str) else None,
                            )
                            if event == "order_marked_personal":
                                marked_personal += 1
                            elif event == "order_imported":
                                imported += 1
                            elif event == "tracking_updated":
                                tracking_updated += 1
                            else:
                                checked += 1
                        applied += 1
                        db.commit()
                        label_num = store_order_number or "order"
                        update_job(
                            job_id,
                            message=(
                                f"Processed {label_num} "
                                f"({applied} applied"
                                f"{f', {marked_personal} personal' if marked_personal else ''})…"
                            ),
                            order_count=applied,
                        )
                    except Exception as exc:
                        logger.warning("Auto-apply failed for captured order: %s", exc)
                        failed_order_number: str | None = None
                        try:
                            ext = raw.get("externalOrder") if isinstance(raw, dict) else None
                            if isinstance(ext, dict):
                                failed_order_number = str(ext.get("id") or "").strip() or None
                        except Exception:
                            pass
                        try:
                            db.rollback()
                        except Exception:
                            pass
                        errors.append(str(exc))
                        try:
                            _add_import_log(
                                db,
                                profile=profile,
                                job_id=job_id,
                                mode=mode,
                                scheduled=scheduled,
                                level="error",
                                event_type="order_error",
                                store_order_number=failed_order_number,
                                message=str(exc),
                            )
                            db.commit()
                        except Exception:
                            logger.exception(
                                "Failed to write order_error log for job %s", job_id
                            )
                            try:
                                db.rollback()
                            except Exception:
                                pass

            if retailer == "walmart":
                orders = await run_walmart_import(
                    profile_id,
                    max_pages=max_pages,
                    order_ids=order_ids,
                    on_progress=on_progress,
                    on_order=on_order,
                )
            else:
                orders = await run_amazon_import(
                    profile_id,
                    max_pages=max_pages,
                    order_ids=order_ids,
                    on_progress=on_progress,
                    on_order=on_order,
                )

            now = datetime.now(timezone.utc)
            run_kwargs: dict = {}
            if mode == "full":
                run_kwargs["full_check_last_run_at"] = now
            else:
                run_kwargs["unshipped_check_last_run_at"] = now

            if auto_apply:
                if not orders and not applied:
                    _set_profile_status(
                        db,
                        profile_id,
                        status="ready",
                        last_error="Import finished with zero orders." if mode == "full" else None,
                        **run_kwargs,
                    )
                    update_job(
                        job_id,
                        status="failed" if mode == "full" else "succeeded",
                        error="No orders captured. If you expect orders, try Log in again."
                        if mode == "full"
                        else None,
                        message="No orders captured." if mode != "full" else None,
                        order_count=0,
                    )
                    log_finished(
                        "Finished: no orders captured."
                        if mode == "full"
                        else "Finished: no orders captured for unshipped check."
                    )
                    finished_ok = True
                    return

                _set_profile_status(
                    db,
                    profile_id,
                    status="ready",
                    last_error=None if not errors else f"{len(errors)} apply error(s).",
                    touch_last_import=True,
                    last_import_at=now,
                    **run_kwargs,
                )
                msg = f"Updated {applied} order(s)."
                if marked_personal:
                    msg += f" {marked_personal} marked personal."
                if errors:
                    msg += f" {len(errors)} failed."
                update_job(
                    job_id,
                    status="succeeded" if applied else "failed",
                    message=msg,
                    order_count=applied,
                    error=errors[0] if applied == 0 and errors else None,
                )
                summary = (
                    f"Finished: {imported} imported, {tracking_updated} tracking updated, "
                    f"{checked} checked"
                )
                if marked_personal:
                    summary += f", {marked_personal} personal"
                if errors:
                    summary += f", {len(errors)} failed"
                summary += "."
                log_finished(summary)
                finished_ok = True
            else:
                if not orders:
                    _set_profile_status(
                        db,
                        profile_id,
                        status="ready",
                        last_error="Import finished with zero orders." if mode == "full" else None,
                        **run_kwargs,
                    )
                    update_job(
                        job_id,
                        status="failed" if mode == "full" else "succeeded",
                        error="No orders captured. If you expect orders, try Log in again."
                        if mode == "full"
                        else None,
                        message="No orders captured." if mode != "full" else None,
                        order_count=0,
                    )
                    log_finished(
                        "Finished: no orders captured."
                        if mode == "full"
                        else "Finished: no orders captured for unshipped check."
                    )
                    finished_ok = True
                    return

                token, review_url = post_bulk_session(orders)
                _set_profile_status(
                    db,
                    profile_id,
                    status="ready",
                    last_error=None,
                    touch_last_import=True,
                    last_import_at=now,
                    **run_kwargs,
                )
                update_job(
                    job_id,
                    status="succeeded",
                    message=f"Captured {len(orders)} order(s).",
                    token=token,
                    review_url=review_url,
                    order_count=len(orders),
                )
                log_finished(f"Finished: captured {len(orders)} order(s) for import review.")
                finished_ok = True
        except LoginRequiredError as exc:
            _set_profile_status(
                db, profile_id, status="login_required", last_error=str(exc)
            )
            update_job(job_id, status="failed", error=str(exc))
            if finish_message is None:
                log_finished(f"Stopped: login required ({exc}).")
            finished_ok = True
        except Exception as exc:
            logger.exception("%s %s failed for profile %s", label, mode_label, profile_id)
            _set_profile_status(db, profile_id, status="error", last_error=str(exc))
            update_job(job_id, status="failed", error=str(exc))
            if finish_message is None:
                try:
                    log_finished(f"Stopped: {exc}")
                except Exception:
                    logger.exception("Failed to write check_finished log for job %s", job_id)
            finished_ok = True
        finally:
            await session_manager.close_session(profile_id)
            # Never leave the profile stuck in "importing" if the task ends unexpectedly.
            if not finished_ok:
                try:
                    _set_profile_status(
                        db,
                        profile_id,
                        status="error",
                        last_error="Import ended unexpectedly.",
                    )
                    update_job(
                        job_id,
                        status="failed",
                        error="Import ended unexpectedly.",
                    )
                    if finish_message is None:
                        log_finished("Stopped: import ended unexpectedly.")
                except Exception:
                    logger.exception(
                        "Failed to clear stuck importing status for profile %s", profile_id
                    )
    finally:
        db.close()
