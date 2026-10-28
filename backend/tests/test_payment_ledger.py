"""Source totals remain usable independently of purchase reconciliation."""
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import BuyingGroup, Store, Order, Item, Payment, PaymentLineItem, PaymentSource, PaymentReview, PaymentCoverage
from app.models.item import ItemStatus
from app.payment_evidence import PaymentEvidence, ReceiptEvidence, PaymentFetch
from app.payment_review import refresh
from app.payment_ledger import adjust, AllocationEdit, AllocationReset, breakdown, sync_group
from app.payment_totals import payment_amount

T = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)


class PaymentLedgerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.group = BuyingGroup(name='Example', api_framework='parsefile')
        self.store = Store(name='Store')
        self.db.add_all([self.group, self.store]); self.db.flush()
        self.order = Order(store_id=self.store.id, buying_group_id=self.group.id)
        self.db.add(self.order); self.db.commit()

    def tearDown(self):
        self.db.close(); self.engine.dispose()

    def item(self, rid='r1', value='60'):
        i = Item(order_id=self.order.id, receipt_id=rid, quantity=1, price_sold=Decimal(value), status=ItemStatus.SCANNED)
        self.db.add(i); self.db.commit(); return i

    def fetch(self, amount='100', receipts=None, payments=None, **kwargs):
        return refresh(self.db, self.group.id, PaymentFetch(
            payments if payments is not None else [PaymentEvidence(external_id='p1', amount=amount,
                created_at=T, sent_at=T, status='PAID', receipt_ids=['r1', 'old'])],
            receipts if receipts is not None else [ReceiptEvidence(external_id='r1', amount='60', quantity=1),
                ReceiptEvidence(external_id='old', amount='40', quantity=1, created_at=datetime(2025,12,20,tzinfo=timezone.utc))],
            **kwargs), 'parsefile', automate=True)

    def payment(self):
        return self.db.query(Payment).filter_by(ledger_state='imported').one()

    def test_mixed_year_payment_imports_full_total_and_partial_matches(self):
        item = self.item(); self.fetch()
        p = self.payment()
        self.assertEqual(payment_amount(p), Decimal('100'))
        self.assertEqual([(li.item_id, li.amount) for li in p.line_items], [(item.id, Decimal('60'))])
        self.assertEqual(breakdown(p)['historical_amount'], '0')
        self.assertEqual(Decimal(breakdown(p)['unresolved_amount']), Decimal('40'))
        self.assertIsNone(p.payment_received_at)
        self.assertEqual({r['external_id'] for r in p.source_details['receipt_evidence']},{'r1','old'})

    def test_repeated_sync_does_not_duplicate_coverage(self):
        self.item(); self.fetch(); self.fetch()
        self.assertEqual(self.db.query(Payment).count(), 1)
        self.assertEqual(self.db.query(PaymentCoverage).count(), 1)
        self.assertEqual(len(self.payment().line_items), 1)

    def test_later_received_item_automatically_rematches(self):
        self.fetch()
        self.assertFalse(self.payment().line_items)
        item = self.item()
        self.assertEqual(self.payment().line_items[0].item_id, item.id)

    def test_historical_classification_requires_explicit_explained_edit(self):
        self.item(); self.fetch(); p = self.payment()
        allocations = p.allocation_details['allocations']
        adjust(self.db, p.id, AllocationEdit(version=p.allocation_version, allocations=allocations,
            historical_amount='40', note='Prior-year purchases are outside local records'))
        self.assertEqual(Decimal(breakdown(p)['unresolved_amount']), 0)
        self.assertEqual(p.sync_issues, [])
        self.fetch(amount='110')
        self.assertTrue(p.allocation_manual)
        self.assertEqual(Decimal(breakdown(p)['historical_amount']), 40)
        self.assertEqual(payment_amount(p), 110)
        self.assertTrue(any('Source changed' in issue for issue in p.sync_issues))
        self.assertEqual(self.db.query(PaymentSource).filter_by(kind='payment').one().revisions[0]['payload']['amount'], '100')

    def test_stale_adjustments_fail_after_source_change(self):
        self.fetch(); p = self.payment(); version = p.allocation_version
        self.fetch(amount='110')
        with self.assertRaises(HTTPException) as e:
            adjust(self.db, p.id, AllocationEdit(version=version, note='Stale edit'))
        self.assertEqual(e.exception.status_code, 409)

    def test_reset_restores_automatic_matching(self):
        self.item(); self.fetch(); p = self.payment()
        adjust(self.db, p.id, AllocationEdit(version=p.allocation_version, historical_amount='100', note='Temporary classification'))
        self.assertFalse(p.line_items)
        adjust(self.db, p.id, AllocationReset(version=p.allocation_version), reset=True)
        self.assertFalse(p.allocation_manual)
        self.assertEqual(len(p.line_items), 1)
        self.assertEqual(Decimal(breakdown(p)['historical_amount']), 0)

    def test_source_total_is_independent_of_corrections_and_bonus(self):
        self.fetch(); p = self.payment(); p.payment_bonus = 50
        self.assertEqual(payment_amount(p), 100)

    def test_ambiguous_window_is_not_automatically_allocated(self):
        self.item()
        self.fetch(payments=[PaymentEvidence(external_id='p1', amount='100', created_at=T, status='PAID', warnings=['Ambiguous receipts'])])
        self.assertFalse(self.payment().line_items)
        self.assertEqual(payment_amount(self.payment()), 100)

    def test_invalid_group_quantity_and_item_amount_edits_rejected(self):
        item = self.item(); self.fetch(); p = self.payment()
        rid = self.db.query(PaymentSource).filter_by(external_id='r1').one().id
        for qty, value in [(2,'60'),(1,'61')]:
            with self.assertRaises(HTTPException):
                adjust(self.db, p.id, AllocationEdit(version=p.allocation_version,
                    allocations=[dict(receipt_source_id=rid,item_id=item.id,quantity=qty,amount=value)],note='Invalid'))
            self.db.rollback()

    def test_missing_payment_preserved_and_flagged(self):
        self.fetch(); p = self.payment()
        self.fetch(payments=[], receipts=[])
        self.assertEqual(self.db.query(Payment).count(), 1)
        # Reconcile retained sources even when absent from the latest full snapshot.
        self.assertTrue(any('missing' in issue for issue in p.sync_issues))

    def test_partial_history_preserves_old_sources(self):
        self.fetch(); self.fetch(payments=[], receipts=[], history_complete=False)
        self.assertTrue(all(s.present for s in self.db.query(PaymentSource)))

    def test_inaccessible_archive_preserves_previously_matched_coverage(self):
        self.item();self.fetch();p=self.payment()
        self.fetch(history_complete=False)
        self.assertEqual(len(p.line_items),1)
        self.assertEqual(p.line_items[0].amount,60)

    def test_incomplete_fetch_does_not_replace_source(self):
        self.fetch()
        with self.assertRaises(HTTPException): self.fetch(amount='150', complete=False)
        self.db.rollback(); self.assertEqual(payment_amount(self.payment()), 100)

    def test_old_requested_payment_sent_this_year_is_active(self):
        old=datetime(2025,12,30,tzinfo=timezone.utc)
        self.fetch(payments=[PaymentEvidence(external_id='last-year',amount='100',created_at=old,sent_at=T,status='PAID'),
            PaymentEvidence(external_id='context-only',amount='10',created_at=old,sent_at=old,status='PAID')])
        self.assertEqual(self.payment().payment_id, 'last-year')
        self.assertEqual(self.db.query(Payment).count(),1)
        self.assertEqual(self.db.query(PaymentSource).filter_by(kind='payment').count(),2)

    def test_legacy_matching_is_unique_by_reference_and_total(self):
        legacy=Payment(buying_group_id=self.group.id,payment_id='p1',payment_requested_at=T,payment_bonus=100)
        self.db.add(legacy);self.db.commit(); self.fetch()
        self.assertEqual(self.payment().id,legacy.id)
        self.assertTrue(self.payment().allocation_manual)
        self.assertEqual(self.db.query(Payment).count(),1)

    def test_ambiguous_legacy_matches_remain_separate(self):
        for _ in range(2):
            self.db.add(Payment(buying_group_id=self.group.id,payment_id='p1',payment_requested_at=T,payment_bonus=100))
        self.db.commit(); self.fetch()
        self.assertEqual(self.db.query(Payment).filter_by(ledger_state='legacy').count(),2)
        self.assertEqual(self.db.query(Payment).filter_by(ledger_state='imported').count(),1)

    def test_fetch_failure_records_safe_error_and_preserves_payments(self):
        self.fetch()
        with patch('app.payment_providers.ParsefilePaymentProvider.ready',return_value=(True,None)), patch(
            'app.payment_providers.ParsefilePaymentProvider.fetch',side_effect=ValueError('sensitive remote data')):
            with self.assertRaises(HTTPException) as e: sync_group(self.db,self.group.id)
        self.assertEqual(e.exception.status_code,502)
        self.assertNotIn('sensitive',str(e.exception.detail))
        self.assertEqual(payment_amount(self.payment()),100)

    def test_signed_receipt_revision_keeps_original_total_and_carries_to_later_payment(self):
        from datetime import timedelta
        old_item=self.item(value='100')
        original=PaymentEvidence(external_id='p1',amount='100',created_at=T,status='PAID',receipt_ids=['r1'])
        self.fetch(payments=[original],receipts=[ReceiptEvidence(external_id='r1',amount='100',quantity=1)])
        first=self.payment()
        self.item('r2','100')
        later=PaymentEvidence(external_id='p2',amount='90',created_at=T+timedelta(days=1),status='PAID',receipt_ids=['r2'])
        self.fetch(payments=[original,later],receipts=[ReceiptEvidence(external_id='r1',amount='90',quantity=1),
            ReceiptEvidence(external_id='r2',amount='100',quantity=1)])
        self.db.refresh(first)
        second=self.db.query(Payment).filter_by(payment_id='p2').one()
        self.assertEqual(first.line_items[0].amount,100)
        self.assertEqual(payment_amount(first),100)
        self.assertEqual(payment_amount(second),90)
        self.assertEqual(sum(c.amount for c in second.corrections),-10)
        self.assertEqual(Decimal(breakdown(second)['unresolved_amount']),0)
        self.fetch(payments=[original,later],receipts=[ReceiptEvidence(external_id='r1',amount='90',quantity=1),
            ReceiptEvidence(external_id='r2',amount='100',quantity=1)])
        self.assertEqual(len(second.corrections),1)

    def test_receipt_changes_invalidate_manual_allocation_preview(self):
        self.item();self.fetch();p=self.payment()
        adjust(self.db,p.id,AllocationEdit(version=p.allocation_version,
            allocations=p.allocation_details['allocations'],note='Verified allocation'))
        version=p.allocation_version
        self.fetch(receipts=[ReceiptEvidence(external_id='r1',amount='60',quantity=1,description='Revised description'),
            ReceiptEvidence(external_id='old',amount='40',quantity=1)])
        self.assertGreater(p.allocation_version,version)
        self.assertTrue(any('Receipt evidence changed' in i for i in p.sync_issues))

    def test_partial_receipt_installments_reuse_physical_units_without_overpaying(self):
        from datetime import timedelta
        self.item(value='100')
        parts=[PaymentEvidence(external_id=f'p{n}',amount=value,created_at=T+timedelta(days=n),status='PAID',
            receipt_ids=['r1'],receipt_amounts={'r1':value}) for n,value in [(1,'40'),(2,'60')]]
        self.fetch(payments=parts,receipts=[ReceiptEvidence(external_id='r1',amount='100',quantity=1)])
        self.assertEqual(self.db.query(PaymentCoverage).count(),2)
        self.assertEqual(sum(c.amount for c in self.db.query(PaymentCoverage)),100)
        self.fetch(payments=parts,receipts=[ReceiptEvidence(external_id='r1',amount='100',quantity=1)])
        self.assertEqual(self.db.query(PaymentCoverage).count(),2)
