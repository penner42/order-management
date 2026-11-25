"""Receipt matching follows local edits without fetching warehouse data again."""
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import fields, MISSING, replace
from decimal import Decimal
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch
from uuid import uuid4
from threading import Event

from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.config import settings
from app.models import BuyingGroup, Item, Order, ReceivingReceiptCache, ReceivingReviewPending, Shipment, ShipmentItem, Store
from app.models.item import ItemStatus
from app.routers.buying_groups import check_buying_group_receiving, has_pending_receiving_matches
from app.routers.items import bulk_update_items
from app.schemas.item import ItemBulkUpdateEntry, ItemBulkUpdateRequest, ItemSplitRequest
from app.utils.buying_group_apis import check_receiving as receiving
from app.utils.buying_group_apis import bfmr, buyinggroup, dealandrunner, parsefile, usabg
from app.utils.item_split import split_item_record
from app.utils.receiving_cache import ReceiptSource, decode, encode
from app.utils.receiving_links import candidate_lines, link_receipt, receipt_key
from app.schemas.buying_group import ReceivingDamageMatchRequest, ReceivingReceiptLinkRequest


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

    def check(self, provider, *, count=1, error=None, details=None, damaged=None, returns=None):
        self.group.api_framework = provider
        self.db.commit()
        module, method, value = self.responses(count=count)[provider]
        with ExitStack() as stack:
            stack.enter_context(patch.object(module, method, return_value=value, side_effect=error))
            stack.enter_context(patch.object(dealandrunner, "list_damaged_items", return_value=damaged or []))
            stack.enter_context(patch.object(buyinggroup, "list_returns", return_value=returns or []))
            stack.enter_context(patch.object(buyinggroup, "list_tracking_rewards", return_value={"new": Decimal("1.23")}))
            stack.enter_context(patch.object(buyinggroup, "get_receipt_details", side_effect=details,
                return_value={"receipt_id": "R1", "received_items": [
                    {"item_id": "Headphones", "upc": "123", "qty": 1, "price": "20", "total": "20", "verified": True}]}))
            result = receiving.check_receiving_for_group(self.db, buying_group_id=self.group.id)
        if not result.error:
            receiving.save_receiving_review_pending(self.db, result.session_payload)
        return result

    def rematch(self):
        with ExitStack() as stack:
            for module, method, _ in self.responses().values():
                stack.enter_context(patch.object(module, method, side_effect=AssertionError("Network forbidden")))
            for method in ("list_tracking_rewards", "get_receipt_details", "list_returns"):
                stack.enter_context(patch.object(buyinggroup, method, side_effect=AssertionError("Network forbidden")))
            stack.enter_context(patch.object(dealandrunner, "list_damaged_items", side_effect=AssertionError("Network forbidden")))
            return receiving.list_receiving_review_pending_payloads(self.db)

    def test_dnr_damage_match_splits_one_unit_and_keeps_receipt_for_remainder(self):
        self.item.quantity = 2
        self.shipment.tracking_number = "NEW"
        other_item = Item(order_id=self.order.id, description="Other product", status=ItemStatus.SUBMITTED)
        self.db.add(other_item)
        damage = {"warehouse_id": "DAMAGED-1", "description": "Headphones", "tracking_numbers": ["new", "LPN"]}
        self.check("dealandrunner", damaged=[damage])
        group_id = self.group.id
        self.assertEqual(self.rematch()[0]["damaged_items"], [damage])
        self.assertTrue(has_pending_receiving_matches(db=self.db, _=None))
        candidates = candidate_lines(self.db, group_id, "", 1, damaged_warehouse_id="DAMAGED-1")
        self.assertEqual(candidates["items"][0]["item_id"], self.item.id)
        body = ReceivingDamageMatchRequest(buying_group_id=group_id, warehouse_id="DAMAGED-1",
                                           item_id=self.item.id, expected_quantity=2)
        with patch.object(dealandrunner, "list_damaged_items", side_effect=AssertionError("Network forbidden")):
            result = receiving.match_receiving_return(self.db, body)
        returned = self.db.query(Item).filter_by(receiving_return_id="DAMAGED-1").one()
        self.assertEqual((returned.status, returned.quantity), (ItemStatus.NEEDS_RETURN, 1))
        self.assertIsNotNone(returned.needs_return_at)
        self.assertIsNone(returned.receipt_id)
        self.assertIsNone(returned.scanned_at)
        self.assertEqual(returned.shipment_items[0].shipment.tracking_number, "NEW")
        self.assertEqual((self.item.status, self.item.quantity), (ItemStatus.SUBMITTED, 1))
        self.assertEqual([(m["item_id"], m["receipt_id"]) for m in result["matches"]], [(self.item.id, "R1")])
        self.assertEqual(self.rematch()[0]["damaged_items"], [])
        with self.assertRaises(HTTPException) as repeated:
            receiving.match_receiving_return(self.db, body)
        self.assertEqual(repeated.exception.status_code, 409)

    def test_buyinggroup_return_review_without_receipts(self):
        self.item.quantity = 2
        _, _, tasks = buyinggroup._parse_return_page({"status": "SUCCESS", "payload": {"pages": 1, "count": 3, "tasks": [
            {"key": "open", "task_status": "READY", "item": {"item_id": "Headphones"},
             "receipt": {"receipt_id": "R-10", "tracking_id": "OLD"}},
            {"key": "sent", "task_status": "SENT"},
            {"key": "void", "task_status": "LABEL", "void": True},
        ]}})
        returns = [task for task in tasks.values() if task is not None]
        self.assertEqual(len(returns), 1)
        self.check("buyinggroup", count=0, returns=returns)
        session = self.rematch()[0]
        self.assertTrue(session["buyinggroup_returns"])
        self.assertEqual(session["damaged_items"][0]["source_reference"], "R-10")
        body = ReceivingDamageMatchRequest(buying_group_id=self.group.id, warehouse_id="buyinggroup:open",
                                           item_id=self.item.id, expected_quantity=2)
        result = receiving.match_receiving_return(self.db, body)
        self.assertEqual(result["damaged_items"], [])
        matched = self.db.query(Item).filter_by(receiving_return_id="buyinggroup:open").one()
        self.assertEqual((matched.status, matched.quantity), (ItemStatus.NEEDS_RETURN, 1))
        self.assertEqual((self.item.status, self.item.quantity), (ItemStatus.SUBMITTED, 1))
        self.check("buyinggroup", count=0, returns=returns)
        self.assertEqual(self.rematch(), [])

    def test_buyinggroup_return_review_survives_receipt_rematch(self):
        returns = [{"warehouse_id": "buyinggroup:open", "description": "Headphones", "tracking_numbers": ["OLD"]}]
        self.check("buyinggroup", returns=returns)
        self.assertEqual(self.rematch()[0]["damaged_items"], returns)
        self.check("buyinggroup")
        self.assertEqual(self.rematch()[0]["damaged_items"], [])

    def test_buyinggroup_rate_limit_preserves_cached_review_and_returns_retry_after(self):
        self.check("buyinggroup")
        cached = self.db.get(ReceivingReceiptCache, self.group.id).payload
        receipts = self.responses()["buyinggroup"][2]
        with (
            patch.object(buyinggroup, "list_receipts", return_value=receipts),
            patch.object(buyinggroup, "list_returns", side_effect=buyinggroup.RateLimited(42)),
            patch.object(buyinggroup, "list_tracking_rewards") as rewards,
            patch("app.routers.buying_groups.write_group_tracking_log"),
            self.assertRaises(HTTPException) as result,
        ):
            check_buying_group_receiving(self.group.id, db=self.db, _=None)
        self.assertEqual(result.exception.status_code, 429)
        self.assertEqual(result.exception.headers["Retry-After"], "42")
        rewards.assert_not_called()
        self.assertEqual(self.db.get(ReceivingReceiptCache, self.group.id).payload, cached)
        self.assertTrue(self.rematch())

    def test_buyinggroup_http_429_blocks_retries_and_lengthens_repeated_waits(self):
        origin = "https://rate-limit.example.com"
        with (
            patch.dict(buyinggroup._rate_limits, clear=True),
            patch.object(buyinggroup.time, "monotonic", return_value=100) as clock,
            patch.object(buyinggroup.http.client, "HTTPSConnection") as connection,
            patch.object(buyinggroup, "_log_failed_response"),
        ):
            response = connection.return_value.getresponse.return_value
            response.status = 429
            response.getheader.return_value = "45"
            with self.assertRaises(buyinggroup.RateLimited) as limited:
                buyinggroup._json_request(url=origin + buyinggroup.TOKEN_GET_PATH, payload={})
            self.assertEqual(limited.exception.retry_after, 45)
            clock.return_value = 110
            with self.assertRaises(buyinggroup.RateLimited) as cooldown:
                buyinggroup._json_request(url=origin + buyinggroup.GET_RECEIPTS_PATH, payload={})
            self.assertEqual(cooldown.exception.retry_after, 35)
            connection.assert_called_once()
            response.read.assert_not_called()
            clock.return_value = 145
            response.getheader.return_value = None
            with self.assertRaises(buyinggroup.RateLimited) as repeated:
                buyinggroup._json_request(url=origin + buyinggroup.TOKEN_GET_PATH, payload={})
            self.assertEqual(repeated.exception.retry_after, 120)
            clock.return_value = 205
            with self.assertRaises(buyinggroup.RateLimited) as still_waiting:
                buyinggroup._json_request(url=origin + buyinggroup.TOKEN_GET_PATH, payload={})
            self.assertEqual(still_waiting.exception.retry_after, 60)
            self.assertEqual(connection.call_count, 2)
            clock.return_value = 265
            response.status = 200
            response.read.return_value = b'{}'
            buyinggroup._json_request(url=origin + buyinggroup.TOKEN_GET_PATH, payload={})
            self.assertEqual(connection.call_count, 3)
            response.status = 429
            with self.assertRaises(buyinggroup.RateLimited) as reset:
                buyinggroup._json_request(url=origin + buyinggroup.TOKEN_GET_PATH, payload={})
            self.assertEqual(reset.exception.retry_after, 60)

    def test_buyinggroup_refresh_rate_limit_keeps_refresh_token_for_next_check(self):
        credentials = dict(base_url="https://refresh.example.com", username="test",
                           password="test", timeout_seconds=30)
        key = buyinggroup._cache_key(credentials["base_url"], credentials["username"], credentials["password"])
        stale = buyinggroup._AuthTokens("old-access", "old-refresh")
        fresh = buyinggroup._AuthTokens("new-access", "new-refresh")
        with (
            patch.dict(buyinggroup._token_cache, {key: stale}),
            patch.object(buyinggroup, "refresh_access_token", side_effect=[buyinggroup.RateLimited(42), fresh]),
            patch.object(buyinggroup, "login") as login,
        ):
            with self.assertRaises(buyinggroup.RateLimited):
                buyinggroup._reauth_tokens(**credentials, stale=stale)
            self.assertEqual(buyinggroup._ensure_tokens(**credentials), stale)
            self.assertEqual(buyinggroup._reauth_tokens(**credentials, stale=stale), fresh)
            login.assert_not_called()

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
        self.assertTrue(has_pending_receiving_matches(db=self.db, _=None))
        self.group.base_url = "https://other.example.com"
        self.db.commit()
        self.assertEqual(self.rematch(), [])
        self.assertFalse(has_pending_receiving_matches(db=self.db, _=None))
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

    def test_needs_review_matches_later_scan_without_reusing_applied_units(self):
        self.shipment.tracking_number = "NEW"
        remainder = Item(order_id=self.order.id, description=self.item.description, quantity=1,
                         price_sold=self.item.price_sold, status=ItemStatus.NEEDS_REVIEW)
        self.shipment.shipment_items.append(ShipmentItem(item=remainder))
        self.item.status = ItemStatus.SCANNED
        self.item.receipt_id = "R1"
        self.item.receipt_tracking_number = "NEW"
        self.item.receipt_upc = "123"
        self.item.reward = Decimal("1.23")
        self.db.commit()

        def details(receipt_key, **kwargs):
            return {"receipt_id": "R1" if receipt_key == "key" else "R2", "received_items": [
                {"item_id": "Headphones", "upc": "123", "qty": 1, "price": "20", "total": "20", "verified": True}
            ]}

        fields_by_provider = {
            "parsefile": {"receipt_id": "R2"},
            "buyinggroup": {"receipt_id": "R2", "raw": {"key": "second"}},
            "bfmr": {"my_tracker_id": "R2"},
            "dealandrunner": {"system_transaction_id": "R2"},
            "usabg": {"po_number": "R2"},
        }
        for provider, changes in fields_by_provider.items():
            with self.subTest(provider=provider):
                remainder.status = ItemStatus.NEEDS_REVIEW
                remainder.receipt_id = remainder.receipt_tracking_number = None
                remainder.receipt_upc = remainder.scanned_at = None
                self.assertFalse(self.check(provider).matches)
                self.assertFalse(any(session["matches"] for session in self.rematch()))

                responses = self.responses()
                response = responses[provider][2]
                rows = response if provider == "buyinggroup" else response.rows
                rows.append(replace(rows[0], **changes))
                with patch.object(self, "responses", return_value=responses):
                    match, = self.check(provider, details=details).matches
                self.assertEqual((match.item_id, match.receipt_id, match.quantity), (remainder.id, "R2", 1))
                self.assertFalse(match.update_receipt_only)
                session, = self.rematch()
                cached_match, = session["matches"]
                self.assertEqual(cached_match["receipt_id"], "R2")

                bulk_update_items(ItemBulkUpdateRequest(updates=[ItemBulkUpdateEntry(
                    item_id=remainder.id, status=ItemStatus.SCANNED, receipt_id=match.receipt_id,
                    receipt_tracking_number=match.tracking_number, receipt_upc=match.receipt_upc, reward=match.reward,
                )]), db=self.db, _=None)
                self.assertEqual(remainder.status, ItemStatus.SCANNED)
                self.assertFalse(any(session["matches"] for session in self.rematch()))

    def test_needs_review_matches_only_new_units_on_an_existing_receipt(self):
        self.shipment.tracking_number = "NEW"
        self.item.quantity = 3
        received, missing = self.check("parsefile").matches
        self.assertEqual((received.quantity, missing.quantity), (1, 2))
        remainder = split_item_record(self.db, self.item, ItemSplitRequest(
            keep_quantity=received.split_keep_quantity, copy_tracking=True, split_off_status=ItemStatus.NEEDS_REVIEW,
        ))
        self.item.status = ItemStatus.SCANNED
        self.item.receipt_id = received.receipt_id
        self.db.commit()
        self.assertEqual(self.rematch(), [])

        responses = self.responses()
        receipt, = responses["parsefile"][2].rows
        receipt.qty = 2
        receipt.ext_total = Decimal("40")
        with patch.object(self, "responses", return_value=responses):
            received, missing = self.check("parsefile").matches
        self.assertEqual(received.item_id, remainder.id)
        self.assertEqual(received.split_keep_quantity, 1)
        self.assertEqual(received.received_ext_total, Decimal("20"))
        self.assertEqual((missing.match_kind, missing.quantity), ("not_received", 1))

    def test_dealandrunner_does_not_reuse_applied_walmart_scans_on_other_trackings(self):
        self.order.store.name = "Walmart"
        self.item.quantity = 3
        responses = self.responses("035414608074526836")
        rows = responses["dealandrunner"][2].rows
        rows.append(replace(rows[0], tracking_numbers=["056781234567890123"], system_transaction_id="R2"))
        with patch.object(self, "responses", return_value=responses):
            received, missing = self.check("dealandrunner").matches
        self.assertEqual((received.quantity, missing.quantity), (2, 1))
        remainder = split_item_record(self.db, self.item, ItemSplitRequest(
            keep_quantity=2, copy_tracking=True, split_off_status=ItemStatus.NEEDS_REVIEW,
        ))
        self.item.status = ItemStatus.SCANNED
        self.item.receipt_id = received.receipt_id
        self.item.receipt_tracking_number = received.tracking_number
        self.db.commit()
        self.assertFalse(any(session["matches"] for session in self.rematch()))

        rows.append(replace(rows[0], tracking_numbers=["012341234567890123"], system_transaction_id="R3"))
        with patch.object(self, "responses", return_value=responses):
            match, = self.check("dealandrunner").matches
        self.assertEqual((match.item_id, match.receipt_id, match.quantity), (remainder.id, "R3", 1))

    def test_dealandrunner_matches_one_product_at_different_payouts(self):
        self.shipment.tracking_number = "NEW"
        self.item.description = "Dell DC15255 Laptop AMD Ryzen 5"
        self.item.price_sold = Decimal("387.99")
        self.item.quantity = 3
        acer = Item(order_id=self.order.id, description="Acer Aspire Go 15 Intel Core 3",
                    quantity=3, price_sold=Decimal("299.99"), status=ItemStatus.SHIPPED)
        self.shipment.shipment_items.append(ShipmentItem(item=acer))
        responses = self.responses()
        rows = responses["dealandrunner"][2].rows
        rows[0] = replace(rows[0], amount=Decimal("387.99"), product_name=self.item.description,
                          raw={"referenceProductId": "dell-model"})
        rows.extend([
            replace(rows[0], amount=Decimal("411.06"), system_transaction_id="R2"),
            replace(rows[0], amount=Decimal("411.06"), system_transaction_id="R3"),
            replace(rows[0], amount=Decimal("500"), system_transaction_id="R4",
                    raw={"referenceProductId": "different-dell-model"}),
        ])
        with patch.object(self, "responses", return_value=responses):
            matches = self.check("dealandrunner").matches
            session, = self.rematch()
        self.assertEqual(session["matches"], [receiving.match_to_dict(m) for m in matches])
        self.assertEqual(
            [(m.item_id, m.quantity, m.receipt_id, m.received_ext_total, m.amount_discrepancy) for m in matches],
            [(self.item.id, 1, "R1", Decimal("387.99"), False),
             (self.item.id, 2, "R2, R3", Decimal("822.12"), True)],
        )

        # Either payout group can be applied first, with the other remaining matchable.
        first = bulk_update_items(ItemBulkUpdateRequest(updates=[ItemBulkUpdateEntry(
            item_id=self.item.id, status=ItemStatus.SCANNED, receipt_id=matches[1].receipt_id,
            receipt_tracking_number="NEW", receiving_allocation={"quantity": 2, "expected_quantity": 3},
        )]), db=self.db, _=None)
        remainder_id = first.receiving_remainder_ids[self.item.id]
        with patch.object(self, "responses", return_value=responses):
            session, = self.rematch()
        remainder, = [m for m in session["matches"] if not m["update_receipt_only"]]
        self.assertEqual((remainder["item_id"], remainder["receipt_id"], remainder["quantity"]),
                         (remainder_id, "R1", 1))
        self.assertEqual(acer.quantity, 3)
        self.assertEqual(acer.status, ItemStatus.SHIPPED)

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
