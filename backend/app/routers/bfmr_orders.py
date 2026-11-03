"""Authenticated review of BFMR order-number registration tasks."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import BfmrOrderTask, BuyingGroup, Order, User
from app.schemas.bfmr_orders import BfmrAllocateRequest, BfmrOrderTaskRead, BfmrManualSubmitRequest
from app.bfmr_orders import (account_lock, active_reservation, enqueue_order, fetch_rows,
                             matches_product, retailer, validate_task, snapshots, process_task,
                             existing_order_purchases, remote_purchases, review)
from app.submission_policy import require_group_submissions_enabled

router = APIRouter(prefix="/buying-groups/{group_id}/order-numbers", tags=["bfmr-order-numbers"])


def owned_group(db, group_id, user):
    group = db.get(BuyingGroup, group_id)
    if not group or (user.role != "admin" and group.user_id != user.id):
        raise HTTPException(404, "BFMR group not found")
    if group.api_framework != "bfmr":
        raise HTTPException(400, "Order-number registration is available only for BFMR")
    return group


def owned_task(db, group_id, task_id, user):
    group = owned_group(db, group_id, user)
    task = db.get(BfmrOrderTask, task_id)
    order = db.get(Order, task.order_id) if task else None
    if not task or task.buying_group_id != group_id or not order or (user.role != "admin" and order.user_id != user.id):
        raise HTTPException(404, "Order-number task not found")
    return group, task


def tracker_rows(group):
    try:
        return fetch_rows(group)
    except Exception as exc:
        raise HTTPException(502, "Could not fetch BFMR commitments. Check account configuration and try again.") from exc


def owned_order(db, group_id, order_id, user):
    group = owned_group(db, group_id, user)
    order = db.get(Order, order_id)
    if not order or order.buying_group_id != group_id or (user.role != "admin" and order.user_id != user.id):
        raise HTTPException(404, "BFMR order not found")
    return group, order


def public_snapshot(value):
    return {key: val for key, val in value.items() if key != "account"}


@router.get("/orders/{order_id}/preview")
def preview_order(group_id: int, order_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    group, order = owned_order(db, group_id, order_id, user)
    rows = tracker_rows(group)
    tasks = {t.product_key: t for t in order.bfmr_order_tasks if t.buying_group_id == group_id}
    products = []
    current = snapshots(order, group)
    purchase_rows = {r.purchase_id: r for r in rows if r.purchase_id}
    purchases = [{**purchase, "title": purchase_rows[purchase["purchase_id"]].item_name or purchase_rows[purchase["purchase_id"]].deal_title,
                  "order_no": purchase_rows[purchase["purchase_id"]].order_id}
                 for purchase in remote_purchases(rows, None, purchase_ids=set(purchase_rows))]
    for key, snapshot in current.items():
        task = tasks.get(key)
        existing = existing_order_purchases(rows, snapshot, single_product=len(current) == 1)
        products.append({"product_key": key, "snapshot": public_snapshot(snapshot),
                         "task": BfmrOrderTaskRead.model_validate(task).model_dump(mode="json") if task else None,
                         "existing_match": bool(existing) and sum(p["qty"] for p in existing) == snapshot["quantity"]
                                           and (not task or task.snapshot == snapshot),
                         "purchases": purchases,
                         "candidates": [{"reserve_id": row.reserve_id, "qty": row.qty,
                                         "title": row.item_name or row.deal_title,
                                         "exact_match": matches_product(row, snapshot),
                                         "eligible": active_reservation(row),
                                         "order_deadline": row.raw.get("order_deadline"),
                                         "reason": None if active_reservation(row) else "Commitment is expired or is no longer an unassigned reservation"}
                                        for row in rows if active_reservation(row) or (
                                            matches_product(row, snapshot) and row.reserve_id
                                            and (row.status or "").strip().casefold() == "reserved"
                                            and not row.purchase_id and not row.order_id)]})
    return {"order_id": order.id, "order_no": order.store_order_number, "products": products}


@router.post("/orders/{order_id}/reconcile", response_model=list[BfmrOrderTaskRead])
def reconcile_order(group_id: int, order_id: int, body: BfmrManualSubmitRequest,
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Link confirmed purchases locally. This operation never writes to BFMR."""
    group, order = owned_order(db, group_id, order_id, user)
    if len({p.product_key for p in body.products}) != len(body.products):
        raise HTTPException(400, "Choose each product once")
    if any(p.allocations for p in body.products):
        raise HTTPException(400, "Linking existing purchases does not accept new commitments")
    with account_lock(db, group) as acquired:
        if not acquired:
            raise HTTPException(409, "BFMR account is busy. Try again shortly.")
        db.query(Order.id).filter(Order.id == order_id).with_for_update().one()
        db.refresh(order)
        db.expire(order, ["items", "bfmr_order_tasks"])
        if order.buying_group_id != group_id:
            raise HTTPException(409, "Order account changed. Refresh the preview.")
        current = snapshots(order, group)
        tasks = {t.product_key: t for t in order.bfmr_order_tasks if t.buying_group_id == group_id}
        for product in body.products:
            snapshot = current.get(product.product_key)
            if not snapshot or public_snapshot(snapshot) != product.snapshot:
                raise HTTPException(409, "Order details changed. Refresh the preview before linking.")
            task = tasks.get(product.product_key)
            if task and task.snapshot != snapshot:
                raise HTTPException(409, "Registered order details changed. Review BFMR before correcting purchases.")
        rows = tracker_rows(group)
        claimed = {}
        # One remote purchase cannot stand for two local products or orders.
        for other in db.query(BfmrOrderTask).filter_by(buying_group_id=group_id).all():
            for allocation in other.allocations:
                if allocation["status"] == "submitted":
                    claimed.setdefault(allocation.get("purchase_id"), set()).add((other.order_id, other.product_key))
        results = []
        for product in body.products:
            task = tasks.get(product.product_key)
            if task is None:
                task = BfmrOrderTask(order_id=order_id, buying_group_id=group_id, product_key=product.product_key,
                                     snapshot=current[product.product_key], allocations=[], status="review", attempts=0)
                db.add(task)
            if task.status != "submitted":
                existing = (remote_purchases(rows, task, purchase_ids=set(product.purchase_ids)) if product.purchase_ids
                            else existing_order_purchases(rows, task.snapshot, single_product=len(current) == 1))
                confirmed = {a["purchase_id"] for a in task.allocations if a["status"] == "submitted"}
                found = {a["purchase_id"] for a in existing}
                identity = (order_id, product.product_key)
                if any(claimed.get(pid, set()) - {identity} for pid in found):
                    review(task, "This BFMR purchase is already linked to another product or order. Select a different commitment.")
                elif product.purchase_ids and found != set(product.purchase_ids):
                    review(task, "A selected BFMR purchase is no longer active. Refresh commitments and select again.")
                elif existing and sum(a["qty"] for a in existing) == task.snapshot["quantity"] and confirmed <= found:
                    if product.purchase_ids:
                        for purchase in existing:
                            purchase["order_no"] = next(row.order_id for row in rows if row.purchase_id == purchase["purchase_id"])
                    task.allocations = existing
                    task.status = "submitted"
                    task.message = "Existing BFMR submission linked."
                    task.next_attempt_at = None
                elif existing:
                    review(task, "Existing BFMR purchases do not match the saved quantity or confirmed purchases. Review My Tracker.")
                else:
                    review(task, "No matching BFMR purchase found. Select a commitment to register the saved order number, or check My Tracker and the saved product ID.")
            for allocation in task.allocations:
                if allocation["status"] == "submitted":
                    claimed.setdefault(allocation.get("purchase_id"), set()).add((order_id, product.product_key))
            results.append(task)
        db.commit()
        return results


@router.post("/orders/{order_id}/submit", response_model=list[BfmrOrderTaskRead])
def submit_order(group_id: int, order_id: int, body: BfmrManualSubmitRequest,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_group_submissions_enabled()
    group, order = owned_order(db, group_id, order_id, user)
    if any(p.purchase_ids for p in body.products):
        raise HTTPException(400, "Use reconciliation to link existing purchases")
    if len({p.product_key for p in body.products}) != len(body.products):
        raise HTTPException(400, "Choose each product once")
    with account_lock(db, group) as acquired:
        if not acquired:
            raise HTTPException(409, "BFMR account is busy. Try again shortly.")
        # Also coordinate task creation with imports/assignment transactions.
        db.query(Order.id).filter(Order.id == order_id).with_for_update().one()
        db.refresh(order)
        db.expire(order, ["items", "bfmr_order_tasks"])
        if order.buying_group_id != group_id:
            raise HTTPException(409, "Order account changed. Refresh the preview.")
        current = snapshots(order, group)
        tasks = {t.product_key: t for t in order.bfmr_order_tasks if t.buying_group_id == group_id}
        for product in body.products:
            snapshot = current.get(product.product_key)
            if not snapshot or public_snapshot(snapshot) != product.snapshot:
                raise HTTPException(409, "Order details changed. Refresh the preview before submitting.")
            selected = [a.model_dump() for a in product.allocations]
            task = tasks.get(product.product_key)
            if task and task.snapshot != snapshot:
                raise HTTPException(409, "Registered order details changed. Review BFMR before correcting purchases.")
            if task and task.status == "submitted":
                continue
            completed = [a for a in task.allocations if a["status"] == "submitted"] if task else []
            if len({a["reserve_id"] for a in selected}) != len(selected):
                raise HTTPException(400, "Choose each reservation once per product")
            if selected and sum(a["qty"] for a in selected + completed) != snapshot["quantity"]:
                raise HTTPException(400, "Completed and selected quantities must equal the product quantity")
            if not selected and not (task and task.allocations):
                raise HTTPException(400, "Select commitment quantities before submitting")
        results = []
        for product in body.products:
            task = tasks.get(product.product_key)
            if task is None:
                task = BfmrOrderTask(order_id=order_id, buying_group_id=group_id, product_key=product.product_key,
                                     snapshot=current[product.product_key], allocations=[], status="review", attempts=0)
                db.add(task)
                db.flush()
            if task.status == "submitted":
                results.append(task)
                continue
            # An uncertain write must be reconciled using its original journal.
            if not any(a["status"] == "sending" for a in task.allocations):
                if product.allocations:
                    completed = [a for a in task.allocations if a["status"] == "submitted"]
                    task.allocations = completed + [{**a.model_dump(), "status": "planned"} for a in product.allocations]
                elif any(a["status"] == "rejected" for a in task.allocations):
                    task.status = "review"
                    task.message = "Choose fresh allocations for the rejected commitment."
                    db.commit()
                    results.append(task)
                    continue
            task.attempts = 0
            process_task(db, task, group, manual=True)
            results.append(task)
        return results


@router.get("", response_model=list[BfmrOrderTaskRead])
def list_tasks(group_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    owned_group(db, group_id, user)
    q = db.query(BfmrOrderTask).join(Order, Order.id == BfmrOrderTask.order_id).filter(BfmrOrderTask.buying_group_id == group_id)
    if user.role != "admin":
        q = q.filter(Order.user_id == user.id)
    return q.order_by(BfmrOrderTask.id.desc()).all()


@router.post("/scan")
def scan_orders(group_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    group = owned_group(db, group_id, user)
    if not group.order_number_submit_enabled:
        raise HTTPException(400, "Enable automatic order-number submission before scanning existing orders")
    q = db.query(Order).filter(Order.buying_group_id == group_id, Order.status != "personal")
    if user.role != "admin":
        q = q.filter(Order.user_id == user.id)
    count = 0
    for order in q.all():
        # Explicit scan also exposes legacy imports without retained SKUs for manual review.
        if not order.imported_from_store:
            name = retailer(order.store.name)
            if name not in {"amazon", "walmart", "costco"}:
                continue
            order.imported_from_store = name
        enqueue_order(db, order)
        count += 1
    db.commit()
    return {"scanned_orders": count}


@router.get("/{task_id}/candidates")
def candidates(group_id: int, task_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    group, task = owned_task(db, group_id, task_id, user)
    rows = tracker_rows(group)
    return [{"reserve_id": row.reserve_id, "qty": row.qty,
             "title": row.item_name or row.deal_title, "model": row.raw.get("item_model_number"),
             "retailer_links": row.raw.get("retailer_links") or [],
             "exact_match": matches_product(row, task.snapshot), "reserved_at": row.raw.get("reserved_at"),
             "order_deadline": row.raw.get("order_deadline")}
            for row in rows if active_reservation(row)]


@router.post("/{task_id}/allocations", response_model=BfmrOrderTaskRead)
def allocate(group_id: int, task_id: int, body: BfmrAllocateRequest,
             db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_group_submissions_enabled()
    group, task = owned_task(db, group_id, task_id, user)
    if not group.order_number_submit_enabled:
        raise HTTPException(400, "Automatic order-number submission is off")
    with account_lock(db, group) as acquired:
        if not acquired:
            raise HTTPException(409, "BFMR account is busy. Try again shortly.")
        db.refresh(task)
        if not validate_task(db, task, group):
            db.commit()
            raise HTTPException(409, "Order details changed; review BFMR before correcting registered purchases")
        if any(a["status"] == "sending" for a in task.allocations):
            raise HTTPException(409, "Previous submission outcome is uncertain. Retry reconciliation before changing allocations.")
        completed = [a for a in task.allocations if a["status"] == "submitted"]
        selected = [a.model_dump() for a in body.allocations]
        if len({a["reserve_id"] for a in selected}) != len(selected):
            raise HTTPException(400, "Choose each reservation once")
        if sum(a["qty"] for a in completed + selected) != task.snapshot["quantity"]:
            raise HTTPException(400, "Completed and selected quantities must equal the saved product quantity")
        available = {row.reserve_id: row for row in tracker_rows(group) if active_reservation(row)}
        for allocation in selected:
            row = available.get(allocation["reserve_id"])
            if row is None or row.qty < allocation["qty"]:
                raise HTTPException(409, "Commitment is no longer active or has insufficient quantity")
        task.allocations = completed + [{**a, "status": "planned"} for a in selected]
        task.status = "pending"
        task.attempts = 0
        task.next_attempt_at = None
        task.message = "Manual allocations queued; availability will be checked again before submission."
        db.commit()
    return task


@router.post("/{task_id}/retry", response_model=BfmrOrderTaskRead)
def retry(group_id: int, task_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_group_submissions_enabled()
    group, task = owned_task(db, group_id, task_id, user)
    if not group.order_number_submit_enabled:
        raise HTTPException(400, "Automatic order-number submission is off")
    with account_lock(db, group) as acquired:
        if not acquired:
            raise HTTPException(409, "BFMR account is busy. Try again shortly.")
        db.refresh(task)
        if not validate_task(db, task, group):
            db.commit()
            raise HTTPException(409, "Order details changed; review BFMR before retrying")
        if task.status == "submitted":
            return task
        if any(a["status"] == "rejected" for a in task.allocations):
            raise HTTPException(409, "Choose fresh allocations for the rejected commitment")
        task.status = "pending"
        task.attempts = 0
        task.next_attempt_at = None
        task.message = "Queued for reconciliation and retry."
        db.commit()
    return task
