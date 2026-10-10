"""Submit item tracking numbers to buying-group APIs (Parsefile, USABG)."""
from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import BuyingGroup, Item, Order, ShipmentItem
from app.models.item import ItemStatus
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


def group_ready(group: BuyingGroup | None) -> tuple[bool, str | None]:
    """Return (ready, error_detail) for whatever framework the group uses."""
    if group is None:
        return False, "Order has no buying group"
    framework = (group.api_framework or "").strip().casefold()
    if framework == "parsefile":
        return parsefile_group_ready(group)
    if framework == "usabg":
        return usabg_group_ready(group)
    if not framework:
        return False, "Buying group API framework is not configured for submission"
    return False, f"Unsupported buying group API framework: {group.api_framework}"


def tracking_number_for_item(item: Item) -> str | None:
    for si in item.shipment_items or []:
        if si.shipment and (si.shipment.tracking_number or "").strip():
            return si.shipment.tracking_number.strip()
    return None


def _item_amount(item: Item) -> Decimal | float | None:
    if item.price_sold is None:
        return None
    return item.price_sold * (item.quantity or 1)


def build_parsefile_entry(item: Item, order: Order, tracking: str) -> parsefile_api.ParsefileTrackingEntry:
    notes = None
    desc = (item.description or "").strip()
    if desc:
        notes = f"{item.quantity or 1}-{desc}"

    return parsefile_api.ParsefileTrackingEntry(
        tracking=tracking,
        order=(order.store_order_number or None),
        amount=_item_amount(item),
        notes=notes,
    )


def build_usabg_entry(item: Item, tracking: str) -> usabg_api.UsabgTrackingEntry:
    return usabg_api.UsabgTrackingEntry(
        tracking=tracking,
        amount=_item_amount(item),
    )


def mark_item_submitted(item: Item, when: datetime | None = None) -> None:
    item.status = ItemStatus.SUBMITTED
    item.submitted_at = to_date_only(when or datetime.now(timezone.utc))


@dataclass
class SubmitResult:
    """Normalized result from any buying-group tracking submit."""

    message: str
    affected: int | None = None


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


def submit_item_tracking_to_group(
    group: BuyingGroup,
    item: Item,
    order: Order,
    tracking: str,
) -> SubmitResult:
    """Submit a single item's tracking via the group's configured framework."""
    framework = (group.api_framework or "").strip().casefold()
    if framework == "parsefile":
        result = submit_parsefile_trackings(group, [build_parsefile_entry(item, order, tracking)])
        return SubmitResult(message=result.message, affected=result.affected)
    if framework == "usabg":
        result = submit_usabg_trackings(group, [build_usabg_entry(item, tracking)])
        return SubmitResult(message=result.message, affected=result.affected)
    raise ValueError("Buying group API framework is not configured for submission")


def _aggregate_usabg_entries(
    pending: Sequence[tuple[Item, Order, str]],
) -> list[usabg_api.UsabgTrackingEntry]:
    """One entry per tracking number; sum amounts when items share a tracking."""
    amounts: OrderedDict[str, Decimal | float | None] = OrderedDict()
    for item, _order, tracking in pending:
        amount = _item_amount(item)
        if tracking not in amounts:
            amounts[tracking] = amount
            continue
        existing = amounts[tracking]
        if amount is None:
            continue
        if existing is None:
            amounts[tracking] = amount
        else:
            amounts[tracking] = existing + amount
    return [
        usabg_api.UsabgTrackingEntry(tracking=tn, amount=amt)
        for tn, amt in amounts.items()
    ]


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


def batch_submit_pending_trackings(
    db: Session,
    *,
    buying_group_id: int,
) -> BatchSubmitResult:
    """Batch-submit SHIPPED items with tracking for one API-enabled buying group.

    Only marks items SUBMITTED after a successful API call.
    """
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

    try:
        if framework == "parsefile":
            # Capture credentials before any commit/rollback can expire the ORM object.
            base_url = str(group.base_url)
            api_url = parsefile_api.resolve_api_url(group.api_url)
            bearer_token = str(group.bearer_token)
            api_user_id = int(group.api_user_id)
            api_email = str(group.api_email)
            entries = [build_parsefile_entry(item, order, tracking) for item, order, tracking in pending]
            api_result = parsefile_api.submit_trackings(
                base_url=base_url,
                api_url=api_url,
                bearer_token=bearer_token,
                user_id=api_user_id,
                email=api_email,
                trackings=entries,
            )
            message = api_result.message
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
            mark_item_submitted(item, when=now)
        db.commit()
        return BatchSubmitResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            submitted_count=len(items_to_mark),
            shipped_count=shipped_count,
            missing_tracking_count=missing_tracking,
            tracking_numbers=tracking_numbers,
            message=message,
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
