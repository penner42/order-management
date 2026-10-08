"""Amazon bulk order capture using Playwright + extension DOM parsers."""
from __future__ import annotations

import logging
from typing import Any, Callable

from playwright.async_api import Page

from app.browser_automation.common import LoginRequiredError
from app.browser_automation.paths import amazon_script_paths
from app.browser_automation.session_manager import (
    AMAZON_ORDERS_URL,
    looks_like_amazon_signin,
    session_manager,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, Any]], None]


async def _inject_amazon_scripts(page: Page) -> None:
    for path in amazon_script_paths():
        await page.add_script_tag(path=str(path))


async def _ensure_scripts(page: Page) -> None:
    has = await page.evaluate(
        """() => !!(globalThis.OrderManagerAmazonDom && globalThis.OrderManagerAmazon)"""
    )
    if not has:
        await _inject_amazon_scripts(page)


async def _parse_list_page(page: Page) -> list[dict[str, Any]]:
    await _ensure_scripts(page)
    return await page.evaluate(
        """async () => {
          const d = globalThis.OrderManagerAmazonDom;
          if (!d) throw new Error('Amazon DOM helpers not loaded.');
          try { await d.waitForOrderListReady(); } catch (e) {}
          try { await d.waitForParseableOrderList(25000); } catch (e) {}
          return d.parseOrderListPage() || [];
        }"""
    )


async def _parse_detail_page(page: Page, *, skip_tracking: bool = True) -> dict[str, Any]:
    await _ensure_scripts(page)
    return await page.evaluate(
        """async (skipTracking) => {
          const d = globalThis.OrderManagerAmazonDom;
          if (!d) throw new Error('Amazon DOM helpers not loaded.');
          await d.waitForOrderDetailReady(35000);
          const parsed = d.parseOrderDetailPage();
          if (!parsed || !parsed.orderId) throw new Error('Could not parse Amazon order detail page.');
          if (!skipTracking && typeof d.enrichShipmentsWithTracking === 'function') {
            await d.enrichShipmentsWithTracking(parsed.shipments, window.location.origin);
          }
          return parsed;
        }""",
        skip_tracking,
    )


async def _fetch_account_email(page: Page) -> str | None:
    await _ensure_scripts(page)
    try:
        return await page.evaluate(
            """async () => {
              const d = globalThis.OrderManagerAmazonDom;
              if (!d || typeof d.fetchAccountEmail !== 'function') return null;
              return await d.fetchAccountEmail(window.location.origin, { allowSlowLookup: false });
            }"""
        )
    except Exception as exc:
        logger.info("Account email lookup failed: %s", exc)
        return None


def _normalize_order(raw: dict[str, Any], source_url: str, account_email: str | None) -> dict[str, Any]:
    """Mirror browser-extension/lib/amazon.js normalizeAmazonOrderPayload in Python."""

    def coerce(v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s or None
        if isinstance(v, (int, float, bool)):
            return str(v)
        return None

    def normalize_date(v: Any) -> str | None:
        s = coerce(v)
        if not s:
            return None
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return s[:10]
        return s

    order_id = coerce(raw.get("orderId"))
    if not order_id:
        raise ValueError("Amazon order missing orderId")

    items: list[dict[str, Any]] = []
    for it in raw.get("items") or []:
        if not isinstance(it, dict):
            continue
        qty = it.get("quantity") if isinstance(it.get("quantity"), (int, float)) and it["quantity"] > 0 else 1
        unit_price = it.get("unitPrice") if isinstance(it.get("unitPrice"), (int, float)) else None
        line_total = it.get("lineTotal") if isinstance(it.get("lineTotal"), (int, float)) else None
        if line_total is None and unit_price is not None:
            line_total = unit_price * qty
        item_shipment_id = coerce(it.get("shipmentId"))
        shipment_slices = (
            [{"shipmentId": item_shipment_id, "quantity": qty, "normalizedStatus": None}]
            if item_shipment_id
            else []
        )
        items.append(
            {
                "logicalItemId": coerce(it.get("asin")),
                "externalSku": coerce(it.get("asin")),
                "name": coerce(it.get("name")),
                "productUrl": coerce(it.get("productUrl")),
                "imageUrl": coerce(it.get("imageUrl")),
                "variants": [],
                "quantities": {"ordered": qty},
                "pricing": {
                    "unitPrice": unit_price,
                    "linePrice": line_total,
                    "lineTotal": line_total,
                    "strikethroughPrice": None,
                    "discounts": [],
                },
                "status": {
                    "rawStatusCode": None,
                    "normalizedStatus": coerce(raw.get("status")),
                },
                "shipments": shipment_slices,
                "returnability": {"isReturnable": False, "returnEligibilityMessage": None},
            }
        )

    shipments: list[dict[str, Any]] = []
    for si, s in enumerate(raw.get("shipments") or []):
        if not isinstance(s, dict):
            continue
        st = s.get("status") if isinstance(s.get("status"), dict) else {}
        shipment_id = coerce(s.get("shipmentId")) or coerce(s.get("trackingNumber")) or f"shipment-{si}"
        shipments.append(
            {
                "shipmentId": shipment_id,
                "trackingNumber": coerce(s.get("trackingNumber")),
                "trackingUrl": coerce(s.get("trackingUrl")),
                "deliveryDate": coerce(s.get("deliveryDate")),
                "status": {
                    "rawStatusType": coerce(st.get("rawStatusType")) or coerce(raw.get("status")),
                    "normalizedStatus": coerce(st.get("message")) or coerce(st.get("rawStatusType")),
                    "message": coerce(st.get("message")),
                },
            }
        )

    if (
        len(shipments) == 1
        and shipments[0].get("shipmentId")
        and items
        and all(not (item.get("shipments") or []) for item in items)
    ):
        fallback_id = shipments[0]["shipmentId"]
        for item in items:
            qty = (item.get("quantities") or {}).get("ordered") or 1
            item["shipments"] = [{"shipmentId": fallback_id, "quantity": qty, "normalizedStatus": None}]

    addr = raw.get("shippingAddress") if isinstance(raw.get("shippingAddress"), dict) else None
    shipping_address = None
    if addr:
        shipping_address = {
            "fullName": coerce(addr.get("fullName")),
            "addressLine1": coerce(addr.get("addressLine1")),
            "addressLine2": coerce(addr.get("addressLine2")),
            "city": coerce(addr.get("city")),
            "state": coerce(addr.get("state")),
            "postalCode": coerce(addr.get("postalCode")),
            "country": coerce(addr.get("country")),
            "phoneNumber": None,
        }

    totals_raw = raw.get("totals") if isinstance(raw.get("totals"), dict) else {}
    grand = totals_raw.get("grandTotal")
    if not isinstance(grand, (int, float)):
        grand = raw.get("totalAmount") if isinstance(raw.get("totalAmount"), (int, float)) else None
    totals = {
        "subtotal": totals_raw.get("subtotal") if isinstance(totals_raw.get("subtotal"), (int, float)) else None,
        "grandTotal": grand,
    }

    payment_methods = []
    for pm in raw.get("paymentMethods") or []:
        if not isinstance(pm, dict):
            continue
        payment_methods.append(
            {
                "description": coerce(pm.get("description")),
                "cardType": coerce(pm.get("cardType")),
                "paymentType": None,
                "last4": coerce(pm.get("last4")),
            }
        )

    from datetime import datetime, timezone

    payload: dict[str, Any] = {
        "store": "amazon",
        "source": "browser-automation",
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "externalOrder": {
            "id": order_id,
            "orderDate": normalize_date(raw.get("orderDate")),
            "url": source_url or coerce(raw.get("detailUrl")),
            "statusType": coerce(raw.get("status")),
        },
        "customer": {"email": coerce(account_email)},
        "shippingAddress": shipping_address,
        "shipments": shipments,
        "items": items,
        "paymentMethods": payment_methods,
        "totals": totals,
    }
    discount = totals_raw.get("orderDiscount")
    if isinstance(discount, (int, float)) and discount > 0:
        payload["orderDiscount"] = discount
    return payload


async def _get_next_page_url(page: Page) -> str | None:
    await _ensure_scripts(page)
    return await page.evaluate(
        """() => {
          const d = globalThis.OrderManagerAmazonDom;
          if (!d) return null;
          return d.getNextPageUrl() || null;
        }"""
    )


async def _detail_url_for_summary(page: Page, summary: dict[str, Any]) -> str | None:
    detail = summary.get("detailUrl") or summary.get("url")
    if detail:
        return str(detail)
    order_id = summary.get("orderId")
    if not order_id:
        return None
    origin = await page.evaluate("() => window.location.origin")
    return f"{origin}/your-orders/order-details?orderID={order_id}&disableCsd=missing-library"


async def run_amazon_import(
    profile_id: int,
    *,
    max_pages: int = 3,
    on_progress: ProgressCallback | None = None,
) -> list[dict[str, Any]]:
    """Capture up to max_pages of Amazon order history for a profile."""

    def progress(**kwargs: Any) -> None:
        if on_progress:
            on_progress(kwargs)

    session = await session_manager.ensure_session(
        profile_id,
        mode="import",
        start_url=AMAZON_ORDERS_URL,
    )
    page = session.page

    if looks_like_amazon_signin(page.url):
        raise LoginRequiredError("Amazon session requires login.")

    account_email = await _fetch_account_email(page)
    progress(phase="list", page=1, message="Scanning order list…", account_email=account_email)

    orders: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    page_num = 1
    list_url = page.url or AMAZON_ORDERS_URL

    while page_num <= max_pages:
        if looks_like_amazon_signin(page.url):
            raise LoginRequiredError("Amazon session requires login.")

        # Ensure we are on the list page before parsing / reading next URL.
        if page.url != list_url:
            await page.goto(list_url, wait_until="domcontentloaded", timeout=60_000)
            if looks_like_amazon_signin(page.url):
                raise LoginRequiredError("Amazon session requires login.")

        progress(phase="list", page=page_num, message=f"Parsing list page {page_num}…")
        summaries = await _parse_list_page(page)
        if not isinstance(summaries, list):
            summaries = []

        next_url = await _get_next_page_url(page)

        progress(
            phase="list",
            page=page_num,
            message=f"Found {len(summaries)} order(s) on page {page_num}",
            list_count=len(summaries),
        )

        for idx, summary in enumerate(summaries):
            if not isinstance(summary, dict):
                continue
            order_id = str(summary.get("orderId") or "")
            if not order_id or order_id in seen_ids:
                continue
            seen_ids.add(order_id)
            detail_url = await _detail_url_for_summary(page, summary)
            progress(
                phase="detail",
                page=page_num,
                message=f"Capturing order {order_id} ({idx + 1}/{len(summaries)})",
                order_id=order_id,
                captured=len(orders),
            )
            try:
                if detail_url:
                    await page.goto(detail_url, wait_until="domcontentloaded", timeout=60_000)
                    if looks_like_amazon_signin(page.url):
                        raise LoginRequiredError("Amazon session requires login.")
                    raw = await _parse_detail_page(page, skip_tracking=True)
                    source_url = page.url
                else:
                    raw = summary
                    source_url = detail_url or list_url
                normalized = _normalize_order(raw, source_url, account_email)
                orders.append(normalized)
            except LoginRequiredError:
                raise
            except Exception as exc:
                logger.warning("Failed to capture Amazon order %s: %s", order_id, exc)
                try:
                    if summary.get("orderId"):
                        orders.append(_normalize_order(summary, detail_url or list_url, account_email))
                except Exception:
                    pass

        if page_num >= max_pages or not next_url:
            break
        page_num += 1
        list_url = next_url
        progress(phase="list", page=page_num, message=f"Opening list page {page_num}…")
        await page.goto(list_url, wait_until="domcontentloaded", timeout=60_000)

    progress(phase="done", message=f"Captured {len(orders)} order(s)", captured=len(orders))
    return orders
