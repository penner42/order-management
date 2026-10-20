"""Read-only adapters. Reconciliation never depends on a provider's wire fields."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol
from urllib.parse import urlsplit
import urllib.request

from pydantic import BaseModel, Field
from app.utils.buying_group_apis import parsefile


class ReceiptEvidence(BaseModel):
    external_id: str
    amount: Decimal
    currency: str = "USD"
    created_at: datetime | None = None
    sequence: int | None = None
    void: bool = False
    verified: bool = True
    quantity: int | None = None
    tracking_numbers: list[str] = Field(default_factory=list)
    description: str = ""
    details: list[dict] = Field(default_factory=list)


class PaymentEvidence(BaseModel):
    external_id: str
    amount: Decimal
    currency: str = "USD"
    created_at: datetime | None = None
    sent_at: datetime | None = None
    sequence: int | None = None
    status: str
    status_known: bool = True
    void: bool = False
    receipt_ids: list[str] | None = None
    memo: str = ""


@dataclass
class PaymentFetch:
    payments: list[PaymentEvidence]
    receipts: list[ReceiptEvidence]
    complete: bool = True
    history_complete: bool = True
    warnings: list[str] = field(default_factory=list)


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

    def fetch(self, group):
        base = canonical_base(group.base_url)
        sid = parsefile.portal_login(base_url=base, email=group.api_email, password=group.api_password)
        sid, activity = self._table(base, group.api_email, group.api_password, sid, "it@activity")
        sid, payments = self._table(base, group.api_email, group.api_password, sid, "it@payments")
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
            receipts.append(ReceiptEvidence(external_id=str(row["f_id"]), amount=Decimal(str(row["f_amount"])),
                created_at=datetime.fromtimestamp(int(row["f_time"]), timezone.utc) if row.get("f_time") else portal_date(row.get("f_created")),
                sequence=row.get("f_num"), void=parsefile._parse_boolish(str(row.get("f_void") or "")), verified=parsefile._parse_boolish(str(row.get("f_verified") or "")),
                quantity=sum(d.qty for d in lines) if lines and all(d.qty is not None for d in lines) else None,
                tracking_numbers=sorted({tn for d in lines for tn in d.tracking_numbers}),
                description="; ".join(dict.fromkeys(d.item or "" for d in lines)) or str(row.get("f_details") or ""),
                details=[{"description": d.item, "quantity": d.qty, "amount": str(d.ext_total) if d.ext_total is not None else None,
                    "tracking_numbers": d.tracking_numbers} for d in lines]))
        return PaymentFetch(normalized_payments, receipts, warnings=["Unsupported activity types: " + ", ".join(sorted(unknown))] if unknown else [])


PROVIDERS: dict[str, PaymentProvider] = {"parsefile": ParsefilePaymentProvider()}


def provider_for(group):
    return PROVIDERS.get((group.api_framework or "").strip().lower())
