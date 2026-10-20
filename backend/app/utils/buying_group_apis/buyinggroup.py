"""BuyingGroup.com tracking submission client.

Flow:
1. Use a cached access token when available; otherwise login via ``POST /v2/token/get``
   (username/password → access/refresh). Re-auth only on auth failure (refresh, then login).
2. Add trackings via ``POST /v1/order/add_trackings`` (``auth_token`` + Bearer)
3. Enrich each tracking via ``/v1/order/get_orders`` + ``/v1/order/update_order``
   (order_num, amount, buyer_note)
"""
from __future__ import annotations

from app.submission_policy import require_group_submissions_enabled
from app.application_logging import redact
import hashlib
import http.client
import json
import logging
import ssl
import threading
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, TypeVar
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.prod.buyinggroup.com"
DEFAULT_ADD_TRACKINGS_PATH = "/v1/order/add_trackings"
TOKEN_GET_PATH = "/v2/token/get"
TOKEN_REFRESH_PATH = "/v2/token/refresh"
GET_ORDERS_PATH = "/v1/order/get_orders"
UPDATE_ORDER_PATH = "/v1/order/update_order"
GET_RECEIPTS_PATH = "/v1/receipt/get_receipts"
GET_ANALYTICS_PATH = "/v1/receipt/get_analytics"

# Browser FormData sends the literal string for empty filters.
_UNDEFINED = "undefined"
_MAX_LIST_PAGES = 100
_DEFAULT_PAGE_SIZE = 50

# Match dealsbot's working BuyingGroup client (WAF rejects non-browser UAs).
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_SITE_ORIGIN = "https://buyinggroup.com"

_T = TypeVar("_T")

# Process-local access/refresh tokens keyed by (base_url, username, password hash).
# Reused across submits; refreshed or replaced only on auth failure.
_token_cache: dict[tuple[str, str, str], "_AuthTokens"] = {}
_token_lock = threading.Lock()


@dataclass
class BuyinggroupTrackingEntry:
    tracking: str
    amount: Decimal | float | None = None
    order_num: str | None = None
    note: str | None = None


@dataclass
class BuyinggroupOrderRow:
    """Remote order row after submit.

    ``order_id`` is the UUID used by ``update_order``.
    ``display_order_id`` is the human id (e.g. ``O-269283098``) when present.
    """

    order_id: str
    tracking_id: str
    display_order_id: str | None = None


@dataclass
class BuyinggroupSubmissionIds:
    display_id: str | None
    api_id: str | None


@dataclass
class BuyinggroupSubmitResult:
    success: bool
    message: str
    affected: int | None
    raw: dict[str, Any] | list[Any] | None = None
    enriched_count: int = 0
    enrich_errors: list[str] = field(default_factory=list)
    # tracking number -> display + API ids
    submission_ids: dict[str, BuyinggroupSubmissionIds] = field(default_factory=dict)
    # Explicit per-tracking outcomes let batch callers leave unresolved items pending.
    succeeded: list[str] | None = None
    failed: list[str] = field(default_factory=list)


@dataclass
class BuyinggroupReceipt:
    receipt_id: str | None
    tracking_id: str | None
    alt_tracking_id: str | None
    quantity: int | None
    total: Decimal | None
    status: str | None
    void: bool
    created_dt: str | None
    order_id: str | None
    note: str | None
    tracking_note: str | None
    delivery: str | None
    raw: dict[str, Any] = field(default_factory=dict)
    reward: Decimal | None = None


@dataclass
class BuyinggroupAnalyticsRow:
    tracking_id: str | None
    order_id: str | None
    description: str | None
    quantity: int | None
    price: Decimal | None
    total: Decimal | None
    note: str | None
    tracking_note: str | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class _AuthTokens:
    access: str
    refresh: str | None = None


class _SessionExpired(RuntimeError):
    pass


def _cache_key(base_url: str, username: str, password: str) -> tuple[str, str, str]:
    pw_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return (resolve_base_url(base_url), username.strip().casefold(), pw_hash)


def _ensure_tokens(
    *,
    base_url: str,
    username: str,
    password: str,
    timeout_seconds: float,
) -> _AuthTokens:
    """Return cached tokens, logging in only when none are cached."""
    key = _cache_key(base_url, username, password)
    with _token_lock:
        cached = _token_cache.get(key)
        if cached and cached.access:
            return cached
    tokens = login(
        base_url=base_url,
        username=username,
        password=password,
        timeout_seconds=timeout_seconds,
    )
    with _token_lock:
        cached = _token_cache.get(key)
        if cached and cached.access:
            return cached
        _token_cache[key] = tokens
        return tokens


def _reauth_tokens(
    *,
    base_url: str,
    username: str,
    password: str,
    stale: _AuthTokens,
    timeout_seconds: float,
) -> _AuthTokens:
    """Refresh or re-login after auth failure; prefer a fresher concurrent cache entry."""
    key = _cache_key(base_url, username, password)
    with _token_lock:
        cached = _token_cache.get(key)
        if cached and cached.access and cached.access != stale.access:
            return cached
        _token_cache.pop(key, None)

    if stale.refresh:
        try:
            tokens = refresh_access_token(
                base_url=base_url,
                refresh_token=stale.refresh,
                timeout_seconds=timeout_seconds,
            )
            with _token_lock:
                cached = _token_cache.get(key)
                if cached and cached.access and cached.access != stale.access:
                    return cached
                _token_cache[key] = tokens
                return tokens
        except Exception:
            logger.debug(
                "BuyingGroup refresh failed; falling back to full login",
                exc_info=True,
            )

    tokens = login(
        base_url=base_url,
        username=username,
        password=password,
        timeout_seconds=timeout_seconds,
    )
    with _token_lock:
        cached = _token_cache.get(key)
        if cached and cached.access and cached.access != stale.access:
            return cached
        _token_cache[key] = tokens
        return tokens


def _encode_multipart(fields: list[tuple[str, str]]) -> tuple[bytes, str]:
    """Build multipart/form-data body; returns (body_bytes, content_type_header)."""
    boundary = f"----om{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for key, value in fields:
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n'
                f"\r\n"
                f"{value}\r\n"
            ).encode("utf-8")
        )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def resolve_base_url(base_url: str | None) -> str:
    """Return API host root (no trailing slash), e.g. https://api.prod.buyinggroup.com.

    Accepts either the host root or dealsbot-style ``.../v1`` base URL.
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        base = DEFAULT_BASE_URL
    if base.endswith("/v1"):
        base = base[:-3]
    return base.rstrip("/")


def resolve_add_trackings_url(base_url: str | None, api_url: str | None) -> str:
    """Resolve add_trackings URL.

    Default is ``{base}/v1/order/add_trackings`` on api.prod.buyinggroup.com.
    The public OpenAPI Cloud Run host 404s; do not use it.
    """
    override = (api_url or "").strip()
    if override.startswith("http://") or override.startswith("https://"):
        # Migrate callers still pointing at the dead Cloud Run OpenAPI server.
        if "prod-buyinggroup-ar3jdliyeq-wl.a.run.app" in override:
            return f"{resolve_base_url(base_url)}{DEFAULT_ADD_TRACKINGS_PATH}"
        return override
    base = resolve_base_url(base_url)
    path = override if override else DEFAULT_ADD_TRACKINGS_PATH
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _format_amount(amount: Decimal | float | None) -> str | None:
    if amount is None:
        return None
    dec = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    text = format(dec.quantize(Decimal("0.01")), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _parse_json_body(status: int, raw_text: str) -> Any:
    if not raw_text.strip():
        return None
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"BuyingGroup API returned non-JSON (HTTP {status}): {raw_text[:300]}"
        ) from e


def _error_detail(raw: Any, body_text: str, fallback: str = "empty response") -> str:
    if isinstance(raw, dict):
        for key in ("message", "error", "detail", "errors"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                msg = value.get("message")
                if isinstance(msg, str) and msg.strip():
                    return msg.strip()
        status = raw.get("status")
        if isinstance(status, str) and status.strip() and status.strip().upper() not in ("SUCCESS", "OK"):
            return status.strip()
    text = (body_text or "").strip()
    return text[:400] if text else fallback


def _is_auth_failure(status: int, raw: Any, body_text: str) -> bool:
    """True when BG rejected the access token (not WAF / bad credentials login)."""
    if status == 401:
        return True
    blob = ""
    if isinstance(raw, dict):
        for key in ("message", "error", "detail"):
            value = raw.get(key)
            if isinstance(value, str):
                blob += " " + value
        errors = raw.get("errors")
        if isinstance(errors, dict):
            msg = errors.get("message")
            if isinstance(msg, str):
                blob += " " + msg
        status_val = raw.get("status")
        if isinstance(status_val, str) and status_val.strip().upper() == "FAIL":
            # Auth FAIL messages from add_trackings / authed endpoints.
            pass
    blob += " " + (body_text or "")
    msg = blob.casefold()
    if "invalid_auth_token" in msg:
        return True
    if "authentication credentials were not provided or invalid" in msg:
        return True
    if "auth" in msg and "token" in msg and ("invalid" in msg or "expired" in msg):
        return True
    return False


def _default_headers(*, content_type: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": _BROWSER_USER_AGENT,
        "Origin": _SITE_ORIGIN,
        "Referer": f"{_SITE_ORIGIN}/",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _http_post(
    *,
    url: str,
    body: bytes,
    content_type: str,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 30.0,
) -> tuple[int, str, Any]:
    """POST via http.client so header names like ``auth_token`` stay exact.

    ``urllib.request.Request`` capitalizes headers (``auth_token`` → ``Auth_Token``),
    which BuyingGroup's API does not accept.
    """
    req_headers = _default_headers(content_type=content_type)
    if headers:
        req_headers.update(headers)
    authed = bool(
        (headers or {}).get("Authorization") or (headers or {}).get("auth_token")
    )

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise RuntimeError(f"BuyingGroup API invalid URL: {url}")
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    try:
        if parsed.scheme == "https":
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                parsed.hostname,
                parsed.port or 443,
                timeout=timeout_seconds,
                context=ssl.create_default_context(),
            )
        else:
            conn = http.client.HTTPConnection(
                parsed.hostname,
                parsed.port or 80,
                timeout=timeout_seconds,
            )
        try:
            conn.request("POST", path, body=body, headers=req_headers)
            resp = conn.getresponse()
            status = int(resp.status)
            raw_text = resp.read().decode("utf-8", errors="replace")
        finally:
            conn.close()
    except (OSError, http.client.HTTPException) as e:
        reason = str(e).strip() or e.__class__.__name__
        raise RuntimeError(f"BuyingGroup API request failed ({url}): {reason}") from e

    try:
        raw = _parse_json_body(status, raw_text)
    except RuntimeError:
        _log_failed_response(url, status, raw_text)
        if status >= 400:
            detail = raw_text.strip()[:400] or f"HTTP {status}"
            raise RuntimeError(f"BuyingGroup API error {status} at {url}: {detail}")
        raise

    if status >= 400 or (
        isinstance(raw, dict) and str(raw.get("status", "")).strip().upper() == "FAIL"
    ):
        _log_failed_response(url, status, raw)

    if status >= 400:
        if authed and _is_auth_failure(status, raw, raw_text):
            raise _SessionExpired(f"BuyingGroup session expired (HTTP {status})")
        detail = _error_detail(raw, raw_text)
        raise RuntimeError(f"BuyingGroup API error {status} at {url}: {detail}")
    return status, raw_text, raw


def _log_failed_response(url: str, status: int, response: Any) -> None:
    parsed = urlparse(url)
    # Auth responses can include session credentials; retain only their HTTP status.
    auth_endpoint = parsed.path in (TOKEN_GET_PATH, TOKEN_REFRESH_PATH)
    sanitized = json.dumps(redact(response), ensure_ascii=False, default=str)
    logger.warning(
        "BuyingGroup API failure response",
        extra={"details": {
            "method": "POST",
            "url": f"{parsed.scheme}://{parsed.hostname}{parsed.path}",
            "http_status": status,
            "response": "[auth response omitted]" if auth_endpoint else sanitized[:16384],
            "response_truncated": not auth_endpoint and len(sanitized) > 16384,
        }},
    )


def _multipart_request(
    *,
    url: str,
    fields: list[tuple[str, str]],
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 30.0,
) -> tuple[int, str, Any]:
    body, content_type = _encode_multipart(fields)
    return _http_post(
        url=url,
        body=body,
        content_type=content_type,
        headers=headers,
        timeout_seconds=timeout_seconds,
    )


def _json_request(
    *,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 30.0,
) -> tuple[int, str, Any]:
    body = json.dumps(payload).encode("utf-8")
    return _http_post(
        url=url,
        body=body,
        content_type="application/json",
        headers=headers,
        timeout_seconds=timeout_seconds,
    )


def _raise_if_fail_status(raw: Any, *, url: str, raw_text: str = "") -> None:
    """BG often returns HTTP 200 with ``{"status":"FAIL","message":...}``."""
    if not isinstance(raw, dict):
        return
    status_val = raw.get("status")
    if not isinstance(status_val, str) or status_val.strip().upper() != "FAIL":
        return
    detail = _error_detail(raw, raw_text, "FAIL")
    if _is_auth_failure(200, raw, raw_text):
        raise _SessionExpired(f"BuyingGroup auth failed at {url}: {detail}")
    raise RuntimeError(f"BuyingGroup API failed at {url}: {detail}")


def login(
    *,
    base_url: str | None,
    username: str,
    password: str,
    timeout_seconds: float = 30.0,
) -> _AuthTokens:
    if not (username or "").strip():
        raise ValueError("username is required")
    if not (password or "").strip():
        raise ValueError("password is required")

    # Same contract as dealsbot internal/sources/buyinggroup/auth.go:
    # JSON POST to {host}/v2/token/get.
    url = f"{resolve_base_url(base_url)}{TOKEN_GET_PATH}"
    status, raw_text, raw = _json_request(
        url=url,
        payload={
            "username": username.strip(),
            "password": password,
            "device_type": "web",
            "browser_name": "Chrome",
            "browser_version": "126",
            "hostname": "buyinggroup.com",
        },
        timeout_seconds=timeout_seconds,
    )

    if not isinstance(raw, dict):
        raise RuntimeError(f"BuyingGroup login returned unexpected payload: {raw_text[:300]}")

    _raise_if_fail_status(raw, url=url, raw_text=raw_text)

    if raw.get("verif_req_id"):
        raise RuntimeError(
            "BuyingGroup login requires interactive verification (2FA/email). "
            "Use an account without verification for API submit, or complete login in the browser first."
        )

    token_obj = raw.get("token")
    access = None
    refresh = None
    if isinstance(token_obj, dict):
        access_val = token_obj.get("access")
        if isinstance(access_val, str) and access_val.strip():
            access = access_val.strip()
        refresh_val = token_obj.get("refresh")
        if isinstance(refresh_val, str) and refresh_val.strip():
            refresh = refresh_val.strip()
    elif isinstance(token_obj, str) and token_obj.strip():
        access = token_obj.strip()

    # Some responses nest under data.
    if not access:
        data_obj = raw.get("data")
        if isinstance(data_obj, dict):
            nested = data_obj.get("token")
            if isinstance(nested, dict):
                access_val = nested.get("access")
                if isinstance(access_val, str) and access_val.strip():
                    access = access_val.strip()
                refresh_val = nested.get("refresh")
                if isinstance(refresh_val, str) and refresh_val.strip():
                    refresh = refresh_val.strip()
            elif isinstance(nested, str) and nested.strip():
                access = nested.strip()

    if status != 200 or not access:
        detail = _error_detail(raw, raw_text, "login failed")
        raise RuntimeError(f"BuyingGroup login failed (HTTP {status}): {detail}")

    return _AuthTokens(access=access, refresh=refresh)


def refresh_access_token(
    *,
    base_url: str | None,
    refresh_token: str,
    timeout_seconds: float = 30.0,
) -> _AuthTokens:
    if not (refresh_token or "").strip():
        raise ValueError("refresh_token is required")
    url = f"{resolve_base_url(base_url)}{TOKEN_REFRESH_PATH}"
    status, raw_text, raw = _json_request(
        url=url,
        payload={"refresh": refresh_token.strip()},
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(raw, dict):
        raise RuntimeError(f"BuyingGroup token refresh returned unexpected payload: {raw_text[:300]}")
    access = raw.get("access")
    if not isinstance(access, str) or not access.strip():
        detail = _error_detail(raw, raw_text, "refresh failed")
        raise RuntimeError(f"BuyingGroup token refresh failed (HTTP {status}): {detail}")
    new_refresh = raw.get("refresh")
    refresh_out = (
        new_refresh.strip()
        if isinstance(new_refresh, str) and new_refresh.strip()
        else refresh_token.strip()
    )
    return _AuthTokens(access=access.strip(), refresh=refresh_out)


def _with_reauth(
    *,
    base_url: str,
    username: str,
    password: str,
    tokens: _AuthTokens,
    timeout_seconds: float,
    call: Callable[[_AuthTokens], _T],
) -> tuple[_T, _AuthTokens]:
    """Run ``call(tokens)``; on auth failure, refresh/re-login, cache, and retry."""
    try:
        return call(tokens), tokens
    except _SessionExpired:
        tokens = _reauth_tokens(
            base_url=base_url,
            username=username,
            password=password,
            stale=tokens,
            timeout_seconds=timeout_seconds,
        )
        return call(tokens), tokens


def add_trackings(
    *,
    access_token: str,
    tracking_list: list[str],
    add_trackings_url: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float = 30.0,
) -> tuple[int, Any]:
    numbers = [(t or "").strip() for t in tracking_list if (t or "").strip()]
    if not numbers:
        raise ValueError("At least one tracking number is required")
    url = (add_trackings_url or "").strip() or resolve_add_trackings_url(base_url, None)
    # API validates tracking_list as JSON (array of strings), not repeated form fields.
    fields = [("tracking_list", json.dumps(numbers))]
    # OpenAPI documents ``auth_token``; the main API also accepts Bearer like other routes.
    status, raw_text, raw = _multipart_request(
        url=url,
        fields=fields,
        headers={
            "auth_token": access_token,
            "Authorization": f"Bearer {access_token}",
        },
        timeout_seconds=timeout_seconds,
    )
    if status >= 400:
        detail = _error_detail(raw, raw_text)
        raise RuntimeError(f"BuyingGroup add_trackings failed (HTTP {status}): {detail}")
    duplicates = _response_trackings(raw, "duplicate_trackings")
    if not duplicates.intersection(t.casefold() for t in numbers) or _is_auth_failure(status, raw, raw_text):
        _raise_if_fail_status(raw, url=url, raw_text=raw_text)
    return status, raw if raw is not None else {"raw": raw_text}


def _response_trackings(raw: Any, key: str) -> set[str]:
    payload = raw.get("payload") if isinstance(raw, dict) else None
    values = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(values, list):
        return set()
    return {value.strip().casefold() for value in values if isinstance(value, str) and value.strip()}


def _orders_from_payload(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [o for o in raw if isinstance(o, dict)]
    if not isinstance(raw, dict):
        return []
    payload = raw.get("payload")
    if isinstance(payload, dict):
        orders = payload.get("orders")
        if isinstance(orders, list):
            return [o for o in orders if isinstance(o, dict)]
        # Some responses nest the list under data inside payload.
        nested = payload.get("data")
        if isinstance(nested, list):
            return [o for o in nested if isinstance(o, dict)]
    elif isinstance(payload, list):
        return [o for o in payload if isinstance(o, dict)]
    data = raw.get("data")
    if isinstance(data, dict):
        payload = data.get("payload")
        if isinstance(payload, dict):
            orders = payload.get("orders")
            if isinstance(orders, list):
                return [o for o in orders if isinstance(o, dict)]
        orders = data.get("orders")
        if isinstance(orders, list):
            return [o for o in orders if isinstance(o, dict)]
    orders = raw.get("orders")
    if isinstance(orders, list):
        return [o for o in orders if isinstance(o, dict)]
    return []


def _looks_like_uuid(value: object) -> bool:
    text = str(value or "").strip()
    if len(text) < 32:
        return False
    # Accept standard UUID strings (with or without hyphens).
    hex_only = text.replace("-", "")
    if len(hex_only) != 32:
        return False
    try:
        int(hex_only, 16)
    except ValueError:
        return False
    return True


def _parse_order_row(
    row: dict[str, Any],
    *,
    fallback_tracking: str | None = None,
) -> BuyinggroupOrderRow | None:
    """Pick the UUID used by update_order and the human display id when present.

    BG list rows expose a human display id in ``order_id`` (e.g. ``O-269283098``)
    and the real UUID in ``key`` (what the UI sends as ``order_id`` on update).
    """
    display_raw = row.get("order_id")
    display_order_id: str | None = None
    if display_raw is not None and str(display_raw).strip():
        display_candidate = str(display_raw).strip()
        if not _looks_like_uuid(display_candidate):
            display_order_id = display_candidate

    candidates = (
        row.get("key"),
        row.get("id"),
        row.get("order_uuid"),
        row.get("uuid"),
        row.get("order_id"),
    )
    order_id = None
    for candidate in candidates:
        if candidate is None or not str(candidate).strip():
            continue
        # Prefer UUID-shaped values; skip display ids like O-123.
        if _looks_like_uuid(candidate) or order_id is None:
            order_id = candidate
        if _looks_like_uuid(candidate):
            break
    if order_id is not None and not _looks_like_uuid(order_id):
        # Last resort: if nothing looked like a UUID, still try key/id before display order_id.
        for candidate in (row.get("key"), row.get("id")):
            if candidate is not None and str(candidate).strip():
                order_id = candidate
                break

    tracking_id = row.get("tracking_id")
    if tracking_id is None:
        tracking_id = row.get("tracking_number") or row.get("tracking")
    if tracking_id is None or str(tracking_id).strip() in ("", "---", "null", "None"):
        tracking_id = fallback_tracking
    if order_id is None:
        return None
    oid = str(order_id).strip()
    tid = str(tracking_id or "").strip()
    if not oid:
        return None
    return BuyinggroupOrderRow(
        order_id=oid,
        tracking_id=tid,
        display_order_id=display_order_id,
    )


def _auth_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "auth_token": access_token,
    }


def find_order_by_tracking(
    *,
    base_url: str,
    access_token: str,
    tracking_number: str,
    timeout_seconds: float = 30.0,
) -> BuyinggroupOrderRow | None:
    tn = (tracking_number or "").strip()
    if not tn:
        return None
    url = f"{resolve_base_url(base_url)}{GET_ORDERS_PATH}"
    fields = [
        ("page", "1"),
        ("page_size", "50"),
        ("tracking_id", tn),
    ]
    _status, _text, raw = _multipart_request(
        url=url,
        fields=fields,
        headers=_auth_headers(access_token),
        timeout_seconds=timeout_seconds,
    )
    if isinstance(raw, dict):
        if _is_auth_failure(200, raw, _text):
            raise _SessionExpired(f"BuyingGroup auth failed at {url}: {_error_detail(raw, _text)}")
        status_val = raw.get("status")
        if isinstance(status_val, str) and status_val.strip().upper() == "FAIL":
            raise RuntimeError(f"BuyingGroup get_orders failed: {_error_detail(raw, _text)}")
    rows = _orders_from_payload(raw)
    tn_key = tn.casefold().replace(" ", "").replace("-", "")
    for row in rows:
        parsed = _parse_order_row(row)
        if not parsed:
            continue
        if parsed.tracking_id.casefold().replace(" ", "").replace("-", "") == tn_key:
            return parsed
    return None


def update_order(
    *,
    base_url: str,
    access_token: str,
    order_id: str,
    order_num: str | None = None,
    amount: Decimal | float | None = None,
    buyer_note: str | None = None,
    timeout_seconds: float = 30.0,
) -> None:
    """Patch order fields. Sends one field at a time (matches BG UI)."""
    oid = (order_id or "").strip()
    if not oid:
        raise ValueError("order_id is required")

    patches: list[tuple[str, str]] = []
    if order_num is not None and str(order_num).strip():
        patches.append(("order_num", str(order_num).strip()))
    amount_text = _format_amount(amount)
    if amount_text is not None:
        patches.append(("amount", amount_text))
    if buyer_note is not None and str(buyer_note).strip():
        patches.append(("buyer_note", str(buyer_note).strip()))
    if not patches:
        return

    url = f"{resolve_base_url(base_url)}{UPDATE_ORDER_PATH}"
    for field_name, field_value in patches:
        fields = [("order_id", oid), (field_name, field_value)]
        _status, raw_text, raw = _multipart_request(
            url=url,
            fields=fields,
            headers=_auth_headers(access_token),
            timeout_seconds=timeout_seconds,
        )
        _raise_if_fail_status(raw, url=url, raw_text=raw_text)
        if isinstance(raw, dict):
            status_val = raw.get("status")
            if isinstance(status_val, str) and status_val.strip().upper() not in (
                "SUCCESS",
                "OK",
                "",
            ):
                detail = _error_detail(raw, raw_text, status_val)
                raise RuntimeError(f"BuyingGroup update_order failed ({field_name}): {detail}")
            data = raw.get("data")
            if isinstance(data, dict):
                nested_status = data.get("status")
                if isinstance(nested_status, str) and nested_status.strip().upper() not in (
                    "SUCCESS",
                    "OK",
                    "",
                ):
                    detail = _error_detail(data, raw_text, nested_status)
                    raise RuntimeError(f"BuyingGroup update_order failed ({field_name}): {detail}")


def _message_from_add_payload(raw: Any, *, count: int) -> str:
    if isinstance(raw, dict):
        for key in ("message", "detail"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        status = raw.get("status")
        if isinstance(status, str) and status.strip().upper() == "SUCCESS":
            return f"Submitted {count} tracking number(s) to BuyingGroup.com."
    return f"Submitted {count} tracking number(s) to BuyingGroup.com."


def _nested_tracking_id(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("tracking_id", "tracking_number", "tracking"):
            nested = value.get(key)
            if nested is not None and str(nested).strip() and str(nested).strip().lower() not in (
                "null",
                "none",
                "---",
                "undefined",
            ):
                return str(nested).strip()
        return None
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("null", "none", "---", "undefined"):
        return None
    return text


def _parse_money_field(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    text = str(value).strip().replace(",", "").replace("$", "")
    if not text or text.lower() in ("null", "none", "undefined"):
        return None
    try:
        return Decimal(text)
    except Exception:
        return None


def _parse_int_field(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text or text.lower() in ("null", "none", "undefined"):
        return None
    try:
        return int(Decimal(text))
    except Exception:
        return None


def _parse_receipt_row(row: dict[str, Any]) -> BuyinggroupReceipt:
    tracking_id = _nested_tracking_id(row.get("tracking"))
    if tracking_id is None:
        tracking_id = _nested_tracking_id(row.get("tracking_id"))
    alt_tracking_id = _nested_tracking_id(row.get("alt_tracking"))
    receipt_id = row.get("receipt_id")
    order_id = row.get("order_id")
    status = row.get("status")
    note = row.get("note")
    tracking_note = row.get("tracking_note")
    delivery = row.get("delivery")
    created_dt = row.get("created_dt") or row.get("created_date") or row.get("created_at")
    void_raw = row.get("void")
    void = bool(void_raw) if void_raw is not None else False
    return BuyinggroupReceipt(
        receipt_id=str(receipt_id).strip() if receipt_id is not None and str(receipt_id).strip() else None,
        tracking_id=tracking_id,
        alt_tracking_id=alt_tracking_id,
        quantity=_parse_int_field(row.get("quantity") if row.get("quantity") is not None else row.get("qty")),
        total=_parse_money_field(row.get("total")),
        reward=_parse_money_field(row.get("reward")),
        status=str(status).strip() if status is not None and str(status).strip() else None,
        void=void,
        created_dt=str(created_dt).strip() if created_dt is not None and str(created_dt).strip() else None,
        order_id=str(order_id).strip() if order_id is not None and str(order_id).strip() else None,
        note=str(note).strip() if note is not None and str(note).strip() else None,
        tracking_note=(
            str(tracking_note).strip()
            if tracking_note is not None and str(tracking_note).strip()
            else None
        ),
        delivery=str(delivery).strip() if delivery is not None and str(delivery).strip() else None,
        raw=row,
    )


def _receipts_from_payload(raw: Any) -> tuple[list[BuyinggroupReceipt], int]:
    """Return (receipts, page_count). page_count is 1 when unknown."""
    if not isinstance(raw, dict):
        return [], 1
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        payload = raw
    rows_raw = payload.get("receipts")
    if not isinstance(rows_raw, list):
        rows_raw = payload.get("data") if isinstance(payload.get("data"), list) else []
    pages = _parse_int_field(payload.get("pages")) or 1
    if pages < 1:
        pages = 1
    receipts = [_parse_receipt_row(r) for r in rows_raw if isinstance(r, dict)]
    return receipts, pages


def _receipt_list_fields(*, page: int, page_size: int = _DEFAULT_PAGE_SIZE) -> list[tuple[str, str]]:
    return [
        ("page", str(page)),
        ("page_size", str(page_size)),
        ("receiver_id", _UNDEFINED),
        ("delivery", _UNDEFINED),
        ("tracking_id", _UNDEFINED),
        ("receipt_ids", _UNDEFINED),
        ("status", "[]"),
        ("paid", _UNDEFINED),
        ("is_label_mismatch", _UNDEFINED),
        ("no_user", _UNDEFINED),
        ("no_item", _UNDEFINED),
        ("note", _UNDEFINED),
        ("split_from", _UNDEFINED),
        ("bulk_order", _UNDEFINED),
        ("warehouse_id", _UNDEFINED),
        ("order_id", _UNDEFINED),
        ("date_range", "[]"),
        ("time_period", _UNDEFINED),
        ("order_by", _UNDEFINED),
        ("direction", _UNDEFINED),
        ("enable_totalization", "false"),
    ]


def _fetch_receipts_page(
    *,
    base_url: str,
    access_token: str,
    page: int,
    page_size: int = _DEFAULT_PAGE_SIZE,
    timeout_seconds: float = 30.0,
) -> tuple[list[BuyinggroupReceipt], int, Any]:
    url = f"{resolve_base_url(base_url)}{GET_RECEIPTS_PATH}"
    status, text, raw = _multipart_request(
        url=url,
        fields=_receipt_list_fields(page=page, page_size=page_size),
        headers=_auth_headers(access_token),
        timeout_seconds=timeout_seconds,
    )
    if isinstance(raw, dict):
        if _is_auth_failure(status, raw, text):
            raise _SessionExpired(f"BuyingGroup auth failed at {url}: {_error_detail(raw, text)}")
        status_val = raw.get("status")
        if isinstance(status_val, str) and status_val.strip().upper() == "FAIL":
            raise RuntimeError(f"BuyingGroup get_receipts failed: {_error_detail(raw, text)}")
    receipts, pages = _receipts_from_payload(raw)
    return receipts, pages, raw


def list_receipts(
    *,
    base_url: str | None,
    username: str,
    password: str,
    page_size: int = _DEFAULT_PAGE_SIZE,
    timeout_seconds: float = 30.0,
) -> list[BuyinggroupReceipt]:
    """Paginate get_receipts with no filters; return all receipt rows."""
    base = resolve_base_url(base_url)
    tokens = _ensure_tokens(
        base_url=base,
        username=username,
        password=password,
        timeout_seconds=timeout_seconds,
    )
    all_rows: list[BuyinggroupReceipt] = []
    total_pages = 1
    page = 1
    while page <= total_pages and page <= _MAX_LIST_PAGES:

        def _call(toks: _AuthTokens, *, _page: int = page) -> tuple[list[BuyinggroupReceipt], int, Any]:
            return _fetch_receipts_page(
                base_url=base,
                access_token=toks.access,
                page=_page,
                page_size=page_size,
                timeout_seconds=timeout_seconds,
            )

        (rows, pages, _raw), tokens = _with_reauth(
            base_url=base,
            username=username,
            password=password,
            tokens=tokens,
            timeout_seconds=timeout_seconds,
            call=_call,
        )
        if page == 1:
            total_pages = min(max(pages, 1), _MAX_LIST_PAGES)
        all_rows.extend(rows)
        if not rows:
            break
        page += 1
    return all_rows


def _analytics_list_fields(*, page: int, page_size: int = _DEFAULT_PAGE_SIZE) -> list[tuple[str, str]]:
    return [
        ("page", str(page)),
        ("page_size", str(page_size)),
        ("order_by", _UNDEFINED),
        ("direction", _UNDEFINED),
        ("date_range", _UNDEFINED),
        ("user_id", _UNDEFINED),
        ("serial", _UNDEFINED),
        ("order_note", _UNDEFINED),
        ("tracking_note", _UNDEFINED),
        ("item_id", _UNDEFINED),
        ("commission", _UNDEFINED),
        ("price", _UNDEFINED),
        ("tracking_id", _UNDEFINED),
        ("location_id", _UNDEFINED),
        ("time_period", "LAST_3_MONTHS"),
        ("no_user", _UNDEFINED),
        ("enable_totalization", "false"),
    ]


def _first_str(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in ("null", "none", "undefined", "---"):
            return text
    return None


def _parse_analytics_row(row: dict[str, Any]) -> BuyinggroupAnalyticsRow:
    tracking_id = _nested_tracking_id(row.get("tracking"))
    if tracking_id is None:
        tracking_id = _nested_tracking_id(row.get("tracking_id") or row.get("tracking_number"))
    description = _first_str(
        row.get("item"),
        row.get("item_name"),
        row.get("description"),
        row.get("product"),
        row.get("title"),
        row.get("name"),
        row.get("serial"),
    )
    note = _first_str(row.get("note"), row.get("order_note"), row.get("buyer_note"))
    tracking_note = _first_str(row.get("tracking_note"))
    order_id = _first_str(row.get("order_id"), row.get("order_num"), row.get("order_number"))
    qty = _parse_int_field(row.get("quantity") if row.get("quantity") is not None else row.get("qty"))
    price = _parse_money_field(row.get("price") if row.get("price") is not None else row.get("unit_price"))
    total = _parse_money_field(
        row.get("total")
        if row.get("total") is not None
        else row.get("ext_total") if row.get("ext_total") is not None else row.get("amount")
    )
    return BuyinggroupAnalyticsRow(
        tracking_id=tracking_id,
        order_id=order_id,
        description=description,
        quantity=qty,
        price=price,
        total=total,
        note=note,
        tracking_note=tracking_note,
        raw=row,
    )


def _analytics_rows_from_payload(raw: Any) -> tuple[list[BuyinggroupAnalyticsRow], int]:
    if not isinstance(raw, dict):
        if isinstance(raw, list):
            return [_parse_analytics_row(r) for r in raw if isinstance(r, dict)], 1
        return [], 1
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        payload = raw if isinstance(raw, dict) else {}
    rows_raw: list[Any] = []
    for key in (
        "analytics",
        "orders",
        "items",
        "receipts",
        "rows",
        "data",
        "results",
    ):
        candidate = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(candidate, list):
            rows_raw = candidate
            break
    if not rows_raw and isinstance(payload, list):
        rows_raw = payload
    pages = 1
    if isinstance(payload, dict):
        pages = _parse_int_field(payload.get("pages")) or 1
    if pages < 1:
        pages = 1
    return [_parse_analytics_row(r) for r in rows_raw if isinstance(r, dict)], pages


def _fetch_analytics_page(
    *,
    base_url: str,
    access_token: str,
    page: int,
    page_size: int = _DEFAULT_PAGE_SIZE,
    timeout_seconds: float = 30.0,
) -> tuple[list[BuyinggroupAnalyticsRow], int, Any]:
    url = f"{resolve_base_url(base_url)}{GET_ANALYTICS_PATH}"
    status, text, raw = _multipart_request(
        url=url,
        fields=_analytics_list_fields(page=page, page_size=page_size),
        headers=_auth_headers(access_token),
        timeout_seconds=timeout_seconds,
    )
    if isinstance(raw, dict):
        if _is_auth_failure(status, raw, text):
            raise _SessionExpired(f"BuyingGroup auth failed at {url}: {_error_detail(raw, text)}")
        status_val = raw.get("status")
        if isinstance(status_val, str) and status_val.strip().upper() == "FAIL":
            raise RuntimeError(f"BuyingGroup get_analytics failed: {_error_detail(raw, text)}")
    rows, pages = _analytics_rows_from_payload(raw)
    return rows, pages, raw


def list_receipt_analytics(
    *,
    base_url: str | None,
    username: str,
    password: str,
    page_size: int = _DEFAULT_PAGE_SIZE,
    timeout_seconds: float = 30.0,
) -> list[BuyinggroupAnalyticsRow]:
    """Paginate get_analytics (LAST_3_MONTHS, no other filters)."""
    base = resolve_base_url(base_url)
    tokens = _ensure_tokens(
        base_url=base,
        username=username,
        password=password,
        timeout_seconds=timeout_seconds,
    )
    all_rows: list[BuyinggroupAnalyticsRow] = []
    total_pages = 1
    page = 1
    while page <= total_pages and page <= _MAX_LIST_PAGES:

        def _call(toks: _AuthTokens, *, _page: int = page) -> tuple[list[BuyinggroupAnalyticsRow], int, Any]:
            return _fetch_analytics_page(
                base_url=base,
                access_token=toks.access,
                page=_page,
                page_size=page_size,
                timeout_seconds=timeout_seconds,
            )

        (rows, pages, _raw), tokens = _with_reauth(
            base_url=base,
            username=username,
            password=password,
            tokens=tokens,
            timeout_seconds=timeout_seconds,
            call=_call,
        )
        if page == 1:
            total_pages = min(max(pages, 1), _MAX_LIST_PAGES)
        all_rows.extend(rows)
        if not rows:
            break
        page += 1
    return all_rows


def submit_trackings(
    *,
    base_url: str | None,
    api_url: str | None,
    username: str,
    password: str,
    trackings: list[BuyinggroupTrackingEntry],
    timeout_seconds: float = 30.0,
) -> BuyinggroupSubmitResult:
    require_group_submissions_enabled()
    if not trackings:
        raise ValueError("At least one tracking entry is required")

    base = resolve_base_url(base_url)
    add_url = resolve_add_trackings_url(base, api_url)
    tracking_list = [(e.tracking or "").strip() for e in trackings if (e.tracking or "").strip()]
    if not tracking_list:
        raise ValueError("At least one tracking number is required")

    tokens = _ensure_tokens(
        base_url=base,
        username=username,
        password=password,
        timeout_seconds=timeout_seconds,
    )

    def _do_add(toks: _AuthTokens) -> tuple[int, Any]:
        return add_trackings(
            access_token=toks.access,
            tracking_list=tracking_list,
            add_trackings_url=add_url,
            base_url=base,
            timeout_seconds=timeout_seconds,
        )

    (_status, raw), tokens = _with_reauth(
        base_url=base,
        username=username,
        password=password,
        tokens=tokens,
        timeout_seconds=timeout_seconds,
        call=_do_add,
    )

    message = _message_from_add_payload(raw, count=len(tracking_list))
    duplicates = _response_trackings(raw, "duplicate_trackings")
    added = _response_trackings(raw, "added_trackings")
    rejected = _response_trackings(raw, "invalid_trackings") | _response_trackings(raw, "invalid_otps")
    payload = raw.get("payload") if isinstance(raw, dict) else None
    explicit_outcomes = isinstance(payload, dict) and any(
        isinstance(payload.get(key), list)
        for key in ("added_trackings", "duplicate_trackings", "invalid_trackings", "invalid_otps")
    )
    succeeded = {
        tn.casefold() for tn in tracking_list
        if tn.casefold() not in duplicates | rejected
        and (not explicit_outcomes or tn.casefold() in added)
    }

    enriched_count = 0
    enrich_errors: list[str] = []
    submission_ids: dict[str, BuyinggroupSubmissionIds] = {}
    for entry in trackings:
        tn = (entry.tracking or "").strip()
        if not tn:
            continue
        is_duplicate = tn.casefold() in duplicates and tn.casefold() not in rejected
        if tn.casefold() not in succeeded and not is_duplicate:
            continue
        needs_enrich = bool(
            (entry.order_num or "").strip()
            or entry.amount is not None
            or (entry.note or "").strip()
        )
        try:

            def _lookup(toks: _AuthTokens, *, _tn: str = tn) -> BuyinggroupOrderRow | None:
                return find_order_by_tracking(
                    base_url=base,
                    access_token=toks.access,
                    tracking_number=_tn,
                    timeout_seconds=timeout_seconds,
                )

            row: BuyinggroupOrderRow | None = None
            # Newly added trackings are not always immediately searchable.
            for attempt in range(6):
                row, tokens = _with_reauth(
                    base_url=base,
                    username=username,
                    password=password,
                    tokens=tokens,
                    timeout_seconds=timeout_seconds,
                    call=_lookup,
                )
                if row is not None:
                    break
                if attempt < 5:
                    time.sleep(1.0)
            if row is None:
                if is_duplicate:
                    raise RuntimeError("duplicate tracking could not be verified in get_orders; left pending")
                if needs_enrich:
                    raise RuntimeError("order not found after submit (get_orders empty)")
                logger.warning(
                    "BuyingGroup order lookup empty for %s after submit (no ids)",
                    tn,
                )
                continue

            if is_duplicate:
                succeeded.add(tn.casefold())
                logger.info(
                    "BuyingGroup verified existing tracking %s (display=%s api=%s)",
                    tn, row.display_order_id, row.order_id,
                )
            submission_ids[tn] = BuyinggroupSubmissionIds(
                display_id=row.display_order_id or None,
                api_id=row.order_id or None,
            )

            if is_duplicate:
                continue

            if not needs_enrich:
                logger.info(
                    "BuyingGroup captured ids for %s (display=%s api=%s); enrich skipped",
                    tn,
                    row.display_order_id,
                    row.order_id,
                )
                continue

            def _update(
                toks: _AuthTokens,
                *,
                _row: BuyinggroupOrderRow = row,
                _entry: BuyinggroupTrackingEntry = entry,
            ) -> None:
                update_order(
                    base_url=base,
                    access_token=toks.access,
                    order_id=_row.order_id,
                    order_num=_entry.order_num,
                    amount=_entry.amount,
                    buyer_note=_entry.note,
                    timeout_seconds=timeout_seconds,
                )

            _, tokens = _with_reauth(
                base_url=base,
                username=username,
                password=password,
                tokens=tokens,
                timeout_seconds=timeout_seconds,
                call=_update,
            )
            enriched_count += 1
            logger.info(
                "BuyingGroup enriched tracking %s (order_id=%s display=%s order_num=%s amount=%s)",
                tn,
                row.order_id,
                row.display_order_id,
                entry.order_num,
                entry.amount,
            )
        except Exception as exc:
            err = f"{tn}: {exc}"
            enrich_errors.append(err)
            logger.warning("BuyingGroup enrich/lookup failed: %s", err)

    successful_trackings = [tn for tn in tracking_list if tn.casefold() in succeeded]
    failed_trackings = [tn for tn in tracking_list if tn.casefold() not in succeeded]
    if duplicates:
        verified = len({tn.casefold() for tn in successful_trackings} & duplicates)
        message = (
            f"Submitted {len(successful_trackings) - verified} new tracking number(s); "
            f"verified {verified} already present in BuyingGroup.com."
        )
    if failed_trackings:
        message += f"\nLeft pending ({len(failed_trackings)}): {', '.join(failed_trackings)}."

    if enriched_count and enrich_errors:
        message = (
            f"{message}\nEnriched {enriched_count} tracking(s); "
            f"{len(enrich_errors)} enrich error(s): {'; '.join(enrich_errors[:3])}"
        )
    elif enriched_count:
        message = f"{message}\nEnriched {enriched_count} tracking(s) with order/amount/note."
    elif enrich_errors:
        message = f"{message}\nEnrich failed: {'; '.join(enrich_errors[:3])}"

    return BuyinggroupSubmitResult(
        success=bool(successful_trackings),
        message=message,
        affected=len(successful_trackings),
        raw=raw if isinstance(raw, (dict, list)) else None,
        enriched_count=enriched_count,
        enrich_errors=enrich_errors,
        submission_ids=submission_ids,
        succeeded=successful_trackings,
        failed=failed_trackings,
    )
