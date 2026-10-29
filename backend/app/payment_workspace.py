"""Batched evidence for a single payment's allocation workspace."""
from collections import defaultdict
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import joinedload

from app.models import (Item, Order, Payment, PaymentLineItem, PaymentCorrection,
                        PaymentCoverage, PaymentSource, PaymentReview, ShipmentItem)
from app.payment_ledger import breakdown
from app.payment_review import item_payable
from app.payment_totals import payment_amount

ZERO = Decimal('0')


def linking_workspace(db, payment_id):
    payment = db.query(Payment).options(joinedload(Payment.buying_group),
        joinedload(Payment.line_items), joinedload(Payment.corrections)).filter_by(id=payment_id).first()
    if payment is None:
        raise HTTPException(404, 'Payment not found')
    review = db.query(PaymentReview).options(joinedload(PaymentReview.source)).filter_by(payment_id=payment_id).first()
    if payment.ledger_state != 'imported' or not review:
        raise HTTPException(409, 'Only imported payments support allocation linking.')
    group_id = payment.buying_group_id
    receipts = db.query(PaymentSource).filter_by(buying_group_id=group_id, kind='receipt',
        provider=review.source.provider).order_by(PaymentSource.id).all()
    coverage = db.query(PaymentCoverage).join(PaymentSource,
        PaymentCoverage.receipt_source_id == PaymentSource.id).filter(
        PaymentSource.buying_group_id == group_id).order_by(PaymentCoverage.id).all()
    items = db.query(Item).join(Order).filter(Order.buying_group_id == group_id).options(
        joinedload(Item.order).joinedload(Order.store),
        joinedload(Item.order).joinedload(Order.buying_group),
        joinedload(Item.shipment_items).joinedload(ShipmentItem.shipment)).order_by(Item.id).all()
    # Keep independent sums: joining corrections to principal multiplies amounts.
    other_amount = defaultdict(lambda: ZERO)
    for model in (PaymentLineItem, PaymentCorrection):
        for iid, amount in db.query(model.item_id, model.amount).join(Item).join(Order).filter(
                Order.buying_group_id == group_id, model.payment_id != payment_id):
            other_amount[iid] += amount
    current_corrections = defaultdict(lambda: ZERO)
    for correction in payment.corrections:
        current_corrections[correction.item_id] += correction.amount
    by_receipt = defaultdict(list)
    item_commitments = defaultdict(dict)
    for row in coverage:
        by_receipt[row.receipt_source_id].append(row)
        if row.payment_id != payment_id:
            item_commitments[row.item_id][row.receipt_source_id] = row.quantity
    associated = set(review.content.get('receipt_ids', []))
    associated.update(c.receipt_source_id for c in coverage if c.payment_id == payment_id)
    source = review.source.payload
    item_index = {i.id: i for i in items}
    covered_items = {c.item_id for c in coverage if c.payment_id == payment_id}
    return {
        'payment': {'id': payment.id, 'payment_id': payment.payment_id,
            'buying_group_id': group_id, 'group_name': payment.buying_group.name,
            'allocation_version': payment.allocation_version, 'allocation_manual': payment.allocation_manual,
            'payment_requested_at': payment.payment_requested_at, 'payment_sent_at': payment.payment_sent_at,
            'status': source.get('status'), 'total_amount': str(payment_amount(payment)),
            'allocation_breakdown': breakdown(payment), 'allocation_details': payment.allocation_details,
            'sync_issues': payment.sync_issues, 'receipt_conflict': payment.receipt_conflict,
            'monarch_receipt': payment.monarch_receipt,
            'corrections': [{'item_id': c.item_id, 'amount': str(c.amount), 'explanation': c.explanation}
                            for c in payment.corrections]},
        # Merged legacy payments can retain local lines without receipt coverage.
        # Expose them explicitly so a save cannot silently discard those links.
        'unassigned_links': [{'item_id': li.item_id, 'amount': str(li.amount),
            'quantity': item_index[li.item_id].quantity if li.item_id in item_index else 1}
            for li in payment.line_items
            if li.item_id not in covered_items],
        'allocations': [{'receipt_source_id': c.receipt_source_id, 'item_id': c.item_id,
                         'quantity': c.quantity, 'amount': str(c.amount)}
                        for c in coverage if c.payment_id == payment_id],
        'receipts': [{'id': r.id, 'external_id': r.external_id, 'associated': r.id in associated,
            'description': r.payload.get('description', ''), 'quantity': r.payload.get('quantity'),
            'amount': str(r.payload['amount']), 'present': r.present, 'void': bool(r.payload.get('void')),
            'tracking_numbers': r.payload.get('tracking_numbers') or [],
            'baseline_amount': str(by_receipt[r.id][0].snapshot['amount'] if by_receipt[r.id] else r.payload['amount']),
            'other_amount': str(sum((c.amount for c in by_receipt[r.id] if c.payment_id != payment_id), ZERO)),
            'other_commitments': [{'item_id': c.item_id, 'quantity': c.quantity}
                                  for c in by_receipt[r.id] if c.payment_id != payment_id]}
            for r in receipts],
        'items': [{'id': i.id, 'description': i.description or 'Unnamed item', 'quantity': i.quantity,
            'order_id': i.order_id, 'order_number': i.order.store_order_number,
            'store': i.order.store.name if i.order.store else '', 'receipt_id': i.receipt_id,
            'tracking_numbers': list(dict.fromkeys(filter(None, [i.receipt_tracking_number,
                *(s.shipment.tracking_number for s in i.shipment_items)]))),
            'eligible': i.status.value not in {'purchased', 'shipped', 'submitted', 'canceled', 'needs_review'},
            'status': i.status.value, 'payable_amount': str(item_payable(i)),
            'other_amount': str(other_amount[i.id]), 'correction_amount': str(current_corrections[i.id]),
            'other_commitments': [{'receipt_source_id': rid, 'quantity': qty} for rid, qty in
                item_commitments[i.id].items()]}
            for i in items],
    }
