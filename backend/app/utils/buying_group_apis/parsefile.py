"""Parsefile order-management API (tracking submission + received-items search)."""
from __future__ import annotations

import csv
import io
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any


ADD_TRACKING_CMD = "/cmd/addtracking"
SEARCH_CMD = "/cmd/search"
DEFAULT_API_URL = "/p/it@api@order-management"
RECEIVED_ITEMS_API_URL = "/p/it@api@received-items"


@dataclass
class ParsefileTrackingEntry:
    tracking: str
    order: str | None = None
    amount: Decimal | float | None = None
    notes: str | None = None
    passcode: str | None = None


@dataclass
class ParsefileSubmitResult:
    success: bool
    affected: int | None
    message: str
    raw: dict[str, Any]


def resolve_api_url(api_url: str | None) -> str:
    """Use the buying-group API path, or the Parsefile framework default when blank."""
    api = (api_url or "").strip()
    if not api:
        api = DEFAULT_API_URL
    if not api.startswith("/"):
        api = "/" + api
    return api.rstrip("/")


def build_url(base_url: str, api_url: str | None, *, command: str = ADD_TRACKING_CMD) -> str:
    """Join base_url + api_url + command path (default: addtracking)."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("base_url is required")
    api = resolve_api_url(api_url)
    cmd = command if command.startswith("/") else f"/{command}"
    return f"{base}{api}{cmd}"


def build_received_items_search_url(base_url: str) -> str:
    """Join base_url + received-items API path + search command."""
    return build_url(base_url, RECEIVED_ITEMS_API_URL, command=SEARCH_CMD)


def _message_from_payload(raw: dict[str, Any], *, success: bool) -> str:
    for key in ("response", "message", "detail", "error"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    toast = raw.get("$mdToast")
    if isinstance(toast, dict):
        text = toast.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    if success:
        return "Tracking numbers submitted successfully."
    try:
        return f"Submission failed: {json.dumps(raw)[:400]}"
    except Exception:
        return "Submission failed"


def submit_trackings(
    *,
    base_url: str,
    api_url: str | None,
    bearer_token: str,
    user_id: int,
    email: str,
    trackings: list[ParsefileTrackingEntry],
    timeout_seconds: float = 30.0,
) -> ParsefileSubmitResult:
    if not trackings:
        raise ValueError("At least one tracking entry is required")
    if not (bearer_token or "").strip():
        raise ValueError("bearer_token is required")
    if not (email or "").strip():
        raise ValueError("email is required")

    payload_trackings: list[dict[str, Any]] = []
    for entry in trackings:
        tracking = (entry.tracking or "").strip()
        if not tracking:
            raise ValueError("Each tracking entry must include a tracking number")
        obj: dict[str, Any] = {"tracking": tracking}
        if entry.order and str(entry.order).strip():
            obj["order"] = str(entry.order).strip()
        if entry.passcode and str(entry.passcode).strip():
            obj["passcode"] = str(entry.passcode).strip()
        if entry.notes and str(entry.notes).strip():
            obj["notes"] = str(entry.notes).strip()
        if entry.amount is not None:
            obj["amount"] = float(entry.amount)
        payload_trackings.append(obj)

    body = {
        "user": int(user_id),
        "email": email.strip(),
        "trackings": payload_trackings,
    }
    url = build_url(base_url, api_url)
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {bearer_token.strip()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw_text = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        parsed_msg = ""
        if err_body.strip():
            try:
                err_json = json.loads(err_body)
                if isinstance(err_json, dict):
                    parsed_msg = _message_from_payload(err_json, success=False)
            except json.JSONDecodeError:
                parsed_msg = err_body.strip()[:400]
        detail = parsed_msg or (str(e.reason).strip() if e.reason else "") or "empty response"
        raise RuntimeError(f"Parsefile API error {e.code} at {url}: {detail}") from e
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e)).strip() or e.__class__.__name__
        raise RuntimeError(f"Parsefile API request failed ({url}): {reason}") from e

    try:
        raw = json.loads(raw_text) if raw_text else {}
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Parsefile API returned non-JSON (HTTP {status}) from {url}: {raw_text[:300]}") from e

    if not isinstance(raw, dict):
        raise RuntimeError(f"Parsefile API returned unexpected payload from {url}: {raw_text[:300]}")

    success = bool(raw.get("success"))
    message = _message_from_payload(raw, success=success)
    affected_raw = raw.get("affected")
    affected = int(affected_raw) if isinstance(affected_raw, (int, float)) else None
    if not success:
        raise RuntimeError(message)
    return ParsefileSubmitResult(success=success, affected=affected, message=message, raw=raw)


@dataclass
class ParsefileReceivedItem:
    verified: bool
    void: bool
    receipt_id: str | None
    created_date: str | None
    user: str | None
    item: str | None
    warehouse: str | None
    qty: int | None
    price: Decimal | None
    tax: Decimal | None
    commission: Decimal | None
    ext_total: Decimal | None
    status: str | None
    delivery: str | None
    tracking_numbers: list[str]
    raw: dict[str, str]


def _parse_boolish(value: str | None) -> bool:
    text = (value or "").strip().casefold()
    return text in {"1", "true", "yes", "y", "verified", "void"}


def _parse_decimal(value: str | None) -> Decimal | None:
    text = (value or "").strip()
    if not text:
        return None
    cleaned = text.replace("$", "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _parse_int(value: str | None) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return int(Decimal(text.replace(",", "").strip()))
    except (InvalidOperation, ValueError):
        return None


def _unwrap_excel_text(value: str) -> str:
    """Strip Excel CSV text-forcing wrappers like =\"TRACKING\" / =\"\"TRACKING\"\"."""
    text = (value or "").strip()
    if not text:
        return ""
    # Formula-style export: ="value"
    if text.startswith("="):
        text = text[1:].strip()
    # Peel balanced surrounding quotes (CSV may leave them after =).
    while len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1].strip()
    return text.strip()


def _split_trackings(value: str | None) -> list[str]:
    text = _unwrap_excel_text(value or "")
    if not text:
        return []
    parts: list[str] = []
    seen: set[str] = set()
    # After unwrapping a single Excel cell there is usually one tracking; still
    # allow comma/semicolon lists for multi-tracking cells.
    for chunk in text.replace(";", ",").split(","):
        tn = _unwrap_excel_text(chunk)
        if not tn:
            continue
        key = tn.casefold()
        if key in seen:
            continue
        seen.add(key)
        parts.append(tn)
    return parts


def _normalize_csv_header(name: str) -> str:
    return " ".join((name or "").strip().casefold().replace("_", " ").split())


_RECEIVED_HEADER_ALIASES: dict[str, str] = {
    "verified": "verified",
    "void": "void",
    "id": "id",
    "receipt id": "receipt_id",
    "receiptid": "receipt_id",
    "created date": "created_date",
    "createddate": "created_date",
    "user": "user",
    "item": "item",
    "warehouse": "warehouse",
    "qty": "qty",
    "quantity": "qty",
    "price": "price",
    "tax": "tax",
    "commission": "commission",
    "ext total": "ext_total",
    "exttotal": "ext_total",
    "status": "status",
    "delivery": "delivery",
    "tracking numbers": "tracking_numbers",
    "tracking number": "tracking_numbers",
    "tracking": "tracking_numbers",
}


def parse_received_items_csv(raw_text: str) -> list[ParsefileReceivedItem]:
    """Parse the receiving-analytics CSV export from received-items search."""
    text = (raw_text or "").lstrip("\ufeff").strip()
    if not text:
        return []

    # Error responses are JSON; success is CSV.
    if text[:1] in "{[":
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Parsefile received-items returned invalid JSON: {text[:300]}") from e
        if isinstance(raw, dict):
            raise RuntimeError(_message_from_payload(raw, success=False) or "Parsefile received-items search failed")
        raise RuntimeError(f"Parsefile received-items returned unexpected JSON: {text[:300]}")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []

    field_map: dict[str, str] = {}
    for header in reader.fieldnames:
        key = _RECEIVED_HEADER_ALIASES.get(_normalize_csv_header(header or ""))
        if key:
            field_map[header] = key

    rows: list[ParsefileReceivedItem] = []
    for raw_row in reader:
        mapped: dict[str, str] = {}
        for header, value in raw_row.items():
            key = field_map.get(header)
            if not key:
                continue
            mapped[key] = value if value is not None else ""

        trackings = _split_trackings(mapped.get("tracking_numbers"))
        rows.append(
            ParsefileReceivedItem(
                verified=_parse_boolish(mapped.get("verified")),
                void=_parse_boolish(mapped.get("void")),
                receipt_id=(mapped.get("receipt_id") or "").strip() or None,
                created_date=(mapped.get("created_date") or "").strip() or None,
                user=(mapped.get("user") or "").strip() or None,
                item=(mapped.get("item") or "").strip() or None,
                warehouse=(mapped.get("warehouse") or "").strip() or None,
                qty=_parse_int(mapped.get("qty")),
                price=_parse_decimal(mapped.get("price")),
                tax=_parse_decimal(mapped.get("tax")),
                commission=_parse_decimal(mapped.get("commission")),
                ext_total=_parse_decimal(mapped.get("ext_total")),
                status=(mapped.get("status") or "").strip() or None,
                delivery=(mapped.get("delivery") or "").strip() or None,
                tracking_numbers=trackings,
                raw={k: (v if v is not None else "") for k, v in raw_row.items()},
            )
        )
    return rows


@dataclass
class ParsefileReceivedItemsSearchResult:
    rows: list[ParsefileReceivedItem]
    raw_csv: str
    url: str


def search_received_items(
    *,
    base_url: str,
    bearer_token: str,
    user_id: int,
    email: str,
    trackings: list[str] | None = None,
    receipts: list[int] | None = None,
    min_modified_date: str | None = None,
    max_modified_date: str | None = None,
    min_created_date: str | None = None,
    max_created_date: str | None = None,
    timeout_seconds: float = 60.0,
    include_empty_trackings_receipts: bool = False,
) -> ParsefileReceivedItemsSearchResult:
    """POST received-items search; returns parsed rows plus the raw CSV body.

    When ``include_empty_trackings_receipts`` is True, always send ``trackings`` /
    ``receipts`` keys (possibly empty arrays). Parsefile treats empty arrays plus
    min dates as a full received-items export.
    """
    if not (bearer_token or "").strip():
        raise ValueError("bearer_token is required")
    if not (email or "").strip():
        raise ValueError("email is required")

    cleaned_trackings: list[str] = []
    seen: set[str] = set()
    for tn in trackings or []:
        value = (tn or "").strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned_trackings.append(value)

    body: dict[str, Any] = {
        "user": int(user_id),
        "email": email.strip(),
    }
    if cleaned_trackings or include_empty_trackings_receipts:
        body["trackings"] = cleaned_trackings
    if receipts or include_empty_trackings_receipts:
        body["receipts"] = [int(r) for r in (receipts or [])]
    if min_modified_date:
        body["minModifiedDate"] = min_modified_date
    if max_modified_date:
        body["maxModifiedDate"] = max_modified_date
    if min_created_date:
        body["minCreatedDate"] = min_created_date
    if max_created_date:
        body["maxCreatedDate"] = max_created_date

    if "trackings" not in body and "receipts" not in body and not any(
        k in body for k in ("minModifiedDate", "maxModifiedDate", "minCreatedDate", "maxCreatedDate")
    ):
        raise ValueError("At least one search filter (trackings, receipts, or date range) is required")

    url = build_received_items_search_url(base_url)
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {bearer_token.strip()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw_text = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        parsed_msg = ""
        if err_body.strip():
            try:
                err_json = json.loads(err_body)
                if isinstance(err_json, dict):
                    parsed_msg = _message_from_payload(err_json, success=False)
            except json.JSONDecodeError:
                parsed_msg = err_body.strip()[:400]
        detail = parsed_msg or (str(e.reason).strip() if e.reason else "") or "empty response"
        raise RuntimeError(f"Parsefile API error {e.code} at {url}: {detail}") from e
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e)).strip() or e.__class__.__name__
        raise RuntimeError(f"Parsefile API request failed ({url}): {reason}") from e

    if status >= 400:
        raise RuntimeError(f"Parsefile received-items search failed (HTTP {status}) from {url}")

    return ParsefileReceivedItemsSearchResult(
        rows=parse_received_items_csv(raw_text),
        raw_csv=raw_text,
        url=url,
    )


# Default lower bound for full received-items export (Parsefile ``YYYY-MM-DD HH:MM:SS``).
_ALL_RECEIVED_MIN_DATE = "2020-02-01 00:00:00"


def _parsefile_datetime(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def list_all_received_items(
    *,
    base_url: str,
    bearer_token: str,
    user_id: int,
    email: str,
    min_created_date: str | None = None,
    max_created_date: str | None = None,
    min_modified_date: str | None = None,
    max_modified_date: str | None = None,
    timeout_seconds: float = 120.0,
) -> ParsefileReceivedItemsSearchResult:
    """POST received-items search with empty trackings/receipts to return all rows.

    Empty ``trackings``/``receipts`` plus a date window. Max dates default to end of
    tomorrow (local) when not provided so callers can pass them later.
    """
    min_dt = (min_created_date or min_modified_date or _ALL_RECEIVED_MIN_DATE).strip()
    # Inclusive upper bound slightly ahead of "now" so same-day receipts are included.
    default_max = _parsefile_datetime(
        (datetime.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    )
    max_created = (max_created_date or default_max).strip()
    max_modified = (max_modified_date or max_created).strip()
    min_created = (min_created_date or min_dt).strip()
    min_modified = (min_modified_date or min_dt).strip()

    return search_received_items(
        base_url=base_url,
        bearer_token=bearer_token,
        user_id=user_id,
        email=email,
        trackings=[],
        receipts=[],
        min_created_date=min_created,
        max_created_date=max_created,
        min_modified_date=min_modified,
        max_modified_date=max_modified,
        timeout_seconds=timeout_seconds,
        include_empty_trackings_receipts=True,
    )
