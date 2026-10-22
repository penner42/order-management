"""Record provider inputs once; replay them without network access for local matching."""
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


def fetch_receipt_source(group, *, usabg_details=None):
    """Collect complete inputs before taking the short review-publication lock."""
    source = ReceiptSource()
    framework = (group.api_framework or "").strip().casefold()
    base_url = (group.base_url or "").strip() or None
    username, password = str(group.api_username or "").strip(), str(group.api_password or "")
    if framework == "parsefile":
        source.request("rows", parsefile.list_all_received_items,
                       base_url=base_url, bearer_token=group.bearer_token,
                       user_id=int(group.api_user_id), email=group.api_email)
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
    elif framework == "buyinggroup":
        credentials = {"base_url": base_url, "username": username, "password": password}
        receipts = source.request("rows", buyinggroup.list_receipts, **credentials)
        if receipts:
            try:
                source.request("rewards", buyinggroup.list_tracking_rewards, **credentials)
            except Exception:
                pass  # Optional rewards never prevent receipt review.

        def details(receipt):
            key = str(receipt.raw.get("key") or "")
            try:
                source.request("detail:" + key, buyinggroup.get_receipt_details,
                               receipt_key=key, **credentials)
            except Exception:
                pass  # Matching retains unavailable details as unresolved receipts.

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(details, (r for r in receipts if not r.void and (r.quantity or 0) > 0)))
    else:
        raise ValueError("Unsupported receiving provider")
    return source
