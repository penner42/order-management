"""Read-only signals for reviewing possible duplicate order lines."""
from decimal import Decimal
from difflib import SequenceMatcher
from itertools import combinations
import re

from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app.models import Item, Order, ShipmentItem
from app.models.item import ItemStatus


def _money(value) -> Decimal:
    return Decimal(value or 0)


def _tracking(item: Item) -> set[str]:
    numbers = {(item.receipt_tracking_number or "").strip().casefold()}
    for link in item.shipment_items:
        if link.shipment:
            numbers.add((link.shipment.tracking_number or "").strip().casefold())
            for original in re.findall(r"^Walmart tracking: (.+)$", link.shipment.notes or "", re.M):
                numbers.add(original.strip().casefold())
    return numbers - {""}


def audit_order(order: Order) -> list[dict]:
    # Canceled/refunded units can legitimately coexist with replacements.
    items = [item for item in order.items if item.status not in {
        ItemStatus.CANCELED, ItemStatus.RETURN_REFUNDED,
    }]
    findings = []
    unreviewed = [item for item in items if not item.duplicate_audit_ignored]
    tracking = {item.id: _tracking(item) for item in items}
    for left, right in combinations(items, 2):
        if left.duplicate_audit_ignored and right.duplicate_audit_ignored:
            continue
        shared = tracking[left.id] & tracking[right.id]
        if not shared or left.price_paid is None or right.price_paid is None:
            continue
        if _money(left.price_paid) != _money(right.price_paid):
            continue
        reasons = ["Same tracking number and unit cost"]
        same_quantity = left.quantity == right.quantity
        if same_quantity:
            reasons.append("Same quantity")
        a, b = (re.sub(r"\W+", " ", item.description or "").strip().casefold()
                for item in (left, right))
        similar = bool(a and b and SequenceMatcher(None, a, b).ratio() >= 0.55)
        if similar:
            reasons.append("Similar descriptions" if a != b else "Identical descriptions")
        zero_payout_sibling = any(
            (received.status == ItemStatus.SCANNED or received.scanned_at or received.receipt_id
             or received.payment_line_items)
            and _money(other.price_sold) == 0
            and (_money(received.price_sold) > 0 or received.receipt_id)
            for received, other in ((left, right), (right, left))
        )
        if zero_payout_sibling:
            reasons.append("Scanned or receipt-linked line alongside a zero-payout line")
        reviewed = [item for item in (left, right) if item.duplicate_audit_ignored]
        if reviewed:
            reasons.append(f"Matches reviewed line #{reviewed[0].id}")
        findings.append({
            "item_ids": [item.id for item in (left, right) if not item.duplicate_audit_ignored], "tracking_numbers": sorted(shared),
            "confidence": "likely" if same_quantity and similar else "possible",
            "reasons": reasons,
        })

    payments = order.order_payments
    if unreviewed and len(items) > 1 and payments and all(p.amount is not None for p in payments):
        paid = sum((_money(p.amount) for p in payments), Decimal(0))
        cost = sum(((_money(i.price_paid) + _money(i.shipping) + _money(i.sales_tax)) * i.quantity
                    for i in items), Decimal(0)) - _money(order.order_discount)
        if paid > 0 and cost > paid + Decimal("0.01"):
            findings.append({
                "item_ids": [i.id for i in unreviewed], "tracking_numbers": [],
                "confidence": "possible",
                "reasons": [f"Item costs ({cost:.2f}) exceed recorded order payments ({paid:.2f}) by {cost - paid:.2f}"],
            })
    return findings


def find_duplicate_orders(db, query) -> dict[int, list[dict]]:
    """Audit all matching orders before pagination, loading relationships in batches."""
    multiple_lines = db.query(Item.order_id).group_by(Item.order_id).having(func.count(Item.id) > 1)
    query = query.filter(Order.id.in_(multiple_lines)).options(
        selectinload(Order.items).selectinload(Item.shipment_items).joinedload(ShipmentItem.shipment),
        selectinload(Order.items).selectinload(Item.payment_line_items),
        selectinload(Order.order_payments),
    )
    findings = {}
    for order in query.yield_per(250):
        signals = audit_order(order)
        if signals:
            findings[order.id] = signals
    return findings
