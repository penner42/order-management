"""USABG (api.usabuying.group) tracking submission client."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


DEFAULT_BASE_URL = "https://api.usabuying.group/buyers"
TRACKINGS_PATH = "/trackings"
LOGIN_PATH = "/login"


@dataclass
class UsabgTrackingEntry:
    tracking: str
    amount: Decimal | float | None = None


@dataclass
class UsabgSubmitResult:
    success: bool
    message: str
    affected: int | None
    raw: dict[str, Any]


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

    try:
        status, raw = _post_trackings(
            url=url,
            bearer_token=token,
            trackings_text=trackings_text,
            timeout_seconds=timeout_seconds,
        )
    except _SessionExpired:
        token = login(
            base_url=base,
            username=username,
            password=password,
            timeout_seconds=timeout_seconds,
        )
        status, raw = _post_trackings(
            url=url,
            bearer_token=token,
            trackings_text=trackings_text,
            timeout_seconds=timeout_seconds,
        )

    if _is_session_expired(status, raw, ""):
        # Unexpected: HTTP 200 with expiry message — treat as failure.
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

    # Fail hard when the API only reported errors and no successes.
    if errors_text and not success_text:
        raise RuntimeError(errors_text or message)

    affected = len(trackings) if success_text or not errors_text else None
    return UsabgSubmitResult(
        success=True,
        message=message,
        affected=affected,
        raw=raw,
    )
