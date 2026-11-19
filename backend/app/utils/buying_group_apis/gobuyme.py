"""GoBuyMe buyer API and account-scoped Tracker lookups."""
from __future__ import annotations

import base64
import json
import re
from collections.abc import Sequence
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.submission_policy import require_group_submissions_enabled
from app.utils.product_upc import product_upc

SITE = "https://gobuyme.com"
PROJECT = "uuwytliqkfkpylruxhew"
ORIGIN = f"https://{PROJECT}.supabase.co"
TRACKABLE_STATUSES = {"pending", "confirmed", "in_progress"}
COMMITMENT_COLUMNS = (
    "id,status,tracking_deadline,units_committed,selected_deal_item_id,"
    "selected_deal_item:deal_items!commitments_selected_deal_item_id_fkey(quantity,product:products(upc)),"
    "deal:deals(deal_items!deal_items_deal_id_fkey(role,quantity,product:products(upc))),"
    "shipment_commitments(units_in_shipment)"
)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(url, *, headers=None, body=None):
    request = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json", **(headers or {})},
        data=None if body is None else json.dumps(body).encode(),
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=20) as response:
            return response.read().decode()
    except HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError("GoBuyMe rate limit reached. Try again later.") from None
        raise RuntimeError(f"GoBuyMe request failed (HTTP {exc.code})") from None
    except (URLError, TimeoutError):
        raise RuntimeError("Could not reach GoBuyMe. Try again later.") from None


def _browser_api_key():
    html = _request(SITE + "/auth")
    asset = re.search(r'src="(/assets/index-[\w-]+\.js)"', html)
    if asset:
        bundle = _request(SITE + asset[1])
        # Use only the public client configuration attached to the expected project.
        config = re.search(re.escape(ORIGIN) + r'["\'][^;]{0,250}?["\'](eyJ[\w-]+\.[\w-]+\.[\w-]+|sb_publishable_[\w-]+)["\']', bundle)
        if config:
            key = config[1]
            if key.startswith("sb_publishable_"):
                return key
            segment = key.split(".")[1]
            try:
                claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
            except ValueError:
                claims = {}
            if claims.get("role") == "anon" and claims.get("ref") == PROJECT:
                return key
    raise RuntimeError("Could not find GoBuyMe's public sign-in configuration")


def _json_request(url, *, headers, body=None):
    try:
        return json.loads(_request(url, headers=headers, body=body))
    except ValueError:
        raise RuntimeError("GoBuyMe returned an unreadable response") from None


def _barcode(value):
    upc = product_upc(value)
    return upc.zfill(14) if upc else None


def normalize_tracking(value: str) -> str:
    return value.strip().replace(" ", "").upper()


def _commitment_upc(row):
    lines = (row.get("deal") or {}).get("deal_items") or []
    if any(line.get("role") == "bundle_component" for line in lines):
        return None
    if row.get("selected_deal_item_id"):
        line = row.get("selected_deal_item")
    else:
        line = lines[0] if len(lines) == 1 and lines[0].get("role") == "primary" else None
    if line and line.get("quantity") == 1:
        return _barcode((line.get("product") or {}).get("upc"))
    return None


def _can_link(row, units, now):
    if row.get("status") not in TRACKABLE_STATUSES:
        return False
    deadline = row.get("tracking_deadline")
    if deadline and datetime.fromisoformat(deadline) < now:
        return False
    allocated = sum(link.get("units_in_shipment") or 1 for link in row["shipment_commitments"])
    return row["units_committed"] - allocated >= units


class BuyerTracker:
    def __init__(self, *, email: str, password: str, api_key: str):
        self.api_key = api_key.strip()
        self.headers = {"apikey": _browser_api_key(), "Content-Type": "application/json"}
        session = _json_request(
            ORIGIN + "/auth/v1/token?grant_type=password",
            headers=self.headers,
            body={"email": email.strip(), "password": password},
        )
        try:
            self.headers["Authorization"] = "Bearer " + session["access_token"]
            self.user_id = session["user"]["id"]
        except (KeyError, TypeError):
            raise RuntimeError("GoBuyMe sign-in did not return a buyer session") from None
        profiles = self._read("profiles", {"id": f"eq.{self.user_id}", "select": "buyer_id"})
        self.buyer_id = profiles[0].get("buyer_id") if len(profiles) == 1 else None
        if not self.buyer_id:
            raise RuntimeError("GoBuyMe profile has no buyer ID")

    def _read(self, table, params):
        rows = _json_request(f"{ORIGIN}/rest/v1/{table}?{urlencode(params)}", headers=self.headers)
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise RuntimeError("GoBuyMe returned unexpected Tracker data")
        return rows

    def _commitments(self, *, commitment_id=None):
        params = {"user_id": f"eq.{self.user_id}", "select": COMMITMENT_COLUMNS, "order": "id"}
        if commitment_id:
            return self._read("commitments", {**params, "id": f"eq.{commitment_id}"})
        params["status"] = f"in.({','.join(sorted(TRACKABLE_STATUSES))})"
        rows = []
        while True:
            page = self._read("commitments", {**params, "limit": 100, "offset": len(rows)})
            rows.extend(page)
            if len(page) < 100:
                return rows

    def _shipment(self, tracking):
        rows = self._read("shipments", {
            "user_id": f"eq.{self.user_id}",
            "tracking_number": f"eq.{tracking}",
            "select": "id,shipment_commitments(commitment_id,units_in_shipment)",
            "limit": 2,
        })
        if len(rows) > 1:
            raise ValueError("Multiple GoBuyMe boxes already have this tracking number")
        return rows[0] if rows else None

    def submit_tracking(self, *, tracking: str, items: Sequence[tuple[str | None, int]], order_number: str | None):
        require_group_submissions_enabled()
        tracking = normalize_tracking(tracking)
        if not re.fullmatch(r"[A-Z0-9-]+", tracking):
            raise ValueError("GoBuyMe requires a valid tracking number")
        upcs = {_barcode(upc) for upc, _ in items}
        if not upcs or None in upcs:
            raise ValueError("Every item sharing this tracking needs a valid UPC for GoBuyMe")
        if len(upcs) != 1:
            raise ValueError("This box contains multiple UPCs. Link it to the correct lines in GoBuyMe Tracker.")
        upc = next(iter(upcs))
        units = sum(quantity for _, quantity in items)
        if units <= 0 or any(quantity <= 0 for _, quantity in items):
            raise ValueError("GoBuyMe requires positive shipment quantities")

        existing = self._shipment(tracking)
        if existing is not None:
            links = existing.get("shipment_commitments") or []
            if len(links) == 1 and links[0].get("units_in_shipment") == units:
                linked_id = links[0]["commitment_id"]
                linked = self._commitments(commitment_id=linked_id)
                if len(linked) == 1 and _commitment_upc(linked[0]) == upc:
                    return existing["id"]
            raise ValueError("Tracking already exists in GoBuyMe without the expected UPC and units. Review it in Tracker.")

        now = datetime.now(timezone.utc)
        matches = [row for row in self._commitments() if _commitment_upc(row) == upc and _can_link(row, units, now)]
        if len(matches) != 1:
            reason = "Multiple matching reservations" if matches else "No matching reservation with enough units"
            raise ValueError(f"{reason} for UPC {upc}; review the GoBuyMe Tracker")
        commitment_id = matches[0]["id"]
        payload = {"tracking_number": tracking, "units": units, "commitment_ids": [commitment_id]}
        if order_number:
            payload["order_id"] = order_number
        result = _json_request(
            ORIGIN + "/functions/v1/buyer-api",
            headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
            body={"action": "submit_tracking", "buyer_id": self.buyer_id, "payload": payload},
        )
        if not isinstance(result, dict) or result.get("status") not in {"created", "already_added"}:
            raise RuntimeError("GoBuyMe did not confirm the tracking submission")
        shipment = self._shipment(tracking)
        links = (shipment or {}).get("shipment_commitments") or []
        if (
            len(links) != 1
            or links[0].get("commitment_id") != commitment_id
            or links[0].get("units_in_shipment") != units
            or (result.get("box_id") and result["box_id"] != shipment["id"])
        ):
            raise RuntimeError("GoBuyMe did not confirm the expected Tracker link and quantity. Review the box in Tracker.")
        return shipment["id"]
