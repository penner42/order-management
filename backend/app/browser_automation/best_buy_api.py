"""Allowlisted Best Buy API fields used by the shared order importer."""
from collections import Counter, defaultdict, deque
from math import isfinite
import re
from urllib.parse import urlsplit

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


def normalize_tracking_number(value):
    number = _text(value)
    if number and re.fullmatch(r"[A-Z0-9-]{6,45}", number, re.I) and re.search(r"[0-9]", number):
        return number.upper()
    return None


def _line_matches(rows, item_info):
    def key(line):
        if "externalSku" in line:
            return line["externalSku"], line["quantities"]["ordered"]
        return line["sku"], line.get("quantity")

    # A partial API response can shift the identities of repeated items.
    if (
        len({line["id"] for line in item_info}) != len(item_info)
        or Counter(map(key, rows)) != Counter(map(key, item_info))
    ):
        return {}

    page_groups = defaultdict(list)
    for row in rows:
        parts = row.get("shipments") or []
        if len(parts) == 1:
            page_groups[parts[0]["shipmentId"]].append(row)
    api_groups = defaultdict(list)
    for line in item_info:
        if group_id := line.get("fulfillmentGroupId"):
            api_groups[group_id].append(line)

    def by_contents(groups):
        signatures = defaultdict(list)
        for lines in groups.values():
            signatures[frozenset(Counter(map(key, lines)).items())].append(lines)
        return signatures

    matches = {}
    # A unique group composition pairs repeat SKUs even if group order differs.
    api_by_contents = by_contents(api_groups)
    for signature, page in by_contents(page_groups).items():
        api = api_by_contents.get(signature, [])
        if len(page) == len(api) == 1:
            candidates = defaultdict(deque)
            for line in api[0]:
                candidates[key(line)].append(line)
            for row in page[0]:
                matches[row["logicalItemId"]] = candidates[key(row)].popleft()

    used = {line["id"] for line in matches.values()}
    remaining_page = defaultdict(list)
    remaining_api = defaultdict(list)
    for row in rows:
        if row["logicalItemId"] not in matches:
            remaining_page[key(row)].append(row)
    for line in item_info:
        if line["id"] not in used:
            remaining_api[key(line)].append(line)
    for line_key, page in remaining_page.items():
        api = remaining_api[line_key]
        page_ids = {part["shipmentId"] for row in page for part in row.get("shipments") or []}
        api_ids = {line.get("fulfillmentGroupId") for line in api}
        if len(page) == 1 or (len(page_ids) <= 1 and len(api_ids) <= 1):
            matches.update((row["logicalItemId"], line) for row, line in zip(page, api))
    return matches


def _tracking_shipment(tracking, group):
    number = normalize_tracking_number(tracking.get("trackingNumber"))
    if not number:
        number = normalize_tracking_number(group.get("trackingNumber"))
        tracking = {}
    if not number:
        return None

    shipment = {"trackingNumber": number, "fulfillmentType": "shipping"}
    if carrier := (_text(tracking.get("carrierDisplayName")) or _text(tracking.get("carrierName")) or group.get("carrier")):
        shipment["carrier"] = carrier
    if status := (_text(tracking.get("statusDesc")) or group.get("status")):
        shipment["status"] = {"message": status}
    if url := _text(tracking.get("trackingUrl")):
        try:
            parsed = urlsplit(url)
            if parsed.scheme == "https" and parsed.hostname and parsed.username is None:
                shipment["trackingUrl"] = url
        except ValueError:
            pass
    return shipment


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
    api_shipments = {}
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
        group = by_line.get(info.get("id"), {})
        if group:
            info["fulfillmentGroupId"] = group["groupId"]
        if info.get("id") and info.get("sku"):
            item_info.append(info)
            if shipment := _tracking_shipment(_object(fulfillment.get("tracking")), group):
                api_shipments[info["id"]] = shipment

    rows = payload.items or []
    matches = _line_matches(rows, item_info)
    page_shipments = {raw["shipmentId"]: raw for raw in payload.shipments or []}
    ids_by_tracking = {raw["trackingNumber"]: sid for sid, raw in page_shipments.items() if raw.get("trackingNumber")}
    normalized_shipments = dict(page_shipments)
    items = []
    for row in rows:
        legacy_id = row["logicalItemId"]
        info = matches.get(legacy_id)
        if not info:
            items.append(row)
            continue
        info["legacyLogicalItemId"] = legacy_id
        pricing = dict(row["pricing"])
        for source, target in (("totalShippingPrice", "shipping"), ("salesTaxFeesSurcharges", "tax")):
            if source in info["price"]:
                pricing[target] = info["price"][source]
        item = {**row, "logicalItemId": info["id"], "legacyLogicalItemId": legacy_id, "pricing": pricing}
        if shipment := api_shipments.get(info["id"]):
            number = shipment["trackingNumber"]
            sid = ids_by_tracking.setdefault(number, f"best-buy-{number}")
            normalized_shipments[sid] = {**normalized_shipments.get(sid, {}), **shipment, "shipmentId": sid}
            item["shipments"] = [{"shipmentId": sid, "quantity": row["quantities"]["ordered"]}]
        items.append(item)

    shipments = []
    line_ids_by_shipment = defaultdict(list)
    for item in items:
        for part in item.get("shipments") or []:
            line_ids_by_shipment[part["shipmentId"]].append(item["logicalItemId"])
    final_shipments = [raw for sid, raw in normalized_shipments.items() if sid in line_ids_by_shipment]
    for raw in final_shipments:
        shipment = _fields(raw, ("shipmentId", "trackingNumber", "deliveryDate", "carrier"))
        shipment.update(_fields(_object(raw.get("status")), ("message",)))
        shipment["lineIds"] = line_ids_by_shipment[raw["shipmentId"]]
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
        "shipments": final_shipments,
        "totals": totals,
        "paymentMethods": [method for method in methods if method.get("last4")] or payload.paymentMethods,
        "bestBuyDetails": details,
    })
