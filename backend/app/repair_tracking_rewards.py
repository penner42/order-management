"""Preview/apply a BG reward repair without changing confirmed payments.

Run inside the backend container: python -m app.repair_tracking_rewards [--apply]
"""
import argparse
import json
from decimal import Decimal

from app.database import SessionLocal
from app.models import (BuyingGroup, Payment, PaymentLineItem, PaymentSource, PaymentReview, ReceivingReceiptCache,
                        PaymentCoverage, PaymentCorrection, MonarchPaymentReceipt, TrackingReward)
from app.bg_reward_math import tracking_key, ZERO
from app.tracking_rewards import cached_evidence, line_allocations, save_credits, saved_credit_values
from app.utils.receiving_cache import provider_identity


def payment_snapshot(db):
    """Include allocation and bank-confirmation evidence, not just payment totals."""
    return {model.__tablename__: [tuple(getattr(row, c.key) for c in model.__table__.columns)
            for row in db.query(model).order_by(*model.__table__.primary_key.columns).all()]
            for model in (Payment, PaymentLineItem, PaymentCoverage, PaymentCorrection, MonarchPaymentReceipt)}


def payment_checked_credits(db, group, credits):
    """Honor the credited receipt in existing cash evidence; flag disagreement."""
    sources = db.query(PaymentSource).filter_by(buying_group_id=group.id, provider="buyinggroup", kind="receipt", present=True).all()
    checked = dict(credits)
    issues = []
    for aliases, credit in {tuple(c["aliases"]): c for c in credits.values()}.items():
        matching = [s for s in sources if set(aliases) & {tracking_key(t) for t in s.payload.get("tracking_numbers", [])}]
        cash = []
        for source in matching:
            details = [d for d in source.payload.get("details", []) if "cash_reward" in d]
            if details:
                value = sum((Decimal(d["cash_reward"]) for d in details), ZERO)
                if value:
                    cash.append((source.external_id, value))
        if not matching:
            continue  # The account-matched receiving snapshot is sufficient.
        if len(cash) == 1 and cash[0][1] == Decimal(credit["amount"]):
            replacement = {**credit, "receipt_id": cash[0][0]}
            for rid, original in credits.items():
                if original is credit:
                    checked[rid] = replacement
        elif cash or Decimal(credit["amount"]):
            issues.append({"tracking_number": credit["tracking_number"], "reason": "Cached tracking reward conflicts with receipt cash evidence"})
            checked = {rid: c for rid, c in checked.items() if not set(c["aliases"]) & set(aliases)}
    return checked, issues


def repair(db, *, apply=False):
    from app.payment_review import effective_content, outstanding_corrections, validate_content, item_payable
    from app.payment_totals import item_allocated
    from app.models import Item

    before = payment_snapshot(db)
    previous_guard = db.info.get("payment_rematching")
    db.info["payment_rematching"] = True
    report = {"mode": "apply" if apply else "preview", "groups": [], "payment_discrepancies": []}
    try:
        for group in db.query(BuyingGroup).filter_by(api_framework="buyinggroup").order_by(BuyingGroup.id).with_for_update():
            entry = {"buying_group_id": group.id, "tracking_records_created": 0, "tracking_records_updated": 0,
                     "order_ids": [], "line_changes": [], "reviews_updated": [], "manual_review_issues": [], "unresolved": []}
            report["groups"].append(entry)
            try:
                credits, quantities, issues = cached_evidence(db, group)
            except (ValueError, KeyError, TypeError) as exc:
                entry["unresolved"].append({"reason": str(exc)})
                continue
            entry["unresolved"].extend({"reason": issue} for issue in issues)
            credits, conflicts = payment_checked_credits(db, group, credits)
            entry["unresolved"].extend(conflicts)
            existing = db.query(TrackingReward).filter_by(buying_group_id=group.id, provider_identity=provider_identity(group)).all()
            old = {r.id: (r.tracking_number, r.aliases, r.amount, r.receipt_id) for r in existing}
            cache = db.get(ReceivingReceiptCache, group.id)
            rows = save_credits(db, group, credits, observed_at=cache.fetched_at if cache else None)
            credits = saved_credit_values(credits, rows)
            entry["tracking_records_created"] = sum(r.id not in old for r in rows)
            entry["tracking_records_updated"] = sum(r.id in old and old[r.id] != (r.tracking_number, r.aliases, r.amount, r.receipt_id) for r in rows)
            proposals, issues = line_allocations(db, group, credits, quantities)
            entry["unresolved"].extend(issues)
            for item, share in proposals:
                entry["line_changes"].append({"item_id": item.id, "order_id": item.order_id,
                    "old_reward": str(item.reward) if item.reward is not None else None, "new_reward": str(share)})
                item.reward = share
            entry["order_ids"] = sorted({item.order_id for item, _ in proposals})
            db.flush()
            outstanding = outstanding_corrections(db, group.id)
            for review in db.query(PaymentReview).join(PaymentSource).filter(
                PaymentSource.buying_group_id == group.id, PaymentReview.payment_id.is_(None)).all():
                if review.content.get("manual"):
                    issues, _ = validate_content(db, review, review.content, outstanding)
                    if issues:
                        entry["manual_review_issues"].append({"review_id": review.id, "issues": issues})
                else:
                    content = effective_content(db, review, outstanding)
                    if content != review.content:
                        review.content = content
                        review.version += 1
                        entry["reviews_updated"].append(review.id)
            for item in db.query(Item).join(Item.order).filter_by(buying_group_id=group.id):
                allocated = item_allocated(db, item.id)
                if allocated > item_payable(item):
                    report["payment_discrepancies"].append({"item_id": item.id, "order_id": item.order_id,
                        "allocated": str(allocated), "corrected_payable": str(item_payable(item))})
        db.flush()
        if before != payment_snapshot(db):
            raise RuntimeError("Repair attempted to change payment or receipt confirmation records")
        if apply:
            db.commit()
        else:
            db.rollback()
        return report
    except Exception:
        db.rollback()
        raise
    finally:
        db.info.pop("payment_rematch_groups", None)
        if previous_guard is None:
            db.info.pop("payment_rematching", None)
        else:
            db.info["payment_rematching"] = previous_guard


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the repair (default: preview and roll back)")
    args = parser.parse_args()
    with SessionLocal() as db:
        print(json.dumps(repair(db, apply=args.apply), indent=2))


if __name__ == "__main__":
    main()
