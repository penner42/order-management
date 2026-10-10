"""Submit item tracking numbers to buying-group APIs (Parsefile)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import BuyingGroup, Item, Order, ShipmentItem
from app.models.item import ItemStatus
from app.utils.buying_group_apis import parsefile as parsefile_api
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


def tracking_number_for_item(item: Item) -> str | None:
    for si in item.shipment_items or []:
        if si.shipment and (si.shipment.tracking_number or "").strip():
            return si.shipment.tracking_number.strip()
    return None


def build_parsefile_entry(item: Item, order: Order, tracking: str) -> parsefile_api.ParsefileTrackingEntry:
    notes = None
    desc = (item.description or "").strip()
    if desc:
        notes = f"{item.quantity or 1}-{desc}"

    amount = None
    if item.price_sold is not None:
        amount = item.price_sold * (item.quantity or 1)

    return parsefile_api.ParsefileTrackingEntry(
        tracking=tracking,
        order=(order.store_order_number or None),
        amount=amount,
        notes=notes,
    )


def mark_item_submitted(item: Item, when: datetime | None = None) -> None:
    item.status = ItemStatus.SUBMITTED
    item.submitted_at = to_date_only(when or datetime.now(timezone.utc))


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
    ready, reason = parsefile_group_ready(group)
    if not ready:
        return BatchSubmitResult(
            buying_group_id=buying_group_id,
            buying_group_name=group_name,
            error=reason or "Buying group API is not configured",
        )

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

    # Capture credentials before any commit/rollback can expire the ORM object.
    base_url = str(group.base_url)
    api_url = parsefile_api.resolve_api_url(group.api_url)
    bearer_token = str(group.bearer_token)
    api_user_id = int(group.api_user_id)
    api_email = str(group.api_email)
    item_ids = [item.id for item, _, _ in pending]
    entries = [build_parsefile_entry(item, order, tracking) for item, order, tracking in pending]
    tracking_numbers = [tracking for *_, tracking in pending]

    try:
        api_result = parsefile_api.submit_trackings(
            base_url=base_url,
            api_url=api_url,
            bearer_token=bearer_token,
            user_id=api_user_id,
            email=api_email,
            trackings=entries,
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
            message=api_result.message,
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
