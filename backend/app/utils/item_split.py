"""Shared explicit line splitting; the caller owns the transaction."""
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models import Item, ShipmentItem
from app.models.item import ItemStatus
from app.schemas.item import ItemSplitRequest
from app.utils.dates import to_date_only


def split_item_record(db: Session, item: Item, data: ItemSplitRequest) -> Item:
    if item.payment_line_items:
        raise HTTPException(status_code=409, detail="Cannot split a line linked to a payment. Update the payment on the Payments page first.")
    qty = getattr(item, "quantity", 1)
    keep = data.keep_quantity
    if keep < 1 or keep >= qty:
        raise HTTPException(
            status_code=400,
            detail=f"keep_quantity must be between 1 and {qty - 1} (inclusive) for quantity {qty}",
        )
    split_off_qty = qty - keep
    item.quantity = keep
    split_off_status = data.split_off_status if data.split_off_status is not None else item.status
    now = datetime.now(timezone.utc)
    new_item = Item(
        order_id=item.order_id,
        import_line_key=item.import_line_key,
        external_sku=item.external_sku,
        product_url=item.product_url,
        price_paid=item.price_paid,
        price_sold=item.price_sold,
        status=split_off_status,
        quantity=split_off_qty,
        description=item.description,
        shipping=item.shipping,
        sales_tax=item.sales_tax,
        submission_id=item.submission_id,
        submission_api_id=item.submission_api_id,
        # Remainder is not received — do not carry receipt onto the new line.
        receipt_id=None,
        receipt_tracking_number=None,
        purchased_at=item.purchased_at,
        submitted_at=item.submitted_at,
        scanned_at=None if data.split_off_status is not None else item.scanned_at,
        needs_review_at=to_date_only(now) if split_off_status == ItemStatus.NEEDS_REVIEW else None,
    )
    db.add(new_item)
    db.flush()
    if data.copy_tracking:
        shipment_id = None
        for si in item.shipment_items or []:
            if si.shipment_id:
                shipment_id = si.shipment_id
                break
        if shipment_id is None:
            existing = db.query(ShipmentItem).filter(ShipmentItem.item_id == item.id).first()
            if existing:
                shipment_id = existing.shipment_id
        if shipment_id is not None:
            db.add(ShipmentItem(shipment_id=shipment_id, item_id=new_item.id))
    return new_item
