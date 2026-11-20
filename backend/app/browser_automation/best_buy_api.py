"""Allowlisted Best Buy API fields used by the shared order importer."""
from collections import Counter, defaultdict, deque
from math import isfinite
import re

from app.schemas.store_import import StoreOrderImportPayload


def _object(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _text(value):
    return value.strip()[:500] if isinstance(value, str) and value.strip() else None


def _number(value):
    return value if type(value) in (int, float) and isfinite(value) else None


def _fields(raw, keys, normalize=_text):
    return {key: value for key in keys if (value := normalize(raw.get(key))) is not None}


def _phone(value):
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 10 or (len(digits) == 11 and digits.startswith("1")):
        return "+1" + digits[-10:]
    return None


def _email(value):
    value = _text(value)
    return value.lower() if value and re.fullmatch(r"[^\s@*•]+@[^\s@*•]+\.[^\s@*•]+", value) else None


def _address(raw):
    address = _fields(raw, ("firstName", "lastName", "addressLine1", "addressLine2", "city", "state", "postalCode", "country"))
    if not address.get("addressLine1"):
        return None
    name = " ".join(address[key] for key in ("firstName", "lastName") if key in address)
    if name:
        address["fullName"] = name
    phone = _phone(_text(raw.get("phoneNumber")))
    if phone:
        address["phoneNumber"] = phone
    return address


def enrich_order(payload: StoreOrderImportPayload, response: object) -> StoreOrderImportPayload:
    """Merge fields for the exact order already verified on the retailer page."""
    order = _object(_object(response).get("order"))
    if order.get("userOrderId") != payload.externalOrder["id"]:
        return payload

    user = _object(order.get("user"))
    customer = _fields(user, ("firstName", "lastName"))
    if email := _email(user.get("emailAddress")):
        customer["email"] = email

    raw_items = _rows(order.get("items"))
    address_ids = {fulfillment.get("addressId") for row in raw_items
                   if (fulfillment := _object(row.get("fulfillment"))) and isinstance(fulfillment.get("addressId"), str)}
    shipping_addresses = []
    for raw in _rows(user.get("addresses")):
        if _text(raw.get("id")) in address_ids or (not address_ids and str(raw.get("type", "")).lower() in {"shipping", "shipto"}):
            address = _address(raw)
            if address and address not in shipping_addresses:
                shipping_addresses.append(address)
    shipping = payload.shippingAddress
    if len(shipping_addresses) == 1:
        # Keep the page's recipient and address lines for buying-group matching.
        address = shipping_addresses[0]
        shipping = {**address, **(shipping or {})}
        if "phoneNumber" in address:
            shipping["phoneNumber"] = address["phoneNumber"]
    if shipping and not shipping_addresses:
        shipping_addresses.append(shipping)

    price = _fields(_object(order.get("price")), (
        "productTotal", "shippingTotal", "totalSalesTax", "totalTaxableFees",
        "totalShippingTaxes", "salesTaxFeesSurcharges", "totalDiscounts",
        "totalAdjustmentAmount", "orderTotal", "outstandingAmount",
    ), _number)
    methods = []
    for raw in _rows(order.get("payments")):
        payment = _fields(raw, ("type", "creditCardType", "authorizationStatus"))
        payment.update(_fields(raw, ("amount", "authorizedAmount", "chargedAmount", "refundedAmount"), _number))
        # Only the suffix is needed to match a configured payment method.
        card = _text(raw.get("displayCreditCardNumber")) or ""
        if suffix := re.search(r"(?<!\d)\d{4}$", card):
            payment["last4"] = suffix[0]
            payment["description"] = f"{payment.get('creditCardType', 'Card')} ending in {suffix[0]}"
        if payment:
            methods.append(payment)

    groups = []
    by_line = {}
    for raw in _rows(_object(order.get("groups")).get("fulfillmentGroups")):
        group = _fields(raw, ("groupId", "carrier", "trackingNumber", "status"))
        group.update(_fields(_object(raw.get("grouping")), ("type", "displayName")))
        group["lineIds"] = [key for member in _rows(raw.get("members")) if (key := _text(member.get("itemKey")))]
        if group.get("groupId"):
            groups.append(group)
            by_line.update({key: group for key in group["lineIds"]})

    item_info = []
    candidates = defaultdict(deque)
    for raw in raw_items:
        info = _fields(raw, ("id", "sku", "manufacturerName", "thirdPartyOrderId"))
        info.update(_fields(raw, ("quantity", "originalQuantity"), _number))
        catalog = _object(_object(raw.get("metadata")).get("catalog"))
        info.update(_fields(_object(catalog.get("manufacturer")), ("modelNumber",)))
        info["price"] = _fields(_object(raw.get("price")), (
            "unitRegularPrice", "unitCurrentPrice", "productPrice", "totalShippingPrice",
            "totalSalesTax", "totalTaxableFees", "salesTaxFeesSurcharges", "totalDiscounts", "itemTotal",
        ), _number)
        fulfillment = _object(raw.get("fulfillment"))
        info["fulfillment"] = _fields(fulfillment, ("type", "levelOfService", "minArrivalDate", "maxArrivalDate", "receivedDateTime"))
        if group := by_line.get(info.get("id")):
            info["fulfillmentGroupId"] = group["groupId"]
        if info.get("id") and info.get("sku"):
            item_info.append(info)
            candidates[(info["sku"], info.get("quantity"))].append(info)

    rows = payload.items or []
    # A partial API order must not shift the identities of repeated items.
    items = rows
    if (
        len({info["id"] for info in item_info}) == len(item_info)
        and Counter((row["externalSku"], row["quantities"]["ordered"]) for row in rows)
        == Counter({key: len(values) for key, values in candidates.items()})
    ):
        dom_groups = defaultdict(set)
        for row in rows:
            key = (row["externalSku"], row["quantities"]["ordered"])
            dom_groups[key].update(part["shipmentId"] for part in row.get("shipments") or [])
        ambiguous = {
            key for key, values in candidates.items() if len(values) > 1 and (
                len(dom_groups[key]) > 1 or len({line.get("fulfillmentGroupId") for line in values}) > 1
            )
        }
        items = []
        for row in rows:
            key = (row["externalSku"], row["quantities"]["ordered"])
            if key in ambiguous:
                items.append(row)
                continue
            info = candidates[key].popleft()
            info["legacyLogicalItemId"] = row["logicalItemId"]
            pricing = dict(row["pricing"])
            for source, target in (("totalShippingPrice", "shipping"), ("salesTaxFeesSurcharges", "tax")):
                if source in info["price"]:
                    pricing[target] = info["price"][source]
            items.append({
                **row, "logicalItemId": info["id"], "legacyLogicalItemId": row["logicalItemId"], "pricing": pricing,
            })

    shipments = []
    for raw in payload.shipments or []:
        shipment = _fields(raw, ("shipmentId", "trackingNumber", "deliveryDate"))
        shipment.update(_fields(_object(raw.get("status")), ("message",)))
        shipment["lineIds"] = [
            row["logicalItemId"] for row in items
            if any(part["shipmentId"] == raw["shipmentId"] for part in row.get("shipments") or [])
        ]
        shipments.append(shipment)

    details = {
        "customer": customer,
        "shippingAddresses": shipping_addresses,
        "price": price,
        "payments": methods,
        "items": item_info,
        "fulfillmentGroups": groups,
        "shipments": shipments,
    }
    totals = dict(payload.totals or {})
    for source, target in (
        ("orderTotal", "grandTotal"), ("productTotal", "subtotal"),
        ("shippingTotal", "shipping"), ("salesTaxFeesSurcharges", "tax"),
    ):
        if source in price:
            totals[target] = price[source]
    return payload.model_copy(update={
        "customer": {**(payload.customer or {}), **customer} or None,
        "shippingAddress": shipping,
        "items": items,
        "totals": totals,
        "paymentMethods": [method for method in methods if method.get("last4")] or payload.paymentMethods,
        "bestBuyDetails": details,
    })
