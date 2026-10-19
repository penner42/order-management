"""Read-only Monarch UI capture. Keep undocumented response assumptions isolated here.

Contract verified against the authenticated web app in October 2026: summary
queries share the list's root, the initial offset is omitted, and the list uses
infinite scrolling. Format changes fail closed.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from app.browser_automation.common import LoginRequiredError

HOME = "https://app.monarch.com"
API_HOSTS = {"api.monarch.com", "api.monarchmoney.com", "app.monarch.com", "app.monarchmoney.com"}


def is_transaction_list_query(body):
    query = body.get("query", "") if isinstance(body, dict) else ""
    return isinstance(query, str) and bool(re.search(r"\ballTransactions\s*\(", query) and re.search(r"\bresults\s*\(", query))


class MonarchCaptureError(ValueError):
    pass


def parse_accounts(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise MonarchCaptureError("Monarch account response format changed.")
    if payload.get("errors"):
        raise MonarchCaptureError("Monarch returned an account query error. Reconnect and try again.")
    accounts = payload.get("data", {}).get("accounts")
    if not isinstance(accounts, list):
        raise MonarchCaptureError("Monarch account response format changed.")
    result = []
    for row in accounts:
        if not isinstance(row, dict) or not row.get("id") or not isinstance(row.get("displayName"), str):
            raise MonarchCaptureError("Monarch account identifiers or names are missing.")
        result.append({"id": str(row["id"]), "name": row["displayName"]})
    return result


def parse_transactions(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise MonarchCaptureError("Monarch transaction response format changed.")
    if payload.get("errors"):
        raise MonarchCaptureError("Monarch returned a transaction query error. Reconnect and try again.")
    data = payload.get("data", {})
    collection = data.get("allTransactions", data.get("transactions"))
    if (not isinstance(collection, dict) or type(collection.get("totalCount")) is not int
            or collection["totalCount"] < 0 or not isinstance(collection.get("results"), list)):
        raise MonarchCaptureError("Monarch transaction pagination format changed.")
    result = []
    for row in collection["results"]:
        try:
            account = row["account"]
            amount = Decimal(str(row["amount"]))
            posted_date = date.fromisoformat(row["date"])
            if (not row["id"] or not account["id"] or type(row["pending"]) is not bool
                    or not isinstance(account["displayName"], str) or not amount.is_finite()
                    or amount != amount.quantize(Decimal("0.01"))):
                raise ValueError()
            merchant = row["merchant"]["name"]
            description = row.get("dataProviderDescription") or row.get("plaidName")
            if not isinstance(merchant, str) or not isinstance(description, str):
                raise ValueError()
            if amount > 0 and row.get("isSplitTransaction"):
                raise MonarchCaptureError("Split Monarch deposits need manual reconciliation; the check was not imported.")
            result.append({"transaction_id": str(row["id"]), "account_id": str(account["id"]),
                "account_name": account["displayName"], "posted_date": posted_date, "amount": amount,
                "merchant": merchant, "description": description, "pending": row["pending"]})
        except MonarchCaptureError:
            raise
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise MonarchCaptureError("Monarch transaction fields are missing or invalid; no deposits were imported.") from None
    return collection["totalCount"], result


def is_login_page(url):
    parsed = urlparse(url)
    return parsed.hostname not in {"app.monarch.com", "app.monarchmoney.com"} or any(
        word in parsed.path.lower() for word in ("login", "signin", "sign-in", "authenticate"))


async def _get_captured(queue, page, timeout=35):
    try:
        value = await asyncio.wait_for(queue.get(), timeout)
    except asyncio.TimeoutError:
        if is_login_page(page.url):
            raise LoginRequiredError("Monarch login expired. Reconnect in Settings.") from None
        raise MonarchCaptureError("No supported Monarch response captured. The page may have changed; reconnect and retry.") from None
    if isinstance(value, Exception):
        raise value
    return value


async def capture_monarch(page, start_date, end_date, selected_accounts):
    """Navigate the UI; filter its existing list query and click through every page.

    No stored tokens, independent API authentication, or GraphQL mutations.
    Results are returned only once the reported total is captured completely.
    """
    accounts_queue, transactions_queue = asyncio.Queue(), asyncio.Queue()
    tasks = set()

    async def capture(response):
        try:
            if urlparse(response.url).hostname not in API_HOSTS or "graphql" not in response.url:
                return
            request = response.request
            body = request.post_data_json
            if not isinstance(body, dict):
                return
            query = body.get("query", "")
            if is_transaction_list_query(body):
                if response.status in (401, 403):
                    transactions_queue.put_nowait(LoginRequiredError("Monarch login expired. Reconnect in Settings."))
                    return
                payload = await response.json()
                total, rows = parse_transactions(payload)
                transactions_queue.put_nowait((body.get("variables", {}).get("offset", 0), total, rows))
            else:
                payload = await response.json()
                if isinstance(payload, dict) and "accounts" in (payload.get("data") or {}):
                    accounts_queue.put_nowait(parse_accounts(payload))
        except Exception:
            # Never retain the response or include provider error messages/credentials in logs.
            queue = transactions_queue if is_transaction_list_query(locals().get("body")) else accounts_queue
            queue.put_nowait(MonarchCaptureError("Unsupported Monarch response. No deposits were imported."))

    def on_response(response):
        task = asyncio.create_task(capture(response))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    async def filter_query(route):
        handled = False
        try:
            body = route.request.post_data_json
            if is_transaction_list_query(body):
                variables = body.get("variables")
                if not isinstance(variables, dict) or not isinstance(variables.get("filters"), dict):
                    raise MonarchCaptureError("Monarch transaction query format changed.")
                variables.setdefault("offset", 0)  # Monarch omits the optional offset on its first page.
                # Clear transient UI filters to make the imported range complete and repeatable.
                visibility = {key: value for key, value in variables["filters"].items() if key == "transactionVisibility"}
                variables["filters"] = {**visibility, "startDate": start_date.isoformat(), "endDate": end_date.isoformat(),
                                        "accounts": selected_accounts}
                handled = True
                await route.continue_(post_data=json.dumps(body))
                return
            handled = True
            await route.continue_()
        except Exception:
            transactions_queue.put_nowait(MonarchCaptureError("Monarch transaction query format changed."))
            if not handled:
                await route.abort()

    page.on("response", on_response)
    await page.route("**/*graphql*", filter_query)
    try:
        await page.goto(f"{HOME}/accounts", wait_until="domcontentloaded", timeout=60000)
        accounts = await _get_captured(accounts_queue, page)
        if not selected_accounts:
            return accounts, []
        if not set(selected_accounts) <= {row["id"] for row in accounts}:
            raise MonarchCaptureError("A selected Monarch account is unavailable. Update account selection.")
        await page.goto(f"{HOME}/transactions", wait_until="domcontentloaded", timeout=60000)
        rows, seen, expected_total = [], set(), None
        for _ in range(1000):
            offset, total, batch = await _get_captured(transactions_queue, page)
            if expected_total is None:
                expected_total = total
            if total != expected_total or offset != len(rows) or (not batch and total != 0):
                raise MonarchCaptureError("Monarch pagination changed or capture was incomplete. Retry the check.")
            for row in batch:
                if row["transaction_id"] in seen:
                    raise MonarchCaptureError("Monarch returned duplicate transaction pages. Retry the check.")
                seen.add(row["transaction_id"])
            rows.extend(batch)
            if len(rows) == total:
                return accounts, rows
            if len(rows) > total:
                raise MonarchCaptureError("Monarch pagination total is inconsistent.")
            # Support explicit pagers and Monarch's current infinite-scroll list.
            next_button = page.get_by_role("button", name="Next", exact=True)
            if await next_button.count() != 1 or not await next_button.is_enabled():
                next_button = page.get_by_role("button", name="Next page", exact=True)
            if await next_button.count() != 1 or not await next_button.is_enabled():
                advanced = await page.evaluate("""async () => {
                    const containers = Array.from(document.querySelectorAll('*')).filter(e =>
                        e.clientHeight > 200 && e.scrollHeight > e.clientHeight + 100 &&
                        /auto|scroll/.test(getComputedStyle(e).overflowY));
                    containers.sort((a, b) => b.clientHeight - a.clientHeight);
                    const container = containers[0];
                    if (!container) return false;
                    container.scrollTop = 0;
                    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    container.scrollTop = container.scrollHeight;
                    return true;
                }""")
                if not advanced:
                    raise MonarchCaptureError("Unable to advance Monarch transaction pages. No deposits were imported.")
            else:
                await next_button.click()
        raise MonarchCaptureError("Monarch capture exceeded the page limit. Use a shorter date range.")
    finally:
        page.remove_listener("response", on_response)
        await page.unroute("**/*graphql*", filter_query)
        for task in list(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def monarch_session_logged_in(page):
    accounts, _ = await capture_monarch(page, date.today(), date.today(), [])
    return isinstance(accounts, list)
