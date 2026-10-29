"""Source-owned payments and versioned allocation adjustments."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from app.auth import get_current_user
from app.database import get_db
from app.models import Payment, PaymentLineItem, User
from app.schemas.payment import PaymentRead
from app.payment_ledger import AllocationEdit, AllocationReset, adjust

router = APIRouter(prefix="/payments", tags=["payments"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=list[PaymentRead])
def list_payments(buying_group_id: int | None = Query(None), db: Session = Depends(get_db)):
    q = db.query(Payment).options(joinedload(Payment.line_items).joinedload(PaymentLineItem.item), joinedload(Payment.corrections))
    if buying_group_id is not None:
        q = q.filter(Payment.buying_group_id == buying_group_id)
    return q.order_by(Payment.payment_requested_at.desc(), Payment.id.desc()).all()


@router.get("/{payment_id}", response_model=PaymentRead)
def get_payment(payment_id: int, db: Session = Depends(get_db)):
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "Payment not found")
    return payment


@router.get("/{payment_id}/linking-workspace")
def get_linking_workspace(payment_id: int, db: Session = Depends(get_db)):
    from app.payment_workspace import linking_workspace
    return linking_workspace(db, payment_id)


@router.post("")
def create_payment():
    raise HTTPException(405, "Manual payment creation is disabled. Sync buying-group payments.")


@router.patch("/{payment_id}")
def update_payment(payment_id: int, data: dict, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    raise HTTPException(409, "Payment fields are owned by the buying group. Bank receipt requires a Monarch deposit link.")


@router.delete("/{payment_id}")
def delete_payment(payment_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    raise HTTPException(409, "Source and legacy payment history cannot be deleted.")


@router.post("/{payment_id}/line-items")
@router.patch("/{payment_id}/line-items/{line_item_id}")
@router.delete("/{payment_id}/line-items/{line_item_id}")
def legacy_line_mutation(payment_id: int, line_item_id: int | None = None):
    raise HTTPException(409, "Use versioned allocation adjustments on an imported payment.")


def add_payment_line_item(*args, **kwargs):
    raise HTTPException(409, "Use versioned allocation adjustments on an imported payment.")


remove_payment_line_item = add_payment_line_item
update_payment_line_item = add_payment_line_item


@router.patch("/{payment_id}/allocations", response_model=PaymentRead)
def adjust_allocations(payment_id: int, body: AllocationEdit, db: Session = Depends(get_db)):
    return adjust(db, payment_id, body)


@router.post("/{payment_id}/allocations/reset", response_model=PaymentRead)
def reset_allocations(payment_id: int, body: AllocationReset, db: Session = Depends(get_db)):
    return adjust(db, payment_id, body, reset=True)
