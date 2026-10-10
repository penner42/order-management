"""BuyingGroup.com tracking submission client.

Flow:
1. Login via ``POST /v2/token/get`` (username/password → access token) — JSON, same as dealsbot
2. Add trackings via ``POST /v1/order/add_trackings`` (``auth_token`` + Bearer)
3. Enrich each tracking via ``/v1/order/get_orders`` + ``/v1/order/update_order``
   (order_num, amount, buyer_note)
"""
from __future__ import annotations

import http.client
import json
import logging
import ssl
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

# Match dealsbot's working BuyingGroup client (WAF rejects non-browser UAs).
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_SITE_ORIGIN = "https://buyinggroup.com"

_T = TypeVar("_T")


@dataclass
class BuyinggroupTrackingEntry:
    tracking: str
    amount: Decimal | float | None = None
    order_num: str | None = None
    note: str | None = None


@dataclass
class BuyinggroupOrderRow:
    order_id: str
    tracking_id: str


@dataclass
class BuyinggroupSubmitResult:
    success: bool
    message: str
    affected: int | None
    raw: dict[str, Any] | list[Any] | None = None
    enriched_count: int = 0
    enrich_errors: list[str] = field(default_factory=list)


@dataclass
class _AuthTokens:
    access: str
    refresh: str | None = None


class _SessionExpired(RuntimeError):
    pass


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
        if status >= 400:
            detail = raw_text.strip()[:400] or f"HTTP {status}"
            raise RuntimeError(f"BuyingGroup API error {status} at {url}: {detail}")
        raise

    if status >= 400:
        if authed and _is_auth_failure(status, raw, raw_text):
            raise _SessionExpired(f"BuyingGroup session expired (HTTP {status})")
        detail = _error_detail(raw, raw_text)
        raise RuntimeError(f"BuyingGroup API error {status} at {url}: {detail}")
    return status, raw_text, raw


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
    try:
        return call(tokens), tokens
    except _SessionExpired:
        if tokens.refresh:
            try:
                tokens = refresh_access_token(
                    base_url=base_url,
                    refresh_token=tokens.refresh,
                    timeout_seconds=timeout_seconds,
                )
                return call(tokens), tokens
            except Exception:
                logger.debug("BuyingGroup refresh failed; falling back to full login", exc_info=True)
        tokens = login(
            base_url=base_url,
            username=username,
            password=password,
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
    _raise_if_fail_status(raw, url=url, raw_text=raw_text)
    return status, raw if raw is not None else {"raw": raw_text}


def _orders_from_payload(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    payload = raw.get("payload")
    if isinstance(payload, dict):
        orders = payload.get("orders")
        if isinstance(orders, list):
            return [o for o in orders if isinstance(o, dict)]
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


def _parse_order_row(row: dict[str, Any]) -> BuyinggroupOrderRow | None:
    order_id = row.get("order_id")
    if order_id is None:
        order_id = row.get("key")
    tracking_id = row.get("tracking_id")
    if tracking_id is None:
        tracking_id = row.get("tracking_number") or row.get("tracking")
    if order_id is None or tracking_id is None:
        return None
    oid = str(order_id).strip()
    tid = str(tracking_id).strip()
    if not oid or not tid:
        return None
    return BuyinggroupOrderRow(order_id=oid, tracking_id=tid)


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
        ("page_size", "20"),
        ("tracking_id", tn),
    ]
    _status, _text, raw = _multipart_request(
        url=url,
        fields=fields,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout_seconds=timeout_seconds,
    )
    # Auth FAIL should re-login; other FAIL statuses still attempt empty parse.
    if isinstance(raw, dict) and _is_auth_failure(200, raw, _text):
        raise _SessionExpired(f"BuyingGroup auth failed at {url}: {_error_detail(raw, _text)}")
    rows = _orders_from_payload(raw)
    tn_key = tn.casefold()
    for row in rows:
        parsed = _parse_order_row(row)
        if parsed and parsed.tracking_id.casefold() == tn_key:
            return parsed
    if len(rows) == 1:
        return _parse_order_row(rows[0])
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
    oid = (order_id or "").strip()
    if not oid:
        raise ValueError("order_id is required")
    fields: list[tuple[str, str]] = [("order_id", oid)]
    if order_num is not None and str(order_num).strip():
        fields.append(("order_num", str(order_num).strip()))
    amount_text = _format_amount(amount)
    if amount_text is not None:
        fields.append(("amount", amount_text))
    if buyer_note is not None and str(buyer_note).strip():
        fields.append(("buyer_note", str(buyer_note).strip()))
    if len(fields) == 1:
        return  # nothing to update

    url = f"{resolve_base_url(base_url)}{UPDATE_ORDER_PATH}"
    _status, raw_text, raw = _multipart_request(
        url=url,
        fields=fields,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout_seconds=timeout_seconds,
    )
    _raise_if_fail_status(raw, url=url, raw_text=raw_text)
    if isinstance(raw, dict):
        status_val = raw.get("status")
        if isinstance(status_val, str) and status_val.strip().upper() not in ("SUCCESS", "OK", ""):
            detail = _error_detail(raw, raw_text, status_val)
            raise RuntimeError(f"BuyingGroup update_order failed: {detail}")
        data = raw.get("data")
        if isinstance(data, dict):
            nested_status = data.get("status")
            if isinstance(nested_status, str) and nested_status.strip().upper() not in (
                "SUCCESS",
                "OK",
                "",
            ):
                detail = _error_detail(data, raw_text, nested_status)
                raise RuntimeError(f"BuyingGroup update_order failed: {detail}")


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


def submit_trackings(
    *,
    base_url: str | None,
    api_url: str | None,
    username: str,
    password: str,
    trackings: list[BuyinggroupTrackingEntry],
    timeout_seconds: float = 30.0,
) -> BuyinggroupSubmitResult:
    if not trackings:
        raise ValueError("At least one tracking entry is required")

    base = resolve_base_url(base_url)
    add_url = resolve_add_trackings_url(base, api_url)
    tracking_list = [(e.tracking or "").strip() for e in trackings if (e.tracking or "").strip()]
    if not tracking_list:
        raise ValueError("At least one tracking number is required")

    tokens = login(
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
    affected = len(tracking_list)

    enriched_count = 0
    enrich_errors: list[str] = []
    for entry in trackings:
        tn = (entry.tracking or "").strip()
        if not tn:
            continue
        needs_enrich = bool(
            (entry.order_num or "").strip()
            or entry.amount is not None
            or (entry.note or "").strip()
        )
        if not needs_enrich:
            continue
        try:

            def _lookup(toks: _AuthTokens, *, _tn: str = tn) -> BuyinggroupOrderRow | None:
                return find_order_by_tracking(
                    base_url=base,
                    access_token=toks.access,
                    tracking_number=_tn,
                    timeout_seconds=timeout_seconds,
                )

            row: BuyinggroupOrderRow | None = None
            for attempt in range(3):
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
                if attempt < 2:
                    time.sleep(0.75)
            if row is None:
                enrich_errors.append(f"{tn}: order not found after submit")
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
        except Exception as exc:
            enrich_errors.append(f"{tn}: {exc}")

    if enriched_count and enrich_errors:
        message = (
            f"{message}\nEnriched {enriched_count} tracking(s); "
            f"{len(enrich_errors)} enrich error(s)."
        )
    elif enriched_count:
        message = f"{message}\nEnriched {enriched_count} tracking(s) with order/amount/note."
    elif enrich_errors:
        message = f"{message}\nEnrich failed: {'; '.join(enrich_errors[:3])}"

    return BuyinggroupSubmitResult(
        success=True,
        message=message,
        affected=affected,
        raw=raw if isinstance(raw, (dict, list)) else None,
        enriched_count=enriched_count,
        enrich_errors=enrich_errors,
    )
