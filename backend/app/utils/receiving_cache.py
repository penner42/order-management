"""Record provider inputs once; replay them without network access for local matching."""
from collections import Counter
from dataclasses import fields, is_dataclass
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json

from app.utils.buying_group_apis import bfmr, buyinggroup, dealandrunner, parsefile, usabg


_TYPES = {cls.__name__: cls for cls in (
    bfmr.BfmrTrackerRow, bfmr.BfmrTrackerListResult,
    buyinggroup.BuyinggroupReceipt,
    dealandrunner.DealandrunnerReceivedRow, dealandrunner.DealandrunnerRunnerTransactionsResult,
    parsefile.ParsefileReceivedItem, parsefile.ParsefileReceivedItemsSearchResult,
    usabg.UsabgReceivedRow, usabg.UsabgReceivedListResult,
)}


def encode(value):
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if is_dataclass(value):
        return {"$type": type(value).__name__, "fields": {
            f.name: encode([] if f.name == "request_urls" else "" if f.name == "url" else value.__dict__[f.name])
            for f in fields(value)
        }}
    if isinstance(value, dict):
        return {"$dict": {str(k): encode(v) for k, v in value.items()}}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    return value


def decode(value):
    if isinstance(value, list):
        return [decode(v) for v in value]
    if isinstance(value, dict):
        if "$decimal" in value:
            return Decimal(value["$decimal"])
        if "$type" in value:
            return _TYPES[value["$type"]](**{k: decode(v) for k, v in value["fields"].items()})
        return {k: decode(v) for k, v in value["$dict"].items()}
    return value


def provider_identity(group):
    # Account identifiers distinguish warehouses; passwords/tokens never enter the cache.
    identity = json.dumps([(group.api_framework or "").strip().casefold(),
                           (group.base_url or "").strip().rstrip("/"),
                           group.api_username, group.api_user_id, group.api_email])
    return hashlib.sha256(identity.encode()).hexdigest()


class ReceiptSource:
    def __init__(self, payload=None):
        self.cached = payload is not None
        self.payload = payload if self.cached else {
            "version": 1, "started_at": datetime.now(timezone.utc).isoformat(), "requests": {},
        }

    def request(self, key, fetch, **kwargs):
        if self.cached:
            saved = self.payload["requests"].get(key)
            if saved is None or "error" in saved:
                raise ValueError("Receipt details were unavailable at the last receipt check.")
            return decode(saved["value"])
        try:
            result = fetch(**kwargs)
        except Exception:
            self.payload["requests"][key] = {"error": True}
            raise
        self.payload["requests"][key] = {"value": encode(result)}
        return result


def _reusable_buyinggroup_details(receipts, previous_snapshot):
    if previous_snapshot is None:
        return {}
    previous = previous_snapshot.get("requests", {})
    try:
        previous_receipts = decode(previous["rows"]["value"])
    except (KeyError, TypeError, ValueError):
        return {}

    key_counts = Counter(str(row.raw.get("key") or "") for row in receipts)
    prior_key_counts = Counter(str(row.raw.get("key") or "") for row in previous_receipts)
    prior_by_key = {str(row.raw.get("key") or ""): row for row in previous_receipts}
    reusable = {}
    for row in receipts:
        key = str(row.raw.get("key") or "")
        revision = row.raw.get("modified_dt")
        if (not key or not (row.receipt_id or "").strip() or row.void or not row.quantity or row.quantity <= 0
                or not isinstance(revision, str) or revision.strip().casefold() in ("", "---", "null", "none")
                or key_counts[key] != 1 or prior_key_counts[key] != 1 or prior_by_key[key] != row):
            continue

        request_key = "detail:" + key
        try:
            details = decode(previous[request_key]["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if _buyinggroup_details_cover_receipt(details, row):
            reusable[request_key] = previous[request_key]
    return reusable


def _buyinggroup_details_cover_receipt(details, row):
    if not isinstance(details, dict) or details.get("receipt_id") != row.receipt_id:
        return False
    items = details.get("received_items")
    if not isinstance(items, list) or not items:
        return False
    quantity = 0
    for item in items:
        if not isinstance(item, dict) or item.get("verified") is not True:
            return False
        qty = item.get("qty")
        price = buyinggroup._parse_money_field(item.get("price"))
        total = buyinggroup._parse_money_field(item.get("total"))
        if (type(qty) is not int or qty <= 0 or price is None or total is None
                or not price.is_finite() or not total.is_finite()):
            return False
        quantity += qty
    return quantity == row.quantity


def fetch_receipt_source(group, *, usabg_details=None, browser_token=None, buyinggroup_previous=None):
    """Collect complete inputs before taking the short review-publication lock."""
    source = ReceiptSource()
    framework = (group.api_framework or "").strip().casefold()
    base_url = (group.base_url or "").strip() or None
    username, password = str(group.api_username or "").strip(), str(group.api_password or "")
    if framework == "parsefile":
        source.request("rows", parsefile.list_all_received_items,
                       base_url=base_url, bearer_token=group.bearer_token,
                       user_id=int(group.api_user_id), email=group.api_email,
                       portal_password=group.api_password)
    elif framework == "bfmr":
        source.request("rows", bfmr.list_received_tracker_rows,
                       base_url=base_url, api_key=username, api_secret=password)
    elif framework == "usabg":
        source.request("rows", usabg.list_received_po_rows,
                       base_url=base_url, username=username, password=password,
                       cached_details=usabg_details or {})
    elif framework == "dealandrunner":
        source.request("rows", dealandrunner.list_runner_transactions,
                       base_url=base_url, email=username, password=password)
        try:
            source.request("damaged", dealandrunner.list_damaged_items,
                           base_url=base_url, email=username, password=password)
        except Exception:
            pass  # Report the damage feed error without blocking receipt review.
    elif framework == "buyinggroup":
        credentials = {"base_url": base_url, "username": username, "password": password}
        if browser_token is not None:
            credentials["browser_token"] = browser_token
        receipts = source.request("rows", buyinggroup.list_receipts, **credentials)
        reusable = _reusable_buyinggroup_details(receipts, buyinggroup_previous)
        source.payload["requests"].update(reusable)
        try:
            source.request("returns", buyinggroup.list_returns, **credentials)
        except (buyinggroup.RateLimited, buyinggroup.BrowserSessionExpired, buyinggroup.ReceivingCheckStopped):
            raise
        except Exception:
            pass  # Receipt review remains available if only the Returns list fails.
        if receipts:
            try:
                source.request("rewards", buyinggroup.list_tracking_rewards, **credentials)
            except (buyinggroup.RateLimited, buyinggroup.BrowserSessionExpired, buyinggroup.ReceivingCheckStopped):
                raise
            except Exception:
                pass  # Optional rewards never prevent receipt review.

        def details(key):
            try:
                source.request("detail:" + key, buyinggroup.get_receipt_details,
                               receipt_key=key, **credentials)
            except (buyinggroup.RateLimited, buyinggroup.BrowserSessionExpired, buyinggroup.ReceivingCheckStopped):
                raise
            except Exception:
                pass  # Matching retains unavailable details as unresolved receipts.

        keys = list(dict.fromkeys(str(r.raw.get("key") or "") for r in receipts
                                  if not r.void and (r.quantity or 0) > 0))
        keys = [key for key in keys if "detail:" + key not in reusable]
        if buyinggroup.has_resumable_browser_reads():
            for key in keys:
                details(key)
        else:
            with ThreadPoolExecutor(max_workers=2) as pool:
                for start in range(0, len(keys), 2):
                    list(pool.map(details, keys[start:start + 2]))
    else:
        raise ValueError("Unsupported receiving provider")
    return source
