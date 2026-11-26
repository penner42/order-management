"""Manage each buying group's independently editable shipping addresses."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import BuyingGroup, BuyingGroupAddress, BuyingGroupAddressVariant
from app.schemas.buying_group_address import (
    SavedVariantDetails, SavedVariantRead, ShippingAddressDetails, ShippingAddressRead,
)

# Buying groups are shared among signed-in app users, matching the existing group API.
router = APIRouter(
    prefix="/{group_id}/addresses", dependencies=[Depends(get_current_user)],
)


def require_group(db: Session, group_id: int) -> None:
    if db.get(BuyingGroup, group_id) is None:
        raise HTTPException(404, "Buying group not found")


def get_address(db: Session, group_id: int, address_id: int, *, lock: bool = False) -> BuyingGroupAddress:
    query = select(BuyingGroupAddress).where(
        BuyingGroupAddress.id == address_id, BuyingGroupAddress.buying_group_id == group_id,
    )
    if lock:
        query = query.with_for_update()
    address = db.scalar(query)
    if address is None:
        raise HTTPException(404, "Shipping address not found")
    return address


@router.get("", response_model=list[ShippingAddressRead])
def list_addresses(group_id: int, db: Session = Depends(get_db)):
    require_group(db, group_id)
    return db.scalars(select(BuyingGroupAddress).where(
        BuyingGroupAddress.buying_group_id == group_id,
    ).order_by(BuyingGroupAddress.id)).all()


@router.post("", response_model=ShippingAddressRead, status_code=201)
def create_address(group_id: int, data: ShippingAddressDetails, db: Session = Depends(get_db)):
    require_group(db, group_id)
    address = BuyingGroupAddress(buying_group_id=group_id, details=data.model_dump())
    db.add(address)
    db.flush()
    result = ShippingAddressRead(id=address.id, details=data, variants=[])
    db.commit()
    return result


@router.put("/{address_id}", response_model=ShippingAddressRead)
def update_address(group_id: int, address_id: int, data: ShippingAddressDetails, db: Session = Depends(get_db)):
    address = get_address(db, group_id, address_id, lock=True)
    address.details = data.model_dump()
    result = ShippingAddressRead.model_validate(address)
    db.commit()
    return result


@router.delete("/{address_id}", status_code=204)
def delete_address(group_id: int, address_id: int, db: Session = Depends(get_db)):
    address = get_address(db, group_id, address_id, lock=True)
    db.delete(address)
    db.commit()


@router.post("/{address_id}/variants", response_model=SavedVariantRead, status_code=201)
def save_variant(group_id: int, address_id: int, data: SavedVariantDetails, db: Session = Depends(get_db)):
    address = get_address(db, group_id, address_id, lock=True)
    fields = data.model_dump(exclude={"label"})
    if any({key: variant.details[key] for key in fields} == fields for variant in address.variants):
        raise HTTPException(409, "This address variant is already saved")
    variant = BuyingGroupAddressVariant(address_id=address.id, details=data.model_dump())
    db.add(variant)
    db.flush()
    result = SavedVariantRead.model_validate(variant)
    db.commit()
    return result


@router.delete("/{address_id}/variants/{variant_id}", status_code=204)
def delete_variant(group_id: int, address_id: int, variant_id: int, db: Session = Depends(get_db)):
    address = get_address(db, group_id, address_id, lock=True)
    variant = next((variant for variant in address.variants if variant.id == variant_id), None)
    if variant is None:
        raise HTTPException(404, "Saved address variant not found")
    db.delete(variant)
    db.commit()
