"""BFMR (api.bfmr.com) my-tracker client for warehouse receiving checks.

Auth: ``API-KEY`` + ``API-SECRET`` headers (same as dealsbot deals client).
Receiving: ``GET /api/v2/my-tracker`` with status / date filters; paginate until done.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any


DEFAULT_BASE_URL = "https://api.bfmr.com"
MY_TRACKER_PATH = "/api/v2/my-tracker"

# Statuses that indicate the warehouse handled the shipment (may still need qty filter).
DEFAULT_RECEIVING_STATUSES = (
    "processed",
    "paid",
    "set_aside",
    "pkg_received",
)

_DEFAULT_PAGE_SIZE = 500
_MAX_LIST_PAGES = 100
_DEFAULT_LOOKBACK_DAYS = 180

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class BfmrTrackerRow:
    tracking_number: str | None
    qty_received: int | None
    received: bool | None
    scanned_at: str | None
    status: str | None
    amount_paid: Decimal | None
    retail_price: Decimal | None
    item_name: str | None
    deal_title: str | None
    order_id: str | None
    my_tracker_id: str | None
    shipment_id: str | None
    qty: int | None
    raw: dict[str, Any] = field(default_factory=dict)

    def is_warehouse_received(self) -> bool:
        """True when BFMR has counted received quantity (not delivery-only)."""
        if self.qty_received is not None and self.qty_received > 0:
            return True
        if self.received is True:
            return True
        return False


def resolve_base_url(base_url: str | None) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        base = DEFAULT_BASE_URL
    return base.rstrip("/")


def _auth_headers(*, api_key: str, api_secret: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "API-KEY": api_key.strip(),
        "API-SECRET": api_secret,
        "User-Agent": _BROWSER_USER_AGENT,
    }


def _first_str(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in ("null", "none", "undefined", "---"):
            return text
    return None


def _money(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _intish(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def _truthy_received(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return int(value) != 0
    text = str(value).strip().casefold()
    if text in ("1", "true", "yes", "y"):
        return True
    if text in ("0", "false", "no", "n"):
        return False
    return None


def _message_from_payload(raw: dict[str, Any]) -> str:
    for key in ("message", "detail", "error"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _parse_tracker_row(row: dict[str, Any]) -> BfmrTrackerRow:
    tracking = _first_str(
        row.get("tracking_number"),
        row.get("tracking"),
        row.get("tracking_id"),
    )
    qty_received = _intish(row.get("qty_received"))
    received = _truthy_received(row.get("received"))
    status = _first_str(row.get("status"))
    my_tracker_id = _first_str(
        row.get("my_tracker_id"),
        row.get("id"),
        row.get("PID"),
    )
    shipment_id = _first_str(
        row.get("SID"),
        row.get("shipment_id"),
        row.get("sil"),
    )
    return BfmrTrackerRow(
        tracking_number=tracking,
        qty_received=qty_received,
        received=received,
        scanned_at=_first_str(row.get("scanned_at"), row.get("date_processed")),
        status=status,
        amount_paid=_money(row.get("amount_paid")),
        retail_price=_money(row.get("retail_price") or row.get("deal_retail_price")),
        item_name=_first_str(row.get("item_name"), row.get("name")),
        deal_title=_first_str(row.get("deal_title"), row.get("title")),
        order_id=_first_str(row.get("order_id"), row.get("order_no"), row.get("order_number")),
        my_tracker_id=my_tracker_id,
        shipment_id=shipment_id,
        qty=_intish(row.get("qty")),
        raw=row,
    )


def _rows_from_payload(raw: dict[str, Any]) -> tuple[list[BfmrTrackerRow], int, int]:
    """Return (rows, page, pages) from a my-tracker response."""
    data = raw.get("data")
    payload: dict[str, Any]
    if isinstance(data, dict):
        payload = data
    else:
        payload = raw

    items = payload.get("my_tracker")
    if items is None:
        items = raw.get("my_tracker")
    if not isinstance(items, list):
        items = []

    rows = [_parse_tracker_row(item) for item in items if isinstance(item, dict)]

    paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
    if not paging and isinstance(raw.get("paging"), dict):
        paging = raw["paging"]
    page = _intish(paging.get("page")) or 1
    pages = _intish(paging.get("pages")) or page
    return rows, page, max(pages, 1)


def _default_date_window(*, lookback_days: int = _DEFAULT_LOOKBACK_DAYS) -> tuple[str, str]:
    end = date.today()
    start = end - timedelta(days=max(lookback_days, 1))
    return start.isoformat(), end.isoformat()


def _fetch_tracker_page(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    page_no: int,
    page_size: int,
    start_date: str,
    end_date: str,
    statuses: str | None,
    timeout_seconds: float,
) -> tuple[list[BfmrTrackerRow], int, int, dict[str, Any]]:
    base = resolve_base_url(base_url)
    query: dict[str, str] = {
        "page_size": str(page_size),
        "page_no": str(page_no),
        "start_date": start_date,
        "end_date": end_date,
    }
    if statuses:
        query["status"] = statuses
    url = f"{base}{MY_TRACKER_PATH}?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(
        url,
        method="GET",
        headers=_auth_headers(api_key=api_key, api_secret=api_secret),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw_text = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        detail = ""
        raw_err: dict[str, Any] = {}
        if err_body.strip():
            try:
                parsed = json.loads(err_body)
                if isinstance(parsed, dict):
                    raw_err = parsed
                    detail = _message_from_payload(parsed)
            except json.JSONDecodeError:
                pass
        if not detail:
            detail = err_body.strip()[:400] or (str(e.reason).strip() if e.reason else "") or "empty response"
        if e.code == 401:
            raise RuntimeError(
                f"BFMR authentication failed (HTTP 401) at {url}: "
                f"{detail or 'API-KEY or API-SECRET incorrect'}"
            ) from e
        if e.code == 429:
            raise RuntimeError(f"BFMR rate limited (HTTP 429) at {url}: {detail or 'Too Many Requests'}") from e
        raise RuntimeError(f"BFMR my-tracker failed (HTTP {e.code}) at {url}: {detail}") from e
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e)).strip() or e.__class__.__name__
        raise RuntimeError(f"BFMR my-tracker request failed ({url}): {reason}") from e

    try:
        raw = json.loads(raw_text) if raw_text else {}
    except json.JSONDecodeError as e:
        raise RuntimeError(f"BFMR my-tracker returned non-JSON (HTTP {status}): {raw_text[:300]}") from e
    if not isinstance(raw, dict):
        raise RuntimeError(f"BFMR my-tracker returned unexpected payload: {raw_text[:300]}")

    if status == 401:
        raise RuntimeError(
            f"BFMR authentication failed (HTTP 401) at {url}: "
            f"{_message_from_payload(raw) or 'API-KEY or API-SECRET incorrect'}"
        )
    if status == 429:
        raise RuntimeError(
            f"BFMR rate limited (HTTP 429) at {url}: {_message_from_payload(raw) or 'Too Many Requests'}"
        )
    if status != 200:
        raise RuntimeError(
            f"BFMR my-tracker failed (HTTP {status}) at {url}: "
            f"{_message_from_payload(raw) or raw_text[:400]}"
        )

    success = raw.get("success")
    if success is False:
        raise RuntimeError(
            f"BFMR my-tracker failed at {url}: {_message_from_payload(raw) or raw_text[:400]}"
        )

    rows, page, pages = _rows_from_payload(raw)
    return rows, page, pages, raw


def list_my_tracker(
    *,
    base_url: str | None,
    api_key: str,
    api_secret: str,
    start_date: str | None = None,
    end_date: str | None = None,
    statuses: tuple[str, ...] | list[str] | None = DEFAULT_RECEIVING_STATUSES,
    page_size: int = _DEFAULT_PAGE_SIZE,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    timeout_seconds: float = 30.0,
) -> list[BfmrTrackerRow]:
    """Paginate GET /api/v2/my-tracker; return all rows for the date/status window."""
    if not (api_key or "").strip():
        raise ValueError("api_key is required")
    if not (api_secret or "").strip():
        raise ValueError("api_secret is required")

    default_start, default_end = _default_date_window(lookback_days=lookback_days)
    start = (start_date or "").strip() or default_start
    end = (end_date or "").strip() or default_end
    status_csv = ",".join(s.strip() for s in (statuses or ()) if (s or "").strip()) or None
    size = max(1, min(int(page_size or _DEFAULT_PAGE_SIZE), 1000))

    all_rows: list[BfmrTrackerRow] = []
    page_no = 1
    total_pages = 1
    while page_no <= total_pages and page_no <= _MAX_LIST_PAGES:
        rows, page, pages, _raw = _fetch_tracker_page(
            base_url=base_url or DEFAULT_BASE_URL,
            api_key=api_key,
            api_secret=api_secret,
            page_no=page_no,
            page_size=size,
            start_date=start,
            end_date=end,
            statuses=status_csv,
            timeout_seconds=timeout_seconds,
        )
        if page_no == 1:
            total_pages = min(max(pages, 1), _MAX_LIST_PAGES)
        all_rows.extend(rows)
        if not rows or page_no >= pages:
            break
        page_no += 1
        # Guard against APIs that ignore page_no.
        if page > 0 and page != page_no - 1 and page >= pages:
            break
    return all_rows


def list_received_tracker_rows(
    *,
    base_url: str | None,
    api_key: str,
    api_secret: str,
    start_date: str | None = None,
    end_date: str | None = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
    timeout_seconds: float = 30.0,
) -> list[BfmrTrackerRow]:
    """Fetch my-tracker rows and keep only warehouse-received ones with a tracking number."""
    rows = list_my_tracker(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        start_date=start_date,
        end_date=end_date,
        statuses=DEFAULT_RECEIVING_STATUSES,
        page_size=page_size,
        timeout_seconds=timeout_seconds,
    )
    out: list[BfmrTrackerRow] = []
    for row in rows:
        if not (row.tracking_number or "").strip():
            continue
        if not row.is_warehouse_received():
            continue
        status = (row.status or "").strip().casefold()
        if status in ("cancelled", "deadline", "return", "returned"):
            continue
        out.append(row)
    return out
