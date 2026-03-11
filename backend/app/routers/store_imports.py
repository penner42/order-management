"""Store order import API - generic multi-store integration (e.g. Walmart)."""
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Order, Store, StoreOrderImport, User, Shipment, ShipmentItem, Item
from app.models.item import ItemStatus
from app.schemas.store_import import (
    StoreOrderImportApplyResponse,
    StoreOrderImportCreate,
    StoreOrderImportListResponse,
    StoreOrderImportRead,
)


router = APIRouter(prefix="/integrations/stores", tags=["integrations"])


def _get_or_create_store(db: Session, store_name: str, user_id: int | None) -> Store:
    store = db.query(Store).filter(Store.name == store_name).first()
    if store:
        return store
    store = Store(user_id=user_id, name=store_name)
    db.add(store)
    db.flush()
    return store


def _parse_external_order_fields(payload: StoreOrderImportCreate) -> tuple[str, str | None]:
    ext = payload.externalOrder or {}
    external_order_id = str(ext.get("id") or "").strip()
    if not external_order_id:
        raise HTTPException(status_code=400, detail="externalOrder.id is required")
    external_order_url = ext.get("url")
    if isinstance(external_order_url, str):
        external_order_url = external_order_url.strip() or None
    else:
        external_order_url = None
    return external_order_id, external_order_url


def _build_diff_placeholder(normalized: dict[str, Any]) -> dict[str, Any]:
    """Placeholder diff; can be extended later with real comparisons."""
    return {
        "summary": "diff not implemented - this is a placeholder structure",
        "external_order_id": normalized.get("externalOrder", {}).get("id"),
        "store": normalized.get("store"),
    }


@router.post(
    "/orders/import",
    response_model=StoreOrderImportRead,
)
def create_store_order_import(
    data: StoreOrderImportCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create or overwrite a store order import from a normalized external payload."""
    external_order_id, external_order_url = _parse_external_order_fields(data)
    normalized_payload: dict[str, Any] = data.model_dump(mode="json")

    linked_order: Order | None = (
        db.query(Order)
        .filter(Order.store_order_number == external_order_id)
        .first()
    )

    diff_json = _build_diff_placeholder(normalized_payload)

    # Upsert: overwrite any existing import for this (store, external_order_id)
    import_record = (
        db.query(StoreOrderImport)
        .filter(
            StoreOrderImport.store == data.store,
            StoreOrderImport.external_order_id == external_order_id,
        )
        .first()
    )

    if import_record:
        import_record.external_order_url = external_order_url
        import_record.status = "pending"
        import_record.linked_order_id = linked_order.id if linked_order else None
        import_record.raw_payload_json = data.rawPayload or {}
        import_record.normalized_payload_json = normalized_payload
        import_record.diff_json = diff_json
        import_record.applied_at = None
        import_record.discarded_at = None
        import_record.applied_by_user_id = None
    else:
        import_record = StoreOrderImport(
            store=data.store,
            external_order_id=external_order_id,
            external_order_url=external_order_url,
            status="pending",
            linked_order_id=linked_order.id if linked_order else None,
            raw_payload_json=data.rawPayload or {},
            normalized_payload_json=normalized_payload,
            diff_json=diff_json,
        )
        db.add(import_record)

    db.commit()
    db.refresh(import_record)
    return import_record


@router.get(
    "/imports",
    response_model=StoreOrderImportListResponse,
)
def list_store_order_imports(
    status: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """List store order imports, optionally filtered by status (default: pending only)."""
    q = db.query(StoreOrderImport)
    if status:
        q = q.filter(StoreOrderImport.status == status)
    else:
        q = q.filter(StoreOrderImport.status == "pending")
    imports = q.order_by(StoreOrderImport.created_at.desc()).all()
    return StoreOrderImportListResponse(imports=imports)


@router.get(
    "/imports/{import_id}",
    response_model=StoreOrderImportRead,
)
def get_store_order_import(
    import_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    record = db.query(StoreOrderImport).filter(StoreOrderImport.id == import_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Import not found")
    return record


def _ensure_walmart_store_for_import(
    db: Session, import_record: StoreOrderImport, user_id: int | None
) -> Store:
    """Return the Store row corresponding to this import's store string."""
    # For now we use the literal store string as the Store.name (e.g. "walmart" -> "Walmart").
    # Normalize simple cases but avoid guessing beyond capitalization.
    store_name = import_record.store.strip()
    if store_name.lower() == "walmart":
        store_name = "Walmart"
    return _get_or_create_store(db, store_name, user_id)


def _create_or_link_order_for_import(
    db: Session, import_record: StoreOrderImport, current_user: User
) -> Order:
    """Ensure an Order exists for this import; create one when needed."""
    if import_record.linked_order_id:
        order = db.query(Order).filter(Order.id == import_record.linked_order_id).first()
        if order:
            return order

    normalized: dict[str, Any] = import_record.normalized_payload_json or {}
    external_order = normalized.get("externalOrder") or {}
    order_date_raw = external_order.get("orderDate")

    purchase_date: datetime | None = None
    if isinstance(order_date_raw, str) and order_date_raw.strip():
        try:
            purchase_date = datetime.fromisoformat(
                order_date_raw.replace("Z", "+00:00")
            )
        except ValueError:
            purchase_date = None
    if purchase_date and purchase_date.tzinfo is None:
        purchase_date = purchase_date.replace(tzinfo=timezone.utc)

    store = _ensure_walmart_store_for_import(
        db,
        import_record,
        current_user.id,
    )

    order = Order(
        user_id=current_user.id,
        store_id=store.id,
        store_account_id=None,
        buying_group_id=None,
        store_order_number=import_record.external_order_id,
        status="imported",
        purchase_date=purchase_date,
        shipping=None,
        sales_tax=None,
        notes="Imported from external store payload.",
    )
    db.add(order)
    db.flush()
    import_record.linked_order_id = order.id
    return order


def _apply_items_and_shipments_for_import(
    db: Session,
    import_record: StoreOrderImport,
    order: Order,
) -> None:
    """Create items and shipments for an import, splitting items per shipment slice."""
    normalized: dict[str, Any] = import_record.normalized_payload_json or {}
    items = normalized.get("items") or []
    shipments = normalized.get("shipments") or []

    # Index shipments by shipmentId for quick lookup
    shipments_by_id: dict[str, dict[str, Any]] = {}
    for s in shipments:
        sid = s.get("shipmentId")
        if isinstance(sid, str) and sid:
            shipments_by_id[sid] = s

    shipments_created: dict[str, Shipment] = {}

    def get_or_create_shipment_for_slice(shipment_id: str | None) -> Shipment | None:
        if not shipment_id:
            return None
        if shipment_id in shipments_created:
            return shipments_created[shipment_id]
        src = shipments_by_id.get(shipment_id) or {}
        delivered_at: datetime | None = None
        delivery_raw = src.get("deliveryDate")
        if isinstance(delivery_raw, str) and delivery_raw.strip():
            try:
                delivered_at = datetime.fromisoformat(
                    delivery_raw.replace("Z", "+00:00")
                )
            except ValueError:
                delivered_at = None
        shipment = Shipment(
            user_id=order.user_id,
            tracking_number=src.get("trackingNumber"),
            shipped_at=None,
            delivered_at=delivered_at,
            notes=f"Imported from store '{import_record.store}'.",
        )
        db.add(shipment)
        db.flush()
        shipments_created[shipment_id] = shipment
        return shipment

    for item_data in items:
        name = item_data.get("name") or ""
        pricing = item_data.get("pricing") or {}
        unit_price = pricing.get("unitPrice")
        status = ItemStatus.PURCHASED
        shipments_slices = item_data.get("shipments") or []

        if not shipments_slices:
            quantity = item_data.get("quantities", {}).get("ordered") or 1
            item = Item(
                order_id=order.id,
                price_paid=unit_price,
                price_sold=None,
                status=status,
                quantity=quantity,
                description=name,
                shipping=None,
                sales_tax=None,
            )
            db.add(item)
            continue

        for slice_data in shipments_slices:
            shipment_id = slice_data.get("shipmentId")
            quantity = slice_data.get("quantity") or 1
            item = Item(
                order_id=order.id,
                price_paid=unit_price,
                price_sold=None,
                status=status,
                quantity=quantity,
                description=name,
                shipping=None,
                sales_tax=None,
            )
            db.add(item)
            db.flush()
            shipment = get_or_create_shipment_for_slice(
                shipment_id if isinstance(shipment_id, str) else None
            )
            if shipment:
                db.add(
                    ShipmentItem(
                        shipment_id=shipment.id,
                        item_id=item.id,
                    )
                )


@router.post(
    "/imports/{import_id}/apply",
    response_model=StoreOrderImportApplyResponse,
)
def apply_store_order_import(
    import_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Apply a pending store order import: create/update order, items, and shipments."""
    import_record = (
        db.query(StoreOrderImport)
        .filter(StoreOrderImport.id == import_id)
        .first()
    )
    if not import_record:
        raise HTTPException(status_code=404, detail="Import not found")
    if import_record.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Import status must be 'pending' to apply (got '{import_record.status}').",
        )

    order = _create_or_link_order_for_import(db, import_record, current_user)
    _apply_items_and_shipments_for_import(db, import_record, order)

    import_record.status = "applied"
    import_record.applied_at = datetime.now(timezone.utc)
    import_record.applied_by_user_id = current_user.id

    db.commit()
    db.refresh(import_record)
    return StoreOrderImportApplyResponse(
        import_record=import_record,
        order_id=order.id,
    )


@router.post(
    "/imports/{import_id}/discard",
    response_model=StoreOrderImportRead,
)
def discard_store_order_import(
    import_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Discard a pending store order import without applying changes."""
    import_record = (
        db.query(StoreOrderImport)
        .filter(StoreOrderImport.id == import_id)
        .first()
    )
    if not import_record:
        raise HTTPException(status_code=404, detail="Import not found")
    if import_record.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Import status must be 'pending' to discard (got '{import_record.status}').",
        )
    import_record.status = "discarded"
    import_record.discarded_at = datetime.now(timezone.utc)
    import_record.applied_by_user_id = current_user.id
    db.commit()
    db.refresh(import_record)
    return import_record

