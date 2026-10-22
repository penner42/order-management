"""Receipt matching follows local edits without fetching warehouse data again."""
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import fields, MISSING
from decimal import Decimal
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch
from uuid import uuid4
from threading import Event

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.config import settings
from app.models import BuyingGroup, Item, Order, ReceivingReceiptCache, ReceivingReviewPending, Shipment, ShipmentItem, Store
from app.models.item import ItemStatus
from app.utils.buying_group_apis import check_receiving as receiving
from app.utils.buying_group_apis import bfmr, buyinggroup, dealandrunner, parsefile, usabg
from app.utils.receiving_cache import ReceiptSource, decode, encode
from app.utils.receiving_links import link_receipt, receipt_key
from app.schemas.buying_group import ReceivingReceiptLinkRequest


def row(cls, **values):
    return cls(**{**{f.name: None for f in fields(cls) if f.default is MISSING and f.default_factory is MISSING}, **values})


class ReceivingCacheTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.initialize_data()

    def initialize_data(self):
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.group = BuyingGroup(name="Cache group", api_framework="parsefile", base_url="https://example.com",
                                 api_username="test", api_password="test-password", api_user_id=1,
                                 api_email="test@example.com", bearer_token="test-token")
        store = Store(name="Cache store")
        self.db.add_all([store, self.group])
        self.db.flush()
        self.order = Order(store_id=store.id, buying_group_id=self.group.id)
        self.db.add(self.order)
        self.db.flush()
        self.item = Item(order_id=self.order.id, description="Headphones", quantity=1,
                         price_sold=Decimal("20.00"), status=ItemStatus.SUBMITTED)
        self.shipment = Shipment(tracking_number="OLD", shipment_items=[ShipmentItem(item=self.item)])
        self.db.add(self.shipment)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def responses(self, tracking="NEW", count=1):
        pf = row(parsefile.ParsefileReceivedItem, verified=True, void=False, receipt_id="R1", item="Headphones",
                 qty=1, price=Decimal("20"), ext_total=Decimal("20"), status="Received", tracking_numbers=[tracking], raw={})
        bg = row(buyinggroup.BuyinggroupReceipt, receipt_id="R1", tracking_id=tracking,
                 quantity=1, total=Decimal("20"), status="Verified", void=False, raw={"key": "key"})
        bf = row(bfmr.BfmrTrackerRow, tracking_number=tracking, qty_received=1, received=True,
                 status="Received", payout_price=Decimal("20"), item_name="Headphones", my_tracker_id="R1")
        dn = row(dealandrunner.DealandrunnerReceivedRow, tracking_numbers=[tracking], amount=Decimal("20"),
                 product_name="Headphones", system_transaction_id="R1", entry_type_id=1)
        us = row(usabg.UsabgReceivedRow, po_number="R1", tracking_number=tracking, quantity=1,
                 unit_price=Decimal("20"), ext_total=Decimal("20"), received_status="Received",
                 receiving_status="Received", received_item="Headphones", sku="SKU")
        return {
            "parsefile": (parsefile, "list_all_received_items", parsefile.ParsefileReceivedItemsSearchResult([pf] * count, "csv", "")),
            "buyinggroup": (buyinggroup, "list_receipts", [bg] * count),
            "bfmr": (bfmr, "list_received_tracker_rows", bfmr.BfmrTrackerListResult([bf] * count, "", "")),
            "dealandrunner": (dealandrunner, "list_runner_transactions", dealandrunner.DealandrunnerRunnerTransactionsResult([dn] * count)),
            "usabg": (usabg, "list_received_po_rows", usabg.UsabgReceivedListResult([us] * count, 1, 1)),
        }

    def check(self, provider, *, count=1, error=None):
        self.group.api_framework = provider
        self.db.commit()
        module, method, value = self.responses(count=count)[provider]
        with ExitStack() as stack:
            stack.enter_context(patch.object(module, method, return_value=value, side_effect=error))
            stack.enter_context(patch.object(buyinggroup, "list_tracking_rewards", return_value={"new": Decimal("1.23")}))
            stack.enter_context(patch.object(buyinggroup, "get_receipt_details", return_value={"receipt_id": "R1", "received_items": [
                {"item_id": "Headphones", "upc": "123", "qty": 1, "price": "20", "total": "20", "verified": True}]}))
            result = receiving.check_receiving_for_group(self.db, buying_group_id=self.group.id)
        if not result.error:
            receiving.save_receiving_review_pending(self.db, result.session_payload)
        return result

    def rematch(self):
        with ExitStack() as stack:
            for module, method, _ in self.responses().values():
                stack.enter_context(patch.object(module, method, side_effect=AssertionError("Network forbidden")))
            for method in ("list_tracking_rewards", "get_receipt_details"):
                stack.enter_context(patch.object(buyinggroup, method, side_effect=AssertionError("Network forbidden")))
            return receiving.list_receiving_review_pending_payloads(self.db)

    def test_all_providers_rematch_local_tracking_and_price_without_network(self):
        for provider in self.responses():
            with self.subTest(provider=provider):
                self.shipment.tracking_number = "OLD"
                self.item.price_sold = Decimal("20")
                result = self.check(provider)
                self.assertIsNone(result.error)
                self.assertEqual(result.matches, [])
                fetched_at = self.db.get(ReceivingReceiptCache, self.group.id).fetched_at
                self.shipment.tracking_number = "NEW"
                self.item.price_sold = Decimal("19")
                # Cached matching also works after credentials are removed.
                self.group.api_password = None
                self.group.bearer_token = None
                self.db.commit()
                session, = self.rematch()
                match, = [m for m in session["matches"] if m["match_kind"] == "received"]
                self.assertEqual(match["expected_amount"], "19.00")
                self.assertTrue(match["amount_discrepancy"])
                self.assertEqual(self.db.get(ReceivingReceiptCache, self.group.id).fetched_at, fetched_at)
                self.assertIn("rematched_at", session)
                self.group.api_password = "test-password"
                self.group.bearer_token = "test-token"

    def test_cache_survives_pruning_and_failed_check_but_empty_success_replaces_it(self):
        self.shipment.tracking_number = "NEW"
        self.check("parsefile")
        receiving.prune_receiving_review_pending(self.db, buying_group_id=self.group.id, item_ids=[self.item.id])
        self.assertIsNone(self.db.get(ReceivingReviewPending, self.group.id))
        self.assertEqual(len(self.rematch()), 1)
        old = self.db.get(ReceivingReceiptCache, self.group.id).payload
        self.assertIsNotNone(self.check("parsefile", error=ValueError("offline")).error)
        self.assertEqual(self.db.get(ReceivingReceiptCache, self.group.id).payload, old)
        self.check("parsefile", count=0)
        self.assertEqual(self.rematch(), [])

    def test_older_inflight_check_cannot_replace_a_newer_snapshot(self):
        older = ReceiptSource()
        older.request("rows", lambda: parsefile.ParsefileReceivedItemsSearchResult([], "", ""))
        self.shipment.tracking_number = "NEW"
        self.check("parsefile")
        newer = self.db.get(ReceivingReceiptCache, self.group.id).payload
        with patch.object(receiving, "fetch_receipt_source", return_value=older):
            result = self.check("parsefile", count=0)
        self.assertEqual(len(result.matches), 1)
        self.assertEqual(self.db.get(ReceivingReceiptCache, self.group.id).payload, newer)

    def test_cache_contains_all_rows_and_no_credentials(self):
        self.check("parsefile", count=250)
        payload = self.db.get(ReceivingReceiptCache, self.group.id).payload
        self.assertEqual(len(decode(payload["requests"]["rows"]["value"]).rows), 250)
        self.assertNotIn("test-password", json.dumps(payload))
        self.assertNotIn("test-token", json.dumps(payload))
        self.assertEqual(decode(encode(Decimal("1.234567890123456789"))), Decimal("1.234567890123456789"))

    def test_buyinggroup_details_are_cached_before_local_date_filter(self):
        self.shipment.tracking_number = "NEW"
        self.order.purchase_date = datetime(2026, 10, 20, tzinfo=timezone.utc)
        responses = self.responses()
        responses["buyinggroup"][2][0].created_dt = "2026-10-01T12:00:00Z"
        with patch.object(self, "responses", return_value=responses):
            result = self.check("buyinggroup")
        self.assertEqual(result.matches, [])
        self.order.purchase_date = datetime(2026, 9, 1, tzinfo=timezone.utc)
        self.db.commit()
        session, = self.rematch()
        self.assertEqual(session["matches"][0]["receipt_id"], "R1")

    def test_deleted_items_and_changed_provider_are_not_shown(self):
        self.shipment.tracking_number = "NEW"
        self.check("parsefile")
        self.group.base_url = "https://other.example.com"
        self.db.commit()
        self.assertEqual(self.rematch(), [])
        self.group.base_url = "https://example.com"
        self.db.delete(self.item)
        self.db.commit()
        sessions = self.rematch()
        self.assertFalse(any(s["matches"] for s in sessions))

    def test_manual_links_refresh_prices_and_release_changed_quantities(self):
        result = self.check("parsefile")
        receipt, = result.session_payload["unknown_receipts"]
        link_receipt(self.db, ReceivingReceiptLinkRequest(buying_group_id=self.group.id,
                     receipt_key=receipt_key(receipt), item_ids=[self.item.id], receipt_tracking_number="NEW"))
        self.item.price_sold = Decimal("18")
        self.item.description = "Updated headphones"
        self.db.commit()
        session, = self.rematch()
        match, = session["matches"]
        self.assertEqual(match["description"], "Updated headphones")
        self.assertEqual(match["expected_amount"], "18.00")
        self.item.quantity = 2
        self.db.commit()
        session, = self.rematch()
        self.assertEqual(session["matches"], [])
        self.assertEqual(len(session["unknown_receipts"]), 1)
        self.shipment.tracking_number = "NEW"
        self.db.commit()
        session, = self.rematch()
        self.assertEqual(session["matches"], [])
        self.assertEqual(len(session["unknown_receipts"]), 1)

    def test_status_change_removes_suggestion_and_applied_receipt_stays_applied(self):
        self.shipment.tracking_number = "NEW"
        self.check("parsefile")
        self.item.status = ItemStatus.CANCELED
        self.db.commit()
        self.assertEqual(self.rematch(), [])
        self.item.status = ItemStatus.SCANNED
        self.item.receipt_id = "R1"
        self.item.receipt_tracking_number = "NEW"
        self.db.commit()
        self.assertEqual(self.rematch(), [])

    def test_cached_missing_details_never_fetches_on_replay(self):
        source = ReceiptSource()
        with self.assertRaises(ValueError):
            source.request("details", lambda: (_ for _ in ()).throw(ValueError("unavailable")))
        with self.assertRaises(ValueError):
            ReceiptSource(source.payload).request("details", lambda: self.fail("network"))


@unittest.skipUnless(settings.database_url.startswith("postgresql"), "requires PostgreSQL in Docker")
class PostgresCacheTests(unittest.TestCase):
    def test_refresh_waits_for_group_lock_and_reads_committed_order_changes(self):
        admin = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
        name = "test_receipt_cache_" + uuid4().hex
        fixture = None
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        try:
            fixture = ReceivingCacheTests()
            fixture.engine = create_engine(admin.url.set(database=name))
            fixture.initialize_data()
            fixture.shipment.tracking_number = "NEW"
            fixture.check("parsefile")
            group_id = fixture.group.id
            fixture.db.query(BuyingGroup).filter_by(id=group_id).with_for_update().one()
            fixture.item.price_sold = Decimal("18")
            fixture.db.flush()

            def refresh():
                with sessionmaker(bind=fixture.engine, autoflush=False)() as db:
                    return receiving.list_receiving_review_pending_payloads(db)

            with ThreadPoolExecutor(max_workers=2) as pool:
                pending = pool.submit(refresh)
                try:
                    with self.assertRaises(TimeoutError):
                        pending.result(timeout=0.2)
                finally:
                    fixture.db.commit()
                session, = pending.result(timeout=10)
                self.assertEqual(session["matches"][0]["expected_amount"], "18.00")

                # A slow provider fetch must not hold the review lock.
                started, release = Event(), Event()
                result_rows = fixture.responses()["parsefile"][2]

                def fetch(**kwargs):
                    started.set()
                    if not release.wait(timeout=10):
                        raise ValueError("Test fetch timed out")
                    return result_rows

                def check():
                    with sessionmaker(bind=fixture.engine, autoflush=False)() as db:
                        result = receiving.check_receiving_for_group(db, buying_group_id=group_id)
                        if result.session_payload:
                            receiving.save_receiving_review_pending(db, result.session_payload)
                        return result

                with patch.object(parsefile, "list_all_received_items", side_effect=fetch):
                    background = pool.submit(check)
                    try:
                        self.assertTrue(started.wait(timeout=5))
                        cached_session, = pool.submit(refresh).result(timeout=5)
                        self.assertEqual(cached_session["matches"][0]["expected_amount"], "18.00")
                    finally:
                        release.set()
                    self.assertIsNone(background.result(timeout=10).error)
        finally:
            if fixture is not None:
                fixture.tearDown()
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
            admin.dispose()
