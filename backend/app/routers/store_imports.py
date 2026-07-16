"""Store order import API — in-memory import flow.

The browser extension sends the normalized payload directly to the frontend via
URL hash.  The frontend calls these endpoints:

  POST /orders/diff   – read-only diff against an existing order
  POST /orders/apply  – create/update order, items, shipments in one shot
"""
import re
from datetime import datetime, timezone
from typing import Any, Dict
import secrets
import math
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
from app.utils.invoices import prewarm_invoice_pdf, render_invoice_pdf

router = APIRouter(prefix="/integrations/stores", tags=["integrations"])


# In-memory store for short-lived bulk import sessions.
_bulk_sessions: Dict[str, list[StoreOrderImportPayload]] = {}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _parse_order_date_str(value: str | None) -> datetime | None:
    """Parse store orderDate into a timezone-aware datetime for Order.purchase_date."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return datetime.strptime(raw, "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
            dt = dt.replace(hour=12)
        return dt
    except ValueError:
        pass
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(hour=12, tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _normalize_iso_datetime_str(value: str | None) -> str | None:
    """Return a canonical ISO datetime string for comparison.

    This avoids treating format-only differences (e.g. 'Z' vs '+00:00') or
    missing tzinfo as real changes when computing diffs.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    dt = _parse_order_date_str(value)
    if dt:
        return dt.isoformat()
    return value.strip()


def _normalize_iso_date_str(value: str | None) -> str | None:
    """Return a canonical YYYY-MM-DD string for date-only comparison.

    For shipment delivery dates we only care about the calendar date, not the
    exact timestamp or timezone offset, so we collapse any valid ISO datetime
    down to its date component.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        # If we can't parse it as a datetime, fall back to the raw string so
        # obviously different values still count as changes.
        return raw
    return dt.date().isoformat()


def _shipment_delivery_calendar_key(value: Any) -> str | None:
    """Normalize any store shipment delivery string to YYYY-MM-DD for comparison.

    Walmart often sends human-readable dates (e.g. \"Mar 15, 2026\") while the DB
    stores ISO datetimes. Comparing raw strings falsely flags perpetual diffs.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.date().isoformat()
    except ValueError:
        pass
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _delivery_string_to_utc_datetime(value: str | None) -> datetime | None:
    """Parse store deliveryDate into a timezone-aware datetime for Shipment.delivered_at."""
    key = _shipment_delivery_calendar_key(value)
    if not key:
        return None
    return datetime.strptime(key, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _incoming_shipment_status(status_info: dict[str, Any] | None) -> str | None:
    """Extract the status string we would persist from a normalized shipment slice."""
    if not status_info:
        return None
    status_message = status_info.get("message")
    status_type = status_info.get("statusType") or status_info.get("rawStatusType")
    if isinstance(status_message, str) and status_message.strip():
        return status_message.strip()
    if isinstance(status_type, str) and status_type.strip():
        return status_type.strip()
    return None


def _shipment_statuses_differ_for_diff(
    existing_status: str | None,
    incoming_status: str | None,
) -> bool:
    if not incoming_status:
        return False
    existing_norm = (existing_status or "").strip()
    return existing_norm != incoming_status


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
    elif name.lower() == "amazon":
        name = "Amazon"
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


def _walmart_tracking_from_notes(notes: str | None) -> str | None:
    if not notes:
        return None
    prefix = "Walmart tracking: "
    for line in notes.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            value = stripped[len(prefix) :].strip()
            return value or None
    return None


def _db_shipment_lookup_key(
    store_name: str,
    external_order_id: str | None,
    tracking_raw: Any,
) -> tuple[str | None, str | None, str | None]:
    """Return canonical tracking, optional Walmart original, and a DB match key.

    The DB match key is always derived from tracking data — never from the store's
    ephemeral shipmentId — so re-imports match existing FedEx/UPS/etc. shipments.
    """
    tracking_key, walmart_original = _normalize_tracking_for_store(
        store_name, external_order_id, tracking_raw
    )
    db_key = walmart_original or tracking_key
    return tracking_key, walmart_original, db_key


def _item_slice_key(
    store_name: str,
    external_order_id: str | None,
    tracking_raw: Any,
    shipment_id: str | None = None,
) -> tuple[str | None, str | None, str]:
    """Return tracking fields plus a dedup key for item/shipment slices within one import."""
    tracking_key, walmart_original, db_key = _db_shipment_lookup_key(
        store_name, external_order_id, tracking_raw
    )
    if db_key:
        return tracking_key, walmart_original, db_key
    if shipment_id:
        return tracking_key, walmart_original, shipment_id
    return tracking_key, walmart_original, ""


def _find_existing_shipment(
    by_key: dict[str, Shipment],
    *,
    db_key: str | None,
    tracking_key: str | None,
) -> Shipment | None:
    """Match an incoming shipment against existing DB rows by tracking."""
    if db_key and db_key in by_key:
        return by_key[db_key]
    if tracking_key and tracking_key in by_key:
        return by_key[tracking_key]
    return None


def _shipment_db_keys(shipment: Shipment) -> set[str]:
    keys: set[str] = set()
    walmart_original = _walmart_tracking_from_notes(shipment.notes)
    if walmart_original:
        keys.add(walmart_original)
    tracking = (shipment.tracking_number or "").strip()
    if tracking:
        keys.add(tracking)
    return keys


def _shipments_by_lookup_key(shipments: list[Shipment]) -> dict[str, Shipment]:
    by_key: dict[str, Shipment] = {}
    for shipment in shipments:
        for key in _shipment_db_keys(shipment):
            if key not in by_key:
                by_key[key] = shipment
    return by_key


def _normalize_price_for_diff(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def _prices_differ_for_diff(a: float | None, b: float | None) -> bool:
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    return abs(a - b) >= 0.005


def _coerce_status_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ("status", "state", "code", "type", "normalizedStatus", "rawStatusCode"):
            nested = value.get(key)
            if isinstance(nested, str):
                return nested
            if isinstance(nested, (int, float, bool)):
                return str(nested)
    return ""


def _status_text_indicates_canceled(value: Any) -> bool:
    s = _coerce_status_text(value).strip().lower()
    return bool(s) and ("canceled" in s or "cancelled" in s)


def _incoming_item_is_canceled(item_data: dict[str, Any]) -> bool:
    status = item_data.get("status")
    if isinstance(status, dict):
        if _status_text_indicates_canceled(status.get("normalizedStatus")):
            return True
        if _status_text_indicates_canceled(status.get("rawStatusCode")):
            return True
    elif _status_text_indicates_canceled(status):
        return True
    return False


def _normalized_payload_is_canceled(normalized: dict[str, Any]) -> bool:
    candidates: list[Any] = [
        normalized.get("status"),
        normalized.get("orderStatus"),
    ]
    external = normalized.get("externalOrder") or {}
    if isinstance(external, dict):
        candidates.extend(
            [
                external.get("status"),
                external.get("orderStatus"),
                external.get("fulfillmentStatus"),
                external.get("state"),
                external.get("statusType"),
            ]
        )

    for candidate in candidates:
        if _status_text_indicates_canceled(candidate):
            return True

    for shipment in normalized.get("shipments") or []:
        if not isinstance(shipment, dict):
            continue
        status_info = shipment.get("status") or {}
        if isinstance(status_info, dict):
            for key in ("normalizedStatus", "rawStatusType", "message"):
                if _status_text_indicates_canceled(status_info.get(key)):
                    return True

    for item in normalized.get("items") or []:
        if isinstance(item, dict) and _incoming_item_is_canceled(item):
            return True

    return False


def _mark_item_canceled(item: Item) -> None:
    if item.status == ItemStatus.CANCELED:
        return
    item.status = ItemStatus.CANCELED
    if item.canceled_at is None:
        item.canceled_at = datetime.now(timezone.utc)


def _apply_canceled_statuses(
    existing_items: list[Item],
    normalized: dict[str, Any],
    incoming_by_name: dict[str, dict[str, Any]] | None = None,
) -> None:
    order_canceled = _normalized_payload_is_canceled(normalized)
    if incoming_by_name is None:
        incoming_by_name = _aggregate_incoming_items_by_name(normalized.get("items") or [])

    for item in existing_items:
        desc = (item.description or "").strip()
        incoming = incoming_by_name.get(desc)
        item_incoming_canceled = bool(incoming and incoming.get("is_canceled"))
        if order_canceled or item_incoming_canceled:
            _mark_item_canceled(item)


def _aggregate_incoming_items_by_name(
    incoming_items: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Collapse duplicate line items (e.g. one per Walmart shipment group) by name."""
    by_name: dict[str, dict[str, Any]] = {}
    for inc_item in incoming_items:
        name = (inc_item.get("name") or "").strip()
        if not name:
            continue
        pricing = inc_item.get("pricing") or {}
        quantities = inc_item.get("quantities") or {}
        inc_qty = quantities.get("ordered") or 1
        inc_price = _normalize_price_for_diff(pricing.get("unitPrice"))
        inc_line_total = _normalize_price_for_diff(pricing.get("lineTotal"))

        if name not in by_name:
            by_name[name] = {
                "total_quantity": 0,
                "unit_price": inc_price,
                "line_total": 0.0 if inc_line_total is not None else None,
                "is_canceled": False,
            }
        by_name[name]["total_quantity"] += inc_qty
        if _incoming_item_is_canceled(inc_item):
            by_name[name]["is_canceled"] = True
        if inc_line_total is not None:
            current_total = by_name[name]["line_total"]
            by_name[name]["line_total"] = (
                inc_line_total if current_total is None else current_total + inc_line_total
            )
    return by_name


def _sync_existing_item_quantities_and_prices(
    db: Session,
    normalized: dict[str, Any],
    existing_items: list[Item],
) -> None:
    """Reconcile aggregated line quantities/prices for an existing order.

    Shipment linking skips rows that already exist, so quantity and unit-price
    changes detected in the diff would otherwise never be persisted.
    """
    incoming_by_name = _aggregate_incoming_items_by_name(normalized.get("items") or [])
    if not incoming_by_name:
        return

    groups: dict[str, list[Item]] = {}
    for item in existing_items:
        desc = (item.description or "").strip()
        if desc:
            groups.setdefault(desc, []).append(item)

    for name, incoming in incoming_by_name.items():
        group = groups.get(name)
        if not group:
            continue

        inc_qty = incoming["total_quantity"]
        inc_price = incoming["unit_price"]

        if inc_price is not None:
            for item in group:
                item.price_paid = inc_price

        current_total = sum(i.quantity or 1 for i in group)
        if inc_qty == current_total:
            continue

        if inc_qty > current_total:
            target = min(group, key=lambda i: (bool(i.shipment_items), i.id))
            target.quantity = (target.quantity or 1) + (inc_qty - current_total)
            continue

        excess = current_total - inc_qty
        for item in sorted(
            group,
            key=lambda i: (bool(i.shipment_items), bool(i.payment_line_items), i.id),
            reverse=True,
        ):
            if excess <= 0:
                break
            qty = item.quantity or 1
            if item.payment_line_items:
                reducible = max(0, qty - 1)
                take = min(excess, reducible)
                if take > 0:
                    item.quantity = qty - take
                    excess -= take
                continue
            if qty <= excess:
                excess -= qty
                db.delete(item)
                if item in existing_items:
                    existing_items.remove(item)
            else:
                item.quantity = qty - excess
                excess = 0


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
                "price_paid": _normalize_price_for_diff(item.price_paid),
                "statuses": set(),
            }
        existing_by_desc[desc]["total_quantity"] += item.quantity
        if item.status:
            existing_by_desc[desc]["statuses"].add(
                item.status.value if hasattr(item.status, "value") else str(item.status)
            )

    incoming_by_name = _aggregate_incoming_items_by_name(normalized.get("items") or [])
    order_is_canceled = _normalized_payload_is_canceled(normalized)
    matched_items: list[dict[str, Any]] = []
    added_items: list[dict[str, Any]] = []

    for name, incoming in incoming_by_name.items():
        inc_qty = incoming["total_quantity"]
        inc_price = incoming["unit_price"]
        inc_line_total = incoming["line_total"]

        if name in existing_by_desc:
            existing = existing_by_desc.pop(name)
            changes: list[str] = []
            detailed_changes: list[dict[str, Any]] = []

            if _prices_differ_for_diff(existing["price_paid"], inc_price):
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

            incoming_canceled = order_is_canceled or incoming.get("is_canceled", False)
            non_canceled_statuses = existing["statuses"] - {"canceled"}
            if incoming_canceled and non_canceled_statuses:
                changes.append("status")
                detailed_changes.append(
                    {
                        "field": "status",
                        "before": sorted(existing["statuses"]),
                        "after": ["canceled"],
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

    if order_is_canceled:
        for entry in unmatched_existing:
            if set(entry["statuses"]) - {"canceled"}:
                entry["changes"] = ["status"]

    # --- Shipment comparison (match by Walmart original tracking or tracking number) ---
    existing_shipments_by_lookup_key = _shipments_by_lookup_key(existing_shipments)

    incoming_shipments = normalized.get("shipments") or []
    matched_shipments: list[dict[str, Any]] = []
    added_shipments: list[dict[str, Any]] = []
    used_existing_lookup_keys: set[str] = set()
    used_existing_shipment_ids: set[int] = set()

    store_name = (normalized.get("store") or "").strip()
    incoming_ext = normalized.get("externalOrder") or {}
    external_order_id = str(incoming_ext.get("id") or "").strip()

    for inc_ship in incoming_shipments:
        tracking_raw = inc_ship.get("trackingNumber")
        tracking_key, _, db_key = _db_shipment_lookup_key(
            store_name, external_order_id, tracking_raw
        )

        tracking_display = tracking_raw if isinstance(tracking_raw, str) else None
        delivery = inc_ship.get("deliveryDate")
        status_info = inc_ship.get("status") or {}
        incoming_status = _incoming_shipment_status(status_info)

        existing_s = _find_existing_shipment(
            existing_shipments_by_lookup_key,
            db_key=db_key,
            tracking_key=tracking_key,
        )
        if existing_s is not None:
            if existing_s.id in used_existing_shipment_ids:
                continue
            used_existing_shipment_ids.add(existing_s.id)
            for matched_key in _shipment_db_keys(existing_s):
                used_existing_lookup_keys.add(matched_key)
            ship_changes: list[str] = []
            detailed_ship_changes: list[dict[str, Any]] = []
            existing_delivered_raw = (
                existing_s.delivered_at.isoformat() if existing_s.delivered_at else None
            )
            existing_delivered_date = _shipment_delivery_calendar_key(existing_delivered_raw)
            incoming_delivered_date = _shipment_delivery_calendar_key(
                delivery if isinstance(delivery, str) else None
            )

            # Only flag delivery when the import supplies a parseable date that
            # differs from what we already have. Missing incoming dates should
            # not look like a change once delivered_at is already stored.
            delivery_changed = (
                incoming_delivered_date is not None
                and existing_delivered_date != incoming_delivered_date
            )
            if delivery_changed:
                ship_changes.append("delivery_date")
                detailed_ship_changes.append(
                    {
                        "field": "delivery_date",
                        "before": existing_delivered_raw,
                        "after": delivery,
                    }
                )

            delivery_dates_locked = (
                existing_delivered_date is not None
                and incoming_delivered_date is not None
                and existing_delivered_date == incoming_delivered_date
            )
            status_changed = (
                not delivery_dates_locked
                and _shipment_statuses_differ_for_diff(
                    existing_s.status,
                    incoming_status,
                )
            )
            if status_changed:
                ship_changes.append("status")
                detailed_ship_changes.append(
                    {
                        "field": "status",
                        "before": existing_s.status,
                        "after": incoming_status,
                    }
                )

            matched_shipments.append(
                {
                    "tracking_number": tracking_display,
                    "current": {
                        "delivered_at": existing_delivered_raw,
                        "status": existing_s.status,
                    },
                    "incoming": {
                        "delivery_date": delivery,
                        "status_message": status_info.get("message"),
                        "status_code": status_info.get("rawStatusType")
                        or status_info.get("statusType"),
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
                    "status_message": status_info.get("message"),
                    "status_code": status_info.get("rawStatusType")
                    or status_info.get("statusType"),
                }
            )

    unmatched_existing_shipments = []
    for s in existing_shipments:
        db_keys = _shipment_db_keys(s)
        if db_keys and not db_keys.intersection(used_existing_lookup_keys):
            unmatched_existing_shipments.append(
                {
                    "tracking_number": s.tracking_number,
                    "delivered_at": s.delivered_at.isoformat() if s.delivered_at else None,
                }
            )

    # --- Order-level comparison ---
    order_changes: dict[str, Any] = {}
    incoming_ext = normalized.get("externalOrder") or {}
    incoming_date_raw = incoming_ext.get("orderDate")
    existing_date_raw = (
        linked_order.purchase_date.isoformat() if linked_order.purchase_date else None
    )
    existing_date = _normalize_iso_datetime_str(existing_date_raw)
    incoming_date = _normalize_iso_datetime_str(incoming_date_raw)
    if existing_date != incoming_date:
        order_changes["purchase_date"] = {
            "current": existing_date_raw,
            "incoming": incoming_date_raw,
        }

    incoming_discount = normalized.get("orderDiscount")
    incoming_discount_num: float | None = None
    if isinstance(incoming_discount, (int, float)) and math.isfinite(incoming_discount):
        incoming_discount_num = float(incoming_discount)
    existing_discount_num = float(linked_order.order_discount) if linked_order.order_discount is not None else 0.0
    if incoming_discount_num is not None and abs(existing_discount_num - incoming_discount_num) >= 0.005:
        order_changes["order_discount"] = {
            "current": existing_discount_num,
            "incoming": incoming_discount_num,
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
        "current_order": {
            "buying_group_id": linked_order.buying_group_id,
            "store_account_id": linked_order.store_account_id,
        },
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
        if store_account_id is not None and existing.store_account_id is None:
            existing.store_account_id = store_account_id
        return existing

    external_order = normalized.get("externalOrder") or {}
    order_date_raw = external_order.get("orderDate")
    purchase_date = (
        _parse_order_date_str(order_date_raw)
        if isinstance(order_date_raw, str)
        else None
    )

    store = _get_or_create_store(db, _resolve_store_name(store_name), current_user.id)

    incoming_discount = normalized.get("orderDiscount")
    order_discount: float = 0.0
    if isinstance(incoming_discount, (int, float)) and math.isfinite(incoming_discount):
        order_discount = float(incoming_discount)

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
        order_discount=order_discount,
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
    *,
    existing_order: bool = False,
) -> None:
    """Create items and shipments from a normalized payload, skipping duplicates.

    When *existing_order* is True, shipment tracking/status is updated and
    tracking is attached to existing line items — splitting lines when a
    shipment slice quantity is less than the matched item quantity.
    """
    items = normalized.get("items") or []
    payouts = item_payouts or []
    shipments = normalized.get("shipments") or []

    existing_items = (
        db.query(Item)
        .options(joinedload(Item.shipment_items))
        .filter(Item.order_id == order.id)
        .all()
    )

    if existing_items:
        existing_order = True

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

    existing_item_keys: set[tuple[str, str]] = set()
    for ei in existing_items:
        desc = (ei.description or "").strip()
        if not ei.shipment_items:
            existing_item_keys.add((desc, ""))
            continue
        for si in ei.shipment_items:
            s = existing_shipments_by_id.get(si.shipment_id)
            if not s:
                existing_item_keys.add((desc, ""))
                continue
            walmart_original = _walmart_tracking_from_notes(s.notes)
            if walmart_original:
                existing_item_keys.add((desc, walmart_original))
            tracking = (s.tracking_number or "").strip() if s.tracking_number else ""
            if tracking:
                existing_item_keys.add((desc, tracking))

    existing_shipments_by_lookup_key = _shipments_by_lookup_key(existing_shipments)
    pre_existing_item_ids: set[int] = {ei.id for ei in existing_items}

    shipments_by_id: dict[str, dict[str, Any]] = {}
    for s in shipments:
        sid = s.get("shipmentId")
        if isinstance(sid, str) and sid:
            shipments_by_id[sid] = s

    shipments_created: dict[str, Shipment] = {}
    used_existing_item_ids: set[int] = set()
    linked_item_ids: set[int] = set()

    def item_tracking_keys(item: Item) -> set[str]:
        if not item.shipment_items:
            return {""}
        keys: set[str] = set()
        for si in item.shipment_items:
            s = existing_shipments_by_id.get(si.shipment_id)
            if not s:
                keys.add("")
                continue
            keys.update(_shipment_db_keys(s) or {""})
        return keys

    def find_existing_item_for_tracking(
        name: str, db_key: str | None, tracking_key: str | None
    ) -> Item | None:
        candidates = [
            ei
            for ei in existing_items
            if (ei.description or "").strip() == name
            and ei.id not in used_existing_item_ids
            and ei.id in pre_existing_item_ids
        ]
        if not candidates:
            return None

        match_keys = {k for k in (db_key, tracking_key) if k}
        if match_keys:
            for ei in candidates:
                if match_keys.intersection(item_tracking_keys(ei)):
                    used_existing_item_ids.add(ei.id)
                    return ei

        for ei in candidates:
            if not ei.shipment_items and ei.id not in linked_item_ids:
                used_existing_item_ids.add(ei.id)
                return ei

        for ei in candidates:
            for si in ei.shipment_items:
                s = existing_shipments_by_id.get(si.shipment_id)
                if s and not (s.tracking_number or "").strip():
                    used_existing_item_ids.add(ei.id)
                    return ei

        if match_keys:
            for ei in candidates:
                if not match_keys.intersection(item_tracking_keys(ei)):
                    used_existing_item_ids.add(ei.id)
                    return ei

        return None

    def allocate_item_for_slice(item: Item, slice_qty: int, item_name: str) -> Item:
        """Keep *slice_qty* units on *item*; peel the rest into a new unlinked line."""
        qty = item.quantity or 1
        keep = min(max(slice_qty, 1), qty)
        if qty <= keep:
            return item
        item.quantity = keep
        remainder = Item(
            order_id=item.order_id,
            price_paid=item.price_paid,
            price_sold=item.price_sold,
            status=item.status,
            quantity=qty - keep,
            description=item.description,
            shipping=item.shipping,
            sales_tax=item.sales_tax,
        )
        db.add(remainder)
        db.flush()
        existing_items.append(remainder)
        existing_item_keys.add((item_name, ""))
        return item

    def link_item_to_shipment(item: Item, shipment: Shipment, *, shipped: bool) -> None:
        already_linked = any(si.shipment_id == shipment.id for si in item.shipment_items)
        if not already_linked:
            si = ShipmentItem(shipment_id=shipment.id, item_id=item.id)
            db.add(si)
            # Keep ORM collections in sync within this import transaction.
            item.shipment_items.append(si)
            existing_shipments_by_id[shipment.id] = shipment
            linked_item_ids.add(item.id)
            if shipped:
                item.status = ItemStatus.SHIPPED

    def _apply_shipment_fields(
        shipment: Shipment,
        *,
        tracking_key: str | None,
        delivery_raw: str | None,
        delivered_at: datetime | None,
        normalized_status: str | None,
        walmart_original: str | None,
    ) -> None:
        if tracking_key and not (shipment.tracking_number or "").strip():
            shipment.tracking_number = tracking_key
        if delivered_at is not None:
            old_key = _shipment_delivery_calendar_key(
                shipment.delivered_at.isoformat() if shipment.delivered_at else None
            )
            new_key = _shipment_delivery_calendar_key(delivery_raw)
            if new_key and old_key != new_key:
                shipment.delivered_at = delivered_at
        if normalized_status is not None and normalized_status != (shipment.status or "").strip():
            shipment.status = normalized_status
        if walmart_original:
            base_notes = (shipment.notes or "").rstrip()
            note_line = f"Walmart tracking: {walmart_original}"
            if note_line not in base_notes.splitlines():
                shipment.notes = (
                    f"{base_notes}\n{note_line}" if base_notes else note_line
                )
        for reg_key in _shipment_db_keys(shipment):
            existing_shipments_by_lookup_key[reg_key] = shipment

    def get_or_create_shipment_for_slice(
        shipment_id: str | None,
        item_to_link: Item | None = None,
    ) -> Shipment | None:
        if not shipment_id:
            return None
        if shipment_id in shipments_created:
            return shipments_created[shipment_id]
        src = shipments_by_id.get(shipment_id) or {}
        tracking_raw = src.get("trackingNumber")
        tracking_key, walmart_original = _normalize_tracking_for_store(
            store_name, external_order_id, tracking_raw
        )

        # Parse delivery date (if any) and shipment-level status from the
        # normalized shipment slice.
        delivery_raw = src.get("deliveryDate")
        delivered_at = (
            _delivery_string_to_utc_datetime(delivery_raw)
            if isinstance(delivery_raw, str)
            else None
        )

        status_info = src.get("status") or {}
        normalized_status = _incoming_shipment_status(status_info)

        if item_to_link:
            for si in item_to_link.shipment_items:
                s = existing_shipments_by_id.get(si.shipment_id)
                if s is not None and not (s.tracking_number or "").strip():
                    _apply_shipment_fields(
                        s,
                        tracking_key=tracking_key,
                        delivery_raw=delivery_raw if isinstance(delivery_raw, str) else None,
                        delivered_at=delivered_at,
                        normalized_status=normalized_status,
                        walmart_original=walmart_original,
                    )
                    return s

        # If we already have a shipment for this slice, update its fields.
        lookup_reg = walmart_original or tracking_key
        existing_shipment = _find_existing_shipment(
            existing_shipments_by_lookup_key,
            db_key=lookup_reg,
            tracking_key=tracking_key,
        )
        if existing_shipment is not None:
            _apply_shipment_fields(
                existing_shipment,
                tracking_key=tracking_key,
                delivery_raw=delivery_raw if isinstance(delivery_raw, str) else None,
                delivered_at=delivered_at,
                normalized_status=normalized_status,
                walmart_original=walmart_original,
            )
            return existing_shipment

        shipment = Shipment(
            user_id=order.user_id,
            tracking_number=tracking_key,
            status=normalized_status,
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
        for reg_key in _shipment_db_keys(shipment):
            existing_shipments_by_lookup_key[reg_key] = shipment
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
            quantity = item_data.get("quantities", {}).get("ordered") or 1
            key = (name, "", item_index, quantity)
            if key in existing_item_keys:
                continue
            if existing_order:
                continue
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
            existing_items.append(item)
            existing_item_keys.add(key)
            continue

        for slice_data in shipments_slices:
            shipment_id = slice_data.get("shipmentId")
            sid = shipment_id if isinstance(shipment_id, str) else None
            src = shipments_by_id.get(sid) or {} if sid else {}
            tracking_for_slice = src.get("trackingNumber")
            tracking_key, walmart_original, slice_key = _item_slice_key(
                store_name, external_order_id, tracking_for_slice, sid
            )
            db_key = walmart_original or tracking_key

            slice_qty = slice_data.get("quantity") or 1
            key = (name, slice_key, item_index, slice_qty)
            if key in existing_item_keys:
                get_or_create_shipment_for_slice(sid)
                continue

            existing_item = find_existing_item_for_tracking(name, db_key, tracking_key)
            if existing_item:
                item_keys = item_tracking_keys(existing_item)
                if db_key and db_key in item_keys:
                    get_or_create_shipment_for_slice(
                        sid,
                        item_to_link=existing_item,
                    )
                    existing_item_keys.add(key)
                    continue
                if tracking_key and tracking_key in item_keys:
                    get_or_create_shipment_for_slice(
                        sid,
                        item_to_link=existing_item,
                    )
                    existing_item_keys.add(key)
                    continue

                incoming_keys = {k for k in (db_key, tracking_key) if k}
                already_linked_elsewhere = (
                    (bool(existing_item.shipment_items) or existing_item.id in linked_item_ids)
                    and bool(incoming_keys)
                    and incoming_keys.isdisjoint(item_keys)
                )
                if already_linked_elsewhere and (existing_item.quantity or 1) <= slice_qty:
                    # Same product on separate shipment slices (e.g. two Amazon
                    # line items with different tracking). Reusing the matched
                    # row would violate shipment_items.item_id uniqueness.
                    used_existing_item_ids.discard(existing_item.id)
                    existing_item = None

            if existing_item:
                allocated_item = allocate_item_for_slice(
                    existing_item, slice_qty, name
                )
                shipment = get_or_create_shipment_for_slice(
                    sid,
                    item_to_link=allocated_item,
                )
                if shipment:
                    link_item_to_shipment(
                        allocated_item, shipment, shipped=has_tracking
                    )
                existing_item_keys.discard((name, ""))
                existing_item_keys.add(key)
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
            existing_items.append(item)
            shipment = get_or_create_shipment_for_slice(
                sid,
                item_to_link=item,
            )
            if shipment:
                link_item_to_shipment(item, shipment, shipped=has_tracking)
            existing_item_keys.add(key)

    # Second pass: update existing shipments' delivered_at / notes even when
    # there are no new items for a given tracking number. This ensures that
    # delivery date changes coming from the store payload are actually
    # persisted, so subsequent diff runs stop flagging the same change.
    for src in shipments:
        if not isinstance(src, dict):
            continue
        shipment_id = src.get("shipmentId")
        sid = shipment_id if isinstance(shipment_id, str) else None
        if existing_order:
            get_or_create_shipment_for_slice(sid)

        tracking_raw = src.get("trackingNumber")
        tracking_key, walmart_original, db_key = _db_shipment_lookup_key(
            store_name, external_order_id, tracking_raw
        )
        existing_shipment = _find_existing_shipment(
            existing_shipments_by_lookup_key,
            db_key=db_key,
            tracking_key=tracking_key,
        )
        if existing_shipment is None:
            # Do not create new shipments here; creation still happens via
            # get_or_create_shipment_for_slice when new items are imported.
            continue

        delivery_raw = src.get("deliveryDate")
        delivered_at = (
            _delivery_string_to_utc_datetime(delivery_raw)
            if isinstance(delivery_raw, str)
            else None
        )

        status_info = src.get("status") or {}
        normalized_status = _incoming_shipment_status(status_info)

        _apply_shipment_fields(
            existing_shipment,
            tracking_key=tracking_key,
            delivery_raw=delivery_raw if isinstance(delivery_raw, str) else None,
            delivered_at=delivered_at,
            normalized_status=normalized_status,
            walmart_original=walmart_original,
        )

    if existing_order:
        _sync_existing_item_quantities_and_prices(db, normalized, existing_items)
        _apply_canceled_statuses(existing_items, normalized)

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

    is_existing_order = (
        db.query(Order)
        .filter(Order.store_order_number == external_order_id)
        .first()
        is not None
    )

    order = _create_or_find_order(
        db, normalized, external_order_id, payload.store, current_user, store_account_id
    )

    if not is_existing_order:
        if buying_group_id is not None:
            order.buying_group_id = buying_group_id

        incoming_discount = normalized.get("orderDiscount")
        if isinstance(incoming_discount, (int, float)) and math.isfinite(incoming_discount):
            order.order_discount = float(incoming_discount)

    _apply_items_and_shipments(
        db,
        normalized,
        payload.store,
        order,
        body.item_payouts,
        external_order_id,
        existing_order=is_existing_order,
    )

    payment_methods_payload = body.payment_methods
    if payment_methods_payload is not None and not is_existing_order:
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

    if payload.invoiceHtml:
        filename = render_invoice_pdf(payload.invoiceHtml, order.id)
        if filename:
            order.invoice_pdf_path = filename

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
    """Resolve a bulk import session token into the original payloads.

    Also kicks off background PDF rendering for any captured invoices, so the
    files are (usually) ready by the time the user applies each order.
    """
    payloads = _bulk_sessions.get(token)
    if not payloads:
        raise HTTPException(status_code=404, detail="Bulk import session not found or expired.")

    for payload in payloads:
        if payload.invoiceHtml:
            prewarm_invoice_pdf(payload.invoiceHtml)

    return BulkImportSessionPayloads(orders=payloads)
