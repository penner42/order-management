"""USABG (api.usabuying.group) tracking submission + PO receiving client."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, TypeVar
from urllib.parse import urlencode


DEFAULT_BASE_URL = "https://api.usabuying.group/buyers"
TRACKINGS_PATH = "/trackings"
NOTES_PATH = "/notes"
LOGIN_PATH = "/login"
POS_PATH = "/pos"

_DEFAULT_POS_PAGE_SIZE = 50
_MAX_POS_PAGES = 100

_T = TypeVar("_T")

# Process-local bearer tokens keyed by (base_url, username, password hash).
# Reused across submits; cleared and replaced only on session expiry / re-login.
_token_cache: dict[tuple[str, str, str], str] = {}
_token_lock = threading.Lock()


def _cache_key(base_url: str, username: str, password: str) -> tuple[str, str, str]:
    pw_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return (resolve_base_url(base_url), username.strip().casefold(), pw_hash)


def _ensure_token(
    *,
    base_url: str,
    username: str,
    password: str,
    timeout_seconds: float,
) -> str:
    """Return a cached bearer token, logging in only when none is cached."""
    key = _cache_key(base_url, username, password)
    with _token_lock:
        cached = _token_cache.get(key)
        if cached:
            return cached
    token = login(
        base_url=base_url,
        username=username,
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
    username: str,
    password: str,
    stale_token: str,
    timeout_seconds: float,
) -> str:
    """Replace a failed token. Prefer a fresher cache entry from a concurrent re-login."""
    key = _cache_key(base_url, username, password)
    with _token_lock:
        cached = _token_cache.get(key)
        if cached and cached != stale_token:
            return cached
        _token_cache.pop(key, None)
    token = login(
        base_url=base_url,
        username=username,
        password=password,
        timeout_seconds=timeout_seconds,
    )
    with _token_lock:
        cached = _token_cache.get(key)
        if cached and cached != stale_token:
            return cached
        _token_cache[key] = token
        return token


@dataclass
class UsabgTrackingEntry:
    tracking: str
    amount: Decimal | float | None = None
    note: str | None = None


@dataclass
class UsabgTrackingRow:
    tracking_id: str
    tracking_number: str
    note_id: str | None = None


@dataclass
class UsabgSubmitResult:
    success: bool
    message: str
    affected: int | None
    raw: dict[str, Any]
    notes_updated: int = 0
    notes_errors: list[str] = field(default_factory=list)
    # tracking number -> USABG tracking_id
    submission_ids: dict[str, str] = field(default_factory=dict)


@dataclass
class UsabgPoSummary:
    po_number: str
    purchase_id: str | None
    amount: Decimal | None
    box_count: int | None
    date: str | None
    receiving_status: str | None
    status: str | None
    raw: dict[str, Any] = field(default_factory=dict)

    def is_warehouse_received(self) -> bool:
        status = (self.receiving_status or "").strip().casefold()
        return status in ("received", "complete", "completed")


@dataclass
class UsabgReceivedRow:
    """One warehouse-received line from a PO box (keyed by tracking + PO number)."""

    po_number: str
    purchase_id: str | None
    tracking_number: str
    quantity: int
    unit_price: Decimal | None
    ext_total: Decimal | None
    received_status: str | None
    receiving_status: str | None
    scanned_at: str | None
    received_item: str | None
    sku: str | None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def receipt_id(self) -> str:
        """Warehouse receipt id for local storage — USABG PO number."""
        return self.po_number


@dataclass
class UsabgReceivedListResult:
    rows: list[UsabgReceivedRow]
    po_count: int
    received_po_count: int
    request_urls: list[str] = field(default_factory=list)
    detail_cache_hits: int = 0
    detail_cache_misses: int = 0
    # po_number -> detail payload for rows that were fetched from the API this call
    newly_fetched_details: dict[str, dict[str, Any]] = field(default_factory=dict)


def resolve_base_url(base_url: str | None) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        base = DEFAULT_BASE_URL
    return base.rstrip("/")


def format_entry(tracking: str, amount: Decimal | float | None = None) -> str:
    """Build one USABG paste token: tracking[$amount]."""
    tn = (tracking or "").strip()
    if not tn:
        raise ValueError("tracking is required")
    if amount is None:
        return tn
    # Match app paste format: no thousands separators; drop trailing zeros.
    dec = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    text = format(dec.quantize(Decimal("0.01")), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{tn}${text}"


def _message_from_payload(raw: dict[str, Any], *, success: bool) -> str:
    added = raw.get("tracking_numbers_added")
    if isinstance(added, dict):
        messages = added.get("messages")
        if isinstance(messages, dict):
            parts: list[str] = []
            for key in ("success", "errors"):
                value = messages.get(key)
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
            if parts:
                return "\n".join(parts)
    insurance = raw.get("tracking_numbers_insurance_added")
    if isinstance(insurance, dict):
        parts = []
        for key in ("success", "errors"):
            value = insurance.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        if parts:
            return "\n".join(parts)
    for key in ("message", "error", "detail", "response"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if success:
        return "Tracking numbers submitted successfully."
    try:
        return f"Submission failed: {json.dumps(raw)[:400]}"
    except Exception:
        return "Submission failed"


def _is_session_expired(status: int, raw: dict[str, Any] | None, body_text: str) -> bool:
    if status == 401:
        return True
    msg = ""
    if isinstance(raw, dict):
        value = raw.get("message")
        if isinstance(value, str):
            msg = value.strip().casefold()
    if not msg and body_text:
        msg = body_text.strip().casefold()[:200]
    if not msg:
        return False
    if msg != "authenticated_user" and "session" in msg and "timeout" in msg:
        return True
    if "session_timeout" in msg:
        return True
    if "token" in msg and ("expired" in msg or "invalid" in msg):
        return True
    return False


def _read_json_response(resp) -> tuple[int, str, dict[str, Any]]:
    raw_text = resp.read().decode("utf-8", errors="replace")
    status = getattr(resp, "status", 200)
    try:
        raw = json.loads(raw_text) if raw_text else {}
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"USABG API returned non-JSON (HTTP {status}): {raw_text[:300]}"
        ) from e
    if not isinstance(raw, dict):
        raise RuntimeError(f"USABG API returned unexpected payload: {raw_text[:300]}")
    return status, raw_text, raw


def login(
    *,
    base_url: str | None,
    username: str,
    password: str,
    timeout_seconds: float = 30.0,
) -> str:
    if not (username or "").strip():
        raise ValueError("username is required")
    if not (password or "").strip():
        raise ValueError("password is required")

    base = resolve_base_url(base_url)
    url = f"{base}{LOGIN_PATH}"
    body = {
        "credentials": username.strip(),
        "password": password,
        "enroll": False,
        "remember_me": True,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            status, raw_text, raw = _read_json_response(resp)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        detail = err_body.strip()[:400] or (str(e.reason).strip() if e.reason else "") or "empty response"
        try:
            err_json = json.loads(err_body) if err_body.strip() else {}
            if isinstance(err_json, dict):
                msg = err_json.get("message")
                if isinstance(msg, str) and msg.strip():
                    detail = msg.strip()
        except json.JSONDecodeError:
            pass
        raise RuntimeError(f"USABG login failed (HTTP {e.code}) at {url}: {detail}") from e
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e)).strip() or e.__class__.__name__
        raise RuntimeError(f"USABG login request failed ({url}): {reason}") from e

    envelope_status = raw.get("status")
    if status != 200 or (isinstance(envelope_status, int) and envelope_status not in (0, 200)):
        msg = raw.get("message")
        detail = msg.strip() if isinstance(msg, str) and msg.strip() else raw_text[:400]
        raise RuntimeError(f"USABG login failed (status {envelope_status or status}): {detail}")

    data_obj = raw.get("data")
    token = None
    if isinstance(data_obj, dict):
        token_val = data_obj.get("token")
        if isinstance(token_val, str) and token_val.strip():
            token = token_val.strip()
    if not token:
        raise RuntimeError("USABG login succeeded but no token returned")
    return token


def _post_trackings(
    *,
    url: str,
    bearer_token: str,
    trackings_text: str,
    timeout_seconds: float,
) -> tuple[int, dict[str, Any]]:
    body = {"trackings": trackings_text}
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
            "from-bubble": "1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            status, _raw_text, raw = _read_json_response(resp)
            return status, raw
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raw: dict[str, Any] = {}
        if err_body.strip():
            try:
                parsed = json.loads(err_body)
                if isinstance(parsed, dict):
                    raw = parsed
            except json.JSONDecodeError:
                pass
        if _is_session_expired(e.code, raw or None, err_body):
            raise _SessionExpired(f"USABG session expired (HTTP {e.code})") from e
        detail = ""
        if raw:
            detail = _message_from_payload(raw, success=False)
        if not detail:
            detail = err_body.strip()[:400] or (str(e.reason).strip() if e.reason else "") or "empty response"
        raise RuntimeError(f"USABG API error {e.code} at {url}: {detail}") from e
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e)).strip() or e.__class__.__name__
        raise RuntimeError(f"USABG API request failed ({url}): {reason}") from e


class _SessionExpired(RuntimeError):
    pass


def _authorized_json(
    *,
    method: str,
    url: str,
    bearer_token: str,
    body: dict[str, Any] | None = None,
    timeout_seconds: float = 30.0,
) -> tuple[int, dict[str, Any] | list[Any] | None, str]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {bearer_token}",
        "from-bubble": "1",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw_text = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raw: dict[str, Any] | None = None
        if err_body.strip():
            try:
                parsed = json.loads(err_body)
                if isinstance(parsed, dict):
                    raw = parsed
            except json.JSONDecodeError:
                pass
        if _is_session_expired(e.code, raw, err_body):
            raise _SessionExpired(f"USABG session expired (HTTP {e.code})") from e
        detail = ""
        if isinstance(raw, dict):
            detail = _message_from_payload(raw, success=False)
        if not detail:
            detail = err_body.strip()[:400] or (str(e.reason).strip() if e.reason else "") or "empty response"
        raise RuntimeError(f"USABG API error {e.code} at {url}: {detail}") from e
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e)).strip() or e.__class__.__name__
        raise RuntimeError(f"USABG API request failed ({url}): {reason}") from e

    if not raw_text.strip():
        return status, None, raw_text
    try:
        parsed_body = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"USABG API returned non-JSON (HTTP {status}): {raw_text[:300]}") from e
    return status, parsed_body, raw_text


def _with_relogin(
    *,
    base_url: str,
    username: str,
    password: str,
    token: str,
    timeout_seconds: float,
    call: Callable[[str], _T],
) -> tuple[_T, str]:
    """Run ``call(token)``; on session expiry, re-login once, cache, and retry."""
    try:
        return call(token), token
    except _SessionExpired:
        token = _rellogin_token(
            base_url=base_url,
            username=username,
            password=password,
            stale_token=token,
            timeout_seconds=timeout_seconds,
        )
        return call(token), token


def _rows_from_trackings_payload(payload: dict[str, Any] | list[Any] | None) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        nested = data.get("data")
        if isinstance(nested, list):
            return [r for r in nested if isinstance(r, dict)]
        # Sometimes the list is under another key.
        for key in ("trackings", "items", "results"):
            val = data.get(key)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
    for key in ("trackings", "items", "results"):
        val = payload.get(key)
        if isinstance(val, list):
            return [r for r in val if isinstance(r, dict)]
    return []


def _parse_tracking_row(row: dict[str, Any]) -> UsabgTrackingRow | None:
    # WeWeb/Bubble shortened keys: a=id, b=tracking number, w=note id.
    tracking_id = row.get("a") if row.get("a") is not None else row.get("id")
    tracking_number = row.get("b") if row.get("b") is not None else row.get("tracking_number")
    note_id = row.get("w") if row.get("w") is not None else row.get("note_id")
    if tracking_id is None or tracking_number is None:
        return None
    tn = str(tracking_number).strip()
    tid = str(tracking_id).strip()
    if not tn or not tid:
        return None
    nid = None
    if note_id is not None and str(note_id).strip() and str(note_id).strip().casefold() not in ("null", "none", "0", "false"):
        nid = str(note_id).strip()
    return UsabgTrackingRow(tracking_id=tid, tracking_number=tn, note_id=nid)


def find_tracking(
    *,
    base_url: str,
    bearer_token: str,
    tracking_number: str,
    timeout_seconds: float = 30.0,
) -> UsabgTrackingRow | None:
    """Look up a buyer tracking row by tracking number (GET /trackings?tracking_number=…)."""
    tn = (tracking_number or "").strip()
    if not tn:
        return None

    qs = urlencode({"tracking_number": tn, "limit": 20, "start": 0})
    url = f"{base_url}{TRACKINGS_PATH}?{qs}"
    _status, payload, _text = _authorized_json(
        method="GET",
        url=url,
        bearer_token=bearer_token,
        body=None,
        timeout_seconds=timeout_seconds,
    )
    rows = _rows_from_trackings_payload(payload)
    tn_key = tn.casefold()
    for row in rows:
        parsed = _parse_tracking_row(row)
        if parsed and parsed.tracking_number.casefold() == tn_key:
            return parsed
    # Fallback: first row if the API filtered exactly.
    if len(rows) == 1:
        return _parse_tracking_row(rows[0])
    return None


def upsert_tracking_note(
    *,
    base_url: str,
    bearer_token: str,
    tracking_id: str,
    note_id: str | None,
    comment: str,
    timeout_seconds: float = 30.0,
) -> None:
    """Create or update the Note column for a tracking (POST /notes or PUT /notes/{id})."""
    comment = (comment or "").strip()
    if not comment:
        raise ValueError("comment is required")
    body = {
        "module": "buyers_portal",
        "model": "trackings",
        "model_id": tracking_id,
        "comment": comment,
    }
    if note_id:
        url = f"{base_url}{NOTES_PATH}/{note_id}"
        method = "PUT"
    else:
        url = f"{base_url}{NOTES_PATH}"
        method = "POST"
    _authorized_json(
        method=method,
        url=url,
        bearer_token=bearer_token,
        body=body,
        timeout_seconds=timeout_seconds,
    )


def submit_trackings(
    *,
    base_url: str | None,
    username: str,
    password: str,
    trackings: list[UsabgTrackingEntry],
    timeout_seconds: float = 30.0,
) -> UsabgSubmitResult:
    if not trackings:
        raise ValueError("At least one tracking entry is required")

    parts: list[str] = []
    for entry in trackings:
        parts.append(format_entry(entry.tracking, entry.amount))
    trackings_text = ",".join(parts)

    base = resolve_base_url(base_url)
    url = f"{base}{TRACKINGS_PATH}"
    token = _ensure_token(
        base_url=base,
        username=username,
        password=password,
        timeout_seconds=timeout_seconds,
    )

    def _do_post(tok: str) -> tuple[int, dict[str, Any]]:
        return _post_trackings(
            url=url,
            bearer_token=tok,
            trackings_text=trackings_text,
            timeout_seconds=timeout_seconds,
        )

    (status, raw), token = _with_relogin(
        base_url=base,
        username=username,
        password=password,
        token=token,
        timeout_seconds=timeout_seconds,
        call=_do_post,
    )

    if _is_session_expired(status, raw, ""):
        raise RuntimeError("USABG session expired after submit")

    message = _message_from_payload(raw, success=True)
    added = raw.get("tracking_numbers_added")
    errors_text = ""
    success_text = ""
    if isinstance(added, dict):
        messages = added.get("messages")
        if isinstance(messages, dict):
            err = messages.get("errors")
            ok = messages.get("success")
            if isinstance(err, str):
                errors_text = err.strip()
            if isinstance(ok, str):
                success_text = ok.strip()

    if errors_text and not success_text:
        raise RuntimeError(errors_text or message)

    affected = len(trackings) if success_text or not errors_text else None

    notes_updated = 0
    notes_errors: list[str] = []
    submission_ids: dict[str, str] = {}
    for entry in trackings:
        tn = (entry.tracking or "").strip()
        if not tn:
            continue
        note = (entry.note or "").strip()
        try:

            def _lookup(tok: str, *, _tn: str = tn) -> UsabgTrackingRow | None:
                return find_tracking(
                    base_url=base,
                    bearer_token=tok,
                    tracking_number=_tn,
                    timeout_seconds=timeout_seconds,
                )

            row: UsabgTrackingRow | None = None
            for attempt in range(3):
                row, token = _with_relogin(
                    base_url=base,
                    username=username,
                    password=password,
                    token=token,
                    timeout_seconds=timeout_seconds,
                    call=_lookup,
                )
                if row is not None:
                    break
                if attempt < 2:
                    time.sleep(0.75)
            if row is None:
                if note:
                    notes_errors.append(f"{tn}: tracking not found after submit")
                continue

            submission_ids[tn] = row.tracking_id
            if not note:
                continue

            def _note(
                tok: str,
                *,
                _row: UsabgTrackingRow = row,
                _comment: str = note,
            ) -> None:
                upsert_tracking_note(
                    base_url=base,
                    bearer_token=tok,
                    tracking_id=_row.tracking_id,
                    note_id=_row.note_id,
                    comment=_comment,
                    timeout_seconds=timeout_seconds,
                )

            _, token = _with_relogin(
                base_url=base,
                username=username,
                password=password,
                token=token,
                timeout_seconds=timeout_seconds,
                call=_note,
            )
            notes_updated += 1
        except Exception as exc:
            notes_errors.append(f"{tn}: {exc}")

    if notes_updated and notes_errors:
        message = f"{message}\nUpdated notes on {notes_updated} tracking(s); {len(notes_errors)} note error(s)."
    elif notes_updated:
        message = f"{message}\nUpdated notes on {notes_updated} tracking(s)."
    elif notes_errors:
        message = f"{message}\nNote update failed: {'; '.join(notes_errors[:3])}"

    return UsabgSubmitResult(
        success=True,
        message=message,
        affected=affected,
        raw=raw,
        notes_updated=notes_updated,
        notes_errors=notes_errors,
        submission_ids=submission_ids,
    )


def _money(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    text = str(value).strip().replace(",", "").replace("$", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _intish(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(Decimal(str(value)))
        except (InvalidOperation, ValueError, TypeError):
            return None


def _strish(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.casefold() in ("null", "none", "undefined"):
        return None
    return text


def _envelope_ok(status: int, payload: dict[str, Any] | list[Any] | None) -> bool:
    if status != 200:
        return False
    if not isinstance(payload, dict):
        return True
    envelope = payload.get("status")
    if isinstance(envelope, int) and envelope not in (0, 200):
        return False
    return True


def _authorized_get_json(
    *,
    url: str,
    bearer_token: str,
    timeout_seconds: float,
) -> tuple[int, dict[str, Any] | list[Any] | None, str]:
    return _authorized_json(
        method="GET",
        url=url,
        bearer_token=bearer_token,
        body=None,
        timeout_seconds=timeout_seconds,
    )


def _parse_po_summary(row: dict[str, Any]) -> UsabgPoSummary | None:
    po_raw = row.get("po_number")
    if po_raw is None:
        po_raw = row.get("po")
    po_number = _strish(po_raw)
    if not po_number:
        return None
    return UsabgPoSummary(
        po_number=po_number,
        purchase_id=_strish(row.get("purchase_id")),
        amount=_money(row.get("amount")),
        box_count=_intish(row.get("box_count")),
        date=_strish(row.get("date")),
        receiving_status=_strish(row.get("receiving_status")),
        status=_strish(row.get("status")),
        raw=row,
    )


def _pos_list_from_payload(payload: dict[str, Any] | list[Any] | None) -> tuple[list[dict[str, Any]], int | None]:
    """Return (row dicts, total items if known)."""
    if payload is None:
        return [], None
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)], None
    if not isinstance(payload, dict):
        return [], None

    total: int | None = None
    totals = payload.get("totals")
    if isinstance(totals, dict):
        total = _intish(totals.get("items"))

    data = payload.get("data")
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)], total
    if isinstance(data, dict):
        for key in ("data", "pos", "purchase_orders", "items", "results"):
            val = data.get(key)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)], total
    for key in ("pos", "purchase_orders", "items", "results"):
        val = payload.get(key)
        if isinstance(val, list):
            return [r for r in val if isinstance(r, dict)], total
    return [], total


def list_purchase_orders(
    *,
    base_url: str | None,
    username: str,
    password: str,
    page_size: int = _DEFAULT_POS_PAGE_SIZE,
    timeout_seconds: float = 30.0,
) -> tuple[list[UsabgPoSummary], list[str]]:
    """Paginate GET /pos until all purchase orders are fetched."""
    base = resolve_base_url(base_url)
    token = _ensure_token(
        base_url=base,
        username=username,
        password=password,
        timeout_seconds=timeout_seconds,
    )
    limit = max(1, min(int(page_size or _DEFAULT_POS_PAGE_SIZE), 200))
    start = 0
    all_rows: list[UsabgPoSummary] = []
    request_urls: list[str] = []
    seen_po: set[str] = set()
    total_items: int | None = None

    for _page in range(_MAX_POS_PAGES):
        qs = urlencode({"limit": limit, "start": start})
        url = f"{base}{POS_PATH}?{qs}"
        request_urls.append(url)

        def _get(tok: str, *, _url: str = url) -> tuple[int, dict[str, Any] | list[Any] | None, str]:
            return _authorized_get_json(url=_url, bearer_token=tok, timeout_seconds=timeout_seconds)

        (status, payload, raw_text), token = _with_relogin(
            base_url=base,
            username=username,
            password=password,
            token=token,
            timeout_seconds=timeout_seconds,
            call=_get,
        )
        if not _envelope_ok(status, payload):
            detail = ""
            if isinstance(payload, dict):
                detail = _message_from_payload(payload, success=False)
            if not detail:
                detail = (raw_text or "")[:300] or f"HTTP {status}"
            raise RuntimeError(f"USABG PO list failed at {url}: {detail}")

        rows_raw, page_total = _pos_list_from_payload(payload)
        if page_total is not None:
            total_items = page_total
        if not rows_raw:
            break

        for row in rows_raw:
            summary = _parse_po_summary(row)
            if summary is None:
                continue
            if summary.po_number in seen_po:
                continue
            seen_po.add(summary.po_number)
            all_rows.append(summary)

        start += len(rows_raw)
        if total_items is not None and start >= total_items:
            break
        if len(rows_raw) < limit:
            break

    return all_rows, request_urls


def get_purchase_order(
    *,
    base_url: str | None,
    username: str,
    password: str,
    po_number: str | int,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """GET /pos/{po_number} and return the ``data`` object (or full payload)."""
    po = _strish(po_number)
    if not po:
        raise ValueError("po_number is required")

    base = resolve_base_url(base_url)
    token = _ensure_token(
        base_url=base,
        username=username,
        password=password,
        timeout_seconds=timeout_seconds,
    )
    url = f"{base}{POS_PATH}/{po}"

    def _get(tok: str) -> tuple[int, dict[str, Any] | list[Any] | None, str]:
        return _authorized_get_json(url=url, bearer_token=tok, timeout_seconds=timeout_seconds)

    (status, payload, raw_text), _token = _with_relogin(
        base_url=base,
        username=username,
        password=password,
        token=token,
        timeout_seconds=timeout_seconds,
        call=_get,
    )
    if not _envelope_ok(status, payload):
        detail = ""
        if isinstance(payload, dict):
            detail = _message_from_payload(payload, success=False)
        if not detail:
            detail = (raw_text or "")[:300] or f"HTTP {status}"
        raise RuntimeError(f"USABG PO detail failed at {url}: {detail}")

    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload
    raise RuntimeError(f"USABG PO detail returned unexpected payload at {url}")


def _box_line_qty(item: dict[str, Any]) -> int:
    qty = _intish(item.get("quantity"))
    if qty is not None and qty > 0:
        return qty
    return 1


def _received_rows_from_po_detail(
    *,
    summary: UsabgPoSummary,
    detail: dict[str, Any],
) -> list[UsabgReceivedRow]:
    po_obj = detail.get("po") if isinstance(detail.get("po"), dict) else {}
    po_number = _strish(po_obj.get("po_number")) or summary.po_number
    purchase_id = _strish(po_obj.get("purchase_id")) or summary.purchase_id
    receiving_status = _strish(po_obj.get("receiving_status")) or summary.receiving_status
    po_status = _strish(po_obj.get("status")) or summary.status

    boxes = detail.get("boxes")
    if not isinstance(boxes, list):
        boxes = []

    rows: list[UsabgReceivedRow] = []
    for box in boxes:
        if not isinstance(box, dict):
            continue
        tn = _strish(box.get("tracking_number"))
        if not tn:
            continue
        scanned_at = _strish(box.get("date"))
        box_items = box.get("items")
        if not isinstance(box_items, list) or not box_items:
            # Box with tracking but no line items — count as 1 unit.
            rows.append(
                UsabgReceivedRow(
                    po_number=po_number,
                    purchase_id=purchase_id,
                    tracking_number=tn,
                    quantity=1,
                    unit_price=None,
                    ext_total=_money(box.get("value")),
                    received_status=po_status,
                    receiving_status=receiving_status,
                    scanned_at=scanned_at,
                    received_item=None,
                    sku=None,
                    raw={"po": summary.raw, "box": box},
                )
            )
            continue
        for line in box_items:
            if not isinstance(line, dict):
                continue
            qty = _box_line_qty(line)
            # USABG box lines: ``price`` is unit, ``amount`` is line total.
            unit = _money(line.get("price"))
            ext = _money(line.get("amount"))
            if ext is None and unit is not None and qty > 0:
                ext = (unit * Decimal(qty)).quantize(Decimal("0.01"))
            if unit is None and ext is not None and qty > 0:
                unit = (ext / Decimal(qty)).quantize(Decimal("0.01"))
            name = _strish(line.get("name"))
            sku = _strish(line.get("sku"))
            rows.append(
                UsabgReceivedRow(
                    po_number=po_number,
                    purchase_id=purchase_id,
                    tracking_number=tn,
                    quantity=qty,
                    unit_price=unit,
                    ext_total=ext,
                    received_status=po_status,
                    receiving_status=receiving_status,
                    scanned_at=scanned_at,
                    received_item=name,
                    sku=sku,
                    raw={"po": summary.raw, "box": box, "line": line},
                )
            )
    return rows


def list_received_po_rows(
    *,
    base_url: str | None,
    username: str,
    password: str,
    page_size: int = _DEFAULT_POS_PAGE_SIZE,
    timeout_seconds: float = 30.0,
    cached_details: dict[str, dict[str, Any]] | None = None,
) -> UsabgReceivedListResult:
    """List warehouse-received PO box lines (tracking + PO number as receipt id).

    1. Paginate ``GET /pos``
    2. For each PO with ``receiving_status`` received, use ``cached_details`` when
       present; otherwise ``GET /pos/{po_number}``
    3. Flatten box line items into received rows keyed by tracking number

    ``cached_details`` maps po_number -> detail JSON (same shape as
    ``get_purchase_order``). Newly fetched details are returned in
    ``newly_fetched_details`` so the caller can persist them.
    """
    cache = cached_details or {}
    summaries, request_urls = list_purchase_orders(
        base_url=base_url,
        username=username,
        password=password,
        page_size=page_size,
        timeout_seconds=timeout_seconds,
    )
    rows: list[UsabgReceivedRow] = []
    received_po_count = 0
    cache_hits = 0
    cache_misses = 0
    newly_fetched: dict[str, dict[str, Any]] = {}
    for summary in summaries:
        if not summary.is_warehouse_received():
            continue
        received_po_count += 1
        cached = cache.get(summary.po_number)
        if isinstance(cached, dict):
            cache_hits += 1
            detail = cached
        else:
            cache_misses += 1
            detail_url = f"{resolve_base_url(base_url)}{POS_PATH}/{summary.po_number}"
            request_urls.append(detail_url)
            detail = get_purchase_order(
                base_url=base_url,
                username=username,
                password=password,
                po_number=summary.po_number,
                timeout_seconds=timeout_seconds,
            )
            newly_fetched[summary.po_number] = detail
        rows.extend(_received_rows_from_po_detail(summary=summary, detail=detail))

    return UsabgReceivedListResult(
        rows=rows,
        po_count=len(summaries),
        received_po_count=received_po_count,
        request_urls=request_urls,
        detail_cache_hits=cache_hits,
        detail_cache_misses=cache_misses,
        newly_fetched_details=newly_fetched,
    )
