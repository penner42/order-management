"""Read-only reconciliation of local received units against BuyingGroup receipts."""
from collections import defaultdict
from datetime import datetime, timezone
import json

from sqlalchemy import or_, func
from sqlalchemy.orm import joinedload, selectinload

from app.models import BuyingGroup, Item, Order, ShipmentItem
from app.models.item import ItemStatus
from app.utils.buying_group_apis import buyinggroup as api
from app.utils.buying_group_apis.submit_tracking import buyinggroup_group_ready


def _ids(value):
    return {part.strip().casefold() for part in (value or "").replace(";", ",").split(",") if part.strip()}


def _product(row):
    for key in ("description", "item_name", "item", "product", "title", "name"):
        value = row.raw.get(key)
        if isinstance(value, dict):
            value = value.get("name") or value.get("title")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _line(item):
    return {
        "item_id": item.id, "order_id": item.order_id,
        "store_order_number": item.order.store_order_number,
        "description": item.description, "quantity": item.quantity,
        "status": item.status.value, "receipt_id": item.receipt_id,
        "receipt_tracking_number": item.receipt_tracking_number,
        "tracking_numbers": sorted({link.shipment.tracking_number for link in item.shipment_items
                                    if link.shipment and link.shipment.tracking_number}),
        "scanned_at": item.scanned_at.isoformat() if item.scanned_at else None,
        "reward": str(item.reward) if item.reward is not None else None,
        "payment_linked": bool(item.payment_line_items),
    }


def reconcile_receipts(items, receipts):
    """Compare receipt totals; never assign a multi-receipt item's full qty twice.

    BG's receipt endpoint does not always supply product identities. Receipt
    totals can establish an excess, but balanced totals cannot verify products.
    """
    remote = defaultdict(list)
    for receipt in receipts:
        if receipt.receipt_id:
            remote[receipt.receipt_id.strip().casefold()].append(receipt)
    linked = defaultdict(list)
    unlinked = []
    for item in items:
        ids = _ids(item.receipt_id)
        if not ids:
            unlinked.append(item)
        for rid in ids:
            linked[rid].append(item)
    findings = []
    for rid, lines in sorted(linked.items()):
        rows = remote.get(rid, [])
        active = [row for row in rows if not row.void]
        notes = []
        # The same local line may refer to several receipts. Its quantity is an
        # upper bound for each, not a known allocation to any particular receipt.
        certain = sum(item.quantity for item in lines if len(_ids(item.receipt_id)) == 1)
        possible = sum(item.quantity for item in lines)
        ambiguous = certain != possible
        if ambiguous:
            notes.append("Some lines link multiple receipts; their allocation is unknown. Do not add their quantities across receipts.")
        fingerprints = [json.dumps(row.raw, sort_keys=True, default=str) if row.raw else
                        repr((row.tracking_id, row.alt_tracking_id, row.quantity, row.total, row.order_id))
                        for row in active]
        duplicate_rows = len(fingerprints) != len(set(fingerprints))
        known_qty = bool(rows) and not duplicate_rows and all(
            row.quantity is not None and row.quantity >= 0 for row in active)
        warehouse_qty = sum(row.quantity for row in active) if known_qty else None
        if not rows:
            notes.append("Receipt was not returned by BG. Missing history is not treated as zero received.")
        elif duplicate_rows:
            notes.append("BG returned identical receipt rows; the warehouse quantity cannot be verified.")
        elif not known_qty:
            notes.append("BG did not provide a valid quantity for every receipt line.")
        elif not active:
            notes.append("BG has voided every line of this receipt.")
        if len(active) > 1:
            notes.append("Totals include multiple BG receipt lines. Product-by-product allocation is not verified.")
        excess = max(certain - warehouse_qty, 0) if warehouse_qty is not None else None
        if excess:
            status = "excess"
        elif warehouse_qty is None or ambiguous:
            status = "unverified"
        else:
            status = "within_quantity"
        findings.append({
            "receipt_id": rows[0].receipt_id if rows else rid,
            "status": status, "bg_quantity": warehouse_qty,
            "local_quantity_min": certain, "local_quantity_max": possible,
            "excess_quantity": excess, "notes": notes,
            "bg_lines": [{"tracking_number": r.tracking_id, "alternate_tracking": r.alt_tracking_id,
                          "description": _product(r),
                          "quantity": r.quantity, "void": r.void, "bg_order_id": r.order_id,
                          "received_at": r.created_dt, "total": str(r.total) if r.total is not None else None,
                          "note": r.note} for r in rows],
            "items": [_line(item) for item in lines],
        })
    for item in unlinked:
        findings.append({
            "receipt_id": None, "status": "unverified", "bg_quantity": None,
            "local_quantity_min": item.quantity, "local_quantity_max": item.quantity,
            "excess_quantity": None, "bg_lines": [], "items": [_line(item)],
            "notes": ["Received locally without a receipt ID; compare tracking and BG history manually."],
        })
    priority = {"excess": 0, "unverified": 1, "within_quantity": 2}
    return sorted(findings, key=lambda finding: priority[finding["status"]])


def build_receiving_audit(db, group_id=None):
    groups = db.query(BuyingGroup).filter(func.lower(BuyingGroup.api_framework) == "buyinggroup")
    if group_id is not None:
        groups = groups.filter(BuyingGroup.id == group_id)
    results = []
    for group in groups.order_by(func.lower(BuyingGroup.name)).all():
        result = {"group_id": group.id, "group_name": group.name, "error": None, "findings": [],
                  "local_item_count": 0, "receipt_row_count": 0}
        results.append(result)
        items = db.query(Item).join(Order).filter(Order.buying_group_id == group.id).filter(or_(
            Item.status == ItemStatus.SCANNED, Item.scanned_at.isnot(None),
            func.trim(func.coalesce(Item.receipt_id, "")) != "",
            func.trim(func.coalesce(Item.receipt_tracking_number, "")) != "",
        )).options(joinedload(Item.order), selectinload(Item.payment_line_items),
                   selectinload(Item.shipment_items).joinedload(ShipmentItem.shipment)).order_by(Item.id).all()
        result["local_item_count"] = len(items)
        if not items:
            continue
        ready, reason = buyinggroup_group_ready(group)
        if not ready:
            result["error"] = reason
            continue
        try:
            receipts = api.list_receipts(base_url=group.base_url, username=group.api_username,
                                         password=group.api_password, require_complete=True)
        except Exception:
            # Provider exceptions can include raw responses. Keep diagnostics
            # out of this report and never describe a failed fetch as clean.
            result["error"] = "Could not retrieve complete BG receipt history. Check the group connection and try again."
            continue
        result["receipt_row_count"] = len(receipts)
        result["findings"] = reconcile_receipts(items, receipts)
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "groups": results}
