"""Store order import API - generic multi-store integration (e.g. Walmart)."""
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import (
    BuyingGroup,
    Order,
    OrderPaymentMethod,
    Store,
    StoreAccount,
    StoreOrderImport,
    User,
    Shipment,
    ShipmentItem,
    Item,
    PaymentMethod,
)
from app.models.item import ItemStatus
from app.schemas.store_import import (
    StoreOrderImportApplyBody,
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


def _build_order_diff(
    db: Session, linked_order: Order | None, normalized: dict[str, Any]
) -> dict[str, Any]:
    """Build a structured diff between an existing order and the incoming import."""
    if not linked_order:
        return {"is_existing_order": False}

    existing_items = db.query(Item).filter(Item.order_id == linked_order.id).all()

    existing_shipment_ids: set[int] = set()
    for item in existing_items:
        for si in item.shipment_items:
            existing_shipment_ids.add(si.shipment_id)
    existing_shipments = (
        db.query(Shipment).filter(Shipment.id.in_(existing_shipment_ids)).all()
        if existing_shipment_ids
        else []
    )

    # --- Item comparison (aggregate by description since items may be split per shipment) ---
    existing_by_desc: dict[str, dict[str, Any]] = {}
    for item in existing_items:
        desc = (item.description or "").strip()
        if desc not in existing_by_desc:
            existing_by_desc[desc] = {
                "total_quantity": 0,
                "price_paid": float(item.price_paid) if item.price_paid is not None else None,
                "statuses": set(),
            }
        existing_by_desc[desc]["total_quantity"] += item.quantity
        if item.status:
            existing_by_desc[desc]["statuses"].add(
                item.status.value if hasattr(item.status, "value") else str(item.status)
            )

    incoming_items = normalized.get("items") or []
    matched_items: list[dict[str, Any]] = []
    added_items: list[dict[str, Any]] = []

    for inc_item in incoming_items:
        name = (inc_item.get("name") or "").strip()
        pricing = inc_item.get("pricing") or {}
        quantities = inc_item.get("quantities") or {}
        inc_qty = quantities.get("ordered") or 1
        inc_price = pricing.get("unitPrice")

        if name in existing_by_desc:
            existing = existing_by_desc.pop(name)
            changes: list[str] = []
            if existing["price_paid"] != inc_price:
                changes.append("price")
            if existing["total_quantity"] != inc_qty:
                changes.append("quantity")
            matched_items.append({
                "name": name,
                "current": {
                    "quantity": existing["total_quantity"],
                    "price_paid": existing["price_paid"],
                    "statuses": sorted(existing["statuses"]),
                },
                "incoming": {
                    "quantity": inc_qty,
                    "unit_price": inc_price,
                },
                "changes": changes,
            })
        else:
            added_items.append({
                "name": name,
                "quantity": inc_qty,
                "unit_price": inc_price,
            })

    unmatched_existing = [
        {
            "description": desc,
            "quantity": data["total_quantity"],
            "price_paid": data["price_paid"],
            "statuses": sorted(data["statuses"]),
        }
        for desc, data in existing_by_desc.items()
    ]

    # --- Shipment comparison (match by tracking number) ---
    existing_shipments_by_tracking: dict[str, Shipment] = {}
    for s in existing_shipments:
        if s.tracking_number:
            existing_shipments_by_tracking[s.tracking_number] = s

    incoming_shipments = normalized.get("shipments") or []
    matched_shipments: list[dict[str, Any]] = []
    added_shipments: list[dict[str, Any]] = []
    used_existing_tracking: set[str] = set()

    for inc_ship in incoming_shipments:
        tracking = inc_ship.get("trackingNumber")
        delivery = inc_ship.get("deliveryDate")
        status_info = inc_ship.get("status") or {}
        status_msg = status_info.get("message")

        if tracking and tracking in existing_shipments_by_tracking:
            existing_s = existing_shipments_by_tracking[tracking]
            used_existing_tracking.add(tracking)
            ship_changes: list[str] = []
            existing_delivered = (
                existing_s.delivered_at.isoformat() if existing_s.delivered_at else None
            )
            if existing_delivered != delivery:
                ship_changes.append("delivery_date")
            matched_shipments.append({
                "tracking_number": tracking,
                "current": {"delivered_at": existing_delivered},
                "incoming": {"delivery_date": delivery, "status_message": status_msg},
                "changes": ship_changes,
            })
        else:
            added_shipments.append({
                "tracking_number": tracking,
                "delivery_date": delivery,
                "status_message": status_msg,
            })

    unmatched_existing_shipments = [
        {
            "tracking_number": tracking,
            "delivered_at": s.delivered_at.isoformat() if s.delivered_at else None,
        }
        for tracking, s in existing_shipments_by_tracking.items()
        if tracking not in used_existing_tracking
    ]

    # --- Order-level comparison ---
    order_changes: dict[str, Any] = {}
    incoming_ext = normalized.get("externalOrder") or {}
    incoming_date = incoming_ext.get("orderDate")
    existing_date_str = (
        linked_order.purchase_date.isoformat() if linked_order.purchase_date else None
    )
    if existing_date_str != incoming_date:
        order_changes["purchase_date"] = {
            "current": existing_date_str,
            "incoming": incoming_date,
        }

    has_changes = bool(
        order_changes
        or any(m["changes"] for m in matched_items)
        or added_items
        or unmatched_existing
        or any(m["changes"] for m in matched_shipments)
        or added_shipments
    )

    return {
        "is_existing_order": True,
        "has_changes": has_changes,
        "order": order_changes,
        "items": {
            "matched": matched_items,
            "added": added_items,
            "unmatched_existing": unmatched_existing,
        },
        "shipments": {
            "matched": matched_shipments,
            "added": added_shipments,
            "unmatched_existing": unmatched_existing_shipments,
        },
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

    diff_json = _build_order_diff(db, linked_order, normalized_payload)

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
    db: Session,
    import_record: StoreOrderImport,
    current_user: User,
    store_account_id: int | None = None,
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
        store_account_id=store_account_id,
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
    item_payouts: list[float | None] | None = None,
) -> None:
    """Create items and shipments for an import, skipping duplicates for existing orders."""
    normalized: dict[str, Any] = import_record.normalized_payload_json or {}
    items = normalized.get("items") or []
    payouts = item_payouts or []
    shipments = normalized.get("shipments") or []

    # Build sets of existing data so we can skip duplicates against items that were
    # already on the order before this import was applied.
    existing_items = db.query(Item).filter(Item.order_id == order.id).all()
    existing_desc_set: set[str] = {(ei.description or "").strip() for ei in existing_items}

    existing_shipment_ids: set[int] = set()
    for ei in existing_items:
        for si in ei.shipment_items:
            existing_shipment_ids.add(si.shipment_id)
    existing_tracking_set: set[str] = set()
    if existing_shipment_ids:
        for s in db.query(Shipment).filter(Shipment.id.in_(existing_shipment_ids)):
            if s.tracking_number:
                existing_tracking_set.add(s.tracking_number)

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
        tracking = src.get("trackingNumber")
        if tracking and tracking in existing_tracking_set:
            return None
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
            tracking_number=tracking,
            shipped_at=None,
            delivered_at=delivered_at,
            notes=f"Imported from store '{import_record.store}'.",
        )
        db.add(shipment)
        db.flush()
        shipments_created[shipment_id] = shipment
        if tracking:
            existing_tracking_set.add(tracking)
        return shipment

    for item_index, item_data in enumerate(items):
        name = (item_data.get("name") or "").strip()

        # Skip only when this description already exists on the order *prior* to this
        # apply call. We intentionally do not add newly created descriptions to
        # existing_desc_set so that multiple imported lines with the same description
        # are all created.
        if name in existing_desc_set:
            continue

        payout = payouts[item_index] if item_index < len(payouts) else None

        pricing = item_data.get("pricing") or {}
        unit_price = pricing.get("unitPrice")
        shipments_slices = item_data.get("shipments") or []

        # If any shipment slice for this line has a tracking number, treat it as shipped.
        has_tracking = False
        for slice_data in shipments_slices:
            sid = slice_data.get("shipmentId")
            if isinstance(sid, str) and sid:
                src = shipments_by_id.get(sid) or {}
                tracking = src.get("trackingNumber")
                if isinstance(tracking, str) and tracking.strip():
                    has_tracking = True
                    break

        status = ItemStatus.SHIPPED if has_tracking else ItemStatus.PURCHASED

        if not shipments_slices:
            quantity = item_data.get("quantities", {}).get("ordered") or 1
            item = Item(
                order_id=order.id,
                price_paid=unit_price,
                price_sold=payout,
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
                price_sold=payout,
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
    body: StoreOrderImportApplyBody | None = None,
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

    store_account_id: int | None = body.store_account_id if body else None
    if store_account_id is not None:
        account = db.query(StoreAccount).filter(StoreAccount.id == store_account_id).first()
        if not account:
            raise HTTPException(status_code=400, detail="Store account not found")

    buying_group_id: int | None = body.buying_group_id if body else None
    if buying_group_id is not None:
        group = db.query(BuyingGroup).filter(BuyingGroup.id == buying_group_id).first()
        if not group:
            raise HTTPException(status_code=400, detail="Buying group not found")

    order = _create_or_link_order_for_import(
        db, import_record, current_user, store_account_id
    )
    if buying_group_id is not None:
        order.buying_group_id = buying_group_id
    item_payouts = body.item_payouts if body else None
    _apply_items_and_shipments_for_import(db, import_record, order, item_payouts)

    payment_methods_payload = body.payment_methods if body else None
    if payment_methods_payload is not None:
        seen_ids: set[int] = set()
        for pm in payment_methods_payload:
            if pm.payment_method_id in seen_ids:
                raise HTTPException(
                    status_code=400,
                    detail="Each payment method can only be used once per order.",
                )
            seen_ids.add(pm.payment_method_id)

        if seen_ids:
            existing_ids = {
                row.id
                for row in db.query(PaymentMethod.id).filter(
                    PaymentMethod.id.in_(seen_ids)
                )
            }
            missing = seen_ids - existing_ids
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail="One or more payment methods not found.",
                )

        for opm in list(order.order_payments):
            db.delete(opm)
        for pm in payment_methods_payload:
            db.add(
                OrderPaymentMethod(
                    order_id=order.id,
                    payment_method_id=pm.payment_method_id,
                    amount=pm.amount,
                )
            )

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

