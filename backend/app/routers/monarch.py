"""Authenticated Monarch connection, checks, and explicit receipt review."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.auth import get_current_user
from app.database import SessionLocal, get_db
from app.models import BrowserProfile, BuyingGroup, Payment, User, MonarchConnection, MonarchDeposit, MonarchConfirmation, MonarchPaymentReceipt
from app.monarch import (PACIFIC, confirm_receipt, deposit_evidence, next_daily_check, payment_choice,
                        require_connection, suggest_matches, undo_receipt, upsert_deposits, receiving_accounts)
from app.schemas.monarch import MonarchSettingsUpdate, MonarchCheckRequest, MonarchConfirmRequest, MonarchDismissRequest
from app.browser_automation.common import LoginRequiredError
from app.browser_automation.jobs import create_job, get_job, update_job
from app.browser_automation.monarch import capture_monarch, MonarchCaptureError
from app.browser_automation.session_manager import session_manager
from app.routers.browser_profiles import _profile_read

router = APIRouter(prefix="/integrations/monarch", tags=["monarch"])
logger = logging.getLogger(__name__)
_tasks: dict[int, asyncio.Task] = {}


def settings_read(connection):
    if connection is None:
        return {"profile": None, "accounts": [], "selected_account_ids": [], "sender_rules": {},
                "daily_enabled": False, "next_check_at": None, "last_check_at": None, "last_error": None}
    accounts = receiving_accounts(connection.accounts)
    allowed_ids = {account["id"] for account in accounts}
    return {"profile": _profile_read(connection.profile), "accounts": accounts,
            "selected_account_ids": [account_id for account_id in connection.selected_account_ids if account_id in allowed_ids], "sender_rules": connection.sender_rules,
            "daily_enabled": connection.daily_enabled, "next_check_at": connection.next_check_at,
            "last_check_at": connection.last_check_at, "last_error": connection.last_error}


@router.get("")
def get_settings(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return settings_read(db.get(MonarchConnection, 1))


@router.post("/connect")
def connect(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    connection = db.get(MonarchConnection, 1)
    if not connection:
        profile = BrowserProfile(integration="monarch", retailer="monarch", status="logged_out")
        db.add(profile)
        try:
            db.flush()
            connection = MonarchConnection(id=1, browser_profile_id=profile.id)
            db.add(connection)
            db.commit()
        except IntegrityError:
            db.rollback()
            connection = require_connection(db)
    return settings_read(connection)


@router.patch("")
def update_settings(data: MonarchSettingsUpdate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    connection = require_connection(db, lock=True)
    if connection.profile.status in ("importing", "login_in_progress"):
        raise HTTPException(409, "Finish the current check or login before changing settings.")
    if not set(data.selected_account_ids) <= {row["id"] for row in receiving_accounts(connection.accounts)}:
        raise HTTPException(400, "Choose discovered cash or investment accounts.")
    group_ids = {str(row.id) for row in db.query(BuyingGroup).all()}
    if not set(data.sender_rules) <= group_ids:
        raise HTTPException(400, "Unknown buying group in sender rules.")
    if len(data.sender_rules) > 500 or any(len(words) > 50 or any(len(word) > 255 for word in words) for words in data.sender_rules.values()):
        raise HTTPException(400, "Too many sender keywords or keyword too long.")
    connection.selected_account_ids = sorted(set(data.selected_account_ids))
    connection.sender_rules = {key: list(dict.fromkeys(word.strip() for word in words if word.strip())) for key, words in data.sender_rules.items()}
    if data.daily_enabled and not connection.daily_enabled:
        connection.next_check_at = next_daily_check()
    if not data.daily_enabled:
        connection.next_check_at = None
    connection.daily_enabled = data.daily_enabled
    db.commit()
    return settings_read(connection)


def queue_check(db, data, *, scheduled=False):
    connection = require_connection(db, lock=True)
    profile = db.query(BrowserProfile).filter_by(id=connection.browser_profile_id).with_for_update().populate_existing().one()
    if profile.status in ("importing", "login_in_progress") or profile.id in _tasks:
        raise HTTPException(409, "A Monarch check or login is already active.")
    if profile.status not in ("ready", "error"):
        raise HTTPException(409, "Log in to Monarch first.")
    today = datetime.now(PACIFIC).date()
    start, end = data.start_date or today - timedelta(days=90), data.end_date or today
    job = create_job(profile.id, "monarch_check")
    profile.status = "importing"
    profile.last_error = None
    connection.last_error = None
    if scheduled:
        connection.next_check_at = next_daily_check()
    db.commit()
    task = asyncio.create_task(run_check(profile.id, job.id, start, end))
    _tasks[profile.id] = task
    task.add_done_callback(lambda _: _tasks.pop(profile.id, None))
    return job.to_dict()


@router.post("/checks")
async def start_check(data: MonarchCheckRequest, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return queue_check(db, data)


@router.get("/jobs/{job_id}")
def job_status(job_id: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    connection = require_connection(db)
    job = get_job(job_id)
    if not job or job.profile_id != connection.browser_profile_id or job.kind != "monarch_check":
        raise HTTPException(404, "Monarch check not found. Refresh connection status.")
    return job.to_dict()


async def run_check(profile_id, job_id, start, end):
    update_job(job_id, status="running", message="Reading Monarch accounts and transactions")
    db = SessionLocal()
    try:
        connection = require_connection(db)
        selected = list(connection.selected_account_ids)
        db.rollback()  # no DB transaction held while waiting on a browser/network
        session = await session_manager.ensure_session(profile_id, mode="import", retailer="monarch")
        accounts, rows = await capture_monarch(session.page, start, end, selected)
        await session_manager.close_session(profile_id)
        connection = require_connection(db, lock=True)
        allowed_ids = {account["id"] for account in accounts}
        selected = [account_id for account_id in selected if account_id in allowed_ids]
        count = upsert_deposits(db, rows, start, end, selected) if selected else 0
        connection.accounts = accounts
        connection.selected_account_ids = selected
        connection.last_check_at = datetime.now(timezone.utc)
        connection.last_error = None
        connection.profile.status = "ready"
        connection.profile.last_import_at = connection.last_check_at
        db.commit()
        update_job(job_id, status="succeeded", message=f"Checked {count} transactions" if selected else "Accounts discovered. Select receiving accounts to import deposits.", review_url="/payments/monarch")
        logger.info("Monarch check succeeded (job=%s, transactions=%s)", job_id, count)
    except (Exception, asyncio.CancelledError) as exc:
        db.rollback()
        await session_manager.close_session(profile_id)
        login_required = isinstance(exc, LoginRequiredError)
        message = ("Monarch login expired. Reconnect in Settings." if login_required else
                   str(exc) if isinstance(exc, MonarchCaptureError) else "Monarch check failed. Reconnect or retry.")
        connection = require_connection(db, lock=True)
        connection.last_error = message
        connection.profile.status = "login_required" if login_required else "error"
        connection.profile.last_error = message
        db.commit()
        update_job(job_id, status="failed", error=message)
        logger.warning("Monarch check failed (job=%s, login_required=%s)", job_id, login_required)
        if isinstance(exc, asyncio.CancelledError):
            raise
    finally:
        db.close()


async def scheduled_tick():
    db = SessionLocal()
    try:
        connection = db.get(MonarchConnection, 1)
        now = datetime.now(timezone.utc)
        if not connection or not connection.daily_enabled or not connection.selected_account_ids:
            return
        due = connection.next_check_at
        if due and due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if connection.profile.status == "ready" and (due is None or due <= now):
            queue_check(db, MonarchCheckRequest(), scheduled=True)
    finally:
        db.close()


async def stop_checks():
    tasks = list(_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@router.get("/review")
def review(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    connection = require_connection(db)
    payments = db.query(Payment).options(selectinload(Payment.line_items), selectinload(Payment.corrections), selectinload(Payment.buying_group)).filter(
        ~Payment.id.in_(db.query(MonarchPaymentReceipt.payment_id))).all()
    active = {row.active_deposit_id: row for row in db.query(MonarchConfirmation).filter(MonarchConfirmation.active_deposit_id.isnot(None))}
    deposits = []
    for deposit in db.query(MonarchDeposit).filter(MonarchDeposit.amount > 0).order_by(MonarchDeposit.posted_date.desc(), MonarchDeposit.id.desc()).all():
        confirmation = active.get(deposit.id)
        eligible = not deposit.pending and not deposit.dismissed and deposit.account_id in connection.selected_account_ids and not confirmation
        suggestions = suggest_matches(deposit, payments, connection.sender_rules) if eligible else {"suggestions": [], "search_truncated": False}
        deposits.append({**deposit_evidence(deposit), "pending": deposit.pending, "dismissed": deposit.dismissed,
            "eligible": eligible, "confirmation_id": confirmation.id if confirmation else None,
            "confirmation": confirmation.evidence if confirmation else None,
            "evidence_changed": bool(confirmation and confirmation.evidence["deposit"]["revision"] != deposit.revision),
            **suggestions})
    return {"deposits": deposits, "payments": [payment_choice(p) for p in payments]}


@router.post("/deposits/{deposit_id}/confirm")
def confirm(deposit_id: int, data: MonarchConfirmRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        return confirm_receipt(db, deposit_id, data, user.id)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Deposit or payment was already confirmed. Refresh the review.") from None


@router.patch("/deposits/{deposit_id}")
def dismiss(deposit_id: int, data: MonarchDismissRequest, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    require_connection(db, lock=True)
    deposit = db.query(MonarchDeposit).filter_by(id=deposit_id).with_for_update().first()
    if not deposit:
        raise HTTPException(404, "Deposit not found")
    if db.query(MonarchConfirmation).filter_by(active_deposit_id=deposit_id).first():
        raise HTTPException(409, "Undo confirmation before dismissing the deposit.")
    deposit.dismissed = data.dismissed
    deposit.revision += 1
    db.commit()
    return {"dismissed": deposit.dismissed}


@router.post("/confirmations/{confirmation_id}/undo")
def undo(confirmation_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return undo_receipt(db, confirmation_id, user.id)
