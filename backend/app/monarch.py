"""Matching and receipt transactions, independent of Monarch's browser transport."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Payment, MonarchConnection, MonarchDeposit, MonarchConfirmation, MonarchPaymentReceipt

PACIFIC = ZoneInfo("America/Los_Angeles")
SEARCH_LIMIT = 20000
RESULT_LIMIT = 30


def next_daily_check(now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(PACIFIC)
    run = datetime.combine(now.date(), time(8), PACIFIC)
    if run <= now:
        run += timedelta(days=1)
    return run.astimezone(timezone.utc)


def payment_amount(payment):
    return sum((Decimal(li.amount) for li in payment.line_items), Decimal(0)) + Decimal(payment.payment_bonus or 0)


def payment_fingerprint(payment):
    value = {
        "id": payment.id, "group": payment.buying_group_id, "reference": payment.payment_id,
        "bonus": str(payment.payment_bonus),
        "requested": str(payment.payment_requested_at), "sent": str(payment.payment_sent_at),
        "received": str(payment.payment_received_at),
        "lines": sorted((li.id, li.item_id, str(li.amount)) for li in payment.line_items),
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def payment_choice(payment):
    return {"id": payment.id, "reference": payment.payment_id, "buying_group_id": payment.buying_group_id,
            "buying_group_name": payment.buying_group.name if payment.buying_group else "Unknown group",
            "amount": str(payment_amount(payment)), "fingerprint": payment_fingerprint(payment),
            "date": (payment.payment_sent_at or payment.payment_requested_at).date().isoformat()}


def suggest_matches(deposit, payments, rules, *, search_limit=SEARCH_LIMIT):
    """Bounded iterative subset search per group; never imply a unique result when truncated."""
    if deposit.pending or deposit.amount <= 0:
        return {"suggestions": [], "search_truncated": False}
    text = f"{deposit.merchant} {deposit.description}".casefold()
    groups = {}
    for payment in payments:
        keywords = rules.get(str(payment.buying_group_id), [])
        if not any(word.strip().casefold() in text for word in keywords if word.strip()):
            continue
        start = (payment.payment_sent_at or payment.payment_requested_at).date()
        amount = payment_amount(payment)
        if payment.payment_received_at is None and 0 <= (deposit.posted_date - start).days <= 30 and 0 < amount <= deposit.amount:
            groups.setdefault(payment.buying_group_id, []).append((payment.id, amount))
    results, steps, truncated = [], 0, False
    for candidates in groups.values():
        if len(candidates) > 100:
            truncated = True
            continue
        candidates.sort(key=lambda row: row[0])
        stack = [(0, Decimal(0), [])]
        while stack:
            steps += 1
            if steps > search_limit or len(results) >= RESULT_LIMIT:
                truncated = True
                break
            index, total, selected = stack.pop()
            if total == deposit.amount:
                results.append({"payment_ids": selected, "reason": "Sender keyword, exact total, and within 30 days"})
                continue
            if index >= len(candidates) or total > deposit.amount:
                continue
            pid, amount = candidates[index]
            stack.append((index + 1, total, selected))
            stack.append((index + 1, total + amount, selected + [pid]))
        if truncated:
            break
    return {"suggestions": results, "search_truncated": truncated}


def deposit_evidence(deposit):
    return {"id": deposit.id, "transaction_id": deposit.transaction_id,
            "account_id": deposit.account_id, "account_name": deposit.account_name,
            "posted_date": deposit.posted_date.isoformat(), "amount": str(deposit.amount),
            "merchant": deposit.merchant, "description": deposit.description, "revision": deposit.revision}


def require_connection(db: Session, *, lock=False):
    query = db.query(MonarchConnection).filter_by(id=1)
    if lock:
        query = query.with_for_update()
    connection = query.populate_existing().first()
    if not connection:
        raise HTTPException(409, "Connect Monarch in Settings first.")
    return connection


def lock_editable_payment(db: Session, payment_id: int):
    # All receipt confirmations and payment mutations acquire the payment row lock.
    payment = db.query(Payment).filter_by(id=payment_id).with_for_update().populate_existing().first()
    if not payment:
        raise HTTPException(404, "Payment not found")
    if db.get(MonarchPaymentReceipt, payment_id):
        raise HTTPException(409, "Undo the Monarch receipt confirmation before changing this payment.")
    return payment


def confirm_receipt(db, deposit_id, data, reviewer_id):
    connection = require_connection(db, lock=True)
    deposit = db.query(MonarchDeposit).filter_by(id=deposit_id).with_for_update().populate_existing().first()
    if not deposit:
        raise HTTPException(404, "Deposit not found")
    if (deposit.revision != data.revision or deposit.pending or deposit.dismissed or deposit.amount <= 0
            or deposit.account_id not in connection.selected_account_ids):
        raise HTTPException(409, "Deposit changed or is ineligible. Refresh the review.")
    if db.query(MonarchConfirmation).filter_by(active_deposit_id=deposit.id).first():
        raise HTTPException(409, "Deposit already confirmed")
    ids = [row.id for row in data.payments]
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "Select each payment only once")
    payments = db.query(Payment).filter(Payment.id.in_(ids)).order_by(Payment.id).with_for_update().populate_existing().all()
    expected = {row.id: row.fingerprint for row in data.payments}
    if len(payments) != len(ids):
        raise HTTPException(409, "A selected payment was deleted. Refresh the review.")
    for payment in payments:
        db.expire(payment, ["line_items"])
        if (payment.payment_received_at is not None or db.get(MonarchPaymentReceipt, payment.id)
                or payment_fingerprint(payment) != expected[payment.id] or payment_amount(payment) <= 0):
            raise HTTPException(409, "A payment changed or was already received. Refresh the review.")
    if sum((payment_amount(p) for p in payments), Decimal(0)) != deposit.amount:
        raise HTTPException(409, "Selected whole payments must exactly equal the deposit.")
    confirmation = MonarchConfirmation(deposit_id=deposit.id, active_deposit_id=deposit.id,
        reviewer_id=reviewer_id, evidence={"deposit": deposit_evidence(deposit),
                                         "payments": [payment_choice(p) for p in payments]})
    db.add(confirmation)
    db.flush()
    # Match the app's existing date-only payment convention (no local-date rollover).
    received = datetime.combine(deposit.posted_date, time(12), timezone.utc)
    for payment in payments:
        payment.payment_received_at = received
        db.add(MonarchPaymentReceipt(payment_id=payment.id, confirmation_id=confirmation.id))
    db.commit()
    return {"confirmation_id": confirmation.id}


def undo_receipt(db, confirmation_id, reviewer_id):
    require_connection(db, lock=True)
    confirmation = db.query(MonarchConfirmation).filter_by(id=confirmation_id).with_for_update().first()
    if not confirmation or confirmation.undone_at:
        raise HTTPException(409, "Confirmation is no longer active")
    links = db.query(MonarchPaymentReceipt).filter_by(confirmation_id=confirmation.id).all()
    ids = sorted(link.payment_id for link in links)
    payments = db.query(Payment).filter(Payment.id.in_(ids)).order_by(Payment.id).with_for_update().all()
    for payment in payments:
        payment.payment_received_at = None  # confirmations only accept previously unreceived payments
    for link in links:
        db.delete(link)
    confirmation.active_deposit_id = None
    confirmation.undone_at = datetime.now(timezone.utc)
    confirmation.undone_by = reviewer_id
    db.commit()
    return {"undone": True}


def upsert_deposits(db, rows, start_date, end_date, selected_accounts):
    """Called only after a complete capture. Missing records invalidate prior review versions."""
    now, seen = datetime.now(timezone.utc), set()
    for row in rows:
        if row["account_id"] not in selected_accounts or not start_date <= row["posted_date"] <= end_date:
            continue
        seen.add(row["transaction_id"])
        deposit = db.query(MonarchDeposit).filter_by(transaction_id=row["transaction_id"]).with_for_update().first()
        if deposit is None:
            if row["amount"] > 0:
                db.add(MonarchDeposit(**row, revision=1, last_seen_at=now))
        else:
            changed = any(getattr(deposit, key) != value for key, value in row.items())
            if changed:
                for key, value in row.items():
                    setattr(deposit, key, value)
                deposit.revision += 1
            deposit.last_seen_at = now
    # A deleted deposit, or one now outside the date/account filter, must not remain confirmable.
    for deposit in db.query(MonarchDeposit).filter(MonarchDeposit.account_id.in_(selected_accounts),
            MonarchDeposit.posted_date.between(start_date, end_date)).with_for_update().all():
        if deposit.transaction_id not in seen and not deposit.pending:
            deposit.pending = True
            deposit.revision += 1
    return len(seen)
