"""Match buying-group received-items / receipts to local unscanned items."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, TypeVar
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import (
    BuyingGroup,
    Item,
    Order,
    ReceivingReviewPending,
    ReceivingReceiptCache,
    Shipment,
    ShipmentItem,
    UsabgPoCache,
)
from app.models.item import ItemStatus
from app.browser_automation.buyinggroup_session import BrowserAccess, supports_browser_session
from app.schemas.buying_group import ReceivingDamageMatchRequest
from app.utils.buying_group_apis import bfmr as bfmr_api
from app.utils.buying_group_apis import buyinggroup as buyinggroup_api
from app.utils.buying_group_apis import dealandrunner as dealandrunner_api
from app.utils.buying_group_apis import parsefile as parsefile_api
from app.utils.buying_group_apis import usabg as usabg_api
from app.utils.buying_group_apis.submit_tracking import (
    bfmr_group_ready,
    buyinggroup_group_ready,
    dealandrunner_group_ready,
    parsefile_group_ready,
    usabg_group_ready,
)
from app.utils.dates import to_date_only
from app.utils.receipt_upc import receipt_upc, absorb_upc
from app.utils.receiving_cache import ReceiptSource, fetch_receipt_source, provider_identity
from app.utils.receiving_links import (
    applied_receipt_pairs, manual_source_receipts, match_includes_receipt, receipt_is_applied, receipt_key, with_receipt_keys,
    apply_damage_match, pending_row, refresh_manual_assignments,
)

# Items that may already be at the warehouse but not marked scanned locally.
_UNSCANNED_STATUSES = (ItemStatus.SHIPPED, ItemStatus.SUBMITTED, ItemStatus.NEEDS_REVIEW)

# Already received locally — only surface on review when receipt ID would change.
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
# When a tracking has multiple warehouse product lines, match local items to
# lots by unit-price proximity (covers ~$1 BG vs local drift) before names.
LOT_PRICE_TOLERANCE = Decimal("2.00")
_LOT_NAME_FALLBACK_MIN = 0.35
_LOT_NAME_ONLY_MIN = 0.25
_LOT_AMBIGUITY_NAME_MARGIN = 0.05
_DESC_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "with",
        "for",
        "a",
        "an",
        "of",
        "to",
        "in",
        "on",
        "or",
        "by",
        "from",
        "new",
        "latest",
        "model",
        "white",
        "black",
        "gps",
    }
)

MATCH_KIND_RECEIVED = "received"
MATCH_KIND_NOT_RECEIVED = "not_received"

# Shipment note lines that alias an alternate warehouse / Walmart tracker.
_WALMART_TRACKING_NOTE_PREFIX = "Walmart tracking: "
_WAREHOUSE_TRACKING_NOTE_PREFIX = "Warehouse tracking: "
_NOTE_TRACKING_PREFIXES = (
    _WALMART_TRACKING_NOTE_PREFIX,
    _WAREHOUSE_TRACKING_NOTE_PREFIX,
)


@dataclass
class ReceivingMatch:
    item_id: int
    order_id: int
    store_order_number: str | None
    description: str | None
    quantity: int
    tracking_number: str
    price_sold: Decimal
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
    # Item is already scanned+; update receipt details/reward without changing status.
    update_receipt_only: bool = False
    reward: Decimal | None = None
    receipt_upc: str | None = None
    tracking_reward_adjusted_price: Decimal | None = None
    confirmed_reward: Decimal | None = None
    saved_scanned_at: str | None = None


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
    retry_after: int | None = None
    browser_sign_in_required: bool = False


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


def _is_zero_payout(item: Item) -> bool:
    """True when local expected payout is $0 (e-delivery / gratis / unset).

    These lines are not warehouse inventory and must not claim box qty or
    inherit a sibling product's received amount on a shared tracking.
    """
    expected = _expected_amount(item)
    return expected is not None and expected == 0


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


_Receipt = TypeVar("_Receipt")


def _receipts_since_first_order(
    db: Session, rows: list[_Receipt], *, date_field: str
) -> list[_Receipt]:
    """Ignore receipts predating OM history, retaining undated rows and the first day."""
    earliest = db.query(func.min(Order.purchase_date)).scalar()
    if earliest is None:
        return rows
    if earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=timezone.utc)
    cutoff = to_date_only(earliest)
    eligible = []
    for row in rows:
        received_at = _parse_created_date(getattr(row, date_field))
        if received_at is None or received_at >= cutoff:
            eligible.append(row)
    return eligible


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
    return sum(1 for m in matches if isinstance(m, dict) and _is_actionable_match_dict(m)) + len(payload.get("damaged_items") or [])


def _has_receiving_review(payload: dict[str, Any], count: int) -> bool:
    return bool(count or payload.get("unknown_receipts") or payload.get("damaged_items_error"))


def _receipt_details(row: Any) -> dict[str, Any]:
    """Keep provider-specific receipt fields, including Decimal values, in JSON."""
    return json.loads(json.dumps(asdict(row), default=str))


def save_receiving_review_pending(db: Session, payload: dict[str, Any], *, commit: bool = True) -> int:
    """Upsert or clear pending review for one buying group. Returns actionable match count."""
    buying_group_id = int(payload["buying_group_id"])
    group = db.query(BuyingGroup).filter_by(id=buying_group_id).with_for_update().one()
    existing = (
        db.query(ReceivingReviewPending)
        .filter(ReceivingReviewPending.buying_group_id == buying_group_id)
        .with_for_update()
        .first()
    )
    # Unknown receipts are a separate review queue. Keep them when a later check
    # no longer returns them (for example, outside a provider's date window).
    previous = existing.payload if existing and isinstance(existing.payload, dict) else {}
    manual, released = refresh_manual_assignments(db, buying_group_id, previous)
    released = [r for r in previous.get("unknown_receipts", []) if r.get("manual_link_invalid")] + released
    manual_sources = [source for match in manual for source in manual_source_receipts(match)]
    manual_keys = {receipt_key(source) for source in manual_sources}
    manual_items = {m["item_id"] for m in manual}
    payload = {**payload, "matches": [
        m for m in payload.get("matches", [])
        if m.get("item_id") not in manual_items
        and not any(match_includes_receipt(m, receipt)
                    for receipt in [*manual_sources, *released])
    ] + manual}
    received_items = {m["item_id"] for m in payload["matches"] if _is_actionable_match_dict(m)}
    payload["matches"] = [m for m in payload["matches"]
                          if _is_actionable_match_dict(m) or m["item_id"] in received_items]
    applied_pairs = applied_receipt_pairs(db, buying_group_id)
    framework = (group.api_framework or "").strip().casefold()
    is_buyinggroup = framework == "buyinggroup"
    is_dnr = framework == "dealandrunner"
    count = actionable_count_from_payload(payload)
    known_trackings = _known_shipment_trackings(db) if not (is_buyinggroup or is_dnr) else set()
    accounted_dnr_keys = set(payload.pop("_accounted_dnr_receipt_keys", [])) if is_dnr else set()
    current_unknown_keys = {receipt_key(r) for r in payload.get("unknown_receipts", [])}
    accounted_dnr_keys.difference_update(current_unknown_keys)
    unknown_by_key = {}
    for receipt in [*(r for r in (previous.get("unknown_receipts") or []) if r.get("linkable") is not False), *(payload.get("unknown_receipts") or []), *released]:
        # Older BG snapshots used an empty tracking number. Normalize before
        # merging so the receipt-ID fallback replaces the same saved product.
        if is_buyinggroup:
            trackings = [tn.strip() for tn in
                         [receipt.get("tracking_number") or "", *(receipt.get("tracking_numbers") or [])]
                         if tn and tn.strip()]
            rid = (receipt.get("receipt_id") or "").strip()
            if not trackings and rid:
                receipt = {**receipt, "tracking_number": rid, "tracking_numbers": [rid]}
        if receipt_key(receipt) in manual_keys or receipt_is_applied(receipt, applied_pairs):
            continue
        if receipt_key(receipt) in accounted_dnr_keys and not receipt.get("manual_link_invalid"):
            continue
        # A formerly unresolved BG product may now have an automatic match.
        # Keep any current leftover quantity, but discard its stale queue copy.
        if (is_buyinggroup and receipt_key(receipt) not in current_unknown_keys
            and any(_is_actionable_match_dict(m) and match_includes_receipt(m, receipt)
                    for m in payload["matches"])):
            continue
        trackings = receipt.get("tracking_numbers") or [receipt["tracking_number"]]
        # BG and D&R already exclude allocated quantities when building this queue.
        if (known_trackings
            and not receipt.get("manual_link_invalid")
            and any(tn.strip().casefold() in known_trackings for tn in trackings)):
            continue
        unknown_by_key[receipt_key(receipt)] = receipt
    payload = {**payload, "unknown_receipts": list(unknown_by_key.values())}
    if not _has_receiving_review(payload, count):
        if existing is not None:
            db.delete(existing)
        if commit:
            db.commit()
        else:
            db.flush()
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
    if commit:
        db.commit()
    else:
        db.flush()
    return count


def list_receiving_review_pending_payloads(db: Session) -> list[dict[str, Any]]:
    # Lock groups in a stable order before reading caches or pending assignments.
    from sqlalchemy import or_
    groups = (db.query(BuyingGroup)
              .outerjoin(ReceivingReceiptCache, ReceivingReceiptCache.buying_group_id == BuyingGroup.id)
              .outerjoin(ReceivingReviewPending, ReceivingReviewPending.buying_group_id == BuyingGroup.id)
              .filter(or_(ReceivingReceiptCache.buying_group_id.isnot(None),
                          ReceivingReviewPending.buying_group_id.isnot(None)))
              .order_by(BuyingGroup.id).with_for_update(of=BuyingGroup).all())
    matchers = {
        "parsefile": _check_receiving_parsefile, "buyinggroup": _check_receiving_buyinggroup,
        "bfmr": _check_receiving_bfmr, "usabg": _check_receiving_usabg,
        "dealandrunner": _check_receiving_dealandrunner,
    }
    out = []
    for group in groups:
        cache = db.query(ReceivingReceiptCache).populate_existing().filter_by(buying_group_id=group.id).first()
        row = db.query(ReceivingReviewPending).populate_existing().filter_by(buying_group_id=group.id).first()
        if cache is not None:
            # Never show warehouse data belonging to a previous account/provider.
            if cache.provider_identity != provider_identity(group):
                continue
            if cache.payload.get("version") == 1:
                matcher = matchers.get((group.api_framework or "").strip().casefold())
                if matcher:
                    result = matcher(db, group=group, group_name=group.name, source=ReceiptSource(cache.payload))
                    if result.error:
                        raise ValueError(result.error)
                    payload = result.session_payload
                    payload["created_at"] = cache.fetched_at.isoformat()
                    payload["rematched_at"] = datetime.now(timezone.utc).isoformat()
                    save_receiving_review_pending(db, payload, commit=False)
                    row = db.get(ReceivingReviewPending, group.id)
        if row is not None and isinstance(row.payload, dict):
            payload = row.payload
            if _has_receiving_review(payload, actionable_count_from_payload(payload)):
                out.append(with_receipt_keys(payload))
    db.commit()
    return sorted(out, key=lambda p: p["buying_group_name"].casefold())


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
    db.flush()


def prune_receiving_review_pending(
    db: Session,
    *,
    buying_group_id: int,
    item_ids: list[int],
) -> tuple[int, bool]:
    """Remove applied item rows from pending snapshot. Returns (remaining_count, deleted)."""
    db.query(BuyingGroup).filter_by(id=buying_group_id).with_for_update().one()
    id_set = {int(i) for i in item_ids if i}
    existing = (
        db.query(ReceivingReviewPending)
        .filter(ReceivingReviewPending.buying_group_id == buying_group_id)
        .with_for_update()
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
        if isinstance(m, dict) and (m.get("manual_receipt") or int(m.get("item_id") or 0) not in id_set)
    ]
    payload = {**payload, "matches": new_matches}
    count = actionable_count_from_payload(payload)
    if not _has_receiving_review(payload, count):
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
    if framework == "dealandrunner":
        return dealandrunner_group_ready(group)
    return False, (
        "Receiving check is only available for Parsefile, BuyingGroup.com, "
        "BFMR, USABG, or Deal & Runner groups"
    )


def match_to_dict(m: ReceivingMatch) -> dict[str, Any]:
    return {
        "item_id": m.item_id,
        "order_id": m.order_id,
        "store_order_number": m.store_order_number,
        "description": m.description,
        "quantity": m.quantity,
        "tracking_number": m.tracking_number,
        "price_sold": str(m.price_sold if m.price_sold is not None else 0),
        "reward": str(m.reward) if m.reward is not None else None,
        "confirmed_reward": str(m.confirmed_reward) if m.confirmed_reward is not None else None,
        "tracking_reward_adjusted_price": str(m.tracking_reward_adjusted_price) if m.tracking_reward_adjusted_price is not None else None,
        "expected_amount": str(m.expected_amount) if m.expected_amount is not None else None,
        "receipt_id": m.receipt_id,
        "scanned_at": m.scanned_at,
        "saved_scanned_at": m.saved_scanned_at,
        "received_item": m.received_item,
        "receipt_upc": m.receipt_upc,
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


def _description_tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    raw = re.findall(r"[a-z0-9]+", text.casefold())
    return {t for t in raw if len(t) >= 2 and t not in _DESC_STOPWORDS}


def _name_similarity(a: str | None, b: str | None) -> float:
    ta = _description_tokens(a)
    tb = _description_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return float(inter) / float(union) if union else 0.0


def _usabg_lot_merge_key(row: usabg_api.UsabgReceivedRow) -> tuple[str, str, str, str, str | None]:
    tn = (row.tracking_number or "").strip().casefold()
    sku = (row.sku or "").strip().casefold()
    price = str(_money(row.unit_price) if row.unit_price is not None else "")
    name = (row.received_item or "").strip().casefold()[:120]
    return (tn, sku, price, name, receipt_upc(row))


def _dnr_lot_merge_key(
    row: dealandrunner_api.DealandrunnerReceivedRow, tn: str
) -> tuple[str, str, str, str | None, str]:
    """Keep separate payouts and products in the D&R ledger."""
    tn_key = (tn or "").strip().casefold()
    price = str(_money(row.amount) if row.amount is not None else "")
    name = (row.product_name or "").strip().casefold()
    product_id = str(row.raw.get("referenceProductId") or row.raw.get("productId") or "").strip().casefold()
    if product_id == "00000000-0000-0000-0000-000000000000":
        product_id = ""
    return (tn_key, price, name, receipt_upc(row), product_id)


def _dnr_lots_from_receipts(
    receipts: list[dealandrunner_api.DealandrunnerReceivedRow],
) -> list[dict[str, Any]]:
    """Collapse identical D&R ledger lines into product lots per tracking.

    Each API row is one warehouse unit (qty 1). Supply for a TN is the row count.
    """
    merged = {}
    for row in receipts:
        if row.entry_type_id != dealandrunner_api.RECEIVING_ENTRY_TYPE_ID:
            continue
        tns = [tn.strip() for tn in (row.tracking_numbers or []) if (tn or "").strip()]
        if not tns:
            continue
        # Attribute the unit to the primary TN; aliases are registered below.
        primary = tns[0]
        key = _dnr_lot_merge_key(row, primary)
        tn_key = primary.casefold()
        lot = merged.get(key)
        if lot is None:
            _, _, name, upc, product_id = key
            lot = {
                "lot_id": f"{tn_key}:{len(merged)}",
                "tn_key": tn_key,
                "product_key": (
                    tn_key, product_id, "" if product_id else name, upc
                ) if product_id or name else None,
                "display_tn": primary,
                "alias_tns": [],
                "qty": 0,
                "unit_price": _money(row.amount),
                "received_item": (row.product_name or "").strip() or None,
                "sku": None,
                "receipt_upc": receipt_upc(row),
                "receipt_ids": [],
                "rows": [],
                "statuses": [],
                "scanned_at": None,
            }
            merged[key] = lot
        lot["qty"] += 1
        lot["rows"].append(row)
        if row.system_transaction_id:
            lot["receipt_ids"].append(row.system_transaction_id)
        for alias in tns[1:]:
            alias_key = alias.casefold()
            if alias_key and alias_key not in lot["alias_tns"] and alias_key != tn_key:
                lot["alias_tns"].append(alias_key)
        row_scanned = _parse_created_date(row.timestamp)
        if row_scanned is not None and (
            lot["scanned_at"] is None or row_scanned > lot["scanned_at"]
        ):
            lot["scanned_at"] = row_scanned
        if not lot["received_item"] and row.product_name:
            lot["received_item"] = (row.product_name or "").strip() or None
        if lot["unit_price"] is None and row.amount is not None:
            lot["unit_price"] = _money(row.amount)
    return list(merged.values())


def _usabg_lots_from_receipts(
    receipts: list[usabg_api.UsabgReceivedRow],
) -> list[dict[str, Any]]:
    """Collapse identical USABG box lines into product lots per tracking."""
    merged: dict[tuple[str, str, str, str, str | None], dict[str, Any]] = {}
    order: list[tuple[str, str, str, str, str | None]] = []
    for row in receipts:
        tn = (row.tracking_number or "").strip()
        if not tn:
            continue
        qty = int(row.quantity) if row.quantity and row.quantity > 0 else 1
        key = _usabg_lot_merge_key(row)
        tn_key = tn.casefold()
        lot = merged.get(key)
        if lot is None:
            lot = {
                "lot_id": f"{tn_key}:{len(order)}",
                "tn_key": tn_key,
                "display_tn": tn,
                "qty": 0,
                "unit_price": _money(row.unit_price),
                "received_item": (row.received_item or "").strip() or None,
                "sku": (row.sku or "").strip() or None,
                "receipt_upc": receipt_upc(row),
                "receipt_ids": [],
                "statuses": [],
                "scanned_at": None,
            }
            merged[key] = lot
            order.append(key)
        lot["qty"] += qty
        if row.receipt_id:
            lot["receipt_ids"].append(row.receipt_id)
        for bit in (row.receiving_status, row.received_status):
            if bit:
                lot["statuses"].append(str(bit))
        row_scanned = _parse_created_date(row.scanned_at)
        if row_scanned is not None and (
            lot["scanned_at"] is None or row_scanned > lot["scanned_at"]
        ):
            lot["scanned_at"] = row_scanned
        if not lot["received_item"] and row.received_item:
            lot["received_item"] = (row.received_item or "").strip() or None
        if lot["unit_price"] is None and row.unit_price is not None:
            lot["unit_price"] = _money(row.unit_price)
    return [merged[k] for k in order]


def _lot_as_match_meta(lot: dict[str, Any]) -> dict[str, Any]:
    """Meta dict for one lot so payout uses this lot's unit price only."""
    unit = _money(lot.get("unit_price"))
    return {
        "receipt_ids": list(lot.get("receipt_ids") or []),
        "statuses": list(lot.get("statuses") or []),
        "unit_prices": [unit] if unit is not None else [],
        "scanned_at": lot.get("scanned_at"),
        "received_item": lot.get("received_item"),
        "display_tn": lot.get("display_tn"),
        "sku": lot.get("sku"),
        "receipt_upc": lot.get("receipt_upc"),
        "unit_reward": lot.get("unit_reward"),
    }


def _meta_by_tn_from_lots(lots: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Merged per-TN meta (receipt ids / supply display) for receipt-only updates."""
    meta_by_tn: dict[str, dict[str, Any]] = {}
    for lot in lots:
        tn_key = lot["tn_key"]
        meta = meta_by_tn.setdefault(
            tn_key,
            {
                "receipt_ids": [],
                "statuses": [],
                "unit_prices": [],
                "scanned_at": None,
                "received_item": None,
                "display_tn": lot.get("display_tn"),
            },
        )
        absorb_upc(meta, lot.get("receipt_upc"))
        meta["receipt_ids"].extend(list(lot.get("receipt_ids") or []))
        meta["statuses"].extend(list(lot.get("statuses") or []))
        unit = _money(lot.get("unit_price"))
        if unit is not None:
            meta["unit_prices"].append(unit)
        scanned = lot.get("scanned_at")
        if scanned is not None and (
            meta["scanned_at"] is None or scanned > meta["scanned_at"]
        ):
            meta["scanned_at"] = scanned
        if meta["received_item"] is None and lot.get("received_item"):
            meta["received_item"] = lot.get("received_item")
    return meta_by_tn


def _item_matching_upc(item: Item) -> str | None:
    """Prefer warehouse-confirmed identity, falling back to the imported product UPC."""
    return getattr(item, "receipt_upc", None) or getattr(item, "upc", None)


def _score_item_to_lot(
    item: Item, lot: dict[str, Any]
) -> tuple[float, float] | None:
    """Return (price_score, name_score) for ranking; higher is better. None = reject."""
    local_upc = _item_matching_upc(item)
    lot_upc = lot.get("receipt_upc")
    if local_upc and lot_upc:
        if local_upc != lot_upc:
            return None
        return (1000.0, 1.0)
    local_price = _money(item.price_sold)
    lot_price = _money(lot.get("unit_price"))
    name_score = _name_similarity(item.description, lot.get("received_item"))

    if local_price is not None and lot_price is not None:
        delta = abs(local_price - lot_price)
        if delta <= LOT_PRICE_TOLERANCE:
            return (float(LOT_PRICE_TOLERANCE - delta), name_score)
        if name_score >= _LOT_NAME_FALLBACK_MIN:
            # Prefer closer prices among name-only fallbacks.
            return (-1.0 - float(min(delta, Decimal("100"))), name_score)
        return None

    if name_score >= _LOT_NAME_ONLY_MIN:
        return (-0.5, name_score)
    return None


def _scores_ambiguous(
    best: tuple[float, float],
    second: tuple[float, float] | None,
) -> bool:
    if second is None:
        return False
    if abs(best[0] - second[0]) > 1e-9:
        return False
    return abs(best[1] - second[1]) <= _LOT_AMBIGUITY_NAME_MARGIN


def _score_bg_item_to_lot(
    item: Item, lot: dict[str, Any], *, single_line: bool = False,
) -> tuple[float, float] | None:
    """BG product identity comes from UPC, then name; payout is reviewed separately."""
    local_upc = _item_matching_upc(item)
    if local_upc and lot.get("receipt_upc"):
        return (1000.0, 1.0) if local_upc == lot["receipt_upc"] else None
    similarity = _name_similarity(item.description, lot.get("received_item"))
    if similarity < _LOT_NAME_ONLY_MIN and not (single_line and similarity > 0):
        return None
    # Keep fallback name scores in the second position so the existing
    # ambiguity margin applies to competing products with similar names.
    return (0.0, similarity)


def _assign_items_to_lots(
    *,
    claimants: list[tuple[Item, list[str]]],
    lots_by_tn: dict[str, list[dict[str, Any]]],
    group_by_lot_id: dict[str, str] | None = None,
) -> dict[int, str]:
    """Map item_id → lot_id. Omits ambiguous or unscored items on multi-lot trackings."""
    assignment: dict[int, str] = {}
    lot_groups = group_by_lot_id or {}
    for item, trackings in claimants:
        tn_keys = [tn.casefold() for tn in trackings if (tn or "").strip()]
        lots: list[dict[str, Any]] = []
        seen_lot_ids: set[str] = set()
        for tn_key in tn_keys:
            for lot in lots_by_tn.get(tn_key) or []:
                lid = lot["lot_id"]
                if lid in seen_lot_ids:
                    continue
                seen_lot_ids.add(lid)
                lots.append(lot)
        if not lots:
            continue
        if len(lots) == 1 and not lots[0].get("require_product_match"):
            assignment[item.id] = lots[0]["lot_id"]
            continue

        ranked: list[tuple[tuple[float, float], str]] = []
        for lot in lots:
            score = _score_item_to_lot(item, lot)
            if score is None:
                continue
            ranked.append((score, lot["lot_id"]))
        if not ranked:
            continue
        ranked.sort(key=lambda pair: (pair[0][0], pair[0][1]), reverse=True)
        best_score, best_lot_id = ranked[0]
        best_group = lot_groups.get(best_lot_id, best_lot_id)
        second_score = next(
            (score for score, lot_id in ranked[1:] if lot_groups.get(lot_id, lot_id) != best_group),
            None,
        )
        if _scores_ambiguous(best_score, second_score):
            continue
        assignment[item.id] = best_lot_id
    return assignment


def _allocate_dnr_lots(
    claimants: list[tuple[Item, list[str]]],
    lots: list[dict[str, Any]],
    lots_by_tn: dict[str, list[dict[str, Any]]],
) -> dict[int, list[tuple[str, int]]]:
    """Allocate preferred payouts first, then fill gaps from the same product."""
    group_by_product = {}
    group_by_lot_id = {}
    lots_by_id = {lot["lot_id"]: lot for lot in lots}
    for lot in lots:
        product = lot["product_key"]
        if product is not None:
            group_by_lot_id[lot["lot_id"]] = group_by_product.setdefault(product, lot["lot_id"])

    assignment = _assign_items_to_lots(
        claimants=claimants, lots_by_tn=lots_by_tn, group_by_lot_id=group_by_lot_id,
    )
    supply = {lot["lot_id"]: int(lot["qty"]) for lot in lots}
    allocated, detail = _allocate_received_across_shared_trackings(
        item_demands=[(item.id, item.quantity or 1, [assignment[item.id]])
                      for item, _ in claimants if item.id in assignment],
        supply_by_tn=supply,
    )
    for pairs in detail.values():
        for lot_id, qty in pairs:
            supply[lot_id] -= qty

    extra_demands = []
    for item, trackings in claimants:
        remaining = (item.quantity or 1) - allocated.get(item.id, 0)
        preferred_id = assignment.get(item.id)
        group = group_by_lot_id.get(preferred_id) if preferred_id else None
        if remaining <= 0 or group is None:
            continue
        preferred = lots_by_id[preferred_id]
        local_upc = _item_matching_upc(item)
        lot_upc = preferred["receipt_upc"]
        if local_upc and lot_upc:
            same_product = local_upc == lot_upc
        else:
            same_product = _name_similarity(item.description, preferred["received_item"]) >= _LOT_NAME_ONLY_MIN
        if not same_product:
            continue
        alternatives = list(dict.fromkeys(
            lot["lot_id"]
            for tn in trackings
            for lot in lots_by_tn.get(tn.casefold(), [])
            if lot["lot_id"] != preferred_id and group_by_lot_id.get(lot["lot_id"]) == group
            and supply[lot["lot_id"]] > 0
        ))
        if alternatives:
            extra_demands.append((item.id, remaining, alternatives))

    _, extra_detail = _allocate_received_across_shared_trackings(
        item_demands=extra_demands, supply_by_tn=supply,
    )
    for item_id, pairs in extra_detail.items():
        detail[item_id].extend(pairs)
    return detail


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


def _receipt_ids_on_item(item: Item | ReceivingMatch) -> set[str]:
    raw = (item.receipt_id or "").strip()
    if not raw:
        return set()
    return {p.strip() for p in raw.replace(";", ",").split(",") if p.strip()}


def _unapplied_receipt_rows(
    rows: list[_Receipt],
    scanned: list[tuple[Item, Order, list[str]]],
    *,
    identity: Callable[[_Receipt], tuple[str | None, list[str]]],
    quantity: Callable[[_Receipt], int],
    with_quantity: Callable[[_Receipt, int], _Receipt],
    description: Callable[[_Receipt], str | None],
    unique_receipt_ids: bool = False,
) -> list[_Receipt]:
    """Reserve applied units before matching the remaining warehouse supply."""
    by_id: dict[str | None, list[tuple[int, set[str]]]] = {}
    quantities = [quantity(row) for row in rows]
    available = list(quantities)
    for index, row in enumerate(rows):
        rid, trackings = identity(row)
        if available[index] > 0:
            by_id.setdefault(rid or None, []).append(
                (index, {tn.strip().casefold() for tn in trackings if tn})
            )

    for item, _order, trackings in scanned:
        if _is_zero_payout(item):
            continue
        saved_tracking = (item.receipt_tracking_number or "").strip().casefold()
        ids = _receipt_ids_on_item(item)
        if not ids and not saved_tracking:
            continue
        keys = {saved_tracking}
        if ids:
            keys.update(tn.casefold() for tn in trackings)
        upc = _item_matching_upc(item)
        candidates = [
            (index, saved_tracking in row_trackings)
            for rid in (ids or {None})
            for index, row_trackings in by_id.get(rid, ())
            if available[index] > 0
            and (keys & row_trackings or (unique_receipt_ids and rid and len(by_id[rid]) == 1))
        ]
        candidates.sort(key=lambda candidate: (
            bool(upc and upc == receipt_upc(rows[candidate[0]])),
            candidate[1],
            _name_similarity(item.description, description(rows[candidate[0]])),
            -candidate[0],
        ), reverse=True)
        remaining = item.quantity or 1
        for index, _is_saved_tracking in candidates:
            used = min(available[index], remaining)
            available[index] -= used
            remaining -= used
            if not remaining:
                break

    return [
        row if qty == original else with_quantity(row, qty)
        for row, original, qty in zip(rows, quantities, available) if qty > 0
    ]


def _exclude_claimed_receipt_updates(
    matches: list[ReceivingMatch],
    scanned: list[tuple[Item, Order, list[str]]],
) -> list[ReceivingMatch]:
    """Skip aggregate receipt updates that would attach another item's scan."""
    ids_by_item = {item.id: _receipt_ids_on_item(item) for item, _order, _trackings in scanned}
    claimed: dict[str, set[str]] = {}
    for item, _order, trackings in scanned:
        for tn in [*trackings, item.receipt_tracking_number or ""]:
            claimed.setdefault(tn.strip().casefold(), set()).update(ids_by_item[item.id])
    for match in matches:
        if not match.update_receipt_only and match.match_kind == MATCH_KIND_RECEIVED:
            claimed.setdefault(match.tracking_number.casefold(), set()).update(_receipt_ids_on_item(match))
    return [
        match for match in matches
        if not match.update_receipt_only
        or not (
            (_receipt_ids_on_item(match) - ids_by_item[match.item_id])
            & claimed.get(match.tracking_number.casefold(), set())
        )
    ]


def _index_available_lots(
    lots: list[dict[str, Any]], original_by_tn: dict[str, list[dict[str, Any]]]
) -> dict[str, list[dict[str, Any]]]:
    by_tn: dict[str, list[dict[str, Any]]] = {}
    for lot in lots:
        for key in (lot["tn_key"], *lot.get("alias_tns", [])):
            by_tn.setdefault(key, []).append(lot)
    for key, available in by_tn.items():
        if len(available) == 1 and len(original_by_tn[key]) > 1:
            # Confirm the product when other lots on the tracking were already applied.
            available[0]["require_product_match"] = True
    return by_tn


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


def _reward_needs_update(item: Item, meta: dict[str, Any], qty: int) -> bool:
    reward = _reward_from_meta(meta, qty)
    return reward is not None and item.reward != reward


def _reward_from_meta(meta: dict[str, Any], qty: int) -> Decimal | None:
    if "allocated_reward" in meta:
        return meta["allocated_reward"]
    unit_reward = meta.get("unit_reward")
    return (unit_reward * qty).quantize(Decimal("0.01")) if unit_reward is not None else None


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


def _tracking_reward_adjusted_price(item: Item, meta: dict[str, Any], qty: int) -> Decimal | None:
    if qty != item.quantity:
        return None
    reward = meta.get("allocated_reward")
    unit_payout = _unit_payout_from_meta(meta, allocated_qty=qty)
    if reward is None or reward <= 0 or unit_payout is None:
        return None
    saved_receipts = _receipt_ids_on_item(item)
    if saved_receipts and saved_receipts != set(meta["receipt_ids"]):
        return None
    base_total = (unit_payout * qty).quantize(Decimal("0.01"))
    price = unit_payout.quantize(Decimal("0.01"))
    if price * qty == base_total and _expected_amount(item) == base_total + reward:
        return price
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
    price_sold = _money(item.price_sold) or Decimal("0")

    lines: list[tuple[str, int, dict[str, Any]]] = []
    for tn_key, qty in tn_allocations:
        if qty <= 0:
            continue
        meta = meta_by_tn.get(tn_key) or {}
        receipt_id = _meta_receipt_id(meta)
        if update_receipt_only and not (
            meta.get("tracking_reward_adjusted_price") is not None or _receipt_needs_update(item, receipt_id)
            or _reward_needs_update(item, meta, qty) or _upc_needs_update(item, meta)
        ):
            continue
        display = (meta.get("display_tn") or "").strip() or _tn_display(trackings, tn_key)
        lines.append((display, qty, meta))
    for display_tn, qty, meta in extra_lines or []:
        if qty <= 0:
            continue
        receipt_id = _meta_receipt_id(meta)
        if update_receipt_only and not (_receipt_needs_update(item, receipt_id) or _reward_needs_update(item, meta, qty) or _upc_needs_update(item, meta)):
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
        tracking_reward_adjusted_price = meta.get("tracking_reward_adjusted_price")
        if tracking_reward_adjusted_price is not None:
            scanned_dt = None
        upc_only = (update_receipt_only and _upc_needs_update(item, meta)
                    and tracking_reward_adjusted_price is None
                    and not _receipt_needs_update(item, receipt_id)
                    and not _reward_needs_update(item, meta, qty))
        if upc_only:
            if "allocated_reward" not in meta:
                unit_payout = received_ext = None
            discrepancy = False
            scanned_dt = None
            receipt_id = item.receipt_id
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
                saved_scanned_at=_iso_date(item.scanned_at) if update_receipt_only else None,
                received_item=received_item,
                receipt_upc=(getattr(item, "receipt_upc", None) or meta.get("receipt_upc")),
                received_qty=qty,
                received_price=unit_payout,
                received_ext_total=received_ext,
                reward=None if upc_only else _reward_from_meta(meta, qty),
                confirmed_reward=meta.get("allocated_reward") if upc_only else None,
                tracking_reward_adjusted_price=tracking_reward_adjusted_price,
                received_status=_join_unique_strs(statuses),
                amount_discrepancy=discrepancy,
                selected=True,
                row_id=f"{item.id}:{MATCH_KIND_RECEIVED}:{tn_key}:{index}",
                match_kind=MATCH_KIND_RECEIVED,
                original_quantity=demand,
                split_keep_quantity=qty if more_after and not update_receipt_only else None,
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


def _upc_needs_update(item: Item, meta: dict[str, Any]) -> bool:
    return bool(meta.get("receipt_upc") and not getattr(item, "receipt_upc", None))


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
    lots_by_tn: dict[str, list[dict[str, Any]]] | None = None,
) -> list[ReceivingMatch]:
    """Review receipt changes and unambiguous missing UPCs on received items."""
    demand = item.quantity or 1
    if lots_by_tn and not getattr(item, "receipt_upc", None):
        assignment = _assign_items_to_lots(claimants=[(item, trackings)], lots_by_tn=lots_by_tn)
        lot_id = assignment.get(item.id)
        lot = next((lot for tn in trackings for lot in lots_by_tn.get(tn.casefold(), [])
                    if lot["lot_id"] == lot_id), None)
        if lot and lot.get("receipt_upc") and lot["qty"] >= demand:
            meta = _lot_as_match_meta(lot)
            if not _receipt_needs_update(item, _meta_receipt_id(meta)):
                return _matches_for_tn_allocations(
                    item=item, order=order, trackings=trackings,
                    tn_allocations=[(lot_id, demand)], meta_by_tn={lot_id: meta},
                    update_receipt_only=True,
                )
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
        if not (_receipt_needs_update(item, receipt_id) or _reward_needs_update(item, meta, demand) or _upc_needs_update(item, meta)):
            continue
        supply = int(supply_by_tn.get(key, 0) or 0)
        qty = min(remaining, supply if supply > 0 else 1)
        if qty <= 0:
            continue
        if qty < demand and _upc_needs_update(item, meta):
            meta_by_tn = dict(meta_by_tn)
            meta_by_tn[key] = {**meta, "receipt_upc": None}
            if not (_receipt_needs_update(item, receipt_id) or _reward_needs_update(item, meta, demand)):
                continue
        tn_allocations.append((key, qty))
        remaining -= qty

    extra_lines: list[tuple[str, int, dict[str, Any]]] = []
    if extra_meta and extra_qty > 0 and remaining > 0:
        receipt_id = _meta_receipt_id(extra_meta)
        if min(remaining, extra_qty) < demand:
            extra_meta = {**extra_meta, "receipt_upc": None}
        if _receipt_needs_update(item, receipt_id) or _upc_needs_update(item, extra_meta):
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


def _tracking_aliases_from_notes(notes: str | None) -> list[str]:
    """Extract alternate trackers stored on shipment notes (Walmart / warehouse)."""
    if not notes:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for line in notes.splitlines():
        stripped = line.strip()
        for prefix in _NOTE_TRACKING_PREFIXES:
            if stripped.startswith(prefix):
                value = stripped[len(prefix) :].strip()
                if not value:
                    break
                key = value.casefold()
                if key in seen:
                    break
                seen.add(key)
                out.append(value)
                break
    return out


def append_warehouse_tracking_note(item: Item, tracking_number: str | None) -> bool:
    """Append ``Warehouse tracking:`` to the item's first shipment when TN is new.

    Returns True when a note line was added. No-op when the TN is already on the
    shipment tracking field or notes.
    """
    tn = (tracking_number or "").strip()
    if not tn:
        return False
    note_line = f"{_WAREHOUSE_TRACKING_NOTE_PREFIX}{tn}"
    for si in item.shipment_items or []:
        shipment = si.shipment
        if shipment is None:
            continue
        known: set[str] = set()
        for existing in _split_tracking_field(shipment.tracking_number):
            known.add(existing.casefold())
        for existing in _tracking_aliases_from_notes(shipment.notes):
            known.add(existing.casefold())
        if tn.casefold() in known:
            return False
        base = (shipment.notes or "").rstrip()
        shipment.notes = f"{base}\n{note_line}" if base else note_line
        return True
    return False


def _is_carrier_like_tracking(tn: str | None) -> bool:
    """True for common carrier-shaped trackers (narrow — do not treat store LPNs as FedEx/USPS)."""
    t = (tn or "").strip().replace(" ", "").replace("-", "")
    if not t:
        return False
    if re.fullmatch(r"1Z[0-9A-Z]{16}", t, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"TBA\d{10,}", t, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"TBC\d{10,}", t, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"AMZN\d+[A-Z]?", t, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"9[0-4]\d{18,21}", t):
        return True
    if re.fullmatch(r"91\d+", t) and 20 <= len(t) <= 22:
        return True
    if re.fullmatch(r"96\d{20}", t) or re.fullmatch(r"96\d{22}", t):
        return True
    if re.fullmatch(r"100\d{31}", t):
        return True
    if re.fullmatch(r"C\d{14}", t):
        return True
    if re.fullmatch(r"1LS\d{8,}", t, flags=re.IGNORECASE):
        return True
    return False


def _is_soft_matchable_warehouse_tn(tn: str | None) -> bool:
    """Walmart-style opaque warehouse ids eligible for D&R name+price soft-match.

    Includes classic 555… trackers and long numeric store LPNs with any store
    prefix. Excludes recognizable carrier trackers.
    """
    t = (tn or "").strip().replace(" ", "").replace("-", "")
    if not t:
        return False
    if _is_carrier_like_tracking(t):
        return False
    if len(t) == 20 and t.startswith("555") and t.isdigit():
        return True
    if t.isdigit() and 12 <= len(t) <= 22:
        return True
    return False


def _is_walmart_order(order: Order) -> bool:
    store = getattr(order, "store", None)
    name = getattr(store, "name", None) if store is not None else None
    return "walmart" in (name or "").strip().casefold()


def _score_item_to_lot_soft(
    item: Item, lot: dict[str, Any]
) -> tuple[float, float] | None:
    """Stricter score for cross-TN soft-match: require price + name agreement."""
    local_price = _money(item.price_sold)
    lot_price = _money(lot.get("unit_price"))
    name_score = _name_similarity(item.description, lot.get("received_item"))
    if local_price is None or lot_price is None:
        return None
    delta = abs(local_price - lot_price)
    if delta > LOT_PRICE_TOLERANCE:
        return None
    if name_score < _LOT_NAME_FALLBACK_MIN:
        return None
    return (float(LOT_PRICE_TOLERANCE - delta), name_score)


def _soft_lot_product_key(lot: dict[str, Any]) -> tuple[str, str, str | None]:
    """Fungible soft-lot key: same unit price + product name."""
    price = str(_money(lot.get("unit_price")) if lot.get("unit_price") is not None else "")
    name = (lot.get("received_item") or "").strip().casefold()[:120]
    return (price, name, lot.get("receipt_upc"))


def _merge_fungible_orphan_lots(lots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse same-product / same-price orphan LPNs into one supply pool.

    D&R store barcodes are unique per package, so identical products arrive as
    many qty-1 lots. Without merging, soft-assign treats them as ambiguous ties
    and skips every claimant.
    """
    merged: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    order: list[tuple[str, str, str | None]] = []
    for lot in lots:
        key = _soft_lot_product_key(lot)
        existing = merged.get(key)
        display = (lot.get("display_tn") or "").strip() or lot.get("tn_key")
        source_tns = list(lot.get("source_tns") or [])
        if not source_tns and lot.get("tn_key"):
            source_tns = [lot["tn_key"]]
        if existing is None:
            soft = dict(lot)
            soft["lot_id"] = f"soft:{key[0]}:{abs(hash(key[1])) % 10**10}:{len(order)}"
            soft["qty"] = int(lot.get("qty") or 0)
            soft["receipt_ids"] = list(lot.get("receipt_ids") or [])
            soft["rows"] = list(lot.get("rows") or [])
            soft["source_tns"] = list(dict.fromkeys(source_tns))
            soft["alias_tns"] = list(lot.get("alias_tns") or [])
            if display:
                soft["display_tn"] = display
            merged[key] = soft
            order.append(key)
            continue
        existing["qty"] = int(existing.get("qty") or 0) + int(lot.get("qty") or 0)
        existing["receipt_ids"].extend(list(lot.get("receipt_ids") or []))
        existing["rows"].extend(lot.get("rows") or [])
        for tn in source_tns:
            if tn and tn not in existing["source_tns"]:
                existing["source_tns"].append(tn)
        for alias in lot.get("alias_tns") or []:
            if alias and alias not in existing["alias_tns"]:
                existing["alias_tns"].append(alias)
    return [merged[k] for k in order]


def _soft_assign_orphan_dnr_lots(
    *,
    claimants: list[tuple[Item, Order, list[str]]],
    lots: list[dict[str, Any]],
) -> dict[int, str]:
    """Map items → soft lots for orphan warehouse TNs (Walmart soft-match).

    Callers should pass lots already merged by product/price when LPNs differ.
    Within-item ties across fungible (same product key) lots pick one lot; ties
    across different products still skip. Multiple items may share one soft lot;
    scarce supply is split later by ``_allocate_received_across_shared_trackings``.
    """
    if not claimants or not lots:
        return {}

    lots_by_id = {lot["lot_id"]: lot for lot in lots}

    assignment: dict[int, str] = {}
    for item, _order, _trackings in claimants:
        ranked_for_item: list[tuple[tuple[float, float], str]] = []
        for lot in lots:
            score = _score_item_to_lot_soft(item, lot)
            if score is None:
                continue
            ranked_for_item.append((score, lot["lot_id"]))
        if not ranked_for_item:
            continue
        ranked_for_item.sort(key=lambda pair: (pair[0][0], pair[0][1]), reverse=True)
        best_score, best_lot_id = ranked_for_item[0]
        best_lot = lots_by_id.get(best_lot_id) or {}
        best_key = _soft_lot_product_key(best_lot)
        # Ignore near-ties that are the same fungible product pool.
        second_score = None
        for score, lot_id in ranked_for_item[1:]:
            other = lots_by_id.get(lot_id) or {}
            if _soft_lot_product_key(other) == best_key:
                continue
            second_score = score
            break
        if second_score is not None and _scores_ambiguous(best_score, second_score):
            continue
        assignment[item.id] = best_lot_id
    return assignment


def _tracking_numbers_for_item(db: Session, item: Item) -> list[str]:
    """All distinct tracking numbers linked to an item (not just the first).

    Includes Walmart / Warehouse tracking aliases stored on shipment notes.
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(raw: str | None) -> None:
        for tn in _split_tracking_field(raw):
            key = tn.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(tn)

    def _add_notes(notes: str | None) -> None:
        for tn in _tracking_aliases_from_notes(notes):
            key = tn.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(tn)

    for si in item.shipment_items or []:
        if si.shipment:
            _add(si.shipment.tracking_number)
            _add_notes(si.shipment.notes)

    if out:
        return out

    # Fallback when the relationship was empty/stale.
    rows = (
        db.query(Shipment.tracking_number, Shipment.notes)
        .join(ShipmentItem, ShipmentItem.shipment_id == Shipment.id)
        .filter(ShipmentItem.item_id == item.id)
        .all()
    )
    for tracking_raw, notes in rows:
        _add(tracking_raw)
        _add_notes(notes)
    return out


def _collect_items_with_trackings(
    db: Session,
    *,
    buying_group_id: int,
    statuses: tuple[ItemStatus, ...],
) -> tuple[list[Item], list[tuple[Item, Order, list[str]]], list[str]]:
    """Return (items, pending triples, unique trackings) for the given statuses.

    Each pending item is matched only against trackings linked to that item.
    Sibling / other order packages are never inherited — a warehouse receipt on
    TN1 must not match an item whose shipments are only TN2 (all frameworks).
    """
    items = (
        db.query(Item)
        .join(Order, Order.id == Item.order_id)
        .filter(Item.status.in_(statuses))
        .filter(Order.buying_group_id == buying_group_id)
        .filter(Order.status != "personal")
        .options(
            joinedload(Item.order).joinedload(Order.store),
            selectinload(Item.shipment_items).joinedload(ShipmentItem.shipment),
        )
        .order_by(Item.id)
        .all()
    )

    pending: list[tuple[Item, Order, list[str]]] = []
    all_trackings: list[str] = []
    seen_tn: set[str] = set()
    for item in items:
        order = item.order
        if not order:
            continue
        trackings = _tracking_numbers_for_item(db, item)
        if not trackings:
            continue
        if (
            order.store and (order.store.name or "").strip().casefold() == "walmart"
            and order.buying_group
            and (order.buying_group.api_framework or "").strip().casefold() == "buyinggroup"
        ):
            order_number = (order.store_order_number or "").strip()
            if order_number and order_number.casefold() not in {tn.casefold() for tn in trackings}:
                trackings.append(order_number)
        pending.append((item, order, trackings))
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
    rows = db.query(Shipment.tracking_number, Shipment.notes).all()
    out: set[str] = set()
    for tracking_raw, notes in rows:
        for tn in _split_tracking_field(tracking_raw):
            out.add(tn.casefold())
        for tn in _tracking_aliases_from_notes(notes):
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


def _buyinggroup_previous_snapshot(db: Session, group_id: int, identity: str) -> dict[str, Any] | None:
    cache = db.get(ReceivingReceiptCache, group_id)
    if cache is None or cache.provider_identity != identity:
        return None
    return cache.payload


def check_receiving_for_group(
    db: Session, *, buying_group_id: int, browser_access: BrowserAccess | None = None,
) -> CheckReceivingResult:
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
    matchers = {
        "parsefile": _check_receiving_parsefile, "buyinggroup": _check_receiving_buyinggroup,
        "bfmr": _check_receiving_bfmr, "usabg": _check_receiving_usabg,
        "dealandrunner": _check_receiving_dealandrunner,
    }
    if framework in matchers:
        identity = provider_identity(group)
        if browser_access:
            if not supports_browser_session(group) or browser_access.identity != identity:
                return CheckReceivingResult(buying_group_id, group_name,
                                            error="Buying group configuration changed. Check receiving again.")
        else:
            ready, reason = receiving_check_group_ready(group)
            if not ready:
                return CheckReceivingResult(buying_group_id, group_name, error=reason)
        try:
            browser_kwargs = {"browser_token": browser_access.token} if browser_access else {}
            previous = _buyinggroup_previous_snapshot(db, buying_group_id, identity) if framework == "buyinggroup" else None
            source_group = group
            if browser_access and buyinggroup_api.has_resumable_browser_reads():
                source_group = SimpleNamespace(api_framework=framework, base_url=group.base_url,
                                               api_username=None, api_password=None)
                # The read can spend several minutes waiting for the provider.
                db.rollback()
            source = fetch_receipt_source(source_group, usabg_details=(
                _load_usabg_po_detail_cache(db, buying_group_id) if framework == "usabg" else None),
                buyinggroup_previous=previous, **browser_kwargs)
        except buyinggroup_api.RateLimited as exc:
            return CheckReceivingResult(buying_group_id, group_name, error=str(exc), retry_after=exc.retry_after)
        except buyinggroup_api.BrowserSessionExpired as exc:
            return CheckReceivingResult(buying_group_id, group_name, error=str(exc), browser_sign_in_required=True)
        except Exception as exc:
            items, _, _ = _collect_pending(db, buying_group_id=buying_group_id)
            return CheckReceivingResult(buying_group_id, group_name, submitted_count=len(items),
                                        error=str(exc).strip() or exc.__class__.__name__)
        # Provider calls are finished. Hold the lock only for local matching/publication.
        group = (db.query(BuyingGroup).populate_existing().filter_by(id=buying_group_id)
                 .with_for_update(of=BuyingGroup).first())
        if group is None or provider_identity(group) != identity:
            return CheckReceivingResult(buying_group_id, group_name,
                                        error="Buying group configuration changed during the receipt check. Check again.")
        cache = db.query(ReceivingReceiptCache).populate_existing().filter_by(buying_group_id=buying_group_id).first()
        newer_cache = (cache is not None and cache.provider_identity == identity
                       and cache.payload.get("started_at", "") > source.payload["started_at"])
        if framework == "buyinggroup" and cache is not None and cache.provider_identity == identity and not newer_cache:
            prior_returns, _ = _cached_warehouse_returns(ReceiptSource(cache.payload), "returns")
            prior_returns = _buyinggroup_return_records(cache.payload, prior_returns)
            matched_ids = _matched_return_ids(db, prior_returns, group_id=buying_group_id)
            source.payload["matched_returns"] = [record for key, record in prior_returns.items() if key in matched_ids]
        inputs = cache.payload if newer_cache else source.payload
        result = matchers[framework](db, group=group, group_name=group.name, source=ReceiptSource(inputs))
        if not result.error and result.session_payload is not None:
            if newer_cache:
                result.session_payload["created_at"] = cache.fetched_at.isoformat()
                return result
            if framework == "usabg":
                fetched = ReceiptSource(inputs).request("rows", None)
                _persist_usabg_po_detail_cache(db, buying_group_id, fetched.newly_fetched_details,
                    receiving_status_by_po={r.po_number: r.receiving_status for r in fetched.rows})
            now = datetime.now(timezone.utc)
            if cache is not None and cache.provider_identity != identity:
                previous = db.get(ReceivingReviewPending, buying_group_id)
                if previous is not None:
                    db.delete(previous)
                    db.flush()
            if cache is None:
                cache = ReceivingReceiptCache(buying_group_id=buying_group_id)
                db.add(cache)
            cache.provider_identity = identity
            cache.payload = source.payload
            cache.fetched_at = now
            result.session_payload["created_at"] = now.isoformat()
            db.flush()
        return result
    return CheckReceivingResult(
        buying_group_id=buying_group_id,
        buying_group_name=group_name,
        error=(
            "Receiving check is only available for Parsefile, BuyingGroup.com, "
            "BFMR, USABG, or Deal & Runner groups"
        ),
    )


def _check_receiving_usabg(
    db: Session,
    *,
    group: BuyingGroup,
    group_name: str,
    source: ReceiptSource | None = None,
) -> CheckReceivingResult:
    source = source or ReceiptSource()
    buying_group_id = int(group.id)
    ready, reason = usabg_group_ready(group)
    if not source.cached and not ready:
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
        fetched = source.request("rows", usabg_api.list_received_po_rows,
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

    if fetched.newly_fetched_details and not source.cached:
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

    receipts = _receipts_since_first_order(db, fetched.rows, date_field="scanned_at")
    lots = _usabg_lots_from_receipts(receipts)
    lots_by_tn: dict[str, list[dict[str, Any]]] = {}
    supply_by_tn: dict[str, int] = {}
    for lot in lots:
        tn_key = lot["tn_key"]
        lots_by_tn.setdefault(tn_key, []).append(lot)
        supply_by_tn[tn_key] = supply_by_tn.get(tn_key, 0) + int(lot["qty"])
    # Receipt-only updates still key by tracking (PO number / receipt id).
    meta_by_tn = _meta_by_tn_from_lots(lots)

    unapplied = _unapplied_receipt_rows(
        receipts, scanned_pending,
        identity=lambda row: (row.receipt_id, [row.tracking_number]),
        quantity=lambda row: max(row.quantity or 1, 1),
        with_quantity=lambda row, qty: replace(row, quantity=qty),
        description=lambda row: row.received_item,
    )
    available_lots = _usabg_lots_from_receipts(unapplied)
    available_by_tn = _index_available_lots(available_lots, lots_by_tn)
    meta_by_lot = {lot["lot_id"]: _lot_as_match_meta(lot) for lot in available_lots}

    eligible: list[tuple[Item, Order, list[str]]] = []
    not_found = 0
    void_skipped = 0
    for item, order, trackings in pending:
        if _is_zero_payout(item):
            continue
        has_hit = any(tn.casefold() in available_by_tn for tn in trackings)
        if has_hit:
            eligible.append((item, order, trackings))
        else:
            not_found += 1

    lot_assignment = _assign_items_to_lots(
        claimants=[(item, trackings) for item, _o, trackings in eligible],
        lots_by_tn=available_by_tn,
    )

    # Allocate qty from each product lot pool (not the whole tracking pool).
    supply_by_lot = {lot["lot_id"]: int(lot["qty"]) for lot in available_lots}
    item_demands: list[tuple[int, int, list[str]]] = []
    for item, _order, _trackings in eligible:
        lot_id = lot_assignment.get(item.id)
        if not lot_id:
            continue
        item_demands.append((item.id, item.quantity or 1, [lot_id]))

    allocated, allocated_by_lot = _allocate_received_across_shared_trackings(
        item_demands=item_demands,
        supply_by_tn=supply_by_lot,
    )

    matches: list[ReceivingMatch] = []
    for item, order, trackings in eligible:
        if item.id not in lot_assignment:
            not_found += 1
            continue
        got = int(allocated.get(item.id, 0))
        if got <= 0:
            not_found += 1
            continue
        matches.extend(
            _matches_for_tn_allocations(
                item=item,
                order=order,
                trackings=trackings,
                tn_allocations=allocated_by_lot.get(item.id, []),
                meta_by_tn=meta_by_lot,
            )
        )

    for item, order, trackings in scanned_pending:
        if _is_zero_payout(item):
            continue
        matches.extend(
            _receipt_update_matches_for_item(
                item=item,
                order=order,
                trackings=trackings,
                meta_by_tn=meta_by_tn,
                supply_by_tn=supply_by_tn,
                lots_by_tn=lots_by_tn,
            )
        )

    matches = _exclude_claimed_receipt_updates(matches, scanned_pending)

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
                "tracking_numbers": [tn],
                "source_data": _receipt_details(row),
                "receipt_upc": receipt_upc(row),
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
            "No unscanned items with tracking numbers for this buying group, "
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


def _matched_return_ids(db: Session, ids, *, group_id: int | None = None) -> set[str]:
    if not ids:
        return set()
    query = db.query(Item.receiving_return_id).filter(Item.receiving_return_id.in_(list(ids)))
    if group_id is not None:
        query = query.join(Order).filter(Order.buying_group_id == group_id)
    return {return_id for (return_id,) in query}


def _cached_warehouse_returns(source: ReceiptSource, key: str) -> tuple[list[dict], bool]:
    # Older caches still support receipt review without a warehouse returns feed.
    if not source.cached or key not in source.payload["requests"]:
        return [], False
    try:
        return source.request(key, None), False
    except ValueError:
        return [], True


def _warehouse_return_review(db: Session, source: ReceiptSource, key: str) -> dict[str, Any]:
    items, error = _cached_warehouse_returns(source, key)
    applied = _matched_return_ids(db, [item["warehouse_id"] for item in items])
    review = {"damaged_items": [item for item in items if item["warehouse_id"] not in applied]}
    if error:
        review["damaged_items_error"] = True
    return review


def _buyinggroup_return_records(payload: dict, current: list[dict]) -> dict[str, dict]:
    return {record["warehouse_id"]: record
            for record in [*payload.get("matched_returns", []), *current]}


def _buyinggroup_receipt_product(receipt_id, product) -> tuple[str, str]:
    return (str(receipt_id or "").strip().casefold(), str(product or "").strip().casefold())


def _deduct_buyinggroup_returns(receipts: list[dict], matched_returns) -> None:
    returned = Counter(_buyinggroup_receipt_product(record.get("source_reference"), record.get("description"))
                       for record in matched_returns)
    unconfirmed = defaultdict(list)
    for receipt in receipts:
        if receipt["received_status"] == "Not confirmed":
            key = _buyinggroup_receipt_product(receipt["receipt_id"], receipt["description"])
            if all(key) and key in returned:
                unconfirmed[key].append(receipt)
    for key, lines in unconfirmed.items():
        # The feed identifies the product, but cannot distinguish duplicate lines for it.
        if len(lines) != 1:
            continue
        receipt = lines[0]
        quantity = receipt["quantity"]
        remaining = max(0, quantity - returned[key])
        receipt["quantity"] = remaining
        total = _money(receipt["received_ext_total"])
        if remaining and total is not None and total.is_finite():
            receipt["received_ext_total"] = str((total * remaining / quantity).quantize(Decimal("0.01")))


def match_receiving_return(db: Session, body: ReceivingDamageMatchRequest) -> dict[str, Any]:
    row = pending_row(db, body.buying_group_id)
    group = db.get(BuyingGroup, body.buying_group_id)
    cache = db.get(ReceivingReceiptCache, body.buying_group_id)
    framework = (group.api_framework or "").strip().casefold()
    identity = provider_identity(group)
    matcher = {"dealandrunner": _check_receiving_dealandrunner,
               "buyinggroup": _check_receiving_buyinggroup}.get(framework)
    if matcher is None or cache is None or cache.provider_identity != identity:
        raise HTTPException(409, "Refresh the warehouse returns for this buying group before matching.")
    apply_damage_match(db, row, body)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This warehouse unit could not be matched. Refresh Receiving and try again.")
    result = matcher(db, group=group, group_name=group.name, source=ReceiptSource(cache.payload))
    if result.error or result.session_payload is None:
        raise HTTPException(409, "Could not refresh the cached receiving details. Run Check receiving and try again.")
    payload = result.session_payload
    payload["created_at"] = cache.fetched_at.isoformat()
    payload["rematched_at"] = datetime.now(timezone.utc).isoformat()
    save_receiving_review_pending(db, payload, commit=False)
    saved = db.get(ReceivingReviewPending, body.buying_group_id)
    response = with_receipt_keys(saved.payload if saved is not None else payload)
    db.commit()
    return response


def _check_receiving_dealandrunner(
    db: Session,
    *,
    group: BuyingGroup,
    group_name: str,
    source: ReceiptSource | None = None,
) -> CheckReceivingResult:
    source = source or ReceiptSource()
    buying_group_id = int(group.id)
    ready, reason = dealandrunner_group_ready(group)
    if not source.cached and not ready:
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            error=reason or "Buying group Deal & Runner API is not configured",
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
        fetched = source.request("rows", dealandrunner_api.list_runner_transactions,
            base_url=base_url,
            email=username,
            password=password,
        )
    except Exception as exc:
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            submitted_count=len(unscanned_items),
            error=str(exc).strip() or exc.__class__.__name__,
        )

    receipts = _receipts_since_first_order(db, fetched.rows, date_field="timestamp")
    damage_review = _warehouse_return_review(db, source, "damaged")
    lots = _dnr_lots_from_receipts(receipts)
    lots_by_tn: dict[str, list[dict[str, Any]]] = {}
    supply_by_tn: dict[str, int] = {}
    for lot in lots:
        tn_key = lot["tn_key"]
        lots_by_tn.setdefault(tn_key, []).append(lot)
        supply_by_tn[tn_key] = supply_by_tn.get(tn_key, 0) + int(lot["qty"])
        for alias in lot.get("alias_tns") or []:
            # Alias TNs point at the same lots for matching, without double-counting supply.
            lots_by_tn.setdefault(alias, []).append(lot)
    meta_by_tn = _meta_by_tn_from_lots(lots)
    # Also expose alias TNs in meta for receipt-only updates.
    for alias, alias_lots in lots_by_tn.items():
        if alias not in meta_by_tn:
            meta_by_tn[alias] = _meta_by_tn_from_lots(
                [{**lot, "tn_key": alias} for lot in alias_lots]
            )[alias]
            supply_by_tn[alias] = sum(int(lot["qty"]) for lot in alias_lots)

    unapplied = _unapplied_receipt_rows(
        receipts, scanned_pending,
        identity=lambda row: (row.system_transaction_id, row.tracking_numbers or []),
        quantity=lambda row: int(row.entry_type_id == dealandrunner_api.RECEIVING_ENTRY_TYPE_ID),
        with_quantity=lambda row, _qty: row,
        description=lambda row: row.product_name,
        # A Walmart soft match can group transaction IDs from several warehouse trackings.
        unique_receipt_ids=True,
    )
    available_lots = _dnr_lots_from_receipts(unapplied)
    available_by_tn = _index_available_lots(available_lots, lots_by_tn)
    meta_by_lot = {lot["lot_id"]: _lot_as_match_meta(lot) for lot in available_lots}

    eligible: list[tuple[Item, Order, list[str]]] = []
    void_skipped = 0
    for item, order, trackings in pending:
        if _is_zero_payout(item):
            continue
        has_hit = any(tn.casefold() in available_by_tn for tn in trackings)
        if has_hit:
            eligible.append((item, order, trackings))

    allocated_by_lot = _allocate_dnr_lots(
        [(item, trackings) for item, _order, trackings in eligible],
        available_lots,
        available_by_tn,
    )

    matches: list[ReceivingMatch] = []
    lots_used_qty: dict[str, int] = {}
    available_by_id = {lot["lot_id"]: lot for lot in available_lots}
    matched_row_ids = set()

    def allocate_meta(pairs, lots_by_id, meta_by_id):
        item_meta = {}
        for lot_id, qty in pairs:
            used = lots_used_qty.get(lot_id, 0)
            rows = lots_by_id[lot_id]["rows"][used:used + qty]
            matched_row_ids.update(map(id, rows))
            item_meta[lot_id] = {**meta_by_id[lot_id], "receipt_ids": [
                row.system_transaction_id for row in rows if row.system_transaction_id
            ]}
            lots_used_qty[lot_id] = used + qty
        return item_meta

    for item, order, trackings in eligible:
        pairs = allocated_by_lot.get(item.id)
        if not pairs:
            continue
        matches.extend(
            _matches_for_tn_allocations(
                item=item,
                order=order,
                trackings=trackings,
                tn_allocations=pairs,
                meta_by_tn=allocate_meta(pairs, available_by_id, meta_by_lot),
            )
        )

    # Soft-match: orphan non-carrier warehouse TNs → unmatched Walmart items by
    # name+price (D&R store-LPN scans that never match submitted order ids).
    known_trackings = _known_shipment_trackings(db)
    received_item_ids = {
        m.item_id for m in matches if m.match_kind == MATCH_KIND_RECEIVED
    }
    pending_tn_keys = {
        tn.casefold()
        for _i, _o, tns in pending
        for tn in tns
        if (tn or "").strip()
    }
    unmatched_walmart: list[tuple[Item, Order, list[str]]] = []
    for item, order, trackings in pending:
        if _is_zero_payout(item):
            continue
        if item.id in received_item_ids:
            continue
        if not _is_walmart_order(order):
            continue
        unmatched_walmart.append((item, order, trackings))

    orphan_lots_for_soft: list[dict[str, Any]] = []
    for lot in available_lots:
        display_tn = (lot.get("display_tn") or "").strip()
        if not _is_soft_matchable_warehouse_tn(display_tn):
            continue
        tn_key = lot["tn_key"]
        if tn_key in known_trackings or tn_key in pending_tn_keys:
            continue
        used = lots_used_qty.get(lot["lot_id"], 0)
        remaining_qty = int(lot["qty"]) - used
        if remaining_qty <= 0:
            continue
        soft_lot = dict(lot)
        soft_lot["qty"] = remaining_qty
        soft_lot["rows"] = lot["rows"][used:]
        orphan_lots_for_soft.append(soft_lot)

    # Same product under different store LPNs → one pooled lot (avoids false ambiguity).
    orphan_lots_for_soft = _merge_fungible_orphan_lots(orphan_lots_for_soft)
    soft_lots_by_id = {lot["lot_id"]: lot for lot in orphan_lots_for_soft}

    soft_assignment = _soft_assign_orphan_dnr_lots(
        claimants=unmatched_walmart,
        lots=orphan_lots_for_soft,
    )
    soft_supply = {
        lot_id: int(lot["qty"]) for lot_id, lot in soft_lots_by_id.items()
    }
    soft_demands: list[tuple[int, int, list[str]]] = []
    soft_claimants_by_id = {
        item.id: (item, order, trackings)
        for item, order, trackings in unmatched_walmart
    }
    for item_id, lot_id in soft_assignment.items():
        item, _order, _trackings = soft_claimants_by_id[item_id]
        soft_demands.append((item_id, item.quantity or 1, [lot_id]))

    soft_allocated, soft_allocated_by_lot = _allocate_received_across_shared_trackings(
        item_demands=soft_demands,
        supply_by_tn=soft_supply,
    )
    soft_meta_by_lot = {
        lot_id: _lot_as_match_meta(lot) for lot_id, lot in soft_lots_by_id.items()
    }
    for item_id, pairs in soft_allocated_by_lot.items():
        got = int(soft_allocated.get(item_id, 0))
        if got <= 0:
            continue
        item, order, trackings = soft_claimants_by_id[item_id]
        matches.extend(
            _matches_for_tn_allocations(
                item=item,
                order=order,
                trackings=trackings,
                tn_allocations=pairs,
                meta_by_tn=allocate_meta(pairs, soft_lots_by_id, soft_meta_by_lot),
            )
        )
        received_item_ids.add(item_id)

    not_found = sum(
        1
        for item, _order, _trackings in pending
        if not _is_zero_payout(item) and item.id not in received_item_ids
    )

    for item, order, trackings in scanned_pending:
        if item.receiving_return_id or _is_zero_payout(item):
            continue
        matches.extend(
            _receipt_update_matches_for_item(
                item=item,
                order=order,
                trackings=trackings,
                meta_by_tn=meta_by_tn,
                supply_by_tn=supply_by_tn,
                lots_by_tn=lots_by_tn,
            )
        )

    matches = _exclude_claimed_receipt_updates(matches, scanned_pending)

    # Preserve receipt-ID corrections when each new ID identifies one available
    # unit. Aggregate updates must not absorb extra scans on a shared shipment.
    scanned_ids = {item.id: _receipt_ids_on_item(item) for item, _, _ in scanned_pending}
    available_rows_by_receipt = defaultdict(list)
    for row in unapplied:
        if row.system_transaction_id and id(row) not in matched_row_ids:
            available_rows_by_receipt[row.system_transaction_id].append(row)
    correction_claims = Counter(
        rid for match in matches if match.update_receipt_only
        for rid in _receipt_ids_on_item(match) - scanned_ids[match.item_id]
    )
    reviewed_matches = []
    for match in matches:
        if match.update_receipt_only:
            receipt_ids = _receipt_ids_on_item(match)
            new_ids = receipt_ids - scanned_ids[match.item_id]
            if new_ids:
                if len(receipt_ids) != match.quantity or any(
                    correction_claims[rid] != 1 or len(available_rows_by_receipt[rid]) != 1
                    for rid in new_ids
                ):
                    continue
                matched_row_ids.update(id(available_rows_by_receipt[rid][0]) for rid in new_ids)
        reviewed_matches.append(match)
    matches = reviewed_matches

    unknown_receipts: list[dict[str, Any]] = []
    accounted_receipt_keys = set()
    unapplied_row_ids = {id(row) for row in unapplied}
    for row in receipts:
        if row.entry_type_id != dealandrunner_api.RECEIVING_ENTRY_TYPE_ID:
            continue
        tns = [tn.strip() for tn in (row.tracking_numbers or []) if (tn or "").strip()]
        if not tns:
            continue
        scanned_dt = _parse_created_date(row.timestamp)
        receipt = {
            "receipt_id": row.system_transaction_id,
            "tracking_number": tns[0],
            "tracking_numbers": tns,
            "source_data": _receipt_details(row),
            "receipt_upc": receipt_upc(row),
            "bg_order_id": None,
            "description": row.product_name,
            "quantity": 1,
            "received_price": str(row.amount) if row.amount is not None else None,
            "received_ext_total": str(row.amount) if row.amount is not None else None,
            "received_status": None,
            "note": None,
            "created_dt": row.timestamp,
            "scanned_at": _iso_date(scanned_dt),
            "delivery": None,
        }
        if id(row) not in unapplied_row_ids or id(row) in matched_row_ids:
            accounted_receipt_keys.add(receipt_key(receipt))
        else:
            unknown_receipts.append(receipt)

    discrepancy_count = sum(1 for m in matches if m.amount_discrepancy)
    received_count = _received_match_count(matches)
    split_off = _split_off_count(matches)
    checked_summary = (
        f"Checked {len(pending)} unscanned item(s) / {len(all_trackings)} tracking number(s) "
        f"against Deal & Runner ({len(receipts)} transaction line(s) / "
        f"{fetched.total_records or len(receipts)} total; {len(fetched.request_urls)} page(s))."
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
        parts.append(
            f"{len(unknown_receipts)} unresolved D&R receiving transaction(s)"
        )
    if damage_review["damaged_items"]:
        parts.append(f'{len(damage_review["damaged_items"])} damaged warehouse unit(s) to review')
    if damage_review.get("damaged_items_error"):
        parts.append("Could not refresh Deal & Runner damaged inventory")
    if not parts:
        bits = [checked_summary]
        if not_found:
            bits.append(f"{not_found} local item(s) not found as received.")
        message = " ".join(bits) + " Nothing to review."
    else:
        message = ". ".join(parts) + f". {checked_summary}"

    if not pending and not parts and not matches:
        message = (
            "No unscanned items with tracking numbers for this buying group, "
            "and no unknown Deal & Runner transactions."
            if not unscanned_items
            else f"{len(unscanned_items)} unscanned item(s) but none have tracking numbers, "
            "and no unknown Deal & Runner transactions."
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
            "_accounted_dnr_receipt_keys": sorted(accounted_receipt_keys),
            "matches": [],
            **damage_review,
        }
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            session_payload=payload,
            submitted_count=len(unscanned_items),
            message=message,
        )

    raw_dump = {
        "page_count": fetched.page_count,
        "total_records": fetched.total_records,
        "owner_id": fetched.owner_id,
        "request_urls": fetched.request_urls[:50],
        "received_rows": [
            {
                "tracking_numbers": r.tracking_numbers,
                "amount": str(r.amount) if r.amount is not None else None,
                "product_name": r.product_name,
                "system_transaction_id": r.system_transaction_id,
                "timestamp": r.timestamp,
                "entry_type_id": r.entry_type_id,
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
        "_accounted_dnr_receipt_keys": sorted(accounted_receipt_keys),
        "matches": [match_to_dict(m) for m in matches],
        **damage_review,
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
    source: ReceiptSource | None = None,
) -> CheckReceivingResult:
    source = source or ReceiptSource()
    buying_group_id = int(group.id)
    ready, reason = parsefile_group_ready(group)
    if not source.cached and not ready:
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            error=reason or f"{group_name} API is not configured",
        )

    unscanned_items, pending, all_trackings = _collect_pending(db, buying_group_id=buying_group_id)
    scanned_pending = _collect_scanned_or_later(db, buying_group_id=buying_group_id)
    all_trackings = _merge_tracking_lists(
        all_trackings, [tn for _i, _o, tns in scanned_pending for tn in tns]
    )

    base_url = str(group.base_url)
    bearer_token = str(group.bearer_token)
    api_user_id = int(group.api_user_id or 0)
    api_email = str(group.api_email)

    try:
        api_result = source.request("rows", parsefile_api.list_all_received_items,
            base_url=base_url,
            bearer_token=bearer_token,
            user_id=api_user_id,
            email=api_email,
            portal_password=group.api_password,
        )
    except Exception as exc:
        return CheckReceivingResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            submitted_count=len(unscanned_items),
            error=str(exc).strip() or exc.__class__.__name__,
        )

    received_rows = _receipts_since_first_order(db, api_result.rows, date_field="created_date")
    raw_csv = api_result.raw_csv

    by_tracking = _index_received_by_tracking(received_rows)

    def pools(rows: list[parsefile_api.ParsefileReceivedItem]):
        supply_by_tn: dict[str, int] = {}
        meta_by_tn: dict[str, dict[str, Any]] = {}
        for row in rows:
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
            absorb_upc(meta, receipt_upc(row))
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
        return supply_by_tn, meta_by_tn

    unapplied = _unapplied_receipt_rows(
        received_rows, scanned_pending,
        identity=lambda row: (row.receipt_id, row.tracking_numbers),
        quantity=lambda row: 0 if row.void else (row.qty if row.qty is not None else 1),
        with_quantity=lambda row, qty: replace(
            row, qty=qty, ext_total=row.ext_total * qty / row.qty if row.ext_total is not None else None
        ),
        description=lambda row: row.item,
    )
    supply_by_tn, meta_by_tn = pools(unapplied)

    eligible: list[tuple[Item, Order, list[str]]] = []
    void_matched_ids: set[int] = set()
    void_skipped = 0
    not_found = 0
    for item, order, trackings in pending:
        if _is_zero_payout(item):
            continue
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

    supply_by_tn, meta_by_tn = pools(received_rows)
    for item, order, trackings in scanned_pending:
        if _is_zero_payout(item):
            continue
        matches.extend(
            _receipt_update_matches_for_item(
                item=item,
                order=order,
                trackings=trackings,
                meta_by_tn=meta_by_tn,
                supply_by_tn=supply_by_tn,
            )
        )

    matches = _exclude_claimed_receipt_updates(matches, scanned_pending)

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
                "tracking_numbers": row.tracking_numbers,
                "source_data": _receipt_details(row),
                "receipt_upc": receipt_upc(row),
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
        f"against {group_name} ({len(received_rows)} received row(s))."
    )
    if api_result.upc_warning:
        checked_summary += f" {api_result.upc_warning}"

    matched_ids = {m.item_id for m in matches}
    unmatched = _build_unmatched(pending, matched_ids, void_matched_ids=void_matched_ids)

    if not pending and not unknown_receipts and not matches:
        message = (
            "No unscanned items with tracking numbers for this buying group, "
            f"and no unknown {group_name} receipts."
            if not unscanned_items
            else f"{len(unscanned_items)} unscanned item(s) but none have tracking numbers, "
            f"and no unknown {group_name} receipts."
        )
        if api_result.upc_warning:
            message += f" {api_result.upc_warning}"
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
            f"{len(unknown_receipts)} {group_name} receipt(s) with tracking not in Order Manager"
        )
    if not matches and not unknown_receipts:
        bits = [checked_summary]
        if not_found:
            bits.append(f"{not_found} local item(s) not found as received.")
        if void_skipped:
            bits.append(f"{void_skipped} voided at {group_name}.")
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
    source: ReceiptSource | None = None,
) -> CheckReceivingResult:
    source = source or ReceiptSource()
    buying_group_id = int(group.id)
    ready, reason = buyinggroup_group_ready(group)
    if not source.cached and not ready:
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
        receipts = source.request("rows", buyinggroup_api.list_receipts,
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

    returns, return_error = _cached_warehouse_returns(source, "returns")
    open_returns = [record for record in returns if record.get("is_open") is not False]
    applied = _matched_return_ids(db, [record["warehouse_id"] for record in open_returns])
    return_review = {"damaged_items": [record for record in open_returns if record["warehouse_id"] not in applied],
                     "buyinggroup_returns": True}
    if return_error:
        return_review["damaged_items_error"] = True
    return_records = _buyinggroup_return_records(source.payload, returns)
    matched_return_ids = _matched_return_ids(db, return_records, group_id=buying_group_id)

    tracking_rewards: dict[str, Decimal] = {}
    reward_error = None
    if receipts:
        try:
            tracking_rewards = source.request("rewards", buyinggroup_api.list_tracking_rewards,
                base_url=base_url, username=username, password=password,
            )
        except Exception as exc:
            # Receiving can still be reviewed; never replace a saved reward with zero.
            reward_error = str(exc).strip() or exc.__class__.__name__
    from app.tracking_rewards import receiving_credits, save_credits, saved_credit_values
    from app.bg_reward_math import reward_share
    try:
        credits = receiving_credits(receipts, tracking_rewards)
    except ValueError as exc:
        # Conflicting reward identities do not make product receiving unusable.
        reward_error = str(exc)
        credits = {}
    observed_at = datetime.fromisoformat(source.payload["started_at"]) if source.payload.get("started_at") else None
    credits = saved_credit_values(credits, save_credits(db, group, credits, observed_at=observed_at))
    local_tracking_keys = {tn.casefold() for tn in all_trackings}

    def receipt_tracking(row: buyinggroup_api.BuyinggroupReceipt) -> str:
        # Keep each receipt's quantity under one key even when both identifiers match.
        numbers = [tn.strip() for tn in (row.tracking_id, row.alt_tracking_id) if tn and tn.strip()]
        return next((tn for tn in numbers if tn.casefold() in local_tracking_keys),
                    numbers[0] if numbers else (row.receipt_id or "").strip())

    # Never use the receipt header quantity: it includes unconfirmed Task rows.
    # Fetch concurrently, once per receipt, while retaining list order for allocation.
    detail_rows = [r for r in receipts if not r.void and (r.quantity or 0) > 0]
    def fetch_details(row):
        try:
            detail = source.request("detail:" + str(row.raw.get("key") or ""), buyinggroup_api.get_receipt_details,
                base_url=base_url, username=username, password=password,
                receipt_key=str(row.raw.get("key") or ""),
            )
            if detail.get("receipt_id") != row.receipt_id:
                raise ValueError("Receipt identity changed")
            for line in detail["received_items"]:
                qty = line.get("qty")
                if (not isinstance(line.get("verified"), bool)
                    or not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0
                    or (line["verified"] and (_money(line.get("price")) is None
                                             or _money(line.get("total")) is None))):
                    raise ValueError("Invalid receipt product details")
                if line["verified"] and (not _money(line["price"]).is_finite()
                                         or not _money(line["total"]).is_finite()):
                    raise ValueError("Invalid product amount")
            return detail
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        details = dict(zip((id(r) for r in detail_rows), pool.map(fetch_details, detail_rows)))

    # Cache details before the local order cutoff, which may change on a later visit.
    receipts = _receipts_since_first_order(db, receipts, date_field="created_dt")
    by_tracking = _index_bg_receipts_by_tracking(receipts)
    detail_rows = [r for r in receipts if not r.void and (r.quantity or 0) > 0]
    lots = []
    unknown_receipts = []
    detail_failures = 0
    for row in detail_rows:
        tn = receipt_tracking(row)
        detail = details[id(row)]
        if detail is None:
            detail_failures += 1
        lines = detail["received_items"] if detail is not None else [None]
        for index, line in enumerate(lines):
            confirmed = line is not None and line["verified"]
            qty = line["qty"] if line else None
            upc = (str(line.get("upc") or "").strip() or None) if line else None
            if upc and len(upc) > 64:
                upc = None
            unit = _money(line.get("price")) if line else None
            # BG's total is the authoritative product payout (commission included).
            total = _money(line.get("total")) if line else None
            if confirmed:
                unit = total / Decimal(qty)
                lots.append({
                    "lot_id": f"{row.raw.get('key')}:{index}".casefold(), "tn_key": tn.casefold(),
                    "display_tn": tn, "qty": qty, "unit_price": unit,
                    "received_item": line.get("item_id"), "receipt_upc": upc,
                    "receipt_ids": [row.receipt_id], "statuses": ["Confirmed"],
                    "scanned_at": _parse_created_date(row.created_dt),
                    "require_product_match": True,
                })
            unknown_receipts.append({
                "receipt_id": row.receipt_id, "tracking_number": tn,
                "tracking_numbers": list(dict.fromkeys(t.strip() for t in
                    (row.tracking_id, row.alt_tracking_id) if t and t.strip())) or ([tn] if tn else []),
                "description": line.get("item_id") if line else row.order_id,
                "receipt_upc": upc, "quantity": qty,
                "received_price": str(unit) if unit is not None else None,
                "received_ext_total": str(total) if total is not None else None,
                "received_status": "Confirmed" if confirmed else
                    "Not confirmed" if line else "Receipt details unavailable",
                "linkable": confirmed, "bg_order_id": row.order_id,
                "_lot_id": f"{row.raw.get('key')}:{index}".casefold() if confirmed else None,
                "source_data": {"receipt": row.raw, "item": line},
                "note": row.note, "created_dt": row.created_dt,
                "scanned_at": _iso_date(_parse_created_date(row.created_dt)),
                "delivery": row.delivery,
            })

    # The reward belongs to the credited receipt, shared by its confirmed units.
    confirmed_qty = {}
    for lot in lots:
        key = lot["receipt_ids"][0]
        confirmed_qty[key] = confirmed_qty.get(key, 0) + lot["qty"]
    reward_by_receipt = {}
    for rid, credit in credits.items():
        reward_by_receipt[rid] = Decimal(credit["amount"]) if credit["receipt_id"] == rid else Decimal("0.00")
    lots_by_tn = {}
    lots_by_id = {lot["lot_id"]: lot for lot in lots}
    for lot in lots:
        lots_by_tn.setdefault(lot["tn_key"], []).append(lot)
    # Alias identifiers refer to the same supply, including Walmart order numbers.
    for row in detail_rows:
        primary = receipt_tracking(row).casefold()
        for alias in (row.tracking_id, row.alt_tracking_id):
            key = (alias or "").strip().casefold()
            if key and key != primary:
                for lot in lots_by_tn.get(primary, []):
                    if lot not in lots_by_tn.setdefault(key, []):
                        lots_by_tn[key].append(lot)
    meta_by_lot = {lid: _lot_as_match_meta(lot) for lid, lot in lots_by_id.items()}
    supply = {lid: lot["qty"] for lid, lot in lots_by_id.items()}

    # Include unconfirmed rows and unavailable details when checking for a lone line.
    receipt_lines_by_tn = {}
    for receipt in unknown_receipts:
        for key in {tn.casefold() for tn in receipt["tracking_numbers"]}:
            receipt_lines_by_tn.setdefault(key, []).append(receipt["_lot_id"])
    # Weak name matches must never compete with another local item's claim.
    claimant_count_by_lot = Counter(
        lid for item, _order, trackings in pending + scanned_pending
        if not _is_zero_payout(item)
        for lid in {
            lot["lot_id"] for tn in trackings for lot in lots_by_tn.get(tn.casefold(), [])
        }
    )

    def choices(item, trackings, received=False):
        candidates = []
        seen = set()
        shipment_lot_ids = set()
        single_line_lot_ids = set()
        order = item.order
        walmart_order_number = (
            (order.store_order_number or "").strip().casefold()
            if order.store and (order.store.name or "").strip().casefold() == "walmart"
            else ""
        )
        for tn in trackings:
            key = tn.casefold()
            for lot in lots_by_tn.get(key, []):
                lid = lot["lot_id"]
                if key != walmart_order_number:
                    shipment_lot_ids.add(lid)
                    if receipt_lines_by_tn.get(key) == [lid] and claimant_count_by_lot[lid] == 1:
                        single_line_lot_ids.add(lid)
                if lid not in seen:
                    seen.add(lid)
                    candidates.append(lot)
        if received:
            saved = (item.receipt_tracking_number or "").casefold()
            exact = [lot for lot in candidates if
                     _receipt_ids_on_item(item) & set(lot["receipt_ids"])
                     and (not saved or saved == lot["tn_key"])]
            if exact:
                candidates = exact
        ranked = []
        for lot in candidates:
            single_line = len(shipment_lot_ids) == 1 and lot["lot_id"] in single_line_lot_ids
            score = _score_bg_item_to_lot(item, lot, single_line=single_line)
            if score is not None:
                ranked.append((score, lot))
        if not ranked:
            return []
        # The Walmart order number spans packages/products. Use it only when
        # no eligible product is found through a shipment tracking identifier.
        shipment_ranked = [(score, lot) for score, lot in ranked
                           if lot["lot_id"] in shipment_lot_ids]
        if shipment_ranked:
            ranked = shipment_ranked
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        best_score, best = ranked[0]
        # Identical products across receipts share demand; different products
        # with indistinguishable scores must be reviewed instead of guessed.
        same_product = lambda lot: lot.get("receipt_upc") or lot.get("received_item")
        if any(same_product(lot) != same_product(best) and _scores_ambiguous(best_score, score)
               for score, lot in ranked[1:]):
            return []
        return [lot["lot_id"] for _score, lot in ranked if same_product(lot) == same_product(best)]

    received_demands = [(item.id, item.quantity or 1, choices(item, tns, True))
                        for item, _o, tns in scanned_pending
                        if not _is_zero_payout(item) and (not item.receiving_return_id or item.receipt_id)]
    _reserved, reserved = _allocate_received_across_shared_trackings(
        item_demands=received_demands, supply_by_tn=supply)
    available = dict(supply)
    for allocations in reserved.values():
        for lid, qty in allocations:
            available[lid] -= qty
    demands = [(item.id, item.quantity or 1, choices(item, tns))
               for item, _o, tns in pending if not _is_zero_payout(item)]
    allocated, allocations = _allocate_received_across_shared_trackings(
        item_demands=demands, supply_by_tn=available)
    # Cumulative rounding preserves each tracking's exact reward across splits.
    reward_allocations = {}
    reward_qty_used = {}
    for batch in (reserved, allocations):
        for item_id, item_allocations in batch.items():
            for lid, qty in item_allocations:
                key = lots_by_id[lid]["receipt_ids"][0]
                if key in reward_by_receipt:
                    before = reward_qty_used.get(key, 0)
                    reward_allocations[item_id, lid] = reward_share(reward_by_receipt[key], before, qty, confirmed_qty[key])
                    reward_qty_used[key] = before + qty
    remaining_supply = dict(available)
    for item_allocations in allocations.values():
        for lid, qty in item_allocations:
            remaining_supply[lid] -= qty
    _deduct_buyinggroup_returns(unknown_receipts, (return_records[key] for key in matched_return_ids))
    unresolved = []
    for receipt in unknown_receipts:
        lid = receipt.pop("_lot_id")
        if receipt["quantity"] == 0:
            continue
        if lid is not None:
            qty = remaining_supply[lid]
            if qty <= 0:
                continue
            receipt["quantity"] = qty
            receipt["received_ext_total"] = str((lots_by_id[lid]["unit_price"] * qty).quantize(Decimal("0.01")))
            key = lots_by_id[lid]["receipt_ids"][0]
            if key in reward_by_receipt:
                before = reward_qty_used.get(key, 0)
                receipt["reward"] = str(reward_share(reward_by_receipt[key], before, qty, confirmed_qty[key]))
                reward_qty_used[key] = before + qty
        unresolved.append(receipt)
    unknown_receipts = unresolved
    matches = []
    not_found = 0
    void_matched_ids = set()
    void_skipped = 0
    for item, order, trackings in pending + scanned_pending:
        if item.receiving_return_id or _is_zero_payout(item):
            continue
        received = item.status not in _UNSCANNED_STATUSES
        item_allocations = (reserved if received else allocations).get(item.id, [])
        if not item_allocations and not received:
            rows = [r for tn in trackings for r in by_tracking.get(tn.casefold(), [])]
            if rows and all(r.void for r in rows):
                void_matched_ids.add(item.id)
                void_skipped += 1
            else:
                not_found += 1
        item_meta = {lid: dict(meta_by_lot[lid]) for lid, _qty in item_allocations}
        for lid, qty in item_allocations:
            if (item.id, lid) in reward_allocations:
                item_meta[lid]["allocated_reward"] = reward_allocations[item.id, lid]
        if received and len(item_allocations) == 1:
            lid, qty = item_allocations[0]
            item_meta[lid]["tracking_reward_adjusted_price"] = _tracking_reward_adjusted_price(item, item_meta[lid], qty)
        matches.extend(_matches_for_tn_allocations(
            item=item, order=order, trackings=trackings,
            tn_allocations=item_allocations, meta_by_tn=item_meta,
            update_receipt_only=received,
        ))
    analytics_rows = []
    analytics_error = (f"{detail_failures} receipt detail request(s) unavailable; no summary quantities used"
                       if detail_failures else None)

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
        parts.append(f"{len(unknown_receipts)} unresolved BG receipt item(s)")
    if analytics_error:
        parts.append(f"Analytics enrich failed: {analytics_error}")
    if return_review["damaged_items"]:
        parts.append(f'{len(return_review["damaged_items"])} open BuyingGroup.com return(s) to match')
    if return_review.get("damaged_items_error"):
        parts.append("Could not refresh BuyingGroup.com returns")
    if not matches and not unknown_receipts and not return_review["damaged_items"] and not return_review.get("damaged_items_error"):
        bits = [checked_summary]
        if not_found:
            bits.append(f"{not_found} local item(s) not found as received.")
        if void_skipped:
            bits.append(f"{void_skipped} voided at BuyingGroup.")
        message = " ".join(bits) + " Nothing to review."
    else:
        message = ". ".join(parts) + f". {checked_summary}"
    if reward_error:
        message += f" Tracking rewards unavailable: {reward_error}."

    # Even with no pending locals, still open review when there are unknown receipts.
    if not pending and not unknown_receipts and not matches and not return_review["damaged_items"] and not return_review.get("damaged_items_error"):
        message = (
            "No unscanned items with tracking numbers for this buying group, "
            "and no unknown BG receipts."
            if not unscanned_items
            else f"{len(unscanned_items)} unscanned item(s) but none have tracking numbers, "
            "and no unknown BG receipts."
        )
        if reward_error:
            message += f" Tracking rewards unavailable: {reward_error}."
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
            **return_review,
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
        **return_review,
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
    source: ReceiptSource | None = None,
) -> CheckReceivingResult:
    source = source or ReceiptSource()
    buying_group_id = int(group.id)
    ready, reason = bfmr_group_ready(group)
    if not source.cached and not ready:
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
        fetched = source.request("rows", bfmr_api.list_received_tracker_rows,
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

    receipts = _receipts_since_first_order(db, fetched.rows, date_field="scanned_at")
    by_tracking = _index_bfmr_by_tracking(receipts)
    all_pending_trackings = {
        tn.casefold()
        for _i, _o, tns in (*pending, *scanned_pending)
        for tn in tns
    }

    def _row_qty(row: bfmr_api.BfmrTrackerRow) -> int:
        qty = row.qty_received if row.qty_received is not None else row.qty
        return int(qty) if qty is not None and int(qty) > 0 else 1

    def _absorb_meta(meta: dict[str, Any], row: bfmr_api.BfmrTrackerRow) -> None:
        absorb_upc(meta, receipt_upc(row))
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

    def pools(rows: list[bfmr_api.BfmrTrackerRow]):
        supply_by_tn: dict[str, int] = {}
        supply_by_order: dict[str, int] = {}
        meta_by_tn: dict[str, dict[str, Any]] = {}
        meta_by_order: dict[str, dict[str, Any]] = {}

        for row in rows:
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
        return supply_by_tn, supply_by_order, meta_by_tn, meta_by_order

    def receipt_identity(row: bfmr_api.BfmrTrackerRow) -> tuple[str | None, list[str]]:
        tn = (row.tracking_number or "").strip()
        order_number = (row.order_id or "").strip()
        keys = [tn] if tn else []
        if order_number and tn.casefold() not in all_pending_trackings:
            keys.append(order_number)
        return _bfmr_receipt_id(row), keys

    scanned_with_orders = [
        (item, order, [*trackings, order.store_order_number] if order.store_order_number else trackings)
        for item, order, trackings in scanned_pending
    ]
    unapplied = _unapplied_receipt_rows(
        receipts, scanned_with_orders, identity=receipt_identity,
        quantity=_row_qty,
        with_quantity=lambda row, qty: replace(row, qty_received=qty),
        description=lambda row: row.item_name or row.deal_title,
    )
    supply_by_tn, supply_by_order, meta_by_tn, meta_by_order = pools(unapplied)

    eligible: list[tuple[Item, Order, list[str]]] = []
    not_found = 0
    void_skipped = 0
    for item, order, trackings in pending:
        if _is_zero_payout(item):
            continue
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

    supply_by_tn, supply_by_order, meta_by_tn, meta_by_order = pools(receipts)
    for item, order, trackings in scanned_pending:
        if _is_zero_payout(item):
            continue
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

    matches = _exclude_claimed_receipt_updates(matches, scanned_with_orders)

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
                "tracking_numbers": [tn],
                "source_data": _receipt_details(row),
                "receipt_upc": receipt_upc(row),
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
            "No unscanned items with tracking numbers for this buying group, "
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
