"""USABG (api.usabuying.group) tracking submission client."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, TypeVar
from urllib.parse import urlencode


DEFAULT_BASE_URL = "https://api.usabuying.group/buyers"
TRACKINGS_PATH = "/trackings"
NOTES_PATH = "/notes"
LOGIN_PATH = "/login"

_T = TypeVar("_T")


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
    """Run ``call(token)``; on session expiry, login once and retry. Returns (result, token)."""
    try:
        return call(token), token
    except _SessionExpired:
        token = login(
            base_url=base_url,
            username=username,
            password=password,
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
    token = login(
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
    for entry in trackings:
        note = (entry.note or "").strip()
        if not note:
            continue
        tn = (entry.tracking or "").strip()
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
                notes_errors.append(f"{tn}: tracking not found after submit")
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
    )
