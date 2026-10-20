"""Authenticated payment provider discovery and review endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.auth import get_current_user
from app.database import get_db
from app.models import BuyingGroup, PaymentSource
from app import payment_review as service
from app.payment_providers import provider_for
from app.schemas.payment_review import ReviewEdit, ReviewConfirm
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payment-review", tags=["payment review"], dependencies=[Depends(get_current_user)])


@router.get("/capabilities")
def capabilities(db: Session = Depends(get_db)):
    return [service.capability(g) for g in db.query(BuyingGroup).order_by(BuyingGroup.name).all()]


@router.get("/groups/{group_id}")
def get_group(group_id: int, db: Session = Depends(get_db)):
    return service.group_view(db, group_id)


@router.post("/groups/{group_id}/refresh")
def refresh_group(group_id: int, db: Session = Depends(get_db)):
    group = db.get(BuyingGroup, group_id)
    if not group:
        raise HTTPException(404, "Buying group not found")
    provider = provider_for(group)
    ready = service.capability(group)
    if not ready["ready"]:
        raise HTTPException(400, ready["reason"])
    # Do network I/O before acquiring write locks. A provider cannot mutate local payments.
    config_signature = (group.api_framework, group.base_url, group.api_email, group.api_password,
        group.api_username, group.bearer_token, group.api_user_id)
    def snapshot_signature():
        return db.query(PaymentSource.id, PaymentSource.revision, PaymentSource.present).filter_by(
            buying_group_id=group_id).order_by(PaymentSource.id).all()
    before = snapshot_signature()
    try:
        fetched = provider.fetch(group)
    except Exception:
        logger.warning("Payment history fetch failed for buying group %s (provider %s); previous snapshot preserved", group_id, provider.key)
        # Provider exceptions can include remote payloads; do not leak account data.
        raise HTTPException(502, "Could not read a complete payment history. Check the group's credentials and website, then retry. Existing review data was preserved.") from None
    db.rollback()
    current = service.lock_group(db, group_id)
    if before != snapshot_signature():
        raise HTTPException(409, "Another payment refresh completed while this one was running. Review the latest snapshot or retry.")
    if config_signature != (current.api_framework, current.base_url, current.api_email, current.api_password,
            current.api_username, current.bearer_token, current.api_user_id):
        raise HTTPException(409, "Group configuration changed during refresh. Try again.")
    return service.refresh(db, group_id, fetched, provider.key)


@router.patch("/drafts/{review_id}")
def edit_review(review_id: int, body: ReviewEdit, db: Session = Depends(get_db)):
    return service.save_review(db, review_id, body)


@router.post("/drafts/{review_id}/confirm")
def confirm_review(review_id: int, body: ReviewConfirm, db: Session = Depends(get_db)):
    return service.confirm_review(db, review_id, body.version, body.fingerprint)
