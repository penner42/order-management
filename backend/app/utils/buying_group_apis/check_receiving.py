"""Match buying-group received-items / receipts to local unscanned items."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import (
    BuyingGroup,
    Item,
    Order,
    ReceivingReviewPending,
    Shipment,
    ShipmentItem,
    UsabgPoCache,
)
from app.models.item import ItemStatus
from app.utils.buying_group_apis import bfmr as bfmr_api
from app.utils.buying_group_apis import buyinggroup as buyinggroup_api
from app.utils.buying_group_apis import parsefile as parsefile_api
from app.utils.buying_group_apis import usabg as usabg_api
from app.utils.buying_group_apis.submit_tracking import (
    bfmr_group_ready,
    buyinggroup_group_ready,
    parsefile_group_ready,
    usabg_group_ready,
)
from app.utils.dates import to_date_only

# Items that may already be at the warehouse but not marked scanned locally.
# needs_review (under-received split-offs) is intentionally excluded so a later
# full check does not overwrite manual/receiving-apply changes.
_UNSCANNED_STATUSES = (ItemStatus.SHIPPED, ItemStatus.SUBMITTED)

# Already received locally — only surface on review when receipt ID would change.
# needs_review is excluded for the same reason as above.
_SCANNED_OR_LATER_STATUSES = (
    ItemStatus.SCANNED,
    ItemStatus.NEEDS_RETURN,
    ItemStatus.RETURN_STARTED,
    ItemStatus.RETURN_SENT,
    ItemStatus.RETURN_RECEIVED,
    ItemStatus.RETURN_REFUNDED,
    ItemStatus.LOST_PACKAGE,
)

AMOUNT_TOLERANCE = Decimal("0.01")

MATCH_KIND_RECEIVED = "received"
MATCH_KIND_NOT_RECEIVED = "not_received"


@dataclass
class ReceivingMatch:
    item_id: int
    order_id: int
    store_order_number: str | None
    description: str | None
    quantity: int
    tracking_number: str
    price_sold: Decimal | None
    expected_amount: Decimal | None
    receipt_id: str | None
    scanned_at: str | None  # ISO date-time for PATCH
    received_item: str | None
    received_qty: int | None
    received_price: Decimal | None
    received_ext_total: Decimal | None
    received_status: str | None
    amount_discrepancy: bool
    selected: bool = True
    row_id: str = ""
    match_kind: str = MATCH_KIND_RECEIVED
    original_quantity: int | None = None
    # When set on a received line, apply should split the item keeping this qty.
    split_keep_quantity: int | None = None
    # Item is already scanned+; apply should update receipt_id only (not status).
    update_receipt_only: bool = False


@dataclass
class CheckReceivingResult:
    buying_group_id: int
    buying_group_name: str
    token: str | None = None
    session_payload: dict[str, Any] | None = None
    matched_count: int = 0
    not_found_count: int = 0
    void_skipped_count: int = 0
    discrepancy_count: int = 0
    submitted_count: int = 0
    message: str | None = None
    error: str | None = None
    matches: list[ReceivingMatch] = field(default_factory=list)


def _money(value: Decimal | float | int | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _expected_amount(item: Item) -> Decimal | None:
    price = _money(item.price_sold)
    if price is None:
        return None
    qty = item.quantity or 1
    return price * qty


def _amounts_differ(expected: Decimal | None, received: Decimal | None) -> bool:
    if expected is None and received is None:
        return False
    if expected is None or received is None:
        return True
    return abs(expected - received) > AMOUNT_TOLERANCE


def _parse_created_date(value: str | None) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    # Parsefile + BuyingGroup formats.
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y",
        "%m-%d-%Y %H:%M:%S",
        "%m-%d-%Y",
    ):
        try:
            dt = datetime.strptime(text, fmt)
            return to_date_only(dt.replace(tzinfo=timezone.utc))
        except ValueError:
            continue
    try:
        # Handle trailing Z / offset.
        cleaned = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return to_date_only(dt)
    except ValueError:
        return None


def _iso_date(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _index_received_by_tracking(
    rows: list[parsefile_api.ParsefileReceivedItem],
) -> dict[str, list[parsefile_api.ParsefileReceivedItem]]:
    index: dict[str, list[parsefile_api.ParsefileReceivedItem]] = {}
    for row in rows:
        for tn in row.tracking_numbers:
            key = tn.casefold()
            index.setdefault(key, []).append(row)
    return index


def _bfmr_received_total(row: bfmr_api.BfmrTrackerRow) -> Decimal | None:
    """BFMR payout is ``payout_price`` only (no amount_paid / retail fallback)."""
    if row.payout_price is None:
        return None
    qty = row.qty_received if row.qty_received is not None else row.qty
    if qty is not None and qty > 0:
        return (row.payout_price * Decimal(qty)).quantize(Decimal("0.01"))
    return row.payout_price


def _bfmr_receipt_id(row: bfmr_api.BfmrTrackerRow) -> str | None:
    return row.my_tracker_id or row.shipment_id


def _index_bfmr_by_tracking(
    rows: list[bfmr_api.BfmrTrackerRow],
) -> dict[str, list[bfmr_api.BfmrTrackerRow]]:
    index: dict[str, list[bfmr_api.BfmrTrackerRow]] = {}
    for row in rows:
        tn = (row.tracking_number or "").strip()
        if not tn:
            continue
        index.setdefault(tn.casefold(), []).append(row)
    return index


def _is_actionable_match_dict(m: dict[str, Any]) -> bool:
    return m.get("match_kind") != MATCH_KIND_NOT_RECEIVED


def actionable_count_from_payload(payload: dict[str, Any]) -> int:
    matches = payload.get("matches") or []
    return sum(1 for m in matches if isinstance(m, dict) and _is_actionable_match_dict(m))


def save_receiving_review_pending(db: Session, payload: dict[str, Any]) -> int:
    """Upsert or clear pending review for one buying group. Returns actionable match count."""
    buying_group_id = int(payload["buying_group_id"])
    count = actionable_count_from_payload(payload)
    existing = (
        db.query(ReceivingReviewPending)
        .filter(ReceivingReviewPending.buying_group_id == buying_group_id)
        .first()
    )
    if count <= 0:
        if existing is not None:
            db.delete(existing)
        db.commit()
        return 0
    now = datetime.now(timezone.utc)
    if existing is not None:
        existing.payload = payload
        existing.matched_count = count
        existing.updated_at = now
    else:
        db.add(
            ReceivingReviewPending(
                buying_group_id=buying_group_id,
                payload=payload,
                matched_count=count,
                updated_at=now,
            )
        )
    db.commit()
    return count


def list_receiving_review_pending_payloads(db: Session) -> list[dict[str, Any]]:
    from sqlalchemy import func

    rows = (
        db.query(ReceivingReviewPending)
        .join(BuyingGroup, BuyingGroup.id == ReceivingReviewPending.buying_group_id)
        .order_by(func.lower(BuyingGroup.name))
        .all()
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        payload = row.payload
        if not isinstance(payload, dict):
            continue
        if actionable_count_from_payload(payload) > 0:
            out.append(payload)
    return out


def _load_usabg_po_detail_cache(db: Session, buying_group_id: int) -> dict[str, dict[str, Any]]:
    """Return po_number -> detail_json for cached USABG POs."""
    rows = (
        db.query(UsabgPoCache)
        .filter(UsabgPoCache.buying_group_id == buying_group_id)
        .all()
    )
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        po = (row.po_number or "").strip()
        detail = row.detail_json
        if po and isinstance(detail, dict):
            out[po] = detail
    return out


def _persist_usabg_po_detail_cache(
    db: Session,
    buying_group_id: int,
    newly_fetched: dict[str, dict[str, Any]],
    *,
    receiving_status_by_po: dict[str, str | None] | None = None,
) -> None:
    """Insert or update cached PO details. No-op when newly_fetched is empty."""
    if not newly_fetched:
        return
    status_map = receiving_status_by_po or {}
    existing = {
        row.po_number: row
        for row in db.query(UsabgPoCache)
        .filter(
            UsabgPoCache.buying_group_id == buying_group_id,
            UsabgPoCache.po_number.in_(list(newly_fetched.keys())),
        )
        .all()
    }
    now = datetime.now(timezone.utc)
    for po_number, detail in newly_fetched.items():
        if not isinstance(detail, dict):
            continue
        status = status_map.get(po_number)
        row = existing.get(po_number)
        if row is not None:
            row.detail_json = detail
            row.receiving_status = status
            row.fetched_at = now
        else:
            db.add(
                UsabgPoCache(
                    buying_group_id=buying_group_id,
                    po_number=po_number,
                    receiving_status=status,
                    detail_json=detail,
                    fetched_at=now,
                )
            )
    db.commit()


def prune_receiving_review_pending(
    db: Session,
    *,
    buying_group_id: int,
    item_ids: list[int],
) -> tuple[int, bool]:
    """Remove applied item rows from pending snapshot. Returns (remaining_count, deleted)."""
    id_set = {int(i) for i in item_ids if i}
    existing = (
        db.query(ReceivingReviewPending)
        .filter(ReceivingReviewPending.buying_group_id == buying_group_id)
        .first()
    )
    if existing is None:
        return 0, True
    if not id_set:
        count = actionable_count_from_payload(existing.payload if isinstance(existing.payload, dict) else {})
        return count, False
    payload = existing.payload
    if not isinstance(payload, dict):
        db.delete(existing)
        db.commit()
        return 0, True
    matches = payload.get("matches") or []
    new_matches = [
        m
        for m in matches
        if isinstance(m, dict) and int(m.get("item_id") or 0) not in id_set
    ]
    payload = {**payload, "matches": new_matches}
    count = actionable_count_from_payload(payload)
    if count <= 0:
        db.delete(existing)
        db.commit()
        return 0, True
    existing.payload = payload
    existing.matched_count = count
    existing.updated_at = datetime.now(timezone.utc)
    db.commit()
    return count, False


def receiving_check_group_ready(group: BuyingGroup | None) -> tuple[bool, str | None]:
    if group is None:
        return False, "Buying group not found"
    framework = (group.api_framework or "").strip().casefold()
    if framework == "parsefile":
        return parsefile_group_ready(group)
    if framework == "buyinggroup":
        return buyinggroup_group_ready(group)
    if framework == "bfmr":
        return bfmr_group_ready(group)
    if framework == "usabg":
        return usabg_group_ready(group)
    return False, "Receiving check is only available for Parsefile, BuyingGroup.com, BFMR, or USABG groups"


def match_to_dict(m: ReceivingMatch) -> dict[str, Any]:
    return {
        "item_id": m.item_id,
        "order_id": m.order_id,
        "store_order_number": m.store_order_number,
        "description": m.description,
        "quantity": m.quantity,
        "tracking_number": m.tracking_number,
        "price_sold": str(m.price_sold) if m.price_sold is not None else None,
        "expected_amount": str(m.expected_amount) if m.expected_amount is not None else None,
        "receipt_id": m.receipt_id,
        "scanned_at": m.scanned_at,
        "received_item": m.received_item,
        "received_qty": m.received_qty,
        "received_price": str(m.received_price) if m.received_price is not None else None,
        "received_ext_total": str(m.received_ext_total) if m.received_ext_total is not None else None,
        "received_status": m.received_status,
        "amount_discrepancy": m.amount_discrepancy,
        "selected": m.selected,
        "row_id": m.row_id or f"{m.item_id}:{m.match_kind}",
        "match_kind": m.match_kind,
        "original_quantity": m.original_quantity,
        "split_keep_quantity": m.split_keep_quantity,
        "update_receipt_only": m.update_receipt_only,
    }


def _allocate_received_across_shared_trackings(
    *,
    item_demands: list[tuple[int, int, list[str]]],
    supply_by_tn: dict[str, int],
) -> tuple[dict[int, int], dict[int, list[tuple[str, int]]]]:
    """Allocate warehouse qty to items, sharing supply when orders share a TN.

    For each tracking, prefer fully matching as many claimant orders/items as
    possible before any under-received split. An item may be filled from
    multiple trackings until its demand is met.

    Returns (total_by_item, per_item list of (tn_key, qty) in allocation order).
    """
    allocated = {item_id: 0 for item_id, _qty, _tns in item_demands}
    detail: dict[int, list[tuple[str, int]]] = {
        item_id: [] for item_id, _qty, _tns in item_demands
    }
    demand = {item_id: max(int(qty or 1), 1) for item_id, qty, _tns in item_demands}
    item_tns = {
        item_id: [tn.casefold() for tn in trackings if (tn or "").strip()]
        for item_id, _qty, trackings in item_demands
    }
    remaining = {
        tn.casefold(): int(qty)
        for tn, qty in supply_by_tn.items()
        if tn and int(qty or 0) > 0
    }

    def _add_detail(item_id: int, tn: str, give: int) -> None:
        for i, (existing_tn, existing_qty) in enumerate(detail[item_id]):
            if existing_tn == tn:
                detail[item_id][i] = (tn, existing_qty + give)
                return
        detail[item_id].append((tn, give))

    for tn in sorted(remaining.keys()):
        while remaining.get(tn, 0) > 0:
            claimants = [
                item_id
                for item_id, tns in item_tns.items()
                if tn in tns and allocated[item_id] < demand[item_id]
            ]
            if not claimants:
                break
            avail = remaining[tn]
            fully_fillable = [
                item_id
                for item_id in claimants
                if (demand[item_id] - allocated[item_id]) <= avail
            ]
            if fully_fillable:
                # Smallest remaining need first so more orders can be completed.
                fully_fillable.sort(
                    key=lambda item_id: (demand[item_id] - allocated[item_id], item_id)
                )
                item_id = fully_fillable[0]
            else:
                # Nobody can be completed — allocate partial to smallest need.
                claimants.sort(
                    key=lambda item_id: (demand[item_id] - allocated[item_id], item_id)
                )
                item_id = claimants[0]
            need = demand[item_id] - allocated[item_id]
            give = min(need, avail)
            allocated[item_id] += give
            _add_detail(item_id, tn, give)
            remaining[tn] = avail - give
    return allocated, detail


def _received_match_count(matches: list[ReceivingMatch]) -> int:
    return sum(1 for m in matches if m.match_kind == MATCH_KIND_RECEIVED)


def _split_off_count(matches: list[ReceivingMatch]) -> int:
    return sum(1 for m in matches if m.match_kind == MATCH_KIND_NOT_RECEIVED)


def _line_expected_amount(item: Item, line_qty: int) -> Decimal | None:
    price = _money(item.price_sold)
    if price is None:
        return None
    return price * max(int(line_qty), 0)


def _join_unique_strs(values: list[Any]) -> str | None:
    uniq = list(dict.fromkeys(str(v) for v in values if v is not None and str(v).strip()))
    return ", ".join(uniq) if uniq else None


def _meta_receipt_id(meta: dict[str, Any] | None) -> str | None:
    if not meta:
        return None
    return _join_unique_strs(list(meta.get("receipt_ids") or []))


def _receipt_ids_on_item(item: Item) -> set[str]:
    raw = (item.receipt_id or "").strip()
    if not raw:
        return set()
    return {p.strip() for p in raw.replace(";", ",").split(",") if p.strip()}


def _receipt_needs_update(item: Item, receipt_id: str | None) -> bool:
    """True when warehouse receipt ID(s) are not already stored on the item."""
    new_ids = {
        p.strip()
        for p in (receipt_id or "").replace(";", ",").split(",")
        if p.strip()
    }
    if not new_ids:
        return False
    return not new_ids.issubset(_receipt_ids_on_item(item))


def _tn_display(trackings: list[str], tn_key: str) -> str:
    for tn in trackings:
        if tn.casefold() == tn_key:
            return tn
    return tn_key


def _unit_payout_from_meta(meta: dict[str, Any], *, allocated_qty: int) -> Decimal | None:
    prices = list(meta.get("prices") or meta.get("unit_prices") or [])
    if prices:
        return _money(prices[0])
    ext_totals = list(meta.get("ext_totals") or [])
    if ext_totals and allocated_qty > 0:
        total_ext = sum((_money(t) or Decimal("0")) for t in ext_totals)
        return (total_ext / Decimal(allocated_qty)).quantize(Decimal("0.01"))
    return None


def _matches_for_tn_allocations(
    *,
    item: Item,
    order: Order,
    trackings: list[str],
    tn_allocations: list[tuple[str, int]],
    meta_by_tn: dict[str, dict[str, Any]],
    fallback_scanned_at: Any = None,
    update_receipt_only: bool = False,
    extra_lines: list[tuple[str, int, dict[str, Any]]] | None = None,
) -> list[ReceivingMatch]:
    """Build one review row per tracking allocation (+ optional not-received remainder)."""
    demand = item.quantity or 1
    store_order_number = (order.store_order_number or "").strip() or None
    description = (item.description or "").strip() or None
    price_sold = _money(item.price_sold)

    lines: list[tuple[str, int, dict[str, Any]]] = []
    for tn_key, qty in tn_allocations:
        if qty <= 0:
            continue
        meta = meta_by_tn.get(tn_key) or {}
        receipt_id = _meta_receipt_id(meta)
        if update_receipt_only and not _receipt_needs_update(item, receipt_id):
            continue
        lines.append((_tn_display(trackings, tn_key), qty, meta))
    for display_tn, qty, meta in extra_lines or []:
        if qty <= 0:
            continue
        receipt_id = _meta_receipt_id(meta)
        if update_receipt_only and not _receipt_needs_update(item, receipt_id):
            continue
        lines.append((display_tn, qty, meta))

    if not lines:
        return []

    got_total = sum(q for _tn, q, _meta in lines)
    out: list[ReceivingMatch] = []
    consumed = 0
    for index, (display_tn, qty, meta) in enumerate(lines):
        consumed += qty
        more_after = consumed < demand
        receipt_id = _meta_receipt_id(meta)
        statuses = list(meta.get("statuses") or [])
        unit_payout = _unit_payout_from_meta(meta, allocated_qty=qty)
        received_ext = None
        if unit_payout is not None:
            received_ext = (unit_payout * Decimal(qty)).quantize(Decimal("0.01"))
        expected = _line_expected_amount(item, qty)
        discrepancy = _amounts_differ(expected, received_ext)
        scanned_dt = meta.get("scanned_at") or fallback_scanned_at
        if scanned_dt is None:
            scanned_dt = to_date_only(datetime.now(timezone.utc))
        received_item = meta.get("received_item")
        tn_key = display_tn.casefold() if display_tn else f"line{index}"
        out.append(
            ReceivingMatch(
                item_id=item.id,
                order_id=order.id,
                store_order_number=store_order_number,
                description=description,
                quantity=qty,
                tracking_number=display_tn,
                price_sold=price_sold,
                expected_amount=expected,
                receipt_id=receipt_id,
                scanned_at=_iso_date(scanned_dt),
                received_item=received_item,
                received_qty=qty,
                received_price=unit_payout,
                received_ext_total=received_ext,
                received_status=_join_unique_strs(statuses),
                amount_discrepancy=discrepancy,
                selected=True,
                row_id=f"{item.id}:{MATCH_KIND_RECEIVED}:{tn_key}:{index}",
                match_kind=MATCH_KIND_RECEIVED,
                original_quantity=demand,
                split_keep_quantity=qty if more_after else None,
                update_receipt_only=update_receipt_only,
            )
        )

    if not update_receipt_only and got_total < demand:
        rem = demand - got_total
        rem_expected = _line_expected_amount(item, rem)
        tracking_label = ", ".join(trackings) if trackings else ""
        out.append(
            ReceivingMatch(
                item_id=item.id,
                order_id=order.id,
                store_order_number=store_order_number,
                description=description,
                quantity=rem,
                tracking_number=tracking_label,
                price_sold=price_sold,
                expected_amount=rem_expected,
                receipt_id=None,
                scanned_at=None,
                received_item=None,
                received_qty=0,
                received_price=None,
                received_ext_total=None,
                received_status="not_received",
                amount_discrepancy=False,
                selected=False,
                row_id=f"{item.id}:{MATCH_KIND_NOT_RECEIVED}",
                match_kind=MATCH_KIND_NOT_RECEIVED,
                original_quantity=demand,
                split_keep_quantity=None,
                update_receipt_only=False,
            )
        )
    return out


def _receipt_update_matches_for_item(
    *,
    item: Item,
    order: Order,
    trackings: list[str],
    meta_by_tn: dict[str, dict[str, Any]],
    supply_by_tn: dict[str, int],
    fallback_scanned_at: Any = None,
    extra_meta: dict[str, Any] | None = None,
    extra_qty: int = 0,
    extra_display_tn: str = "",
) -> list[ReceivingMatch]:
    """Rows for already-scanned+ items whose warehouse receipt ID differs."""
    demand = item.quantity or 1
    remaining = demand
    tn_allocations: list[tuple[str, int]] = []
    for tn in trackings:
        if remaining <= 0:
            break
        key = tn.casefold()
        meta = meta_by_tn.get(key)
        if not meta:
            continue
        receipt_id = _meta_receipt_id(meta)
        if not _receipt_needs_update(item, receipt_id):
            continue
        supply = int(supply_by_tn.get(key, 0) or 0)
        qty = min(remaining, supply if supply > 0 else 1)
        if qty <= 0:
            continue
        tn_allocations.append((key, qty))
        remaining -= qty

    extra_lines: list[tuple[str, int, dict[str, Any]]] = []
    if extra_meta and extra_qty > 0 and remaining > 0:
        receipt_id = _meta_receipt_id(extra_meta)
        if _receipt_needs_update(item, receipt_id):
            give = min(remaining, extra_qty)
            if give > 0:
                extra_lines.append((extra_display_tn, give, extra_meta))

    return _matches_for_tn_allocations(
        item=item,
        order=order,
        trackings=trackings,
        tn_allocations=tn_allocations,
        meta_by_tn=meta_by_tn,
        fallback_scanned_at=fallback_scanned_at,
        update_receipt_only=True,
        extra_lines=extra_lines,
    )


def _split_tracking_field(raw: str | None) -> list[str]:
    """Split a shipment tracking field that may contain comma/semicolon lists."""
    text = (raw or "").strip()
    if not text:
        return []
    parts: list[str] = []
    seen: set[str] = set()
    for chunk in text.replace(";", ",").split(","):
        tn = chunk.strip()
        if not tn:
            continue
        key = tn.casefold()
        if key in seen:
            continue
        seen.add(key)
        parts.append(tn)
    return parts


def _tracking_numbers_for_item(db: Session, item: Item) -> list[str]:
    """All distinct tracking numbers linked to an item (not just the first)."""
    out: list[str] = []
    seen: set[str] = set()

    def _add(raw: str | None) -> None:
        for tn in _split_tracking_field(raw):
            key = tn.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(tn)

    for si in item.shipment_items or []:
        if si.shipment:
            _add(si.shipment.tracking_number)
    if out:
        return out

    # Fallback when the relationship was empty/stale.
    rows = (
        db.query(Shipment.tracking_number)
        .join(ShipmentItem, ShipmentItem.shipment_id == Shipment.id)
        .filter(ShipmentItem.item_id == item.id)
        .filter(Shipment.tracking_number.isnot(None))
        .filter(Shipment.tracking_number != "")
        .all()
    )
    for (raw,) in rows:
        _add(raw)
    return out


def _tracking_numbers_for_order(db: Session, order_id: int) -> list[str]:
    """All distinct trackings linked to any item on the order (multi-package)."""
    out: list[str] = []
    seen: set[str] = set()
    rows = (
        db.query(Shipment.tracking_number)
        .join(ShipmentItem, ShipmentItem.shipment_id == Shipment.id)
        .join(Item, Item.id == ShipmentItem.item_id)
        .filter(Item.order_id == order_id)
        .filter(Shipment.tracking_number.isnot(None))
        .filter(Shipment.tracking_number != "")
        .all()
    )
    for (raw,) in rows:
        for tn in _split_tracking_field(raw):
            key = tn.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(tn)
    return out


def _collect_items_with_trackings(
    db: Session,
    *,
    buying_group_id: int,
    statuses: tuple[ItemStatus, ...],
) -> tuple[list[Item], list[tuple[Item, Order, list[str]]], list[str]]:
    """Return (items, pending triples, unique trackings) for the given statuses.

    Each pending item is matched against:
    - trackings linked to that item, plus
    - other trackings on the same order that are not owned by a different
      pending item (so multi-package orders are fully checked for every
      framework: Parsefile, BuyingGroup.com, BFMR, USABG).
    """
    items = (
        db.query(Item)
        .join(Order, Order.id == Item.order_id)
        .filter(Item.status.in_(statuses))
        .filter(Order.buying_group_id == buying_group_id)
        .filter(Order.status != "personal")
        .options(
            joinedload(Item.order),
            selectinload(Item.shipment_items).joinedload(ShipmentItem.shipment),
        )
        .order_by(Item.id)
        .all()
    )

    # First pass: item-owned trackings only.
    owned_by_item: dict[int, list[str]] = {}
    item_order: dict[int, Order] = {}
    for item in items:
        order = item.order
        if not order:
            continue
        trackings = _tracking_numbers_for_item(db, item)
        if not trackings:
            continue
        owned_by_item[item.id] = trackings
        item_order[item.id] = order

    # Trackings owned by other pending siblings on the same order.
    owned_by_order: dict[int, dict[int, set[str]]] = {}
    for item_id, trackings in owned_by_item.items():
        order = item_order[item_id]
        owned_by_order.setdefault(order.id, {})[item_id] = {tn.casefold() for tn in trackings}

    order_trackings_cache: dict[int, list[str]] = {}
    pending: list[tuple[Item, Order, list[str]]] = []
    for item in items:
        if item.id not in owned_by_item:
            continue
        order = item_order[item.id]
        owned = owned_by_item[item.id]
        if order.id not in order_trackings_cache:
            order_trackings_cache[order.id] = _tracking_numbers_for_order(db, order.id)
        sibling_owned: set[str] = set()
        for other_id, keys in owned_by_order.get(order.id, {}).items():
            if other_id == item.id:
                continue
            sibling_owned |= keys

        merged: list[str] = []
        seen: set[str] = set()
        for tn in [*owned, *order_trackings_cache[order.id]]:
            key = tn.casefold()
            if key in seen:
                continue
            # Leave sibling-owned packages for the sibling pending item.
            if key in sibling_owned and key not in {t.casefold() for t in owned}:
                continue
            seen.add(key)
            merged.append(tn)
        if not merged:
            continue
        pending.append((item, order, merged))

    all_trackings: list[str] = []
    seen_tn: set[str] = set()
    for order_id in sorted(order_trackings_cache):
        for tn in order_trackings_cache[order_id]:
            key = tn.casefold()
            if key in seen_tn:
                continue
            seen_tn.add(key)
            all_trackings.append(tn)
    for _item, _order, trackings in pending:
        for tn in trackings:
            key = tn.casefold()
            if key in seen_tn:
                continue
            seen_tn.add(key)
            all_trackings.append(tn)
    return items, pending, all_trackings


def _collect_pending(
    db: Session, *, buying_group_id: int
) -> tuple[list[Item], list[tuple[Item, Order, list[str]]], list[str]]:
    """Return (unscanned_items, pending triples, unique trackings)."""
    return _collect_items_with_trackings(
        db, buying_group_id=buying_group_id, statuses=_UNSCANNED_STATUSES
    )


def _collect_scanned_or_later(
    db: Session, *, buying_group_id: int
) -> list[tuple[Item, Order, list[str]]]:
    """Already-scanned+ items with trackings (for receipt-ID updates only)."""
    _items, pending, _trackings = _collect_items_with_trackings(
        db, buying_group_id=buying_group_id, statuses=_SCANNED_OR_LATER_STATUSES
    )
    return pending


def _merge_tracking_lists(*lists: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for trackings in lists:
        for tn in trackings:
            key = tn.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(tn)
    return out


def _known_shipment_trackings(db: Session) -> set[str]:
    rows = (
        db.query(Shipment.tracking_number)
        .filter(Shipment.tracking_number.isnot(None))
        .filter(Shipment.tracking_number != "")
        .all()
    )
    out: set[str] = set()
    for (raw,) in rows:
        for tn in _split_tracking_field(raw):
            out.add(tn.casefold())
    return out


def _index_bg_receipts_by_tracking(
    rows: list[buyinggroup_api.BuyinggroupReceipt],
) -> dict[str, list[buyinggroup_api.BuyinggroupReceipt]]:
    index: dict[str, list[buyinggroup_api.BuyinggroupReceipt]] = {}
    for row in rows:
        for tn in (row.tracking_id, row.alt_tracking_id):
            if not tn:
                continue
            index.setdefault(tn.casefold(), []).append(row)
    return index


def _index_analytics_by_tracking(
    rows: list[buyinggroup_api.BuyinggroupAnalyticsRow],
) -> dict[str, list[buyinggroup_api.BuyinggroupAnalyticsRow]]:
    index: dict[str, list[buyinggroup_api.BuyinggroupAnalyticsRow]] = {}
    for row in rows:
        tn = (row.tracking_id or "").strip()
        if not tn:
            continue
        index.setdefault(tn.casefold(), []).append(row)
    return index


def _build_unmatched(
    pending: list[tuple[Item, Order, list[str]]],
    matched_ids: set[int],
    *,
    void_matched_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    void_ids = void_matched_ids or set()
    unmatched: list[dict[str, Any]] = []
    for item, order, trackings in pending:
        if item.id in matched_ids:
            continue
        amt = _expected_amount(item)
        submitted_at = item.submitted_at
        unmatched.append(
            {
                "item_id": item.id,
                "order_id": order.id,
                "store_order_number": (order.store_order_number or "").strip() or None,
                "description": (item.description or "").strip() or None,
                "quantity": item.quantity or 1,
                "status": item.status.value if hasattr(item.status, "value") else str(item.status),
                "tracking_numbers": trackings,
                "expected_amount": str(amt) if amt is not None else None,
                "submitted_at": submitted_at.isoformat() if submitted_at is not None else None,
                "void_matched": item.id in void_ids,
            }
        )
    return unmatched


def check_receiving_for_group(db: Session, *, buying_group_id: int) -> CheckReceivingResult:
    """Search buying-group received items for unscanned trackings; build a review session."""
    group = db.query(BuyingGroup).filter(BuyingGroup.id == buying_group_id).first()
    if not group:
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=f"group {buying_group_id}",
            error="Buying group not found",
        )

    group_name = (group.name or "").strip() or f"group {buying_group_id}"
    framework = (group.api_framework or "").strip().casefold()
    if framework == "parsefile":
        return _check_receiving_parsefile(db, group=group, group_name=group_name)
    if framework == "buyinggroup":
        return _check_receiving_buyinggroup(db, group=group, group_name=group_name)
    if framework == "bfmr":
        return _check_receiving_bfmr(db, group=group, group_name=group_name)
    if framework == "usabg":
        return _check_receiving_usabg(db, group=group, group_name=group_name)
    return CheckReceivingResult(
        buying_group_id=buying_group_id,
        buying_group_name=group_name,
        error="Receiving check is only available for Parsefile, BuyingGroup.com, BFMR, or USABG groups",
    )


def _check_receiving_usabg(
    db: Session,
    *,
    group: BuyingGroup,
    group_name: str,
) -> CheckReceivingResult:
    buying_group_id = int(group.id)
    ready, reason = usabg_group_ready(group)
    if not ready:
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            error=reason or "Buying group USABG API is not configured",
        )

    unscanned_items, pending, all_trackings = _collect_pending(db, buying_group_id=buying_group_id)
    scanned_pending = _collect_scanned_or_later(db, buying_group_id=buying_group_id)
    all_trackings = _merge_tracking_lists(
        all_trackings, [tn for _i, _o, tns in scanned_pending for tn in tns]
    )
    username = str(group.api_username or "").strip()
    password = str(group.api_password or "")
    base_url = (group.base_url or "").strip() or None
    cached_details = _load_usabg_po_detail_cache(db, buying_group_id)

    try:
        fetched = usabg_api.list_received_po_rows(
            base_url=base_url,
            username=username,
            password=password,
            cached_details=cached_details,
        )
    except Exception as exc:
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            submitted_count=len(unscanned_items),
            error=str(exc).strip() or exc.__class__.__name__,
        )

    if fetched.newly_fetched_details:
        status_by_po: dict[str, str | None] = {}
        for row in fetched.rows:
            if row.po_number in fetched.newly_fetched_details and row.po_number not in status_by_po:
                status_by_po[row.po_number] = row.receiving_status
        _persist_usabg_po_detail_cache(
            db,
            buying_group_id,
            fetched.newly_fetched_details,
            receiving_status_by_po=status_by_po,
        )

    receipts = fetched.rows
    supply_by_tn: dict[str, int] = {}
    meta_by_tn: dict[str, dict[str, Any]] = {}
    for row in receipts:
        tn = (row.tracking_number or "").strip()
        if not tn:
            continue
        qty = int(row.quantity) if row.quantity and row.quantity > 0 else 1
        key = tn.casefold()
        supply_by_tn[key] = supply_by_tn.get(key, 0) + qty
        meta = meta_by_tn.setdefault(
            key,
            {
                "receipt_ids": [],
                "statuses": [],
                "unit_prices": [],
                "scanned_at": None,
                "received_item": None,
            },
        )
        # PO number is the warehouse receipt id.
        if row.receipt_id:
            meta["receipt_ids"].append(row.receipt_id)
        status_bits = [row.receiving_status, row.received_status]
        for bit in status_bits:
            if bit:
                meta["statuses"].append(str(bit))
        if row.unit_price is not None:
            meta["unit_prices"].append(row.unit_price)
        row_scanned = _parse_created_date(row.scanned_at)
        if row_scanned is not None and (
            meta["scanned_at"] is None or row_scanned > meta["scanned_at"]
        ):
            meta["scanned_at"] = row_scanned
        if meta["received_item"] is None and row.received_item:
            meta["received_item"] = row.received_item

    eligible: list[tuple[Item, Order, list[str]]] = []
    not_found = 0
    void_skipped = 0
    for item, order, trackings in pending:
        has_hit = any(tn.casefold() in supply_by_tn for tn in trackings)
        if has_hit:
            eligible.append((item, order, trackings))
        else:
            not_found += 1

    allocated, allocated_by_tn = _allocate_received_across_shared_trackings(
        item_demands=[(item.id, item.quantity or 1, trackings) for item, _o, trackings in eligible],
        supply_by_tn=supply_by_tn,
    )

    matches: list[ReceivingMatch] = []
    for item, order, trackings in eligible:
        got = int(allocated.get(item.id, 0))
        if got <= 0:
            not_found += 1
            continue
        matches.extend(
            _matches_for_tn_allocations(
                item=item,
                order=order,
                trackings=trackings,
                tn_allocations=allocated_by_tn.get(item.id, []),
                meta_by_tn=meta_by_tn,
            )
        )

    for item, order, trackings in scanned_pending:
        matches.extend(
            _receipt_update_matches_for_item(
                item=item,
                order=order,
                trackings=trackings,
                meta_by_tn=meta_by_tn,
                supply_by_tn=supply_by_tn,
            )
        )

    known_trackings = _known_shipment_trackings(db)
    unknown_receipts: list[dict[str, Any]] = []
    orphan_seen: set[int] = set()
    for row in receipts:
        tn = (row.tracking_number or "").strip()
        if not tn:
            continue
        if tn.casefold() in known_trackings:
            continue
        if id(row) in orphan_seen:
            continue
        orphan_seen.add(id(row))
        scanned_dt = _parse_created_date(row.scanned_at)
        status = _join_unique_strs(
            [s for s in (row.receiving_status, row.received_status) if s]
        )
        unknown_receipts.append(
            {
                "receipt_id": row.receipt_id,
                "tracking_number": tn,
                "bg_order_id": row.purchase_id,
                "description": row.received_item,
                "quantity": row.quantity,
                "received_price": str(row.unit_price) if row.unit_price is not None else None,
                "received_ext_total": str(row.ext_total) if row.ext_total is not None else None,
                "received_status": status,
                "note": None,
                "created_dt": row.scanned_at,
                "scanned_at": _iso_date(scanned_dt),
                "delivery": None,
            }
        )

    discrepancy_count = sum(1 for m in matches if m.amount_discrepancy)
    received_count = _received_match_count(matches)
    split_off = _split_off_count(matches)
    cache_bits = (
        f"{fetched.detail_cache_hits} PO detail(s) from cache, "
        f"{fetched.detail_cache_misses} fetched"
    )
    checked_summary = (
        f"Checked {len(pending)} unscanned item(s) / {len(all_trackings)} tracking number(s) "
        f"against USABG ({len(receipts)} received line(s) across {fetched.received_po_count}/"
        f"{fetched.po_count} PO(s); {cache_bits})."
    )

    matched_ids = {m.item_id for m in matches}
    unmatched = _build_unmatched(pending, matched_ids)

    parts: list[str] = []
    if received_count:
        part = f"Found {received_count} received item(s) ready to mark scanned"
        if split_off:
            part += f" ({split_off} under-received split-off)"
        if discrepancy_count:
            part += f" ({discrepancy_count} amount discrepancy)"
        parts.append(part)
    if unknown_receipts:
        parts.append(f"{len(unknown_receipts)} USABG box line(s) with tracking not in Order Manager")
    if not matches and not unknown_receipts:
        bits = [checked_summary]
        if not_found:
            bits.append(f"{not_found} local item(s) not found as received.")
        message = " ".join(bits) + " Nothing to review."
    else:
        message = ". ".join(parts) + f". {checked_summary}"

    if not pending and not unknown_receipts and not matches:
        message = (
            "No shipped/submitted (unscanned) items with tracking numbers for this buying group, "
            "and no unknown USABG received boxes."
            if not unscanned_items
            else f"{len(unscanned_items)} unscanned item(s) but none have tracking numbers, "
            "and no unknown USABG received boxes."
        )
        payload = {
            "buying_group_id": buying_group_id,
            "buying_group_name": group_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "not_found_count": 0,
            "void_skipped_count": 0,
            "searched_trackings": all_trackings,
            "raw_csv": "",
            "csv_row_count": len(receipts),
            "unmatched": [],
            "unknown_receipts": [],
            "matches": [],
        }
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            session_payload=payload,
            submitted_count=len(unscanned_items),
            message=message,
        )

    raw_dump = {
        "po_count": fetched.po_count,
        "received_po_count": fetched.received_po_count,
        "detail_cache_hits": fetched.detail_cache_hits,
        "detail_cache_misses": fetched.detail_cache_misses,
        "request_urls": fetched.request_urls[:50],
        "received_rows": [
            {
                "po_number": r.po_number,
                "purchase_id": r.purchase_id,
                "tracking_number": r.tracking_number,
                "quantity": r.quantity,
                "unit_price": str(r.unit_price) if r.unit_price is not None else None,
                "ext_total": str(r.ext_total) if r.ext_total is not None else None,
                "sku": r.sku,
                "received_item": r.received_item,
                "receiving_status": r.receiving_status,
                "status": r.received_status,
                "scanned_at": r.scanned_at,
            }
            for r in receipts[:200]
        ],
        "unknown_count": len(unknown_receipts),
    }

    payload = {
        "buying_group_id": buying_group_id,
        "buying_group_name": group_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "not_found_count": not_found,
        "void_skipped_count": void_skipped,
        "searched_trackings": all_trackings,
        "raw_csv": json.dumps(raw_dump, default=str, indent=2),
        "csv_row_count": len(receipts),
        "unmatched": unmatched,
        "unknown_receipts": unknown_receipts,
        "matches": [match_to_dict(m) for m in matches],
    }
    return CheckReceivingResult(
        buying_group_id=buying_group_id,
        buying_group_name=group_name,
        session_payload=payload,
        matched_count=received_count,
        not_found_count=not_found,
        void_skipped_count=void_skipped,
        discrepancy_count=discrepancy_count,
        submitted_count=len(unscanned_items),
        matches=matches,
        message=message,
    )


def _check_receiving_parsefile(
    db: Session,
    *,
    group: BuyingGroup,
    group_name: str,
) -> CheckReceivingResult:
    buying_group_id = int(group.id)
    ready, reason = parsefile_group_ready(group)
    if not ready:
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            error=reason or "Buying group Parsefile API is not configured",
        )

    unscanned_items, pending, all_trackings = _collect_pending(db, buying_group_id=buying_group_id)
    scanned_pending = _collect_scanned_or_later(db, buying_group_id=buying_group_id)
    all_trackings = _merge_tracking_lists(
        all_trackings, [tn for _i, _o, tns in scanned_pending for tn in tns]
    )

    base_url = str(group.base_url)
    bearer_token = str(group.bearer_token)
    api_user_id = int(group.api_user_id)
    api_email = str(group.api_email)

    try:
        api_result = parsefile_api.list_all_received_items(
            base_url=base_url,
            bearer_token=bearer_token,
            user_id=api_user_id,
            email=api_email,
        )
    except Exception as exc:
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            submitted_count=len(unscanned_items),
            error=str(exc).strip() or exc.__class__.__name__,
        )

    received_rows = api_result.rows
    raw_csv = api_result.raw_csv

    by_tracking = _index_received_by_tracking(received_rows)
    supply_by_tn: dict[str, int] = {}
    meta_by_tn: dict[str, dict[str, Any]] = {}
    for row in received_rows:
        if row.void:
            continue
        qty = int(row.qty) if row.qty is not None else 1
        if qty <= 0:
            continue
        primary_tn = (row.tracking_numbers[0] if row.tracking_numbers else "").strip()
        if not primary_tn:
            continue
        key = primary_tn.casefold()
        supply_by_tn[key] = supply_by_tn.get(key, 0) + qty
        meta = meta_by_tn.setdefault(
            key,
            {
                "receipt_ids": [],
                "statuses": [],
                "prices": [],
                "ext_totals": [],
                "scanned_at": None,
                "received_item": None,
            },
        )
        if row.receipt_id:
            meta["receipt_ids"].append(row.receipt_id)
        if row.status:
            meta["statuses"].append(str(row.status))
        # BG paid amount = CSV Total Price (mapped to ext_total).
        total = row.ext_total
        if total is None and row.price is not None:
            total = row.price * Decimal(qty)
        if total is not None:
            meta["ext_totals"].append(total)
            meta["prices"].append((total / Decimal(qty)).quantize(Decimal("0.01")))
        row_scanned = _parse_created_date(row.created_date)
        if row_scanned is not None and (
            meta["scanned_at"] is None or row_scanned > meta["scanned_at"]
        ):
            meta["scanned_at"] = row_scanned
        if meta["received_item"] is None and row.item:
            meta["received_item"] = row.item

    eligible: list[tuple[Item, Order, list[str]]] = []
    void_matched_ids: set[int] = set()
    void_skipped = 0
    not_found = 0
    for item, order, trackings in pending:
        has_hit = False
        all_void = True
        any_cand = False
        for tn in trackings:
            rows = by_tracking.get(tn.casefold()) or []
            if not rows:
                continue
            any_cand = True
            if any(not r.void for r in rows):
                all_void = False
            if tn.casefold() in supply_by_tn:
                has_hit = True
        if has_hit:
            eligible.append((item, order, trackings))
        elif any_cand and all_void:
            void_skipped += 1
            void_matched_ids.add(item.id)
        else:
            not_found += 1

    allocated, allocated_by_tn = _allocate_received_across_shared_trackings(
        item_demands=[(item.id, item.quantity or 1, trackings) for item, _o, trackings in eligible],
        supply_by_tn=supply_by_tn,
    )

    matches: list[ReceivingMatch] = []
    for item, order, trackings in eligible:
        got = int(allocated.get(item.id, 0))
        if got <= 0:
            not_found += 1
            continue
        matches.extend(
            _matches_for_tn_allocations(
                item=item,
                order=order,
                trackings=trackings,
                tn_allocations=allocated_by_tn.get(item.id, []),
                meta_by_tn=meta_by_tn,
            )
        )

    for item, order, trackings in scanned_pending:
        matches.extend(
            _receipt_update_matches_for_item(
                item=item,
                order=order,
                trackings=trackings,
                meta_by_tn=meta_by_tn,
                supply_by_tn=supply_by_tn,
            )
        )

    known_trackings = _known_shipment_trackings(db)
    unknown_receipts: list[dict[str, Any]] = []
    orphan_seen: set[int] = set()
    for row in received_rows:
        if row.void:
            continue
        if not row.tracking_numbers:
            continue
        if id(row) in orphan_seen:
            continue
        # Skip if any tracking on the row already exists in OM.
        if any(tn.casefold() in known_trackings for tn in row.tracking_numbers):
            continue
        orphan_seen.add(id(row))
        tn = row.tracking_numbers[0]
        received_total = row.ext_total
        row_qty = row.qty if row.qty is not None else 1
        if received_total is None and row.price is not None:
            received_total = row.price * row_qty
        received_unit = None
        if received_total is not None and row_qty > 0:
            received_unit = (received_total / Decimal(row_qty)).quantize(Decimal("0.01"))
        elif row.price is not None:
            received_unit = row.price
        scanned_dt = _parse_created_date(row.created_date)
        unknown_receipts.append(
            {
                "receipt_id": row.receipt_id,
                "tracking_number": tn,
                "bg_order_id": None,
                "description": (row.item or "").strip() or None,
                "quantity": row.qty,
                "received_price": str(received_unit) if received_unit is not None else None,
                "received_ext_total": str(received_total) if received_total is not None else None,
                "received_status": row.status,
                "note": None,
                "created_dt": row.created_date,
                "scanned_at": _iso_date(scanned_dt),
                "delivery": row.delivery,
            }
        )

    discrepancy_count = sum(1 for m in matches if m.amount_discrepancy)
    received_count = _received_match_count(matches)
    split_off = _split_off_count(matches)
    checked_summary = (
        f"Checked {len(pending)} unscanned item(s) / {len(all_trackings)} tracking number(s) "
        f"against Parsefile ({len(received_rows)} received row(s))."
    )

    matched_ids = {m.item_id for m in matches}
    unmatched = _build_unmatched(pending, matched_ids, void_matched_ids=void_matched_ids)

    if not pending and not unknown_receipts and not matches:
        message = (
            "No shipped/submitted (unscanned) items with tracking numbers for this buying group, "
            "and no unknown Parsefile receipts."
            if not unscanned_items
            else f"{len(unscanned_items)} unscanned item(s) but none have tracking numbers, "
            "and no unknown Parsefile receipts."
        )
        payload = {
            "buying_group_id": buying_group_id,
            "buying_group_name": group_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "not_found_count": 0,
            "void_skipped_count": 0,
            "searched_trackings": all_trackings,
            "raw_csv": raw_csv,
            "csv_row_count": len(received_rows),
            "unmatched": [],
            "unknown_receipts": [],
            "matches": [],
        }
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            session_payload=payload,
            submitted_count=len(unscanned_items),
            message=message,
        )

    parts: list[str] = []
    if received_count:
        part = f"Found {received_count} received item(s) ready to mark scanned"
        if split_off:
            part += f" ({split_off} under-received split-off)"
        if discrepancy_count:
            part += f" ({discrepancy_count} amount discrepancy)"
        parts.append(part)
    if unknown_receipts:
        parts.append(
            f"{len(unknown_receipts)} Parsefile receipt(s) with tracking not in Order Manager"
        )
    if not matches and not unknown_receipts:
        bits = [checked_summary]
        if not_found:
            bits.append(f"{not_found} local item(s) not found as received.")
        if void_skipped:
            bits.append(f"{void_skipped} voided at Parsefile.")
        message = " ".join(bits) + " Nothing to review."
    else:
        message = ". ".join(parts) + f". {checked_summary}"

    payload = {
        "buying_group_id": buying_group_id,
        "buying_group_name": group_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "not_found_count": not_found,
        "void_skipped_count": void_skipped,
        "searched_trackings": all_trackings,
        "raw_csv": raw_csv,
        "csv_row_count": len(received_rows),
        "unmatched": unmatched,
        "unknown_receipts": unknown_receipts,
        "matches": [match_to_dict(m) for m in matches],
    }
    return CheckReceivingResult(
        buying_group_id=buying_group_id,
        buying_group_name=group_name,
        session_payload=payload,
        matched_count=received_count,
        not_found_count=not_found,
        void_skipped_count=void_skipped,
        discrepancy_count=discrepancy_count,
        submitted_count=len(unscanned_items),
        matches=matches,
        message=message,
    )


def _check_receiving_buyinggroup(
    db: Session,
    *,
    group: BuyingGroup,
    group_name: str,
) -> CheckReceivingResult:
    buying_group_id = int(group.id)
    ready, reason = buyinggroup_group_ready(group)
    if not ready:
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            error=reason or "Buying group BuyingGroup.com API is not configured",
        )

    unscanned_items, pending, all_trackings = _collect_pending(db, buying_group_id=buying_group_id)
    scanned_pending = _collect_scanned_or_later(db, buying_group_id=buying_group_id)
    all_trackings = _merge_tracking_lists(
        all_trackings, [tn for _i, _o, tns in scanned_pending for tn in tns]
    )
    username = str(group.api_username or "").strip()
    password = str(group.api_password or "")
    base_url = (group.base_url or "").strip() or None

    try:
        receipts = buyinggroup_api.list_receipts(
            base_url=base_url,
            username=username,
            password=password,
        )
    except Exception as exc:
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            submitted_count=len(unscanned_items),
            error=str(exc).strip() or exc.__class__.__name__,
        )

    by_tracking = _index_bg_receipts_by_tracking(receipts)
    supply_by_tn: dict[str, int] = {}
    meta_by_tn: dict[str, dict[str, Any]] = {}
    for row in receipts:
        if row.void:
            continue
        tn = (row.tracking_id or row.alt_tracking_id or "").strip()
        if not tn:
            continue
        qty = int(row.quantity) if row.quantity is not None else 1
        if qty <= 0:
            continue
        key = tn.casefold()
        supply_by_tn[key] = supply_by_tn.get(key, 0) + qty
        meta = meta_by_tn.setdefault(
            key,
            {
                "receipt_ids": [],
                "statuses": [],
                "unit_prices": [],
                "scanned_at": None,
                "received_item": None,
            },
        )
        if row.receipt_id:
            meta["receipt_ids"].append(row.receipt_id)
        if row.status:
            meta["statuses"].append(str(row.status))
        if row.total is not None and qty > 0:
            meta["unit_prices"].append((row.total / Decimal(qty)).quantize(Decimal("0.01")))
        row_scanned = _parse_created_date(row.created_dt)
        if row_scanned is not None and (
            meta["scanned_at"] is None or row_scanned > meta["scanned_at"]
        ):
            meta["scanned_at"] = row_scanned
        if meta["received_item"] is None and row.order_id:
            meta["received_item"] = row.order_id

    eligible: list[tuple[Item, Order, list[str]]] = []
    void_matched_ids: set[int] = set()
    void_skipped = 0
    not_found = 0
    for item, order, trackings in pending:
        has_hit = False
        all_void = True
        any_cand = False
        for tn in trackings:
            rows = by_tracking.get(tn.casefold()) or []
            if not rows:
                continue
            any_cand = True
            if any(not r.void for r in rows):
                all_void = False
            if tn.casefold() in supply_by_tn:
                has_hit = True
        if has_hit:
            eligible.append((item, order, trackings))
        elif any_cand and all_void:
            void_skipped += 1
            void_matched_ids.add(item.id)
        else:
            not_found += 1

    allocated, allocated_by_tn = _allocate_received_across_shared_trackings(
        item_demands=[(item.id, item.quantity or 1, trackings) for item, _o, trackings in eligible],
        supply_by_tn=supply_by_tn,
    )

    matches: list[ReceivingMatch] = []
    for item, order, trackings in eligible:
        got = int(allocated.get(item.id, 0))
        if got <= 0:
            not_found += 1
            continue
        matches.extend(
            _matches_for_tn_allocations(
                item=item,
                order=order,
                trackings=trackings,
                tn_allocations=allocated_by_tn.get(item.id, []),
                meta_by_tn=meta_by_tn,
            )
        )

    for item, order, trackings in scanned_pending:
        matches.extend(
            _receipt_update_matches_for_item(
                item=item,
                order=order,
                trackings=trackings,
                meta_by_tn=meta_by_tn,
                supply_by_tn=supply_by_tn,
            )
        )

    known_trackings = _known_shipment_trackings(db)
    orphan_receipts: list[buyinggroup_api.BuyinggroupReceipt] = []
    orphan_seen: set[int] = set()
    for row in receipts:
        if row.void:
            continue
        tn = (row.tracking_id or row.alt_tracking_id or "").strip()
        if not tn:
            continue
        if tn.casefold() in known_trackings:
            continue
        if id(row) in orphan_seen:
            continue
        orphan_seen.add(id(row))
        orphan_receipts.append(row)

    analytics_by_tracking: dict[str, list[buyinggroup_api.BuyinggroupAnalyticsRow]] = {}
    analytics_rows: list[buyinggroup_api.BuyinggroupAnalyticsRow] = []
    if orphan_receipts:
        try:
            analytics_rows = buyinggroup_api.list_receipt_analytics(
                base_url=base_url,
                username=username,
                password=password,
            )
            analytics_by_tracking = _index_analytics_by_tracking(analytics_rows)
        except Exception as exc:
            # Still show orphans from receipt data; note analytics failure in message later.
            analytics_error = str(exc).strip() or exc.__class__.__name__
        else:
            analytics_error = None
    else:
        analytics_error = None

    unknown_receipts: list[dict[str, Any]] = []
    for row in orphan_receipts:
        tn = (row.tracking_id or row.alt_tracking_id or "").strip()
        analytics_list = analytics_by_tracking.get(tn.casefold()) or []
        analytics = analytics_list[0] if analytics_list else None
        description = (analytics.description if analytics else None) or row.order_id
        note = None
        if analytics:
            note = analytics.note or analytics.tracking_note
        if not note:
            note = row.note or row.tracking_note
        qty = row.quantity
        if qty is None and analytics and analytics.quantity is not None:
            qty = analytics.quantity
        total = row.total
        if total is None and analytics and analytics.total is not None:
            total = analytics.total
        price = None
        if analytics and analytics.price is not None:
            price = analytics.price
        elif total is not None and qty and qty > 0:
            price = (total / Decimal(qty)).quantize(Decimal("0.01"))
        scanned_dt = _parse_created_date(row.created_dt)
        unknown_receipts.append(
            {
                "receipt_id": row.receipt_id,
                "tracking_number": tn,
                "bg_order_id": row.order_id or (analytics.order_id if analytics else None),
                "description": description,
                "quantity": qty,
                "received_price": str(price) if price is not None else None,
                "received_ext_total": str(total) if total is not None else None,
                "received_status": row.status,
                "note": note,
                "created_dt": row.created_dt,
                "scanned_at": _iso_date(scanned_dt),
                "delivery": row.delivery,
            }
        )

    discrepancy_count = sum(1 for m in matches if m.amount_discrepancy)
    received_count = _received_match_count(matches)
    split_off = _split_off_count(matches)
    checked_summary = (
        f"Checked {len(pending)} unscanned item(s) / {len(all_trackings)} tracking number(s) "
        f"against BuyingGroup.com ({len(receipts)} receipt row(s))."
    )

    matched_ids = {m.item_id for m in matches}
    unmatched = _build_unmatched(pending, matched_ids, void_matched_ids=void_matched_ids)

    parts: list[str] = []
    if received_count:
        part = f"Found {received_count} received item(s) ready to mark scanned"
        if split_off:
            part += f" ({split_off} under-received split-off)"
        if discrepancy_count:
            part += f" ({discrepancy_count} amount discrepancy)"
        parts.append(part)
    if unknown_receipts:
        parts.append(f"{len(unknown_receipts)} BG receipt(s) with tracking not in Order Manager")
    if analytics_error:
        parts.append(f"Analytics enrich failed: {analytics_error}")
    if not matches and not unknown_receipts:
        bits = [checked_summary]
        if not_found:
            bits.append(f"{not_found} local item(s) not found as received.")
        if void_skipped:
            bits.append(f"{void_skipped} voided at BuyingGroup.")
        message = " ".join(bits) + " Nothing to review."
    else:
        message = ". ".join(parts) + f". {checked_summary}"

    # Even with no pending locals, still open review when there are unknown receipts.
    if not pending and not unknown_receipts and not matches:
        message = (
            "No shipped/submitted (unscanned) items with tracking numbers for this buying group, "
            "and no unknown BG receipts."
            if not unscanned_items
            else f"{len(unscanned_items)} unscanned item(s) but none have tracking numbers, "
            "and no unknown BG receipts."
        )
        payload = {
            "buying_group_id": buying_group_id,
            "buying_group_name": group_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "not_found_count": 0,
            "void_skipped_count": 0,
            "searched_trackings": all_trackings,
            "raw_csv": "",
            "csv_row_count": len(receipts),
            "unmatched": [],
            "unknown_receipts": [],
            "matches": [],
        }
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            session_payload=payload,
            submitted_count=len(unscanned_items),
            message=message,
        )

    raw_dump = {
        "receipts": [r.raw for r in receipts[:200]],
        "analytics_sample": [a.raw for a in analytics_rows[:50]],
        "unknown_count": len(unknown_receipts),
    }

    payload = {
        "buying_group_id": buying_group_id,
        "buying_group_name": group_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "not_found_count": not_found,
        "void_skipped_count": void_skipped,
        "searched_trackings": all_trackings,
        "raw_csv": json.dumps(raw_dump, default=str, indent=2),
        "csv_row_count": len(receipts),
        "unmatched": unmatched,
        "unknown_receipts": unknown_receipts,
        "matches": [match_to_dict(m) for m in matches],
    }
    return CheckReceivingResult(
        buying_group_id=buying_group_id,
        buying_group_name=group_name,
        session_payload=payload,
        matched_count=received_count,
        not_found_count=not_found,
        void_skipped_count=void_skipped,
        discrepancy_count=discrepancy_count,
        submitted_count=len(unscanned_items),
        matches=matches,
        message=message,
    )


def _check_receiving_bfmr(
    db: Session,
    *,
    group: BuyingGroup,
    group_name: str,
) -> CheckReceivingResult:
    buying_group_id = int(group.id)
    ready, reason = bfmr_group_ready(group)
    if not ready:
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            error=reason or "Buying group BFMR API is not configured",
        )

    unscanned_items, pending, all_trackings = _collect_pending(db, buying_group_id=buying_group_id)
    scanned_pending = _collect_scanned_or_later(db, buying_group_id=buying_group_id)
    all_trackings = _merge_tracking_lists(
        all_trackings, [tn for _i, _o, tns in scanned_pending for tn in tns]
    )
    api_key = str(group.api_username or "").strip()
    api_secret = str(group.api_password or "")
    base_url = (group.base_url or "").strip() or None

    try:
        fetched = bfmr_api.list_received_tracker_rows(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
        )
    except Exception as exc:
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            submitted_count=len(unscanned_items),
            error=str(exc).strip() or exc.__class__.__name__,
        )

    receipts = fetched.rows
    by_tracking = _index_bfmr_by_tracking(receipts)
    all_pending_trackings = {
        tn.casefold()
        for _i, _o, tns in (*pending, *scanned_pending)
        for tn in tns
    }

    supply_by_tn: dict[str, int] = {}
    supply_by_order: dict[str, int] = {}
    meta_by_tn: dict[str, dict[str, Any]] = {}
    meta_by_order: dict[str, dict[str, Any]] = {}

    def _row_qty(row: bfmr_api.BfmrTrackerRow) -> int:
        qty = row.qty_received if row.qty_received is not None else row.qty
        return int(qty) if qty is not None and int(qty) > 0 else 1

    def _absorb_meta(meta: dict[str, Any], row: bfmr_api.BfmrTrackerRow) -> None:
        rid = _bfmr_receipt_id(row)
        if rid:
            meta["receipt_ids"].append(rid)
        if row.status:
            meta["statuses"].append(str(row.status))
        if row.payout_price is not None:
            meta["unit_prices"].append(row.payout_price)
        row_scanned = _parse_created_date(row.scanned_at)
        if row_scanned is not None and (
            meta["scanned_at"] is None or row_scanned > meta["scanned_at"]
        ):
            meta["scanned_at"] = row_scanned
        if meta["received_item"] is None:
            meta["received_item"] = (row.item_name or row.deal_title or "").strip() or None

    empty_meta = lambda: {
        "receipt_ids": [],
        "statuses": [],
        "unit_prices": [],
        "scanned_at": None,
        "received_item": None,
    }

    for row in receipts:
        tn = (row.tracking_number or "").strip()
        qty = _row_qty(row)
        oid = (row.order_id or "").strip()
        if tn and tn.casefold() in all_pending_trackings:
            key = tn.casefold()
            supply_by_tn[key] = supply_by_tn.get(key, 0) + qty
            meta = meta_by_tn.setdefault(key, empty_meta())
            _absorb_meta(meta, row)
        elif oid:
            # Empty TN, or TN not linked to any pending item — fill by store order
            # after shared tracking allocation across orders.
            okey = oid.casefold()
            supply_by_order[okey] = supply_by_order.get(okey, 0) + qty
            meta = meta_by_order.setdefault(okey, empty_meta())
            _absorb_meta(meta, row)
        elif tn:
            key = tn.casefold()
            supply_by_tn[key] = supply_by_tn.get(key, 0) + qty
            meta = meta_by_tn.setdefault(key, empty_meta())
            _absorb_meta(meta, row)

    eligible: list[tuple[Item, Order, list[str]]] = []
    not_found = 0
    void_skipped = 0
    for item, order, trackings in pending:
        store_order = (order.store_order_number or "").strip().casefold()
        has_hit = any(tn.casefold() in supply_by_tn for tn in trackings)
        if not has_hit and store_order and store_order in supply_by_order:
            has_hit = True
        if has_hit:
            eligible.append((item, order, trackings))
        else:
            not_found += 1

    allocated, allocated_by_tn = _allocate_received_across_shared_trackings(
        item_demands=[(item.id, item.quantity or 1, trackings) for item, _o, trackings in eligible],
        supply_by_tn=supply_by_tn,
    )

    # Second pass: leftover order-level supply fills remaining demand for items
    # on that store order (after all shared TNs were considered).
    demand_left = {
        item.id: max((item.quantity or 1) - int(allocated.get(item.id, 0)), 0)
        for item, _o, _t in eligible
    }
    order_fill_by_item: dict[int, int] = {}
    order_items: dict[str, list[int]] = {}
    for item, order, _tns in eligible:
        okey = (order.store_order_number or "").strip().casefold()
        if not okey:
            continue
        order_items.setdefault(okey, []).append(item.id)
    for okey in sorted(order_items.keys()):
        avail = int(supply_by_order.get(okey, 0))
        if avail <= 0:
            continue
        for item_id in sorted(order_items[okey]):
            need = demand_left.get(item_id, 0)
            if need <= 0 or avail <= 0:
                continue
            give = min(need, avail)
            allocated[item_id] = int(allocated.get(item_id, 0)) + give
            order_fill_by_item[item_id] = int(order_fill_by_item.get(item_id, 0)) + give
            demand_left[item_id] = need - give
            avail -= give

    matches: list[ReceivingMatch] = []
    for item, order, trackings in eligible:
        got = int(allocated.get(item.id, 0))
        if got <= 0:
            not_found += 1
            continue
        okey = (order.store_order_number or "").strip().casefold()
        extra_lines: list[tuple[str, int, dict[str, Any]]] = []
        order_qty = int(order_fill_by_item.get(item.id, 0))
        if order_qty > 0 and okey and okey in meta_by_order:
            display = (order.store_order_number or "").strip() or "order"
            extra_lines.append((display, order_qty, meta_by_order[okey]))
        matches.extend(
            _matches_for_tn_allocations(
                item=item,
                order=order,
                trackings=trackings,
                tn_allocations=allocated_by_tn.get(item.id, []),
                meta_by_tn=meta_by_tn,
                extra_lines=extra_lines,
            )
        )

    for item, order, trackings in scanned_pending:
        okey = (order.store_order_number or "").strip().casefold()
        extra_meta = meta_by_order.get(okey) if okey else None
        extra_qty = int(supply_by_order.get(okey, 0)) if okey else 0
        extra_display = (order.store_order_number or "").strip() if okey else ""
        matches.extend(
            _receipt_update_matches_for_item(
                item=item,
                order=order,
                trackings=trackings,
                meta_by_tn=meta_by_tn,
                supply_by_tn=supply_by_tn,
                extra_meta=extra_meta,
                extra_qty=extra_qty,
                extra_display_tn=extra_display,
            )
        )

    known_trackings = _known_shipment_trackings(db)
    unknown_receipts: list[dict[str, Any]] = []
    orphan_seen: set[int] = set()
    for row in receipts:
        tn = (row.tracking_number or "").strip()
        if not tn:
            continue
        if tn.casefold() in known_trackings:
            continue
        if id(row) in orphan_seen:
            continue
        orphan_seen.add(id(row))
        received_qty = row.qty_received if row.qty_received is not None else row.qty
        received_total = _bfmr_received_total(row)
        received_price: Decimal | None = None
        if row.payout_price is not None:
            received_price = row.payout_price
        elif received_total is not None and received_qty and received_qty > 0:
            received_price = (received_total / Decimal(received_qty)).quantize(Decimal("0.01"))
        scanned_dt = _parse_created_date(row.scanned_at)
        unknown_receipts.append(
            {
                "receipt_id": _bfmr_receipt_id(row),
                "tracking_number": tn,
                "bg_order_id": row.order_id,
                "description": (row.item_name or row.deal_title or "").strip() or None,
                "quantity": received_qty,
                "received_price": str(received_price) if received_price is not None else None,
                "received_ext_total": str(received_total) if received_total is not None else None,
                "received_status": row.status,
                "note": None,
                "created_dt": row.scanned_at,
                "scanned_at": _iso_date(scanned_dt),
                "delivery": None,
            }
        )

    discrepancy_count = sum(1 for m in matches if m.amount_discrepancy)
    received_count = _received_match_count(matches)
    split_off = _split_off_count(matches)
    checked_summary = (
        f"Checked {len(pending)} unscanned item(s) / {len(all_trackings)} tracking number(s) "
        f"against BFMR ({len(receipts)} received / {fetched.raw_total} total tracker row(s); "
        f"{fetched.start_date}→{fetched.end_date})."
    )

    matched_ids = {m.item_id for m in matches}
    unmatched = _build_unmatched(pending, matched_ids)

    parts: list[str] = []
    if received_count:
        part = f"Found {received_count} received item(s) ready to mark scanned"
        if split_off:
            part += f" ({split_off} under-received split-off)"
        if discrepancy_count:
            part += f" ({discrepancy_count} amount discrepancy)"
        parts.append(part)
    if unknown_receipts:
        parts.append(f"{len(unknown_receipts)} BFMR tracker row(s) with tracking not in Order Manager")
    if not matches and not unknown_receipts:
        bits = [checked_summary]
        if not_found:
            bits.append(f"{not_found} local item(s) not found as received.")
        message = " ".join(bits) + " Nothing to review."
    else:
        message = ". ".join(parts) + f". {checked_summary}"

    if not pending and not unknown_receipts and not matches:
        message = (
            "No shipped/submitted (unscanned) items with tracking numbers for this buying group, "
            "and no unknown BFMR tracker rows."
            if not unscanned_items
            else f"{len(unscanned_items)} unscanned item(s) but none have tracking numbers, "
            "and no unknown BFMR tracker rows."
        )
        payload = {
            "buying_group_id": buying_group_id,
            "buying_group_name": group_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "not_found_count": 0,
            "void_skipped_count": 0,
            "searched_trackings": all_trackings,
            "raw_csv": "",
            "csv_row_count": len(receipts),
            "unmatched": [],
            "unknown_receipts": [],
            "matches": [],
        }
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            session_payload=payload,
            submitted_count=len(unscanned_items),
            message=message,
        )

    raw_dump = {
        "start_date": fetched.start_date,
        "end_date": fetched.end_date,
        "request_urls": fetched.request_urls,
        "raw_total": fetched.raw_total,
        "received_row_count": len(receipts),
        "tracker_rows": [r.raw for r in receipts[:200]],
        "unknown_count": len(unknown_receipts),
    }

    payload = {
        "buying_group_id": buying_group_id,
        "buying_group_name": group_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "not_found_count": not_found,
        "void_skipped_count": void_skipped,
        "searched_trackings": all_trackings,
        "raw_csv": json.dumps(raw_dump, default=str, indent=2),
        "csv_row_count": len(receipts),
        "unmatched": unmatched,
        "unknown_receipts": unknown_receipts,
        "matches": [match_to_dict(m) for m in matches],
    }
    return CheckReceivingResult(
        buying_group_id=buying_group_id,
        buying_group_name=group_name,
        session_payload=payload,
        matched_count=received_count,
        not_found_count=not_found,
        void_skipped_count=void_skipped,
        discrepancy_count=discrepancy_count,
        submitted_count=len(unscanned_items),
        matches=matches,
        message=message,
    )
