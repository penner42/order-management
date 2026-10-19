"""Items API (order line items)."""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload, joinedload

from app.submission_policy import require_group_submissions_enabled
from app.auth import get_current_user
from app.database import get_db
from app.models import Item, Order, Shipment, ShipmentItem, User, PaymentLineItem
from app.models.item import ItemStatus
from app.schemas.item import (
    ItemBulkDeleteRequest,
    ItemBulkUpdateRequest,
    ItemBulkUpdateResponse,
    ItemCreate,
    ItemRead,
    ItemSplitRequest,
    ItemSplitResponse,
    ItemSubmitTrackingResponse,
    ItemUpdate,
)
from app.utils.item_split import split_item_record
from app.utils.receiving_links import apply_manual_link
from app.utils.buying_group_apis.submit_tracking import (
    find_shipped_items_sharing_tracking,
    group_ready,
    mark_item_submitted,
    submit_item_tracking_to_group,
    tracking_number_for_item,
)
from app.utils.dates import to_date_only

logger = logging.getLogger(__name__)

# Item status date fields: date-only (shipped_at/delivered_at live on Shipment; payment dates on Payment)
_ITEM_DATE_FIELDS = frozenset({
    "purchased_at", "submitted_at", "scanned_at",
    "canceled_at", "needs_review_at", "needs_return_at", "return_started_at",
    "return_sent_at", "return_received_at", "return_refunded_at",
    "lost_package_at",
})

router = APIRouter(prefix="/items", tags=["items"])


@router.get("", response_model=list[ItemRead])
def list_items(order_id: int | None = None, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    q = db.query(Item).options(
        selectinload(Item.payment_line_items).joinedload(PaymentLineItem.payment),
    )
    if order_id is not None:
        q = q.filter(Item.order_id == order_id)
    return q.order_by(Item.id).all()


def _normalize_item_dates(data: dict) -> dict:
    """Normalize item status date fields to date-only."""
    out = dict(data)
    for k in _ITEM_DATE_FIELDS:
        if k in out and out[k] is not None:
            out[k] = to_date_only(out[k])
    return out


@router.post("", response_model=ItemRead)
def create_item(data: ItemCreate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    item = Item(**_normalize_item_dates(data.model_dump()))
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.post("/bulk-update", response_model=ItemBulkUpdateResponse)
def bulk_update_items(
    data: ItemBulkUpdateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Update multiple items in a single request. delivered_at in entry is applied to the item's shipment."""
    updated: list[Item] = []
    newly_submitted: list[Item] = []
    for entry in data.updates:
        item = (
            db.query(Item)
            .filter(Item.id == entry.item_id)
            .options(
                selectinload(Item.shipment_items).joinedload(ShipmentItem.shipment),
                joinedload(Item.order).joinedload(Order.buying_group),
            )
            .first()
        )
        if not item:
            raise HTTPException(status_code=404, detail=f"Item {entry.item_id} not found")
        prev_status = item.status
        payload = entry.model_dump(
            exclude_unset=True,
            exclude={"item_id", "delivered_at", "warehouse_tracking", "receiving_group_id", "receiving_row_id"},
        )
        if entry.receiving_row_id or entry.receiving_group_id is not None:
            payload = apply_manual_link(db, item, entry, payload)
        elif entry.warehouse_tracking and "receipt_tracking_number" not in payload:
            # Compatibility for older receiving clients; never write shared notes.
            payload["receipt_tracking_number"] = entry.warehouse_tracking.strip() or None
        for k, v in payload.items():
            if k in _ITEM_DATE_FIELDS:
                setattr(item, k, to_date_only(v) if v is not None else None)
            else:
                setattr(item, k, v)
        if getattr(entry, "delivered_at", None) is not None:
            si = db.query(ShipmentItem).filter(ShipmentItem.item_id == item.id).first()
            if si:
                shipment = db.query(Shipment).filter(Shipment.id == si.shipment_id).first()
                if shipment:
                    shipment.delivered_at = to_date_only(entry.delivered_at)
        if (
            "status" in payload
            and item.status == ItemStatus.SUBMITTED
            and prev_status != ItemStatus.SUBMITTED
        ):
            newly_submitted.append(item)
        updated.append(item)
    db.commit()
    for item in updated:
        db.refresh(item)

    if newly_submitted:
        _log_manual_submitted_items(newly_submitted)

    return ItemBulkUpdateResponse(items=updated)


def _log_manual_submitted_items(items: list[Item]) -> None:
    """Write Groups log rows for manual (non-API) submit-shipment / bulk-submit."""
    from collections import defaultdict

    from app.routers.buying_groups import write_group_tracking_log
    from app.utils.buying_group_apis.submit_tracking import (
        append_submission_ids_to_message,
        tracking_number_for_item,
    )

    by_group: dict[int | None, list[Item]] = defaultdict(list)
    for item in items:
        order = item.order
        gid = order.buying_group_id if order else None
        by_group[gid].append(item)

    for _gid, group_items in by_group.items():
        order = group_items[0].order if group_items else None
        group = order.buying_group if order else None
        if group:
            group_name = (group.name or "").strip() or f"group {group.id}"
        else:
            group_name = "No buying group"

        ids_by_tn: dict[str, tuple[str | None, str | None]] = {}
        tracking_numbers: list[str] = []
        for item in group_items:
            tn = tracking_number_for_item(item) or f"item-{item.id}"
            tracking_numbers.append(tn)
            sid = (item.submission_id or "").strip() or None
            said = (item.submission_api_id or "").strip() or None
            if sid or said:
                ids_by_tn[tn] = (sid, said)

        msg = append_submission_ids_to_message(
            f"Manually marked {len(group_items)} item(s) submitted.",
            ids_by_tn,
            missing_note=(
                "Submission ID not provided."
                if not ids_by_tn
                else None
            ),
        )
        try:
            write_group_tracking_log(
                group=group,
                group_name=group_name,
                scheduled=False,
                level="updates",
                event_type="tracking_submitted",
                tracking_numbers=tracking_numbers or None,
                message=msg,
            )
        except Exception:
            logger.exception("Failed to write manual submit log for group %s", group_name)


@router.post("/bulk-delete", status_code=204)
def bulk_delete_items(
    data: ItemBulkDeleteRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Delete multiple items in a single request."""
    if not data.item_ids:
        return None
    items = db.query(Item).filter(Item.id.in_(data.item_ids)).all()
    found_ids = {item.id for item in items}
    missing = set(data.item_ids) - found_ids
    if missing:
        raise HTTPException(status_code=404, detail=f"Items not found: {sorted(missing)}")
    if any(item.payment_line_items for item in items):
        raise HTTPException(status_code=409, detail="Cannot delete lines linked to a payment. Update the payment on the Payments page first.")
    for item in items:
        db.delete(item)
    db.commit()
    return None


@router.post("/{item_id}/split", response_model=ItemSplitResponse)
def split_item(item_id: int, data: ItemSplitRequest, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Split an item into two: keep_quantity stays on original, remainder becomes new item."""
    item = (
        db.query(Item)
        .filter(Item.id == item_id)
        .options(selectinload(Item.shipment_items))
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    new_item = split_item_record(db, item, data)
    db.commit()
    db.refresh(item)
    db.refresh(new_item)
    return ItemSplitResponse(kept=item, split_off=new_item)


@router.post("/{item_id}/submit-tracking", response_model=ItemSubmitTrackingResponse)
def submit_item_tracking(
    item_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Submit this item's tracking number to the order's buying-group API."""
    require_group_submissions_enabled()
    item = (
        db.query(Item)
        .filter(Item.id == item_id)
        .options(
            selectinload(Item.payment_line_items).joinedload(PaymentLineItem.payment),
            selectinload(Item.shipment_items).joinedload(ShipmentItem.shipment),
            joinedload(Item.order).joinedload(Order.buying_group),
        )
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if item.status != ItemStatus.SHIPPED:
        raise HTTPException(status_code=400, detail="Item must be in shipped status to submit tracking")

    tracking = tracking_number_for_item(item)
    if not tracking:
        raise HTTPException(status_code=400, detail="Item has no tracking number")

    order = item.order
    if not order or not order.buying_group_id or not order.buying_group:
        raise HTTPException(status_code=400, detail="Order has no buying group")
    group = order.buying_group
    ready, reason = group_ready(group)
    if not ready:
        raise HTTPException(status_code=400, detail=reason or "Buying group API is not configured")

    def _http_detail(exc: BaseException, *, fallback: str) -> str:
        text = str(exc).strip()
        return text or fallback

    siblings = find_shipped_items_sharing_tracking(
        db,
        buying_group_id=int(order.buying_group_id),
        tracking=tracking,
    )
    if not any(s.id == item.id for s in siblings):
        siblings = [item, *siblings]

    try:
        result = submit_item_tracking_to_group(
            group,
            item,
            order,
            tracking,
            sibling_items=siblings,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=_http_detail(e, fallback="Invalid tracking submit request")) from e
    except RuntimeError as e:
        detail = _http_detail(e, fallback="Buying group API request failed")
        logger.warning("Tracking submit failed for item %s: %s", item_id, detail)
        # Credential / login problems are client config, not an upstream outage.
        lower = detail.casefold()
        if "invalid credentials" in lower or "login requires interactive verification" in lower:
            raise HTTPException(status_code=400, detail=detail) from e
        raise HTTPException(status_code=502, detail=detail) from e
    except Exception as e:
        detail = _http_detail(e, fallback=f"Tracking submit failed ({e.__class__.__name__})")
        logger.exception("Tracking submit failed for item %s: %s", item_id, detail)
        raise HTTPException(status_code=502, detail=detail) from e

    mark_ids = result.item_ids or [item.id]
    now = datetime.now(timezone.utc)
    items_to_mark = (
        db.query(Item)
        .filter(Item.id.in_(mark_ids))
        .filter(Item.status == ItemStatus.SHIPPED)
        .all()
    )
    display_id, api_id = (None, None)
    if result.submission_ids_by_tracking:
        # Single-tracking submit: use the (only) mapping for this tracking.
        for _tn, ids in result.submission_ids_by_tracking.items():
            display_id, api_id = ids
            break
        # Prefer exact tracking match when multiple are present.
        matched = result.submission_ids_by_tracking.get(tracking)
        if matched:
            display_id, api_id = matched
    for it in items_to_mark:
        mark_item_submitted(
            it,
            when=now,
            submission_id=display_id,
            submission_api_id=api_id,
        )
    db.commit()
    for it in items_to_mark:
        db.refresh(it)

    # Log Groups-category entry with submission id (same log page as batch submit).
    try:
        from app.routers.buying_groups import write_group_tracking_log
        from app.utils.buying_group_apis.submit_tracking import append_submission_ids_to_message

        # Prefer IDs persisted on items; fall back to the submit result map.
        ids_for_log: dict[str, tuple[str | None, str | None]] = {}
        for it in items_to_mark:
            sid = (it.submission_id or "").strip() or None
            said = (it.submission_api_id or "").strip() or None
            if sid or said:
                ids_for_log[tracking] = (sid, said)
                break
        if not ids_for_log and result.submission_ids_by_tracking:
            ids_for_log = dict(result.submission_ids_by_tracking)

        log_msg = append_submission_ids_to_message(
            result.message or "Submitted tracking number.",
            ids_for_log,
            missing_note=(
                "Submission ID not retrieved after submit."
                if not ids_for_log
                else None
            ),
        )
        write_group_tracking_log(
            group=group,
            group_name=(group.name or "").strip() or f"group {group.id}",
            scheduled=False,
            level="updates",
            event_type="tracking_submitted",
            tracking_numbers=[tracking],
            message=log_msg,
        )
    except Exception:
        logger.exception("Failed to write tracking submit log for item %s", item_id)

    updated_items = (
        db.query(Item)
        .filter(Item.id.in_(mark_ids))
        .options(selectinload(Item.payment_line_items).joinedload(PaymentLineItem.payment))
        .all()
    )
    item = next((it for it in updated_items if it.id == item_id), None)
    if not item:
        item = (
            db.query(Item)
            .filter(Item.id == item_id)
            .options(selectinload(Item.payment_line_items).joinedload(PaymentLineItem.payment))
            .first()
        )
    if not item:
        raise HTTPException(status_code=404, detail="Item not found after submit")
    return ItemSubmitTrackingResponse(
        item=item,
        items=updated_items or [item],
        message=result.message,
        affected=result.affected,
    )


@router.get("/{item_id}", response_model=ItemRead)
def get_item(item_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    item = (
        db.query(Item)
        .filter(Item.id == item_id)
        .options(selectinload(Item.payment_line_items).joinedload(PaymentLineItem.payment))
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.patch("/{item_id}", response_model=ItemRead)
def update_item(item_id: int, data: ItemUpdate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    payload = data.model_dump(exclude_unset=True)
    for k, v in payload.items():
        if k in _ITEM_DATE_FIELDS:
            setattr(item, k, to_date_only(v) if v is not None else None)
        else:
            setattr(item, k, v)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{item_id}", status_code=204)
def delete_item(item_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if item.payment_line_items:
        raise HTTPException(status_code=409, detail="Cannot delete a line linked to a payment. Update the payment on the Payments page first.")
    db.delete(item)
    db.commit()
    return None
