"""Receipt UPC capture and safe backfills across the non-BG integrations."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.models import Payment, PaymentLineItem
from app.models.item import ItemStatus
from app.routers.items import bulk_update_items
from app.schemas.item import ItemBulkUpdateEntry, ItemBulkUpdateRequest
from app.utils.buying_group_apis import check_receiving as receiving, parsefile
from app.utils.item_split import split_item_record
from app.schemas.item import ItemSplitRequest
from app.utils.receipt_upc import receipt_upc
from app.utils.receiving_links import link_receipt, receipt_key
from app.schemas.buying_group import ReceivingReceiptLinkRequest
from tests import test_receiving_cache as cache_tests

PROVIDERS = ('bfmr', 'usabg', 'dealandrunner', 'parsefile')
UPC = '000012345678'


class ReceiptUpcExtractionTests(unittest.TestCase):
    def test_explicit_fields_only_and_string_preservation(self):
        for key in ('UPC', 'upc', 'UPC Code', 'upc_number', 'productUpc', 'itemUPC', 'UPC-A'):
            with self.subTest(key=key):
                self.assertEqual(receipt_upc(SimpleNamespace(raw={key: f' {UPC} '})), UPC)
        for raw in ({}, {'sku': UPC}, {'barcode': UPC}, {'id': UPC},
                    {'upc': ''}, {'upc': ' '}, {'upc': '1' * 65}, {'upc': True},
                    {'upc': [UPC]}, {'upc': 12.5}, {'upc': UPC, 'UPC Code': 'other'}):
            with self.subTest(raw=raw):
                self.assertIsNone(receipt_upc(SimpleNamespace(raw=raw)))
        self.assertEqual(receipt_upc(SimpleNamespace(raw={'upc': 123})), '123')
        self.assertEqual(receipt_upc(SimpleNamespace(raw={'po': {'upc': 'wrong'}, 'line': {'upc': UPC}})), UPC)
        self.assertIsNone(receipt_upc(SimpleNamespace(raw={'box': {'upc': UPC}, 'line': {'sku': UPC}})))

    def test_parsefile_csv_retains_optional_upc_column(self):
        row, = parsefile.parse_received_items_csv(
            'Verified,Void,Receipt ID,Item,Qty,Tracking Numbers,UPC Code\n'
            f'Yes,No,R1,Headphones,1,TRACK,{UPC}\n')
        self.assertEqual(receipt_upc(row), UPC)
        row, = parsefile.parse_received_items_csv('Receipt ID,Item,Qty\nR1,Headphones,1\n')
        self.assertIsNone(receipt_upc(row))


class ProviderReceiptUpcTests(unittest.TestCase):
    initialize_data = cache_tests.ReceivingCacheTests.initialize_data
    tearDown = cache_tests.ReceivingCacheTests.tearDown
    check = cache_tests.ReceivingCacheTests.check
    rematch = cache_tests.ReceivingCacheTests.rematch

    def setUp(self):
        cache_tests.ReceivingCacheTests.setUp(self)
        self.shipment.tracking_number = 'NEW'
        self.db.commit()
        self.product_raw = {'upc': UPC}

    def responses(self, tracking='NEW', count=1):
        responses = cache_tests.ReceivingCacheTests.responses(self, tracking, count)
        for provider in PROVIDERS:
            rows = responses[provider][2].rows
            for row in rows:
                row.raw = {'line': self.product_raw} if provider == 'usabg' else self.product_raw
        return responses

    def apply_upc(self, match):
        bulk_update_items(ItemBulkUpdateRequest(updates=[ItemBulkUpdateEntry(
            item_id=self.item.id, receipt_id=match.receipt_id, receipt_upc=match.receipt_upc,
        )]), db=self.db, _=None)

    def new_receipt(self, provider):
        match, = self.check(provider).matches
        self.assertEqual(match.receipt_upc, UPC)
        self.assertFalse(match.update_receipt_only)
        session, = self.rematch()
        self.assertEqual(session['matches'][0]['receipt_upc'], UPC)
        self.apply_upc(match)
        self.assertEqual(self.item.receipt_upc, UPC)

    def missing_or_invalid(self, provider):
        for raw in ({'sku': UPC}, {}, {'upc': ''}, {'upc': '1' * 65}):
            self.product_raw = raw
            match, = self.check(provider).matches
            self.assertIsNone(match.receipt_upc)

    def backfill(self, provider):
        self.item.status = ItemStatus.RETURN_STARTED
        self.item.receipt_id = 'R1'
        self.item.scanned_at = datetime(2026, 10, 1, 14, 33, 15, tzinfo=timezone.utc)
        self.item.price_sold = Decimal('21.00')
        self.item.reward = Decimal('2.50')
        payment = Payment(buying_group_id=self.group.id, payment_requested_at=datetime.now(timezone.utc))
        self.db.add(payment)
        self.db.flush()
        self.db.add(PaymentLineItem(payment_id=payment.id, item_id=self.item.id, amount=21))
        self.db.commit()
        original_scan = self.item.scanned_at
        match, = self.check(provider).matches
        self.assertTrue(match.update_receipt_only)
        self.assertEqual(match.receipt_upc, UPC)
        self.assertIsNone(match.split_keep_quantity)
        self.assertIsNone(match.received_ext_total)
        self.assertIsNone(match.reward)
        self.assertIsNone(match.scanned_at)
        self.apply_upc(match)
        self.db.refresh(self.item)
        self.assertEqual(self.item.quantity, 1)
        self.assertEqual(self.item.status, ItemStatus.RETURN_STARTED)
        self.assertEqual(self.item.scanned_at, original_scan)
        self.assertEqual(self.item.price_sold, Decimal('21.00'))
        self.assertEqual(self.item.reward, Decimal('2.50'))
        self.assertEqual(self.item.payment_line_items[0].amount, 21)
        self.assertFalse(self.check(provider).matches)
        self.assertFalse(self.rematch())

    def preserve_existing(self, provider):
        self.item.status = ItemStatus.SCANNED
        self.item.receipt_id = 'R1'
        self.item.receipt_upc = 'existing'
        self.db.commit()
        self.assertFalse(self.check(provider).matches)
        # Even a receipt-id correction must retain the UPC already saved.
        self.item.receipt_id = 'OLD-RECEIPT'
        self.db.commit()
        match, = self.check(provider).matches
        self.assertEqual(match.receipt_upc, 'existing')

    def partial(self, provider):
        self.item.quantity = 2
        self.db.commit()
        received, missing = self.check(provider).matches
        self.assertEqual(received.receipt_upc, UPC)
        self.assertIsNone(missing.receipt_upc)
        remainder = split_item_record(self.db, self.item, ItemSplitRequest(
            keep_quantity=1, copy_tracking=True, split_off_status=ItemStatus.NEEDS_REVIEW))
        self.apply_upc(received)
        self.assertEqual(self.item.receipt_upc, UPC)
        self.assertIsNone(remainder.receipt_upc)

    def insufficient_backfill(self, provider):
        self.item.quantity = 2
        self.item.status = ItemStatus.SCANNED
        self.item.receipt_id = 'R1'
        self.db.commit()
        self.assertFalse(self.check(provider).matches)

    def orphan_link(self, provider):
        self.shipment.tracking_number = 'UNRELATED'
        self.db.commit()
        receipt, = self.check(provider).session_payload['unknown_receipts']
        self.assertEqual(receipt['receipt_upc'], UPC)
        payload = link_receipt(self.db, ReceivingReceiptLinkRequest(
            buying_group_id=self.group.id, receipt_key=receipt_key(receipt),
            item_id=self.item.id, receipt_tracking_number='NEW'))
        match, = payload['matches']
        self.assertEqual(match['receipt_upc'], UPC)
        bulk_update_items(ItemBulkUpdateRequest(updates=[ItemBulkUpdateEntry(
            item_id=self.item.id, receipt_id=match['receipt_id'],
            receipt_upc=UPC, receiving_group_id=self.group.id, receiving_row_id=match['row_id'],
        )]), db=self.db, _=None)
        self.assertEqual(self.item.receipt_upc, UPC)

    def conflicting_products(self, provider):
        responses = self.responses()
        rows = responses[provider][2].rows
        other = deepcopy(rows[0])
        other.raw = {'line': {'upc': '999999999999'}} if provider == 'usabg' else {'upc': '999999999999'}
        rows.append(other)
        with patch.object(self, 'responses', return_value=responses):
            result = self.check(provider)
            if provider in ('usabg', 'dealandrunner'):
                # Same description/price, different UPCs: neither lot is a safe choice.
                self.assertFalse(result.matches)
            else:
                match, = result.matches
                self.assertIsNone(match.receipt_upc)
            self.item.status = ItemStatus.SCANNED
            self.item.receipt_id = 'R1'
            self.db.commit()
            self.assertFalse(self.check(provider).matches)

    def incomplete_product_identity(self, provider):
        responses = self.responses()
        rows = responses[provider][2].rows
        other = deepcopy(rows[0])
        other.raw = {'line': {'sku': UPC}} if provider == 'usabg' else {'sku': UPC}
        rows.append(other)
        with patch.object(self, 'responses', return_value=responses):
            result = self.check(provider)
            if provider in ('usabg', 'dealandrunner'):
                self.assertFalse(result.matches)
            else:
                match, = result.matches
                self.assertIsNone(match.receipt_upc)

    def product_backfill(self, provider):
        self.item.status = ItemStatus.SCANNED
        self.item.receipt_id = 'R1'
        self.db.commit()
        responses = self.responses()
        rows = responses[provider][2].rows
        other = deepcopy(rows[0])
        if provider == 'usabg':
            other.received_item = 'Apple Pencil Pro'
            other.unit_price = Decimal('90')
            other.raw = {'line': {'upc': '999999999999'}}
        else:
            other.product_name = 'Apple Pencil Pro'
            other.amount = Decimal('90')
            other.raw = {'upc': '999999999999'}
        rows.append(other)
        with patch.object(self, 'responses', return_value=responses):
            match, = self.check(provider).matches
        self.assertEqual(match.receipt_upc, UPC)
        self.assertTrue(match.update_receipt_only)
        self.assertIsNone(match.received_ext_total)

    def test_bfmr_order_fallback_retains_upc(self):
        self.shipment.tracking_number = 'OTHER'
        self.order.store_order_number = 'ORDER-1'
        self.db.commit()
        responses = self.responses()
        responses['bfmr'][2].rows[0].order_id = 'ORDER-1'
        with patch.object(self, 'responses', return_value=responses):
            match, = self.check('bfmr').matches
            self.assertEqual(match.receipt_upc, UPC)
            self.item.status = ItemStatus.SCANNED
            self.item.receipt_id = 'R1'
            self.db.commit()
            match, = self.check('bfmr').matches
            self.assertEqual(match.receipt_upc, UPC)
            self.assertTrue(match.update_receipt_only)

    def test_dnr_conflicting_aliases_do_not_backfill(self):
        self.item.status = ItemStatus.SCANNED
        self.item.receipt_id = 'R1'
        self.db.commit()
        responses = self.responses()
        rows = responses['dealandrunner'][2].rows
        rows[0].tracking_numbers = ['PRIMARY', 'NEW']
        other = deepcopy(rows[0])
        other.raw = {'upc': '999999999999'}
        rows.append(other)
        with patch.object(self, 'responses', return_value=responses):
            self.assertFalse(self.check('dealandrunner').matches)

    def test_dnr_soft_lots_keep_different_upcs_separate(self):
        responses = self.responses()
        row = responses['dealandrunner'][2].rows[0]
        other = deepcopy(row)
        other.tracking_numbers = ['OTHER']
        other.raw = {'upc': '999999999999'}
        lots = receiving._dnr_lots_from_receipts([row, other])
        self.assertEqual(len(receiving._merge_fungible_orphan_lots(lots)), 2)


for provider in PROVIDERS:
    for scenario in ('new_receipt', 'missing_or_invalid', 'backfill', 'preserve_existing',
                     'partial', 'insufficient_backfill', 'orphan_link', 'conflicting_products',
                     'incomplete_product_identity'):
        def test(self, provider=provider, scenario=scenario):
            getattr(self, scenario)(provider)
        setattr(ProviderReceiptUpcTests, f'test_{provider}_{scenario}', test)
    if provider in ('usabg', 'dealandrunner'):
        def test(self, provider=provider):
            self.product_backfill(provider)
        setattr(ProviderReceiptUpcTests, f'test_{provider}_product_backfill', test)
