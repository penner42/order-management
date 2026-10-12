"""Match buying-group received-items / receipts to local unscanned items."""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import BuyingGroup, Item, Order, Shipment, ShipmentItem
from app.models.item import ItemStatus
from app.utils.buying_group_apis import bfmr as bfmr_api
from app.utils.buying_group_apis import buyinggroup as buyinggroup_api
from app.utils.buying_group_apis import parsefile as parsefile_api
from app.utils.buying_group_apis.submit_tracking import (
    bfmr_group_ready,
    buyinggroup_group_ready,
    parsefile_group_ready,
)
from app.utils.dates import to_date_only

# Items that may already be at the warehouse but not marked scanned locally.
_UNSCANNED_STATUSES = (ItemStatus.SHIPPED, ItemStatus.SUBMITTED)

# Short-lived in-memory sessions for the receiving review UI (same pattern as bulk import).
_receiving_sessions: dict[str, dict[str, Any]] = {}

AMOUNT_TOLERANCE = Decimal("0.01")


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


@dataclass
class CheckReceivingResult:
    buying_group_id: int
    buying_group_name: str
    token: str | None = None
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


def _pick_all_received_rows(
    candidates: list[parsefile_api.ParsefileReceivedItem],
    *,
    used_ids: set[int],
) -> list[parsefile_api.ParsefileReceivedItem]:
    """All unused non-void Parsefile rows (one per multi-package tracking)."""
    return [c for c in candidates if id(c) not in used_ids and not c.void]


def _pick_all_bg_receipts(
    candidates: list[buyinggroup_api.BuyinggroupReceipt],
    *,
    used_ids: set[int],
) -> list[buyinggroup_api.BuyinggroupReceipt]:
    """All unused non-void BuyingGroup receipts (one per multi-package tracking)."""
    return [c for c in candidates if id(c) not in used_ids and not c.void]


def _bfmr_received_total(row: bfmr_api.BfmrTrackerRow) -> Decimal | None:
    if row.amount_paid is not None and row.amount_paid > 0:
        return row.amount_paid
    qty = row.qty_received if row.qty_received is not None else row.qty
    if row.retail_price is not None and qty is not None and qty > 0:
        return (row.retail_price * Decimal(qty)).quantize(Decimal("0.01"))
    return None


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


def _index_bfmr_by_order_id(
    rows: list[bfmr_api.BfmrTrackerRow],
) -> dict[str, list[bfmr_api.BfmrTrackerRow]]:
    index: dict[str, list[bfmr_api.BfmrTrackerRow]] = {}
    for row in rows:
        oid = (row.order_id or "").strip()
        if not oid:
            continue
        index.setdefault(oid.casefold(), []).append(row)
    return index


def _pick_bfmr_rows_for_item(
    *,
    trackings: list[str],
    store_order_number: str | None,
    by_tracking: dict[str, list[bfmr_api.BfmrTrackerRow]],
    by_order_id: dict[str, list[bfmr_api.BfmrTrackerRow]],
    used_ids: set[int],
    item_qty: int,
    sibling_trackings: set[str] | None = None,
) -> list[bfmr_api.BfmrTrackerRow]:
    """Collect unused BFMR received rows for an item (all package trackings).

    Prefer exact tracking matches; then fill remaining quantity from other
    received rows that share the store order number (BFMR ``order_id``), skipping
    trackings that belong to sibling pending items.
    """
    picked: list[bfmr_api.BfmrTrackerRow] = []
    seen: set[int] = set()
    own_keys = {tn.casefold() for tn in trackings}
    reserved_for_siblings = {
        tn.casefold() for tn in (sibling_trackings or set()) if tn.casefold() not in own_keys
    }

    def _take(row: bfmr_api.BfmrTrackerRow) -> None:
        rid = id(row)
        if rid in used_ids or rid in seen:
            return
        seen.add(rid)
        picked.append(row)

    for tn in trackings:
        for row in by_tracking.get(tn.casefold()) or []:
            _take(row)

    order_key = (store_order_number or "").strip().casefold()
    if order_key:
        need = max(item_qty, 1)
        if len(picked) < need:
            for row in by_order_id.get(order_key) or []:
                if len(picked) >= need:
                    break
                row_tn = (row.tracking_number or "").strip().casefold()
                if row_tn and row_tn in reserved_for_siblings:
                    continue
                _take(row)

    return picked


def create_receiving_session(payload: dict[str, Any]) -> str:
    token = secrets.token_urlsafe(16)
    _receiving_sessions[token] = payload
    return token


def get_receiving_session(token: str) -> dict[str, Any] | None:
    return _receiving_sessions.get(token)


def pop_receiving_session(token: str) -> dict[str, Any] | None:
    return _receiving_sessions.pop(token, None)


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
    }


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


def _collect_pending(
    db: Session, *, buying_group_id: int
) -> tuple[list[Item], list[tuple[Item, Order, list[str]]], list[str]]:
    """Return (unscanned_items, pending triples, unique trackings).

    Each pending item is matched against:
    - trackings linked to that item, plus
    - other trackings on the same order that are not owned by a different
      pending item (so multi-package orders are fully checked for every
      framework: Parsefile, BuyingGroup.com, BFMR).
    """
    unscanned_items = (
        db.query(Item)
        .join(Order, Order.id == Item.order_id)
        .filter(Item.status.in_(_UNSCANNED_STATUSES))
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
    for item in unscanned_items:
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
    for item in unscanned_items:
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
    return unscanned_items, pending, all_trackings


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
    return CheckReceivingResult(
        buying_group_id=buying_group_id,
        buying_group_name=group_name,
        error="Receiving check is only available for Parsefile, BuyingGroup.com, or BFMR groups",
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
    used_row_ids: set[int] = set()
    matches: list[ReceivingMatch] = []
    not_found = 0
    void_skipped = 0
    void_matched_ids: set[int] = set()

    for item, order, trackings in pending:
        candidates: list[parsefile_api.ParsefileReceivedItem] = []
        seen_cand: set[int] = set()
        for tn in trackings:
            for row in by_tracking.get(tn.casefold()) or []:
                rid = id(row)
                if rid in seen_cand:
                    continue
                seen_cand.add(rid)
                candidates.append(row)

        if not candidates:
            not_found += 1
            continue
        if all(c.void for c in candidates):
            void_skipped += 1
            void_matched_ids.add(item.id)
            continue

        expected = _expected_amount(item)
        rows = _pick_all_received_rows(candidates, used_ids=used_row_ids)
        if not rows:
            void_skipped += 1
            if any(c.void for c in candidates):
                void_matched_ids.add(item.id)
            continue
        for row in rows:
            used_row_ids.add(id(row))

        matched_trackings: list[str] = []
        seen_mt: set[str] = set()
        for row in rows:
            for tn in row.tracking_numbers:
                key = tn.casefold()
                if key in seen_mt:
                    continue
                # Prefer trackings we were actually searching for.
                if trackings and key not in {t.casefold() for t in trackings}:
                    continue
                seen_mt.add(key)
                matched_trackings.append(tn)
        if not matched_trackings:
            matched_trackings = list(trackings[:1]) if trackings else []

        received_qty = 0
        received_total = Decimal("0")
        has_total = False
        scanned_dt = None
        description = None
        statuses: list[str] = []
        receipt_ids: list[str] = []
        unit_prices: list[Decimal] = []
        for row in rows:
            if row.qty is not None:
                received_qty += int(row.qty)
            total = row.ext_total
            if total is None and row.price is not None:
                total = row.price * (row.qty if row.qty is not None else 1)
            if total is not None:
                received_total += total
                has_total = True
            if row.price is not None:
                unit_prices.append(row.price)
            row_scanned = _parse_created_date(row.created_date)
            if row_scanned is not None and (scanned_dt is None or row_scanned > scanned_dt):
                scanned_dt = row_scanned
            if description is None and row.item:
                description = row.item
            if row.status:
                statuses.append(str(row.status))
            if row.receipt_id:
                receipt_ids.append(row.receipt_id)

        if received_qty <= 0:
            received_qty = len(rows)
        received_ext: Decimal | None = received_total if has_total else None
        received_price: Decimal | None = None
        if received_ext is not None and received_qty > 0:
            received_price = (received_ext / Decimal(received_qty)).quantize(Decimal("0.01"))
        elif unit_prices:
            received_price = unit_prices[0]

        if scanned_dt is None:
            scanned_dt = to_date_only(datetime.now(timezone.utc))
        discrepancy = _amounts_differ(expected, received_ext)

        matches.append(
            ReceivingMatch(
                item_id=item.id,
                order_id=order.id,
                store_order_number=(order.store_order_number or "").strip() or None,
                description=(item.description or "").strip() or None,
                quantity=item.quantity or 1,
                tracking_number=", ".join(matched_trackings),
                price_sold=_money(item.price_sold),
                expected_amount=expected,
                receipt_id=", ".join(receipt_ids) if receipt_ids else None,
                scanned_at=_iso_date(scanned_dt),
                received_item=description,
                received_qty=received_qty,
                received_price=received_price,
                received_ext_total=received_ext,
                received_status=", ".join(dict.fromkeys(statuses)) if statuses else None,
                amount_discrepancy=discrepancy,
                selected=True,
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
        if id(row) in used_row_ids or id(row) in orphan_seen:
            continue
        # Skip if any tracking on the row already exists in OM.
        if any(tn.casefold() in known_trackings for tn in row.tracking_numbers):
            continue
        orphan_seen.add(id(row))
        tn = row.tracking_numbers[0]
        received_total = row.ext_total
        if received_total is None and row.price is not None:
            received_total = row.price * (row.qty if row.qty is not None else 1)
        scanned_dt = _parse_created_date(row.created_date)
        unknown_receipts.append(
            {
                "receipt_id": row.receipt_id,
                "tracking_number": tn,
                "bg_order_id": None,
                "description": (row.item or "").strip() or None,
                "quantity": row.qty,
                "received_price": str(row.price) if row.price is not None else None,
                "received_ext_total": str(received_total) if received_total is not None else None,
                "received_status": row.status,
                "note": None,
                "created_dt": row.created_date,
                "scanned_at": _iso_date(scanned_dt),
                "delivery": row.delivery,
            }
        )

    discrepancy_count = sum(1 for m in matches if m.amount_discrepancy)
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
        token = create_receiving_session(payload)
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            token=token,
            submitted_count=len(unscanned_items),
            message=message,
        )

    parts: list[str] = []
    if matches:
        parts.append(
            f"Found {len(matches)} received item(s) ready to mark scanned"
            + (f" ({discrepancy_count} amount discrepancy)" if discrepancy_count else "")
        )
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
    token = create_receiving_session(payload)

    return CheckReceivingResult(
        buying_group_id=buying_group_id,
        buying_group_name=group_name,
        token=token,
        matched_count=len(matches),
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
    used_row_ids: set[int] = set()
    matches: list[ReceivingMatch] = []
    not_found = 0
    void_skipped = 0
    void_matched_ids: set[int] = set()

    for item, order, trackings in pending:
        candidates: list[buyinggroup_api.BuyinggroupReceipt] = []
        seen_cand: set[int] = set()
        for tn in trackings:
            for row in by_tracking.get(tn.casefold()) or []:
                rid = id(row)
                if rid in seen_cand:
                    continue
                seen_cand.add(rid)
                candidates.append(row)

        if not candidates:
            not_found += 1
            continue
        if all(c.void for c in candidates):
            void_skipped += 1
            void_matched_ids.add(item.id)
            continue

        expected = _expected_amount(item)
        rows = _pick_all_bg_receipts(candidates, used_ids=used_row_ids)
        if not rows:
            void_skipped += 1
            if any(c.void for c in candidates):
                void_matched_ids.add(item.id)
            continue
        for row in rows:
            used_row_ids.add(id(row))

        matched_trackings: list[str] = []
        seen_mt: set[str] = set()
        tracking_keys = {t.casefold() for t in trackings}
        for row in rows:
            for tn in (row.tracking_id, row.alt_tracking_id):
                text = (tn or "").strip()
                if not text:
                    continue
                key = text.casefold()
                if key in seen_mt:
                    continue
                if tracking_keys and key not in tracking_keys:
                    continue
                seen_mt.add(key)
                matched_trackings.append(text)
        if not matched_trackings:
            matched_trackings = list(trackings[:1]) if trackings else []

        received_qty = 0
        received_total = Decimal("0")
        has_total = False
        scanned_dt = None
        description = None
        statuses: list[str] = []
        receipt_ids: list[str] = []
        for row in rows:
            if row.quantity is not None:
                received_qty += int(row.quantity)
            if row.total is not None:
                received_total += row.total
                has_total = True
            row_scanned = _parse_created_date(row.created_dt)
            if row_scanned is not None and (scanned_dt is None or row_scanned > scanned_dt):
                scanned_dt = row_scanned
            if description is None and row.order_id:
                description = row.order_id
            if row.status:
                statuses.append(str(row.status))
            if row.receipt_id:
                receipt_ids.append(row.receipt_id)

        if received_qty <= 0:
            received_qty = len(rows)
        received_ext: Decimal | None = received_total if has_total else None
        received_price: Decimal | None = None
        if received_ext is not None and received_qty > 0:
            received_price = (received_ext / Decimal(received_qty)).quantize(Decimal("0.01"))

        if scanned_dt is None:
            scanned_dt = to_date_only(datetime.now(timezone.utc))
        discrepancy = _amounts_differ(expected, received_ext)

        matches.append(
            ReceivingMatch(
                item_id=item.id,
                order_id=order.id,
                store_order_number=(order.store_order_number or "").strip() or None,
                description=(item.description or "").strip() or None,
                quantity=item.quantity or 1,
                tracking_number=", ".join(matched_trackings),
                price_sold=_money(item.price_sold),
                expected_amount=expected,
                receipt_id=", ".join(receipt_ids) if receipt_ids else None,
                scanned_at=_iso_date(scanned_dt),
                received_item=description,
                received_qty=received_qty,
                received_price=received_price,
                received_ext_total=received_ext,
                received_status=", ".join(dict.fromkeys(statuses)) if statuses else None,
                amount_discrepancy=discrepancy,
                selected=True,
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
        if id(row) in used_row_ids or id(row) in orphan_seen:
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
    checked_summary = (
        f"Checked {len(pending)} unscanned item(s) / {len(all_trackings)} tracking number(s) "
        f"against BuyingGroup.com ({len(receipts)} receipt row(s))."
    )

    matched_ids = {m.item_id for m in matches}
    unmatched = _build_unmatched(pending, matched_ids, void_matched_ids=void_matched_ids)

    parts: list[str] = []
    if matches:
        parts.append(
            f"Found {len(matches)} received item(s) ready to mark scanned"
            + (f" ({discrepancy_count} amount discrepancy)" if discrepancy_count else "")
        )
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
        token = create_receiving_session(payload)
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            token=token,
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
    token = create_receiving_session(payload)

    return CheckReceivingResult(
        buying_group_id=buying_group_id,
        buying_group_name=group_name,
        token=token,
        matched_count=len(matches),
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
    by_order_id = _index_bfmr_by_order_id(receipts)
    used_row_ids: set[int] = set()
    matches: list[ReceivingMatch] = []
    not_found = 0
    void_skipped = 0
    all_pending_trackings = {tn.casefold() for _i, _o, tns in pending for tn in tns}

    for item, order, trackings in pending:
        store_order_number = (order.store_order_number or "").strip() or None
        candidates = _pick_bfmr_rows_for_item(
            trackings=trackings,
            store_order_number=store_order_number,
            by_tracking=by_tracking,
            by_order_id=by_order_id,
            used_ids=used_row_ids,
            item_qty=item.quantity or 1,
            sibling_trackings=all_pending_trackings,
        )
        if not candidates:
            not_found += 1
            continue

        for row in candidates:
            used_row_ids.add(id(row))

        expected = _expected_amount(item)
        matched_trackings: list[str] = []
        seen_mt: set[str] = set()
        for row in candidates:
            tn = (row.tracking_number or "").strip()
            if not tn:
                continue
            key = tn.casefold()
            if key in seen_mt:
                continue
            seen_mt.add(key)
            matched_trackings.append(tn)
        if not matched_trackings:
            matched_trackings = list(trackings[:1])

        received_qty = 0
        received_total = Decimal("0")
        has_total = False
        scanned_dt = None
        description = None
        statuses: list[str] = []
        receipt_ids: list[str] = []
        for row in candidates:
            qty = row.qty_received if row.qty_received is not None else row.qty
            if qty is not None:
                received_qty += int(qty)
            total = _bfmr_received_total(row)
            if total is not None:
                received_total += total
                has_total = True
            row_scanned = _parse_created_date(row.scanned_at)
            if row_scanned is not None and (scanned_dt is None or row_scanned > scanned_dt):
                scanned_dt = row_scanned
            if description is None:
                description = (row.item_name or row.deal_title or "").strip() or None
            if row.status:
                statuses.append(str(row.status))
            rid = _bfmr_receipt_id(row)
            if rid:
                receipt_ids.append(rid)

        if received_qty <= 0:
            received_qty = len(candidates)
        received_ext: Decimal | None = received_total if has_total else None
        received_price: Decimal | None = None
        if received_ext is not None and received_qty > 0:
            received_price = (received_ext / Decimal(received_qty)).quantize(Decimal("0.01"))

        if scanned_dt is None:
            scanned_dt = to_date_only(datetime.now(timezone.utc))
        discrepancy = _amounts_differ(expected, received_ext)

        matches.append(
            ReceivingMatch(
                item_id=item.id,
                order_id=order.id,
                store_order_number=store_order_number,
                description=(item.description or "").strip() or None,
                quantity=item.quantity or 1,
                tracking_number=", ".join(matched_trackings),
                price_sold=_money(item.price_sold),
                expected_amount=expected,
                receipt_id=", ".join(receipt_ids) if receipt_ids else None,
                scanned_at=_iso_date(scanned_dt),
                received_item=description,
                received_qty=received_qty,
                received_price=received_price,
                received_ext_total=received_ext,
                received_status=", ".join(dict.fromkeys(statuses)) if statuses else None,
                amount_discrepancy=discrepancy,
                selected=True,
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
        if id(row) in used_row_ids or id(row) in orphan_seen:
            continue
        orphan_seen.add(id(row))
        received_qty = row.qty_received if row.qty_received is not None else row.qty
        received_total = _bfmr_received_total(row)
        received_price: Decimal | None = None
        if received_total is not None and received_qty and received_qty > 0:
            received_price = (received_total / Decimal(received_qty)).quantize(Decimal("0.01"))
        elif row.retail_price is not None:
            received_price = row.retail_price
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
    checked_summary = (
        f"Checked {len(pending)} unscanned item(s) / {len(all_trackings)} tracking number(s) "
        f"against BFMR ({len(receipts)} received / {fetched.raw_total} total tracker row(s); "
        f"{fetched.start_date}→{fetched.end_date})."
    )

    matched_ids = {m.item_id for m in matches}
    unmatched = _build_unmatched(pending, matched_ids)

    parts: list[str] = []
    if matches:
        parts.append(
            f"Found {len(matches)} received item(s) ready to mark scanned"
            + (f" ({discrepancy_count} amount discrepancy)" if discrepancy_count else "")
        )
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
        token = create_receiving_session(payload)
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            token=token,
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
    token = create_receiving_session(payload)

    return CheckReceivingResult(
        buying_group_id=buying_group_id,
        buying_group_name=group_name,
        token=token,
        matched_count=len(matches),
        not_found_count=not_found,
        void_skipped_count=void_skipped,
        discrepancy_count=discrepancy_count,
        submitted_count=len(unscanned_items),
        matches=matches,
        message=message,
    )
