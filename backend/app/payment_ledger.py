"""Source-owned payments with independently reconciled local allocations."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, object_session

from app.models import (BuyingGroup, Item, Order, Payment, PaymentLineItem,
    PaymentSource, PaymentReview, PaymentCoverage, PaymentCorrection, MonarchPaymentReceipt)
from app.payment_totals import item_allocated, payment_amount

CUTOFF = datetime(2026, 1, 1, tzinfo=timezone.utc)
ZERO = Decimal("0.00")


class LedgerAllocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receipt_source_id: int
    item_id: int
    quantity: int = Field(gt=0, strict=True)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2, allow_inf_nan=False)


class AllocationEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    allocations: list[LedgerAllocation] = Field(default_factory=list, max_length=2000)
    historical_amount: Decimal = Field(default=ZERO, ge=0, max_digits=12, decimal_places=2, allow_inf_nan=False)
    adjustment_amount: Decimal = Field(default=ZERO, max_digits=12, decimal_places=2, allow_inf_nan=False)
    note: str = Field(min_length=1, max_length=2000)


class AllocationReset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int


def breakdown(payment):
    details = payment.allocation_details or {}
    linked = sum((Decimal(li.amount) for li in payment.line_items), ZERO)
    corrections = sum((Decimal(c.amount) for c in payment.corrections), ZERO)
    historical = Decimal(str(details.get("historical_amount", 0)))
    adjustment = Decimal(str(details.get("adjustment_amount", payment.payment_bonus or 0)))
    return {"linked_amount": str(linked), "correction_amount": str(corrections),
        "historical_amount": str(historical), "adjustment_amount": str(adjustment),
        "unresolved_amount": str(payment_amount(payment) - linked - corrections - historical - adjustment)}


def receipt_conflict(payment):
    link = payment.monarch_receipt_link
    if not link:
        return False
    db = object_session(payment)
    evidence = link.confirmation.evidence
    from app.models import MonarchDeposit
    deposit = db.get(MonarchDeposit, link.confirmation.deposit_id)
    return (not deposit or deposit.pending or deposit.revision != evidence["deposit"]["revision"]
        or any(not (p := db.get(Payment, row["id"])) or payment_amount(p) != Decimal(row["amount"])
               for row in evidence["payments"]))


def _replace_allocations(db, payment, allocations):
    # Keep receipt baselines for installment/revision accounting.
    snapshots = {c.receipt_source_id: c.snapshot for c in db.query(PaymentCoverage).filter_by(payment_id=payment.id)}
    db.query(PaymentCoverage).filter_by(payment_id=payment.id).delete(synchronize_session=False)
    payment.line_items.clear()
    db.flush()
    grouped = defaultdict(lambda: ZERO)
    for a in allocations:
        receipt = db.get(PaymentSource, a["receipt_source_id"])
        grouped[a["item_id"]] += Decimal(str(a["amount"]))
        db.add(PaymentCoverage(receipt_source_id=receipt.id, payment_id=payment.id,
            item_id=a["item_id"], amount=Decimal(str(a["amount"])), quantity=a["quantity"],
            snapshot=snapshots.get(receipt.id, receipt.payload)))
    for item_id, value in grouped.items():
        payment.line_items.append(PaymentLineItem(item_id=item_id, amount=value))
    db.flush()


def _validate_allocations(db, payment, allocations):
    from app.payment_review import item_payable
    if len({(a["receipt_source_id"], a["item_id"]) for a in allocations}) != len(allocations):
        raise HTTPException(400, "Each receipt/item allocation must be unique.")
    amounts = defaultdict(lambda: ZERO)
    quantities = {}
    receipts = defaultdict(list)
    review = db.query(PaymentReview).filter_by(payment_id=payment.id).one()
    for a in allocations:
        receipt = db.get(PaymentSource, a["receipt_source_id"])
        item = db.get(Item, a["item_id"])
        if (not receipt or receipt.kind != "receipt" or receipt.buying_group_id != payment.buying_group_id
            or receipt.provider != review.source.provider or not receipt.present or receipt.payload.get("void")):
            raise HTTPException(400, "Choose a current receipt from this payment's buying group and provider.")
        if not item or item.order.buying_group_id != payment.buying_group_id:
            raise HTTPException(400, "Choose items from this payment's buying group.")
        if item.status.value in {"purchased", "shipped", "submitted", "canceled", "needs_review"}:
            raise HTTPException(400, "Allocated items must be received.")
        if a["quantity"] > item.quantity or Decimal(str(a["amount"])) <= 0:
            raise HTTPException(400, "Invalid item quantity or amount.")
        quantities[(receipt.id, item.id)] = a["quantity"]
        amounts[item.id] += Decimal(str(a["amount"]))
        receipts[receipt.id].append(a)
    for rid, lines in receipts.items():
        receipt = db.get(PaymentSource, rid)
        prior = db.query(PaymentCoverage).filter(PaymentCoverage.receipt_source_id == rid,
            PaymentCoverage.payment_id != payment.id).all()
        other = sum((Decimal(c.amount) for c in prior), ZERO)
        # Revisions are kept separate from installment principal.
        first_coverage = db.query(PaymentCoverage).filter_by(receipt_source_id=rid).order_by(PaymentCoverage.id).first()
        baseline = Decimal(str(first_coverage.snapshot["amount"])) if first_coverage else Decimal(str(receipt.payload["amount"]))
        if sum((Decimal(str(a["amount"])) for a in lines), ZERO) + other > baseline:
            raise HTTPException(400, "Allocations exceed the receipt balance.")
        if receipt.payload.get("quantity") is not None and sum(a["quantity"] for a in lines) > receipt.payload["quantity"]:
            raise HTTPException(400, "Allocations exceed the receipt quantity.")
        previous = {c.item_id: c.quantity for c in prior}
        if previous and any(a["item_id"] not in previous or previous[a["item_id"]] != a["quantity"] for a in lines):
            raise HTTPException(400, "Installments must preserve the receipt's item quantities.")
    for iid, value in amounts.items():
        item = db.get(Item, iid)
        existing = {(c.receipt_source_id, c.item_id): c.quantity for c in db.query(PaymentCoverage).filter(
            PaymentCoverage.item_id == iid, PaymentCoverage.payment_id != payment.id)}
        existing.update({key: qty for key, qty in quantities.items() if key[1] == iid})
        if sum(existing.values()) > item.quantity:
            raise HTTPException(400, "Item quantity is already allocated to another receipt.")
        # Signed corrections on this payment also consume or release local capacity.
        correction = sum((Decimal(c.amount) for c in payment.corrections if c.item_id == iid), ZERO)
        if value + correction > item_payable(item) - item_allocated(db, iid, payment.id):
            raise HTTPException(400, "Allocations exceed the item's remaining payout.")


def _automatic_allocations(db, review):
    from app.payment_review import receipt_matches, money
    payment, source = db.get(Payment, review.payment_id), review.source
    payload = source.payload
    if not source.present or payload.get("void") or not payload.get("status_known", True):
        return []
    receipts = [db.get(PaymentSource, rid) for rid in review.content.get("receipt_ids", [])]
    receipts = [r for r in receipts if r and r.present and not r.payload.get("void")]
    shares = payload.get("receipt_amounts")
    # Provider warnings can indicate ambiguous cash coverage. Do not promote a time window to evidence.
    allocation_warnings = [w for w in payload.get("warnings", []) if not w.startswith("Archive ")]
    if allocation_warnings or (payload.get("receipt_ids") is None and not payload.get("history_complete", True)) or (shares is None and payload.get("receipt_ids") is None and
        sum((money(r.payload["amount"]) for r in receipts), ZERO) != money(payload["amount"])):
        return []
    result = []
    for receipt in receipts:
        if not receipt.payload.get("verified", True):
            continue
        matches = receipt_matches(db, receipt)
        qty = receipt.payload.get("quantity")
        if not matches or qty is None or sum(a["quantity"] for a in matches) != qty:
            continue
        target = money(shares[receipt.external_id]) if shares and receipt.external_id in shares else money(receipt.payload["amount"])
        total = sum((money(a["amount"]) for a in matches), ZERO)
        # A local price mismatch needs review, rather than silently changing item earnings.
        if total <= 0 or (shares is None and total != target) or target > total:
            continue
        used = ZERO
        proposal = []
        for n, a in enumerate(matches):
            value = target - used if n == len(matches)-1 else (target * money(a["amount"]) / total).quantize(Decimal("0.01"))
            used += value
            if value > 0:
                proposal.append({**a, "amount": str(value)})
        try:
            _validate_allocations(db, payment, result + proposal)
        except HTTPException:
            continue
        corrections = sum((Decimal(c.amount) for c in payment.corrections), ZERO)
        if sum((money(a["amount"]) for a in result + proposal), ZERO) + corrections <= payment_amount(payment):
            result.extend(proposal)
    return result


def reconcile_group(db, group_id):
    from app.payment_review import lock_group
    db.flush()
    lock_group(db, group_id)
    db.info["payment_sources_cache"] = {group_id: db.query(PaymentSource).filter_by(buying_group_id=group_id).populate_existing().all()}
    db.info["payment_items_cache"] = {group_id: db.query(Item).join(Order).filter(Order.buying_group_id == group_id).populate_existing().all()}
    try:
        return _reconcile_group(db, group_id)
    finally:
        db.info.pop("payment_sources_cache", None)
        db.info.pop("payment_items_cache", None)


def _reconcile_group(db, group_id):
    from app.payment_review import lock_group, order_key, when
    lock_group(db, group_id)
    reviews = db.query(PaymentReview).join(PaymentSource).filter(PaymentSource.buying_group_id == group_id).all()
    linked = {r.payment_id for r in reviews if r.payment_id}
    for review in sorted(reviews, key=lambda r: order_key(r.source)):
        source, p = review.source, review.source.payload
        dates = [d for k in ("created_at", "sent_at") if (d := when(p.get(k)))]
        active = any(d >= CUTOFF for d in dates)
        if review.payment_id is None and (not active or p.get("currency") != "USD"):
            continue
        payment = db.get(Payment, review.payment_id) if review.payment_id else None
        if payment is None:
            # Require a unique reference AND total; never merge by amount alone.
            candidates = [q for q in db.query(Payment).filter_by(buying_group_id=group_id, ledger_state="legacy")
                if q.id not in linked and q.payment_id == p["external_id"] and payment_amount(q) == Decimal(p["amount"])]
            if sum(r.source.payload["external_id"] == p["external_id"] and
                   Decimal(r.source.payload["amount"]) == Decimal(p["amount"])
                   for r in reviews) != 1:
                candidates = []
            if len(candidates) == 1:
                payment = candidates[0]
                payment.allocation_manual = True
            else:
                payment = Payment(buying_group_id=group_id, payment_id=p["external_id"],
                    payment_requested_at=when(p.get("created_at")) or when(p.get("sent_at")),
                    allocation_manual=False, allocation_details={})
                db.add(payment)
                db.flush()
            review.payment_id = payment.id
            linked.add(payment.id)
        db.flush()
        db.query(Payment).filter_by(id=payment.id).with_for_update().populate_existing().one()
        if (payment.allocation_details or {}).get("source_revision") != source.revision:
            payment.allocation_version += 1
            payment.allocation_details = {**(payment.allocation_details or {}), "source_revision": source.revision}
        receipt_ids = set(review.content.get("receipt_ids", [])) | {c.receipt_source_id for c in db.query(PaymentCoverage).filter_by(payment_id=payment.id)}
        receipt_revisions = {str(rid): [r.revision, r.present] for rid in receipt_ids if (r := db.get(PaymentSource, rid))}
        details = dict(payment.allocation_details or {})
        if details.get("receipt_revisions_seen") != receipt_revisions:
            payment.allocation_version += 1
            details["receipt_revisions_seen"] = receipt_revisions
        if "receipt_revisions_baseline" not in details:
            details["receipt_revisions_baseline"] = receipt_revisions
        payment.allocation_details = details
        payment.source_amount = Decimal(p["amount"])
        payment.ledger_state = "imported" if active else "context"
        payment.payment_id = p["external_id"]
        payment.payment_requested_at = when(p.get("created_at")) or when(p.get("sent_at")) or payment.payment_requested_at
        payment.payment_sent_at = when(p.get("sent_at"))
        issues = list(p.get("warnings", []))
        if not source.present:
            issues.append("Payment is missing from source history.")
        if p.get("void"):
            issues.append("Payment is void in the source.")
        if not p.get("status_known", True):
            issues.append("Unknown source status.")
        if not p.get("history_complete", True):
            issues.append("Source history is incomplete.")
        explicit = p.get("receipt_ids")
        if explicit is not None:
            available = {r.external_id for r in db.info.get("payment_sources_cache", {}).get(group_id, [])
                if r.kind == "receipt" and r.provider == source.provider and r.present}
            if any(rid not in available for rid in explicit):
                issues.append("Source references receipts absent from the available history.")
        if not p.get("created_at"):
            issues.append("Source request date is unavailable; sent date is used for display.")
        if payment.allocation_manual:
            if review.confirmed_revision is None:
                review.confirmed_revision = source.revision
            if review.confirmed_revision and review.confirmed_revision != source.revision:
                issues.append("Source changed after allocation edits; verify retained allocations.")
            if details.get("receipt_revisions_baseline") != receipt_revisions:
                issues.append("Receipt evidence changed after allocation edits; verify retained allocations.")
        elif source.present and not p.get("void"):
            if p.get("history_complete", True):
                _carry_receipt_corrections(db, review, reviews)
            coverage = db.query(PaymentCoverage).filter_by(payment_id=payment.id).all()
            revised = (bool(coverage) and not p.get("history_complete", True)) or any((r := db.get(PaymentSource, c.receipt_source_id)) and
                (not r.present or r.payload.get("void") or Decimal(str(r.payload["amount"])) != Decimal(str(c.snapshot["amount"])))
                for c in coverage)
            allocations = (payment.allocation_details or {}).get("allocations", []) if revised else _automatic_allocations(db, review)
            if any(not db.get(PaymentSource, c.receipt_source_id).present for c in coverage):
                issues.append("Covered receipt is missing; original coverage was preserved.")
            previous = (payment.allocation_details or {}).get("allocations", [])
            if allocations != previous:
                _replace_allocations(db, payment, allocations)
                payment.allocation_details = {**(payment.allocation_details or {}), "allocations": allocations}
                payment.allocation_version += 1
            review.confirmed_revision = source.revision
        if Decimal(breakdown(payment)["unresolved_amount"]) != 0:
            issues.append("Item allocations do not yet reconcile to the source payment.")
        payment.sync_issues = list(dict.fromkeys(issues))
        db.flush()
    from app.payment_review import outstanding_corrections
    for correction in outstanding_corrections(db, group_id):
        if Decimal(correction["amount"]):
            origin = db.get(Payment, correction["origin_payment_id"])
            issue = f"Pending receipt correction {correction['external_id']}: ${correction['amount']}. A later payment or manual allocation review is needed."
            origin.sync_issues = list(origin.sync_issues or []) + [issue]


def _carry_receipt_corrections(db, review, reviews):
    from app.payment_review import outstanding_corrections, review_created, when, order_key
    payment = db.get(Payment, review.payment_id)
    for correction in outstanding_corrections(db, review.source.buying_group_id):
        if not correction["present"] or not Decimal(correction["amount"]):
            continue
        eligible = [r for r in reviews if r.source.provider == correction["provider"] and r.source.present
            and not r.source.payload.get("void") and review_created(r, r.content)
            and review_created(r, r.content) > when(correction["origin_requested_at"])]
        target = min(eligible, key=lambda r: order_key(r.source), default=None)
        if target is None or target.id != review.id:
            continue
        delta = Decimal(correction["amount"])
        # Keep unexplained monetary differences visible instead of forcing a balancing correction.
        if delta > 0 and delta > Decimal(breakdown(payment)["unresolved_amount"]):
            continue
        weights = correction["items"]
        total = sum((Decimal(w["amount"]) for w in weights), ZERO)
        if total <= 0:
            continue
        used = ZERO
        for n, weight in enumerate(weights):
            value = delta-used if n == len(weights)-1 else (delta*Decimal(weight["amount"])/total).quantize(Decimal('0.01'))
            used += value
            if value:
                payment.corrections.append(PaymentCorrection(receipt_source_id=correction["receipt_source_id"],
                    origin_payment_id=correction["origin_payment_id"], item_id=weight["item_id"], amount=value,
                    explanation="Receipt value changed in group history", source_revision=correction["revision"]))
        payment.allocation_version += 1
        db.flush()


def adjust(db, payment_id, body, *, reset=False):
    from app.payment_review import lock_group
    payment = db.get(Payment, payment_id)
    if not payment or payment.source_amount is None:
        raise HTTPException(404, "Imported payment not found")
    lock_group(db, payment.buying_group_id)
    db.refresh(payment)
    db.query(Payment).filter_by(id=payment_id).with_for_update().one()
    if payment.allocation_version != body.version:
        raise HTTPException(409, "Payment changed. Reload before adjusting allocations.")
    try:
        db.query(Item).join(Order).filter(Order.buying_group_id == payment.buying_group_id).order_by(Item.id).with_for_update(nowait=True, of=Item).all()
    except OperationalError as exc:
        if getattr(exc.orig, "pgcode", None) == "55P03":
            raise HTTPException(409, "Order items are changing. Retry the adjustment after receiving finishes.") from None
        raise
    old = dict(payment.allocation_details or {})
    history = list(old.get("history", []))
    history.append({"at": datetime.now(timezone.utc).isoformat(), "previous": {k: v for k, v in old.items() if k != "history"}, "reset": reset})
    if reset:
        payment.allocation_manual = False
        payment.allocation_details = {"history": history}
        from app.payment_review import group_sources, order_key, in_window
        review = db.query(PaymentReview).filter_by(payment_id=payment.id).one()
        sources = group_sources(db, payment.buying_group_id)
        ordered = sorted([s for s in sources if s.kind == "payment" and s.present and not s.payload.get("void")], key=order_key)
        previous = next((s for s in reversed(ordered) if order_key(s) < order_key(review.source)), None)
        explicit = review.source.payload.get("receipt_ids")
        ids = [s.id for s in sources if s.kind == "receipt" and s.present and not s.payload.get("void")
               and (s.external_id in explicit if explicit is not None else in_window(s, previous, review.source))]
        review.content = {**review.content, "manual": False, "receipt_ids": ids}
    else:
        if not body.note.strip():
            raise HTTPException(400, "Explain the allocation adjustment.")
        allocations = [a.model_dump(mode="json") for a in body.allocations]
        _validate_allocations(db, payment, allocations)
        total = sum((Decimal(a["amount"]) for a in allocations), ZERO) + body.historical_amount + body.adjustment_amount
        total += sum((Decimal(c.amount) for c in payment.corrections), ZERO)
        if total < 0 or total > payment_amount(payment):
            raise HTTPException(400, "Allocated, historical, and adjustment amounts must fit within the source total.")
        _replace_allocations(db, payment, allocations)
        payment.allocation_manual = True
        payment.allocation_details = {**body.model_dump(mode="json", exclude={"version"}), "history": history}
        review = db.query(PaymentReview).filter_by(payment_id=payment.id).one()
        review.confirmed_revision = review.source.revision
    payment.allocation_version += 1
    reconcile_group(db, payment.buying_group_id)
    db.commit()
    db.refresh(payment)
    return payment


def sync_group(db, group_id):
    """Network reads before locks; every caller uses the same snapshot/ledger transaction."""
    from app.payment_providers import provider_for
    from app.payment_review import refresh, capability
    group = db.get(BuyingGroup, group_id)
    if not group:
        raise HTTPException(404, "Buying group not found")
    if not capability(group)["ready"]:
        raise HTTPException(400, capability(group)["reason"])
    provider = provider_for(group)
    fields = ("api_framework", "base_url", "api_email", "api_password", "api_username", "bearer_token", "api_user_id")
    signature = tuple(getattr(group, k) for k in fields)
    before = [(s.id, s.revision, s.present) for s in db.query(PaymentSource).filter_by(buying_group_id=group_id).order_by(PaymentSource.id)]
    try:
        result = provider.fetch(group)
    except Exception:
        db.rollback()
        group = db.get(BuyingGroup, group_id)
        group.payment_sync_last_run_at = datetime.now(timezone.utc)
        group.payment_sync_error = "Could not read complete payment history. Previous evidence was preserved."
        db.commit()
        raise HTTPException(502, group.payment_sync_error) from None
    db.rollback()
    from app.payment_review import lock_group
    group = lock_group(db, group_id)
    after = [(s.id, s.revision, s.present) for s in db.query(PaymentSource).filter_by(buying_group_id=group_id).order_by(PaymentSource.id)]
    if before != after or signature != tuple(getattr(group, k) for k in fields):
        raise HTTPException(409, "Payment source or group configuration changed during sync. Retry.")
    try:
        return refresh(db, group_id, result, provider.key, automate=True)
    except HTTPException:
        db.rollback()
        group = db.get(BuyingGroup, group_id)
        group.payment_sync_last_run_at = datetime.now(timezone.utc)
        group.payment_sync_error = "Payment sync did not return consistent evidence. Previous evidence was preserved."
        db.commit()
        raise


@event.listens_for(Session, "before_commit")
def rematch_changed_purchases(db):
    if db.info.get("payment_rematching"):
        return
    changed = [row for row in db.new | db.dirty | db.deleted if isinstance(row, (Item, Order))]
    pending = db.info.pop("payment_rematch_groups", set())
    if not changed and not pending:
        return
    groups = set(pending)
    for row in changed:
        order = row if isinstance(row, Order) else row.order or db.get(Order, row.order_id)
        if order:
            groups.add(order.buying_group_id)
    db.info["payment_rematching"] = True
    try:
        db.flush()
        for group_id in sorted(g for g in groups if g is not None):
            if db.query(Payment).filter_by(buying_group_id=group_id, ledger_state="imported").first():
                reconcile_group(db, group_id)
    finally:
        db.info.pop("payment_rematch_groups", None)
        db.info.pop("payment_rematching", None)


@event.listens_for(Session, "after_flush")
def remember_changed_purchases(db, context):
    if db.info.get("payment_rematching"):
        return
    groups = db.info.setdefault("payment_rematch_groups", set())
    for row in db.new | db.dirty | db.deleted:
        if isinstance(row, Order):
            groups.add(row.buying_group_id)
        elif isinstance(row, Item):
            order = row.order or db.get(Order, row.order_id)
            if order:
                groups.add(order.buying_group_id)


@event.listens_for(Session, "after_rollback")
def clear_pending_rematches(db):
    db.info.pop("payment_rematch_groups", None)
