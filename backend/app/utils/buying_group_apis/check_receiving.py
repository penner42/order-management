"""Match Parsefile received-items search results to local unscanned items."""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import BuyingGroup, Item, Order, Shipment, ShipmentItem
from app.models.item import ItemStatus
from app.utils.buying_group_apis import parsefile as parsefile_api
from app.utils.buying_group_apis.submit_tracking import parsefile_group_ready
from app.utils.dates import to_date_only

# Items that may already be at the warehouse but not marked scanned locally.
_UNSCANNED_STATUSES = (ItemStatus.SHIPPED, ItemStatus.SUBMITTED)

# Short-lived in-memory sessions for the receiving review UI (same pattern as bulk import).
_receiving_sessions: dict[str, dict[str, Any]] = {}

TRACKING_SEARCH_CHUNK = 150
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
    # Common Parsefile export formats: "2021-02-01 00:00:00", "2021-02-01", ISO.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y"):
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


def _pick_received_row(
    candidates: list[parsefile_api.ParsefileReceivedItem],
    *,
    expected: Decimal | None,
    used_ids: set[int],
) -> parsefile_api.ParsefileReceivedItem | None:
    available = [c for c in candidates if id(c) not in used_ids and not c.void]
    if not available:
        return None
    if len(available) == 1 or expected is None:
        return available[0]

    def score(row: parsefile_api.ParsefileReceivedItem) -> tuple[Decimal, int]:
        total = row.ext_total
        if total is None and row.price is not None and row.qty is not None:
            total = row.price * row.qty
        if total is None:
            return (Decimal("999999"), 1)
        return (abs(total - expected), 0)

    available.sort(key=score)
    return available[0]


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


def _tracking_numbers_for_item(db: Session, item: Item) -> list[str]:
    """All distinct tracking numbers linked to an item (not just the first)."""
    out: list[str] = []
    seen: set[str] = set()
    for si in item.shipment_items or []:
        tn = ""
        if si.shipment and (si.shipment.tracking_number or "").strip():
            tn = si.shipment.tracking_number.strip()
        if not tn:
            continue
        key = tn.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(tn)
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
        tn = (raw or "").strip()
        if not tn:
            continue
        key = tn.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(tn)
    return out


def check_receiving_for_group(db: Session, *, buying_group_id: int) -> CheckReceivingResult:
    """Search Parsefile received-items for unscanned trackings; build a review session."""
    group = db.query(BuyingGroup).filter(BuyingGroup.id == buying_group_id).first()
    if not group:
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=f"group {buying_group_id}",
            error="Buying group not found",
        )

    group_name = (group.name or "").strip() or f"group {buying_group_id}"
    ready, reason = parsefile_group_ready(group)
    if not ready:
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            error=reason or "Buying group Parsefile API is not configured",
        )

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

    # One entry per item, with every linked tracking number.
    pending: list[tuple[Item, Order, list[str]]] = []
    for item in unscanned_items:
        order = item.order
        if not order:
            continue
        trackings = _tracking_numbers_for_item(db, item)
        if not trackings:
            continue
        pending.append((item, order, trackings))

    all_trackings: list[str] = []
    seen_tn: set[str] = set()
    for _item, _order, trackings in pending:
        for tn in trackings:
            key = tn.casefold()
            if key in seen_tn:
                continue
            seen_tn.add(key)
            all_trackings.append(tn)

    if not pending:
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            submitted_count=len(unscanned_items),
            message=(
                "No shipped/submitted (unscanned) items with tracking numbers for this buying group."
                if not unscanned_items
                else f"{len(unscanned_items)} unscanned item(s) but none have tracking numbers."
            ),
        )

    base_url = str(group.base_url)
    bearer_token = str(group.bearer_token)
    api_user_id = int(group.api_user_id)
    api_email = str(group.api_email)

    received_rows: list[parsefile_api.ParsefileReceivedItem] = []
    try:
        for i in range(0, len(all_trackings), TRACKING_SEARCH_CHUNK):
            chunk = all_trackings[i : i + TRACKING_SEARCH_CHUNK]
            received_rows.extend(
                parsefile_api.search_received_items(
                    base_url=base_url,
                    bearer_token=bearer_token,
                    user_id=api_user_id,
                    email=api_email,
                    trackings=chunk,
                )
            )
    except Exception as exc:
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            submitted_count=len(unscanned_items),
            error=str(exc).strip() or exc.__class__.__name__,
        )

    by_tracking = _index_received_by_tracking(received_rows)
    used_row_ids: set[int] = set()
    matches: list[ReceivingMatch] = []
    not_found = 0
    void_skipped = 0

    for item, order, trackings in pending:
        candidates: list[parsefile_api.ParsefileReceivedItem] = []
        seen_cand: set[int] = set()
        matched_tracking = trackings[0]
        for tn in trackings:
            for row in by_tracking.get(tn.casefold()) or []:
                rid = id(row)
                if rid in seen_cand:
                    continue
                seen_cand.add(rid)
                candidates.append(row)
            if any(not c.void for c in (by_tracking.get(tn.casefold()) or [])):
                matched_tracking = tn

        if not candidates:
            not_found += 1
            continue
        if all(c.void for c in candidates):
            void_skipped += 1
            continue

        expected = _expected_amount(item)
        row = _pick_received_row(candidates, expected=expected, used_ids=used_row_ids)
        if row is None:
            void_skipped += 1
            continue
        used_row_ids.add(id(row))

        # Prefer a tracking that actually appears on the matched Parsefile row.
        for tn in trackings:
            if tn.casefold() in {t.casefold() for t in row.tracking_numbers}:
                matched_tracking = tn
                break

        received_total = row.ext_total
        if received_total is None and row.price is not None:
            received_total = row.price * (row.qty if row.qty is not None else (item.quantity or 1))

        scanned_dt = _parse_created_date(row.created_date) or to_date_only(datetime.now(timezone.utc))
        discrepancy = _amounts_differ(expected, received_total)

        matches.append(
            ReceivingMatch(
                item_id=item.id,
                order_id=order.id,
                store_order_number=(order.store_order_number or "").strip() or None,
                description=(item.description or "").strip() or None,
                quantity=item.quantity or 1,
                tracking_number=matched_tracking,
                price_sold=_money(item.price_sold),
                expected_amount=expected,
                receipt_id=row.receipt_id,
                scanned_at=_iso_date(scanned_dt),
                received_item=row.item,
                received_qty=row.qty,
                received_price=row.price,
                received_ext_total=received_total,
                received_status=row.status,
                amount_discrepancy=discrepancy,
                selected=True,
            )
        )

    discrepancy_count = sum(1 for m in matches if m.amount_discrepancy)
    checked_summary = (
        f"Checked {len(pending)} unscanned item(s) / {len(all_trackings)} tracking number(s) "
        f"against Parsefile."
    )

    if not matches:
        parts = [checked_summary]
        if not_found:
            parts.append(f"{not_found} not found as received.")
        if void_skipped:
            parts.append(f"{void_skipped} voided at Parsefile.")
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            submitted_count=len(unscanned_items),
            not_found_count=not_found,
            void_skipped_count=void_skipped,
            message=" ".join(parts) + " Nothing to mark scanned.",
        )

    payload = {
        "buying_group_id": buying_group_id,
        "buying_group_name": group_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "not_found_count": not_found,
        "void_skipped_count": void_skipped,
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
        message=(
            f"Found {len(matches)} received item(s) ready to mark scanned"
            + (f" ({discrepancy_count} amount discrepancy)" if discrepancy_count else "")
            + f". {checked_summary}"
        ),
    )
