"""Capture verified retailer order regions without page chrome or recommendations."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
from urllib.parse import unquote, urlsplit
import uuid

from app.database import SessionLocal
from app.models import BrowserProfile
from app.utils.order_screenshots import (
    CAPTURE_LAYOUT_VERSION, cleanup_screenshots, fingerprint, latest_screenshot, profile_order,
    screenshot_dir, semantic_snapshot,
)

logger = logging.getLogger(__name__)
_CAPTURE_VIEWPORT = {"width": 1440, "height": 1000}
_CAPTURE_PADDING = 24
_WALMART_CAPTURE_LAYOUT_VERSION = 5
_WALMART_COLLAPSED_ITEMS = 'button[aria-expanded="false"][aria-label="Show items" i]'

# Prefer the retailer's order container; main is allowed only on a verified
# details route containing both this order number and its purchase lines.
_REGIONS = {
    "amazon": ['#orderDetails', '[data-component="orderDetails"]', '.od-content',
               '[data-testid="orderDetailsPage"]', '#ordersContainer', 'main'],
    "walmart": ['.print-bill-body', '[data-testid="order-details"]', '[data-testid="order-details-container"]',
                '[data-testid="orderDetails"]', 'main > section', 'main', '[role="main"]'],
    "costco": ['[data-testid="order-details"]', 'app-order-details', '.order-details',
               '#order-details', 'main', '[role="main"]'],
}
_EXCLUDE = """
nav, footer, [role="navigation"], [role="banner"], [role="contentinfo"],
[data-testid*="recommend" i], [data-testid*="carousel" i],
[data-testid*="sponsored" i], [data-testid*="advert" i],
[aria-label*="recommend" i], [aria-label*="sponsored" i],
[data-component*="recommend" i], [id*="recommend" i], [class*="recommend" i],
[id*="sponsored" i], [class*="sponsored" i], #nav-main, #nav-belt,
#rhf, #rhf-container, #navFooter, .a-carousel-container,
[aria-label*="chat" i], [data-testid*="chat" i], [id*="cookie" i]
"""
_FIND_REGION = """({selectors, retailer, number, names, itemSkus = [], details, paymentDigits = [], marker,
                   collapsedItems, diagnose = false, checkOnly = false, checkItems = true}) => {
  const text = value => (value || '').normalize('NFKC').replace(/\\s+/g, ' ').trim().toLowerCase();
  const productText = value => text(value).replace(/[^\\p{L}\\p{N}]+/gu, ' ').trim();
  const generic = selector => selector === 'main' || selector === '[role="main"]';
  const candidates = selectors.filter(selector => retailer !== 'costco' || !generic(selector))
    .flatMap(selector => [...document.querySelectorAll(selector)]);
  if (retailer === 'costco') {
    // Costco's React account application can render outside the storefront's
    // <main id="tiles-body-attribute">. Anchor on its order heading and walk
    // outward to the smallest complete order panel; never fall back to body.
    for (const heading of document.querySelectorAll('h1, [role="heading"][aria-level="1"]')) {
      if (text(heading.innerText) !== 'order details') continue;
      let element = heading.parentElement;
      while (element && element !== document.body && element !== document.documentElement) {
        if (!candidates.includes(element)) candidates.push(element);
        element = element.parentElement;
      }
    }
    candidates.push(...selectors.filter(generic).flatMap(selector => [...document.querySelectorAll(selector)]));
  }
  const diagnostics = [];
  for (const element of candidates) {
      const rect = element.getBoundingClientRect();
      const content = text(element.innerText);
      // Walmart inserts a hyphen in the displayed order number.
      const hasNumber = retailer === 'walmart'
        ? (content.match(/\\b\\d+(?:\\s*[-\\u2010-\\u2015]\\s*\\d+)*\\b/g) || [])
            .some(value => value.replace(/\\D/g, '') === number.replace(/-/g, ''))
        : content.includes(number.toLowerCase());
      const productContent = productText(content);
      const tokens = new Set(productContent.split(' '));
      const matchedNames = names.filter((name, index) => productContent.includes(productText(name)) ||
        (itemSkus[index] && tokens.has(text(itemSkus[index])))).length;
      const matchedDetails = details.filter(detail => content.includes(text(detail))).length;
      const matchedPayments = paymentDigits.filter(digits => content.includes(text(digits))).length;
      if (diagnose) diagnostics.push({tag:element.tagName, id:element.id,
        width:rect.width, height:rect.height, hasNumber, matchedNames, expectedNames:names.length,
        matchedDetails, expectedDetails:details.length, matchedPayments, expectedPayments:paymentDigits.length});
      if (rect.width < 200 || rect.height < 60 || !hasNumber) continue;
      // Costco's API includes bundle components and repeated status rows which
      // the website folds into a single purchase line. Verify a purchased item
      // plus the exact order identity, without requiring invisible API rows.
      if (checkItems && (!names.length || (retailer === 'costco' ? !matchedNames : matchedNames !== names.length))) continue;
      if (checkItems && collapsedItems && [...element.querySelectorAll(collapsedItems)]
          .some(button => button.getClientRects().length && getComputedStyle(button).visibility === 'visible')) continue;
      if (matchedDetails !== details.length) continue;
      if (paymentDigits.length && !matchedPayments) continue;
      // Do not accept a container whose order content is still loading.
      if (element.querySelector('[aria-busy="true"], [role="progressbar"]')) continue;
      if (diagnose) continue;
      if (!checkOnly) element.setAttribute('data-order-screenshot', marker);
      return true;
  }
  return diagnose ? diagnostics : false;
}"""


async def capture_order_screenshot(page, payload: dict, profile_id: int | None) -> None:
    """Skip unchanged orders before taking an image; failures never block import."""
    if profile_id is None:
        return
    token = uuid.uuid4().hex
    style = None
    overlay_style = None
    region_args = None
    previous_viewport = page.viewport_size
    viewport_changed = False
    number = str((payload.get("externalOrder") or {}).get("id") or "").strip()
    retailer = str(payload.get("store") or "").lower()
    layout_version = _WALMART_CAPTURE_LAYOUT_VERSION if retailer == "walmart" else CAPTURE_LAYOUT_VERSION
    try:
        snapshot = semantic_snapshot(payload)
        digest = fingerprint(snapshot)
        with SessionLocal() as db:
            cleanup_screenshots(db)
            profile = db.get(BrowserProfile, profile_id)
            if not profile or profile.retailer != retailer:
                return
            order = profile_order(db, profile_id, number)
            prior = latest_screenshot(db, order.id) if order else None
            if (prior and prior.fingerprint == digest and prior.layout_version >= layout_version
                    and (screenshot_dir() / prior.filename).is_file()):
                return
            metadata = dict(number=number, retailer=retailer, store_id=profile.store_id,
                            store_account_id=profile.store_account_id, snapshot=snapshot,
                            fingerprint=digest, captured_at=datetime.now(timezone.utc).isoformat(),
                            layout_version=layout_version)
        url = unquote(page.url).lower()
        if retailer == "walmart":
            order_path = re.fullmatch(r"/orders/(\d+(?:-\d+)*)/?", urlsplit(url).path)
            matches_order = bool(order_path and order_path[1].replace("-", "") == number.replace("-", ""))
        else:
            route = {"amazon": "order-details", "costco": "orderdetails"}.get(retailer)
            matches_order = bool(route and route in url and number.lower() in url)
        if not number or not matches_order:
            raise ValueError("Matching order details page is not displayed")
        if previous_viewport is not None and previous_viewport != _CAPTURE_VIEWPORT:
            await page.set_viewport_size(_CAPTURE_VIEWPORT)
            viewport_changed = True
        names = [str(item["name"]) for item in payload.get("items") or [] if item.get("name")]
        item_skus = [str(item.get("externalSku") or "") for item in payload.get("items") or [] if item.get("name")]
        address = payload.get("shippingAddress") or {}
        details = [str(address[key]) for key in ("addressLine1", "postalCode") if address.get(key)]
        payment_digits = list({str(pm["last4"]) for pm in payload.get("paymentMethods") or [] if pm.get("last4")})
        # Costco includes historical payment authorizations in its API; its
        # summary displays only the applicable instruments. Require a matching
        # displayed payment, rather than every historical authorization.
        if retailer != "costco":
            details.extend(payment_digits)
        region_args = dict(selectors=_REGIONS[retailer], retailer=retailer,
                           number=number, names=names, itemSkus=item_skus, details=details, marker=token,
                           paymentDigits=payment_digits if retailer == "costco" else [])
        root = page.locator(f'[data-order-screenshot="{token}"]')
        if retailer == "walmart":
            # Shipment cards render thumbnails before their item rows are expanded.
            # Locate the order and its summary first, then open every item list.
            region_args["collapsedItems"] = _WALMART_COLLAPSED_ITEMS
            await page.wait_for_function(_FIND_REGION, arg={**region_args, "checkItems": False}, timeout=15_000)
            collapsed = root.locator(f"{_WALMART_COLLAPSED_ITEMS}:visible")
            for _ in range(await collapsed.count()):
                await collapsed.first.click(timeout=5000)
            await root.evaluate("element => element.removeAttribute('data-order-screenshot')")
        elif retailer == "costco":
            # GraphQL responds before React finishes rendering. Wait for this
            # order's purchase lines, then reveal the collapsed address/payment
            # summary before requiring those details in the final crop.
            await page.wait_for_function(_FIND_REGION, arg={
                **region_args, "details": [], "paymentDigits": [], "checkOnly": True,
            }, timeout=15_000)
            summary = page.get_by_role("button", name=re.compile(r"^Order Summary\b", re.I)).first
            if await summary.count() and await summary.get_attribute("aria-expanded") == "false":
                await summary.click(timeout=5000)
        await page.wait_for_function(_FIND_REGION, arg=region_args, timeout=15_000)
        # Hide only unrelated UI. Restore all styling and markers in finally.
        await root.evaluate("""(root, marker) => {
          for (let current = root; current && current !== document.body; current = current.parentElement) {
            for (const sibling of current.parentElement?.children || []) {
              if (sibling !== current) sibling.setAttribute('data-order-screenshot-outside', marker);
            }
          }
        }""", token)
        outside_selector = f'[data-order-screenshot-outside="{token}"]'
        # Walmart's account sidebar reserves a flex column. Remove it from
        # layout while the order panel is captured so the panel can fill the page.
        outside_style = "display: none" if retailer == "walmart" else "visibility: hidden"
        walmart_style = """
            .od-print-message-notification { display: none !important; }
            [data-testid="layout-container"] { box-shadow: none !important; }
        """ if retailer == "walmart" else ""
        style = await page.add_style_tag(content=f"""
            {_EXCLUDE} {{ display: none !important; }}
            html {{ padding: {_CAPTURE_PADDING}px !important; box-sizing: border-box !important; }}
            {outside_selector}, {outside_selector} * {{ {outside_style} !important; }}
            {walmart_style}
        """)
        await root.evaluate("""(root, marker) => {
          for (const element of document.querySelectorAll('body *')) {
            if (root.contains(element) || element.contains(root)) continue;
            const position = getComputedStyle(element).position;
            if (position === 'fixed' || position === 'sticky') {
              element.setAttribute('data-order-screenshot-overlay', marker);
            }
          }
        }""", token)
        overlay_style = await page.add_style_tag(content=(
            f'[data-order-screenshot-overlay="{token}"] {{ visibility: hidden !important; }}'
        ))
        await root.evaluate("""async element => {
          const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
          const box = element.getBoundingClientRect();
          const top = box.top + window.scrollY;
          // Trigger lazy product images throughout long/multi-shipment orders.
          for (let y = top; y < top + box.height; y += Math.max(400, window.innerHeight)) {
            window.scrollTo(0, y); await pause(60);
          }
          window.scrollTo(0, top);
          await Promise.race([
            Promise.all([...element.querySelectorAll('img')].map(img => img.decode().catch(() => {}))),
            pause(4000)
          ]);
          if (document.fonts) await Promise.race([document.fonts.ready, pause(2000)]);
        }""")
        stage = screenshot_dir() / "staging"
        stage.mkdir(exist_ok=True)
        clip = await root.evaluate("""(root, {padding, marker}) => {
          const box = root.getBoundingClientRect();
          let left = box.left, top = box.top, right = box.right, bottom = box.bottom;
          // Include overflowing labels and controls, rather than cutting them
          // at the panel's CSS border. Ignore offscreen accessibility elements.
          for (const element of root.querySelectorAll('*')) {
            const rect = element.getBoundingClientRect();
            if (rect.width < 2 || rect.height < 2 || getComputedStyle(element).visibility !== 'visible') continue;
            if (rect.right <= box.left || rect.left >= box.right || rect.bottom <= box.top || rect.top >= box.bottom) continue;
            left = Math.min(left, rect.left); top = Math.min(top, rect.top);
            right = Math.max(right, rect.right); bottom = Math.max(bottom, rect.bottom);
          }
          const x = Math.max(0, Math.floor(left + window.scrollX - padding));
          const y = Math.max(0, Math.floor(top + window.scrollY - padding));
          const endX = Math.ceil(right + window.scrollX + padding);
          const endY = Math.ceil(bottom + window.scrollY + padding);
          // Make room for the margin when content ends at the document edge.
          // This invisible spacer extends scroll bounds without moving content.
          const spacer = document.createElement('div');
          spacer.setAttribute('data-order-screenshot-spacer', marker);
          spacer.setAttribute('aria-hidden', 'true');
          spacer.style.cssText = 'position:absolute!important;left:0;top:0;width:1px;height:1px;visibility:hidden!important;pointer-events:none';
          document.body.appendChild(spacer);
          const origin = spacer.getBoundingClientRect();
          spacer.style.left = (endX - 1 - origin.left - window.scrollX) + 'px';
          spacer.style.top = (endY - 1 - origin.top - window.scrollY) + 'px';
          return {x, y, width: endX - x, height: endY - y};
        }""", {"padding": _CAPTURE_PADDING, "marker": token})
        await page.screenshot(path=str(stage / f"{token}.png"), full_page=True,
                              clip=clip, animations="disabled", timeout=15_000)
        (stage / f"{token}.json").write_text(json.dumps(metadata))
        payload["screenshotToken"] = token
    except Exception:
        logger.warning("Screenshot skipped for %s order %s", retailer, number, exc_info=True)
        if region_args is not None:
            try:
                diagnostics = await page.evaluate(_FIND_REGION, {**region_args, "diagnose": True})
                logger.warning("Screenshot crop diagnostics for %s: %s", retailer, diagnostics)
            except Exception:
                pass
    finally:
        try:
            await page.locator(f'[data-order-screenshot-spacer="{token}"]').evaluate_all(
                "elements => elements.forEach(element => element.remove())"
            )
            await page.locator(f'[data-order-screenshot-outside="{token}"]').evaluate_all(
                "elements => elements.forEach(element => element.removeAttribute('data-order-screenshot-outside'))"
            )
            if overlay_style is not None:
                await overlay_style.evaluate("element => element.remove()")
            await page.locator(f'[data-order-screenshot-overlay="{token}"]').evaluate_all(
                "elements => elements.forEach(element => element.removeAttribute('data-order-screenshot-overlay'))"
            )
            if style is not None:
                await style.evaluate("element => element.remove()")
            await page.locator(f'[data-order-screenshot="{token}"]').evaluate_all(
                "elements => elements.forEach(element => element.removeAttribute('data-order-screenshot'))"
            )
        except Exception:
            pass
        if viewport_changed:
            try:
                await page.set_viewport_size(previous_viewport)
            except Exception:
                pass
