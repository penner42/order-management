"""Workspace evidence excludes editable principal and keeps cross-payment commitments."""
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import (BuyingGroup, Store, Order, Item, Payment, PaymentSource,
                        PaymentReview, PaymentCoverage, PaymentCorrection, PaymentLineItem)
from app.models.item import ItemStatus
from app.payment_workspace import linking_workspace


class PaymentWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.group = BuyingGroup(name='Workspace group', api_framework='parsefile')
        self.store = Store(name='Workspace store')
        self.db.add_all([self.group, self.store]); self.db.flush()
        self.order = Order(store_id=self.store.id, buying_group_id=self.group.id, store_order_number='ORDER-1')
        self.db.add(self.order); self.db.flush()
        self.item = Item(order_id=self.order.id, description='Local item', quantity=2,
            price_sold=50, status=ItemStatus.SCANNED, receipt_id='r1', receipt_tracking_number='TRACK-1')
        self.db.add(self.item); self.db.commit()
        self.receipt = self.source('r1', 'receipt', amount='100', quantity=2)
        self.payment = self.pay('p1')
        self.db.add(PaymentReview(source_id=self.source('p1', 'payment', amount='100', status='PAID').id,
            payment_id=self.payment.id, content={'receipt_ids': [self.receipt.id]}))
        self.db.commit()

    def tearDown(self):
        self.db.close(); self.engine.dispose()

    def source(self, reference, kind, provider='parsefile', group=None, **payload):
        source = PaymentSource(buying_group_id=group or self.group.id, provider=provider,
            external_id=reference, kind=kind, payload=payload, revision='rev', present=True)
        self.db.add(source); self.db.flush(); return source

    def pay(self, reference):
        p = Payment(buying_group_id=self.group.id, payment_id=reference, source_amount=100,
            payment_requested_at=datetime(2026, 10, 1, tzinfo=timezone.utc), ledger_state='imported')
        self.db.add(p); self.db.flush(); return p

    def cover(self, payment, amount, quantity=2):
        self.db.add(PaymentCoverage(payment_id=payment.id, receipt_source_id=self.receipt.id,
            item_id=self.item.id, amount=Decimal(amount), quantity=quantity, snapshot={'amount': '100'}))
        self.db.add(PaymentLineItem(payment_id=payment.id, item_id=self.item.id, amount=Decimal(amount)))

    def test_current_allocations_are_editable_other_installments_stay_committed(self):
        other = self.pay('p2'); self.cover(other, '40'); self.cover(self.payment, '30')
        for p, amount in [(other, '-10'), (other, '5'), (self.payment, '-5')]:
            self.db.add(PaymentCorrection(payment_id=p.id, origin_payment_id=other.id,
                receipt_source_id=self.receipt.id, item_id=self.item.id, amount=Decimal(amount),
                explanation='Signed correction', source_revision='rev'))
        self.db.commit()
        w = linking_workspace(self.db, self.payment.id)
        item, receipt = w['items'][0], w['receipts'][0]
        self.assertEqual(item['other_amount'], '35.00')
        self.assertEqual(item['correction_amount'], '-5.00')
        self.assertEqual(receipt['other_amount'], '40.00')
        self.assertEqual(receipt['baseline_amount'], '100')
        self.assertEqual(item['other_commitments'], [{'receipt_source_id': self.receipt.id, 'quantity': 2}])
        self.assertEqual(receipt['other_commitments'], [{'item_id': self.item.id, 'quantity': 2}])
        self.assertEqual(w['allocations'][0]['amount'], '30.00')
        self.assertEqual(item['order_number'], 'ORDER-1')
        self.assertEqual(item['tracking_numbers'], ['TRACK-1'])

    def test_group_provider_associations_missing_receipts_and_eligibility(self):
        extra = self.source('extra', 'receipt', amount='10')
        self.source('wrong-provider', 'receipt', provider='usabg', amount='1')
        other_group = BuyingGroup(name='Other'); self.db.add(other_group); self.db.flush()
        self.source('wrong-group', 'receipt', group=other_group.id, amount='1')
        self.receipt.present = False
        self.receipt.payload = {**self.receipt.payload, 'void': True}
        self.cover(self.payment, '10')
        # Use flush to avoid invoking automatic reconciliation for the fixture.
        self.item.status = ItemStatus.SHIPPED; self.db.flush()
        w = linking_workspace(self.db, self.payment.id)
        self.assertEqual({r['id'] for r in w['receipts']}, {extra.id, self.receipt.id})
        r = next(r for r in w['receipts'] if r['id'] == self.receipt.id)
        self.assertTrue(r['associated']); self.assertFalse(r['present']); self.assertTrue(r['void'])
        self.assertFalse(w['items'][0]['eligible'])
        self.assertFalse(next(r for r in w['receipts'] if r['id'] == extra.id)['associated'])

    def test_existing_allocation_receipt_remains_visible_without_source_association(self):
        self.cover(self.payment, '20')
        self.db.query(PaymentReview).one().content = {'receipt_ids': []}; self.db.flush()
        w = linking_workspace(self.db, self.payment.id)
        self.assertTrue(w['receipts'][0]['associated'])

    def test_query_count_does_not_grow_with_items_or_receipts(self):
        payment_id = self.payment.id
        def measure():
            self.db.expunge_all()
            statements = []
            def record(*args): statements.append(args[2])
            event.listen(self.engine, 'before_cursor_execute', record)
            try: linking_workspace(self.db, payment_id)
            finally: event.remove(self.engine, 'before_cursor_execute', record)
            return len(statements)
        initial = measure()
        p = self.db.get(Payment, payment_id)
        order = self.db.query(Order).first()
        for n in range(25):
            self.source(f'extra-{n}', 'receipt', group=p.buying_group_id, amount='1')
            self.db.add(Item(order_id=order.id, quantity=1, price_sold=1, status=ItemStatus.SCANNED))
        self.db.flush()
        self.assertEqual(measure(), initial)
        self.assertLess(initial, 15)

    def test_preserved_local_lines_without_coverage_are_explicit(self):
        self.db.add(PaymentLineItem(payment_id=self.payment.id, item_id=self.item.id, amount=25))
        self.db.flush()
        w = linking_workspace(self.db, self.payment.id)
        self.assertEqual(w['unassigned_links'], [{'item_id': self.item.id, 'amount': '25.00', 'quantity': 2}])
        self.assertEqual(w['allocations'], [])

    def test_missing_and_legacy_payments(self):
        with self.assertRaises(HTTPException) as error: linking_workspace(self.db, -1)
        self.assertEqual(error.exception.status_code, 404)
        self.payment.ledger_state = 'legacy'; self.db.flush()
        with self.assertRaises(HTTPException) as error: linking_workspace(self.db, self.payment.id)
        self.assertEqual(error.exception.status_code, 409)
