"""Submit item tracking numbers to buying-group APIs (Parsefile)."""
from __future__ import annotations

import logging
from collections import defaultdict
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
    if not (group.api_url or "").strip():
        return False, "Buying group has no API URL"
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
        api_url=group.api_url,
        bearer_token=group.bearer_token,
        user_id=int(group.api_user_id),
        email=group.api_email,
        trackings=list(entries),
    )


@dataclass
class GroupSubmitResult:
    buying_group_id: int
    buying_group_name: str
    submitted_count: int = 0
    tracking_numbers: list[str] = field(default_factory=list)
    message: str | None = None
    error: str | None = None


@dataclass
class BatchSubmitResult:
    groups: list[GroupSubmitResult] = field(default_factory=list)

    @property
    def submitted_count(self) -> int:
        return sum(g.submitted_count for g in self.groups)

    @property
    def error_groups(self) -> list[GroupSubmitResult]:
        return [g for g in self.groups if g.error]


@dataclass
class _PendingGroup:
    buying_group_id: int
    buying_group_name: str
    base_url: str
    api_url: str
    bearer_token: str
    api_user_id: int
    api_email: str
    item_ids: list[int]
    entries: list[parsefile_api.ParsefileTrackingEntry]
    tracking_numbers: list[str]


def batch_submit_pending_trackings(
    db: Session,
    *,
    store_account_id: int,
) -> BatchSubmitResult:
    """Batch-submit SHIPPED items with tracking for API-enabled buying groups.

    Groups by buying group (one Parsefile request per group). Failures for one
    group do not block others. Only marks items SUBMITTED after a successful API call.
    """
    items = (
        db.query(Item)
        .join(Order, Order.id == Item.order_id)
        .filter(Item.status == ItemStatus.SHIPPED)
        .filter(Order.store_account_id == store_account_id)
        .filter(Order.status != "personal")
        .filter(Order.buying_group_id.isnot(None))
        .options(
            joinedload(Item.order).joinedload(Order.buying_group),
            selectinload(Item.shipment_items).joinedload(ShipmentItem.shipment),
        )
        .all()
    )

    by_group: dict[int, list[tuple[Item, Order, BuyingGroup, str]]] = defaultdict(list)
    for item in items:
        order = item.order
        if not order or not order.buying_group:
            continue
        group = order.buying_group
        ready, _ = parsefile_group_ready(group)
        if not ready:
            continue
        tracking = tracking_number_for_item(item)
        if not tracking:
            continue
        by_group[group.id].append((item, order, group, tracking))

    pending: list[_PendingGroup] = []
    for group_id, rows in by_group.items():
        group = rows[0][2]
        group_name = (group.name or "").strip() or f"group {group_id}"
        pending.append(
            _PendingGroup(
                buying_group_id=group_id,
                buying_group_name=group_name,
                base_url=str(group.base_url),
                api_url=str(group.api_url),
                bearer_token=str(group.bearer_token),
                api_user_id=int(group.api_user_id),
                api_email=str(group.api_email),
                item_ids=[item.id for item, *_ in rows],
                entries=[
                    build_parsefile_entry(item, order, tracking)
                    for item, order, _, tracking in rows
                ],
                tracking_numbers=[tracking for *_, tracking in rows],
            )
        )

    result = BatchSubmitResult()
    if not pending:
        return result

    now = datetime.now(timezone.utc)
    for group in pending:
        try:
            api_result = parsefile_api.submit_trackings(
                base_url=group.base_url,
                api_url=group.api_url,
                bearer_token=group.bearer_token,
                user_id=group.api_user_id,
                email=group.api_email,
                trackings=group.entries,
            )
            items_to_mark = (
                db.query(Item)
                .filter(Item.id.in_(group.item_ids))
                .filter(Item.status == ItemStatus.SHIPPED)
                .all()
            )
            for item in items_to_mark:
                mark_item_submitted(item, when=now)
            db.commit()
            result.groups.append(
                GroupSubmitResult(
                    buying_group_id=group.buying_group_id,
                    buying_group_name=group.buying_group_name,
                    submitted_count=len(items_to_mark),
                    tracking_numbers=group.tracking_numbers,
                    message=api_result.message,
                )
            )
        except Exception as exc:
            logger.warning(
                "Batch tracking submit failed for buying group %s (%s): %s",
                group.buying_group_id,
                group.buying_group_name,
                exc,
            )
            try:
                db.rollback()
            except Exception:
                pass
            result.groups.append(
                GroupSubmitResult(
                    buying_group_id=group.buying_group_id,
                    buying_group_name=group.buying_group_name,
                    tracking_numbers=group.tracking_numbers,
                    error=str(exc).strip() or exc.__class__.__name__,
                )
            )

    return result
