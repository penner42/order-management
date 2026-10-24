"""BFMR purchase registration. Imports only enqueue; the worker owns remote writes."""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import threading
import urllib.error

from sqlalchemy import or_, text

from app.config import settings
from app.database import SessionLocal
from app.models import BfmrOrderTask, BuyingGroup, Item, Order
from app.models.item import ItemStatus, RETURN_PROCESS_STATUSES
from app.submission_policy import require_group_submissions_enabled
from app.utils.buying_group_apis import bfmr

logger = logging.getLogger(__name__)
INELIGIBLE = {ItemStatus.CANCELED, *RETURN_PROCESS_STATUSES, ItemStatus.LOST_PACKAGE}
_local_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)


def now():
    return datetime.now(timezone.utc)


def retailer(value):
    return str(value or "").strip().casefold().replace(".com", "")


def account_identity(group):
    # No credentials are persisted in the task or included in logs.
    value = f"{bfmr.resolve_base_url(group.base_url)}|{(group.api_username or '').strip()}"
    return hashlib.sha256(value.encode()).hexdigest()


@contextmanager
def account_lock(db, group):
    """Session advisory lock survives journal commits and coordinates API workers."""
    key = int(account_identity(group)[:15], 16)
    if db.bind.dialect.name != "postgresql":
        lock = _local_locks[key]
        acquired = lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()
        return
    with db.bind.connect() as conn:
        acquired = conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar()
        try:
            yield bool(acquired)
        finally:
            if acquired:
                conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


def import_identity(raw, index):
    sku = str(raw.get("externalSku") or "").strip() or None
    logical = str(raw.get("logicalItemId") or "").strip()
    return {"import_line_key": (logical or sku or f"line:{index}")[:500],
            "external_sku": sku, "product_url": raw.get("productUrl") or None}


def capture_import_metadata(db, order, normalized, store):
    order.imported_from_store = retailer(store)
    db.flush()
    db.expire(order, ["items"])
    by_name = defaultdict(list)
    for index, raw in enumerate(normalized.get("items") or []):
        by_name[str(raw.get("name") or "").strip()].append(import_identity(raw, index))
    for item in order.items:
        matches = by_name.get((item.description or "").strip(), [])
        identities = {(m["external_sku"], m["import_line_key"]) for m in matches}
        if len(identities) == 1:
            match = matches[0]
            for field, value in match.items():
                if value:
                    setattr(item, field, value)


def product_key(item):
    sku = (item.external_sku or "").strip().casefold()
    return f"sku:{sku}" if sku else f"line:{item.import_line_key or item.id}"


def snapshots(order, group):
    products = {}
    if not order.imported_from_store or order.status == "personal" or not (order.store_order_number or "").strip():
        return products
    for item in order.items:
        if item.status in INELIGIBLE:
            continue
        key = product_key(item)
        entry = products.setdefault(key, {
            "retailer": retailer(order.imported_from_store), "sku": item.external_sku,
            "title": item.description, "product_url": item.product_url,
            "order_no": order.store_order_number.strip(), "quantity": 0,
            "account": account_identity(group),
        })
        entry["quantity"] += max(int(item.quantity or 0), 0)
    return {key: value for key, value in products.items() if value["quantity"] > 0}


def enqueue_order(db, order):
    """Called inside the caller's transaction; never performs network IO or commits."""
    db.flush()
    # Serialize concurrent import/assignment transactions for the same order.
    db.query(Order.id).filter(Order.id == order.id).with_for_update().first()
    tasks = db.query(BfmrOrderTask).filter_by(order_id=order.id).all()
    group = db.get(BuyingGroup, order.buying_group_id) if order.buying_group_id else None
    current = snapshots(order, group) if group and group.api_framework == "bfmr" else {}
    known = set()
    changed_registered = any(task.allocations and
                             (not group or task.buying_group_id != group.id or current.get(task.product_key) != task.snapshot)
                             for task in tasks)
    for task in tasks:
        value = current.get(task.product_key) if group and task.buying_group_id == group.id else None
        known.add((task.buying_group_id, task.product_key))
        if value != task.snapshot:
            if task.allocations or task.status == "processing":
                task.status = "review"
                task.message = "Registered or planned order details changed. Review BFMR before making corrections."
            elif value:
                task.snapshot = value
                task.status = "pending"
                task.attempts = 0
                task.next_attempt_at = None
            else:
                task.status = "skipped"
                task.message = "Order or product is no longer eligible for BFMR registration."
        elif value and task.status == "skipped" and not task.allocations:
            task.status = "pending"
            task.next_attempt_at = None
            task.message = None
    if not group or not group.order_number_submit_enabled:
        return
    for key, value in current.items():
        if (group.id, key) not in known:
            db.add(BfmrOrderTask(order_id=order.id, buying_group_id=group.id,
                                 product_key=key, snapshot=value, allocations=[], attempts=0,
                                 status="review" if changed_registered else "pending",
                                 message="Registered order details changed. Review BFMR before submitting new products." if changed_registered else None))


def fetch_rows(group):
    return bfmr.list_my_tracker(base_url=group.base_url, api_key=group.api_username or "",
                                api_secret=group.api_password or "").rows


def matches_product(row, snapshot):
    sku = (snapshot.get("sku") or "").strip().casefold()
    return bool(sku) and any(retailer(name) == snapshot["retailer"] and identifier == sku
                             for name, identifier in bfmr.retailer_identifiers(row))


def active_reservation(row):
    if (row.status or "").strip().casefold() != "reserved" or not row.reserve_id or row.purchase_id or row.order_id:
        return False
    if not row.qty or row.qty < 1:
        return False
    # Only explicit order deadlines apply; tracking_deadline is a different deadline.
    deadline = row.raw.get("order_deadline")
    if deadline not in (None, "", 0, "0"):
        try:
            if str(deadline).replace(".", "", 1).isdigit():
                timestamp = float(deadline)
                parsed = datetime.fromtimestamp(timestamp / 1000 if timestamp > 10**11 else timestamp, tz=timezone.utc)
            else:
                parsed = datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
            if (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed) <= now():
                return False
        except (ValueError, OverflowError, OSError):
            return False
    return True


def reservation_candidates(rows, snapshot):
    return [row for row in rows if active_reservation(row) and matches_product(row, snapshot)]


def review(task, message):
    task.status = "review"
    task.message = message
    task.next_attempt_at = None


def http_status(exc):
    while exc.__cause__ is not None:
        exc = exc.__cause__
    return exc.code if isinstance(exc, urllib.error.HTTPError) else None


def remote_purchases(rows, task, allocation=None):
    excluded = {"reserved", "cancelled", "deadline", "closed", "return", "returned"}
    found = {}
    seen = set()
    for row in rows:
        if not row.purchase_id or not row.order_id or row.order_id.strip().casefold() != task.snapshot["order_no"].casefold():
            continue
        if (row.status or "").casefold() in excluded:
            continue
        if allocation is not None:
            identity_match = row.reserve_id == allocation["reserve_id"]
        else:
            identity_match = matches_product(row, task.snapshot)
        if not identity_match:
            continue
        key = (row.purchase_id, row.shipment_id, row.tracking_number)
        if key in seen:
            continue
        seen.add(key)
        # Multiple shipment rows under one PID count their quantities once per row.
        entry = found.setdefault(row.purchase_id, {"reserve_id": row.reserve_id, "purchase_id": row.purchase_id,
                                                   "qty": 0, "status": "submitted"})
        entry["qty"] += max(int(row.qty or 0), 0)
    return list(found.values())


def validate_task(db, task, group):
    db.flush()
    order = db.get(Order, task.order_id)
    if order:
        db.refresh(order)
        db.expire(order, ["items"])
    if not order or order.buying_group_id != group.id or (order.user_id and group.user_id and order.user_id != group.user_id):
        review(task, "Order no longer belongs to this BFMR account.")
        return False
    if snapshots(order, group).get(task.product_key) != task.snapshot:
        review(task, "Order details changed. Review the saved quantity and BFMR before retrying.")
        return False
    return True


def process_task(db, task, group):
    require_group_submissions_enabled()
    if not group.order_number_submit_enabled or group.api_framework != "bfmr":
        return
    if not validate_task(db, task, group):
        db.commit()
        return
    task.status = "processing"
    task.attempts += 1
    task.next_attempt_at = now() + timedelta(minutes=10)
    db.commit()
    try:
        rows = fetch_rows(group)
        allocations = [dict(a) for a in task.allocations]
        # Recover the write journal first. A transport failure is never blindly replayed.
        for allocation in allocations:
            if allocation["status"] != "sending":
                continue
            candidates = [p for p in remote_purchases(rows, task, allocation)
                          if p["purchase_id"] not in allocation.get("before_purchase_ids", [])]
            if len(candidates) != 1 or candidates[0]["qty"] != allocation["qty"]:
                review(task, "Previous submission outcome is uncertain. Check My Tracker before retrying; no duplicate was sent.")
                db.commit()
                return
            allocation.update(candidates[0])
        task.allocations = allocations
        if not allocations:
            existing = remote_purchases(rows, task)
            if existing:
                if sum(p["qty"] for p in existing) == task.snapshot["quantity"]:
                    task.allocations = existing
                    if not validate_task(db, task, group):
                        db.commit()
                        return
                    task.status = "submitted"
                    task.message = "Existing BFMR purchase(s) reconciled."
                else:
                    review(task, "BFMR already has purchases for this order/product with a different quantity.")
                db.commit()
                return
            if any(row.purchase_id and row.order_id
                   and row.order_id.strip().casefold() == task.snapshot["order_no"].casefold()
                   and matches_product(row, task.snapshot) for row in rows):
                review(task, "An inactive BFMR purchase already uses this order/product. Review My Tracker before registering again.")
                db.commit()
                return
            candidates = reservation_candidates(rows, task.snapshot)
            if len(candidates) != 1 or candidates[0].qty < task.snapshot["quantity"]:
                review(task, "Choose commitment(s): no unique exact retailer/product match with enough quantity.")
                db.commit()
                return
            allocations = [{"reserve_id": candidates[0].reserve_id, "qty": task.snapshot["quantity"], "status": "planned"}]
        if sum(a["qty"] for a in allocations) != task.snapshot["quantity"]:
            review(task, "Allocation quantities must equal the saved product quantity.")
            db.commit()
            return
        for index, allocation in enumerate(allocations):
            if allocation["status"] == "submitted":
                continue
            rows = fetch_rows(group)
            completed_pids = {a.get("purchase_id") for a in allocations if a["status"] == "submitted"}
            existing = [p for p in remote_purchases(rows, task, allocation) if p["purchase_id"] not in completed_pids]
            if existing:
                if len(existing) == 1 and existing[0]["qty"] == allocation["qty"]:
                    allocation.update(existing[0])
                    task.allocations = [dict(a) for a in allocations]
                    db.commit()
                    continue
                review(task, "An existing BFMR purchase conflicts with the planned allocation. Review My Tracker.")
                db.commit()
                return
            candidate = next((row for row in rows if row.reserve_id == allocation["reserve_id"] and active_reservation(row)), None)
            if candidate is None or candidate.qty < allocation["qty"]:
                review(task, "Selected commitment changed or no longer has enough reserved quantity.")
                db.commit()
                return
            db.refresh(group)
            if not group.order_number_submit_enabled or group.api_framework != "bfmr":
                task.status = "pending"
                task.message = "Automatic order-number submission was disabled before sending."
                task.next_attempt_at = None
                db.commit()
                return
            if not validate_task(db, task, group):
                db.commit()
                return
            allocation["status"] = "sending"
            allocation["before_purchase_ids"] = [row.purchase_id for row in rows if row.purchase_id]
            allocations[index] = allocation
            task.allocations = [dict(a) for a in allocations]
            db.commit()  # Intent is durable before any remote mutation.
            try:
                raw = bfmr.create_purchase(base_url=group.base_url, api_key=group.api_username or "", api_secret=group.api_password or "",
                                           reserve_id=allocation["reserve_id"], order_no=task.snapshot["order_no"], qty=allocation["qty"])
            except Exception as exc:
                # Only a rejected POST proves that no purchase was created. A failed
                # verification GET must retain the sending journal for reconciliation.
                if http_status(exc) in (401, 403, 404, 422, 429):
                    allocation["status"] = "planned"
                    task.allocations = [dict(a) for a in allocations]
                raise
            invalid = (raw.get("reservations_response") or {}).get("invalid_items")
            if invalid:
                allocation["status"] = "rejected"
                task.allocations = [dict(a) for a in allocations]
                review(task, "BFMR rejected this allocation. Refresh commitments and choose again.")
                db.commit()
                return
            rows = fetch_rows(group)
            purchases = [p for p in remote_purchases(rows, task, allocation)
                         if p["purchase_id"] not in allocation["before_purchase_ids"]]
            if len(purchases) != 1 or purchases[0]["qty"] != allocation["qty"]:
                review(task, "Submission sent, but BFMR has not confirmed a unique matching purchase. Review My Tracker.")
                db.commit()
                return
            allocation.update(purchases[0])
            task.allocations = [dict(a) for a in allocations]
            db.commit()
        if not validate_task(db, task, group):
            db.commit()
            return
        task.status = "submitted"
        task.message = "Order number registered with BFMR."
        task.next_attempt_at = None
        db.commit()
    except Exception as exc:
        # Store only a classification: provider errors may echo account or order data.
        code = http_status(exc)
        if code in (401, 403, 404, 422):
            review(task, "BFMR rejected the request. Check account configuration and commitment details.")
        elif task.attempts >= 5:
            review(task, "Retry limit reached. Refresh and review BFMR before retrying.")
        else:
            task.status = "retry"
            task.message = "BFMR request failed; the next attempt will reconcile remote purchases before proceeding."
            task.next_attempt_at = now() + timedelta(seconds=min(60 * 2 ** (task.attempts - 1), 900))
        db.commit()
        logger.warning("BFMR order task %s deferred (%s)", task.id, type(exc).__name__)


def worker_tick():
    if not settings.group_submissions_enabled:
        return
    with SessionLocal() as db:
        ids = [row.id for row in db.query(BfmrOrderTask).join(BuyingGroup, BuyingGroup.id == BfmrOrderTask.buying_group_id)
               .filter(BuyingGroup.order_number_submit_enabled.is_(True), BuyingGroup.api_framework == "bfmr",
                       BfmrOrderTask.status.in_(["pending", "retry", "processing"]),
                       or_(BfmrOrderTask.next_attempt_at.is_(None), BfmrOrderTask.next_attempt_at <= now()))
               .order_by(BfmrOrderTask.id).limit(10).all()]
    for task_id in ids:
        with SessionLocal() as db:
            task = db.get(BfmrOrderTask, task_id)
            if task is None:
                continue
            group = db.get(BuyingGroup, task.buying_group_id)
            with account_lock(db, group) as acquired:
                if not acquired:
                    continue
                db.refresh(task)
                if task.status not in {"pending", "retry", "processing"}:
                    continue
                due = task.next_attempt_at
                if due and (due.replace(tzinfo=timezone.utc) if due.tzinfo is None else due) > now():
                    continue
                process_task(db, task, group)


def submit_tracking(db, group, pending):
    """Route each product to its registered PIDs; success is recorded per item.

    Send separately per PID/tracking, since BFMR's tracking-keyed batch result
    cannot distinguish two products in one package when only one is rejected.
    """
    from app.utils.buying_group_apis.submit_tracking import SubmitResult, tracking_number_for_item, mark_item_submitted

    require_group_submissions_enabled()
    with account_lock(db, group) as acquired:
        if not acquired:
            raise ValueError("BFMR account is busy. Try again shortly.")
        rows = fetch_rows(group)
        capacities = defaultdict(int)
        shipped = defaultdict(int)
        purchase_rows = {}
        for row in rows:
            if not row.purchase_id:
                continue
            if row.purchase_id not in purchase_rows or row.needs_tracking_submit():
                purchase_rows[row.purchase_id] = row
            if row.needs_tracking_submit():
                capacities[row.purchase_id] += max(int(row.qty or 0), 0)
            elif row.tracking_number and (row.status or "").casefold() not in {"cancelled", "deadline", "return", "returned"}:
                shipped[(row.purchase_id, row.tracking_number.casefold())] += max(int(row.qty or 0), 0)
        # Remove units already represented by locally submitted items. This leaves
        # recoverable capacity for a previous partial success on an unmarked item.
        done = db.query(Item).join(Order).filter(Order.buying_group_id == group.id,
                                                 Item.status.in_([ItemStatus.SUBMITTED, ItemStatus.SCANNED])).all()
        for item in done:
            tracking = (tracking_number_for_item(item) or "").casefold()
            left = item.quantity
            for pid in (item.submission_api_id or "").split(","):
                key = (pid, tracking)
                take = min(left, shipped[key])
                shipped[key] -= take
                left -= take
        plans = {}
        notes = []
        for item, order, tracking in sorted(pending, key=lambda pair: pair[0].id):
            tasks = db.query(BfmrOrderTask).filter_by(order_id=order.id, buying_group_id=group.id).all()
            task = next((task for task in tasks if task.product_key == product_key(item)), None)
            if tasks:
                if task is None or task.status != "submitted" or not validate_task(db, task, group):
                    notes.append(f"Item {item.id}: review order-number registration first.")
                    continue
                pids = list(dict.fromkeys(a["purchase_id"] for a in task.allocations if a.get("purchase_id")))
            else:
                # Legacy manual registrations are safe only with one purchase for the order.
                pids = list(dict.fromkeys(row.purchase_id for row in rows if row.order_id
                            and row.order_id.strip().casefold() == (order.store_order_number or "").strip().casefold()
                            and row.purchase_id and (row.needs_tracking_submit() or row.tracking_number)))
                if len(pids) != 1:
                    notes.append(f"Item {item.id}: no unique BFMR purchase; register or review the product first.")
                    continue
            needed = item.quantity
            portions = []
            for pid in pids:
                row = purchase_rows.get(pid)
                if row is None or not row.order_id or row.order_id.strip().casefold() != (order.store_order_number or "").strip().casefold():
                    continue
                already = min(needed, shipped[(pid, tracking.casefold())])
                needed -= already
                fresh = min(needed, capacities[pid])
                needed -= fresh
                if already or fresh:
                    portions.append((pid, already, fresh, tracking))
            if needed:
                notes.append(f"Item {item.id}: BFMR purchase capacity does not cover this shipment.")
                continue
            for pid, already, fresh, tn in portions:
                shipped[(pid, tn.casefold())] -= already
                capacities[pid] -= fresh
            plans[item.id] = portions
        grouped = defaultdict(int)
        for portions in plans.values():
            for pid, _already, fresh, tracking in portions:
                if fresh:
                    grouped[(pid, tracking)] += fresh
        succeeded = set()
        for (pid, tracking), qty in grouped.items():
            row = purchase_rows[pid]
            entry = bfmr.BfmrTrackingEntry(reserve_id=row.reserve_id, purchase_id=pid,
                                            order_no=row.order_id, tracking_number=tracking, qty=qty)
            result = bfmr.submit_trackings(base_url=group.base_url, api_key=group.api_username or "",
                                            api_secret=group.api_password or "", trackings=[entry])
            if tracking.casefold() in {tn.casefold() for tn in result.succeeded}:
                succeeded.add((pid, tracking))
            else:
                notes.append("BFMR rejected a shipment allocation; successful allocations will be reconciled on retry.")
        ids_by_item = {}
        for item_id, portions in plans.items():
            if all(not fresh or (pid, tracking) in succeeded for pid, _already, fresh, tracking in portions):
                pids = list(dict.fromkeys(pid for pid, _already, _fresh, _tn in portions))
                ids_by_item[item_id] = (",".join(purchase_rows[pid].my_tracker_id or pid for pid in pids), ",".join(pids))
        for item, _order, _tracking in pending:
            if item.id in ids_by_item:
                display, pid = ids_by_item[item.id]
                mark_item_submitted(item, when=now(), submission_id=display, submission_api_id=pid)
        # Local completion is committed while the account lock is still held.
        db.commit()
        return SubmitResult(message=f"Registered tracking for {len(ids_by_item)} item(s). " + " ".join(notes),
                            affected=len(ids_by_item), item_ids=list(ids_by_item),
                            submission_ids_by_item=ids_by_item)
