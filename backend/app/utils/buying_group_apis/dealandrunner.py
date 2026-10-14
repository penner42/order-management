"""Deal & Runner (api.dealandrunner.com) tracking + receiving client.

Flow (aligned with dealsbot dealandrunner auth):
1. Login via ``POST /account/login`` (email/password → bearer token). Cache token.
2. Create trackings via ``POST /tracking`` with ``{"trackingNumbers":[...]}``.
3. On 401, re-login once and retry. On batch HTTP 400, fall back to per-TN POSTs.
4. Duplicate TN (``4004000035``) counts as success for that number.
5. Look up row ``id`` via ``GET /tracking`` after submit for submission_id.
6. Receiving: paginate ``GET /balanace-reports/runner-transactions`` (API typo preserved).
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, TypeVar


DEFAULT_BASE_URL = "https://api.dealandrunner.com"
LOGIN_PATH = "/account/login"
TRACKING_PATH = "/tracking"
# Upstream path is misspelled ("balanace"); do not "fix" it.
RUNNER_TRANSACTIONS_PATH = "/balanace-reports/runner-transactions"

_SITE_ORIGIN = "https://portal.dealandrunner.com"
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

DUPLICATE_TN_CODE = "4004000035"
CREATE_SUCCESS_CODE = "2004000007"

_DEFAULT_TX_PAGE_SIZE = 50
_MAX_TX_PAGES = 100

_T = TypeVar("_T")

# Process-local bearer tokens keyed by (base_url, email, password hash).
_token_cache: dict[tuple[str, str, str], str] = {}
# Optional runner/owner UUID resolved from login (same key as token cache).
_owner_cache: dict[tuple[str, str, str], str | None] = {}
_token_lock = threading.Lock()


@dataclass
class DealandrunnerTrackingEntry:
    tracking: str


@dataclass
class DealandrunnerTrackingRow:
    tracking_id: str
    tracking_numbers: list[str]


@dataclass
class DealandrunnerSubmitResult:
    success: bool
    message: str
    affected: int | None
    raw: dict[str, Any] | list[Any] | None = None
    succeeded: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    # tracking number -> remote row id
    submission_ids: dict[str, str] = field(default_factory=dict)


@dataclass
class DealandrunnerReceivedRow:
    """One runner-transactions ledger line (typically qty 1 at the warehouse)."""

    tracking_numbers: list[str]
    amount: Decimal | None
    product_name: str | None
    system_transaction_id: str | None
    timestamp: str | None
    owner_id: str | None = None
    reference_warehouse_item_id: str | None = None
    entry_type_id: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DealandrunnerRunnerTransactionsResult:
    rows: list[DealandrunnerReceivedRow]
    page_count: int = 0
    total_records: int = 0
    request_urls: list[str] = field(default_factory=list)
    owner_id: str | None = None


class _SessionExpired(RuntimeError):
    pass


def resolve_base_url(base_url: str | None) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        base = DEFAULT_BASE_URL
    return base.rstrip("/")


def _cache_key(base_url: str, email: str, password: str) -> tuple[str, str, str]:
    pw_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return (resolve_base_url(base_url), email.strip().casefold(), pw_hash)


def _browser_headers(*, content_json: bool = False, bearer: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Origin": _SITE_ORIGIN,
        "Referer": f"{_SITE_ORIGIN}/",
        "User-Agent": _BROWSER_USER_AGENT,
    }
    if content_json:
        headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    return headers


def _read_json_response(resp) -> tuple[int, str, dict[str, Any]]:
    raw_text = resp.read().decode("utf-8", errors="replace")
    status = getattr(resp, "status", 200)
    try:
        raw = json.loads(raw_text) if raw_text else {}
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Deal & Runner API returned non-JSON (HTTP {status}): {raw_text[:300]}"
        ) from e
    if not isinstance(raw, dict):
        raise RuntimeError(f"Deal & Runner API returned unexpected payload: {raw_text[:300]}")
    return status, raw_text, raw


def _parse_http_error(e: urllib.error.HTTPError) -> tuple[str, dict[str, Any]]:
    err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
    raw: dict[str, Any] = {}
    if err_body.strip():
        try:
            parsed = json.loads(err_body)
            if isinstance(parsed, dict):
                raw = parsed
        except json.JSONDecodeError:
            pass
    return err_body, raw


def _envelope_error_items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    errors = raw.get("errors")
    if isinstance(errors, list):
        return [e for e in errors if isinstance(e, dict)]
    return []


def _envelope_message_items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    messages = raw.get("messages")
    if isinstance(messages, list):
        return [m for m in messages if isinstance(m, dict)]
    return []


def _first_error_description(raw: dict[str, Any]) -> str:
    for err in _envelope_error_items(raw):
        desc = err.get("description")
        if isinstance(desc, str) and desc.strip():
            return desc.strip()
        code = err.get("errorCode")
        if isinstance(code, str) and code.strip():
            return code.strip()
    for key in ("title", "detail", "message"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _is_duplicate_payload(raw: dict[str, Any]) -> bool:
    for err in _envelope_error_items(raw):
        code = err.get("errorCode")
        if isinstance(code, str) and code.strip() == DUPLICATE_TN_CODE:
            return True
    return False


def _token_from_login_payload(raw: dict[str, Any]) -> str | None:
    result = raw.get("result")
    if isinstance(result, dict):
        for key in ("accessToken", "token"):
            val = result.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    for key in ("accessToken", "token"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _owner_id_from_payload(raw: dict[str, Any]) -> str | None:
    """Best-effort runner/owner UUID from login or transaction payloads."""

    def _from_obj(obj: dict[str, Any]) -> str | None:
        for key in (
            "ownerId",
            "referenceRunnerId",
            "runnerId",
            "userId",
            "id",
        ):
            val = obj.get(key)
            if isinstance(val, str) and val.strip() and val.strip() != "00000000-0000-0000-0000-000000000000":
                return val.strip()
        owners = obj.get("ownerIds")
        if isinstance(owners, list):
            for val in owners:
                if isinstance(val, str) and val.strip():
                    return val.strip()
        user = obj.get("user")
        if isinstance(user, dict):
            nested = _from_obj(user)
            if nested:
                return nested
        return None

    result = raw.get("result")
    if isinstance(result, dict):
        found = _from_obj(result)
        if found:
            return found
    return _from_obj(raw)


def _login_raw(
    *,
    base_url: str | None,
    email: str,
    password: str,
    timeout_seconds: float = 30.0,
) -> tuple[str, str | None]:
    """Login and return (bearer_token, owner_id_or_none)."""
    if not (email or "").strip():
        raise ValueError("email is required")
    if not (password or "").strip():
        raise ValueError("password is required")

    base = resolve_base_url(base_url)
    url = f"{base}{LOGIN_PATH}"
    body = {"email": email.strip(), "password": password}
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers=_browser_headers(content_json=True),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            status, raw_text, raw = _read_json_response(resp)
    except urllib.error.HTTPError as e:
        err_body, err_raw = _parse_http_error(e)
        detail = _first_error_description(err_raw) if err_raw else ""
        if not detail:
            detail = err_body.strip()[:400] or (str(e.reason).strip() if e.reason else "") or "empty response"
        raise RuntimeError(f"Deal & Runner login failed (HTTP {e.code}) at {url}: {detail}") from e
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e)).strip() or e.__class__.__name__
        raise RuntimeError(f"Deal & Runner login request failed ({url}): {reason}") from e

    if status != 200:
        detail = _first_error_description(raw) or raw_text[:400]
        raise RuntimeError(f"Deal & Runner login failed (HTTP {status}): {detail}")

    errors = _envelope_error_items(raw)
    if errors:
        detail = _first_error_description(raw) or "login failed"
        raise RuntimeError(f"Deal & Runner login failed: {detail}")

    token = _token_from_login_payload(raw)
    if not token:
        raise RuntimeError("Deal & Runner login succeeded but no access token returned")
    return token, _owner_id_from_payload(raw)


def login(
    *,
    base_url: str | None,
    email: str,
    password: str,
    timeout_seconds: float = 30.0,
) -> str:
    token, _owner = _login_raw(
        base_url=base_url,
        email=email,
        password=password,
        timeout_seconds=timeout_seconds,
    )
    return token


def _store_session(
    key: tuple[str, str, str],
    *,
    token: str,
    owner_id: str | None,
    stale_token: str | None = None,
) -> str:
    with _token_lock:
        cached = _token_cache.get(key)
        if cached and (stale_token is None or cached != stale_token):
            return cached
        _token_cache[key] = token
        # Only overwrite owner when login provided one (preserve prior if None).
        if owner_id or key not in _owner_cache:
            _owner_cache[key] = owner_id
        return token


def _cached_owner_id(key: tuple[str, str, str]) -> str | None:
    with _token_lock:
        return _owner_cache.get(key)


def _ensure_token(
    *,
    base_url: str,
    email: str,
    password: str,
    timeout_seconds: float,
) -> str:
    key = _cache_key(base_url, email, password)
    with _token_lock:
        cached = _token_cache.get(key)
        if cached:
            return cached
    token, owner_id = _login_raw(
        base_url=base_url,
        email=email,
        password=password,
        timeout_seconds=timeout_seconds,
    )
    return _store_session(key, token=token, owner_id=owner_id)


def _rellogin_token(
    *,
    base_url: str,
    email: str,
    password: str,
    stale_token: str,
    timeout_seconds: float,
) -> str:
    key = _cache_key(base_url, email, password)
    with _token_lock:
        cached = _token_cache.get(key)
        if cached and cached != stale_token:
            return cached
        _token_cache.pop(key, None)
        _owner_cache.pop(key, None)
    token, owner_id = _login_raw(
        base_url=base_url,
        email=email,
        password=password,
        timeout_seconds=timeout_seconds,
    )
    return _store_session(key, token=token, owner_id=owner_id, stale_token=stale_token)


def _with_relogin(
    *,
    base_url: str,
    email: str,
    password: str,
    token: str,
    timeout_seconds: float,
    call: Callable[[str], _T],
) -> tuple[_T, str]:
    try:
        return call(token), token
    except _SessionExpired:
        token = _rellogin_token(
            base_url=base_url,
            email=email,
            password=password,
            stale_token=token,
            timeout_seconds=timeout_seconds,
        )
        return call(token), token


def _post_trackings(
    *,
    url: str,
    bearer_token: str,
    tracking_numbers: list[str],
    timeout_seconds: float,
) -> tuple[int, dict[str, Any]]:
    body = {"trackingNumbers": tracking_numbers}
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers=_browser_headers(content_json=True, bearer=bearer_token),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            status, _raw_text, raw = _read_json_response(resp)
            return status, raw
    except urllib.error.HTTPError as e:
        err_body, raw = _parse_http_error(e)
        if e.code == 401:
            raise _SessionExpired(f"Deal & Runner session expired (HTTP {e.code})") from e
        # Return structured 400 payloads to the caller for duplicate / per-TN handling.
        if e.code == 400 and raw:
            return e.code, raw
        detail = _first_error_description(raw) if raw else ""
        if not detail:
            detail = err_body.strip()[:400] or (str(e.reason).strip() if e.reason else "") or "empty response"
        raise RuntimeError(f"Deal & Runner API error {e.code} at {url}: {detail}") from e
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e)).strip() or e.__class__.__name__
        raise RuntimeError(f"Deal & Runner API request failed ({url}): {reason}") from e


def _get_json(
    *,
    url: str,
    bearer_token: str,
    timeout_seconds: float,
) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(
        url,
        method="GET",
        headers=_browser_headers(bearer=bearer_token),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            status, _raw_text, raw = _read_json_response(resp)
            return status, raw
    except urllib.error.HTTPError as e:
        err_body, raw = _parse_http_error(e)
        if e.code == 401:
            raise _SessionExpired(f"Deal & Runner session expired (HTTP {e.code})") from e
        detail = _first_error_description(raw) if raw else ""
        if not detail:
            detail = err_body.strip()[:400] or (str(e.reason).strip() if e.reason else "") or "empty response"
        raise RuntimeError(f"Deal & Runner API error {e.code} at {url}: {detail}") from e
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e)).strip() or e.__class__.__name__
        raise RuntimeError(f"Deal & Runner API request failed ({url}): {reason}") from e


def _items_from_tracking_list(raw: dict[str, Any]) -> list[dict[str, Any]]:
    result = raw.get("result")
    if isinstance(result, dict):
        items = result.get("items")
        if isinstance(items, list):
            return [i for i in items if isinstance(i, dict)]
        if isinstance(result.get("id"), (str, int)):
            return [result]
    items = raw.get("items")
    if isinstance(items, list):
        return [i for i in items if isinstance(i, dict)]
    return []


def _parse_tracking_row(row: dict[str, Any]) -> DealandrunnerTrackingRow | None:
    tid = row.get("id")
    if tid is None or not str(tid).strip():
        return None
    tns_raw = row.get("trackingNumbers")
    numbers: list[str] = []
    if isinstance(tns_raw, list):
        for val in tns_raw:
            if val is not None and str(val).strip():
                numbers.append(str(val).strip())
    elif isinstance(tns_raw, str) and tns_raw.strip():
        numbers.append(tns_raw.strip())
    single = row.get("trackingNumber") or row.get("tracking") or row.get("number")
    if isinstance(single, str) and single.strip():
        numbers.append(single.strip())
    if not numbers:
        return None
    return DealandrunnerTrackingRow(tracking_id=str(tid).strip(), tracking_numbers=numbers)


def find_tracking(
    *,
    base_url: str | None,
    bearer_token: str,
    tracking_number: str,
    timeout_seconds: float = 30.0,
    max_pages: int = 5,
    page_size: int = 50,
) -> DealandrunnerTrackingRow | None:
    """Look up a tracking row by TN via ``GET /tracking`` (newest first)."""
    tn = (tracking_number or "").strip()
    if not tn:
        return None
    tn_key = tn.casefold()
    base = resolve_base_url(base_url)
    for page in range(1, max_pages + 1):
        qs = urllib.parse.urlencode(
            {
                "pageNumber": str(page),
                "pageSize": str(page_size),
                "orderBy": "dateCreated|desc",
            }
        )
        url = f"{base}{TRACKING_PATH}?{qs}"
        status, raw = _get_json(url=url, bearer_token=bearer_token, timeout_seconds=timeout_seconds)
        if status != 200:
            detail = _first_error_description(raw) or f"HTTP {status}"
            raise RuntimeError(f"Deal & Runner list trackings failed: {detail}")
        items = _items_from_tracking_list(raw)
        if not items:
            break
        for row in items:
            parsed = _parse_tracking_row(row)
            if not parsed:
                continue
            for candidate in parsed.tracking_numbers:
                if candidate.casefold() == tn_key:
                    return parsed
        if len(items) < page_size:
            break
    return None


def _classify_single_result(
    *,
    tracking: str,
    status: int,
    raw: dict[str, Any],
) -> str:
    """Return ``success``, ``duplicate``, or ``failed`` for one tracking number."""
    if status == 200:
        result_type = raw.get("resultType")
        if isinstance(result_type, int) and result_type == 1:
            return "success"
        if _envelope_message_items(raw):
            for msg in _envelope_message_items(raw):
                code = msg.get("statusCode")
                if isinstance(code, str) and code.strip() == CREATE_SUCCESS_CODE:
                    return "success"
            return "success"
        if not _envelope_error_items(raw):
            return "success"
    if _is_duplicate_payload(raw):
        return "duplicate"
    # Some error descriptions are the TN itself (invalid format).
    return "failed"


def _submit_numbers(
    *,
    url: str,
    base_url: str,
    email: str,
    password: str,
    token: str,
    numbers: list[str],
    timeout_seconds: float,
) -> tuple[int, dict[str, Any], str]:
    def _do(tok: str) -> tuple[int, dict[str, Any]]:
        return _post_trackings(
            url=url,
            bearer_token=tok,
            tracking_numbers=numbers,
            timeout_seconds=timeout_seconds,
        )

    (status, raw), token = _with_relogin(
        base_url=base_url,
        email=email,
        password=password,
        token=token,
        timeout_seconds=timeout_seconds,
        call=_do,
    )
    return status, raw, token


def submit_trackings(
    *,
    base_url: str | None,
    email: str,
    password: str,
    trackings: list[DealandrunnerTrackingEntry],
    timeout_seconds: float = 30.0,
) -> DealandrunnerSubmitResult:
    if not trackings:
        raise ValueError("At least one tracking entry is required")

    seen: set[str] = set()
    numbers: list[str] = []
    for entry in trackings:
        tn = (entry.tracking or "").strip()
        if not tn:
            raise ValueError("tracking is required")
        key = tn.casefold()
        if key in seen:
            continue
        seen.add(key)
        numbers.append(tn)

    base = resolve_base_url(base_url)
    url = f"{base}{TRACKING_PATH}"
    token = _ensure_token(
        base_url=base,
        email=email,
        password=password,
        timeout_seconds=timeout_seconds,
    )

    status, raw, token = _submit_numbers(
        url=url,
        base_url=base,
        email=email,
        password=password,
        token=token,
        numbers=numbers,
        timeout_seconds=timeout_seconds,
    )

    succeeded: list[str] = []
    duplicates: list[str] = []
    failed: list[str] = []
    fail_details: list[str] = []

    if status == 200 and isinstance(raw.get("resultType"), int) and raw.get("resultType") == 1:
        succeeded = list(numbers)
    elif status == 200 and not _envelope_error_items(raw):
        succeeded = list(numbers)
    elif len(numbers) == 1:
        kind = _classify_single_result(tracking=numbers[0], status=status, raw=raw)
        if kind == "success":
            succeeded = list(numbers)
        elif kind == "duplicate":
            duplicates = list(numbers)
        else:
            failed = list(numbers)
            detail = _first_error_description(raw) or f"HTTP {status}"
            fail_details.append(f"{numbers[0]}: {detail}")
    else:
        # Batch failed (or ambiguous): fall back to per-TN so one bad TN does not block others.
        for tn in numbers:
            st, one_raw, token = _submit_numbers(
                url=url,
                base_url=base,
                email=email,
                password=password,
                token=token,
                numbers=[tn],
                timeout_seconds=timeout_seconds,
            )
            kind = _classify_single_result(tracking=tn, status=st, raw=one_raw)
            if kind == "success":
                succeeded.append(tn)
            elif kind == "duplicate":
                duplicates.append(tn)
            else:
                failed.append(tn)
                detail = _first_error_description(one_raw) or f"HTTP {st}"
                fail_details.append(f"{tn}: {detail}")

    ok_count = len(succeeded) + len(duplicates)
    if ok_count == 0:
        detail = "; ".join(fail_details[:5]) if fail_details else _first_error_description(raw) or "submission failed"
        raise RuntimeError(f"Deal & Runner tracking submit failed: {detail}")

    parts: list[str] = []
    if succeeded:
        parts.append(f"Submitted {len(succeeded)} tracking number(s).")
    if duplicates:
        parts.append(f"{len(duplicates)} already present (duplicate).")
    if failed:
        parts.append(f"{len(failed)} failed: {'; '.join(fail_details[:3])}")
    message = " ".join(parts) if parts else "Tracking numbers submitted successfully."

    submission_ids: dict[str, str] = {}
    for tn in succeeded + duplicates:
        try:

            def _lookup(tok: str, *, _tn: str = tn) -> DealandrunnerTrackingRow | None:
                return find_tracking(
                    base_url=base,
                    bearer_token=tok,
                    tracking_number=_tn,
                    timeout_seconds=timeout_seconds,
                )

            row: DealandrunnerTrackingRow | None = None
            for attempt in range(4):
                row, token = _with_relogin(
                    base_url=base,
                    email=email,
                    password=password,
                    token=token,
                    timeout_seconds=timeout_seconds,
                    call=_lookup,
                )
                if row is not None:
                    break
                if attempt < 3:
                    time.sleep(0.75)
            if row is not None:
                submission_ids[tn] = row.tracking_id
        except Exception:
            # ID lookup must not fail the submit.
            continue

    return DealandrunnerSubmitResult(
        success=True,
        message=message,
        affected=ok_count,
        raw=raw,
        succeeded=succeeded,
        duplicates=duplicates,
        failed=failed,
        submission_ids=submission_ids,
    )


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def _tracking_numbers_from_tx(row: dict[str, Any]) -> list[str]:
    numbers: list[str] = []
    seen: set[str] = set()

    def _add(raw: Any) -> None:
        if raw is None:
            return
        text = str(raw).strip()
        if not text:
            return
        key = text.casefold()
        if key in seen:
            return
        seen.add(key)
        numbers.append(text)

    tns_raw = row.get("trackingNumbers")
    if isinstance(tns_raw, list):
        for val in tns_raw:
            _add(val)
    elif isinstance(tns_raw, str):
        _add(tns_raw)
    for key in ("trackingNumber", "tracking", "number"):
        _add(row.get(key))
    return numbers


def _parse_received_row(row: dict[str, Any]) -> DealandrunnerReceivedRow | None:
    tracking_numbers = _tracking_numbers_from_tx(row)
    if not tracking_numbers:
        return None

    system_tx = row.get("systemTransactionId")
    system_transaction_id = (
        str(system_tx).strip()
        if system_tx is not None and str(system_tx).strip()
        else None
    )
    # Ignore the always-zero ``id`` field; warehouse item id is a fallback receipt key.
    if not system_transaction_id:
        wh = row.get("referenceWarehouseItemId")
        if wh is not None and str(wh).strip():
            system_transaction_id = str(wh).strip()

    product = row.get("productName")
    product_name = str(product).strip() if product is not None and str(product).strip() else None

    ts = row.get("timestamp")
    timestamp = str(ts).strip() if ts is not None and str(ts).strip() else None

    owner = row.get("ownerId") or row.get("referenceRunnerId")
    owner_id = str(owner).strip() if owner is not None and str(owner).strip() else None

    wh_item = row.get("referenceWarehouseItemId")
    warehouse_item_id = (
        str(wh_item).strip() if wh_item is not None and str(wh_item).strip() else None
    )

    entry_raw = row.get("entryTypeId")
    entry_type_id: int | None
    try:
        entry_type_id = int(entry_raw) if entry_raw is not None else None
    except (TypeError, ValueError):
        entry_type_id = None

    return DealandrunnerReceivedRow(
        tracking_numbers=tracking_numbers,
        amount=_parse_decimal(row.get("amount")),
        product_name=product_name,
        system_transaction_id=system_transaction_id,
        timestamp=timestamp,
        owner_id=owner_id,
        reference_warehouse_item_id=warehouse_item_id,
        entry_type_id=entry_type_id,
        raw=row,
    )


def _pagination_meta(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], bool, int, int]:
    """Return (items, has_next_page, page_count, total_records)."""
    result = raw.get("result")
    items: list[dict[str, Any]] = []
    has_next = False
    page_count = 0
    total_records = 0
    if isinstance(result, dict):
        raw_items = result.get("items")
        if isinstance(raw_items, list):
            items = [i for i in raw_items if isinstance(i, dict)]
        has_next = bool(result.get("hasNextPage"))
        try:
            page_count = int(result.get("pageCount") or 0)
        except (TypeError, ValueError):
            page_count = 0
        try:
            total_records = int(result.get("totalRecords") or 0)
        except (TypeError, ValueError):
            total_records = 0
    return items, has_next, page_count, total_records


# Receiving / warehouse-credit ledger entries (payments and other types differ).
RECEIVING_ENTRY_TYPE_ID = 1


def list_runner_transactions(
    *,
    base_url: str | None,
    email: str,
    password: str,
    owner_id: str | None = None,
    page_size: int = _DEFAULT_TX_PAGE_SIZE,
    timeout_seconds: float = 30.0,
) -> DealandrunnerRunnerTransactionsResult:
    """Paginate runner balance transactions for receiving checks.

    Keeps only ``entryTypeId == 1`` rows that have tracking numbers (payments and
    other ledger types are excluded). Each kept item is typically one warehouse
    unit (qty 1), not a grouped quantity.
    """
    if not (email or "").strip():
        raise ValueError("email is required")
    if not (password or "").strip():
        raise ValueError("password is required")

    base = resolve_base_url(base_url)
    key = _cache_key(base, email, password)
    token = _ensure_token(
        base_url=base,
        email=email,
        password=password,
        timeout_seconds=timeout_seconds,
    )
    resolved_owner = (owner_id or "").strip() or _cached_owner_id(key)
    size = max(1, min(int(page_size or _DEFAULT_TX_PAGE_SIZE), 200))

    all_rows: list[DealandrunnerReceivedRow] = []
    request_urls: list[str] = []
    page_count = 0
    total_records = 0
    page_number = 1

    while page_number <= _MAX_TX_PAGES:
        query: dict[str, str] = {
            "pageNumber": str(page_number),
            "pageSize": str(size),
            "orderBy": "timestamp|desc",
        }
        if resolved_owner:
            query["ownerIds"] = resolved_owner
        url = f"{base}{RUNNER_TRANSACTIONS_PATH}?{urllib.parse.urlencode(query)}"
        request_urls.append(url)

        def _fetch(tok: str, *, _url: str = url) -> tuple[int, dict[str, Any]]:
            return _get_json(url=_url, bearer_token=tok, timeout_seconds=timeout_seconds)

        (status, raw), token = _with_relogin(
            base_url=base,
            email=email,
            password=password,
            token=token,
            timeout_seconds=timeout_seconds,
            call=_fetch,
        )
        if status != 200:
            detail = _first_error_description(raw) or f"HTTP {status}"
            raise RuntimeError(f"Deal & Runner runner-transactions failed: {detail}")
        errors = _envelope_error_items(raw)
        if errors:
            detail = _first_error_description(raw) or "runner-transactions failed"
            raise RuntimeError(f"Deal & Runner runner-transactions failed: {detail}")

        items, has_next, page_count, total_records = _pagination_meta(raw)

        # Discover ownerId from the first page when login did not provide one.
        if not resolved_owner:
            for item in items:
                cand = _owner_id_from_payload({"result": item}) or (
                    str(item.get("ownerId") or item.get("referenceRunnerId") or "").strip() or None
                )
                if cand:
                    resolved_owner = cand
                    with _token_lock:
                        _owner_cache[key] = cand
                    break

        for item in items:
            parsed = _parse_received_row(item)
            if parsed is None:
                continue
            if parsed.entry_type_id != RECEIVING_ENTRY_TYPE_ID:
                continue
            all_rows.append(parsed)

        if not items or not has_next:
            break
        if page_count > 0 and page_number >= page_count:
            break
        page_number += 1

    return DealandrunnerRunnerTransactionsResult(
        rows=all_rows,
        page_count=page_count,
        total_records=total_records,
        request_urls=request_urls,
        owner_id=resolved_owner,
    )
