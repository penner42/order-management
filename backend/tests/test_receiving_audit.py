"""Reconciliation flags excess units without changing local or remote records."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import BuyingGroup, Item, Order, ReceivingReviewPending, Store
from app.models.item import ItemStatus
from app.routers.buying_groups import receiving_audit
from app.utils.buying_group_apis import buyinggroup as api
from app.utils.receiving_audit import build_receiving_audit, reconcile_receipts


class ReceivingAuditTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.db.close)
        self.group = BuyingGroup(name="BG", api_framework="buyinggroup", api_username="test", api_password="test")
        self.store = Store(name="Walmart")
        self.db.add_all([self.group, self.store])
        self.db.flush()

    def item(self, qty=1, receipt="R1", status=ItemStatus.SCANNED, group=None):
        order = Order(store=self.store, buying_group=group or self.group, store_order_number="WALMART-ORDER")
        item = Item(order=order, quantity=qty, price_sold=199, description="Laptop", status=status,
                    receipt_id=receipt, reward="0.05")
        self.db.add(item)
        self.db.commit()
        return item

    def receipt(self, qty=1, rid="R1", **extra):
        return api._parse_receipt_row({"receipt_id": rid, "quantity": qty, "tracking_id": "TRACK", **extra})

    def test_two_scanned_laptops_against_one_received_flags_both_orders(self):
        items = [self.item(), self.item()]
        finding, = reconcile_receipts(items, [self.receipt()])
        self.assertEqual((finding["status"], finding["bg_quantity"], finding["local_quantity_min"],
                          finding["excess_quantity"]), ("excess", 1, 2, 1))
        self.assertEqual({row["order_id"] for row in finding["items"]}, {i.order_id for i in items})

    def test_shared_receipt_and_tracking_do_not_imply_duplicate_receiving(self):
        finding, = reconcile_receipts([self.item(), self.item()], [self.receipt(2, alt_tracking="WALMART-ORDER")])
        self.assertEqual(finding["status"], "within_quantity")
        self.assertEqual(finding["bg_quantity"], 2)

    def test_submitted_units_with_old_receipt_details_are_not_counted_received(self):
        scanned = self.item()
        submitted = self.item(qty=2, status=ItemStatus.SUBMITTED)
        submitted.scanned_at = datetime.now(timezone.utc)
        submitted.receipt_tracking_number = "OLD-TRACKING"
        self.db.commit()
        with patch.object(api, "list_receipts", return_value=[self.receipt()]):
            finding, = build_receiving_audit(self.db)["groups"][0]["findings"]
        self.assertEqual((finding["status"], finding["local_quantity_min"], finding["bg_quantity"],
                          finding["excess_quantity"]), ("stale_details", 1, 1, 0))
        self.assertEqual({row["item_id"]: row["received_quantity"] for row in finding["items"]},
                         {scanned.id: 1, submitted.id: 0})
        self.assertTrue(any("excluded" in note for note in finding["notes"]))

    def test_old_receipt_or_scan_date_does_not_count_open_statuses_as_received(self):
        for status in (ItemStatus.SUBMITTED, ItemStatus.SHIPPED, ItemStatus.PURCHASED,
                       ItemStatus.NEEDS_REVIEW, ItemStatus.CANCELED):
            with self.subTest(status=status):
                item = self.item(qty=2, status=status)
                item.scanned_at = datetime.now(timezone.utc)
                finding, = reconcile_receipts([item], [self.receipt()])
                self.assertEqual(finding["local_quantity_max"], 0)
                self.assertEqual(finding["status"], "stale_details")

    def test_open_line_without_receipt_id_but_old_scan_date_counts_zero(self):
        item = self.item(qty=2, receipt=None, status=ItemStatus.SUBMITTED)
        item.scanned_at = datetime.now(timezone.utc)
        finding, = reconcile_receipts([item], [self.receipt()])
        self.assertEqual(finding["local_quantity_max"], 0)
        self.assertEqual(finding["status"], "unverified")

    def test_receipt_linked_returns_still_count_as_previously_received(self):
        for status in ItemStatus:
            if not (status.value.startswith('return_') or status in {ItemStatus.NEEDS_RETURN, ItemStatus.LOST_PACKAGE}):
                continue
            with self.subTest(status=status):
                finding, = reconcile_receipts([self.item(qty=2, status=status)], [self.receipt()])
                self.assertEqual(finding["excess_quantity"], 1)

    def test_distinct_receipt_product_rows_sum_once_and_expose_limit(self):
        finding, = reconcile_receipts([self.item(3)], [self.receipt(1, item="Laptop"), self.receipt(2, item="Watch")])
        self.assertEqual(finding["status"], "within_quantity")
        self.assertEqual(finding["bg_quantity"], 3)
        self.assertEqual([row["description"] for row in finding["bg_lines"]], ["Laptop", "Watch"])
        self.assertTrue(any("Product-by-product" in note for note in finding["notes"]))

    def test_missing_and_unknown_quantities_are_not_zero(self):
        item = self.item(3)
        for rows in ([], [self.receipt(None)], [self.receipt(-1)]):
            with self.subTest(rows=rows):
                finding, = reconcile_receipts([item], rows)
                self.assertEqual(finding["status"], "unverified")
                self.assertIsNone(finding["bg_quantity"])
                self.assertIsNone(finding["excess_quantity"])

    def test_voided_receipts_supply_no_received_units(self):
        finding, = reconcile_receipts([self.item()], [self.receipt(5, void=True)])
        self.assertEqual((finding["status"], finding["bg_quantity"]), ("excess", 0))

    def test_duplicate_provider_rows_do_not_produce_a_clean_report(self):
        receipt = self.receipt()
        finding, = reconcile_receipts([self.item(2)], [receipt, receipt])
        self.assertEqual(finding["status"], "unverified")

    def test_multiple_receipt_ids_use_ranges_instead_of_double_counting(self):
        item = self.item(2, receipt=" R1 ; r2, R1 ")
        findings = reconcile_receipts([item], [self.receipt(), self.receipt(rid="R2")])
        self.assertEqual(len(findings), 2)
        for finding in findings:
            self.assertEqual((finding["status"], finding["local_quantity_min"], finding["local_quantity_max"]),
                             ("unverified", 0, 2))

    def test_missing_receipt_id_is_visible_for_manual_review(self):
        finding, = reconcile_receipts([self.item(receipt=None)], [self.receipt()])
        self.assertEqual(finding["status"], "unverified")
        self.assertIsNone(finding["receipt_id"])

    def test_report_is_read_only_includes_later_statuses_and_isolates_groups(self):
        scanned = self.item()
        returned = self.item(status=ItemStatus.RETURN_REFUNDED)
        no_receipt = self.item(receipt=None)
        other = BuyingGroup(name="Other", api_framework="buyinggroup", api_username="test", api_password="test")
        self.db.add(other)
        self.db.flush()
        self.item(group=other)
        self.db.add(ReceivingReviewPending(buying_group_id=self.group.id, payload={"matches": [{"item_id": scanned.id}]}, matched_count=1))
        self.db.commit()
        before = [(item.id, item.status, item.receipt_id, item.reward) for item in self.db.query(Item).all()]
        with patch.object(api, "list_receipts", return_value=[self.receipt()]) as fetch:
            report = receiving_audit(group_id=self.group.id, db=self.db, _=None)
        fetch.assert_called_once_with(base_url=self.group.base_url, username="test", password="test", require_complete=True)
        result, = report["groups"]
        self.assertEqual(result["local_item_count"], 3)
        self.assertEqual({i["item_id"] for f in result["findings"] for i in f["items"]},
                         {scanned.id, returned.id, no_receipt.id})
        self.assertEqual(before, [(item.id, item.status, item.receipt_id, item.reward) for item in self.db.query(Item).all()])
        self.assertEqual(self.db.query(ReceivingReviewPending).one().matched_count, 1)
        self.assertFalse(self.db.new or self.db.dirty or self.db.deleted)

    def test_fetch_failure_is_incomplete_and_does_not_expose_credentials(self):
        self.item()
        with patch.object(api, "list_receipts", side_effect=RuntimeError("password=secret")):
            report = build_receiving_audit(self.db)
        result, = report["groups"]
        self.assertTrue(result["error"])
        self.assertNotIn("secret", result["error"])
        self.assertEqual(result["findings"], [])

    def test_route_rejects_unknown_or_unsupported_group(self):
        with self.assertRaises(HTTPException) as missing:
            receiving_audit(group_id=12345, db=self.db, _=None)
        self.assertEqual(missing.exception.status_code, 404)
        self.group.api_framework = "parsefile"
        self.db.commit()
        with self.assertRaises(HTTPException) as unsupported:
            receiving_audit(group_id=self.group.id, db=self.db, _=None)
        self.assertEqual(unsupported.exception.status_code, 400)


class ReceiptHistoryCompletenessTests(unittest.TestCase):
    def fetch(self, pages):
        with (patch.object(api, "_ensure_tokens", return_value=api._AuthTokens("token")),
              patch.object(api, "_fetch_receipts_page", side_effect=pages)):
            return api.list_receipts(base_url="https://example.test", username="test", password="test", require_complete=True)

    def test_all_pages_are_retrieved(self):
        row = api._parse_receipt_row({"receipt_id": "R1", "quantity": 1})
        rows = self.fetch([([row], 2, {}), ([row], 2, {})])
        self.assertEqual(len(rows), 2)

    def test_truncation_and_early_empty_page_fail(self):
        with self.assertRaises(RuntimeError):
            self.fetch([([], api._MAX_LIST_PAGES + 1, {})])
        with self.assertRaises(RuntimeError):
            self.fetch([([], 2, {})])
        row = api._parse_receipt_row({"receipt_id": "R1", "quantity": 1})
        with self.assertRaises(RuntimeError):
            self.fetch([([row], 2, {}), ([], 2, {})])
