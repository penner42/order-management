"""Parsefile order-management API (tracking submission + received-items search).

Also supports encrypted portal ``it@orders-all`` lookup (CryptoJS + SESSID) for
submission ids after tracking submit.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.utils.buying_group_apis import parsefile_crypto

logger = logging.getLogger(__name__)


ADD_TRACKING_CMD = "/cmd/addtracking"
SEARCH_CMD = "/cmd/search"
DEFAULT_API_URL = "/p/it@api@order-management"
RECEIVED_ITEMS_API_URL = "/p/it@api@received-items"
ORDERS_ALL_PAGE = "it@orders-all"
ORDERS_ALL_TABLE = "it@orders-all"
SESSION_COOKIE_NAME = "SESSID"
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Process-local portal sessions keyed by (base_url, email, password hash).
_session_cache: dict[tuple[str, str, str], str] = {}
_session_lock = threading.Lock()


@dataclass
class ParsefileTrackingEntry:
    tracking: str
    order: str | None = None
    amount: Decimal | float | None = None
    notes: str | None = None
    passcode: str | None = None


@dataclass
class ParsefileSubmissionIds:
    display_id: str | None
    api_id: str | None


@dataclass
class ParsefileSubmitResult:
    success: bool
    affected: int | None
    message: str
    raw: dict[str, Any]
    # tracking number -> display + API ids (from it@orders-all when available)
    submission_ids: dict[str, ParsefileSubmissionIds] = field(default_factory=dict)


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


# --- Encrypted portal session + it@orders-all (submission id lookup) ---


def _portal_cache_key(base_url: str, email: str, password: str) -> tuple[str, str, str]:
    pw_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return ((base_url or "").strip().rstrip("/").casefold(), email.strip().casefold(), pw_hash)


def _resolve_portal_base(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("base_url is required")
    return base


def _build_portal_path(page: str, cmd: dict[str, Any]) -> str:
    segments = [f"/p/{page}"]
    for key in sorted(cmd.keys()):
        segments.append(str(key))
        segments.append(str(cmd[key]))
    segments.extend(["_dc", str(int(time.time() * 1000))])
    return "/".join(segments)


def _cookie_header(session_id: str) -> str:
    return f"{SESSION_COOKIE_NAME}={session_id}"


def _extract_sessid(headers: Any) -> str | None:
    """Pull SESSID from Set-Cookie headers on an http.client response."""
    try:
        raw_headers = headers.get_all("Set-Cookie") if hasattr(headers, "get_all") else None
    except Exception:
        raw_headers = None
    if not raw_headers:
        cookie = headers.get("Set-Cookie") if headers is not None else None
        raw_headers = [cookie] if cookie else []
    for header in raw_headers:
        if not header:
            continue
        for part in str(header).split(";"):
            part = part.strip()
            if part.startswith(f"{SESSION_COOKIE_NAME}="):
                val = part.split("=", 1)[1].strip()
                if val:
                    return val
    return None


def _bootstrap_session(base_url: str, timeout_seconds: float) -> str:
    base = _resolve_portal_base(base_url)
    url = f"{base}/f/login"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": _BROWSER_UA, "Accept": "text/html,application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            resp.read()
            sess = _extract_sessid(resp.headers)
    except urllib.error.HTTPError as e:
        sess = _extract_sessid(e.headers)
        if not sess:
            raise RuntimeError(f"Parsefile session bootstrap failed (HTTP {e.code})") from e
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e)).strip() or e.__class__.__name__
        raise RuntimeError(f"Parsefile session bootstrap failed: {reason}") from e
    if not sess:
        raise RuntimeError("Parsefile session bootstrap: SESSID cookie not returned")
    return sess


def _decode_portal_response(
    session_ids: list[str],
    body: bytes,
) -> dict[str, Any]:
    """Decrypt portal envelope, trying each session id (old then new) as passphrase."""
    try:
        wire = json.loads(body.decode("utf-8", errors="replace") if body else "{}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Parsefile portal returned non-JSON: {body[:300]!r}") from e
    if not isinstance(wire, dict):
        raise RuntimeError("Parsefile portal returned unexpected payload")
    if not wire.get("success"):
        toast = wire.get("$mdToast")
        detail = ""
        if isinstance(toast, dict) and isinstance(toast.get("text"), str):
            detail = toast["text"].strip()
        raise RuntimeError(detail or "Parsefile portal request returned success=false")
    encrypted = wire.get("_encrypted")
    if isinstance(encrypted, str) and encrypted.strip():
        last_err: Exception | None = None
        seen: set[str] = set()
        for sid in session_ids:
            if not sid or sid in seen:
                continue
            seen.add(sid)
            try:
                plain = parsefile_crypto.decrypt(sid, encrypted)
            except Exception as e:
                last_err = e
                continue
            try:
                parsed = json.loads(plain) if plain else {}
            except json.JSONDecodeError as e:
                last_err = e
                continue
            if not isinstance(parsed, dict):
                last_err = RuntimeError("Parsefile portal decrypted unexpected payload")
                continue
            parsed["success"] = True
            return parsed
        raise RuntimeError(f"Parsefile portal decrypt failed: {last_err}")
    return wire


def _portal_post(
    *,
    base_url: str,
    session_id: str,
    page: str,
    cmd: dict[str, Any],
    data: dict[str, Any],
    timeout_seconds: float,
) -> tuple[str, dict[str, Any]]:
    """POST encrypted portal command. Returns (possibly rotated session_id, payload)."""
    base = _resolve_portal_base(base_url)
    plain = json.dumps(data, separators=(",", ":"))
    encrypted = parsefile_crypto.encrypt(session_id, plain)
    path = _build_portal_path(page, cmd)
    url = f"{base}{path}"
    body = json.dumps({"_encrypted": encrypted}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _BROWSER_UA,
            "Cookie": _cookie_header(session_id),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read()
            new_sess = _extract_sessid(resp.headers) or session_id
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise _PortalSessionExpired(f"Parsefile portal auth failed (HTTP {e.code})") from e
        err_body = e.read() if e.fp else b""
        raise RuntimeError(
            f"Parsefile portal error {e.code} at {url}: {err_body[:300]!r}"
        ) from e
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e)).strip() or e.__class__.__name__
        raise RuntimeError(f"Parsefile portal request failed ({url}): {reason}") from e

    if status in (401, 403):
        raise _PortalSessionExpired(f"Parsefile portal auth failed (HTTP {status})")
    try:
        # Response may be encrypted with either the request or rotated SESSID.
        payload = _decode_portal_response([session_id, new_sess], raw)
    except RuntimeError as e:
        if "decrypt" in str(e).casefold():
            raise _PortalSessionExpired(str(e)) from e
        raise
    return new_sess, payload


class _PortalSessionExpired(RuntimeError):
    pass


def _login_accepted(payload: dict[str, Any]) -> bool:
    location = payload.get("location")
    if isinstance(location, str) and location.strip():
        return True
    scope = payload.get("scope")
    if isinstance(scope, dict) and scope.get("logged_in"):
        return True
    if isinstance(scope, str) and scope.strip():
        try:
            parsed = json.loads(scope)
            if isinstance(parsed, dict) and parsed.get("logged_in"):
                return True
        except json.JSONDecodeError:
            pass
    return False


def portal_login(
    *,
    base_url: str,
    email: str,
    password: str,
    timeout_seconds: float = 30.0,
) -> str:
    """Bootstrap SESSID and log into the Parsefile portal. Returns session id."""
    if not (email or "").strip():
        raise ValueError("email is required")
    if not (password or "").strip():
        raise ValueError("password is required")
    session_id = _bootstrap_session(base_url, timeout_seconds)
    body = {
        "email": email.strip().lower(),
        # Portal login form strips spaces from the password before submit.
        "password": password.replace(" ", ""),
    }
    cmd = {
        "cmd": "login",
        "checkLoading": True,
        "screen": 1,
        "encrypt": True,
        "page": "login",
        "_arg": 0,
        "section": "back@login",
    }
    session_id, payload = _portal_post(
        base_url=base_url,
        session_id=session_id,
        page="login",
        cmd=cmd,
        data=body,
        timeout_seconds=timeout_seconds,
    )
    toast = payload.get("$mdToast")
    if isinstance(toast, dict):
        text = toast.get("text")
        if isinstance(text, str) and text.strip():
            raise RuntimeError(f"Parsefile portal login failed: {text.strip()}")
    if not _login_accepted(payload):
        raise RuntimeError("Parsefile portal login failed: credentials rejected")
    return session_id


def _ensure_portal_session(
    *,
    base_url: str,
    email: str,
    password: str,
    timeout_seconds: float,
) -> str:
    key = _portal_cache_key(base_url, email, password)
    with _session_lock:
        cached = _session_cache.get(key)
        if cached:
            return cached
    session_id = portal_login(
        base_url=base_url,
        email=email,
        password=password,
        timeout_seconds=timeout_seconds,
    )
    with _session_lock:
        cached = _session_cache.get(key)
        if cached:
            return cached
        _session_cache[key] = session_id
        return session_id


def _rellogin_portal_session(
    *,
    base_url: str,
    email: str,
    password: str,
    stale_session: str,
    timeout_seconds: float,
) -> str:
    key = _portal_cache_key(base_url, email, password)
    with _session_lock:
        cached = _session_cache.get(key)
        if cached and cached != stale_session:
            return cached
        _session_cache.pop(key, None)
    session_id = portal_login(
        base_url=base_url,
        email=email,
        password=password,
        timeout_seconds=timeout_seconds,
    )
    with _session_lock:
        cached = _session_cache.get(key)
        if cached and cached != stale_session:
            return cached
        _session_cache[key] = session_id
        return session_id


def _with_portal_session(
    *,
    base_url: str,
    email: str,
    password: str,
    session_id: str,
    timeout_seconds: float,
    call,
):
    try:
        return call(session_id), session_id
    except _PortalSessionExpired:
        session_id = _rellogin_portal_session(
            base_url=base_url,
            email=email,
            password=password,
            stale_session=session_id,
            timeout_seconds=timeout_seconds,
        )
        return call(session_id), session_id


def _iter_string_values(value: Any, *, depth: int = 0) -> list[str]:
    if depth > 3 or value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text or text.casefold() in ("---", "null", "none"):
            return []
        parts: list[str] = []
        for part in text.replace(";", ",").split(","):
            part = part.strip()
            if part:
                parts.append(part)
        return parts
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [str(value)]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_iter_string_values(item, depth=depth + 1))
        return out
    if isinstance(value, dict):
        out = []
        for item in value.values():
            out.extend(_iter_string_values(item, depth=depth + 1))
        return out
    return []


def _tracking_values_from_row(row: dict[str, Any]) -> list[str]:
    """Collect likely tracking values, then fall back to every string cell."""
    values: list[str] = []
    preferred_keys = (
        "tracking",
        "tracking_number",
        "tracking_numbers",
        "trackingNumber",
        "trackingNumbers",
        "tn",
        "f_tracking",
        "f_tracking_number",
        "f_tracking_numbers",
        "f_tn",
        "f_track",
    )
    for key in preferred_keys:
        if key in row:
            values.extend(_iter_string_values(row.get(key)))
    # Also any key that looks tracking-related.
    for key, val in row.items():
        key_l = str(key).casefold()
        if "track" in key_l or key_l in ("tn", "f_tn"):
            values.extend(_iter_string_values(val))
    if values:
        return values
    # Last resort: every string-ish cell (orders-all schemas vary by portal).
    for val in row.values():
        values.extend(_iter_string_values(val))
    return values


def _row_matches_tracking(row: dict[str, Any], tn_key: str) -> bool:
    tn_compact = tn_key.replace(" ", "")
    for candidate in _tracking_values_from_row(row):
        c = candidate.casefold()
        if c == tn_key or c.replace(" ", "") == tn_compact:
            return True
        # Cell may contain "TN notes…" etc.
        if tn_key in c or tn_compact in c.replace(" ", ""):
            return True
    return False


def _first_str_from_keys(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if key not in row:
            continue
        vals = _iter_string_values(row.get(key))
        if vals:
            return vals[0]
    return None


def _ids_from_order_row(row: dict[str, Any]) -> ParsefileSubmissionIds | None:
    """Best-effort map of Parsefile orders-all row → display + API ids.

    Parsefile portals typically use ``f_*`` column names (e.g. ``f_id``, ``f_order``).
    """
    api_id = _first_str_from_keys(
        row,
        (
            "f_id",
            "id",
            "f_order_id",
            "f_key",
            "key",
            "uuid",
            "order_uuid",
            "orderId",
        ),
    )
    display_id = _first_str_from_keys(
        row,
        (
            "f_order",
            "f_order_number",
            "f_ordernum",
            "f_po",
            "f_purchase_order",
            "f_name",
            "order",
            "order_id",
            "order_number",
            "orderNumber",
            "number",
            "display_id",
            "name",
        ),
    )
    if display_id and api_id and display_id == api_id:
        # Look for a distinct display-ish key.
        for key, val in row.items():
            key_l = str(key).casefold()
            if key_l in ("f_id", "id", "key", "uuid"):
                continue
            if "order" in key_l or key_l.endswith("_name") or "po" in key_l:
                vals = _iter_string_values(val)
                if vals and vals[0] != api_id:
                    display_id = vals[0]
                    break
    if not display_id and not api_id:
        # Any key ending in id / containing order as last resort.
        for key, val in row.items():
            key_l = str(key).casefold()
            if key_l.endswith("id") or "order" in key_l:
                vals = _iter_string_values(val)
                if vals:
                    api_id = vals[0]
                    break
    if not display_id and not api_id:
        return None
    if not display_id:
        display_id = api_id
    return ParsefileSubmissionIds(display_id=display_id, api_id=api_id)


def _rows_from_loadtable(payload: dict[str, Any], *, table_name: str) -> list[dict[str, Any]]:
    tables = payload.get("loadtable")
    if not isinstance(tables, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in tables:
        if not isinstance(entry, dict):
            continue
        if table_name and entry.get("table") not in (None, table_name, ORDERS_ALL_TABLE):
            # Accept either exact table or first table when name differs slightly.
            if entry.get("table") and str(entry.get("table")).strip() not in (
                table_name,
                ORDERS_ALL_PAGE,
            ):
                continue
        data = entry.get("data")
        rows = None
        if isinstance(data, dict):
            rows = data.get("data")
        elif isinstance(data, list):
            rows = data
        if isinstance(rows, list):
            out.extend(r for r in rows if isinstance(r, dict))
    if out:
        return out
    # Fallback: any loadtable data if table name filter matched nothing.
    for entry in tables:
        if not isinstance(entry, dict):
            continue
        data = entry.get("data")
        rows = data.get("data") if isinstance(data, dict) else data
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    return []


_ORDERS_TABLE_CANDIDATES = (
    ORDERS_ALL_TABLE,
    "it@orders",
    "orders-all",
    "orders",
)


def find_order_by_tracking(
    *,
    base_url: str,
    email: str,
    password: str,
    tracking_number: str,
    timeout_seconds: float = 30.0,
) -> ParsefileSubmissionIds | None:
    """Look up an order on encrypted ``it@orders-all`` by tracking number."""
    tn = (tracking_number or "").strip()
    if not tn:
        return None
    tn_key = tn.casefold()

    session_id = _ensure_portal_session(
        base_url=base_url,
        email=email,
        password=password,
        timeout_seconds=timeout_seconds,
    )

    def _load(sess: str) -> tuple[list[dict[str, Any]], str]:
        filter_variants = (
            {"f_tracking": tn},
            {"f_tracking_number": tn},
            {"tracking": tn},
            {"tracking_number": tn},
            {"q": tn},
            {"search": tn},
        )
        queries: list[dict[str, Any]] = [
            {"page": 1, "limit": 50, "filters": filt} for filt in filter_variants
        ]
        queries.append({"page": 1, "limit": 150, "filters": {}})

        current = sess
        last_rows: list[dict[str, Any]] = []
        for table_name in _ORDERS_TABLE_CANDIDATES:
            cmd = {
                "cmd": "load",
                "instance": "table",
                "_table": table_name,
                "checkLoading": 0,
                "init": True,
            }
            for query in queries:
                try:
                    current, payload = _portal_post(
                        base_url=base_url,
                        session_id=current,
                        page=ORDERS_ALL_PAGE,
                        cmd=cmd,
                        data=query,
                        timeout_seconds=timeout_seconds,
                    )
                except RuntimeError as exc:
                    # Wrong _table often returns success=false; try next candidate.
                    logger.info(
                        "Parsefile orders-all load skipped table=%s filters=%s: %s",
                        table_name,
                        query.get("filters"),
                        exc,
                    )
                    continue
                rows = _rows_from_loadtable(payload, table_name=table_name)
                if not rows:
                    continue
                last_rows = rows
                matched = [row for row in rows if _row_matches_tracking(row, tn_key)]
                if matched:
                    return matched, current
                # Log schema once to help diagnose field names in prod logs.
                sample = rows[0]
                logger.info(
                    "Parsefile orders-all rows=%s table=%s keys=%s (no TN match yet for %s)",
                    len(rows),
                    table_name,
                    sorted(str(k) for k in sample.keys())[:40],
                    tn,
                )
        return last_rows, current

    (rows, rotated_session), _outer_session = _with_portal_session(
        base_url=base_url,
        email=email,
        password=password,
        session_id=session_id,
        timeout_seconds=timeout_seconds,
        call=_load,
    )
    final_session = rotated_session or _outer_session or session_id
    with _session_lock:
        key = _portal_cache_key(base_url, email, password)
        if final_session:
            _session_cache[key] = final_session

    for row in rows:
        if _row_matches_tracking(row, tn_key):
            ids = _ids_from_order_row(row)
            if ids is None:
                logger.warning(
                    "Parsefile orders-all matched TN %s but no id fields in keys=%s",
                    tn,
                    sorted(str(k) for k in row.keys())[:40],
                )
            return ids
    if len(rows) == 1:
        return _ids_from_order_row(rows[0])
    return None


@dataclass
class ParsefileLookupResult:
    submission_ids: dict[str, ParsefileSubmissionIds] = field(default_factory=dict)
    error: str | None = None


def lookup_submission_ids(
    *,
    base_url: str,
    email: str,
    password: str,
    tracking_numbers: list[str],
    timeout_seconds: float = 30.0,
) -> ParsefileLookupResult:
    """Look up submission ids for each tracking after addtracking (best-effort)."""
    out: dict[str, ParsefileSubmissionIds] = {}
    errors: list[str] = []
    for tracking in tracking_numbers:
        tn = (tracking or "").strip()
        if not tn:
            continue
        try:
            ids = None
            for attempt in range(5):
                ids = find_order_by_tracking(
                    base_url=base_url,
                    email=email,
                    password=password,
                    tracking_number=tn,
                    timeout_seconds=timeout_seconds,
                )
                if ids is not None:
                    break
                if attempt < 4:
                    time.sleep(1.0)
            if ids is not None:
                out[tn] = ids
            else:
                errors.append(f"{tn}: not found in it@orders-all")
        except Exception as exc:
            logger.warning("Parsefile orders-all lookup failed for %s: %s", tn, exc)
            errors.append(f"{tn}: {exc}")
    err = None
    if errors and not out:
        err = "; ".join(errors[:3])
    elif errors:
        err = "; ".join(errors[:3])
    return ParsefileLookupResult(submission_ids=out, error=err)

