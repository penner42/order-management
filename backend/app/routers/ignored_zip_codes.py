"""Ignored zip codes API."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import User, IgnoredZipCode
from app.schemas.ignored_zip_code import IgnoredZipCodeCreate, IgnoredZipCodeRead
from app.utils.ignored_zip_codes import normalize_zip_code

router = APIRouter(prefix="/ignored-zip-codes", tags=["ignored-zip-codes"])


@router.get("", response_model=list[IgnoredZipCodeRead])
def list_ignored_zip_codes(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(IgnoredZipCode).order_by(IgnoredZipCode.zip_code.asc()).all()


@router.post("", response_model=IgnoredZipCodeRead)
def create_ignored_zip_code(
    data: IgnoredZipCodeCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    normalized = normalize_zip_code(data.zip_code)
    existing = db.query(IgnoredZipCode).all()
    for row in existing:
        if normalize_zip_code(row.zip_code) == normalized:
            return row
    row = IgnoredZipCode(zip_code=data.zip_code.strip())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{zip_id}", status_code=204)
def delete_ignored_zip_code(
    zip_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    row = db.query(IgnoredZipCode).filter(IgnoredZipCode.id == zip_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Ignored zip code not found")
    db.delete(row)
    db.commit()
    return None
