"""Normalize repeated Costco purchase-line status records."""
import json
import re
from typing import Any


def deduplicate_costco_items(normalized: dict[str, Any]) -> dict[str, Any]:
    """Count a purchase once when Costco repeats it as returned/delivered.

    Require the same retailer line identity, product, quantity, price and
    shipment allocation. SKU alone cannot distinguish separate purchases.
    Prefer the purchase record over a return/refund snapshot.
    """
    if (normalized.get("store") or "").strip().lower() != "costco":
        return normalized
    shipments = {s.get("shipmentId"): s for s in normalized.get("shipments") or []}
    items: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for item in normalized.get("items") or []:
        identity = item.get("logicalItemId")
        if identity is None or not str(identity).strip():
            items.append(item)
            continue
        slices = sorted(
            [(str(shipments.get(s.get("shipmentId"), {}).get("trackingNumber")
                  or s.get("shipmentId") or ""), s.get("quantity") or 1)
             for s in item.get("shipments") or []],
            key=json.dumps,
        )
        key = json.dumps([
            str(identity).strip(), item.get("externalSku"), item.get("name"),
            (item.get("quantities") or {}).get("ordered"),
            (item.get("pricing") or {}).get("unitPrice"), slices,
        ], sort_keys=True)
        if key not in positions:
            positions[key] = len(items)
            items.append(item)
        else:
            previous_status = (items[positions[key]].get("status") or {}).get("rawStatusCode")
            if re.search(r"return|refund", str(previous_status or ""), re.I):
                items[positions[key]] = item
    return {**normalized, "items": items}
