"""Walmart bulk order capture using Playwright + extension page hooks."""
from __future__ import annotations

import logging
from typing import Any, Callable

from playwright.async_api import Page

from app.browser_automation.common import (
    LoginRequiredError,
    OrderErrorCallback,
    inject_scripts_for_evaluate,
)
from app.browser_automation.paths import walmart_orders_script_path, walmart_script_paths
from app.browser_automation.session_manager import (
    WALMART_ORDERS_URL,
    looks_like_walmart_signin,
    session_manager,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, Any]], None]
OrderCallback = Callable[[dict[str, Any]], None]


async def _inject_walmart_scripts(page: Page) -> None:
    # orders.js installs network hooks + collectOrders RPC; lib/walmart.js normalizes.
    await inject_scripts_for_evaluate(page, walmart_script_paths())


async def _ensure_orders_hook(page: Page) -> None:
    installed = await page.evaluate("() => !!window.__wmOrdersHookInstalled")
    if not installed:
        await inject_scripts_for_evaluate(page, [walmart_orders_script_path()])


async def _ensure_normalize_lib(page: Page) -> None:
    has = await page.evaluate(
        """() => !!(globalThis.OrderManagerWalmart
          && typeof globalThis.OrderManagerWalmart.normalizeWalmartOrderDetailPayload === 'function')"""
    )
    if not has:
        await _inject_walmart_scripts(page)


async def _collect_order_numbers(page: Page, max_pages: int) -> list[str]:
    await _ensure_orders_hook(page)
    result = await page.evaluate(
        """async (maxPages) => {
          return await new Promise((resolve) => {
            const timeout = setTimeout(() => {
              window.removeEventListener('message', handler);
              resolve({ orders: [], error: 'Timed out collecting Walmart order numbers.' });
            }, Math.min(120000, 25000 * Math.max(1, maxPages)));

            function handler(event) {
              const data = event.data;
              if (!data || data.source !== 'order-manager-walmart-extension') return;
              if (data.type !== 'collectOrdersResult') return;
              clearTimeout(timeout);
              window.removeEventListener('message', handler);
              resolve(data);
            }
            window.addEventListener('message', handler);
            window.postMessage(
              {
                source: 'order-manager-walmart-extension',
                type: 'collectOrdersAcrossPages',
                maxPages: maxPages,
              },
              '*'
            );
          });
        }""",
        max_pages,
    )
    if not isinstance(result, dict):
        return []
    if result.get("error"):
        logger.warning("Walmart collect orders: %s", result.get("error"))
    orders = result.get("orders") or []
    numbers: list[str] = []
    seen: set[str] = set()
    for row in orders:
        if not isinstance(row, dict):
            continue
        n = row.get("orderNumber")
        if n is None:
            continue
        s = str(n).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        numbers.append(s)
    return numbers


async def _wait_for_order_detail_payload(page: Page, order_number: str, timeout_ms: int = 35000) -> dict[str, Any]:
    """Wait until __NEXT_DATA__ / message bus exposes a usable order detail."""
    await _ensure_orders_hook(page)
    payload = await page.evaluate(
        """async ({ orderNumber, timeoutMs }) => {
          function getNextData() {
            try {
              if (window.__NEXT_DATA__) return window.__NEXT_DATA__;
            } catch (e) {}
            try {
              const script = document.querySelector('script#__NEXT_DATA__');
              if (script && script.textContent) return JSON.parse(script.textContent);
            } catch (e) {}
            return null;
          }

          function extractOrder(nextData) {
            if (!nextData || !nextData.props || !nextData.props.pageProps) return null;
            const pageProps = nextData.props.pageProps;
            if (pageProps.order) return pageProps.order;
            if (pageProps.orderDetail) return pageProps.orderDetail;
            if (pageProps.initialData && pageProps.initialData.data && pageProps.initialData.data.order) {
              return pageProps.initialData.data.order;
            }
            return null;
          }

          function matches(order) {
            if (!order || order.id == null) return false;
            const wanted = String(orderNumber).replace(/\\D/g, '');
            const got = String(order.id).replace(/\\D/g, '');
            return !wanted || got === wanted || got.indexOf(wanted) >= 0 || wanted.indexOf(got) >= 0;
          }

          const deadline = Date.now() + timeoutMs;
          return await new Promise((resolve, reject) => {
            function tryResolve(order, raw) {
              if (!matches(order)) return false;
              resolve({ order, raw: raw || null });
              return true;
            }

            function fromNext() {
              const nextData = getNextData();
              const order = extractOrder(nextData);
              return tryResolve(order, nextData);
            }

            function onMessage(event) {
              const data = event.data;
              if (!data || data.source !== 'order-manager-walmart') return;
              if (data.type !== 'orderDetail') return;
              const order = data.payload && data.payload.order;
              if (tryResolve(order, data.payload && data.payload.raw)) {
                window.removeEventListener('message', onMessage);
              }
            }

            window.addEventListener('message', onMessage);
            if (fromNext()) {
              window.removeEventListener('message', onMessage);
              return;
            }

            const timer = setInterval(() => {
              if (fromNext()) {
                clearInterval(timer);
                window.removeEventListener('message', onMessage);
                return;
              }
              if (Date.now() > deadline) {
                clearInterval(timer);
                window.removeEventListener('message', onMessage);
                reject(new Error('Timed out waiting for Walmart order detail.'));
              }
            }, 400);
          });
        }""",
        {"orderNumber": order_number, "timeoutMs": timeout_ms},
    )
    if not isinstance(payload, dict) or not payload.get("order"):
        raise RuntimeError(f"Missing Walmart order detail for {order_number}")
    return payload


async def _normalize_detail(page: Page, detail_payload: dict[str, Any], source_url: str) -> dict[str, Any]:
    await _ensure_normalize_lib(page)
    normalized = await page.evaluate(
        """({ payload, sourceUrl }) => {
          const wm = globalThis.OrderManagerWalmart;
          if (!wm || typeof wm.normalizeWalmartOrderDetailPayload !== 'function') {
            throw new Error('Walmart normalize helpers not loaded.');
          }
          const body = wm.normalizeWalmartOrderDetailPayload(payload, sourceUrl);
          body.source = 'browser-automation';
          return body;
        }""",
        {"payload": detail_payload, "sourceUrl": source_url},
    )
    return normalized


async def _try_capture_invoice_html(page: Page, timeout_ms: int = 8000) -> str | None:
    """Best-effort: wait briefly for invoice-ish content, return serialized HTML."""
    try:
        html = await page.evaluate(
            """async (timeoutMs) => {
              const deadline = Date.now() + timeoutMs;
              while (Date.now() < deadline) {
                const text = (document.body && document.body.innerText) || '';
                if (text && text.length > 500) {
                  // Prefer a print-friendly root if present.
                  const root =
                    document.querySelector('[data-testid*="invoice" i], #invoice, .invoice') ||
                    document.documentElement;
                  return '<!DOCTYPE html><html><head><base href="https://www.walmart.com/"></head><body>'
                    + (root ? root.outerHTML : document.documentElement.outerHTML)
                    + '</body></html>';
                }
                await new Promise((r) => setTimeout(r, 400));
              }
              return null;
            }""",
            timeout_ms,
        )
        return html if isinstance(html, str) and html.strip() else None
    except Exception as exc:
        logger.info("Walmart invoice capture skipped: %s", exc)
        return None


async def _capture_walmart_order_details(
    page: Page,
    order_numbers: list[str],
    *,
    on_progress: ProgressCallback | None = None,
    on_order: OrderCallback | None = None,
    on_order_error: OrderErrorCallback | None = None,
) -> list[dict[str, Any]]:
    def progress(**kwargs: Any) -> None:
        if on_progress:
            on_progress(kwargs)

    captured: list[dict[str, Any]] = []
    for idx, order_number in enumerate(order_numbers):
        progress(
            phase="detail",
            message=f"Capturing order {order_number} ({idx + 1}/{len(order_numbers)})",
            order_id=order_number,
            captured=len(captured),
        )
        detail_url = f"https://www.walmart.com/orders/{order_number}"
        try:
            await page.goto(detail_url, wait_until="domcontentloaded", timeout=60_000)
            if looks_like_walmart_signin(page.url):
                raise LoginRequiredError("Walmart session requires login.")
            await _ensure_orders_hook(page)
            detail = await _wait_for_order_detail_payload(page, str(order_number))
            body = await _normalize_detail(page, detail, page.url)
            try:
                invoice_html = await _try_capture_invoice_html(page, timeout_ms=6000)
                if invoice_html:
                    body["invoiceHtml"] = invoice_html
            except Exception:
                pass
            captured.append(body)
            if on_order:
                on_order(body)
        except LoginRequiredError:
            raise
        except Exception as exc:
            logger.warning("Failed to capture Walmart order %s: %s", order_number, exc)
            if on_order_error:
                on_order_error(order_number, str(exc))

        if idx < len(order_numbers) - 1:
            await page.wait_for_timeout(1200)
    return captured


async def run_walmart_import(
    profile_id: int,
    *,
    max_pages: int = 3,
    order_ids: list[str] | None = None,
    on_progress: ProgressCallback | None = None,
    on_order: OrderCallback | None = None,
    on_order_error: OrderErrorCallback | None = None,
) -> list[dict[str, Any]]:
    """Capture Walmart orders for a profile (list pages → detail pages).

    When *order_ids* is provided, skip list pagination and capture those order
    numbers directly (used for unshipped refresh).
    """

    def progress(**kwargs: Any) -> None:
        if on_progress:
            on_progress(kwargs)

    start_url = WALMART_ORDERS_URL
    if order_ids:
        start_url = f"https://www.walmart.com/orders/{order_ids[0]}"

    session = await session_manager.ensure_session(
        profile_id,
        mode="import",
        start_url=start_url,
        retailer="walmart",
    )
    page = session.page

    if looks_like_walmart_signin(page.url):
        raise LoginRequiredError("Walmart session requires login.")

    # Give SPA a moment, then inject hooks.
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=30_000)
    except Exception:
        pass
    await _ensure_orders_hook(page)

    if order_ids is not None:
        # Preserve caller order; drop empties / dupes.
        seen: set[str] = set()
        order_numbers: list[str] = []
        for raw in order_ids:
            s = str(raw or "").strip()
            if not s or s in seen:
                continue
            seen.add(s)
            order_numbers.append(s)
        progress(
            phase="list",
            message=f"Refreshing {len(order_numbers)} unshipped order(s)…",
            list_count=len(order_numbers),
        )
    else:
        progress(phase="list", message="Collecting order numbers from Walmart…", page=1)
        order_numbers = await _collect_order_numbers(page, max_pages)
        if not order_numbers:
            # Fallback: DOM links on current page only
            order_numbers = await page.evaluate(
                """() => {
                  const out = [];
                  const seen = new Set();
                  document.querySelectorAll('a[href*="/orders/"]').forEach((a) => {
                    const href = a.getAttribute('href') || '';
                    const m = /\\/orders\\/([^/?#]+)/.exec(href);
                    if (!m) return;
                    const id = String(m[1]);
                    if (seen.has(id)) return;
                    seen.add(id);
                    out.push(id);
                  });
                  return out;
                }"""
            )
            if not isinstance(order_numbers, list):
                order_numbers = []

        if looks_like_walmart_signin(page.url):
            raise LoginRequiredError("Walmart session requires login.")

        progress(
            phase="list",
            message=f"Found {len(order_numbers)} order(s)",
            list_count=len(order_numbers),
        )

    captured = await _capture_walmart_order_details(
        page,
        [str(n) for n in order_numbers],
        on_progress=on_progress,
        on_order=on_order,
        on_order_error=on_order_error,
    )
    progress(phase="done", message=f"Captured {len(captured)} order(s)", captured=len(captured))
    return captured
