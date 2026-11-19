"""GoBuyMe buyer API and account-scoped Tracker lookups."""
from __future__ import annotations

import base64
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.submission_policy import require_group_submissions_enabled
from app.utils.product_upc import product_upc

SITE = "https://gobuyme.com"
PROJECT = "uuwytliqkfkpylruxhew"
ORIGIN = f"https://{PROJECT}.supabase.co"
TRACKABLE_STATUSES = {"pending", "confirmed", "in_progress"}
PRODUCT_COLUMNS = (
    "product:products(upc,name,asin,bestbuy_sku,product_links(retailer,url)),"
    "links:deal_item_links(product_links(retailer,url))"
)
COMMITMENT_COLUMNS = (
    "id,status,tracking_deadline,units_committed,selected_deal_item_id,"
    f"selected_deal_item:deal_items!commitments_selected_deal_item_id_fkey(quantity,{PRODUCT_COLUMNS}),"
    f"deal:deals(title,deal_id,deal_items!deal_items_deal_id_fkey(role,quantity,{PRODUCT_COLUMNS})),"
    "shipment_commitments(units_in_shipment)"
)
RETAILER_URL_IDS = {
    "amazon": r"/(?:dp|gp/product|gp/aw/d)/([a-z0-9]{10})(?:/|$)",
    "walmart": r"/ip/(?:[^/]+/)?([0-9]+)(?:/|$)",
    "costco": r"(?:\.product\.|/p/(?:[^/]+/)*)([0-9]+)(?:\.html|/|$)",
    "bestbuy": r"/site/(?:[^/]+/)?([0-9]+)\.p(?:/|$)",
}
RETAILER_SKUS = {"amazon": r"[a-z0-9]{10}", "walmart": r"[0-9]+", "costco": r"[0-9]+", "bestbuy": r"[0-9]+"}


@dataclass(frozen=True)
class TrackingItem:
    upc: str | None
    quantity: int
    retailer: str | None = None
    sku: str | None = None
    product_url: str | None = None


class TrackerChoiceRequired(ValueError):
    def __init__(self, message, *, tracking, units, choices):
        super().__init__(message)
        self.detail = {"code": "gobuyme_reservation_choice", "message": message,
                       "tracking": tracking, "units": units, "choices": choices}


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


def _deal_line(row):
    lines = (row.get("deal") or {}).get("deal_items") or []
    if any(line.get("role") == "bundle_component" for line in lines):
        return None
    if row.get("selected_deal_item_id"):
        line = row.get("selected_deal_item")
    else:
        line = lines[0] if len(lines) == 1 and lines[0].get("role") == "primary" else None
    return line if line and line.get("quantity") == 1 else None


def _retailer(value):
    return (value or "").strip().casefold().removesuffix(".com").replace(" ", "")


def _url_product_id(value, retailer):
    if not value or retailer not in RETAILER_URL_IDS:
        return None
    try:
        url = urlsplit(value)
    except ValueError:
        return None
    domain = retailer + ".com"
    if url.scheme not in {"http", "https"} or not url.hostname:
        return None
    if url.hostname != domain and not url.hostname.endswith("." + domain):
        return None
    match = re.search(RETAILER_URL_IDS[retailer], url.path.casefold())
    return match[1] if match else None


def _item_retailer_id(item):
    retailer = _retailer(item.retailer)
    sku = (item.sku or "").strip().casefold()
    link_sku = _url_product_id(item.product_url, retailer)
    if retailer not in RETAILER_SKUS or (sku and link_sku and sku != link_sku):
        return None
    sku = sku or link_sku
    return (retailer, sku) if sku and re.fullmatch(RETAILER_SKUS[retailer], sku) else None


def _line_identifiers(line):
    product = line.get("product") or {}
    deal_links = [link.get("product_links") for link in line.get("links") or []]
    links = deal_links + (product.get("product_links") or [])
    ids = set()
    for retailer, field in (("amazon", "asin"), ("bestbuy", "bestbuy_sku")):
        sku = str(product.get(field) or "").strip().casefold()
        if re.fullmatch(RETAILER_SKUS[retailer], sku):
            ids.add((retailer, sku))
    for link in links:
        if link:
            retailer = _retailer(link.get("retailer"))
            sku = _url_product_id(link.get("url"), retailer)
            if sku:
                ids.add((retailer, sku))
    return _barcode(product.get("upc")), ids


def _match_score(line, identities):
    upc, product_ids = _line_identifiers(line)
    exact_upc = False
    exact_retailer_id = False
    for item_upc, product_id in identities:
        upc_matches = bool(item_upc and item_upc == upc)
        sku_matches = product_id in product_ids
        if (item_upc and upc and not upc_matches) or not (upc_matches or sku_matches):
            return 0
        exact_upc |= upc_matches
        exact_retailer_id |= sku_matches
    return 2 * exact_upc + exact_retailer_id


def _matches_existing_order(existing, order_number, line, identities):
    remote_order = (existing.get("order_id") or "").strip().casefold()
    if not remote_order or not order_number or remote_order != order_number.strip().casefold():
        return False
    upc, product_ids = _line_identifiers(line)
    for item_upc, product_id in identities:
        if item_upc and upc and item_upc != upc:
            return False
        if product_id and product_id not in product_ids and any(key[0] == product_id[0] for key in product_ids):
            return False
    return True


def _remaining_units(row):
    allocated = sum(link.get("units_in_shipment") or 1 for link in row["shipment_commitments"])
    return row["units_committed"] - allocated


def _can_link(row, units, now):
    if row.get("status") not in TRACKABLE_STATUSES:
        return False
    deadline = row.get("tracking_deadline")
    if deadline and datetime.fromisoformat(deadline) < now:
        return False
    return _remaining_units(row) >= units


def _choice(row, line, identities):
    deal = row.get("deal") or {}
    product = line.get("product") or {}
    return {"id": row["id"], "title": product.get("name") or deal.get("title") or "GoBuyMe reservation",
            "deal_number": deal.get("deal_id"), "upc": product.get("upc"),
            "remaining_units": _remaining_units(row), "exact_match": bool(_match_score(line, identities))}


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
            "select": "id,order_id,shipment_commitments(commitment_id,units_in_shipment)",
            "limit": 2,
        })
        if len(rows) > 1:
            raise ValueError("Multiple GoBuyMe boxes already have this tracking number")
        return rows[0] if rows else None

    def submit_tracking(
        self, *, tracking: str, items: Sequence[TrackingItem], order_number: str | None,
        commitment_id: str | None = None,
    ):
        require_group_submissions_enabled()
        tracking = normalize_tracking(tracking)
        if not re.fullmatch(r"[A-Z0-9-]+", tracking):
            raise ValueError("GoBuyMe requires a valid tracking number")
        if not items or any(item.quantity <= 0 for item in items):
            raise ValueError("GoBuyMe requires positive shipment quantities")
        units = sum(item.quantity for item in items)
        identities = [(_barcode(item.upc), _item_retailer_id(item)) for item in items]
        upcs, skus = map(set, zip(*identities))
        if len(upcs - {None}) > 1 or (len(items) > 1 and not (
            len(upcs) == 1 and None not in upcs or len(skus) == 1 and None not in skus
        )):
            raise ValueError("This box has multiple UPCs or products. Link its lines in GoBuyMe Tracker.")

        existing = self._shipment(tracking)
        if existing is not None:
            links = existing.get("shipment_commitments") or []
            if len(links) == 1 and links[0].get("units_in_shipment") == units:
                linked_id = links[0]["commitment_id"]
                linked = self._commitments(commitment_id=linked_id)
                line = _deal_line(linked[0]) if len(linked) == 1 else None
                if line:
                    if commitment_id == linked_id or (commitment_id is None and (
                        _match_score(line, identities) or _matches_existing_order(existing, order_number, line, identities)
                    )):
                        return existing["id"]
                    if commitment_id is None:
                        raise TrackerChoiceRequired(
                            "Confirm the reservation already linked to this GoBuyMe box.", tracking=tracking, units=units,
                            choices=[_choice(linked[0], line, identities)],
                        )
            raise ValueError("Tracking already exists in GoBuyMe with a different reservation or quantity. Review it in Tracker.")

        now = datetime.now(timezone.utc)
        rows = self._commitments(commitment_id=commitment_id) if commitment_id else self._commitments()
        eligible = [(row, line) for row in rows if (line := _deal_line(row)) and _can_link(row, units, now)]
        if commitment_id:
            if len(eligible) != 1 or eligible[0][0]["id"] != commitment_id:
                raise ValueError("The selected GoBuyMe reservation is no longer available for this quantity.")
        else:
            scored = [(row["id"], _match_score(line, identities)) for row, line in eligible]
            highest = max((score for _, score in scored), default=0)
            matches = [row_id for row_id, score in scored if score and score == highest]
            if len(matches) != 1:
                reason = "Multiple matching reservations." if matches else "No matching reservation with enough units."
                if not eligible:
                    raise ValueError(reason + " Review the GoBuyMe Tracker.")
                raise TrackerChoiceRequired(
                    reason + " Choose the correct GoBuyMe reservation.", tracking=tracking, units=units,
                    choices=[_choice(row, line, identities) for row, line in eligible],
                )
            commitment_id = matches[0]
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
