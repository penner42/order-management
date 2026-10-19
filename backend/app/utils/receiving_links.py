"""Manual receipt assignments in the existing persisted receiving-review queue."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import Item, Order, ReceivingReviewPending, ShipmentItem
from app.models.item import ItemStatus
from app.schemas.item import ItemSplitRequest, ItemBulkUpdateEntry
from app.schemas.buying_group import ReceivingReceiptLinkRequest
from app.utils.item_split import split_item_record


OPEN_STATUSES = {ItemStatus.PURCHASED, ItemStatus.SHIPPED, ItemStatus.SUBMITTED, ItemStatus.NEEDS_REVIEW}


def receipt_key(receipt: dict) -> str:
    identity = [(receipt.get("receipt_id") or "").strip().casefold(),
                (receipt.get("tracking_number") or "").strip().casefold(), receipt.get("description")]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()


def applied_receipt_pairs(db: Session, group_id: int) -> set[tuple[str, str]]:
    rows = db.query(Item.receipt_id, Item.receipt_tracking_number).join(Order).filter(
        Order.buying_group_id == group_id, Item.receipt_tracking_number.isnot(None),
    ).all()
    return {((rid or "").strip().casefold(), tracking.strip().casefold())
            for ids, tracking in rows for rid in (ids or "").replace(";", ",").split(",") if tracking}


def receipt_is_applied(receipt: dict, pairs: set[tuple[str, str]]) -> bool:
    rid = (receipt.get("receipt_id") or "").strip().casefold()
    trackings = [receipt.get("tracking_number") or "", *(receipt.get("tracking_numbers") or [])]
    return any((rid, tn.strip().casefold()) in pairs for tn in trackings)


def match_includes_receipt(match: dict, receipt: dict) -> bool:
    """Keep regenerated automatic suggestions from claiming a staged receipt."""
    ids = {(value or "").strip().casefold()
           for value in (match.get("receipt_id") or "").replace(";", ",").split(",")}
    if (receipt.get("receipt_id") or "").strip().casefold() not in ids:
        return False
    tracking = (match.get("tracking_number") or "").strip().casefold()
    source_trackings = [receipt.get("tracking_number") or "", *(receipt.get("tracking_numbers") or [])]
    if tracking not in {tn.strip().casefold() for tn in source_trackings}:
        return False
    # A warehouse receipt can contain distinct product lines with the same ID/TN.
    source_item = (receipt.get("description") or "").strip().casefold()
    matched_item = (match.get("received_item") or "").strip().casefold()
    return not source_item or not matched_item or source_item == matched_item


def pending_row(db: Session, group_id: int) -> ReceivingReviewPending:
    row = db.query(ReceivingReviewPending).filter_by(buying_group_id=group_id).with_for_update().first()
    if row is None:
        raise HTTPException(409, "Receiving review changed. Refresh and select the receipt again.")
    return row


def with_receipt_keys(payload: dict) -> dict:
    return {**payload, "unknown_receipts": [{**r, "receipt_key": receipt_key(r)}
            for r in payload.get("unknown_receipts", [])]}


def store_payload(db: Session, row: ReceivingReviewPending, payload: dict) -> None:
    count = sum(m.get("match_kind") != "not_received" for m in payload.get("matches", []))
    row.payload = payload
    if not count and not payload.get("unknown_receipts"):
        db.delete(row)
    else:
        row.matched_count = count
        row.updated_at = datetime.now(timezone.utc)


def candidate_lines(db: Session, group_id: int, search: str, page: int) -> dict:
    query = db.query(Item).join(Order).filter(Order.buying_group_id == group_id)
    if search.strip():
        escaped = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        term = f"%{escaped}%"
        query = query.filter(or_(Order.store_order_number.ilike(term, escape="\\"),
                                 Item.description.ilike(term, escape="\\"),
                                 Item.receipt_id.ilike(term, escape="\\")))
    total = query.count()
    items = query.options(joinedload(Item.order).joinedload(Order.store),
                          selectinload(Item.payment_line_items),
                          selectinload(Item.shipment_items).joinedload(ShipmentItem.shipment)).order_by(
                              Order.purchase_date.desc(), Item.id.desc()).offset((page - 1) * 25).limit(25).all()
    return {"total": total, "page": page, "items": [{
        "item_id": i.id, "order_id": i.order_id, "store_order_number": i.order.store_order_number,
        "store": i.order.store.name if i.order.store else None,
        "description": i.description, "quantity": i.quantity, "status": i.status.value,
        "receipt_id": i.receipt_id, "receipt_tracking_number": i.receipt_tracking_number,
        "payment_linked": bool(i.payment_line_items),
        "tracking_numbers": [si.shipment.tracking_number for si in i.shipment_items if si.shipment],
    } for i in items]}


def _check_receipt(item: Item, rid: str | None) -> None:
    if (item.receipt_id or "").strip() and (item.receipt_id or "").strip() != (rid or "").strip():
        raise HTTPException(409, "This line already has a different receipt. Split it or correct its receipt on Orders.")


def link_receipt(db: Session, body: ReceivingReceiptLinkRequest) -> dict:
    row = pending_row(db, body.buying_group_id)
    payload = dict(row.payload)
    receipts = list(payload.get("unknown_receipts") or [])
    receipt = next((r for r in receipts if receipt_key(r) == body.receipt_key), None)
    if receipt is None:
        raise HTTPException(409, "Receipt is no longer unknown. Refresh receiving review.")
    item = db.query(Item).filter_by(id=body.item_id).with_for_update().first()
    if item is None or item.order.buying_group_id != body.buying_group_id:
        raise HTTPException(404, "Order line not found in this buying group.")
    matches = list(payload.get("matches") or [])
    if any(m.get("item_id") == item.id and m.get("manual_receipt") for m in matches):
        raise HTTPException(409, "This line already has a pending manual receipt. Apply or unlink it first.")
    tracking = body.receipt_tracking_number.strip()
    allowed = [receipt.get("tracking_number") or "", *(receipt.get("tracking_numbers") or [])]
    if tracking not in [tn.strip() for tn in allowed] or not tracking:
        raise HTTPException(400, "Choose a tracking number reported on this receipt.")
    qty = receipt.get("quantity")
    if qty is None or qty <= 0:
        if not body.confirm_full_quantity:
            raise HTTPException(400, "Confirm that this receipt covers the entire selected line.")
        qty = body.split_quantity if body.split_quantity is not None else item.quantity
    if body.split_quantity is not None:
        if body.split_quantity != qty or not 0 < qty < item.quantity:
            raise HTTPException(400, "Split quantity must match the receipt and be smaller than the line quantity.")
        # Validate the destination before splitting so failures leave quantities intact.
        if body.split_target == "kept":
            _check_receipt(item, receipt.get("receipt_id"))
        original_id = item.id
        keep = qty if body.split_target == "kept" else item.quantity - qty
        split_off = split_item_record(db, item, ItemSplitRequest(keep_quantity=keep, copy_tracking=True))
        if body.split_target == "split_off":
            item = split_off
        # Existing automatic allocations are stale after an explicit split.
        matches = [m for m in matches if m.get("item_id") != original_id]
    elif qty != item.quantity:
        raise HTTPException(409, "Receipt and line quantities differ. Split the line explicitly first.")
    _check_receipt(item, receipt.get("receipt_id"))
    expected = Decimal(item.price_sold or 0) * item.quantity
    try:
        total = Decimal(str(receipt.get("received_ext_total")))
        discrepancy = abs(total - expected) > Decimal("0.01")
    except (InvalidOperation, TypeError):
        discrepancy = False
    match = {
        "item_id": item.id, "order_id": item.order_id,
        "store_order_number": item.order.store_order_number, "description": item.description,
        "quantity": item.quantity, "tracking_number": tracking, "receipt_tracking_number": tracking,
        "price_sold": str(item.price_sold or 0), "expected_amount": str(expected),
        "receipt_id": receipt.get("receipt_id"), "scanned_at": receipt.get("scanned_at"),
        "received_item": receipt.get("description"), "received_qty": receipt.get("quantity"),
        "received_price": receipt.get("received_price"), "received_ext_total": receipt.get("received_ext_total"),
        "received_status": receipt.get("received_status"), "amount_discrepancy": discrepancy,
        "selected": True, "row_id": f"manual:{body.receipt_key}", "manual_receipt_key": body.receipt_key,
        "manual_receipt": receipt, "match_kind": "received",
        "update_receipt_only": bool(item.payment_line_items) or item.status not in OPEN_STATUSES,
    }
    payload["matches"] = [m for m in matches if m.get("item_id") != item.id] + [match]
    payload["unknown_receipts"] = [r for r in receipts if receipt_key(r) != body.receipt_key]
    store_payload(db, row, payload)
    db.commit()
    return with_receipt_keys(payload)


def unlink_receipt(db: Session, group_id: int, key: str) -> dict:
    row = pending_row(db, group_id)
    payload = dict(row.payload)
    matches = list(payload.get("matches") or [])
    match = next((m for m in matches if m.get("manual_receipt_key") == key), None)
    if match is None:
        raise HTTPException(409, "Manual link no longer exists. Refresh receiving review.")
    receipts = {receipt_key(r): r for r in payload.get("unknown_receipts") or []}
    receipts[key] = match["manual_receipt"]
    payload["unknown_receipts"] = list(receipts.values())
    payload["matches"] = [m for m in matches if m is not match]
    store_payload(db, row, payload)
    db.commit()
    return with_receipt_keys(payload)


def apply_manual_link(db: Session, item: Item, entry: ItemBulkUpdateEntry, changes: dict) -> dict:
    if entry.receiving_group_id is None or not entry.receiving_row_id:
        raise HTTPException(400, "Both receiving group and row are required.")
    if set(changes) - {"receipt_id", "receipt_tracking_number", "status", "scanned_at", "price_sold"}:
        raise HTTPException(400, "Apply receipt details separately from other line edits.")
    row = pending_row(db, entry.receiving_group_id)
    # Refresh retains the caller's joined eager loads; lock only the item so
    # PostgreSQL does not try to lock nullable order/buying-group join rows.
    db.refresh(item, with_for_update={"of": Item})
    payload = dict(row.payload)
    match = next((m for m in payload.get("matches", [])
                  if m.get("row_id") == entry.receiving_row_id and m.get("manual_receipt")), None)
    if match is None or match["item_id"] != item.id or item.order.buying_group_id != entry.receiving_group_id:
        raise HTTPException(409, "Manual receipt assignment changed. Refresh receiving review.")
    if item.quantity != match["quantity"]:
        raise HTTPException(409, "Line quantity changed. Unlink and select the line again.")
    _check_receipt(item, match.get("receipt_id"))
    if (changes.get("receipt_id") or "").strip() != (match.get("receipt_id") or "").strip():
        raise HTTPException(400, "Unlink to change the receipt assigned to this line.")
    changes["receipt_tracking_number"] = match["receipt_tracking_number"]
    if item.payment_line_items or item.status not in OPEN_STATUSES:
        changes.pop("status", None)
    else:
        changes["status"] = ItemStatus.SCANNED
    payload["matches"] = [m for m in payload.get("matches", []) if m is not match]
    payload["unmatched"] = [m for m in payload.get("unmatched", []) if m.get("item_id") != item.id]
    store_payload(db, row, payload)
    # No commit: item changes and queue removal commit together in bulk-update.
    return changes
