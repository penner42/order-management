"""BFMR (api.bfmr.com) my-tracker client for receiving + tracking submit.

Auth: ``API-KEY`` + ``API-SECRET`` headers (same as dealsbot deals client).
Receiving: ``GET /api/v2/my-tracker`` with status / date filters; paginate until done.
Submit: ``POST /api/v2/my-tracker`` create-shipment (RID + PID + order_no + TN + qty).
"""
from __future__ import annotations

from app.submission_policy import require_group_submissions_enabled
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence


DEFAULT_BASE_URL = "https://api.bfmr.com"
MY_TRACKER_PATH = "/api/v2/my-tracker"
_MAX_TRACKER_DATA_PER_REQUEST = 500

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
    payout_price: Decimal | None
    retail_price: Decimal | None
    item_name: str | None
    deal_title: str | None
    order_id: str | None
    my_tracker_id: str | None
    reserve_id: str | None
    purchase_id: str | None
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

    def needs_tracking_submit(self) -> bool:
        """True for purchased rows that have an order number but no tracking yet."""
        status = (self.status or "").strip().casefold().replace(" ", "_")
        if status != "purchased":
            return False
        if not (self.order_id or "").strip():
            return False
        if (self.tracking_number or "").strip():
            return False
        if not (self.reserve_id or "").strip():
            return False
        if not (self.purchase_id or "").strip():
            return False
        return True


@dataclass
class BfmrTrackingEntry:
    """One create-shipment (or update-shipment) row for POST /api/v2/my-tracker."""

    reserve_id: str
    purchase_id: str
    order_no: str
    tracking_number: str
    qty: int
    shipment_id: str | None = None


@dataclass
class BfmrSubmitResult:
    success: bool
    message: str
    affected: int | None = None
    raw: dict[str, Any] | None = None
    succeeded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    # tracking number -> (display_id, api_id) where api_id is purchase_id
    submission_ids: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)


def retailer_identifiers(row: BfmrTrackerRow) -> list[tuple[str, str]]:
    links = row.raw.get("retailer_links")
    if not isinstance(links, list):
        return []
    return [
        (str(link.get("retailer") or "").strip().casefold(), str(link.get("identifier_id") or "").strip().casefold())
        for link in links if isinstance(link, dict) and link.get("retailer") and link.get("identifier_id")
    ]


def create_purchase(*, base_url: str | None, api_key: str, api_secret: str,
                    reserve_id: str, order_no: str, qty: int) -> dict[str, Any]:
    """Register one purchase; callers reconcile a fresh tracker read before retrying."""
    require_group_submissions_enabled()
    if not api_key.strip() or not api_secret.strip():
        raise ValueError("BFMR API key and secret are required")
    if not reserve_id.strip() or not order_no.strip() or isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
        raise ValueError("Reservation, order number and positive integer quantity are required")
    return _post_tracker_data(
        base_url=base_url or DEFAULT_BASE_URL, api_key=api_key, api_secret=api_secret,
        tracker_data=[{"reserve_id": reserve_id, "order_no": order_no, "qty": qty,
                       "purchase_id": None, "shipment_id": None, "tracking_number": None}],
        timeout_seconds=30.0,
    )


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
    reserve_id = _first_str(row.get("reserve_id"), row.get("RID"))
    purchase_id = _first_str(row.get("purchase_id"), row.get("PID"))
    # Receiving receipt id prefers my_tracker_id; fall back to id then PID.
    my_tracker_id = _first_str(
        row.get("my_tracker_id"),
        row.get("id"),
        purchase_id,
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
        payout_price=_money(row.get("payout_price")),
        retail_price=_money(row.get("retail_price") or row.get("deal_retail_price")),
        item_name=_first_str(row.get("item_name"), row.get("name")),
        deal_title=_first_str(row.get("deal_title"), row.get("title")),
        order_id=_first_str(row.get("order_id"), row.get("order_no"), row.get("order_number")),
        my_tracker_id=my_tracker_id,
        reserve_id=reserve_id,
        purchase_id=purchase_id,
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
    # end_date is inclusive on the website; bump one day so TZ / exclusive-end APIs
    # still include "today" reservations and scans.
    end = date.today() + timedelta(days=1)
    start = date.today() - timedelta(days=max(lookback_days, 1))
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
    quick_filter: str | None,
    timeout_seconds: float,
) -> tuple[list[BfmrTrackerRow], int, int, dict[str, Any]]:
    base = resolve_base_url(base_url)
    query: dict[str, str] = {
        "page_size": str(page_size),
        "page_no": str(page_no),
        "start_date": start_date,
        "end_date": end_date,
    }
    # Prefer quick_filter when not narrowing by status. Comma-separated ``status``
    # is documented but has been observed to interact badly with date filtering
    # (API docs default both dates to 2023-11-03 when the request is rejected /
    # partially applied).
    if statuses:
        query["status"] = statuses
    elif quick_filter:
        query["quick_filter"] = quick_filter
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


@dataclass
class BfmrTrackerListResult:
    rows: list[BfmrTrackerRow]
    start_date: str
    end_date: str
    request_urls: list[str] = field(default_factory=list)
    raw_total: int = 0


def list_my_tracker(
    *,
    base_url: str | None,
    api_key: str,
    api_secret: str,
    start_date: str | None = None,
    end_date: str | None = None,
    statuses: tuple[str, ...] | list[str] | None = None,
    quick_filter: str | None = "all",
    page_size: int = _DEFAULT_PAGE_SIZE,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    timeout_seconds: float = 30.0,
) -> BfmrTrackerListResult:
    """Paginate GET /api/v2/my-tracker; return all rows for the date window.

    By default uses ``quick_filter=all`` and no ``status`` filter so BFMR applies
    the date range reliably; callers filter statuses client-side.
    """
    if not (api_key or "").strip():
        raise ValueError("api_key is required")
    if not (api_secret or "").strip():
        raise ValueError("api_secret is required")

    default_start, default_end = _default_date_window(lookback_days=lookback_days)
    start = (start_date or "").strip() or default_start
    end = (end_date or "").strip() or default_end
    status_csv = ",".join(s.strip() for s in (statuses or ()) if (s or "").strip()) or None
    qf = None if status_csv else ((quick_filter or "").strip() or None)
    size = max(1, min(int(page_size or _DEFAULT_PAGE_SIZE), 1000))

    all_rows: list[BfmrTrackerRow] = []
    request_urls: list[str] = []
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
            quick_filter=qf,
            timeout_seconds=timeout_seconds,
        )
        # Reconstruct URL for diagnostics (no secrets).
        base = resolve_base_url(base_url)
        q: dict[str, str] = {
            "page_size": str(size),
            "page_no": str(page_no),
            "start_date": start,
            "end_date": end,
        }
        if status_csv:
            q["status"] = status_csv
        elif qf:
            q["quick_filter"] = qf
        request_urls.append(f"{base}{MY_TRACKER_PATH}?{urllib.parse.urlencode(q)}")
        if page_no == 1:
            total_pages = min(max(pages, 1), _MAX_LIST_PAGES)
        all_rows.extend(rows)
        if not rows or page_no >= pages:
            break
        page_no += 1
        # Guard against APIs that ignore page_no.
        if page > 0 and page != page_no - 1 and page >= pages:
            break
    return BfmrTrackerListResult(
        rows=all_rows,
        start_date=start,
        end_date=end,
        request_urls=request_urls,
        raw_total=len(all_rows),
    )


def list_received_tracker_rows(
    *,
    base_url: str | None,
    api_key: str,
    api_secret: str,
    start_date: str | None = None,
    end_date: str | None = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
    timeout_seconds: float = 30.0,
) -> BfmrTrackerListResult:
    """Fetch my-tracker (all statuses in date window); keep warehouse-received rows."""
    fetched = list_my_tracker(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        start_date=start_date,
        end_date=end_date,
        statuses=None,
        quick_filter="all",
        page_size=page_size,
        timeout_seconds=timeout_seconds,
    )
    out: list[BfmrTrackerRow] = []
    for row in fetched.rows:
        if not (row.tracking_number or "").strip():
            continue
        if not row.is_warehouse_received():
            continue
        status = (row.status or "").strip().casefold().replace(" ", "_")
        if status in ("cancelled", "deadline", "return", "returned"):
            continue
        # Keep processed / paid / set_aside / pkg_received / in_review / etc. when
        # qty_received or received already passed is_warehouse_received().
        out.append(row)
    return BfmrTrackerListResult(
        rows=out,
        start_date=fetched.start_date,
        end_date=fetched.end_date,
        request_urls=fetched.request_urls,
        raw_total=fetched.raw_total,
    )


def list_purchases_needing_tracking(
    *,
    base_url: str | None,
    api_key: str,
    api_secret: str,
    start_date: str | None = None,
    end_date: str | None = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
    timeout_seconds: float = 30.0,
) -> BfmrTrackerListResult:
    """Fetch my-tracker; keep purchased rows that still need a tracking number."""
    fetched = list_my_tracker(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        start_date=start_date,
        end_date=end_date,
        statuses=None,
        quick_filter="all",
        page_size=page_size,
        timeout_seconds=timeout_seconds,
    )
    out = [row for row in fetched.rows if row.needs_tracking_submit()]
    return BfmrTrackerListResult(
        rows=out,
        start_date=fetched.start_date,
        end_date=fetched.end_date,
        request_urls=fetched.request_urls,
        raw_total=fetched.raw_total,
    )


def index_purchases_needing_tracking_by_order(
    rows: Sequence[BfmrTrackerRow],
) -> dict[str, BfmrTrackerRow]:
    """Map casefold(order_id) → first purchase row needing tracking.

    If multiple purchases share an order_id, the first seen wins; callers that
    consume a row should pop or otherwise mark it used when splitting across TNs.
    """
    out: dict[str, BfmrTrackerRow] = {}
    for row in rows:
        oid = (row.order_id or "").strip()
        if not oid:
            continue
        key = oid.casefold()
        if key not in out:
            out[key] = row
    return out


def _entry_to_tracker_data(entry: BfmrTrackingEntry) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "reserve_id": str(entry.reserve_id).strip(),
        "purchase_id": str(entry.purchase_id).strip(),
        "order_no": str(entry.order_no).strip(),
        "tracking_number": str(entry.tracking_number).strip(),
        "qty": int(entry.qty),
    }
    sid = (entry.shipment_id or "").strip()
    if sid:
        payload["shipment_id"] = sid
    else:
        payload["shipment_id"] = None
    return payload


def _tracking_key_from_invalid_item(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    return _first_str(
        item.get("tracking_number"),
        item.get("tracking"),
        item.get("order_no"),
        item.get("order_id"),
        item.get("purchase_id"),
        item.get("PID"),
    )


def _post_tracker_data(
    *,
    base_url: str,
    api_key: str,
    api_secret: str,
    tracker_data: list[dict[str, Any]],
    timeout_seconds: float,
) -> dict[str, Any]:
    require_group_submissions_enabled()
    url = f"{resolve_base_url(base_url)}{MY_TRACKER_PATH}"
    body = json.dumps({"tracker_data": tracker_data}).encode("utf-8")
    headers = _auth_headers(api_key=api_key, api_secret=api_secret)
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw_text = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        detail = ""
        if err_body.strip():
            try:
                parsed = json.loads(err_body)
                if isinstance(parsed, dict):
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
        raise RuntimeError(f"BFMR my-tracker submit failed (HTTP {e.code}) at {url}: {detail}") from e
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e)).strip() or e.__class__.__name__
        raise RuntimeError(f"BFMR my-tracker submit request failed ({url}): {reason}") from e

    try:
        raw = json.loads(raw_text) if raw_text else {}
    except json.JSONDecodeError as e:
        raise RuntimeError(f"BFMR my-tracker submit returned non-JSON (HTTP {status}): {raw_text[:300]}") from e
    if not isinstance(raw, dict):
        raise RuntimeError(f"BFMR my-tracker submit returned unexpected payload: {raw_text[:300]}")

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
            f"BFMR my-tracker submit failed (HTTP {status}) at {url}: "
            f"{_message_from_payload(raw) or raw_text[:400]}"
        )
    if raw.get("success") is False:
        raise RuntimeError(
            f"BFMR my-tracker submit failed at {url}: {_message_from_payload(raw) or raw_text[:400]}"
        )
    return raw


def submit_trackings(
    *,
    base_url: str | None,
    api_key: str,
    api_secret: str,
    trackings: Sequence[BfmrTrackingEntry],
    timeout_seconds: float = 30.0,
) -> BfmrSubmitResult:
    """POST create/update shipment rows to ``/api/v2/my-tracker`` (max 500 per request)."""
    require_group_submissions_enabled()
    if not (api_key or "").strip():
        raise ValueError("api_key is required")
    if not (api_secret or "").strip():
        raise ValueError("api_secret is required")
    if not trackings:
        raise ValueError("At least one tracking entry is required")

    entries: list[BfmrTrackingEntry] = []
    for entry in trackings:
        rid = (entry.reserve_id or "").strip()
        pid = (entry.purchase_id or "").strip()
        order_no = (entry.order_no or "").strip()
        tn = (entry.tracking_number or "").strip()
        qty = int(entry.qty or 0)
        if not rid:
            raise ValueError("reserve_id is required")
        if not pid:
            raise ValueError("purchase_id is required")
        if not order_no:
            raise ValueError("order_no is required")
        if not tn:
            raise ValueError("tracking_number is required")
        if qty < 1:
            raise ValueError("qty must be >= 1")
        entries.append(
            BfmrTrackingEntry(
                reserve_id=rid,
                purchase_id=pid,
                order_no=order_no,
                tracking_number=tn,
                qty=qty,
                shipment_id=(entry.shipment_id or "").strip() or None,
            )
        )

    base = resolve_base_url(base_url)
    key = api_key.strip()
    secret = api_secret
    succeeded: list[str] = []
    failed: list[str] = []
    fail_details: list[str] = []
    submission_ids: dict[str, tuple[str | None, str | None]] = {}
    last_raw: dict[str, Any] | None = None

    for offset in range(0, len(entries), _MAX_TRACKER_DATA_PER_REQUEST):
        chunk = entries[offset : offset + _MAX_TRACKER_DATA_PER_REQUEST]
        payload = [_entry_to_tracker_data(e) for e in chunk]
        raw = _post_tracker_data(
            base_url=base,
            api_key=key,
            api_secret=secret,
            tracker_data=payload,
            timeout_seconds=timeout_seconds,
        )
        last_raw = raw

        reservations = raw.get("reservations_response")
        if not isinstance(reservations, dict):
            reservations = {}
        invalid = reservations.get("invalid_items")
        valid = reservations.get("valid_items")
        invalid_list = invalid if isinstance(invalid, list) else []
        valid_list = valid if isinstance(valid, list) else []

        invalid_keys: set[str] = set()
        for item in invalid_list:
            label = _tracking_key_from_invalid_item(item)
            if label:
                invalid_keys.add(label.casefold())
                detail = ""
                if isinstance(item, dict):
                    detail = _first_str(
                        item.get("message"),
                        item.get("error"),
                        item.get("detail"),
                        item.get("reason"),
                    ) or ""
                fail_details.append(f"{label}: {detail}" if detail else label)

        # Honor explicit invalid_items; everything else in the chunk succeeds unless
        # the API also returned a non-empty valid_items list that omits the entry.
        valid_keys: set[str] = set()
        for item in valid_list:
            label = _tracking_key_from_invalid_item(item)
            if label:
                valid_keys.add(label.casefold())
        for entry in chunk:
            tn = entry.tracking_number
            tn_cf = tn.casefold()
            pid_cf = entry.purchase_id.casefold()
            order_cf = entry.order_no.casefold()
            is_invalid = tn_cf in invalid_keys or pid_cf in invalid_keys or order_cf in invalid_keys
            if is_invalid:
                if tn not in failed:
                    failed.append(tn)
                continue
            if valid_keys and not (
                tn_cf in valid_keys or pid_cf in valid_keys or order_cf in valid_keys
            ):
                if tn not in failed:
                    failed.append(tn)
                continue
            if tn not in succeeded:
                succeeded.append(tn)
            display = (entry.purchase_id or "").strip() or None
            submission_ids[tn] = (display, display)

    # De-dupe failed that also succeeded (prefer success).
    succeeded_set = {t.casefold() for t in succeeded}
    failed = [t for t in failed if t.casefold() not in succeeded_set]

    parts: list[str] = []
    msg = _message_from_payload(last_raw or {}) if last_raw else ""
    if msg:
        parts.append(msg)
    if succeeded:
        parts.append(f"Submitted {len(succeeded)} tracking number(s).")
    if failed:
        detail = "; ".join(fail_details) if fail_details else ", ".join(failed)
        parts.append(f"Failed ({len(failed)}): {detail}")
    if not parts:
        parts.append("BFMR tracking submit completed.")

    ok = bool(succeeded) and not failed
    return BfmrSubmitResult(
        success=ok,
        message=" ".join(parts),
        affected=len(succeeded) if succeeded else None,
        raw=last_raw,
        succeeded=succeeded,
        failed=failed,
        submission_ids=submission_ids,
    )
