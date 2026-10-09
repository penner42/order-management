"""Parsefile order-management API (tracking submission)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin


DEFAULT_ADD_TRACKING_PATH = "/p/it@api@order-management/cmd/addtracking"


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


def build_url(base_url: str, api_url: str | None) -> str:
    base = base_url.strip().rstrip("/") + "/"
    path = (api_url or DEFAULT_ADD_TRACKING_PATH).strip() or DEFAULT_ADD_TRACKING_PATH
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return urljoin(base, path.lstrip("/"))


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
        detail = err_body.strip() or e.reason
        raise RuntimeError(f"Parsefile API error {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Parsefile API request failed: {e.reason}") from e

    try:
        raw = json.loads(raw_text) if raw_text else {}
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Parsefile API returned non-JSON (HTTP {status}): {raw_text[:300]}") from e

    if not isinstance(raw, dict):
        raise RuntimeError(f"Parsefile API returned unexpected payload: {raw_text[:300]}")

    success = bool(raw.get("success"))
    message = str(raw.get("response") or raw.get("message") or ("OK" if success else "Submission failed"))
    affected_raw = raw.get("affected")
    affected = int(affected_raw) if isinstance(affected_raw, (int, float)) else None
    if not success:
        raise RuntimeError(message)
    return ParsefileSubmitResult(success=success, affected=affected, message=message, raw=raw)
