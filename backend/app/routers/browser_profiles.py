"""Browser automation profiles: CRUD, live login WebSocket, import jobs."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session, joinedload

from app.auth import decode_token, get_current_user
from app.browser_automation.amazon_import import run_amazon_import
from app.browser_automation.common import LoginRequiredError, post_bulk_session
from app.browser_automation.jobs import create_job, get_job, update_job
from app.browser_automation.paths import profile_user_data_dir
from app.browser_automation.session_manager import (
    login_start_url_for_retailer,
    retailer_session_logged_in,
    session_manager,
)
from app.browser_automation.walmart_import import run_walmart_import
from app.database import SessionLocal, get_db
from app.models import BrowserProfile, StoreAccount, User
from app.schemas.browser_profile import (
    BrowserJobRead,
    BrowserProfileCreate,
    BrowserProfileRead,
    ImportStartRequest,
    ImportStartResponse,
    LoginStartResponse,
)

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
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        store_id=store.id if store else None,
        store_name=store.name if store else None,
        store_account_name=account.name if account else None,
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


@router.delete("/{profile_id}", status_code=204)
async def delete_browser_profile(
    profile_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    profile = _get_profile_or_404(db, profile_id)
    await session_manager.close_session(profile_id)
    user_data = profile_user_data_dir(profile_id)
    db.delete(profile)
    db.commit()
    if user_data.is_dir():
        shutil.rmtree(user_data, ignore_errors=True)
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

    login_url = login_start_url_for_retailer(profile.retailer)
    profile.status = "login_in_progress"
    profile.last_error = None
    db.commit()

    try:
        await session_manager.ensure_session(
            profile_id,
            mode="login",
            start_url=login_url,
        )
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


@router.websocket("/{profile_id}/live")
async def live_view(websocket: WebSocket, profile_id: int, token: str | None = Query(None)):
    """Stream CDP screencast frames and accept input events for login."""
    if not token:
        await websocket.close(code=4401)
        return
    username = decode_token(token)
    if not username:
        await websocket.close(code=4401)
        return

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            await websocket.close(code=4401)
            return
        profile = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
        if not profile:
            await websocket.close(code=4404)
            return
    finally:
        db.close()

    await websocket.accept()
    session = session_manager.get(profile_id)
    if not session:
        await websocket.send_json({"type": "error", "message": "No active browser session. Start login first."})
        await websocket.close()
        return

    queue: asyncio.Queue = asyncio.Queue(maxsize=3)
    await session_manager.start_screencast(session, queue)

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
            if msg_type in ("mouse", "key"):
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


@router.post("/{profile_id}/import", response_model=ImportStartResponse)
async def start_import(
    profile_id: int,
    body: ImportStartRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    profile = _get_profile_or_404(db, profile_id)
    if profile.retailer not in SUPPORTED_RETAILERS:
        raise HTTPException(status_code=400, detail=f"Unsupported retailer: {profile.retailer}")
    if profile.status == "login_in_progress":
        raise HTTPException(status_code=400, detail="Finish or cancel login before importing.")
    if profile.status not in ("ready", "login_required", "error", "logged_out"):
        if profile.status == "importing":
            raise HTTPException(status_code=400, detail="An import is already running for this profile.")

    job = create_job(profile_id, "import")
    profile.status = "importing"
    profile.last_error = None
    db.commit()

    asyncio.create_task(_run_import_job(job.id, profile_id, profile.retailer, body.max_pages))

    return ImportStartResponse(job_id=job.id, profile_id=profile_id, status="queued")


@router.get("/jobs/{job_id}", response_model=BrowserJobRead)
def get_browser_job(
    job_id: str,
    _: User = Depends(get_current_user),
):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return BrowserJobRead(**job.to_dict())


async def _run_import_job(job_id: str, profile_id: int, retailer: str, max_pages: int) -> None:
    label = retailer.capitalize()
    update_job(job_id, status="running", message=f"Starting {label} import…")

    def on_progress(data: dict) -> None:
        update_job(job_id, progress=data, message=data.get("message"))

    db = SessionLocal()
    try:
        profile = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
        if not profile:
            update_job(job_id, status="failed", error="Profile not found.")
            return

        try:
            if retailer == "walmart":
                orders = await run_walmart_import(
                    profile_id, max_pages=max_pages, on_progress=on_progress
                )
            else:
                orders = await run_amazon_import(
                    profile_id, max_pages=max_pages, on_progress=on_progress
                )
            if not orders:
                update_job(
                    job_id,
                    status="failed",
                    error="No orders captured. If you expect orders, try Log in again.",
                )
                profile.status = "ready"
                profile.last_error = "Import finished with zero orders."
                db.commit()
                return

            token, review_url = post_bulk_session(orders)
            profile.status = "ready"
            profile.last_error = None
            profile.last_import_at = datetime.now(timezone.utc)
            db.commit()
            update_job(
                job_id,
                status="succeeded",
                message=f"Captured {len(orders)} order(s).",
                token=token,
                review_url=review_url,
                order_count=len(orders),
            )
        except LoginRequiredError as exc:
            profile.status = "login_required"
            profile.last_error = str(exc)
            db.commit()
            update_job(job_id, status="failed", error=str(exc))
        except Exception as exc:
            logger.exception("%s import failed for profile %s", label, profile_id)
            profile.status = "error"
            profile.last_error = str(exc)
            db.commit()
            update_job(job_id, status="failed", error=str(exc))
        finally:
            await session_manager.close_session(profile_id)
    finally:
        db.close()
