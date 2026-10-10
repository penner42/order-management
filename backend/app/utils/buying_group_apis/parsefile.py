"""Parsefile order-management API (tracking submission)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


ADD_TRACKING_CMD = "/cmd/addtracking"
DEFAULT_BASE_URL = "https://www.powerbuynetwork.com"


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


def resolve_base_url(base_url: str | None) -> str:
    """Use the buying-group base URL, or the Parsefile framework default when blank."""
    base = (base_url or "").strip().rstrip("/")
    return base or DEFAULT_BASE_URL.rstrip("/")


def build_url(base_url: str | None, api_url: str) -> str:
    """Join base_url + api_url + command path for addtracking."""
    base = resolve_base_url(base_url)
    api = (api_url or "").strip()
    if not api:
        raise ValueError("api_url is required")
    if not api.startswith("/"):
        api = "/" + api
    api = api.rstrip("/")
    return f"{base}{api}{ADD_TRACKING_CMD}"


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
    base_url: str | None,
    api_url: str,
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
