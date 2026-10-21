"""Read-only group history adapters. Never call submission or payout APIs here."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from itertools import combinations
from urllib.parse import urlencode, quote, urlsplit

from app.payment_evidence import PaymentEvidence, ReceiptEvidence, PaymentFetch
from app.utils.buying_group_apis import buyinggroup as bg, usabg, dealandrunner as dar

ZERO = Decimal("0.00")


def amount(value):
    try:
        result = Decimal(str(value).replace(",", "").replace("$", "").strip())
        if not result.is_finite() or result != result.quantize(Decimal("0.01")):
            raise ValueError()
        return result
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Invalid history amount") from None


def date(value):
    if value in (None, "", "---"):
        return None
    text = str(value).strip()
    for fmt in ("%m-%d-%Y %H:%M:%S", "%m-%d-%Y, %H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%m/%d/%Y %I:%M%p", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except ValueError:
        raise ValueError("Invalid history date") from None


def identity(value):
    if value is None or not str(value).strip():
        raise ValueError("History record has no identity")
    return str(value).strip()


def flag(value):
    if value in (False, None, 0, "0", "false", "False", ""):
        return False
    if value in (True, 1, "1", "true", "True"):
        return True
    raise ValueError("Invalid history flag")


class HistoryPages:
    """Fail closed on inconsistent counts, repeated records, or truncated pages."""
    def __init__(self):
        self.rows = []
        self.seen = set()
        self.total = None

    def add(self, rows, count, key):
        if not isinstance(rows, list) or type(count) is not int or count < 0:
            raise ValueError("History pagination metadata is missing")
        if self.total is not None and self.total != count:
            raise ValueError("History changed during pagination")
        self.total = count
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid history row")
            rid = identity(key(row))
            if rid in self.seen:
                raise ValueError("History pagination repeated a record")
            self.seen.add(rid)
        self.rows.extend(rows)
        if len(self.rows) > count or (not rows and len(self.rows) != count):
            raise ValueError("History pagination is incomplete")
        return len(self.rows) == count


class LoginProvider:
    def ready(self, group):
        missing = [k for k in ("api_username", "api_password") if not getattr(group, k, None)]
        if missing:
            return False, "Missing payment configuration: " + ", ".join(missing)
        base = getattr(group, "base_url", None)
        if base:
            parsed = urlsplit(base)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                return False, "Payment APIs require an HTTPS URL without embedded credentials."
        return True, None


class BuyinggroupPaymentProvider(LoginProvider):
    key = "buyinggroup"

    def fetch(self, group):
        base = bg.resolve_base_url(group.base_url)
        auth = dict(base_url=base, username=group.api_username, password=group.api_password, timeout_seconds=30)
        tokens = bg._ensure_tokens(**auth)

        def table(path, field):
            nonlocal tokens
            pages = HistoryPages()
            for page in range(1, 1001):
                def load(toks):
                    status, text, payload = bg._multipart_request(url=base + "/v1/" + path,
                        fields=[("page", str(page)), ("page_size", "500"), ("order_by", "created_dt"),
                                ("direction", "asc"), ("date_range", "[]"), ("enable_totalization", "false")],
                        headers=bg._auth_headers(toks.access), timeout_seconds=30)
                    if bg._is_auth_failure(status, payload, text):
                        raise bg._SessionExpired("Payment history session expired")
                    if status != 200 or not isinstance(payload, dict) or payload.get("status") != "SUCCESS":
                        raise ValueError("Payment history request failed")
                    return payload["payload"]
                payload, tokens = bg._with_reauth(**auth, tokens=tokens, call=load)
                if pages.add(payload.get(field), payload.get("count"), lambda r: r.get("key")):
                    return pages.rows
            raise ValueError("History pagination limit exceeded")

        payments = table("payment/get_payments", "payments")
        activity = table("activity/get_activities", "activities")
        receipts = table("receipt/get_receipts", "receipts")
        orders = table("order/get_orders", "orders")
        return normalize_buyinggroup(payments, activity, receipts, orders)


def normalize_buyinggroup(payments, activity, receipts, orders):
    rewards = {}
    reward_aliases = {}
    for row in orders:
        if row.get("reward") in (None, "", "---"):
            continue
        reward = amount(row["reward"])
        aliases = {bg.tracking_reward_key(t) for key in ("tracking_id", "alt_tracking_id") if (t := bg._nested_tracking_id(row.get(key)))}
        for key in ("tracking_id", "alt_tracking_id"):
            tracking = bg._nested_tracking_id(row.get(key))
            if tracking:
                normalized = bg.tracking_reward_key(tracking)
                if normalized in rewards and rewards[normalized] != reward:
                    raise ValueError("Tracking reward identities disagree")
                rewards[normalized] = reward
                reward_aliases.setdefault(normalized, set()).update(aliases)

    receipt_rows = {identity(r.get("receipt_id")): r for r in receipts}
    if len(receipt_rows) != len(receipts):
        raise ValueError("Receipt history repeats an identity")
    # A tracking reward is paid once, even when its tracking has several receipts.
    reward_used = set()
    normalized_receipts = []
    cash_total = {}
    for row in sorted(receipts, key=lambda r: (date(r.get("created_dt")) or datetime.min.replace(tzinfo=timezone.utc), r["receipt_id"])):
        rid = identity(row["receipt_id"])
        tracking = [t for k in ("tracking", "alt_tracking") if (t := bg._nested_tracking_id(row.get(k)))]
        reward = ZERO
        for tn in tracking:
            key = bg.tracking_reward_key(tn)
            if key in rewards:
                aliases = reward_aliases[key]
                if not (aliases & reward_used):
                    reward = rewards[key]
                    # Mark both aliases as used.
                    reward_used.update(aliases)
                break
        # Void/unpriced receipts remain visible without inventing a monetary value.
        total = amount(row["total"]) if row.get("total") not in (None, "", "---") else ZERO
        cash_total[rid] = total + reward
        normalized_receipts.append(ReceiptEvidence(external_id=rid, amount=total + reward,
            created_at=date(row.get("created_dt")), void=flag(row.get("void")), verified=flag(row.get("verified")),
            quantity=int(row["quantity"]) if row.get("quantity") is not None else None,
            tracking_numbers=sorted(set(tracking)), description=str(row.get("order_id") or ""),
            details=[{"amount": str(total), "cash_reward": str(reward)}]))

    processed = {}
    credits = []
    cash_adjustments = []
    warnings = []
    for row in activity:
        typ, detail = row.get("type"), row.get("details")
        if typ == "Bg_Points":
            continue
        if typ == "Payment" and detail == "Processed Payment":
            ref = identity(row.get("reference_id"))
            if ref in processed:
                raise ValueError("Activity repeats a processed payment")
            processed[ref] = row
        elif typ == "Receipt":
            rid = identity(row.get("reference_id"))
            if rid not in receipt_rows:
                raise ValueError("Activity references an unavailable receipt")
            # Verified/revised events can repeat a full value, not a cash delta.
            if detail in {"New Receipt", "Verified Receipt"}:
                credits.append((date(row.get("created_dt")), rid, amount(row["amount"])))
            elif detail != "Revised Receipt":
                warnings.append("Review unsupported cash activity: " + str(detail))
        elif typ == "Referral Reward":
            cash_adjustments.append((date(row.get("created_dt")), amount(row["amount"])))
        else:
            warnings.append("Review unsupported cash activity: " + str(detail))
    known = {identity(p.get("payment_id")) for p in payments}
    if set(processed) - known:
        raise ValueError("Activity and payment identities disagree")

    # Retain unspent receipts across requests. A maximum request can skip unverified
    # receipts; use only uniquely reconciling whole-receipt selections automatically.
    credits.sort(key=lambda x: (x[0] or datetime.max.replace(tzinfo=timezone.utc), x[1]))
    consumed = defaultdict(lambda: ZERO)
    normalized_payments = []
    previous_request = None
    for row in sorted(payments, key=lambda r: (date(r.get("created_dt")) or datetime.min.replace(tzinfo=timezone.utc), r["payment_id"])):
        pid, value = identity(row.get("payment_id")), amount(row["amount"])
        status = str(row.get("status") or "UNKNOWN").upper()
        ledger = processed.get(pid)
        if ledger and -amount(ledger["amount"]) != value:
            raise ValueError("Activity and payment amounts disagree")
        if status in {"PAID", "SENT", "PROCESSING"} and not ledger:
            raise ValueError("Processed payment is missing from Activity")
        created = date(row.get("created_dt"))
        candidates = {}
        for credited, rid, gross in credits:
            if created and credited and credited <= created:
                # Do not count a verification event as a second receipt.
                candidates[rid] = min(gross, cash_total[rid]) - consumed[rid]
        candidates = {rid: v for rid, v in candidates.items() if v > 0 and not flag(receipt_rows[rid].get("void"))}
        shares = None
        notes = []
        if not flag(row.get("void")) and value > 0:
            if sum(candidates.values(), ZERO) == value:
                shares = candidates
            elif candidates and sum(candidates.values(), ZERO) < value:
                # Maximum requests consume all available evidenced receipts. A
                # missing opening balance/adjustment must still be explained locally.
                shares = candidates
                notes.append("Receipt credits are below the maximum request. Reconcile the opening balance or cash adjustment and explain any bonus before confirming.")
            else:
                # Most carryovers involve one held receipt. Limit combinatorial work
                # and refuse ambiguous matches instead of assigning a FIFO fiction.
                selection = unique_receipt_subset(candidates, value)
                if selection is not None:
                    shares = selection
                else:
                    recent_ids = {rid for t, rid, _ in credits if t and created and (previous_request is None or t > previous_request) and t <= created}
                    recent = {rid: v for rid, v in candidates.items() if rid in recent_ids}
                    if recent and sum(recent.values(), ZERO) == value:
                        shares = recent
                        notes.append("This request's Activity window reconciles, but earlier receipt balances remain unresolved. Verify held receipts before confirming.")
            if shares is None:
                notes.append("Maximum-payment receipt balance does not reconcile uniquely. Review held receipts, earlier requests, and adjustments before confirming.")
                # Reserving an arbitrary subset here would contaminate later requests.
                # Use the request's chronological candidates for review only.
            else:
                for rid, share in shares.items():
                    consumed[rid] += share
        if any(created and t and t <= created for t, _ in cash_adjustments):
            notes.append("Cash referral rewards or reversals exist in Activity; verify their payment allocation and explain any bonus or fee.")
        normalized_payments.append(PaymentEvidence(external_id=pid, amount=value, created_at=created,
            sent_at=date(row.get("paid_dt")) if status in {"PAID", "SENT"} else None,
            status=status, status_known=status in {"PAID", "SENT", "PROCESSING", "REQUESTED", "VOIDED"},
            void=flag(row.get("void")) or status == "VOIDED",
            receipt_ids=list(shares) if shares is not None else None, receipt_amounts=shares,
            warnings=notes, evidence={"processed_at": row.get("processed_dt"), "activity_reference": pid if ledger else None},
            memo="Maximum available payment requested. Activity processing dates do not define receipt coverage."))
        if not flag(row.get("void")):
            previous_request = created
    return PaymentFetch(normalized_payments, normalized_receipts, warnings=sorted(set(warnings)))


def unique_receipt_subset(candidates, target):
    """Find a unique monetary subset; cap states and never break ties by ordering."""
    # Work in integer cents to avoid decimal context and rounding ambiguity.
    limit = int(target * 100)
    states = {0: ()}
    for rid, value in candidates.items():
        cents = int(value * 100)
        if cents <= 0 or cents > limit:
            continue
        for subtotal, path in list(states.items()):
            new = subtotal + cents
            if new > limit:
                continue
            proposal = None if path is None else (*path, rid)
            if new in states:
                states[new] = None
            else:
                states[new] = proposal
        if len(states) > 20000:
            return None
    selected = states.get(limit)
    return {rid: candidates[rid] for rid in selected} if selected else None


class UsabgPaymentProvider(LoginProvider):
    key = "usabg"

    def fetch(self, group):
        base = usabg.resolve_base_url(group.base_url)
        auth = dict(base_url=base, username=group.api_username, password=group.api_password, timeout_seconds=30)
        token = usabg._ensure_token(**auth)

        def get(path, query=None):
            nonlocal token
            (status, payload, _), token = usabg._with_relogin(**auth, token=token,
                call=lambda tok: usabg._authorized_get_json(url=base + path + ("?" + urlencode(query) if query else ""), bearer_token=tok, timeout_seconds=30))
            if status != 200 or not isinstance(payload, dict) or payload.get("status") is False or payload.get("status") not in (True, 0, 200):
                raise ValueError("USABG history request failed")
            return payload

        pages = HistoryPages()
        for number in range(1, 1001):
            p = get("/checks-requests", {"page_number": number, "limit": 100})
            if pages.add(p.get("data"), p.get("totals", {}).get("items"), lambda r: r.get("id")):
                break
        else:
            raise ValueError("Payment pagination limit exceeded")
        payments = pages.rows
        pos = HistoryPages()
        for _ in range(1000):
            p = get("/pos", {"start": len(pos.rows), "limit": 100})
            rows, total = usabg._pos_list_from_payload(p)
            if pos.add(rows, total, lambda r: r.get("po_number")):
                break
        else:
            raise ValueError("PO pagination limit exceeded")
        details = {}
        for row in pos.rows:
            summary = usabg._parse_po_summary(row)
            if summary is None:
                raise ValueError("Invalid purchase order")
            if summary.is_warehouse_received():
                detail = get("/pos/" + quote(summary.po_number, safe="")).get("data")
                if not isinstance(detail, dict) or not isinstance(detail.get("boxes"), list):
                    raise ValueError("Incomplete PO detail")
                details[summary.po_number] = usabg._received_rows_from_po_detail(summary=summary, detail=detail)
        return normalize_usabg(payments, details)


def normalize_usabg(payments, details):
    receipts = []
    for po, lines in details.items():
        if not lines:
            continue
        if any(r.ext_total is None for r in lines):
            raise ValueError("PO receiving amounts are incomplete")
        receipts.append(ReceiptEvidence(external_id=po, amount=sum((r.ext_total for r in lines), ZERO),
            quantity=sum(r.quantity for r in lines), created_at=min((date(r.scanned_at) for r in lines if r.scanned_at), default=None),
            tracking_numbers=sorted({r.tracking_number for r in lines}),
            description="; ".join(dict.fromkeys(r.received_item for r in lines if r.received_item)),
            details=[{"description": r.received_item, "quantity": r.quantity, "amount": str(r.ext_total), "tracking_numbers": [r.tracking_number]} for r in lines]))
    normalized = []
    for row in payments:
        value = amount(row["value"])
        status = str((row.get("status") or {}).get("name") or "UNKNOWN")
        shares = defaultdict(lambda: ZERO)
        references = []
        for tx in row.get("transactions") or []:
            if flag(tx.get("cancelled")):
                continue
            po = identity((tx.get("business_order") or {}).get("po_number"))
            share = -amount(tx["value"])
            if share <= 0:
                raise ValueError("Unexpected USABG payment transaction sign")
            shares[po] += share
            references.append({"reference": identity(tx.get("reference_id")), "receipt_id": po, "amount": str(share), "date": tx.get("date")})
        if shares and sum(shares.values(), ZERO) != value:
            raise ValueError("USABG transactions and payout amount disagree")
        check = row.get("check")
        if check and amount(check["amount"]) != value:
            raise ValueError("USABG check and payout amounts disagree")
        normalized.append(PaymentEvidence(external_id=identity(row.get("id")), amount=value,
            created_at=date(row.get("added_date")), sent_at=date(check.get("date_insert")) if check and status == "Approved" else None,
            status=status.upper(), status_known=status in {"Approved", "Pending", "Denied"}, void=status == "Denied",
            receipt_ids=list(shares) if shares else None, receipt_amounts=dict(shares) if shares else None,
            evidence={"transactions": references, "requested_date": row.get("added_date"), "issued_date": check.get("date_insert") if check else None}))
    return PaymentFetch(normalized, receipts)


class DealandrunnerPaymentProvider(LoginProvider):
    key = "dealandrunner"

    def fetch(self, group):
        base = dar.resolve_base_url(group.base_url)
        auth = dict(base_url=base, email=group.api_username, password=group.api_password, timeout_seconds=30)
        token = dar._ensure_token(**auth)
        owner = dar._cached_owner_id(dar._cache_key(base, group.api_username, group.api_password))
        if not owner:
            raise ValueError("Authenticated runner identity is unavailable")

        def table(path, query, key):
            nonlocal token
            pages = HistoryPages()
            for number in range(1, 1001):
                url = base + path + "?" + urlencode({**query, "pageNumber": number, "pageSize": 200})
                (status, p), token = dar._with_relogin(**auth, token=token,
                    call=lambda tok: dar._get_json(url=url, bearer_token=tok, timeout_seconds=30))
                if status != 200 or not isinstance(p, dict) or p.get("errors"):
                    raise ValueError("D&R history request failed")
                result = p.get("result")
                if not isinstance(result, dict):
                    raise ValueError("D&R pagination metadata missing")
                done = pages.add(result.get("items"), result.get("totalRecords"), key)
                if done:
                    if result.get("hasNextPage") is not False:
                        raise ValueError("D&R pagination disagrees with record count")
                    return pages.rows
                if result.get("hasNextPage") is not True:
                    raise ValueError("D&R history was truncated")
            raise ValueError("D&R pagination limit exceeded")
        payments = table("/disbursement-orders/runners/" + quote(owner, safe=""), {"orderBy": "dateScheduled|asc"}, lambda r: r.get("disbursementOrderId"))
        transactions = table(dar.RUNNER_TRANSACTIONS_PATH, {"ownerIds": owner, "orderBy": "timestamp|asc"}, lambda r: r.get("systemTransactionId"))
        if any(identity(r.get("ownerId") or r.get("referenceRunnerId")) != owner for r in transactions):
            raise ValueError("Runner ledger contains another account")
        return normalize_dealandrunner(payments, transactions)


def match_disbursement_batches(payments, batches):
    """Require unique, noncompeting batch evidence; late posting is permitted.

    Fourteen days covers the observed eight-day posting delay. Older or more
    fragmented payouts stay unresolved for review rather than using nearest-date
    assignment. Two payouts claiming a batch are never both suggested.
    """
    pending = {p.external_id: p for p in payments}
    remaining = dict(batches)
    matched = {}
    while pending:
        candidates = {}
        for pid, p in pending.items():
            start = p.created_at
            lock = date(p.evidence["lock_date"])
            if not start or not lock:
                continue
            eligible = [key for key in remaining if start <= date(key) <= lock + timedelta(days=14)]
            options = []
            if len(eligible) > 40:
                continue
            for size in (1, 2, 3):
                for keys in combinations(eligible, size):
                    if sum((sum((-amount(t["amount"]) for t in remaining[k]), ZERO) for k in keys), ZERO) == p.amount:
                        options.append(keys)
                        if len(options) > 1:
                            break
                if len(options) > 1:
                    break
            if len(options) == 1:
                candidates[pid] = options[0]
        claims = defaultdict(set)
        for pid, keys in candidates.items():
            for key in keys:
                claims[key].add(pid)
        accepted = {pid: keys for pid, keys in candidates.items() if all(len(claims[k]) == 1 for k in keys)}
        if not accepted:
            break
        for pid, keys in accepted.items():
            matched[pid] = [t for k in keys for t in remaining.pop(k)]
            pending.pop(pid)
    return matched


def normalize_dealandrunner(payments, transactions):
    credits, debits = {}, []
    for row in transactions:
        typ = row.get("entryTypeId")
        wh = identity(row.get("referenceWarehouseItemId"))
        if typ == 1:
            if wh in credits or amount(row["amount"]) < 0:
                raise ValueError("Receiving transaction identities disagree")
            credits[wh] = row
        elif typ == 2 and amount(row["amount"]) < 0:
            debits.append(row)
        else:
            raise ValueError("Unsupported runner ledger entry")
    receipts = []
    for row in credits.values():
        parsed = dar._parse_received_row(row)
        if not parsed or not parsed.system_transaction_id:
            raise ValueError("Receiving credit is missing evidence")
        receipts.append(ReceiptEvidence(external_id=parsed.system_transaction_id, amount=parsed.amount,
            created_at=date(parsed.timestamp), quantity=1, description=parsed.product_name or "",
            tracking_numbers=parsed.tracking_numbers))
    batches = defaultdict(list)
    seen_wh = set()
    for row in debits:
        wh = row["referenceWarehouseItemId"]
        credit = credits.get(wh)
        if not credit or wh in seen_wh or amount(credit["amount"]) != -amount(row["amount"]):
            raise ValueError("Payout debit does not match a unique receiving credit")
        seen_wh.add(wh)
        if not date(row.get("timestamp")):
            raise ValueError("Payout transaction has no posting date")
        batches[row["timestamp"]].append(row)
    normalized = []
    for row in payments:
        if row.get("disbursementOrderStatusId") != 3 or row.get("disbursementOrderItemStatusId") != 2:
            continue
        normalized.append(PaymentEvidence(external_id=identity(row.get("disbursementOrderId")),
            amount=amount(row["disbursementTotal"]), created_at=date(row.get("dateScheduled")), status="COMPLETED",
            receipt_ids=[], evidence={"reference": str(row.get("numericalId") or ""), "scheduled_date": row.get("dateScheduled"), "lock_date": row.get("dateUpdated")},
            memo=f"Disbursement #{row.get('numericalId') or row['disbursementOrderId']}. Request date uses the scheduled payout date. Sent date uses the latest matched payout transaction."))
    matched = match_disbursement_batches(normalized, batches)
    for p in normalized:
        rows = matched.get(p.external_id)
        if rows is None:
            p.warnings.append("Payout debit batches could not be assigned uniquely. Review receipt coverage; sent date remains unverified.")
            continue
        shares = defaultdict(lambda: ZERO)
        for row in rows:
            shares[identity(credits[row["referenceWarehouseItemId"]]["systemTransactionId"])] += -amount(row["amount"])
        p.receipt_ids = list(shares)
        p.receipt_amounts = dict(shares)
        p.sent_at = max(date(r["timestamp"]) for r in rows)
        p.evidence["transactions"] = [{"reference": identity(r.get("systemTransactionId")), "posted_at": r["timestamp"], "amount": str(-amount(r["amount"]))} for r in rows]
    return PaymentFetch(normalized, receipts)
