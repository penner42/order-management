"""Submit item tracking numbers to buying-group APIs (Parsefile, USABG, BuyingGroup.com, Deal & Runner, BFMR)."""
from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from sqlalchemy.orm import Session, joinedload, selectinload

from app.submission_policy import require_group_submissions_enabled
from app.models import BuyingGroup, Item, Order, ShipmentItem
from app.models.item import ItemStatus
from app.utils.buying_group_apis import bfmr as bfmr_api
from app.utils.buying_group_apis import buyinggroup as buyinggroup_api
from app.utils.buying_group_apis import dealandrunner as dealandrunner_api
from app.utils.buying_group_apis import parsefile as parsefile_api
from app.utils.buying_group_apis import usabg as usabg_api
from app.utils.dates import to_date_only

logger = logging.getLogger(__name__)


def parsefile_group_ready(group: BuyingGroup | None) -> tuple[bool, str | None]:
    """Return (ready, error_detail) for Parsefile tracking submission."""
    if group is None:
        return False, "Order has no buying group"
    framework = (group.api_framework or "").strip().casefold()
    if framework != "parsefile":
        return False, "Buying group API framework is not configured for submission"
    if not (group.bearer_token or "").strip():
        return False, "Buying group has no API token"
    if not (group.base_url or "").strip():
        return False, "Buying group has no base URL"
    # Blank api_url falls back to the Parsefile framework default.
    if group.api_user_id is None:
        return False, "Buying group is missing API user id"
    if not (group.api_email or "").strip():
        return False, "Buying group is missing API email"
    return True, None


def usabg_group_ready(group: BuyingGroup | None) -> tuple[bool, str | None]:
    """Return (ready, error_detail) for USABG tracking submission."""
    if group is None:
        return False, "Order has no buying group"
    framework = (group.api_framework or "").strip().casefold()
    if framework != "usabg":
        return False, "Buying group API framework is not configured for submission"
    if not (group.api_username or "").strip():
        return False, "Buying group is missing API username"
    if not (group.api_password or "").strip():
        return False, "Buying group is missing API password"
    # Blank base_url falls back to the USABG default.
    return True, None


def buyinggroup_group_ready(group: BuyingGroup | None) -> tuple[bool, str | None]:
    """Return (ready, error_detail) for BuyingGroup.com tracking submission."""
    if group is None:
        return False, "Order has no buying group"
    framework = (group.api_framework or "").strip().casefold()
    if framework != "buyinggroup":
        return False, "Buying group API framework is not configured for submission"
    if not (group.api_username or "").strip():
        return False, "Buying group is missing API username"
    if not (group.api_password or "").strip():
        return False, "Buying group is missing API password"
    # Blank base_url / api_url fall back to BuyingGroup.com defaults.
    return True, None


def dealandrunner_group_ready(group: BuyingGroup | None) -> tuple[bool, str | None]:
    """Return (ready, error_detail) for Deal & Runner tracking submission."""
    if group is None:
        return False, "Order has no buying group"
    framework = (group.api_framework or "").strip().casefold()
    if framework != "dealandrunner":
        return False, "Buying group API framework is not configured for submission"
    if not (group.api_username or "").strip():
        return False, "Buying group is missing API email"
    if not (group.api_password or "").strip():
        return False, "Buying group is missing API password"
    # Blank base_url falls back to the Deal & Runner default.
    return True, None


def bfmr_group_ready(group: BuyingGroup | None) -> tuple[bool, str | None]:
    """Return (ready, error_detail) for BFMR receiving + tracking submit (API key + secret)."""
    if group is None:
        return False, "Order has no buying group"
    framework = (group.api_framework or "").strip().casefold()
    if framework != "bfmr":
        return False, "Buying group API framework is not configured for BFMR"
    if not (group.api_username or "").strip():
        return False, "Buying group is missing BFMR API key"
    if not (group.api_password or "").strip():
        return False, "Buying group is missing BFMR API secret"
    # Blank base_url falls back to https://api.bfmr.com.
    return True, None


def group_ready(group: BuyingGroup | None) -> tuple[bool, str | None]:
    """Return (ready, error_detail) for whatever framework the group uses."""
    if group is None:
        return False, "Order has no buying group"
    framework = (group.api_framework or "").strip().casefold()
    if framework == "parsefile":
        return parsefile_group_ready(group)
    if framework == "usabg":
        return usabg_group_ready(group)
    if framework == "buyinggroup":
        return buyinggroup_group_ready(group)
    if framework == "dealandrunner":
        return dealandrunner_group_ready(group)
    if framework == "bfmr":
        return bfmr_group_ready(group)
    if not framework:
        return False, "Buying group API framework is not configured for submission"
    return False, f"Unsupported buying group API framework: {group.api_framework}"


def tracking_number_for_item(item: Item) -> str | None:
    for si in item.shipment_items or []:
        if si.shipment and (si.shipment.tracking_number or "").strip():
            return si.shipment.tracking_number.strip()
    return None


def _item_amount(item: Item) -> Decimal | float | None:
    return item.price_sold * (item.quantity or 1)


def _sum_amounts(items: Sequence[Item]) -> Decimal | float | None:
    total: Decimal | float | None = None
    for item in items:
        amount = _item_amount(item)
        if amount is None:
            continue
        total = amount if total is None else total + amount
    return total


def _product_notes(items: Sequence[Item]) -> str | None:
    """Item quantities (+ descriptions), e.g. ``2-Widget; 1-Gadget``."""
    parts: list[str] = []
    for item in items:
        qty = item.quantity or 1
        desc = (item.description or "").strip()
        parts.append(f"{qty}-{desc}" if desc else str(qty))
    return "; ".join(parts) if parts else None


def _order_numbers(pairs: Sequence[tuple[Item, Order]]) -> str | None:
    """Unique store order numbers in first-seen order, comma-separated."""
    seen: set[str] = set()
    orders: list[str] = []
    for _item, order in pairs:
        num = (order.store_order_number or "").strip()
        if not num:
            continue
        key = num.casefold()
        if key in seen:
            continue
        seen.add(key)
        orders.append(num)
    return ",".join(orders) if orders else None


def _tracking_note_text(pairs: Sequence[tuple[Item, Order]]) -> str | None:
    """Note body with order numbers and products (USABG Note column / Parsefile notes)."""
    orders = _order_numbers(pairs)
    products = _product_notes([item for item, _order in pairs])
    parts = [p for p in (orders, products) if p]
    return "\n".join(parts) if parts else None

# Back-compat aliases used by Parsefile builders.
_parsefile_notes = _product_notes
_parsefile_order_numbers = _order_numbers


def build_parsefile_entry(
    item: Item,
    order: Order,
    tracking: str,
) -> parsefile_api.ParsefileTrackingEntry:
    return build_parsefile_entry_from_items([(item, order)], tracking)


def build_parsefile_entry_from_items(
    pairs: Sequence[tuple[Item, Order]],
    tracking: str,
) -> parsefile_api.ParsefileTrackingEntry:
    """One Parsefile entry for a tracking: summed amount, comma-joined orders, all notes."""
    if not pairs:
        raise ValueError("At least one item is required")
    items = [item for item, _order in pairs]
    return parsefile_api.ParsefileTrackingEntry(
        tracking=tracking,
        order=_parsefile_order_numbers(pairs),
        amount=_sum_amounts(items),
        notes=_parsefile_notes(items),
    )


def build_usabg_entry(item: Item, tracking: str) -> usabg_api.UsabgTrackingEntry:
    return usabg_api.UsabgTrackingEntry(
        tracking=tracking,
        amount=_item_amount(item),
    )


def build_buyinggroup_entry_from_items(
    pairs: Sequence[tuple[Item, Order]],
    tracking: str,
) -> buyinggroup_api.BuyinggroupTrackingEntry:
    """One BuyingGroup.com entry: summed amount, order numbers, note."""
    if not pairs:
        raise ValueError("At least one item is required")
    items = [item for item, _order in pairs]
    return buyinggroup_api.BuyinggroupTrackingEntry(
        tracking=tracking,
        amount=_sum_amounts(items),
        order_num=_order_numbers(pairs),
        note=_tracking_note_text(pairs),
    )


def mark_item_submitted(
    item: Item,
    when: datetime | None = None,
    *,
    submission_id: str | None = None,
    submission_api_id: str | None = None,
) -> None:
    item.status = ItemStatus.SUBMITTED
    item.submitted_at = to_date_only(when or datetime.now(timezone.utc))
    # Do not overwrite values already set (e.g. manual entry).
    if submission_id and not (item.submission_id or "").strip():
        item.submission_id = submission_id.strip()
    if submission_api_id and not (item.submission_api_id or "").strip():
        item.submission_api_id = submission_api_id.strip()


@dataclass
class SubmitResult:
    """Normalized result from any buying-group tracking submit."""

    message: str
    affected: int | None = None
    item_ids: list[int] = field(default_factory=list)
    # tracking number -> (display_id, api_id)
    submission_ids_by_tracking: dict[str, tuple[str | None, str | None]] = field(
        default_factory=dict
    )


def submit_parsefile_trackings(
    group: BuyingGroup,
    entries: Sequence[parsefile_api.ParsefileTrackingEntry],
) -> parsefile_api.ParsefileSubmitResult:
    ready, reason = parsefile_group_ready(group)
    if not ready:
        raise ValueError(reason or "Buying group API is not configured")
    if not entries:
        raise ValueError("At least one tracking entry is required")
    return parsefile_api.submit_trackings(
        base_url=group.base_url,
        api_url=parsefile_api.resolve_api_url(group.api_url),
        bearer_token=group.bearer_token,
        user_id=int(group.api_user_id),
        email=group.api_email,
        trackings=list(entries),
    )


def submit_usabg_trackings(
    group: BuyingGroup,
    entries: Sequence[usabg_api.UsabgTrackingEntry],
) -> usabg_api.UsabgSubmitResult:
    ready, reason = usabg_group_ready(group)
    if not ready:
        raise ValueError(reason or "Buying group API is not configured")
    if not entries:
        raise ValueError("At least one tracking entry is required")
    return usabg_api.submit_trackings(
        base_url=group.base_url,
        username=str(group.api_username),
        password=str(group.api_password),
        trackings=list(entries),
    )


def submit_buyinggroup_trackings(
    group: BuyingGroup,
    entries: Sequence[buyinggroup_api.BuyinggroupTrackingEntry],
) -> buyinggroup_api.BuyinggroupSubmitResult:
    ready, reason = buyinggroup_group_ready(group)
    if not ready:
        raise ValueError(reason or "Buying group API is not configured")
    if not entries:
        raise ValueError("At least one tracking entry is required")
    return buyinggroup_api.submit_trackings(
        base_url=group.base_url,
        api_url=group.api_url,
        username=str(group.api_username),
        password=str(group.api_password),
        trackings=list(entries),
    )


def submit_dealandrunner_trackings(
    group: BuyingGroup,
    entries: Sequence[dealandrunner_api.DealandrunnerTrackingEntry],
) -> dealandrunner_api.DealandrunnerSubmitResult:
    ready, reason = dealandrunner_group_ready(group)
    if not ready:
        raise ValueError(reason or "Buying group API is not configured")
    if not entries:
        raise ValueError("At least one tracking entry is required")
    return dealandrunner_api.submit_trackings(
        base_url=group.base_url,
        email=str(group.api_username),
        password=str(group.api_password),
        trackings=list(entries),
    )


def submit_bfmr_trackings(
    group: BuyingGroup,
    entries: Sequence[bfmr_api.BfmrTrackingEntry],
) -> bfmr_api.BfmrSubmitResult:
    ready, reason = bfmr_group_ready(group)
    if not ready:
        raise ValueError(reason or "Buying group API is not configured")
    if not entries:
        raise ValueError("At least one tracking entry is required")
    return bfmr_api.submit_trackings(
        base_url=group.base_url,
        api_key=str(group.api_username),
        api_secret=str(group.api_password),
        trackings=list(entries),
    )


def _bfmr_qty_for_items(items: Sequence[Item], *, cap: int | None) -> int:
    total = sum(max(int(item.quantity or 1), 1) for item in items)
    if cap is not None and cap > 0:
        return max(min(total, cap), 1)
    return max(total, 1)


def build_bfmr_entry_from_purchase(
    purchase: bfmr_api.BfmrTrackerRow,
    *,
    tracking: str,
    items: Sequence[Item],
) -> bfmr_api.BfmrTrackingEntry:
    """Create-shipment entry for a purchased BFMR row (SID null)."""
    rid = (purchase.reserve_id or "").strip()
    pid = (purchase.purchase_id or "").strip()
    order_no = (purchase.order_id or "").strip()
    tn = (tracking or "").strip()
    if not rid or not pid or not order_no or not tn:
        raise ValueError("BFMR purchase is missing reserve_id, purchase_id, order_id, or tracking")
    return bfmr_api.BfmrTrackingEntry(
        reserve_id=rid,
        purchase_id=pid,
        order_no=order_no,
        tracking_number=tn,
        qty=_bfmr_qty_for_items(items, cap=purchase.qty),
        shipment_id=None,
    )


def _fetch_bfmr_purchases_needing_tracking(
    group: BuyingGroup,
) -> dict[str, bfmr_api.BfmrTrackerRow]:
    """order_id.casefold() → BFMR purchase row still needing tracking."""
    fetched = bfmr_api.list_purchases_needing_tracking(
        base_url=group.base_url,
        api_key=str(group.api_username),
        api_secret=str(group.api_password),
    )
    return bfmr_api.index_purchases_needing_tracking_by_order(fetched.rows)


def _aggregate_bfmr_entries(
    pending: Sequence[tuple[Item, Order, str]],
    purchases_by_order: dict[str, bfmr_api.BfmrTrackerRow],
) -> tuple[
    list[bfmr_api.BfmrTrackingEntry],
    list[tuple[Item, Order, str]],
    list[str],
]:
    """Build BFMR create-shipment entries for pending items that match a purchase.

    Returns ``(entries, matched_pending, skip_notes)``.
    Multiple trackings on one purchase get one entry each with qty split by items;
    remaining BFMR qty is capped per purchase across those entries.
    """
    # Group pending by (order_id.casefold(), tracking)
    by_order_tn: OrderedDict[tuple[str, str], list[tuple[Item, Order]]] = OrderedDict()
    skip_notes: list[str] = []
    unmatched_orders: set[str] = set()

    for item, order, tracking in pending:
        store_order = (order.store_order_number or "").strip()
        if not store_order:
            skip_notes.append(f"item {item.id}: order has no store order number")
            continue
        okey = store_order.casefold()
        if okey not in purchases_by_order:
            unmatched_orders.add(store_order)
            continue
        tn = (tracking or "").strip()
        if not tn:
            continue
        by_order_tn.setdefault((okey, tn), []).append((item, order))

    for store_order in sorted(unmatched_orders):
        skip_notes.append(f"No BFMR purchase needing tracking for order {store_order}")

    # Remaining qty capacity per purchase (mutated as we allocate).
    remaining_qty: dict[str, int] = {}
    for okey, row in purchases_by_order.items():
        cap = int(row.qty) if row.qty is not None and int(row.qty) > 0 else None
        remaining_qty[okey] = cap if cap is not None else 10**9

    entries: list[bfmr_api.BfmrTrackingEntry] = []
    matched: list[tuple[Item, Order, str]] = []
    for (okey, tn), pairs in by_order_tn.items():
        purchase = purchases_by_order.get(okey)
        if purchase is None:
            continue
        left = remaining_qty.get(okey, 0)
        if left < 1:
            skip_notes.append(
                f"BFMR purchase qty exhausted for order {purchase.order_id} (tracking {tn})"
            )
            continue
        items = [item for item, _order in pairs]
        want = sum(max(int(item.quantity or 1), 1) for item in items)
        qty = max(min(want, left), 1)
        try:
            entry = build_bfmr_entry_from_purchase(purchase, tracking=tn, items=items)
        except ValueError as exc:
            skip_notes.append(str(exc))
            continue
        entry.qty = qty
        remaining_qty[okey] = left - qty
        entries.append(entry)
        for item, order in pairs:
            matched.append((item, order, tn))

    return entries, matched, skip_notes


def find_shipped_items_sharing_tracking(
    db: Session,
    *,
    buying_group_id: int,
    tracking: str,
) -> list[Item]:
    """All SHIPPED items on this buying group that share the tracking number."""
    from app.models import Shipment
    from sqlalchemy import func

    tn = (tracking or "").strip()
    if not tn:
        return []

    rows = (
        db.query(Item)
        .join(Order, Order.id == Item.order_id)
        .join(ShipmentItem, ShipmentItem.item_id == Item.id)
        .join(Shipment, Shipment.id == ShipmentItem.shipment_id)
        .filter(Item.status == ItemStatus.SHIPPED)
        .filter(Order.buying_group_id == buying_group_id)
        .filter(Order.status != "personal")
        .filter(Shipment.tracking_number.isnot(None))
        .filter(Shipment.tracking_number != "")
        .filter(func.trim(Shipment.tracking_number) == tn)
        .options(
            joinedload(Item.order),
            selectinload(Item.shipment_items).joinedload(ShipmentItem.shipment),
            selectinload(Item.payment_line_items),
        )
        .distinct()
        .all()
    )
    return list(rows)


def submit_item_tracking_to_group(
    group: BuyingGroup,
    item: Item,
    order: Order,
    tracking: str,
    *,
    sibling_items: Sequence[Item] | None = None,
) -> SubmitResult:
    """Submit tracking for an item, summing amounts across siblings that share it.

    When ``sibling_items`` is provided (shipped items with the same tracking on
    the same buying group), the submitted amount is the sum of all siblings and
    ``item_ids`` lists every sibling so the caller can mark them all submitted.
    """
    require_group_submissions_enabled()
    items = list(sibling_items) if sibling_items is not None else [item]
    if not any(i.id == item.id for i in items):
        items = [item, *items]
    item_ids = [i.id for i in items]
    amount = _sum_amounts(items)

    pairs: list[tuple[Item, Order]] = []
    for it in items:
        ord_ = it.order if it.order is not None else (order if it.id == item.id else None)
        if ord_ is None:
            continue
        pairs.append((it, ord_))
    if not pairs:
        pairs = [(item, order)]

    framework = (group.api_framework or "").strip().casefold()
    if framework == "parsefile":
        entry = build_parsefile_entry_from_items(pairs, tracking)
        result = submit_parsefile_trackings(group, [entry])
        ids_map, id_err = _parsefile_submission_ids(group, [tracking], result)
        message = result.message
        if id_err and not ids_map:
            message = f"{message}\n{id_err}" if message else id_err
        return SubmitResult(
            message=message,
            affected=result.affected,
            item_ids=item_ids,
            submission_ids_by_tracking=ids_map,
        )
    if framework == "usabg":
        result = submit_usabg_trackings(
            group,
            [
                usabg_api.UsabgTrackingEntry(
                    tracking=tracking,
                    amount=amount,
                    note=_tracking_note_text(pairs),
                )
            ],
        )
        ids_map = {
            tn: (sid, None)
            for tn, sid in (result.submission_ids or {}).items()
            if sid
        }
        return SubmitResult(
            message=result.message,
            affected=result.affected,
            item_ids=item_ids,
            submission_ids_by_tracking=ids_map,
        )
    if framework == "buyinggroup":
        entry = build_buyinggroup_entry_from_items(pairs, tracking)
        # Prefer summed amount already computed for siblings.
        entry.amount = amount
        result = submit_buyinggroup_trackings(group, [entry])
        ids_map = _buyinggroup_submission_ids(result)
        return SubmitResult(
            message=result.message,
            affected=result.affected,
            item_ids=item_ids,
            submission_ids_by_tracking=ids_map,
        )
    if framework == "dealandrunner":
        result = submit_dealandrunner_trackings(
            group,
            [dealandrunner_api.DealandrunnerTrackingEntry(tracking=tracking)],
        )
        ids_map = {
            tn: (sid, None)
            for tn, sid in (result.submission_ids or {}).items()
            if sid
        }
        return SubmitResult(
            message=result.message,
            affected=result.affected,
            item_ids=item_ids,
            submission_ids_by_tracking=ids_map,
        )
    if framework == "bfmr":
        store_order = (order.store_order_number or "").strip()
        if not store_order:
            raise ValueError("Order has no store order number to match a BFMR purchase")
        purchases = _fetch_bfmr_purchases_needing_tracking(group)
        purchase = purchases.get(store_order.casefold())
        if purchase is None:
            raise ValueError(
                f"No BFMR purchase needing tracking for order {store_order}"
            )
        # Only count siblings on the same store order (BFMR purchase is per order_id).
        order_key = store_order.casefold()
        same_order_pairs = [
            (it, ord_)
            for it, ord_ in pairs
            if ((ord_.store_order_number or "").strip().casefold() == order_key)
        ]
        if not same_order_pairs:
            same_order_pairs = [(item, order)]
        same_order_items = [it for it, _ord in same_order_pairs]
        entry = build_bfmr_entry_from_purchase(
            purchase,
            tracking=tracking,
            items=same_order_items,
        )
        result = submit_bfmr_trackings(group, [entry])
        if result.failed and tracking.casefold() in {t.casefold() for t in result.failed}:
            raise RuntimeError(result.message or "BFMR tracking submit failed")
        ids_map = {
            tn: (display, api)
            for tn, (display, api) in (result.submission_ids or {}).items()
            if display or api
        }
        # Prefer my_tracker_id as display when available.
        display = (purchase.my_tracker_id or purchase.purchase_id or "").strip() or None
        api_id = (purchase.purchase_id or "").strip() or None
        if tracking not in ids_map and (display or api_id):
            ids_map[tracking] = (display, api_id)
        return SubmitResult(
            message=result.message,
            affected=result.affected,
            item_ids=[it.id for it in same_order_items],
            submission_ids_by_tracking=ids_map,
        )
    raise ValueError("Buying group API framework is not configured for submission")


def _buyinggroup_submission_ids(
    result: buyinggroup_api.BuyinggroupSubmitResult,
) -> dict[str, tuple[str | None, str | None]]:
    out: dict[str, tuple[str | None, str | None]] = {}
    for tn, ids in (result.submission_ids or {}).items():
        display = (ids.display_id or "").strip() or None
        api = (ids.api_id or "").strip() or None
        if not display and api:
            display = api
        if display or api:
            out[tn] = (display, api)
    return out


def _parsefile_submission_ids(
    group: BuyingGroup,
    trackings: Sequence[str],
    result: parsefile_api.ParsefileSubmitResult,
    *,
    portal_password: str | None = None,
) -> tuple[dict[str, tuple[str | None, str | None]], str | None]:
    """Look up Parsefile orders-all ids when portal password is configured.

    Returns ``(ids_by_tracking, error_note)``.
    """
    if result.submission_ids:
        out: dict[str, tuple[str | None, str | None]] = {}
        for tn, ids in result.submission_ids.items():
            display = (ids.display_id or "").strip() or None
            api = (ids.api_id or "").strip() or None
            if not display and api:
                display = api
            if display or api:
                out[tn] = (display, api)
        return out, None
    email = (group.api_email or "").strip()
    password = (portal_password if portal_password is not None else group.api_password or "").strip()
    base_url = (group.base_url or "").strip()
    if not password:
        return {}, "Submission ID not retrieved (set Parsefile portal password on the buying group)."
    if not email or not base_url:
        return {}, "Submission ID not retrieved (Parsefile email/base URL missing)."
    try:
        looked = parsefile_api.lookup_submission_ids(
            base_url=base_url,
            email=email,
            password=password,
            tracking_numbers=[(t or "").strip() for t in trackings if (t or "").strip()],
        )
    except Exception as exc:
        logger.warning("Parsefile submission id lookup failed: %s", exc)
        return {}, f"Submission ID not retrieved ({exc})"
    out = {}
    for tn, ids in looked.submission_ids.items():
        display = (ids.display_id or "").strip() or None
        api = (ids.api_id or "").strip() or None
        if not display and api:
            display = api
        if display or api:
            out[tn] = (display, api)
    if out:
        return out, looked.error
    return {}, (
        f"Submission ID not retrieved ({looked.error})"
        if looked.error
        else "Submission ID not retrieved (order not found in it@orders-all)."
    )


def _ids_for_tracking(
    submission_ids_by_tracking: dict[str, tuple[str | None, str | None]],
    tracking: str | None,
) -> tuple[str | None, str | None]:
    tn = (tracking or "").strip()
    if not tn:
        return None, None
    if tn in submission_ids_by_tracking:
        return submission_ids_by_tracking[tn]
    key = tn.casefold()
    for candidate, ids in submission_ids_by_tracking.items():
        if candidate.casefold() == key:
            return ids
    return None, None


def format_submission_ids_for_log(
    submission_ids_by_tracking: dict[str, tuple[str | None, str | None]] | None,
) -> str | None:
    """One line per tracking: ``{tracking} → {submission_id}``."""
    if not submission_ids_by_tracking:
        return None
    parts: list[str] = []
    for tn, (display, api) in submission_ids_by_tracking.items():
        # Prefer user-facing submission_id (display); fall back to api id.
        label = (display or "").strip() or (api or "").strip()
        if not label:
            continue
        tn_label = (tn or "").strip() or "?"
        parts.append(f"{tn_label} → {label}")
    if not parts:
        return None
    return "\n".join(parts)


def append_submission_ids_to_message(
    message: str | None,
    submission_ids_by_tracking: dict[str, tuple[str | None, str | None]] | None,
    *,
    missing_note: str | None = None,
) -> str:
    """Put tracking→submission-id lines first, then the status message."""
    base = (message or "").strip()
    summary = format_submission_ids_for_log(submission_ids_by_tracking)
    if summary:
        # Avoid duplicating if the message already embeds the same lines.
        if summary in base:
            return base
        return f"{summary}\n{base}" if base else summary
    if missing_note and "submission id not retrieved" not in base.casefold():
        note = missing_note.strip()
        if note and note not in base:
            return f"{note}\n{base}" if base else note
    return base or "Submitted tracking number(s)."


def _aggregate_usabg_entries(
    pending: Sequence[tuple[Item, Order, str]],
) -> list[usabg_api.UsabgTrackingEntry]:
    """One entry per tracking number; sum amounts and build notes when items share a tracking."""
    groups: OrderedDict[str, list[tuple[Item, Order]]] = OrderedDict()
    for item, order, tracking in pending:
        groups.setdefault(tracking, []).append((item, order))
    entries: list[usabg_api.UsabgTrackingEntry] = []
    for tracking, pairs in groups.items():
        items = [item for item, _order in pairs]
        entries.append(
            usabg_api.UsabgTrackingEntry(
                tracking=tracking,
                amount=_sum_amounts(items),
                note=_tracking_note_text(pairs),
            )
        )
    return entries


def _aggregate_parsefile_entries(
    pending: Sequence[tuple[Item, Order, str]],
) -> list[parsefile_api.ParsefileTrackingEntry]:
    """One Parsefile entry per tracking: summed amount, comma-joined orders, merged notes."""
    groups: OrderedDict[str, list[tuple[Item, Order]]] = OrderedDict()
    for item, order, tracking in pending:
        groups.setdefault(tracking, []).append((item, order))
    return [
        build_parsefile_entry_from_items(pairs, tracking)
        for tracking, pairs in groups.items()
    ]


def _aggregate_buyinggroup_entries(
    pending: Sequence[tuple[Item, Order, str]],
) -> list[buyinggroup_api.BuyinggroupTrackingEntry]:
    """One BuyingGroup.com entry per tracking number."""
    groups: OrderedDict[str, list[tuple[Item, Order]]] = OrderedDict()
    for item, order, tracking in pending:
        groups.setdefault(tracking, []).append((item, order))
    return [
        build_buyinggroup_entry_from_items(pairs, tracking)
        for tracking, pairs in groups.items()
    ]


def _aggregate_dealandrunner_entries(
    pending: Sequence[tuple[Item, Order, str]],
) -> list[dealandrunner_api.DealandrunnerTrackingEntry]:
    """One Deal & Runner entry per unique tracking number (API has no amount field)."""
    seen: OrderedDict[str, None] = OrderedDict()
    for _item, _order, tracking in pending:
        tn = (tracking or "").strip()
        if not tn:
            continue
        seen.setdefault(tn, None)
    return [dealandrunner_api.DealandrunnerTrackingEntry(tracking=tn) for tn in seen]


@dataclass
class BatchSubmitResult:
    buying_group_id: int
    buying_group_name: str
    submitted_count: int = 0
    shipped_count: int = 0
    missing_tracking_count: int = 0
    tracking_numbers: list[str] = field(default_factory=list)
    message: str | None = None
    error: str | None = None
    # tracking number -> (display_id, api_id)
    submission_ids_by_tracking: dict[str, tuple[str | None, str | None]] = field(
        default_factory=dict
    )


def batch_submit_pending_trackings(
    db: Session,
    *,
    buying_group_id: int,
) -> BatchSubmitResult:
    """Batch-submit SHIPPED items with tracking for one API-enabled buying group.

    Only marks items SUBMITTED after a successful API call.
    """
    require_group_submissions_enabled()
    from app.models import Shipment

    group = db.query(BuyingGroup).filter(BuyingGroup.id == buying_group_id).first()
    if not group:
        return BatchSubmitResult(
            buying_group_id=buying_group_id,
            buying_group_name=f"group {buying_group_id}",
            error="Buying group not found",
        )

    group_name = (group.name or "").strip() or f"group {buying_group_id}"
    ready, reason = group_ready(group)
    if not ready:
        return BatchSubmitResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            error=reason or "Buying group API is not configured",
        )

    framework = (group.api_framework or "").strip().casefold()

    shipped_items = (
        db.query(Item)
        .join(Order, Order.id == Item.order_id)
        .filter(Item.status == ItemStatus.SHIPPED)
        .filter(Order.buying_group_id == buying_group_id)
        .filter(Order.status != "personal")
        .options(
            joinedload(Item.order),
            selectinload(Item.shipment_items).joinedload(ShipmentItem.shipment),
        )
        .all()
    )
    shipped_count = len(shipped_items)

    pending: list[tuple[Item, Order, str]] = []
    missing_tracking = 0
    for item in shipped_items:
        order = item.order
        if not order:
            continue
        tracking = tracking_number_for_item(item)
        if not tracking:
            # Fallback: re-read tracking from DB in case relationship was empty.
            row = (
                db.query(Shipment.tracking_number)
                .join(ShipmentItem, ShipmentItem.shipment_id == Shipment.id)
                .filter(ShipmentItem.item_id == item.id)
                .filter(Shipment.tracking_number.isnot(None))
                .filter(Shipment.tracking_number != "")
                .first()
            )
            tracking = (row[0] or "").strip() if row else None
        if not tracking:
            missing_tracking += 1
            continue
        pending.append((item, order, tracking))

    if not pending:
        if shipped_count == 0:
            msg = "No shipped items for this buying group."
        else:
            msg = (
                f"No pending tracking numbers to submit "
                f"({shipped_count} shipped item(s), {missing_tracking} without tracking)."
            )
        return BatchSubmitResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            shipped_count=shipped_count,
            missing_tracking_count=missing_tracking,
            message=msg,
        )

    item_ids = [item.id for item, _, _ in pending]
    tracking_numbers = [tracking for *_, tracking in pending]
    submission_ids_by_tracking: dict[str, tuple[str | None, str | None]] = {}
    # item_id -> tracking for applying ids after submit
    item_tracking: dict[int, str] = {item.id: tracking for item, _order, tracking in pending}

    try:
        if framework == "parsefile":
            # Capture credentials before any commit/rollback can expire the ORM object.
            base_url = str(group.base_url)
            api_url = parsefile_api.resolve_api_url(group.api_url)
            bearer_token = str(group.bearer_token)
            api_user_id = int(group.api_user_id)
            api_email = str(group.api_email)
            api_password = str(group.api_password or "")
            entries = _aggregate_parsefile_entries(pending)
            tracking_numbers = [e.tracking for e in entries]
            api_result = parsefile_api.submit_trackings(
                base_url=base_url,
                api_url=api_url,
                bearer_token=bearer_token,
                user_id=api_user_id,
                email=api_email,
                trackings=entries,
            )
            message = api_result.message
            submission_ids_by_tracking, parsefile_id_err = _parsefile_submission_ids(
                group,
                tracking_numbers,
                api_result,
                portal_password=api_password,
            )
            if parsefile_id_err and not submission_ids_by_tracking:
                message = (
                    f"{message}\n{parsefile_id_err}" if message else parsefile_id_err
                )
        elif framework == "usabg":
            base_url = group.base_url
            username = str(group.api_username)
            password = str(group.api_password)
            entries = _aggregate_usabg_entries(pending)
            tracking_numbers = [e.tracking for e in entries]
            api_result = usabg_api.submit_trackings(
                base_url=base_url,
                username=username,
                password=password,
                trackings=entries,
            )
            message = api_result.message
            submission_ids_by_tracking = {
                tn: (sid, None)
                for tn, sid in (api_result.submission_ids or {}).items()
                if sid
            }
        elif framework == "buyinggroup":
            base_url = group.base_url
            api_url = group.api_url
            username = str(group.api_username)
            password = str(group.api_password)
            entries = _aggregate_buyinggroup_entries(pending)
            tracking_numbers = [e.tracking for e in entries]
            api_result = buyinggroup_api.submit_trackings(
                base_url=base_url,
                api_url=api_url,
                username=username,
                password=password,
                trackings=entries,
            )
            message = api_result.message
            submission_ids_by_tracking = _buyinggroup_submission_ids(api_result)
        elif framework == "dealandrunner":
            base_url = group.base_url
            email = str(group.api_username)
            password = str(group.api_password)
            entries = _aggregate_dealandrunner_entries(pending)
            tracking_numbers = [e.tracking for e in entries]
            api_result = dealandrunner_api.submit_trackings(
                base_url=base_url,
                email=email,
                password=password,
                trackings=entries,
            )
            message = api_result.message
            # Only mark items whose tracking succeeded or was already present.
            ok_tns = {tn.casefold() for tn in (api_result.succeeded + api_result.duplicates)}
            if ok_tns and api_result.failed:
                item_ids = [
                    item.id
                    for item, _order, tracking in pending
                    if (tracking or "").strip().casefold() in ok_tns
                ]
            if api_result.failed and not item_ids:
                raise RuntimeError(api_result.message or "Deal & Runner tracking submit failed")
            submission_ids_by_tracking = {
                tn: (sid, None)
                for tn, sid in (api_result.submission_ids or {}).items()
                if sid
            }
        elif framework == "bfmr":
            base_url = group.base_url
            api_key = str(group.api_username)
            api_secret = str(group.api_password)
            purchases_by_order = bfmr_api.index_purchases_needing_tracking_by_order(
                bfmr_api.list_purchases_needing_tracking(
                    base_url=base_url,
                    api_key=api_key,
                    api_secret=api_secret,
                ).rows
            )
            entries, matched_pending, skip_notes = _aggregate_bfmr_entries(
                pending, purchases_by_order
            )
            if not entries:
                skip_msg = "; ".join(skip_notes) if skip_notes else (
                    f"No matching BFMR purchases needing tracking "
                    f"({shipped_count} shipped item(s) with tracking)."
                )
                return BatchSubmitResult(
                    buying_group_id=buying_group_id,
                    buying_group_name=group_name,
                    shipped_count=shipped_count,
                    missing_tracking_count=missing_tracking,
                    message=skip_msg,
                )
            tracking_numbers = [e.tracking_number for e in entries]
            item_ids = [item.id for item, _order, _tn in matched_pending]
            item_tracking = {
                item.id: tracking for item, _order, tracking in matched_pending
            }
            api_result = bfmr_api.submit_trackings(
                base_url=base_url,
                api_key=api_key,
                api_secret=api_secret,
                trackings=entries,
            )
            message = api_result.message
            if skip_notes:
                message = f"{message}\n" + "\n".join(skip_notes) if message else "\n".join(skip_notes)
            ok_tns = {tn.casefold() for tn in api_result.succeeded}
            if ok_tns and api_result.failed:
                item_ids = [
                    item.id
                    for item, _order, tracking in matched_pending
                    if (tracking or "").strip().casefold() in ok_tns
                ]
            if api_result.failed and not item_ids:
                raise RuntimeError(api_result.message or "BFMR tracking submit failed")
            # Enrich submission ids with my_tracker_id when we still have the purchase.
            submission_ids_by_tracking = {}
            for tn, (display, api) in (api_result.submission_ids or {}).items():
                submission_ids_by_tracking[tn] = (display, api)
            for entry in entries:
                tn = entry.tracking_number
                if tn.casefold() not in ok_tns:
                    continue
                purchase = purchases_by_order.get(entry.order_no.casefold())
                display = None
                if purchase is not None:
                    display = (purchase.my_tracker_id or purchase.purchase_id or "").strip() or None
                api = (entry.purchase_id or "").strip() or None
                if tn not in submission_ids_by_tracking or display:
                    prev = submission_ids_by_tracking.get(tn, (None, None))
                    submission_ids_by_tracking[tn] = (display or prev[0], api or prev[1])
        else:
            return BatchSubmitResult(
                buying_group_id=buying_group_id,
                buying_group_name=group_name,
                shipped_count=shipped_count,
                missing_tracking_count=missing_tracking,
                error=f"Unsupported buying group API framework: {group.api_framework}",
            )

        now = datetime.now(timezone.utc)
        items_to_mark = (
            db.query(Item)
            .filter(Item.id.in_(item_ids))
            .filter(Item.status == ItemStatus.SHIPPED)
            .all()
        )
        for item in items_to_mark:
            display_id, api_id = _ids_for_tracking(
                submission_ids_by_tracking, item_tracking.get(item.id)
            )
            mark_item_submitted(
                item,
                when=now,
                submission_id=display_id,
                submission_api_id=api_id,
            )
        db.commit()
        # Prefer IDs actually persisted on items (authoritative for the log).
        persisted: dict[str, tuple[str | None, str | None]] = {}
        for item in items_to_mark:
            tn = item_tracking.get(item.id) or ""
            sid = (item.submission_id or "").strip() or None
            said = (item.submission_api_id or "").strip() or None
            if tn and (sid or said):
                persisted[tn] = (sid, said)
        ids_for_log = persisted or submission_ids_by_tracking
        message = append_submission_ids_to_message(
            message,
            ids_for_log,
            missing_note=(
                "Submission ID not retrieved after submit."
                if items_to_mark and not ids_for_log
                else None
            ),
        )
        return BatchSubmitResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            submitted_count=len(items_to_mark),
            shipped_count=shipped_count,
            missing_tracking_count=missing_tracking,
            tracking_numbers=tracking_numbers,
            message=message,
            submission_ids_by_tracking=ids_for_log,
        )
    except Exception as exc:
        logger.warning(
            "Batch tracking submit failed for buying group %s (%s): %s",
            buying_group_id,
            group_name,
            exc,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return BatchSubmitResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            shipped_count=shipped_count,
            missing_tracking_count=missing_tracking,
            tracking_numbers=tracking_numbers,
            error=str(exc).strip() or exc.__class__.__name__,
        )
