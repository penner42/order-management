"""Portals API."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.auth import get_current_user
from app.database import get_db
from app.models import User, Portal
from app.schemas.portal import PortalRead, PortalCreate, PortalUpdate

router = APIRouter(prefix="/portals", tags=["portals"])


@router.get("", response_model=list[PortalRead])
def list_portals(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Portal).all()


@router.post("", response_model=PortalRead)
def create_portal(data: PortalCreate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    portal = Portal(**data.model_dump())
    db.add(portal)
    db.commit()
    db.refresh(portal)
    return portal


@router.get("/{portal_id}", response_model=PortalRead)
def get_portal(portal_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    portal = db.query(Portal).filter(Portal.id == portal_id).first()
    if not portal:
        raise HTTPException(status_code=404, detail="Portal not found")
    return portal


@router.patch("/{portal_id}", response_model=PortalRead)
def update_portal(portal_id: int, data: PortalUpdate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    portal = db.query(Portal).filter(Portal.id == portal_id).first()
    if not portal:
        raise HTTPException(status_code=404, detail="Portal not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(portal, k, v)
    db.commit()
    db.refresh(portal)
    return portal


@router.delete("/{portal_id}", status_code=204)
def delete_portal(portal_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    portal = db.query(Portal).filter(Portal.id == portal_id).first()
    if not portal:
        raise HTTPException(status_code=404, detail="Portal not found")
    db.delete(portal)
    db.commit()
    return None
