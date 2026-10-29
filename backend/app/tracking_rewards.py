"""Tracking reward persistence, order summaries, and evidence-based line repair."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import joinedload, object_session, selectinload

from app.bg_reward_math import tracking_key, tracking_credits, reward_share, ZERO
from app.models import Item, Order, ReceivingReceiptCache, ShipmentItem, TrackingReward
from app.utils.receiving_cache import decode, provider_identity


def _utc(value):
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def receiving_credits(rows, rewards):
    from app.group_payment_providers import date
    return tracking_credits([{
        "receipt_id": r.receipt_id, "trackings": [t for t in (r.tracking_id, r.alt_tracking_id) if t],
        "sort_date": date(r.created_dt).isoformat() if r.created_dt else "", "void": r.void,
    } for r in rows], rewards)


def save_credits(db, group, credits, *, observed_at=None):
    """Caller holds the group's publication lock and owns the transaction."""
    identity = provider_identity(group)
    observed_at = observed_at or datetime.now(timezone.utc)
    existing = db.query(TrackingReward).filter_by(buying_group_id=group.id, provider_identity=identity).all()
    saved = []
    for credit in {tuple(c["aliases"]): c for c in credits.values()}.values():
        aliases = set(credit["aliases"])
        matches = [r for r in existing if aliases & set(r.aliases)]
        if any(r.observed_at and _utc(r.observed_at) > _utc(observed_at) for r in matches):
            saved.extend(matches)
            continue
        # A later complete fetch may reveal that two previously separate
        # identifiers are aliases. No item/payment rows reference these IDs.
        matches.sort(key=lambda r: r.id or 0)
        for duplicate in matches[1:]:
            aliases.update(duplicate.aliases)
            existing.remove(duplicate)
            db.delete(duplicate)
        row = matches[0] if matches else TrackingReward(
            buying_group_id=group.id, provider_identity=identity,
            tracking_key=tracking_key(credit["tracking_number"]))
        row.tracking_number = credit["tracking_number"]
        row.aliases = sorted(aliases | set(row.aliases or []))
        row.amount = Decimal(credit["amount"])
        row.receipt_id = credit["receipt_id"]
        row.observed_at = observed_at
        if not matches:
            db.add(row)
            existing.append(row)
        saved.append(row)
    db.flush()
    return saved


def saved_credit_values(credits, records):
    """Use newer persisted values when an older receipt snapshot is replayed."""
    by_alias = {alias: r for r in records for alias in r.aliases}
    result = {}
    for rid, credit in credits.items():
        row = next((by_alias[a] for a in credit["aliases"] if a in by_alias), None)
        result[rid] = ({"tracking_number": row.tracking_number, "aliases": row.aliases,
                        "amount": str(row.amount), "receipt_id": row.receipt_id} if row else credit)
    return result


def cached_evidence(db, group):
    cache = db.get(ReceivingReceiptCache, group.id)
    if not cache or cache.provider_identity != provider_identity(group):
        return {}, {}, ["No receipt cache for the current provider account"]
    requests = cache.payload.get("requests", {})
    rewards = requests.get("rewards", {})
    rows = requests.get("rows", {})
    if "value" not in rewards or "value" not in rows:
        return {}, {}, ["Cached tracking rewards or receipts unavailable"]
    rows = decode(rows["value"])
    credits = receiving_credits(rows, decode(rewards["value"]))
    quantities = {}
    for row in rows:
        detail = requests.get("detail:" + str(row.raw.get("key") or ""), {})
        if row.void or "value" not in detail:
            continue
        detail = decode(detail["value"])
        if detail.get("receipt_id") != row.receipt_id:
            continue
        quantities[row.receipt_id] = sum(line["qty"] for line in detail["received_items"] if line["verified"])
    return credits, quantities, []


def line_allocations(db, group, credits, quantities):
    """Return proposed shares, leaving missing/ambiguous evidence unchanged."""
    items = db.query(Item).join(Order).filter(Order.buying_group_id == group.id).order_by(Item.id).all()
    by_receipt = defaultdict(list)
    issues = []
    for item in items:
        ids = [s.strip() for s in (item.receipt_id or "").replace(";", ",").split(",") if s.strip()]
        if len(ids) == 1:
            by_receipt[ids[0]].append(item)
        elif ids and item.reward is not None:
            issues.append({"item_id": item.id, "reason": "Multiple receipt IDs require review"})
        elif item.reward is not None and not ids:
            issues.append({"item_id": item.id, "reason": "Saved reward has no receipt assignment"})
    proposals = []
    for rid, lines in by_receipt.items():
        credit = credits.get(rid)
        if credit is None:
            if any(i.reward is not None for i in lines):
                issues.append({"receipt_id": rid, "reason": "Tracking reward evidence unavailable"})
            continue
        denominator = quantities.get(rid)
        total_qty = sum(i.quantity for i in lines)
        if not denominator or total_qty > denominator:
            issues.append({"receipt_id": rid, "order_ids": sorted({i.order_id for i in lines}),
                           "reason": "Confirmed quantity missing or over-assigned"})
            continue
        # Receipt identity and tracking must both agree when local tracking is known.
        if any(i.receipt_tracking_number and tracking_key(i.receipt_tracking_number) not in credit["aliases"] for i in lines):
            issues.append({"receipt_id": rid, "reason": "Receipt tracking differs from reward evidence"})
            continue
        amount = Decimal(credit["amount"]) if credit["receipt_id"] == rid else ZERO
        used = 0
        for item in lines:
            share = reward_share(amount, used, item.quantity, denominator)
            used += item.quantity
            if item.reward != share:
                proposals.append((item, share))
        if used < denominator and amount:
            issues.append({"receipt_id": rid, "order_ids": sorted({i.order_id for i in lines}),
                           "reason": "Reward share reserved for unmatched confirmed units",
                           "unresolved_reward": str(amount - reward_share(amount, 0, used, denominator))})
    return proposals, issues


def sync_payment_rewards(db, group, receipts):
    credits = {}
    for receipt in receipts:
        for detail in receipt.payload.get("details", []):
            if detail.get("tracking_reward"):
                credits[receipt.external_id] = detail["tracking_reward"]
    if not credits:
        return
    save_credits(db, group, credits)
    _, quantities, _ = cached_evidence(db, group)
    proposals, _ = line_allocations(db, group, credits, quantities)
    for item, share in proposals:
        item.reward = share


def order_reward_summaries(orders):
    """Fetch associations before filtering order lines; totals are never page sums."""
    if not orders:
        return
    db = object_session(orders[0])
    if db is None:
        return
    group_ids = {o.buying_group_id for o in orders if o.buying_group and o.buying_group.api_framework == "buyinggroup"}
    identities = {o.buying_group_id: provider_identity(o.buying_group) for o in orders if o.buying_group_id in group_ids}
    rewards = db.query(TrackingReward).filter(TrackingReward.buying_group_id.in_(group_ids)).all() if group_ids else []
    rewards = [r for r in rewards if r.provider_identity == identities.get(r.buying_group_id)]
    by_alias = defaultdict(list)
    for reward in rewards:
        for alias in reward.aliases:
            by_alias[reward.buying_group_id, alias].append(reward)
    all_items = db.query(Item).join(Order).filter(Order.buying_group_id.in_(group_ids)).options(
        joinedload(Item.order).joinedload(Order.buying_group),
        selectinload(Item.shipment_items).joinedload(ShipmentItem.shipment),
    ).all() if rewards else []
    associated = defaultdict(list)
    for item in all_items:
        aliases = {tracking_key(item.receipt_tracking_number)}
        aliases.update(tracking_key(si.shipment.tracking_number) for si in item.shipment_items if si.shipment)
        linked_rewards = {r.id for alias in aliases for r in by_alias[item.order.buying_group_id, alias]}
        for reward_id in linked_rewards:
            associated[reward_id].append(item)
    for order in orders:
        summaries = []
        for reward in rewards:
            linked = associated[reward.id]
            item_ids = [i.id for i in linked if i.order_id == order.id]
            if item_ids:
                summaries.append({"id": reward.id, "tracking_number": reward.tracking_number,
                    "amount": reward.amount, "item_ids": item_ids,
                    "shared_order_count": len({i.order_id for i in linked})})
        order.tracking_rewards = summaries
