"""Items API (order line items)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload, joinedload

from app.auth import get_current_user
from app.database import get_db
from app.models import Item, Order, Shipment, ShipmentItem, User, Payment, PaymentLineItem
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
from app.utils.buying_group_apis.submit_tracking import (
    build_parsefile_entry,
    mark_item_submitted,
    parsefile_group_ready,
    submit_parsefile_trackings,
    tracking_number_for_item,
)
from app.utils.dates import to_date_only

# Item status date fields: date-only (shipped_at/delivered_at live on Shipment; payment dates on Payment)
_ITEM_DATE_FIELDS = frozenset({
    "purchased_at", "submitted_at", "scanned_at",
    "canceled_at", "needs_return_at", "return_started_at",
    "return_sent_at", "return_received_at", "return_refunded_at",
    "lost_package_at",
})

# Statuses that are "earlier" than payment_requested; moving item to these removes it from its payment
_STATUSES_BEFORE_PAYMENT_REQUESTED = frozenset(
    {
        ItemStatus.PURCHASED,
        ItemStatus.SHIPPED,
        ItemStatus.SUBMITTED,
        ItemStatus.SCANNED,
    }
)


def _remove_item_from_payment_if_earlier_status(item: Item, db: Session) -> None:
    """If item is in an earlier-than-payment_requested status, remove from payment; delete payment if empty."""
    if item.status not in _STATUSES_BEFORE_PAYMENT_REQUESTED:
        return
    line_item = db.query(PaymentLineItem).filter(PaymentLineItem.item_id == item.id).first()
    if not line_item:
        return
    payment_id = line_item.payment_id
    db.delete(line_item)
    if db.query(PaymentLineItem).filter(PaymentLineItem.payment_id == payment_id).count() == 0:
        payment = db.query(Payment).filter(Payment.id == payment_id).first()
        if payment:
            db.delete(payment)

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
    for entry in data.updates:
        item = db.query(Item).filter(Item.id == entry.item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail=f"Item {entry.item_id} not found")
        payload = entry.model_dump(exclude_unset=True, exclude={"item_id"})
        for k, v in payload.items():
            if k == "delivered_at":
                continue  # applied to shipment below
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
        updated.append(item)
    for item in updated:
        _remove_item_from_payment_if_earlier_status(item, db)
    db.commit()
    for item in updated:
        db.refresh(item)
    return ItemBulkUpdateResponse(items=updated)


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
    for item in items:
        db.delete(item)
    db.commit()
    return None


@router.post("/{item_id}/split", response_model=ItemSplitResponse)
def split_item(item_id: int, data: ItemSplitRequest, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Split an item into two: keep_quantity stays on original, remainder becomes new item."""
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    qty = getattr(item, "quantity", 1)
    keep = data.keep_quantity
    if keep < 1 or keep >= qty:
        raise HTTPException(
            status_code=400,
            detail=f"keep_quantity must be between 1 and {qty - 1} (inclusive) for quantity {qty}",
        )
    split_off_qty = qty - keep
    item.quantity = keep
    new_item = Item(
        order_id=item.order_id,
        price_paid=item.price_paid,
        price_sold=item.price_sold,
        status=item.status,
        quantity=split_off_qty,
        description=item.description,
        shipping=item.shipping,
        sales_tax=item.sales_tax,
    )
    db.add(new_item)
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
    """Submit this item's tracking number to the order's buying-group API (Parsefile for now)."""
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
    ready, reason = parsefile_group_ready(group)
    if not ready:
        raise HTTPException(status_code=400, detail=reason or "Buying group API is not configured")

    def _http_detail(exc: BaseException, *, fallback: str) -> str:
        text = str(exc).strip()
        return text or fallback

    try:
        result = submit_parsefile_trackings(
            group,
            [build_parsefile_entry(item, order, tracking)],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=_http_detail(e, fallback="Invalid tracking submit request")) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=_http_detail(e, fallback="Buying group API request failed")) from e
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=_http_detail(e, fallback=f"Tracking submit failed ({e.__class__.__name__})"),
        ) from e

    mark_item_submitted(item)
    db.commit()
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
    for k, v in data.model_dump(exclude_unset=True).items():
        if k in _ITEM_DATE_FIELDS:
            setattr(item, k, to_date_only(v) if v is not None else None)
        else:
            setattr(item, k, v)
    _remove_item_from_payment_if_earlier_status(item, db)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{item_id}", status_code=204)
def delete_item(item_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(item)
    db.commit()
    return None
