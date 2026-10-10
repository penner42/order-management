"""Deal & Runner (api.dealandrunner.com) tracking submission client.

Flow (aligned with dealsbot dealandrunner auth):
1. Login via ``POST /account/login`` (email/password → bearer token). Cache token.
2. Create trackings via ``POST /tracking`` with ``{"trackingNumbers":[...]}``.
3. On 401, re-login once and retry. On batch HTTP 400, fall back to per-TN POSTs.
4. Duplicate TN (``4004000035``) counts as success for that number.
"""
from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar


DEFAULT_BASE_URL = "https://api.dealandrunner.com"
LOGIN_PATH = "/account/login"
TRACKING_PATH = "/tracking"

_SITE_ORIGIN = "https://portal.dealandrunner.com"
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

DUPLICATE_TN_CODE = "4004000035"
CREATE_SUCCESS_CODE = "2004000007"

_T = TypeVar("_T")

# Process-local bearer tokens keyed by (base_url, email, password hash).
_token_cache: dict[tuple[str, str, str], str] = {}
_token_lock = threading.Lock()


@dataclass
class DealandrunnerTrackingEntry:
    tracking: str


@dataclass
class DealandrunnerSubmitResult:
    success: bool
    message: str
    affected: int | None
    raw: dict[str, Any] | list[Any] | None = None
    succeeded: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


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


def login(
    *,
    base_url: str | None,
    email: str,
    password: str,
    timeout_seconds: float = 30.0,
) -> str:
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
    return token


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
    token = login(
        base_url=base_url,
        email=email,
        password=password,
        timeout_seconds=timeout_seconds,
    )
    with _token_lock:
        cached = _token_cache.get(key)
        if cached:
            return cached
        _token_cache[key] = token
        return token


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
    token = login(
        base_url=base_url,
        email=email,
        password=password,
        timeout_seconds=timeout_seconds,
    )
    with _token_lock:
        cached = _token_cache.get(key)
        if cached and cached != stale_token:
            return cached
        _token_cache[key] = token
        return token


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

    return DealandrunnerSubmitResult(
        success=True,
        message=message,
        affected=ok_count,
        raw=raw,
        succeeded=succeeded,
        duplicates=duplicates,
        failed=failed,
    )
