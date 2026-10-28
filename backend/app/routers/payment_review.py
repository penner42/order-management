"""Payment sync, capabilities, and receipt evidence for allocation adjustments."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.auth import get_current_user
from app.database import get_db
from app.models import BuyingGroup
from app import payment_review as service
from app.payment_ledger import sync_group

router = APIRouter(prefix="/payment-review", tags=["payment review"], dependencies=[Depends(get_current_user)])


@router.get("/capabilities")
def capabilities(db: Session = Depends(get_db)):
    return [{**service.capability(g), "payment_sync_enabled": g.payment_sync_enabled,
             "last_run_at": g.payment_sync_last_run_at, "error": g.payment_sync_error}
            for g in db.query(BuyingGroup).order_by(BuyingGroup.name)]


@router.get("/groups/{group_id}")
def get_group(group_id: int, db: Session = Depends(get_db)):
    return service.group_view(db, group_id)


@router.post("/groups/{group_id}/refresh")
def refresh_group(group_id: int, db: Session = Depends(get_db)):
    return sync_group(db, group_id)


@router.patch("/drafts/{review_id}")
@router.post("/drafts/{review_id}/confirm")
def retired_review_mutation(review_id: int):
    raise HTTPException(410, "Payments import automatically. Adjust allocations on the imported payment.")
