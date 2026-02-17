"""Shipments API."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Item, Shipment, ShipmentItem, User
from app.models.item import ItemStatus
from app.models.user import get_default_app_user_id
from app.schemas.shipment import ShipmentCreate, ShipmentRead, ShipmentUpdate
from app.utils.dates import to_date_only


router = APIRouter(prefix="/shipments", tags=["shipments"])


@router.get("", response_model=list[ShipmentRead])
def list_shipments(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Shipment).order_by(Shipment.created_at.desc()).all()


@router.post("", response_model=ShipmentRead)
def create_shipment(data: ShipmentCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    user_id = data.user_id or (current_user.id if current_user.role != "admin" else None) or get_default_app_user_id(db)
    shipped_at = to_date_only(data.shipped_at) if data.shipped_at is not None else None
    if data.tracking_number and shipped_at is None:
        shipped_at = to_date_only(datetime.now(timezone.utc))
    tracking_trimmed = (data.tracking_number or "").strip() or None
    existing = None
    if tracking_trimmed:
        existing = (
            db.query(Shipment)
            .filter(Shipment.tracking_number == tracking_trimmed)
            .first()
        )
    if existing:
        shipment = existing
        for item_id in data.item_ids or []:
            # Remove from any other shipment first
            old_si = db.query(ShipmentItem).filter(ShipmentItem.item_id == item_id).first()
            if old_si and old_si.shipment_id != shipment.id:
                old_ship_id = old_si.shipment_id
                db.delete(old_si)
                db.flush()
                remaining = db.query(ShipmentItem).filter(ShipmentItem.shipment_id == old_ship_id).count()
                if remaining == 0:
                    db.query(Shipment).filter(Shipment.id == old_ship_id).delete()
            if not db.query(ShipmentItem).filter(ShipmentItem.item_id == item_id).first():
                db.add(ShipmentItem(shipment_id=shipment.id, item_id=item_id))
                if data.tracking_number:
                    item = db.query(Item).filter(Item.id == item_id).first()
                    if item:
                        item.status = ItemStatus.SHIPPED
    else:
        shipment = Shipment(
            user_id=user_id,
            tracking_number=tracking_trimmed or data.tracking_number,
            shipped_at=shipped_at,
            notes=data.notes,
        )
        db.add(shipment)
        db.flush()
        for item_id in data.item_ids or []:
            db.query(ShipmentItem).filter(ShipmentItem.item_id == item_id).delete()
            si = ShipmentItem(shipment_id=shipment.id, item_id=item_id)
            db.add(si)
            if data.tracking_number:
                item = db.query(Item).filter(Item.id == item_id).first()
                if item:
                    item.status = ItemStatus.SHIPPED
    db.commit()
    db.refresh(shipment)
    return shipment


@router.get("/{shipment_id}", response_model=ShipmentRead)
def get_shipment(shipment_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    return shipment


@router.patch("/{shipment_id}", response_model=ShipmentRead)
def update_shipment(shipment_id: int, data: ShipmentUpdate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        if k == "shipped_at":
            setattr(shipment, k, to_date_only(v) if v is not None else None)
        elif k == "item_ids":
            # Replace shipment items; each item can only be in one shipment
            for si in list(shipment.shipment_items):
                db.delete(si)
            for item_id in v:
                db.query(ShipmentItem).filter(ShipmentItem.item_id == item_id).delete()
                db.add(ShipmentItem(shipment_id=shipment.id, item_id=item_id))
        else:
            setattr(shipment, k, v)
    db.commit()
    db.refresh(shipment)
    return shipment


@router.delete("/{shipment_id}", status_code=204)
def delete_shipment(shipment_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    db.delete(shipment)
    db.commit()
    return None
