"""Costco bulk order capture using Playwright + extension normalize helpers.

Capture getOnlineOrders GraphQL from the orders list, then open each order's
detail route (``#/app/.../orderdetails/{orderNumber}``) so the SPA fires
getOrderDetails. GraphQL bodies are collected via Playwright response listeners;
normalize/merge uses ``lib/costco.js``.
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
    establish_costco_session,
    looks_like_costco_signin,
    session_manager,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, Any]], None]
OrderCallback = Callable[[dict[str, Any]], None]

COSTCO_GRAPHQL_URL_FRAGMENT = "ecom-api.costco.com/ebusiness/order/v1/orders/graphql"
COSTCO_GRAPHQL_URL = f"https://{COSTCO_GRAPHQL_URL_FRAGMENT}"
# Costco returns one GraphQL orders page; treat max_pages as ~10 orders per "page".
ORDERS_PER_PAGE_ESTIMATE = 10

_GRAPHQL_HEADER_ALLOW = {
    "accept",
    "accept-language",
    "client-identifier",
    "content-type",
    "costco-x-authorization",
    "costco-x-wcs-clientid",
    "costco.env",
    "costco.service",
    "origin",
    "referer",
    "user-agent",
}


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


def _get_online_orders_block(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    try:
        if not isinstance(payload, dict):
            return None
        go = (payload.get("data") or {}).get("getOnlineOrders")
        if not isinstance(go, list) or not go:
            return None
        block = go[0]
        return block if isinstance(block, dict) else None
    except Exception:
        return None


def _extract_bc_orders(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    block = _get_online_orders_block(payload)
    if not block:
        return []
    orders = block.get("bcOrders")
    if not isinstance(orders, list):
        return []
    return [row for row in orders if isinstance(row, dict)]


def _extract_order_header_pairs(payload: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in _extract_bc_orders(payload):
        order_number = str(row.get("orderNumber") or "").strip()
        order_header_id = str(row.get("orderHeaderId") or "").strip()
        if not order_number or not order_header_id:
            continue
        if order_number in seen:
            continue
        seen.add(order_number)
        out.append({"orderNumber": order_number, "orderHeaderId": order_header_id})
    return out


def _orders_page_meta(payload: dict[str, Any] | None) -> tuple[int, int, int]:
    """Return (page_number, page_size, total_records) from a getOnlineOrders payload."""
    block = _get_online_orders_block(payload)
    if not block:
        return 1, ORDERS_PER_PAGE_ESTIMATE, 0
    try:
        page_number = int(block.get("pageNumber") or 1)
    except Exception:
        page_number = 1
    try:
        page_size = int(block.get("pageSize") or ORDERS_PER_PAGE_ESTIMATE)
    except Exception:
        page_size = ORDERS_PER_PAGE_ESTIMATE
    try:
        total = int(block.get("totalNumberOfRecords") or 0)
    except Exception:
        total = 0
    if page_size < 1:
        page_size = ORDERS_PER_PAGE_ESTIMATE
    if page_number < 1:
        page_number = 1
    return page_number, page_size, max(0, total)


def _has_more_orders_pages(payload: dict[str, Any] | None, *, page_number: int) -> bool:
    """True when another getOnlineOrders page may exist after *page_number*."""
    rows = _extract_bc_orders(payload)
    if not rows:
        return False
    _, page_size, total = _orders_page_meta(payload)
    if total > 0:
        return page_number * page_size < total
    # No total: assume another page only if this page looks full.
    return len(rows) >= page_size


def _payload_with_bc_orders(
    template: dict[str, Any] | None,
    bc_orders: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a getOnlineOrders-shaped payload containing only *bc_orders*."""
    if isinstance(template, dict):
        try:
            base = json.loads(json.dumps(template))
        except Exception:
            base = {"data": {"getOnlineOrders": [{}]}}
    else:
        base = {"data": {"getOnlineOrders": [{}]}}
    go = (base.get("data") or {}).get("getOnlineOrders")
    if not isinstance(go, list) or not go or not isinstance(go[0], dict):
        base = {"data": {"getOnlineOrders": [{}]}}
        go = base["data"]["getOnlineOrders"]
    go[0]["bcOrders"] = list(bc_orders)
    return base


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


def _filter_graphql_headers(raw: dict[str, str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        lk = key.lower()
        if lk in _GRAPHQL_HEADER_ALLOW and value.strip():
            out[lk] = value
    if "content-type" not in out:
        # Costco's orders API rejects plain application/json (API gateway 400).
        out["content-type"] = "application/json-patch+json"
    return out


def _reshape_order_details_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Adapt flat OrderHeaderType payloads to the shipToAddress shape costco.js expects."""
    try:
        od = (payload.get("data") or {}).get("getOrderDetails")
    except Exception:
        return payload
    if not isinstance(od, dict):
        return payload
    if not od.get("orderNumber") and od.get("sourceOrderNumber"):
        od["orderNumber"] = od.get("sourceOrderNumber")
    if not isinstance(od.get("shipToAddress"), list) or not od.get("shipToAddress"):
        items = od.get("orderLineItems")
        if isinstance(items, list):
            od["shipToAddress"] = [
                {
                    "firstName": od.get("firstName"),
                    "lastName": od.get("lastName"),
                    "line1": od.get("line1"),
                    "line2": od.get("line2"),
                    "city": od.get("city"),
                    "state": od.get("state"),
                    "postalCode": od.get("postalCode"),
                    "countryCode": od.get("countryCode"),
                    "phoneNumber": od.get("phoneNumber"),
                    "emailAddress": od.get("emailAddress"),
                    "orderLineItems": items,
                }
            ]
    return payload


class _CostcoGraphqlCapture:
    """Collect Costco order GraphQL responses from Playwright network events."""

    def __init__(self, page: Page) -> None:
        self._page = page
        self._lock = asyncio.Lock()
        self._orders: dict[str, Any] | None = None
        self._orders_url: str | None = None
        self._orders_query: str | None = None
        self._orders_operation_name: str | None = None
        self._orders_variables: dict[str, Any] | None = None
        self._details: dict[str, Any] | None = None
        self._details_url: str | None = None
        self._details_header_id: str | None = None
        self._details_order_number: str | None = None
        self._graphql_url: str = COSTCO_GRAPHQL_URL
        self._graphql_headers: dict[str, str] = {}
        self._details_query: str | None = None
        self._details_operation_name: str | None = None
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
            op_name: str | None = None
            req_query: str | None = None
            req_vars: dict[str, Any] | None = None
            try:
                post = response.request.post_data
                if post:
                    body = json.loads(post)
                    if isinstance(body, dict):
                        if isinstance(body.get("operationName"), str):
                            op_name = body["operationName"]
                        if isinstance(body.get("query"), str) and body["query"].strip():
                            req_query = body["query"].strip()
                        vars_ = body.get("variables") if isinstance(body.get("variables"), dict) else None
                        if isinstance(vars_, dict):
                            req_vars = vars_
                            nums = vars_.get("orderNumbers")
                            if isinstance(nums, list) and nums:
                                header_id = str(nums[0]).strip() or None
            except Exception:
                header_id = None

            looks_orders = _looks_like_orders_graphql(payload)
            looks_details = _looks_like_order_details_graphql(payload)

            if looks_orders:
                try:
                    hdrs = _filter_graphql_headers(await response.request.all_headers())
                except Exception:
                    hdrs = {}
            else:
                hdrs = {}

            async with self._lock:
                if looks_orders:
                    self._orders = payload
                    self._orders_url = url
                    self._graphql_url = url.split("?")[0] or COSTCO_GRAPHQL_URL
                    if hdrs:
                        self._graphql_headers = hdrs
                    if req_query and "getOnlineOrders" in req_query:
                        self._orders_query = req_query
                        self._orders_operation_name = op_name
                        if isinstance(req_vars, dict):
                            self._orders_variables = dict(req_vars)
                if looks_details:
                    self._details = payload
                    self._details_url = url
                    self._details_header_id = header_id
                    self._details_order_number = _detail_order_number(payload)
                    if req_query and "getOrderDetails" in req_query:
                        self._details_query = req_query
                        self._details_operation_name = op_name
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
                    # SPA variables.orderNumbers is the order number; accept either match.
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

    async def fetch_order_details(self, order_number: str) -> dict[str, Any]:
        """Replay the SPA-captured getOrderDetails query with the order number."""
        order_num = str(order_number or "").strip()
        if not order_num:
            raise ValueError("Missing orderNumber for getOrderDetails.")
        async with self._lock:
            query = self._details_query
            op_name = self._details_operation_name
            url = self._graphql_url or COSTCO_GRAPHQL_URL
            headers = dict(self._graphql_headers or {})
        if not query:
            raise RuntimeError("No Costco getOrderDetails query captured yet.")
        if not headers.get("costco-x-authorization") and not headers.get("authorization"):
            raise RuntimeError("Missing Costco GraphQL auth headers.")
        send_headers = dict(headers)
        if "content-type" not in {k.lower() for k in send_headers}:
            send_headers["content-type"] = "application/json-patch+json"

        result = await self._page.evaluate(
            """async ({ url, headers, query, operationName, orderNumber }) => {
              const body = {
                query,
                variables: { orderNumbers: [String(orderNumber)] },
              };
              if (operationName) body.operationName = operationName;
              const resp = await fetch(url, {
                method: 'POST',
                credentials: 'include',
                headers: headers || {},
                body: JSON.stringify(body),
              });
              const text = await resp.text();
              let json = null;
              try { json = JSON.parse(text); } catch (e) { json = null; }
              return { status: resp.status, json, textBody: text.slice(0, 500) };
            }""",
            {
                "url": url,
                "headers": send_headers,
                "query": query,
                "operationName": op_name,
                "orderNumber": order_num,
            },
        )
        if not isinstance(result, dict) or int(result.get("status") or 0) != 200:
            raise RuntimeError(
                f"Costco getOrderDetails HTTP {result.get('status') if isinstance(result, dict) else '?'}"
            )
        payload = result.get("json")
        if not isinstance(payload, dict):
            raise RuntimeError("Costco getOrderDetails returned non-JSON.")
        payload = _reshape_order_details_payload(payload)
        if not _looks_like_order_details_graphql(payload):
            raise RuntimeError(f"Costco getOrderDetails empty for orderNumber={order_num}")
        async with self._lock:
            self._details = payload
            self._details_url = url
            self._details_header_id = order_num
            self._details_order_number = _detail_order_number(payload)
        return payload

    async def fetch_orders_page(self, page_number: int) -> dict[str, Any]:
        """Replay the SPA-captured getOnlineOrders query for *page_number*."""
        page_num = max(1, int(page_number or 1))
        async with self._lock:
            query = self._orders_query
            op_name = self._orders_operation_name
            base_vars = dict(self._orders_variables or {})
            url = self._graphql_url or COSTCO_GRAPHQL_URL
            headers = dict(self._graphql_headers or {})
        if not query:
            raise RuntimeError("No Costco getOnlineOrders query captured yet.")
        if not headers.get("costco-x-authorization") and not headers.get("authorization"):
            raise RuntimeError("Missing Costco GraphQL auth headers.")
        variables = dict(base_vars)
        variables["pageNumber"] = page_num
        send_headers = dict(headers)
        if "content-type" not in {k.lower() for k in send_headers}:
            send_headers["content-type"] = "application/json-patch+json"

        result = await self._page.evaluate(
            """async ({ url, headers, query, operationName, variables }) => {
              const body = { query, variables: variables || {} };
              if (operationName) body.operationName = operationName;
              const resp = await fetch(url, {
                method: 'POST',
                credentials: 'include',
                headers: headers || {},
                body: JSON.stringify(body),
              });
              const text = await resp.text();
              let json = null;
              try { json = JSON.parse(text); } catch (e) { json = null; }
              return { status: resp.status, json, textBody: text.slice(0, 500) };
            }""",
            {
                "url": url,
                "headers": send_headers,
                "query": query,
                "operationName": op_name,
                "variables": variables,
            },
        )
        if not isinstance(result, dict) or int(result.get("status") or 0) != 200:
            raise RuntimeError(
                f"Costco getOnlineOrders HTTP {result.get('status') if isinstance(result, dict) else '?'}"
                f" for page {page_num}"
            )
        payload = result.get("json")
        if not isinstance(payload, dict):
            raise RuntimeError(f"Costco getOnlineOrders returned non-JSON for page {page_num}.")
        if not _looks_like_orders_graphql(payload) and not _extract_bc_orders(payload):
            # Empty page is a valid end-of-list signal; still return the payload.
            go = (payload.get("data") or {}).get("getOnlineOrders")
            if not isinstance(go, list):
                raise RuntimeError(f"Costco getOnlineOrders empty for page {page_num}")
        async with self._lock:
            self._orders = payload
            self._orders_url = url
        return payload


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
    """Drive Costco's hash router via location.assign (same-document goto is a no-op)."""
    target = str(url or "").strip()
    if not target:
        raise ValueError("Missing Costco SPA URL.")
    try:
        await page.evaluate(
            """(targetUrl) => {
              try {
                if (window.location.href === targetUrl) {
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


async def _ensure_orders_and_purchases_route(page: Page) -> None:
    """Navigate the Costco SPA to ordersandpurchases when we have an app id."""
    url = page.url or ""
    if re.search(r"#/app/[^/]+/ordersandpurchases", url):
        return
    app_id = await _extract_app_id(page)
    if not app_id:
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


def _order_details_path_url(app_id: str, order_number: str) -> str:
    """Costco detail route: /orderdetails/{orderNumber} (not orderHeaderId)."""
    return (
        f"https://www.costco.com/myaccount/#/app/{quote(app_id, safe='')}"
        f"/orderdetails/{quote(str(order_number).strip(), safe='')}"
    )


async def _wait_for_order_detail_links(page: Page, timeout_ms: int = 12000) -> int:
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    while time.monotonic() < deadline:
        count = await page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]')).filter((a) =>
              /orderdetails/i.test(a.getAttribute('href') || '')
            ).length"""
        )
        try:
            n = int(count or 0)
        except Exception:
            n = 0
        if n > 0:
            return n
        await page.wait_for_timeout(300)
    return 0


async def _click_order_details_link(page: Page, order_number: str) -> dict[str, Any]:
    return await page.evaluate(
        """(orderNumber) => {
          const target = String(orderNumber || '').trim();
          if (!target) return { ok: false, reason: 'missing-order-number' };
          const anchors = Array.from(document.querySelectorAll('a[href]'));
          let match = null;
          for (let i = 0; i < anchors.length; i++) {
            const a = anchors[i];
            const href = a.getAttribute('href') || '';
            if (!/orderdetails/i.test(href)) continue;
            if (
              href.includes('/orderdetails/' + target) ||
              href.includes('/orderdetails/' + encodeURIComponent(target))
            ) {
              match = a;
              break;
            }
            if (!match && href.includes(target)) match = a;
          }
          if (!match) return { ok: false, reason: 'not-found' };
          const href = match.getAttribute('href') || '';
          try { match.click(); } catch (e) {
            return { ok: false, reason: 'click-failed', href };
          }
          return { ok: true, href };
        }""",
        str(order_number or "").strip(),
    )


async def _capture_order_details(
    page: Page,
    capture: _CostcoGraphqlCapture,
    *,
    order_number: str,
    header_id: str,
) -> dict[str, Any]:
    """Open order details; Costco's GraphQL orderNumbers arg is the order number."""

    await _ensure_orders_and_purchases_route(page)
    await _wait_for_order_detail_links(page)
    await capture.clear_details()

    clicked = await _click_order_details_link(page, order_number)
    if isinstance(clicked, dict) and clicked.get("ok"):
        try:
            detail_payload, _ = await capture.wait_for_details(
                header_id,
                order_number=order_number,
                timeout_ms=25_000,
            )
            return detail_payload
        except TimeoutError:
            logger.debug("Costco click detail timeout for %s; trying path nav", order_number)

    app_id = await _extract_app_id(page)
    if app_id:
        await capture.clear_details()
        await _costco_spa_navigate(page, _order_details_path_url(app_id, order_number))
        try:
            detail_payload, _ = await capture.wait_for_details(
                header_id,
                order_number=order_number,
                timeout_ms=25_000,
            )
            return detail_payload
        except TimeoutError:
            logger.debug("Costco path-nav detail timeout for %s; trying direct fetch", order_number)

    if capture._details_query:
        await capture.clear_details()
        return await capture.fetch_order_details(order_number)

    raise RuntimeError(f"Failed to capture Costco getOrderDetails for order {order_number}")


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

    When *order_ids* is provided (unshipped refresh), filter list capture to those
    order numbers and paginate getOnlineOrders until all are found or *max_pages*
    is exhausted. Never falls back to applying the full list when nothing matches.
    """

    def progress(**kwargs: Any) -> None:
        if on_progress:
            on_progress(kwargs)

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

        if looks_like_costco_signin(page.url) and "#/app/" not in (page.url or ""):
            raise LoginRequiredError("Costco session requires login.")

        try:
            orders_payload, orders_url = await capture.wait_for_orders(timeout_ms=12_000)
        except TimeoutError:
            progress(phase="list", message="Refreshing Costco orders list…")
            await capture.clear_orders()
            await page.reload(wait_until="domcontentloaded", timeout=60_000)
            if looks_like_costco_signin(page.url) and "#/app/" not in (page.url or ""):
                raise LoginRequiredError("Costco session requires login.")
            orders_payload, orders_url = await capture.wait_for_orders(timeout_ms=45_000)

        page_limit = max(1, int(max_pages or 3))
        pairs: list[dict[str, str]] = []
        matched_bc_orders: list[dict[str, Any]] = []

        if order_ids is not None:
            wanted = {str(x).strip() for x in order_ids if str(x or "").strip()}
            found: set[str] = set()
            page_num = 1
            current_payload: dict[str, Any] | None = orders_payload

            while current_payload is not None:
                page_pairs = _extract_order_header_pairs(current_payload)
                page_rows = _extract_bc_orders(current_payload)
                rows_by_number = {
                    str(row.get("orderNumber") or "").strip(): row
                    for row in page_rows
                    if str(row.get("orderNumber") or "").strip()
                }
                for pair in page_pairs:
                    num = pair["orderNumber"]
                    if num not in wanted or num in found:
                        continue
                    found.add(num)
                    pairs.append(pair)
                    row = rows_by_number.get(num)
                    if row is not None:
                        matched_bc_orders.append(row)

                missing = wanted - found
                progress(
                    phase="list",
                    message=(
                        f"List page {page_num}: found {len(found)}/{len(wanted)} "
                        f"unshipped order(s)…"
                    ),
                    page=page_num,
                    list_count=len(pairs),
                )
                if not missing:
                    break
                if page_num >= page_limit:
                    break
                if not _has_more_orders_pages(current_payload, page_number=page_num):
                    break
                page_num += 1
                try:
                    current_payload = await capture.fetch_orders_page(page_num)
                except Exception as exc:
                    logger.warning(
                        "Costco unshipped refresh: failed to fetch list page %s: %s",
                        page_num,
                        exc,
                    )
                    break

            missing = wanted - found
            if missing:
                logger.warning(
                    "Costco unshipped refresh: %s order(s) not found after %s list page(s): %s",
                    len(missing),
                    page_num,
                    ", ".join(sorted(missing)[:8]),
                )
            progress(
                phase="list",
                message=(
                    f"Refreshing {len(pairs)} unshipped order(s) "
                    f"(scanned {page_num} list page(s))…"
                ),
                page=page_num,
                list_count=len(pairs),
            )
            orders_payload = _payload_with_bc_orders(orders_payload, matched_bc_orders)
        else:
            pairs = _extract_order_header_pairs(orders_payload)
            cap = page_limit * ORDERS_PER_PAGE_ESTIMATE
            if len(pairs) > cap:
                pairs = pairs[:cap]
            progress(
                phase="list",
                message=f"Found {len(pairs)} order(s)",
                list_count=len(pairs),
            )

        if not pairs:
            progress(phase="done", message="Captured 0 order(s)", captured=0)
            return []

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
                    order_number=order_number,
                    header_id=header_id,
                )
                details_by_order_number[order_number] = detail_payload
            except LoginRequiredError:
                raise
            except Exception as exc:
                logger.warning("Failed to capture Costco order %s: %s", order_number, exc)
                if on_order_error:
                    on_order_error(order_number, str(exc))

            if idx < len(pairs) - 1:
                await page.wait_for_timeout(400)

        all_orders = await _normalize_and_merge(
            page, orders_payload, orders_url or page.url, details_by_order_number
        )
        wanted_numbers = {p["orderNumber"] for p in pairs}
        orders = [
            o
            for o in all_orders
            if str((o.get("externalOrder") or {}).get("id") or "").strip() in wanted_numbers
        ]

        if on_order:
            for body in orders:
                on_order(body)

        progress(phase="done", message=f"Captured {len(orders)} order(s)", captured=len(orders))
        return orders
    finally:
        capture.detach()
