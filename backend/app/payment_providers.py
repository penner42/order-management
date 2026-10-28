"""Read-only adapters. Reconciliation never depends on a provider's wire fields."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol
from urllib.parse import urlsplit
import urllib.request
import re

from app.utils.buying_group_apis import parsefile
from app.payment_evidence import ReceiptEvidence, PaymentEvidence, PaymentFetch


class PaymentProvider(Protocol):
    key: str
    def ready(self, group) -> tuple[bool, str | None]: ...
    def fetch(self, group) -> PaymentFetch: ...


def portal_date(value) -> datetime | None:
    if not value:
        return None
    for fmt in ("%a %b %d %Y %H:%M:%S %z", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(str(value), fmt)
            # Parsefile's date-only paid dates are calendar dates, represented at noon UTC.
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc, hour=12 if fmt == "%m/%d/%Y" else parsed.hour)
        except ValueError:
            pass
    raise ValueError("Unrecognized portal date")


def canonical_base(base_url: str) -> str:
    """Resolve public login redirects before sending any credentials or cookies."""
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Payment websites require an HTTPS base URL without embedded credentials")
    req = urllib.request.Request(base_url.rstrip("/") + "/f/login", headers={"User-Agent": parsefile._BROWSER_UA})
    with parsefile._safe_urlopen(req, timeout=30) as response:
        final = urlsplit(response.url)
        response.read()
    return f"{final.scheme}://{final.netloc}"


class ParsefilePaymentProvider:
    key = "parsefile"

    def ready(self, group):
        required = ("base_url", "api_email", "api_password", "bearer_token", "api_user_id")
        missing = [name for name in required if getattr(group, name, None) in (None, "")]
        return (False, "Missing payment configuration: " + ", ".join(missing)) if missing else (True, None)

    def _table(self, base, email, password, session_id, page):
        rows, seen = [], set()
        current = session_id
        expected_count = None
        for number in range(1, 1001):
            def load(sid):
                return parsefile._portal_post(base_url=base, session_id=sid, page=page,
                    cmd={"cmd": "load", "instance": "table", "_table": page, "checkLoading": 0, "init": True},
                    data={"page": number, "limit": 500, "order": "f_id", "filters": {}}, timeout_seconds=60)
            try:
                current, payload = load(current)
            except parsefile._PortalSessionExpired:
                current = parsefile.portal_login(base_url=base, email=email, password=password)
                current, payload = load(current)
            tables = [t for t in payload.get("loadtable", []) if t.get("table") == page]
            if len(tables) != 1:
                raise ValueError("Portal returned an unexpected payment table")
            data = tables[0].get("data", {})
            batch = data.get("data") if isinstance(data, dict) else None
            if not isinstance(batch, list):
                raise ValueError("Portal table response is incomplete")
            count = data.get("count")
            if count is None or int(count) < 0:
                raise ValueError("Portal did not provide a verifiable table count")
            if expected_count is not None and expected_count != int(count):
                raise ValueError("Portal history changed while reading pages; refresh again")
            expected_count = int(count)
            for row in batch:
                if row.get("f_id") is None:
                    raise ValueError("Portal returned a record without an identity")
                key = (row.get("f_type") if page == "it@activity" else "PAYMENT", str(row.get("f_id")))
                if key in seen:
                    raise ValueError("Portal pagination repeated a record; refresh again")
                seen.add(key)
            rows.extend(batch)
            if len(rows) > expected_count:
                raise ValueError("Portal table count does not match its records")
            if len(rows) == expected_count:
                return current, rows
            if not batch:
                raise ValueError("Portal returned an empty page before the end of history")
        raise ValueError("Portal pagination exceeded the supported page count")

    def archives(self, group, base):
        """Archive URLs are advertised in the authenticated portal's main menu."""
        sid = parsefile.portal_login(base_url=base, email=group.api_email, password=group.api_password)
        _, payload = parsefile._portal_post(base_url=base, session_id=sid, page="cards",
            cmd={"cmd": "menus", "screen": 1, "page": "cards", "section": "back@mainlayout"},
            data={}, timeout_seconds=30)
        menus = payload.get("scope", {}).get("menus", {}).get("data")
        if not isinstance(menus, list):
            raise ValueError("Archive discovery did not return portal navigation")
        current_host = urlsplit(base).hostname.removeprefix("www.")
        result = []
        for row in menus:
            match = re.fullmatch(r"Archive\s+(\d{4})", str(row.get("title", "")).strip(), re.I)
            if not match:
                continue
            year, url = int(match[1]), str(row.get("href", ""))
            parsed = urlsplit(url)
            host = (parsed.hostname or "").removeprefix("www.")
            # Credentials only go to the portal's advertised same-site annual subdomain.
            if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443) or host != f"{year}.{current_host}":
                raise ValueError("Unsafe or unsupported archive address")
            result.append((year, url.rstrip("/")))
        return sorted(set(result), reverse=True)

    def fetch(self, group):
        base = canonical_base(group.base_url)
        current = self._fetch_partition(group, base)
        partitions = [(datetime.now(timezone.utc).year, current)]
        warnings = []
        try:
            archives = self.archives(group, base)
        except Exception:
            archives = []
            warnings.append("Archive discovery failed; previously imported history was preserved.")
        # Read every advertised older partition: older receipts can fund current-year payments.
        for year, url in archives:
            try:
                archive_base = canonical_base(url)
                if urlsplit(archive_base).hostname.removeprefix("www.") != urlsplit(url).hostname.removeprefix("www."):
                    raise ValueError("Archive redirected to a different site")
                partitions.append((year, self._fetch_partition(group, archive_base, archived=True)))
            except Exception:
                warnings.append(f"Archive {year} is unavailable; previously imported history was preserved.")
        payments, receipts = {}, {}
        for year, partition in partitions:
            local_ids = {}
            for receipt in partition.receipts:
                receipt.identity_key = f"{receipt.created_at.year if receipt.created_at else year}:{receipt.external_id}"
                local_ids[receipt.external_id] = receipt.identity_key
                previous = receipts.get(receipt.identity_key)
                if previous and previous.created_at != receipt.created_at:
                    raise ValueError("Archive receipt identities conflict")
                receipts.setdefault(receipt.identity_key, receipt)
            for payment in partition.payments:
                payment.identity_key = f"{payment.created_at.year if payment.created_at else year}:{payment.external_id}"
                if payment.receipt_ids is not None:
                    payment.receipt_ids = [local_ids.get(rid, rid) for rid in payment.receipt_ids]
                previous = payments.get(payment.identity_key)
                if previous and previous.created_at != payment.created_at:
                    raise ValueError("Archive payment identities conflict")
                payments.setdefault(payment.identity_key, payment)
        # Resolve cross-partition explicit links only where the raw identity is unique.
        raw_ids = {}
        for receipt in receipts.values():
            raw_ids.setdefault(receipt.external_id, []).append(receipt.identity_key)
        for payment in payments.values():
            if payment.receipt_ids is not None:
                payment.receipt_ids = [raw_ids[rid][0] if rid not in receipts and len(raw_ids.get(rid, [])) == 1 else rid
                    for rid in payment.receipt_ids]
        return PaymentFetch(list(payments.values()), list(receipts.values()),
            history_complete=not warnings, warnings=list(dict.fromkeys(current.warnings + warnings)))

    def _fetch_partition(self, group, base, *, archived=False):
        sid = parsefile.portal_login(base_url=base, email=group.api_email, password=group.api_password)
        sid, activity = self._table(base, group.api_email, group.api_password, sid, "it@activity")
        sid, payments = self._table(base, group.api_email, group.api_password, sid, "it@payments")
        portal_receipts = {}
        if archived:
            # Annual sites expose the authenticated receipt table but reject current-site API tokens.
            sid, rows = self._table(base, group.api_email, group.api_password, sid, "it@receipts")
            portal_receipts = {str(r["f_id"]): r for r in rows}
            details = []
        else:
            details = parsefile.list_all_received_items(base_url=base, bearer_token=group.bearer_token,
                user_id=group.api_user_id, email=group.api_email).rows
        detail_index = {}
        for detail in details:
            detail_index.setdefault(str(detail.receipt_id), []).append(detail)
        ledger_payments = {str(r["f_id"]): r for r in activity if r["f_type"] == "PAYMENT"}
        if set(ledger_payments) != {str(r["f_id"]) for r in payments}:
            raise ValueError("Activity and Payments history disagree; refresh again")
        normalized_payments = []
        for row in payments:
            ledger = ledger_payments[str(row["f_id"])]
            amount = Decimal(str(row["f_amount"]))
            if amount != -Decimal(str(ledger["f_amount"])):
                raise ValueError("Activity and Payments amounts disagree; refresh again")
            status = str(row.get("f_status") or "UNKNOWN").upper()
            normalized_payments.append(PaymentEvidence(external_id=str(row["f_id"]), amount=amount,
                created_at=portal_date(row.get("f_created") or row.get("f_date")),
                sent_at=portal_date(row.get("f_sent_date") or row.get("f_paid_date")),
                sequence=ledger.get("f_num"), status=status, status_known=status in {"PAID", "SENT", "REQUESTED", "PENDING", "NEW", "APPROVED"},
                void=parsefile._parse_boolish(str(row.get("f_void") or "")), receipt_ids=[rid.strip() for rid in str(row["f_receipt"]).replace(";", ",").split(",") if rid.strip()] if row.get("f_receipt") else None,
                memo=str(row.get("f_memo") or "")))
        receipts = []
        unknown = set()
        for row in activity:
            if row["f_type"] == "PAYMENT":
                continue
            if row["f_type"] != "RECEIPT":
                unknown.add(str(row["f_type"]))
                continue
            lines = detail_index.get(str(row["f_id"]), [])
            historical = portal_receipts.get(str(row["f_id"]), {})
            receipts.append(ReceiptEvidence(external_id=str(row["f_id"]), amount=Decimal(str(row["f_amount"])),
                created_at=datetime.fromtimestamp(int(row["f_time"]), timezone.utc) if row.get("f_time") else portal_date(row.get("f_created")),
                sequence=row.get("f_num"), void=parsefile._parse_boolish(str(row.get("f_void") or "")), verified=parsefile._parse_boolish(str(row.get("f_verified") or "")),
                quantity=sum(d.qty for d in lines) if lines and all(d.qty is not None for d in lines) else historical.get("f_qty"),
                tracking_numbers=sorted({tn for d in lines for tn in d.tracking_numbers} or set(parsefile._split_trackings(historical.get("f_tracking")))),
                description="; ".join(dict.fromkeys(d.item or "" for d in lines)) or str(row.get("f_details") or ""),
                details=[{"description": d.item, "quantity": d.qty, "amount": str(d.ext_total) if d.ext_total is not None else None,
                    "tracking_numbers": d.tracking_numbers} for d in lines]))
        return PaymentFetch(normalized_payments, receipts, warnings=["Unsupported activity types: " + ", ".join(sorted(unknown))] if unknown else [])


from app.group_payment_providers import BuyinggroupPaymentProvider, UsabgPaymentProvider, DealandrunnerPaymentProvider

PROVIDERS: dict[str, PaymentProvider] = {
    "parsefile": ParsefilePaymentProvider(), "buyinggroup": BuyinggroupPaymentProvider(),
    "usabg": UsabgPaymentProvider(), "dealandrunner": DealandrunnerPaymentProvider(),
}


def provider_for(group):
    return PROVIDERS.get((group.api_framework or "").strip().lower())
