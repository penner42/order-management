"""Automatic deposit linking checks competing deposits and source revisions."""
from datetime import date
import unittest
from decimal import Decimal
from unittest.mock import patch
from tests import test_monarch as fixtures
from app.models import MonarchDeposit, MonarchPaymentReceipt, Payment
from app.monarch import auto_confirm_receipts
from app.payment_ledger import receipt_conflict


class AutomaticMonarchTests(unittest.TestCase):
    setUp = fixtures.MonarchTests.setUp
    tearDown = fixtures.MonarchTests.tearDown
    payment = fixtures.MonarchTests.payment

    def test_unique_exact_deposit_links_without_local_items(self):
        p=self.payment('30');p.line_items.clear();p.source_amount=Decimal('30');self.db.commit()
        self.assertEqual(auto_confirm_receipts(self.db),1)
        self.assertIsNotNone(self.db.get(MonarchPaymentReceipt,p.id))
        self.assertEqual(auto_confirm_receipts(self.db),0)
        p.source_amount=Decimal('31');self.db.commit()
        self.assertTrue(receipt_conflict(p))

    def test_competing_deposits_remain_unlinked(self):
        p=self.payment('30')
        self.db.add(MonarchDeposit(transaction_id='tx2',account_id='bank',account_name='Bank',
            amount=30,posted_date=date(2026,10,10),pending=False,merchant='Sender',description='ACH',revision=1))
        self.db.commit()
        self.assertEqual(auto_confirm_receipts(self.db),0)
        self.assertIsNone(self.db.get(MonarchPaymentReceipt,p.id))

    def test_ambiguous_combinations_reserve_their_payments(self):
        self.payment('10');self.payment('20');self.payment('30')
        self.assertEqual(auto_confirm_receipts(self.db),0)
        self.assertEqual(self.db.query(MonarchPaymentReceipt).count(),0)

    def test_truncated_competing_search_prevents_automatic_confirmation(self):
        self.payment('30')
        with patch('app.monarch.suggest_matches',return_value={'suggestions':[], 'search_truncated':True}):
            self.assertEqual(auto_confirm_receipts(self.db),0)

    def test_legacy_payments_are_excluded(self):
        p=self.payment('30');p.ledger_state='legacy';self.db.commit()
        self.assertEqual(auto_confirm_receipts(self.db),0)
