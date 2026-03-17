"""Store order import API — in-memory import flow.

The browser extension sends the normalized payload directly to the frontend via
URL hash.  The frontend calls these endpoints:

  POST /orders/diff   – read-only diff against an existing order
  POST /orders/apply  – create/update order, items, shipments in one shot
"""
from datetime import datetime, timezone
from typing import Any, Dict
import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user
from app.database import get_db
from app.models import (
    BuyingGroup,
    Item,
    Order,
    OrderPaymentMethod,
    PaymentMethod,
    Shipment,
    ShipmentItem,
    Store,
    StoreAccount,
    User,
)
from app.models.item import ItemStatus
from app.schemas.store_import import (
    DirectApplyBody,
    DirectApplyResponse,
    StoreOrderDiffResponse,
    StoreOrderImportPayload,
    BulkStoreOrderDiffRequest,
    BulkStoreOrderDiffResponse,
    BulkImportSessionCreate,
    BulkImportSessionResponse,
    BulkImportSessionPayloads,
)

router = APIRouter(prefix="/integrations/stores", tags=["integrations"])


# In-memory store for short-lived bulk import sessions.
_bulk_sessions: Dict[str, list[StoreOrderImportPayload]] = {}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get_or_create_store(db: Session, store_name: str, user_id: int | None) -> Store:
    store = db.query(Store).filter(Store.name == store_name).first()
    if store:
        return store
    store = Store(user_id=user_id, name=store_name)
    db.add(store)
    db.flush()
    return store


def _parse_external_order_fields(
    payload: StoreOrderImportPayload,
) -> tuple[str, str | None]:
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


def _resolve_store_name(store_raw: str) -> str:
    """Normalize the store string for DB lookup/creation."""
    name = store_raw.strip()
    if name.lower() == "walmart":
        name = "Walmart"
    return name


def _normalize_tracking_for_store(
    store_name: str,
    external_order_id: str | None,
    tracking_raw: Any,
) -> tuple[str | None, str | None]:
    """Return a canonical tracking key and optional Walmart-original tracking.

    For Walmart orders where the tracking number is a 20-digit value starting
    with 555, we treat the order id as the tracking number in our system and
    preserve the original Walmart tracking in notes.
    """
    if not isinstance(tracking_raw, str):
        return None, None
    tracking_trimmed = tracking_raw.strip()
    if not tracking_trimmed:
        return None, None

    store_lower = (store_name or "").strip().lower()
    if external_order_id and store_lower == "walmart":
        compact = tracking_trimmed.replace(" ", "")
        if len(compact) == 20 and compact.startswith("555"):
            return external_order_id, compact

    return tracking_trimmed, None


def _build_order_diff(
    db: Session,
    linked_order: Order | None,
    normalized: dict[str, Any],
    *,
    existing_items: list[Item] | None = None,
    existing_shipments: list[Shipment] | None = None,
) -> dict[str, Any]:
    """Build a structured diff between an existing order and the incoming import.

    For consumers that only care about a coarse view, the older 'changes' arrays are
    still present and contain field names.  For richer UIs, additional metadata is
    attached that includes before/after values for specific fields.
    """
    if not linked_order:
        return {"is_existing_order": False}

    if existing_items is None:
        existing_items = (
            db.query(Item)
            .options(joinedload(Item.shipment_items))
            .filter(Item.order_id == linked_order.id)
            .all()
        )

    if existing_shipments is None:
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
        inc_line_total = pricing.get("lineTotal")

        if name in existing_by_desc:
            existing = existing_by_desc.pop(name)
            changes: list[str] = []
            detailed_changes: list[dict[str, Any]] = []

            if existing["price_paid"] != inc_price:
                changes.append("price")
                detailed_changes.append(
                    {
                        "field": "price",
                        "before": existing["price_paid"],
                        "after": inc_price,
                    }
                )
            if existing["total_quantity"] != inc_qty:
                changes.append("quantity")
                detailed_changes.append(
                    {
                        "field": "quantity",
                        "before": existing["total_quantity"],
                        "after": inc_qty,
                    }
                )

            matched_items.append(
                {
                    "name": name,
                    "current": {
                        "quantity": existing["total_quantity"],
                        "price_paid": existing["price_paid"],
                        "statuses": sorted(existing["statuses"]),
                    },
                    "incoming": {
                        "quantity": inc_qty,
                        "unit_price": inc_price,
                        "line_total": inc_line_total,
                    },
                    "changes": changes,
                    "detailed_changes": detailed_changes,
                }
            )
        else:
            added_items.append(
                {
                    "name": name,
                    "quantity": inc_qty,
                    "unit_price": inc_price,
                    "line_total": inc_line_total,
                }
            )

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

    store_name = (normalized.get("store") or "").strip()
    incoming_ext = normalized.get("externalOrder") or {}
    external_order_id = str(incoming_ext.get("id") or "").strip()

    for inc_ship in incoming_shipments:
        tracking_raw = inc_ship.get("trackingNumber")
        tracking_key, _ = _normalize_tracking_for_store(
            store_name, external_order_id, tracking_raw
        )
        tracking_display = tracking_raw if isinstance(tracking_raw, str) else None
        delivery = inc_ship.get("deliveryDate")
        status_info = inc_ship.get("status") or {}
        status_msg = status_info.get("message")
        incoming_status_raw = status_info.get("rawStatusType")

        if tracking_key and tracking_key in existing_shipments_by_tracking:
            existing_s = existing_shipments_by_tracking[tracking_key]
            used_existing_tracking.add(tracking_key)
            ship_changes: list[str] = []
            detailed_ship_changes: list[dict[str, Any]] = []
            existing_delivered = (
                existing_s.delivered_at.isoformat() if existing_s.delivered_at else None
            )

            if existing_delivered != delivery:
                ship_changes.append("delivery_date")
                detailed_ship_changes.append(
                    {
                        "field": "delivery_date",
                        "before": existing_delivered,
                        "after": delivery,
                    }
                )

            # We only have incoming status information; still note it as a change
            # if there is any human-readable status message or code.
            if status_msg or incoming_status_raw:
                ship_changes.append("status")
                detailed_ship_changes.append(
                    {
                        "field": "status",
                        "before": None,
                        "after": status_msg or incoming_status_raw,
                    }
                )

            matched_shipments.append(
                {
                    "tracking_number": tracking_display,
                    "current": {"delivered_at": existing_delivered},
                    "incoming": {
                        "delivery_date": delivery,
                        "status_message": status_msg,
                        "status_code": incoming_status_raw,
                    },
                    "changes": ship_changes,
                    "detailed_changes": detailed_ship_changes,
                }
            )
        else:
            added_shipments.append(
                {
                    "tracking_number": tracking_display,
                    "delivery_date": delivery,
                    "status_message": status_msg,
                    "status_code": incoming_status_raw,
                }
            )

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


def _create_or_find_order(
    db: Session,
    normalized: dict[str, Any],
    external_order_id: str,
    store_name: str,
    current_user: User,
    store_account_id: int | None = None,
) -> Order:
    """Return an existing order matched by store_order_number, or create a new one."""
    existing = (
        db.query(Order)
        .filter(Order.store_order_number == external_order_id)
        .first()
    )
    if existing:
        return existing

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

    store = _get_or_create_store(db, _resolve_store_name(store_name), current_user.id)

    order = Order(
        user_id=current_user.id,
        store_id=store.id,
        store_account_id=store_account_id,
        buying_group_id=None,
        store_order_number=external_order_id,
        status="imported",
        purchase_date=purchase_date,
        shipping=None,
        sales_tax=None,
        notes="Imported from external store payload.",
    )
    db.add(order)
    db.flush()
    return order


def _apply_items_and_shipments(
    db: Session,
    normalized: dict[str, Any],
    store_name: str,
    order: Order,
    item_payouts: list[float | None] | None = None,
    external_order_id: str | None = None,
) -> None:
    """Create items and shipments from a normalized payload, skipping duplicates."""
    items = normalized.get("items") or []
    payouts = item_payouts or []
    shipments = normalized.get("shipments") or []

    existing_items = db.query(Item).filter(Item.order_id == order.id).all()

    existing_shipment_ids: set[int] = set()
    for ei in existing_items:
        for si in ei.shipment_items:
            existing_shipment_ids.add(si.shipment_id)

    existing_shipments: list[Shipment] = []
    if existing_shipment_ids:
        existing_shipments = list(
            db.query(Shipment).filter(Shipment.id.in_(existing_shipment_ids))
        )

    existing_shipments_by_id: dict[int, Shipment] = {
        s.id: s for s in existing_shipments
    }

    existing_item_keys: set[tuple[str, str | None]] = set()
    for ei in existing_items:
        desc = (ei.description or "").strip()
        if not ei.shipment_items:
            existing_item_keys.add((desc, None))
            continue
        for si in ei.shipment_items:
            s = existing_shipments_by_id.get(si.shipment_id)
            tracking = s.tracking_number if s and s.tracking_number else None
            existing_item_keys.add((desc, tracking))

    existing_shipments_by_tracking: dict[str, Shipment] = {}
    for s in existing_shipments:
        if s.tracking_number:
            existing_shipments_by_tracking[s.tracking_number] = s

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
        tracking_raw = src.get("trackingNumber")
        tracking_key, walmart_original = _normalize_tracking_for_store(
            store_name, external_order_id, tracking_raw
        )
        if tracking_key and tracking_key in existing_shipments_by_tracking:
            shipment = existing_shipments_by_tracking[tracking_key]
            if walmart_original:
                base_notes = (shipment.notes or "").rstrip()
                note_line = f"Walmart tracking: {walmart_original}"
                if note_line not in base_notes.splitlines():
                    shipment.notes = (
                        f"{base_notes}\n{note_line}" if base_notes else note_line
                    )
            return shipment
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
            tracking_number=tracking_key,
            shipped_at=None,
            delivered_at=delivered_at,
            notes=f"Imported from store '{store_name}'.",
        )
        if walmart_original:
            base_notes = (shipment.notes or "").rstrip()
            note_line = f"Walmart tracking: {walmart_original}"
            shipment.notes = f"{base_notes}\n{note_line}" if base_notes else note_line
        db.add(shipment)
        db.flush()
        shipments_created[shipment_id] = shipment
        if tracking_key:
            existing_shipments_by_tracking[tracking_key] = shipment
        return shipment

    for item_index, item_data in enumerate(items):
        name = (item_data.get("name") or "").strip()

        payout = payouts[item_index] if item_index < len(payouts) else None

        pricing = item_data.get("pricing") or {}
        unit_price = pricing.get("unitPrice")
        shipments_slices = item_data.get("shipments") or []

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
            if (name, None) in existing_item_keys:
                continue
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
            existing_item_keys.add((name, None))
            continue

        for slice_data in shipments_slices:
            shipment_id = slice_data.get("shipmentId")
            src = {}
            if isinstance(shipment_id, str):
                src = shipments_by_id.get(shipment_id) or {}
            tracking_for_slice = src.get("trackingNumber")
            tracking_key, _ = _normalize_tracking_for_store(
                store_name, external_order_id, tracking_for_slice
            )

            key = (name, tracking_key)
            if key in existing_item_keys:
                continue

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
            existing_item_keys.add(key)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/orders/diff", response_model=StoreOrderDiffResponse)
def compute_order_diff(
    data: StoreOrderImportPayload,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Read-only diff: compare an incoming payload against an existing order."""
    external_order_id, _ = _parse_external_order_fields(data)
    normalized: dict[str, Any] = data.model_dump(mode="json")

    linked_order: Order | None = (
        db.query(Order)
        .filter(Order.store_order_number == external_order_id)
        .first()
    )

    diff = _build_order_diff(db, linked_order, normalized)
    return StoreOrderDiffResponse(diff=diff)


@router.post("/orders/diff-bulk", response_model=BulkStoreOrderDiffResponse)
def compute_order_diff_bulk(
    body: BulkStoreOrderDiffRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Read-only bulk diff: compute diffs for many payloads in one request."""
    orders = body.orders or []
    if not orders:
        raise HTTPException(status_code=400, detail="At least one order payload is required.")

    normalized_payloads: list[dict[str, Any]] = []
    external_ids: list[str] = []
    for p in orders:
        external_order_id, _ = _parse_external_order_fields(p)
        external_ids.append(external_order_id)
        normalized_payloads.append(p.model_dump(mode="json"))

    linked_orders: list[Order] = (
        db.query(Order).filter(Order.store_order_number.in_(external_ids)).all()
        if external_ids
        else []
    )
    linked_by_external: dict[str, Order] = {
        (o.store_order_number or ""): o for o in linked_orders if o.store_order_number
    }

    linked_order_ids = [o.id for o in linked_orders]
    items_by_order_id: dict[int, list[Item]] = {}
    shipments_by_order_id: dict[int, list[Shipment]] = {}

    if linked_order_ids:
        all_items: list[Item] = (
            db.query(Item)
            .options(joinedload(Item.shipment_items))
            .filter(Item.order_id.in_(linked_order_ids))
            .all()
        )
        for it in all_items:
            items_by_order_id.setdefault(it.order_id, []).append(it)

        shipment_ids: set[int] = set()
        for it in all_items:
            for si in it.shipment_items:
                shipment_ids.add(si.shipment_id)

        shipments_by_id: dict[int, Shipment] = {}
        if shipment_ids:
            all_shipments: list[Shipment] = (
                db.query(Shipment).filter(Shipment.id.in_(shipment_ids)).all()
            )
            shipments_by_id = {s.id: s for s in all_shipments}

        # Map shipments back to orders using the item's shipment_items.
        for order_id, its in items_by_order_id.items():
            seen: set[int] = set()
            out: list[Shipment] = []
            for it in its:
                for si in it.shipment_items:
                    sid = si.shipment_id
                    if sid in seen:
                        continue
                    seen.add(sid)
                    s = shipments_by_id.get(sid)
                    if s:
                        out.append(s)
            shipments_by_order_id[order_id] = out

    diffs: list[dict[str, Any]] = []
    for external_id, normalized in zip(external_ids, normalized_payloads):
        linked = linked_by_external.get(external_id)
        pre_items = items_by_order_id.get(linked.id, []) if linked else None
        pre_shipments = shipments_by_order_id.get(linked.id, []) if linked else None
        diffs.append(
            _build_order_diff(
                db,
                linked,
                normalized,
                existing_items=pre_items,
                existing_shipments=pre_shipments,
            )
        )

    return BulkStoreOrderDiffResponse(diffs=diffs)


@router.post("/orders/apply", response_model=DirectApplyResponse)
def apply_store_order_direct(
    body: DirectApplyBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create or update an order directly from a normalized payload (no staging row)."""
    payload = body.payload
    external_order_id, _ = _parse_external_order_fields(payload)
    normalized: dict[str, Any] = payload.model_dump(mode="json")

    store_account_id = body.store_account_id
    if store_account_id is not None:
        account = db.query(StoreAccount).filter(StoreAccount.id == store_account_id).first()
        if not account:
            raise HTTPException(status_code=400, detail="Store account not found")

    buying_group_id = body.buying_group_id
    if buying_group_id is not None:
        group = db.query(BuyingGroup).filter(BuyingGroup.id == buying_group_id).first()
        if not group:
            raise HTTPException(status_code=400, detail="Buying group not found")

    order = _create_or_find_order(
        db, normalized, external_order_id, payload.store, current_user, store_account_id
    )
    if buying_group_id is not None:
        order.buying_group_id = buying_group_id

    _apply_items_and_shipments(
        db, normalized, payload.store, order, body.item_payouts, external_order_id
    )

    payment_methods_payload = body.payment_methods
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

    db.commit()
    return DirectApplyResponse(order_id=order.id)


@router.post("/orders/bulk-session", response_model=BulkImportSessionResponse)
def create_bulk_import_session(
    body: BulkImportSessionCreate,
):
    """Create an in-memory bulk import session for multiple normalized payloads.

    The browser extension POSTs the normalized orders here and receives a short token
    that the frontend can use to fetch the payloads without putting them in the URL.
    """
    if not body.orders:
        raise HTTPException(status_code=400, detail="At least one order payload is required.")

    # Generate a short, URL-safe token. This is not security-critical; it only
    # references in-memory data and still requires a logged-in user to consume.
    token = secrets.token_urlsafe(16)
    _bulk_sessions[token] = body.orders
    return BulkImportSessionResponse(token=token)


@router.get("/orders/bulk-session/{token}", response_model=BulkImportSessionPayloads)
def get_bulk_import_session(
    token: str,
):
    """Resolve a bulk import session token into the original payloads."""
    payloads = _bulk_sessions.get(token)
    if not payloads:
        raise HTTPException(status_code=404, detail="Bulk import session not found or expired.")
    return BulkImportSessionPayloads(orders=payloads)
