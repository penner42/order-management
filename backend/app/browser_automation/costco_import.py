"""Costco bulk order capture using Playwright + extension normalize helpers.

Mirrors browser-extension Costco bulk import: capture getOnlineOrders GraphQL,
open each order-details SPA route for getOrderDetails, then normalize/merge via
``lib/costco.js``. GraphQL bodies are captured with Playwright response listeners
(Camoufox evaluate worlds cannot reliably hook page ``fetch``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable
from urllib.parse import quote

from playwright.async_api import Page, Response

from app.browser_automation.common import (
    LoginRequiredError,
    OrderErrorCallback,
    inject_scripts_for_evaluate,
)
from app.browser_automation.paths import costco_script_paths
from app.browser_automation.session_manager import (
    COSTCO_MYACCOUNT_URL,
    establish_costco_session,
    looks_like_costco_signin,
    session_manager,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, Any]], None]
OrderCallback = Callable[[dict[str, Any]], None]

COSTCO_GRAPHQL_URL_FRAGMENT = "ecom-api.costco.com/ebusiness/order/v1/orders/graphql"
# Costco returns one GraphQL orders page; treat max_pages as ~10 orders per "page".
ORDERS_PER_PAGE_ESTIMATE = 10


def _looks_like_orders_graphql(payload: Any) -> bool:
    try:
        data = payload.get("data") if isinstance(payload, dict) else None
        go = data.get("getOnlineOrders") if isinstance(data, dict) else None
        return (
            isinstance(go, list)
            and len(go) > 0
            and isinstance((go[0] or {}).get("bcOrders"), list)
        )
    except Exception:
        return False


def _looks_like_order_details_graphql(payload: Any) -> bool:
    try:
        data = payload.get("data") if isinstance(payload, dict) else None
        od = data.get("getOrderDetails") if isinstance(data, dict) else None
        if not isinstance(od, dict):
            return False
        if od.get("orderNumber"):
            return True
        ship = od.get("shipToAddress")
        return isinstance(ship, list) and len(ship) > 0
    except Exception:
        return False


def _extract_order_header_pairs(payload: dict[str, Any]) -> list[dict[str, str]]:
    try:
        go = payload.get("data", {}).get("getOnlineOrders")
        if not isinstance(go, list) or not go:
            return []
        orders = (go[0] or {}).get("bcOrders")
        if not isinstance(orders, list):
            return []
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for row in orders:
            if not isinstance(row, dict):
                continue
            order_number = str(row.get("orderNumber") or "").strip()
            order_header_id = str(row.get("orderHeaderId") or "").strip()
            if not order_number or not order_header_id:
                continue
            if order_number in seen:
                continue
            seen.add(order_number)
            out.append({"orderNumber": order_number, "orderHeaderId": order_header_id})
        return out
    except Exception:
        return []


def _detail_order_number(payload: dict[str, Any] | None) -> str | None:
    try:
        if not isinstance(payload, dict):
            return None
        od = (payload.get("data") or {}).get("getOrderDetails")
        if not isinstance(od, dict):
            return None
        num = str(od.get("orderNumber") or "").strip()
        return num or None
    except Exception:
        return None


def _parse_response_json(payload_text: str | None) -> dict[str, Any] | None:
    if not payload_text:
        return None
    try:
        parsed = json.loads(payload_text)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


class _CostcoGraphqlCapture:
    """Collect Costco order GraphQL responses from Playwright network events."""

    def __init__(self, page: Page) -> None:
        self._page = page
        self._lock = asyncio.Lock()
        self._orders: dict[str, Any] | None = None
        self._orders_url: str | None = None
        self._details: dict[str, Any] | None = None
        self._details_url: str | None = None
        self._details_header_id: str | None = None
        self._details_order_number: str | None = None
        self._handler = self._on_response
        page.on("response", self._handler)

    def detach(self) -> None:
        try:
            self._page.remove_listener("response", self._handler)
        except Exception:
            pass

    async def clear_details(self) -> None:
        async with self._lock:
            self._details = None
            self._details_url = None
            self._details_header_id = None
            self._details_order_number = None

    async def clear_orders(self) -> None:
        async with self._lock:
            self._orders = None
            self._orders_url = None

    async def _on_response(self, response: Response) -> None:
        try:
            url = response.url or ""
            if COSTCO_GRAPHQL_URL_FRAGMENT not in url:
                return
            if response.status != 200:
                return
            payload: dict[str, Any] | None = None
            try:
                raw = await response.json()
                payload = raw if isinstance(raw, dict) else None
            except Exception:
                try:
                    payload = _parse_response_json(await response.text())
                except Exception:
                    payload = None
            if not isinstance(payload, dict):
                return

            header_id: str | None = None
            try:
                req = response.request
                post = req.post_data
                if post:
                    body = json.loads(post)
                    vars_ = body.get("variables") if isinstance(body, dict) else None
                    nums = vars_.get("orderNumbers") if isinstance(vars_, dict) else None
                    if isinstance(nums, list) and nums:
                        header_id = str(nums[0]).strip() or None
            except Exception:
                header_id = None

            async with self._lock:
                if _looks_like_orders_graphql(payload):
                    self._orders = payload
                    self._orders_url = url
                if _looks_like_order_details_graphql(payload):
                    self._details = payload
                    self._details_url = url
                    self._details_header_id = header_id
                    self._details_order_number = _detail_order_number(payload)
        except Exception as exc:
            logger.debug("Costco GraphQL response handler error: %s", exc)

    async def wait_for_orders(self, timeout_ms: int = 45000) -> tuple[dict[str, Any], str | None]:
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while time.monotonic() < deadline:
            async with self._lock:
                if self._orders is not None:
                    return self._orders, self._orders_url
            await asyncio.sleep(0.25)
        raise TimeoutError("Timed out waiting for Costco getOnlineOrders GraphQL.")

    async def wait_for_details(
        self,
        order_header_id: str,
        *,
        order_number: str | None = None,
        timeout_ms: int = 35000,
    ) -> tuple[dict[str, Any], str | None]:
        target_header = str(order_header_id or "").strip()
        target_order = str(order_number or "").strip()
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while time.monotonic() < deadline:
            async with self._lock:
                if self._details is not None:
                    got_header = (self._details_header_id or "").strip()
                    got_order = (self._details_order_number or "").strip()
                    # Accept when request header id or response orderNumber matches.
                    # Do not accept a mismatched sibling order's payload.
                    if target_header and got_header == target_header:
                        return self._details, self._details_url
                    if target_order and got_order == target_order:
                        return self._details, self._details_url
                    if target_order and got_header == target_order:
                        return self._details, self._details_url
                    if not target_header and not target_order:
                        return self._details, self._details_url
            await asyncio.sleep(0.25)
        raise TimeoutError(
            f"Timed out waiting for Costco getOrderDetails GraphQL for {order_header_id}"
        )


async def _inject_costco_lib(page: Page) -> None:
    has = await page.evaluate(
        """() => !!(globalThis.OrderManagerCostco
          && typeof globalThis.OrderManagerCostco.normalizeCostcoOrdersGraphqlPayload === 'function'
          && typeof globalThis.OrderManagerCostco.mergeCostcoOrderDetailsIntoNormalizedOrders === 'function')"""
    )
    if not has:
        await inject_scripts_for_evaluate(page, costco_script_paths())


async def _extract_app_id(page: Page) -> str | None:
    return await page.evaluate(
        """() => {
          try {
            const h = window.location.hash || '';
            const m = /#\\/app\\/([^/]+)\\//.exec(h);
            return m && m[1] ? String(m[1]).trim() : null;
          } catch (e) {
            return null;
          }
        }"""
    )


async def _costco_spa_navigate(page: Page, url: str) -> None:
    """Drive Costco's hash router the way a real tab URL update does.

    Playwright ``page.goto`` on same-document hash changes often updates the
    address bar without Angular refetching GraphQL — which is exactly how we
    were timing out on order details that work in the extension
    (``chrome.tabs.update``). Prefer ``location.assign``; fall back to goto.
    """
    target = str(url or "").strip()
    if not target:
        raise ValueError("Missing Costco SPA URL.")
    try:
        await page.evaluate(
            """(targetUrl) => {
              try {
                if (window.location.href === targetUrl) {
                  // Same URL retry: bounce the hash so the router remounts.
                  const u = new URL(targetUrl);
                  const hash = u.hash || '';
                  window.location.hash = '#';
                  window.location.hash = hash.startsWith('#') ? hash.slice(1) : hash;
                  return 'hash-bounce';
                }
                window.location.assign(targetUrl);
                return 'assign';
              } catch (e) {
                return 'error:' + String(e && e.message ? e.message : e);
              }
            }""",
            target,
        )
    except Exception:
        await page.goto(target, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(600)


async def _resolve_order_details_url(page: Page, app_id: str, order_header_id: str) -> str:
    """Prefer the rendered Costco details link (extension parity); else construct it."""
    header = str(order_header_id or "").strip()
    found = None
    if header:
        try:
            found = await page.evaluate(
                """(headerId) => {
                  const anchors = Array.from(document.querySelectorAll('a[href]'));
                  for (let i = 0; i < anchors.length; i++) {
                    const href = anchors[i].getAttribute('href') || '';
                    if (!href.toLowerCase().includes('orderdetails')) continue;
                    if (!href.includes(headerId)) continue;
                    try {
                      return new URL(href, document.location.href).toString();
                    } catch (e) {}
                  }
                  return null;
                }""",
                header,
            )
        except Exception:
            found = None
    if isinstance(found, str) and found.strip():
        return found.strip()
    return _order_details_url(app_id, header)


async def _ensure_orders_and_purchases_route(page: Page) -> None:
    """Navigate the Costco SPA to ordersandpurchases when we have an app id."""
    url = page.url or ""
    if re.search(r"#/app/[^/]+/ordersandpurchases", url):
        return
    app_id = await _extract_app_id(page)
    if not app_id:
        # Caller should already have run establish_costco_session; wait for hash.
        for _ in range(25):
            if looks_like_costco_signin(page.url or "") and "#/app/" not in (page.url or ""):
                await page.wait_for_timeout(400)
                continue
            app_id = await _extract_app_id(page)
            if app_id:
                break
            await page.wait_for_timeout(400)
    if not app_id:
        raise RuntimeError(
            "Could not determine Costco SPA app id from myaccount URL. "
            "Complete login and open Orders & Purchases once, then retry."
        )
    orders_url = (
        f"https://www.costco.com/myaccount/#/app/{quote(app_id, safe='')}/ordersandpurchases"
    )
    await _costco_spa_navigate(page, orders_url)


def _order_details_url(app_id: str, order_header_id: str) -> str:
    base = f"https://www.costco.com/myaccount/#/app/{quote(app_id, safe='')}/orderdetails"
    return f"{base}?orderNumbers={quote(str(order_header_id).strip(), safe='')}"


async def _capture_order_details(
    page: Page,
    capture: _CostcoGraphqlCapture,
    *,
    app_id: str,
    order_number: str,
    header_id: str,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Open order details and wait for getOrderDetails, with a hard-nav retry."""

    def progress(**kwargs: Any) -> None:
        if on_progress:
            on_progress(kwargs)

    detail_url = await _resolve_order_details_url(page, app_id, header_id)
    await capture.clear_details()
    await _costco_spa_navigate(page, detail_url)
    if looks_like_costco_signin(page.url) and "#/app/" not in (page.url or ""):
        raise LoginRequiredError("Costco session requires login.")

    try:
        detail_payload, _detail_url = await capture.wait_for_details(
            header_id,
            order_number=order_number,
            timeout_ms=20_000,
        )
        return detail_payload
    except TimeoutError:
        progress(
            phase="detail",
            message=f"Retrying Costco order {order_number} (hard navigation)…",
            order_id=order_number,
        )

    # Mirror the extension scrape tab: land on myaccount, then assign the
    # detail hash so Angular boots the orderdetails route cleanly.
    await capture.clear_details()
    await page.goto(COSTCO_MYACCOUNT_URL, wait_until="domcontentloaded", timeout=60_000)
    fresh_app: str | None = None
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        url = page.url or ""
        if looks_like_costco_signin(url) and "#/app/" not in url:
            await page.wait_for_timeout(500)
            url = page.url or ""
            if looks_like_costco_signin(url) and "#/app/" not in url:
                raise LoginRequiredError("Costco session requires login.")
            continue
        fresh_app = await _extract_app_id(page)
        if fresh_app:
            break
        await page.wait_for_timeout(400)
    if fresh_app:
        app_id = fresh_app
    elif not app_id:
        raise RuntimeError("Missing Costco SPA app id after myaccount reload.")
    detail_url = _order_details_url(app_id, header_id)
    await _costco_spa_navigate(page, detail_url)
    if looks_like_costco_signin(page.url) and "#/app/" not in (page.url or ""):
        raise LoginRequiredError("Costco session requires login.")
    detail_payload, _detail_url = await capture.wait_for_details(
        header_id,
        order_number=order_number,
        timeout_ms=35_000,
    )
    return detail_payload


async def _normalize_and_merge(
    page: Page,
    orders_payload: dict[str, Any],
    orders_url: str | None,
    details_by_order_number: dict[str, Any],
) -> list[dict[str, Any]]:
    await _inject_costco_lib(page)
    normalized = await page.evaluate(
        """({ ordersPayload, ordersUrl, detailsByOrderNumber }) => {
          const c = globalThis.OrderManagerCostco;
          if (!c || typeof c.normalizeCostcoOrdersGraphqlPayload !== 'function') {
            throw new Error('Costco normalize helpers not loaded.');
          }
          const orders = c.normalizeCostcoOrdersGraphqlPayload(ordersPayload, ordersUrl || null);
          if (c.mergeCostcoOrderDetailsIntoNormalizedOrders) {
            c.mergeCostcoOrderDetailsIntoNormalizedOrders(orders, detailsByOrderNumber || {});
          }
          for (let i = 0; i < orders.length; i++) {
            if (orders[i] && typeof orders[i] === 'object') {
              orders[i].source = 'browser-automation';
            }
          }
          return orders;
        }""",
        {
            "ordersPayload": orders_payload,
            "ordersUrl": orders_url,
            "detailsByOrderNumber": details_by_order_number,
        },
    )
    if not isinstance(normalized, list):
        return []
    return [o for o in normalized if isinstance(o, dict)]


async def run_costco_import(
    profile_id: int,
    *,
    max_pages: int = 3,
    order_ids: list[str] | None = None,
    on_progress: ProgressCallback | None = None,
    on_order: OrderCallback | None = None,
    on_order_error: OrderErrorCallback | None = None,
) -> list[dict[str, Any]]:
    """Capture Costco orders for a profile (orders GraphQL → detail GraphQL).

    When *order_ids* is provided (unshipped refresh), filter the list capture to
    those order numbers. Orders not present on the current list page are skipped.
    """

    def progress(**kwargs: Any) -> None:
        if on_progress:
            on_progress(kwargs)

    # Start on a blank session tab; we explicitly do home → myaccount below so
    # cookie SSO can hydrate (Costco still shows Sign In on first paint).
    session = await session_manager.ensure_session(
        profile_id,
        mode="import",
        retailer="costco",
    )
    page = session.page

    capture = _CostcoGraphqlCapture(page)
    try:
        await capture.clear_orders()
        progress(phase="list", message="Warming Costco session (home → account)…", page=1)
        if not await establish_costco_session(page):
            raise LoginRequiredError("Costco session requires login.")

        progress(phase="list", message="Opening Costco Orders & Purchases…", page=1)
        await _ensure_orders_and_purchases_route(page)

        # Only fail if we settled on interactive SSO — ignore OAuthLogonCmd hops.
        if looks_like_costco_signin(page.url) and "#/app/" not in (page.url or ""):
            raise LoginRequiredError("Costco session requires login.")

        # If the SPA already fired GraphQL before the listener attached, reload once.
        try:
            orders_payload, orders_url = await capture.wait_for_orders(timeout_ms=12_000)
        except TimeoutError:
            progress(phase="list", message="Refreshing Costco orders list…")
            await capture.clear_orders()
            await page.reload(wait_until="domcontentloaded", timeout=60_000)
            if looks_like_costco_signin(page.url) and "#/app/" not in (page.url or ""):
                raise LoginRequiredError("Costco session requires login.")
            orders_payload, orders_url = await capture.wait_for_orders(timeout_ms=45_000)

        pairs = _extract_order_header_pairs(orders_payload)
        if order_ids is not None:
            wanted = {str(x).strip() for x in order_ids if str(x or "").strip()}
            pairs = [p for p in pairs if p["orderNumber"] in wanted]
            missing = wanted - {p["orderNumber"] for p in pairs}
            if missing:
                logger.warning(
                    "Costco unshipped refresh: %s order(s) not on current list page: %s",
                    len(missing),
                    ", ".join(sorted(missing)[:8]),
                )
            progress(
                phase="list",
                message=f"Refreshing {len(pairs)} unshipped order(s)…",
                list_count=len(pairs),
            )
        else:
            cap = max(1, int(max_pages or 3)) * ORDERS_PER_PAGE_ESTIMATE
            if len(pairs) > cap:
                pairs = pairs[:cap]
            progress(
                phase="list",
                message=f"Found {len(pairs)} order(s)",
                list_count=len(pairs),
            )

        app_id = await _extract_app_id(page)
        if not app_id:
            raise RuntimeError("Missing Costco SPA app id; cannot open order details.")

        details_by_order_number: dict[str, Any] = {}
        for idx, pair in enumerate(pairs):
            order_number = pair["orderNumber"]
            header_id = pair["orderHeaderId"]
            progress(
                phase="detail",
                message=f"Capturing order {order_number} ({idx + 1}/{len(pairs)})",
                order_id=order_number,
                captured=len(details_by_order_number),
            )
            try:
                detail_payload = await _capture_order_details(
                    page,
                    capture,
                    app_id=app_id,
                    order_number=order_number,
                    header_id=header_id,
                    on_progress=on_progress,
                )
                details_by_order_number[order_number] = detail_payload
                # Keep app id current after hard-nav retries.
                fresh_app = await _extract_app_id(page)
                if fresh_app:
                    app_id = fresh_app
            except LoginRequiredError:
                raise
            except Exception as exc:
                logger.warning("Failed to capture Costco order %s: %s", order_number, exc)
                if on_order_error:
                    on_order_error(order_number, str(exc))

            if idx < len(pairs) - 1:
                await page.wait_for_timeout(800)

        # Normalize from the full list payload, then keep only the pairs we targeted.
        all_orders = await _normalize_and_merge(
            page, orders_payload, orders_url or page.url, details_by_order_number
        )
        wanted_numbers = {p["orderNumber"] for p in pairs}
        if wanted_numbers:
            orders = [
                o
                for o in all_orders
                if str((o.get("externalOrder") or {}).get("id") or "").strip() in wanted_numbers
            ]
        else:
            orders = all_orders

        if on_order:
            for body in orders:
                on_order(body)

        progress(phase="done", message=f"Captured {len(orders)} order(s)", captured=len(orders))
        return orders
    finally:
        capture.detach()
