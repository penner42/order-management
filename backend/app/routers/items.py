"""Items API (order line items)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Item, User
from app.schemas.item import ItemCreate, ItemRead, ItemSplitRequest, ItemSplitResponse, ItemUpdate
from app.utils.dates import to_date_only

# Item status date fields: date-only (time doesn't matter)
_ITEM_DATE_FIELDS = frozenset({
    "purchased_at", "shipped_at", "submitted_at", "delivered_at", "scanned_at",
    "payment_requested_at", "payment_sent_at", "payment_received_at",
    "canceled_at", "needs_return_at", "return_started_at",
    "return_sent_at", "return_received_at", "return_refunded_at",
})

router = APIRouter(prefix="/items", tags=["items"])


@router.get("", response_model=list[ItemRead])
def list_items(order_id: int | None = None, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    q = db.query(Item)
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
    )
    db.add(new_item)
    db.commit()
    db.refresh(item)
    db.refresh(new_item)
    return ItemSplitResponse(kept=item, split_off=new_item)


@router.get("/{item_id}", response_model=ItemRead)
def get_item(item_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    item = db.query(Item).filter(Item.id == item_id).first()
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
