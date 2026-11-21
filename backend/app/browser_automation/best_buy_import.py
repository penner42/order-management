"""Read a Best Buy Marketplace order through its guest order link."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

from app.config import settings
from app.browser_automation.best_buy_api import enrich_order, normalize_tracking_number
from app.schemas.store_import import StoreOrderImportPayload

STORE_NAME = "Best Buy"
_EMAIL_HOST = "click.emailinfo2.bestbuy.com"
_STORE_HOST = "www.bestbuy.com"
_ORDER_PATH = re.compile(r"/profile/ss/marketplace/orders/order-details/[A-Za-z0-9_=-]{6,500}/view/?")
_ORDER_NUMBER = re.compile(r"BBY\d{2}-\d{5,30}", re.I)
_EXTRACT_DETAILS = Path(__file__).with_name("best_buy_details.js").read_text(encoding="utf-8")
_browser_slot = asyncio.Semaphore(1)


class BestBuyImportError(Exception):
    """An import failure that can be displayed without exposing the source link."""


def validate_order_url(url: str) -> str:
    url = url.strip()
    if not url or len(url) > 4096 or re.search(r"[\s\\\x00-\x1f\x7f]", url):
        raise BestBuyImportError("Enter a valid HTTPS Best Buy order link.")
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise BestBuyImportError("Enter a valid HTTPS Best Buy order link.") from None
    if parsed.scheme == "https" and not parsed.fragment:
        if parsed.netloc == _EMAIL_HOST and parsed.path == "/":
            query = parse_qs(parsed.query, keep_blank_values=True)
            tokens = query.get("qs", [])
            if set(query) == {"qs"} and len(tokens) == 1 and re.fullmatch(r"[A-Za-z0-9_-]{10,2048}", tokens[0]):
                return url
        if parsed.netloc == _STORE_HOST and _ORDER_PATH.fullmatch(parsed.path) and not parsed.query:
            return url
    raise BestBuyImportError("Use the order link from your Best Buy email or a Best Buy Marketplace order-details URL.")


def _money(value: str | None) -> Decimal | None:
    if not value:
        return None
    value = value.strip()
    if value.casefold() == "free":
        return Decimal(0)
    if not re.fullmatch(r"-?\$[\d,]+\.\d{2}", value):
        return None
    return Decimal(value.replace("$", "").replace(",", ""))


def _date(value: str | None) -> str | None:
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value or "", fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _shipping_address(lines: list[str]) -> dict[str, str] | None:
    if lines and lines[-1].upper() in {"US", "USA", "UNITED STATES"}:
        lines = lines[:-1]
    if len(lines) < 3:
        return None
    city_line = re.fullmatch(
        r"(.+?),?\s+([A-Z]{2}),?\s+(\d{5}(?:-\d{4})?)(?:[ ,]+(?:US|USA|UNITED STATES))?",
        lines[-1], re.I,
    )
    if not city_line:
        return None
    city, state, postal_code = city_line.groups()
    address = {
        "fullName": lines[0],
        "addressLine1": lines[1],
        "city": city.rstrip(","),
        "state": state.upper(),
        "postalCode": postal_code,
        "country": "US",
    }
    if len(lines) > 3:
        address["addressLine2"] = ", ".join(lines[2:-1])
    return address


def normalize_order(details: dict) -> StoreOrderImportPayload:
    order_number = details.get("orderNumber")
    purchase_date = _date(details.get("purchaseDate"))
    grand_total = _money(details.get("total"))
    if not order_number or not _ORDER_NUMBER.fullmatch(order_number) or not purchase_date or grand_total is None:
        raise BestBuyImportError("Best Buy did not provide the order number, purchase date, or total.")

    items = []
    shipments = []
    addresses = []
    emails: set[str] = set()
    phones: set[str] = set()
    for index, group in enumerate(details["groups"]):
        tracking = normalize_tracking_number(group["tracking"])
        status = group["status"]
        shipments.append({
            "shipmentId": f"shipment-{index + 1}",
            "trackingNumber": tracking,
            "trackingUrl": group["trackingUrl"] if tracking else None,
            "fulfillmentType": "shipping",
            "status": {"message": status} if status else None,
            "deliveryDate": _date(group["deliveredDate"]) if status and status.lower().startswith("delivered") else None,
        })
        address = _shipping_address(group["address"])
        contact = group["contact"]
        if address and address not in addresses:
            addresses.append(address)
        if contact.get("phone"):
            phones.add("+1" + re.sub(r"\D", "", contact["phone"])[-10:])
        if contact.get("email"):
            emails.add(contact["email"].lower())

    line_shipping = []
    line_amounts = []
    sku_counts: Counter[str] = Counter()
    for row in details["rows"]:
        name = row["name"]
        sku = row["sku"]
        quantity = row["quantity"]
        item_total = _money(row["itemTotal"])
        product_price = _money(row["productPrice"])
        shipping = _money(row["shipping"])
        if not name or not sku or not quantity or not quantity.isascii() or not quantity.isdecimal() or int(quantity) < 1 or item_total is None:
            raise BestBuyImportError("Best Buy did not provide a name, SKU, quantity, or price for every line item.")
        quantity = int(quantity)
        merchandise = item_total - (shipping or Decimal(0))
        if product_price is not None and product_price != merchandise and product_price * quantity != merchandise:
            merchandise = product_price
        if merchandise < 0:
            raise BestBuyImportError("Best Buy returned an invalid line item price.")
        line_shipping.append(shipping)
        line_amounts.append(merchandise)
        sku_counts[sku] += 1
        group_index = row["groupIndex"]
        shipment = shipments[group_index] if group_index >= 0 else None
        item = {
            "logicalItemId": f"{sku}:{sku_counts[sku]}",
            "externalSku": sku,
            "name": name,
            "productUrl": row["productUrl"],
            "variants": [{"name": "Model", "value": row["model"]}] if row["model"] else [],
            "quantities": {"ordered": quantity},
            "pricing": {
                "unitPrice": float(merchandise / quantity),
                "linePrice": float(merchandise),
                "lineTotal": float(merchandise),
                "shipping": float(shipping) if shipping is not None else None,
            },
            "shipments": [{"shipmentId": shipment["shipmentId"], "quantity": quantity}] if shipment else [],
        }
        if shipment and (shipment["status"] or {}).get("message", "").casefold().startswith(("canceled", "cancelled")):
            item["status"] = {"normalizedStatus": "canceled"}
        items.append(item)
    if not items:
        raise BestBuyImportError("Best Buy did not display any order items.")

    subtotal = _money(details.get("subtotal"))
    merchandise_total = sum(line_amounts, Decimal(0))
    if subtotal is None:
        subtotal = merchandise_total
    shipping = _money(details.get("shipping"))
    if shipping is None and all(cost is not None for cost in line_shipping):
        shipping = sum(line_shipping, Decimal(0))
    tax = _money(details.get("tax"))
    discount = _money(details.get("discount"))
    if tax is not None and merchandise_total > 0:
        allocated = running_subtotal = Decimal(0)
        for item, amount in zip(items, line_amounts):
            running_subtotal += amount
            cumulative_tax = (running_subtotal * tax / merchandise_total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            item["pricing"]["tax"] = float(cumulative_tax - allocated)
            allocated = cumulative_tax

    methods = []
    for line in details["payment"]:
        suffix = re.search(r"(?:ends? in|ending in|[*•x]{2,}[- ]*)\s*(\d{4})\b", line, re.I)
        if suffix:
            brand = re.search(r"\b(Visa|Mastercard|American Express|Discover|Amex)\b", line, re.I)
            last4 = suffix[1]
            methods.append({
                "last4": last4,
                "cardType": brand[1] if brand else None,
                "description": f"{brand[1] if brand else 'Card'} ending in {last4}",
                "paymentType": "card",
            })

    address = addresses[0] if len(addresses) == 1 else None
    if address and len(phones) == 1:
        address["phoneNumber"] = next(iter(phones))
    external_order = {"id": order_number.upper(), "orderDate": purchase_date}
    if all(item.get("status", {}).get("normalizedStatus") == "canceled" for item in items):
        external_order["statusType"] = "canceled"
    return StoreOrderImportPayload(
        store=STORE_NAME,
        source="best-buy-url",
        externalOrder=external_order,
        customer={"email": next(iter(emails))} if len(emails) == 1 else None,
        shippingAddress=address,
        shipments=shipments,
        items=items,
        totals={
            "itemCount": sum(item["quantities"]["ordered"] for item in items),
            "subtotal": float(subtotal),
            "shipping": float(shipping) if shipping is not None else None,
            "tax": float(tax) if tax is not None else None,
            "grandTotal": float(grand_total),
        },
        orderDiscount=float(abs(discount)) if discount is not None else None,
        paymentMethods=methods,
    )


async def fetch_order(source_url: str) -> StoreOrderImportPayload:
    from camoufox.async_api import AsyncCamoufox

    source_url = validate_order_url(source_url)
    try:
        await asyncio.wait_for(_browser_slot.acquire(), timeout=3)
    except TimeoutError:
        raise BestBuyImportError("Another Best Buy order check is running. Try again shortly.") from None
    try:
        async with asyncio.timeout(75):
            async with AsyncCamoufox(headless=settings.browser_headless, locale="en-US") as browser:
                context = await browser.new_context(service_workers="block")

                async def allow_retailer_requests(route):
                    target = urlsplit(route.request.url)
                    host = target.hostname or ""
                    if route.request.is_navigation_request():
                        allowed = host in {_EMAIL_HOST, _STORE_HOST}
                    else:
                        allowed = host in {"bestbuy.com", "bbystatic.com"} or host.endswith((".bestbuy.com", ".bbystatic.com"))
                    if target.scheme == "https" and allowed:
                        await route.continue_()
                    else:
                        await route.abort()

                await context.route("**/*", allow_retailer_requests)
                page = await context.new_page()
                await page.goto(source_url, wait_until="commit", timeout=35000)
                final = urlsplit(page.url)
                if final.scheme != "https" or final.netloc != _STORE_HOST or not _ORDER_PATH.fullmatch(final.path):
                    raise BestBuyImportError("Best Buy did not open a Marketplace order. The link may have expired.")
                await page.get_by_text("SKU:", exact=True).first.wait_for(timeout=22000)
                button = page.get_by_role("button", name="Payment Details", exact=True)
                if await button.count():
                    await button.first.click(timeout=5000)
                    await page.get_by_text("Order Summary", exact=True).first.wait_for(timeout=7000)
                payload = normalize_order(await page.evaluate(_EXTRACT_DETAILS))
                api_url = f"https://{_STORE_HOST}/profile/ss/api/v1/orders/{payload.externalOrder['id']}"
                try:
                    response = await page.goto(api_url, wait_until="commit", timeout=10000)
                    if response and response.ok and response.url == api_url:
                        return enrich_order(payload, await asyncio.wait_for(response.json(), timeout=5))
                except (PlaywrightError, TimeoutError, ValueError):
                    pass
                return payload
    except (TimeoutError, PlaywrightTimeoutError):
        raise BestBuyImportError("Best Buy did not load the order in time. Try again or check the link in your browser.") from None
    except PlaywrightError:
        raise BestBuyImportError("Could not open the Best Buy order. Check the link and try again.") from None
    finally:
        _browser_slot.release()
