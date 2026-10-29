"""Shared payment reconciliation, independent of provider wire formats."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from app.models import (BuyingGroup, Item, Order, Payment, PaymentLineItem,
    PaymentSource, PaymentReview, PaymentCoverage, PaymentCorrection)
from app.payment_providers import PaymentFetch, provider_for
from app.payment_evidence import PAYMENT_IMPORT_START_YEAR
from app.payment_totals import item_allocated, payment_amount
from app.monarch import lock_editable_payment

ZERO = Decimal("0.00")


def item_label(item):
    if item is None:
        return "Missing local item"
    order = item.order
    parts = []
    if order and order.store_order_number:
        parts.append(f"Order {order.store_order_number}")
    elif order and order.store:
        parts.append(order.store.name)
    parts.append(item.description or "Unnamed item")
    if item.receipt_id:
        parts.append(f"Receipt {item.receipt_id}")
    return " · ".join(parts)


def item_issue(item, detail, issue_links=None):
    label = item_label(item)
    message = f"{label} {detail}"
    if issue_links is not None and item and item.order:
        issue_links[message] = {"label": label, "order_id": item.order_id}
    return message


def item_payable(item):
    """BG reward is an allocated line total, added once after unit payout."""
    group = item.order.buying_group
    reward = money(item.reward or 0) if group and group.api_framework == "buyinggroup" else ZERO
    return money(item.price_sold or 0) * item.quantity + reward


def receipt_balance(db, receipt):
    coverage = db.query(PaymentCoverage).filter_by(receipt_source_id=receipt.id).order_by(PaymentCoverage.id).all()
    baseline = money(coverage[0].snapshot["amount"]) if coverage else money(receipt.payload["amount"])
    paid = sum((money(c.amount) for c in coverage), ZERO)
    return baseline, paid, ZERO if receipt.payload.get("void") else max(baseline - paid, ZERO)


def existing_payment_issues(db, review, content, issue_links=None):
    """Compare the selected payment during review as well as at confirmation."""
    payment_id = content.get("existing_payment_id")
    if not payment_id:
        return []
    payment = db.get(Payment, payment_id)
    if not payment:
        return ["Selected local payment no longer exists. Choose another payment."]
    if payment.buying_group_id != review.source.buying_group_id:
        return ["Selected local payment belongs to a different buying group. Choose another payment."]
    issues = []
    if db.query(PaymentReview).filter_by(payment_id=payment_id).first():
        issues.append("Selected local payment is already linked to an import. Choose another payment.")
    from app.models import MonarchPaymentReceipt
    if db.get(MonarchPaymentReceipt, payment_id):
        issues.append("Undo the selected payment's Monarch receipt confirmation before linking it.")
    if payment.corrections:
        issues.append("Selected local payment already has carried corrections and cannot be linked to another import.")
    expected = defaultdict(lambda: ZERO)
    for allocation in content.get("allocations", []):
        expected[allocation["item_id"]] += money(allocation["amount"])
    actual = {line.item_id: money(line.amount) for line in payment.line_items}
    for item_id in sorted(set(expected) | set(actual)):
        item = db.get(Item, item_id)
        if item_id not in actual:
            issues.append(item_issue(item, f"has ${expected[item_id]:,.2f} in this review but is not on the selected local payment.", issue_links))
        elif item_id not in expected:
            issues.append(item_issue(item, f"has ${actual[item_id]:,.2f} on the selected local payment but is not allocated in this review.", issue_links))
        elif actual[item_id] != expected[item_id]:
            issues.append(item_issue(item, f"amount differs: local payment ${actual[item_id]:,.2f}; review ${expected[item_id]:,.2f}.", issue_links))
    actual_bonus, expected_bonus = money(payment.payment_bonus), money(content.get("payment_bonus", 0))
    if actual_bonus != expected_bonus:
        issues.append(f"Bonus differs: local payment ${actual_bonus:,.2f}; review ${expected_bonus:,.2f}.")
    return issues


def money(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number != number.quantize(Decimal("0.01")) or abs(number) >= Decimal("10000000000"):
            raise ValueError()
        return number
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, "Amounts must be finite values with at most two decimal places.")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def when(value):
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def lock_group(db, group_id):
    group = db.query(BuyingGroup).filter_by(id=group_id).with_for_update().populate_existing().first()
    if not group:
        raise HTTPException(404, "Buying group not found")
    return group


def capability(group):
    provider = provider_for(group)
    ready, reason = provider.ready(group) if provider else (False, "Payment import is not supported for this framework yet.")
    return {"buying_group_id": group.id, "name": group.name, "supported": bool(provider),
            "provider": provider.key if provider else None, "ready": ready, "reason": reason}


def order_key(source):
    p = source.payload
    return (when(p.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc), p.get("sequence") or 0, source.external_id)


def review_created(review, content):
    """A missing provider date needs an explicit, explained manual boundary."""
    return when(review.source.payload.get("created_at") or (
        content.get("window_end") if content.get("note", "").strip() else None))


def in_window(receipt, start, end):
    date = when(receipt.payload.get("created_at"))
    if not date or not when(end.payload.get("created_at")):
        return False
    # Ledger sequence resolves equal timestamps when both sides supply it.
    def boundary(other, upper):
        other_date = when(other.payload.get("created_at"))
        if date != other_date:
            return date < other_date if upper else date > other_date
        a, b = receipt.payload.get("sequence"), other.payload.get("sequence")
        return (a <= b if upper else a > b) if a is not None and b is not None else False
    return boundary(end, True) and (start is None or boundary(start, False))


def refresh(db: Session, group_id: int, result: PaymentFetch, provider_key: str, *, automate=False):
    """A failed/partial fetch never replaces the last complete snapshot."""
    if not result.complete:
        raise HTTPException(409, "Payment fetch was incomplete. Existing review data was preserved.")
    group = lock_group(db, group_id)
    provider = provider_for(group)
    if not provider or provider.key != provider_key:
        raise HTTPException(409, "Group provider changed during refresh. Try again.")
    sources = db.query(PaymentSource).filter_by(buying_group_id=group_id, provider=provider_key).all()
    index = {(s.kind, s.external_id): s for s in sources}
    seen = set()
    for kind, records in (("payment", result.payments), ("receipt", result.receipts)):
        for evidence in records:
            date = evidence.created_at or (evidence.sent_at if kind == "payment" else None)
            if date is not None and date.year < PAYMENT_IMPORT_START_YEAR:
                continue
            key = (kind, evidence.identity_key or evidence.external_id)
            if not evidence.external_id or key in seen:
                raise HTTPException(409, "Provider returned duplicate or missing source identities.")
            money(evidence.amount)
            if evidence.amount < 0:
                raise HTTPException(409, "Provider returned an invalid negative source amount.")
            seen.add(key)
            payload = evidence.model_dump(mode="json")
            if payload.get("identity_key") is None:
                payload.pop("identity_key", None)
            if kind == "payment":
                payload["warnings"] = list(dict.fromkeys(result.warnings + payload.get("warnings", [])))
                payload["history_complete"] = result.history_complete
                # Keep pre-upgrade Parsefile fingerprints stable for empty additions.
                if payload.get("receipt_amounts") is None:
                    payload.pop("receipt_amounts", None)
                if not payload.get("evidence"):
                    payload.pop("evidence", None)
                shares = payload.get("receipt_amounts")
                if shares is not None:
                    if set(shares) != set(payload.get("receipt_ids") or []) or any(money(v) <= 0 for v in shares.values()):
                        raise HTTPException(409, "Provider returned invalid receipt payment shares.")
            row = index.get(key)
            if row is None and evidence.identity_key:
                # Adopt pre-ledger identities without changing their source/coverage primary keys.
                old_key = (kind, evidence.external_id)
                previous_row = index.get(old_key)
                if previous_row and previous_row.payload.get("created_at") == payload.get("created_at"):
                    row = index.pop(old_key)
                    row.external_id = key[1]
                    index[key] = row
            if row is None:
                row = PaymentSource(buying_group_id=group_id, provider=provider_key, kind=kind, external_id=key[1])
                db.add(row)
                index[key] = row
            revision = digest(payload)
            if row.revision and row.revision != revision:
                row.revisions = list(row.revisions or []) + [{"revision": row.revision, "payload": row.payload,
                    "at": datetime.now(timezone.utc).isoformat()}]
            row.payload, row.revision, row.present = payload, revision, True
    for key, row in index.items():
        if key not in seen and result.history_complete:
            row.present = False
    db.flush()
    payments = sorted([s for s in index.values() if s.kind == "payment" and s.present], key=order_key)
    receipts = [s for s in index.values() if s.kind == "receipt" and s.present]
    if group.api_framework == "buyinggroup":
        from app.tracking_rewards import sync_payment_rewards
        sync_payment_rewards(db, group, [s for s in receipts if ("receipt", s.external_id) in seen])
    previous = None
    for source in payments:
        review = db.query(PaymentReview).filter_by(source_id=source.id).first()
        explicit = source.payload.get("receipt_ids")
        ids = [r.id for r in receipts if (r.external_id in explicit if explicit is not None else in_window(r, previous, source))
               and not r.payload.get("void")]
        if review is None:
            review = PaymentReview(source_id=source.id, content={})
            db.add(review)
        else:
            review.version += 1
        content = dict(review.content or {})
        if not content.get("manual") and (review.payment_id is None or automate):
            content.update(receipt_ids=ids, window_start=previous.payload.get("created_at") if previous else None,
                window_end=source.payload.get("created_at"), allocations=[], corrections=[], note="", manual=False)
        review.content = content
        if not source.payload.get("void"):
            previous = source
    # Missing sources also invalidate open clients' versions.
    missing_ids = [s.id for s in index.values() if s.kind == "payment" and not s.present]
    for review in db.query(PaymentReview).filter(PaymentReview.source_id.in_(missing_ids)).all():
        review.version += 1
    if automate:
        from app.payment_ledger import reconcile_group
        db.flush()
        reconcile_group(db, group_id)
        group.payment_sync_last_run_at = datetime.now(timezone.utc)
        group.payment_sync_error = None if result.history_complete else "History is incomplete; prior evidence was preserved."
    db.commit()
    if automate:
        from app.monarch import auto_confirm_receipts
        auto_confirm_receipts(db)
        return {"buying_group_id": group_id, "last_run_at": group.payment_sync_last_run_at,
                "error": group.payment_sync_error}
    return group_view(db, group_id)


def group_sources(db, group_id):
    if group_id in db.info.get("payment_sources_cache", {}):
        return db.info["payment_sources_cache"][group_id]
    return db.query(PaymentSource).filter_by(buying_group_id=group_id).all()


def receipt_matches(db, source):
    items = db.info.get("payment_items_cache", {}).get(source.buying_group_id)
    if items is None:
        items = db.query(Item).join(Order).filter(Order.buying_group_id == source.buying_group_id).all()
    raw_id = source.payload.get("external_id", source.external_id)
    collisions = any(s.kind == "receipt" and s.id != source.id and s.provider == source.provider
        and s.payload.get("external_id") == raw_id for s in group_sources(db, source.buying_group_id))
    out = []
    for item in items:
        tokens = {(s or "").strip().casefold() for s in (item.receipt_id or "").replace(";", ",").split(",")}
        if raw_id.casefold() not in tokens:
            continue
        # Multiple receipt IDs on one local line are ambiguous: let the user split coverage.
        if len(tokens - {""}) != 1:
            continue
        trackings = source.payload.get("tracking_numbers") or []
        if collisions and (not item.receipt_tracking_number or item.receipt_tracking_number.casefold() not in {t.casefold() for t in trackings}):
            continue
        if trackings and item.receipt_tracking_number and item.receipt_tracking_number.casefold() not in {t.casefold() for t in trackings}:
            continue
        out.append({"receipt_source_id": source.id, "item_id": item.id,
            "quantity": item.quantity, "amount": str(item_payable(item))})
    return out


def suggested_allocations(db, review, receipt):
    matches = receipt_matches(db, receipt)
    shares = review.source.payload.get("receipt_amounts")
    target = money(shares[receipt.external_id]) if shares is not None and receipt.external_id in shares else receipt_balance(db, receipt)[2]
    total = sum((money(a["amount"]) for a in matches), ZERO)
    # Preserve an incomplete local match so quantity validation explains the issue.
    if not total or (receipt.payload.get("quantity") is not None and sum(a["quantity"] for a in matches) != receipt.payload["quantity"]):
        return matches
    used = ZERO
    result = []
    for n, a in enumerate(matches):
        share = target - used if n == len(matches) - 1 else (target * money(a["amount"]) / total).quantize(Decimal("0.01"))
        used += share
        if share > 0:
            result.append({**a, "amount": str(share)})
    return result


def outstanding_corrections(db, group_id):
    result = []
    receipts = [s for s in group_sources(db, group_id) if s.kind == "receipt"]
    for receipt in receipts:
        coverage = db.query(PaymentCoverage).filter_by(receipt_source_id=receipt.id).order_by(PaymentCoverage.id).all()
        if not coverage:
            continue
        applied = db.query(PaymentCorrection).filter_by(receipt_source_id=receipt.id).all()
        covered = sum((c.amount for c in coverage), ZERO) + sum((c.amount for c in applied), ZERO)
        current = ZERO if receipt.payload.get("void") else money(receipt.payload["amount"])
        baseline = coverage[0].snapshot
        # An installment's unpaid balance is not a receipt revision.
        principal = sum((c.amount for c in coverage), ZERO) if receipt.payload.get("void") else money(baseline["amount"])
        delta = current - principal - sum((c.amount for c in applied), ZERO)
        details_changed = any(receipt.payload.get(k) != baseline.get(k) for k in ("quantity", "description", "tracking_numbers", "created_at"))
        if not delta and receipt.present and not details_changed:
            continue
        origin = db.get(Payment, coverage[0].payment_id)
        origin_review = db.query(PaymentReview).filter_by(payment_id=origin.id).first()
        origin_snapshot = origin_review.content.get("confirmed_source", {}) if origin_review else {}
        origin_created = origin_snapshot.get("reviewed_created_at") or origin_snapshot.get("created_at")
        result.append({"receipt_source_id": receipt.id, "external_id": receipt.external_id,
            "provider": receipt.provider,
            "origin_payment_id": origin.id, "origin_requested_at": origin_created or origin.payment_requested_at.isoformat(),
            "origin_payment_reference": origin.payment_id,
            "original_amount": str(money(baseline["amount"])), "covered_amount": str(covered),
            "current_amount": str(current), "amount": str(delta), "present": receipt.present,
            "revision": receipt.revision, "items": []})
        weights = defaultdict(lambda: ZERO)
        for c in coverage:
            weights[c.item_id] += c.amount
        result[-1]["items"] = [{"item_id": item_id, "amount": str(v)} for item_id, v in sorted(weights.items())]
        result[-1]["details_changed"] = details_changed
    return result


def correction_suggestions(db, review, outstanding):
    drafts = db.query(PaymentReview).join(PaymentSource).filter(PaymentSource.buying_group_id == review.source.buying_group_id,
        PaymentReview.payment_id.is_(None), PaymentSource.present.is_(True)).all()
    results = []
    for correction in outstanding:
        # A saved manual destination takes precedence over the automatic destination.
        manual_targets = [d for d in drafts if d.source.provider == correction["provider"] and d.content.get("manual") and any(
            x["receipt_source_id"] == correction["receipt_source_id"] for x in d.content.get("corrections", []))]
        eligible = [d for d in drafts if d.source.provider == correction["provider"] and not d.source.payload.get("void") and review_created(d, d.content)
                    and review_created(d, d.content) > when(correction["origin_requested_at"])]
        target = min(manual_targets or eligible, key=lambda d: (review_created(d, d.content) or datetime.max.replace(tzinfo=timezone.utc), order_key(d.source)), default=None)
        if target is None or target.id != review.id or not correction["present"] or money(correction["amount"]) == 0:
            continue
        delta = money(correction["amount"])
        items = correction["items"]
        total = sum((money(i["amount"]) for i in items), ZERO)
        allocated = ZERO
        for n, item in enumerate(items):
            amount = delta - allocated if n == len(items) - 1 else (delta * money(item["amount"]) / total).quantize(Decimal("0.01"))
            allocated += amount
            if amount:
                results.append({"receipt_source_id": correction["receipt_source_id"], "item_id": item["item_id"],
                    "amount": str(amount), "explanation": "Receipt value changed after payment confirmation"})
    return results


def effective_content(db, review, outstanding):
    content = dict(review.content)
    if not content.get("manual") and review.payment_id is None:
        content["allocations"] = [a for rid in content.get("receipt_ids", [])
            if (receipt := db.get(PaymentSource, rid)) for a in suggested_allocations(db, review, receipt)]
        content["corrections"] = correction_suggestions(db, review, outstanding)
    return content


def validate_content(db, review, content, outstanding, *, confirming=False, issue_links=None):
    issues = []
    source = review.source
    p = source.payload
    provider = provider_for(db.get(BuyingGroup, source.buying_group_id))
    if provider is None or provider.key != source.provider:
        issues.append("Group payment provider changed; review its configuration before confirming.")
    if not source.present:
        issues.append("Payment is missing from the latest complete source snapshot.")
    if p.get("void"):
        issues.append("Source payment is void.")
    if not p.get("status_known", True):
        issues.append("Unknown source payment status; verify the provider mapping before confirming.")
    if not review_created(review, content):
        issues.append("Payment creation date is missing; set the Through boundary and explain the chosen request time.")
    if p.get("currency") != "USD":
        issues.append("Only USD payments are supported by local payment accounting.")
    if money(p["amount"]) <= 0:
        issues.append("Source payment amount must be greater than zero.")
    if not p.get("history_complete", True) and not content.get("note", "").strip():
        issues.append("History is incomplete; verify coverage and record an explanation.")
    if p.get("warnings") and not content.get("note", "").strip():
        issues.append("Review provider warnings and record an explanation.")
    allocations = content.get("allocations", [])
    receipts = {s.id: s for s in group_sources(db, source.buying_group_id) if s.kind == "receipt"}
    ids = content.get("receipt_ids", [])
    if not ids and not content.get("corrections"):
        issues.append("A payment requires matched receipt coverage or a correction to previously matched items.")
    explicit = p.get("receipt_ids")
    if explicit and any(not any(r.external_id == rid and r.present for r in receipts.values()) for rid in explicit):
        issues.append("The provider references a receipt missing from the source snapshot.")
    if len(ids) != len(set(ids)):
        issues.append("A receipt was selected more than once.")
    if any(a["receipt_source_id"] not in ids for a in allocations):
        issues.append("An allocation references an excluded receipt.")
    if len({(a["receipt_source_id"], a["item_id"]) for a in allocations}) != len(allocations):
        issues.append("Each receipt/item allocation must be unique.")
    used_quantities = {}
    item_amounts = defaultdict(lambda: ZERO)
    for rid in ids:
        receipt = receipts.get(rid)
        if not receipt:
            issues.append("Selected receipt does not belong to this buying group.")
            continue
        if receipt.provider != source.provider or not receipt.present or receipt.payload.get("void"):
            issues.append(f"Receipt {receipt.external_id} is missing, void, or from another provider.")
        if not receipt.payload.get("verified", True) and not content.get("note", "").strip():
            issues.append(f"Receipt {receipt.external_id} is unverified; check it and record an explanation.")
        if receipt.payload.get("currency") != p.get("currency"):
            issues.append(f"Receipt {receipt.external_id} currency differs from the payment.")
        prior = db.query(PaymentCoverage).filter_by(receipt_source_id=rid).order_by(PaymentCoverage.id).all()
        baseline, paid, remaining_receipt = receipt_balance(db, receipt)
        lines = [a for a in allocations if a["receipt_source_id"] == rid]
        quantity = receipt.payload.get("quantity")
        if not lines or (quantity is None and not content.get("note", "").strip()) or (quantity is not None and sum(a["quantity"] for a in lines) != quantity):
            issues.append(f"Receipt {receipt.external_id} requires complete local item/quantity matches.")
        allocated = sum((money(a["amount"]) for a in lines), ZERO)
        shares = p.get("receipt_amounts")
        expected = money(shares[receipt.external_id]) if shares is not None and receipt.external_id in shares else None
        if expected is not None and allocated != expected:
            issues.append(f"Receipt {receipt.external_id} allocations do not equal its provider payment share.")
        if allocated > remaining_receipt:
            issues.append(f"Receipt {receipt.external_id} allocations exceed its remaining receipt balance.")
        if source.provider == "parsefile" and expected is None and allocated != remaining_receipt:
            issues.append(f"Receipt {receipt.external_id} allocations do not equal its source amount.")
        if expected is None and source.provider != "parsefile" and allocated < remaining_receipt and not content.get("note", "").strip():
            issues.append(f"Explain the partial payment of receipt {receipt.external_id}.")
        if prior:
            original = {c.item_id: c.quantity for c in prior if c.payment_id == prior[0].payment_id}
            if {a["item_id"]: a["quantity"] for a in lines} != original:
                issues.append(f"Receipt {receipt.external_id} item/quantity mapping must match its earlier installment.")
        # Equal receipt/payment timestamps without sequence are never inferred silently.
        if (receipt.payload.get("created_at") and p.get("created_at")
            and when(receipt.payload["created_at"]) == when(p["created_at"])
            and (receipt.payload.get("sequence") is None or p.get("sequence") is None)
            and not content.get("note", "").strip()):
            issues.append("Equal-time receipt ordering requires a review explanation.")
        for a in lines:
            item = db.get(Item, a["item_id"])
            if not item or item.order.buying_group_id != source.buying_group_id:
                issues.append("An allocated item does not belong to this buying group.")
                continue
            if money(a["amount"]) <= 0 or a["quantity"] <= 0:
                issues.append("Receipt allocations require positive amounts and quantities.")
            if item.status.value in {"canceled", "purchased", "shipped", "submitted", "needs_review"}:
                issues.append(item_issue(item, "must be received before payment confirmation.", issue_links))
            used_quantities[(rid, item.id)] = a["quantity"]
            item_amounts[item.id] += money(a["amount"])
    corrections = content.get("corrections", [])
    by_receipt = defaultdict(list)
    correction_index = {c["receipt_source_id"]: c for c in outstanding}
    if len({(c["receipt_source_id"], c["item_id"]) for c in corrections}) != len(corrections):
        issues.append("Each correction/item allocation must be unique.")
    for c in corrections:
        by_receipt[c["receipt_source_id"]].append(c)
        original = correction_index.get(c["receipt_source_id"])
        item = db.get(Item, c["item_id"])
        if not original or not original["present"]:
            issues.append("Correction receipt is missing or no longer has an outstanding difference.")
            continue
        if original["provider"] != source.provider:
            issues.append("Corrections must use receipts from the same payment provider.")
        if not item or item.order.buying_group_id != source.buying_group_id or c["item_id"] not in {i["item_id"] for i in original["items"]}:
            issues.append("Corrections must reference the original covered local items.")
        elif item and item.id in item_amounts:
            # Negative corrections offset this installment's principal. Positive
            # revisions remain separately explained earnings, as before installments.
            item_amounts[item.id] += min(money(c["amount"]), ZERO)
        if not c.get("explanation", "").strip():
            issues.append("Every correction needs an explanation.")
        if review_created(review, content) is None or review_created(review, content) <= when(original["origin_requested_at"]):
            issues.append("A correction must be carried to a later payment.")
        if not money(c["amount"]):
            issues.append("Correction amounts must be nonzero.")
        conflicts = db.query(PaymentReview).join(PaymentSource).filter(PaymentSource.buying_group_id == source.buying_group_id,
            PaymentReview.payment_id.is_(None), PaymentReview.id != review.id).all()
        if any(d.content.get("manual") and any(x["receipt_source_id"] == c["receipt_source_id"] for x in d.content.get("corrections", [])) for d in conflicts):
            issues.append("Correction is assigned to another pending payment; remove it there first.")
    for rid, lines in by_receipt.items():
        original = correction_index.get(rid)
        if original and sum((money(c["amount"]) for c in lines), ZERO) != money(original["amount"]):
            # Explicit partial carries are allowed with an explanation; the rest stays queued.
            proposed = sum((money(c["amount"]) for c in lines), ZERO)
            delta = money(original["amount"])
            if proposed * delta <= 0 or abs(proposed) > abs(delta):
                issues.append("Correction exceeds or reverses the outstanding receipt difference.")
    mappings = {}
    for item_id in item_amounts:
        for c in db.query(PaymentCoverage).filter_by(item_id=item_id).all():
            mappings[(c.receipt_source_id, c.item_id)] = c.quantity
    mappings.update(used_quantities)
    for item_id in item_amounts:
        item = db.get(Item, item_id)
        if sum(q for (_, iid), q in mappings.items() if iid == item_id) > item.quantity:
            issues.append(item_issue(item, "quantity was allocated more than once.", issue_links))
        remaining = item_payable(item) - item_allocated(db, item_id, content.get("existing_payment_id"))
        if item_amounts[item_id] > remaining:
            issues.append(item_issue(item, "allocations exceed its remaining local payout; review its received value.", issue_links))
    bonus = money(content.get("payment_bonus", 0))
    if bonus and not content.get("note", "").strip():
        issues.append("An explicit bonus or fee requires a review explanation.")
    total = sum((money(a["amount"]) for a in allocations), ZERO) + sum((money(c["amount"]) for c in corrections), ZERO) + bonus
    if total != money(p["amount"]):
        issues.append("Allocated total plus corrections must equal the portal payment amount.")
    issues.extend(existing_payment_issues(db, review, content, issue_links))
    return list(dict.fromkeys(issues)), total


def review_view(db, review, outstanding):
    content = effective_content(db, review, outstanding)
    issue_links = {}
    if review.payment_id:
        issues = []
        if not review.source.present:
            issues.append("Confirmed payment is missing from the source; local payment was preserved.")
        if review.source.revision != review.confirmed_revision:
            issues.append("Source payment changed after confirmation; local payment was preserved.")
        total = payment_amount(db.get(Payment, review.payment_id))
        payment = db.get(Payment, review.payment_id)
        group = db.get(BuyingGroup, review.source.buying_group_id)
        for line in payment.line_items if group and group.api_framework == "buyinggroup" else []:
            if item_allocated(db, line.item_id) > item_payable(line.item):
                issues.append(item_issue(line.item, "has confirmed allocations exceeding its corrected payout; payment was preserved.", issue_links))
    else:
        issues, total = validate_content(db, review, content, outstanding, issue_links=issue_links)
    return {"id": review.id, "version": review.version, "payment_id": review.payment_id,
        "fingerprint": review_token(db, review, content),
        "source_id": review.source_id, "external_id": review.source.external_id,
        "source": review.source.payload, "content": content, "issues": issues, "issue_links": issue_links,
        "allocated_total": str(total), "difference": str(money(review.source.payload["amount"]) - total)}


def review_token(db, review, content):
    """Bind confirmation to the displayed matches as well as the source version."""
    item_ids = sorted({a["item_id"] for a in content.get("allocations", []) + content.get("corrections", [])})
    items = []
    for item_id in item_ids:
        item = db.get(Item, item_id)
        items.append(None if not item else {"id": item.id, "order_id": item.order_id,
            "group_id": item.order.buying_group_id, "quantity": item.quantity, "price": str(item.price_sold),
            "reward": str(item.reward),
            "status": item.status.value, "receipt_id": item.receipt_id,
            "allocated": str(item_allocated(db, item.id))})
    receipt_ids = sorted(set(content.get("receipt_ids", [])) | {c["receipt_source_id"] for c in content.get("corrections", [])})
    receipts = [(s.id, s.revision, s.present) for rid in receipt_ids if (s := db.get(PaymentSource, rid))]
    return digest({"version": review.version, "source": review.source.revision, "present": review.source.present,
        "content": content, "items": items, "receipts": receipts})


def allocation_evidence(db, group_id):
    """Load only editor choices, with a fixed query count as group history grows."""
    if db.get(BuyingGroup, group_id) is None:
        raise HTTPException(404, "Buying group not found")
    items = db.query(Item).join(Order).filter(Order.buying_group_id == group_id).options(
        joinedload(Item.order).joinedload(Order.store),
        joinedload(Item.order).joinedload(Order.buying_group),
    ).order_by(Item.id).all()
    allocated = defaultdict(lambda: ZERO)
    # Aggregate separately so multiple corrections cannot multiply line amounts.
    for model in (PaymentLineItem, PaymentCorrection):
        totals = db.query(model.item_id, func.sum(model.amount)).join(
            Item, model.item_id == Item.id).join(Order).filter(
                Order.buying_group_id == group_id).group_by(model.item_id)
        for item_id, amount in totals:
            allocated[item_id] += amount
    receipts = db.query(PaymentSource.id, PaymentSource.external_id, PaymentSource.present,
        PaymentSource.payload).filter_by(buying_group_id=group_id, kind="receipt").order_by(PaymentSource.id).all()
    return {
        "receipts": [{"id": r.id, "external_id": r.external_id, "present": r.present,
            "payload": {k: r.payload.get(k) for k in ("description", "amount", "quantity", "void")}}
            for r in receipts],
        "items": [{"id": i.id, "label": item_label(i), "quantity": i.quantity,
            "order_id": i.order_id, "remaining": str(max(item_payable(i) - allocated[i.id], ZERO))}
            for i in items],
    }


def group_view(db, group_id):
    group = db.get(BuyingGroup, group_id)
    if not group:
        raise HTTPException(404, "Buying group not found")
    outstanding = outstanding_corrections(db, group_id)
    reviews = db.query(PaymentReview).join(PaymentSource).filter(PaymentSource.buying_group_id == group_id).all()
    items = db.query(Item).join(Order).filter(Order.buying_group_id == group_id).order_by(Item.id).all()
    receipts = [s for s in group_sources(db, group_id) if s.kind == "receipt"]
    balances = {s.id: receipt_balance(db, s) for s in receipts}
    return {"capability": capability(group), "reviews": [review_view(db, r, outstanding) for r in sorted(reviews, key=lambda r: order_key(r.source))],
        "receipts": [{"id": s.id, "external_id": s.external_id, "payload": s.payload, "present": s.present,
            "covered": balances[s.id][2] == 0 and balances[s.id][1] > 0,
            "baseline_amount": str(balances[s.id][0]), "paid_amount": str(balances[s.id][1]), "remaining_amount": str(balances[s.id][2])} for s in receipts],
        "corrections": outstanding, "items": [{"id": i.id, "description": i.description, "quantity": i.quantity,
            "label": item_label(i), "store_order_number": i.order.store_order_number,
            "receipt_id": i.receipt_id, "status": i.status.value, "price_sold": str(i.price_sold),
            "payable_amount": str(item_payable(i)),
            "remaining": str(max(item_payable(i) - item_allocated(db, i.id), ZERO)),
            "order_id": i.order_id} for i in items]}


def locked_review(db, review_id, version):
    review = db.get(PaymentReview, review_id)
    if not review:
        raise HTTPException(404, "Payment review not found")
    lock_group(db, review.source.buying_group_id)
    db.refresh(review)
    db.refresh(review.source)
    if review.payment_id or review.version != version:
        raise HTTPException(409, "Payment review changed or was confirmed. Refresh before saving.")
    return review


def save_review(db, review_id, body):
    review = locked_review(db, review_id, body.version)
    content = body.content.model_dump(mode="json")
    content["manual"] = True
    if body.rebuild_window:
        start, end = when(content.get("window_start")), when(content.get("window_end"))
        if not end or (start and start >= end):
            raise HTTPException(400, "Choose a valid receipt window.")
        content["receipt_ids"] = [s.id for s in group_sources(db, review.source.buying_group_id)
            if s.kind == "receipt" and s.present and not s.payload.get("void") and when(s.payload.get("created_at"))
            and (start is None or when(s.payload["created_at"]) > start) and when(s.payload["created_at"]) <= end
            and receipt_balance(db, s)[2] > 0]
        content["allocations"] = [a for rid in content["receipt_ids"] for a in suggested_allocations(db, review, db.get(PaymentSource, rid))]
    review.content = content
    review.version += 1
    for other in db.query(PaymentReview).join(PaymentSource).filter(PaymentSource.buying_group_id == review.source.buying_group_id,
            PaymentReview.id != review.id, PaymentReview.payment_id.is_(None)).all():
        other.version += 1
    db.commit()
    return group_view(db, review.source.buying_group_id)


def confirm_review(db, review_id, version, fingerprint):
    review = locked_review(db, review_id, version)
    outstanding = outstanding_corrections(db, review.source.buying_group_id)
    content = effective_content(db, review, outstanding)
    if content.get("existing_payment_id"):
        lock_editable_payment(db, content["existing_payment_id"])
    item_ids = sorted({a["item_id"] for a in content.get("allocations", []) + content.get("corrections", [])})
    db.query(Item).filter(Item.id.in_(item_ids)).order_by(Item.id).with_for_update().populate_existing().all()
    if fingerprint != review_token(db, review, content):
        raise HTTPException(409, "Payment evidence or local items changed. Refresh and review before confirming.")
    issues, total = validate_content(db, review, content, outstanding, confirming=True)
    if issues:
        raise HTTPException(409, {"message": "Resolve review issues before confirming.", "issues": issues})
    source = review.source
    p = source.payload
    existing_id = content.get("existing_payment_id")
    if existing_id:
        payment = lock_editable_payment(db, existing_id)
    else:
        payment = Payment(buying_group_id=source.buying_group_id, payment_id=source.external_id,
            payment_requested_at=review_created(review, content), payment_sent_at=when(p.get("sent_at")), payment_bonus=money(content.get("payment_bonus", 0)))
        db.add(payment)
        db.flush()
        grouped = defaultdict(lambda: ZERO)
        for a in content.get("allocations", []):
            grouped[a["item_id"]] += money(a["amount"])
        for item_id, amount in grouped.items():
            db.add(PaymentLineItem(payment_id=payment.id, item_id=item_id, amount=amount))
    for a in content.get("allocations", []):
        receipt = db.get(PaymentSource, a["receipt_source_id"])
        db.add(PaymentCoverage(receipt_source_id=receipt.id, payment_id=payment.id, item_id=a["item_id"],
            amount=money(a["amount"]), quantity=a["quantity"], snapshot=receipt.payload))
    correction_index = {c["receipt_source_id"]: c for c in outstanding}
    for c in content.get("corrections", []):
        original = correction_index[c["receipt_source_id"]]
        db.add(PaymentCorrection(receipt_source_id=c["receipt_source_id"], payment_id=payment.id,
            origin_payment_id=original["origin_payment_id"], item_id=c["item_id"], amount=money(c["amount"]),
            explanation=c["explanation"], source_revision=original["revision"]))
    review.content = {**content, "confirmed_source": {**p, "reviewed_created_at": review_created(review, content).isoformat()}}
    review.payment_id = payment.id
    review.confirmed_revision = source.revision
    review.version += 1
    # Receipt coverage and correction destinations changed: invalidate all other open drafts.
    for other in db.query(PaymentReview).join(PaymentSource).filter(PaymentSource.buying_group_id == source.buying_group_id,
            PaymentReview.id != review.id, PaymentReview.payment_id.is_(None)).all():
        other.version += 1
    db.commit()
    return group_view(db, source.buying_group_id)
