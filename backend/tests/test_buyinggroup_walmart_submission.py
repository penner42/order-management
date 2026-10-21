"""Walmart carrier shipments submit their tracking and order as separate BG entries."""
import unittest
from contextlib import ExitStack
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import BuyingGroup, Item, Order, Shipment, ShipmentItem, Store
from app.models.item import ItemStatus
from app.utils.buying_group_apis import buyinggroup as api
from app.utils.buying_group_apis.submit_tracking import (
    batch_submit_pending_trackings, submit_item_tracking_to_group,
)


class WalmartSubmissionTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch("app.config.settings.group_submissions_enabled", True))
        self.submit = stack.enter_context(patch.object(api, "submit_trackings", side_effect=self.respond))
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.addCleanup(self.db.close)
        self.group = BuyingGroup(name="BG", api_framework="buyinggroup",
                                 api_username="user", api_password="password")
        self.store = Store(name="Walmart")
        self.order = Order(store=self.store, buying_group=self.group, store_order_number="200015408984219")
        self.db.add(self.order)
        self.db.commit()
        self.failed = set()

    def respond(self, **kwargs):
        numbers = [entry.tracking for entry in kwargs["trackings"]]
        succeeded = [number for number in numbers if number not in self.failed]
        return api.BuyinggroupSubmitResult(
            success=bool(succeeded), message="Submit results", affected=len(succeeded),
            succeeded=succeeded, failed=list(self.failed),
            submission_ids={number: api.BuyinggroupSubmissionIds(f"id-{number}", f"api-{number}")
                            for number in succeeded},
        )

    def item(self, tracking, price="20", quantity=1, order=None):
        item = Item(order=order or self.order, status=ItemStatus.SHIPPED,
                    price_sold=Decimal(price), quantity=quantity)
        item.shipment_items = [ShipmentItem(shipment=Shipment(tracking_number=tracking))]
        self.db.add(item)
        self.db.commit()
        return item

    def entries(self):
        return {entry.tracking: entry for entry in self.submit.call_args.kwargs["trackings"]}

    def test_manual_submission_sends_tracking_and_order_with_sibling_total(self):
        items = [self.item("TRACK", "12", 2), self.item("TRACK", "5", 3)]
        result = submit_item_tracking_to_group(self.group, items[0], self.order, "TRACK", sibling_items=items)
        self.assertEqual(set(self.entries()), {"TRACK", self.order.store_order_number})
        for entry in self.entries().values():
            self.assertEqual(entry.amount, Decimal("39"))
            self.assertEqual(entry.order_num, self.order.store_order_number)
        self.assertEqual(result.item_ids, [item.id for item in items])

    def test_batch_deduplicates_order_across_shipments_and_keeps_carrier_ids(self):
        items = [self.item("TRACK-1", "12", 2), self.item("TRACK-1", "5", 3), self.item("TRACK-2", "10")]
        result = batch_submit_pending_trackings(self.db, buying_group_id=self.group.id)
        self.assertIsNone(result.error)
        self.assertEqual(result.submitted_count, 3)
        self.assertEqual({number: entry.amount for number, entry in self.entries().items()},
                         {"TRACK-1": Decimal("39"), "TRACK-2": Decimal("10"),
                          self.order.store_order_number: Decimal("49")})
        for item, tracking in zip(items, ["TRACK-1", "TRACK-1", "TRACK-2"]):
            self.assertEqual(item.status, ItemStatus.SUBMITTED)
            self.assertEqual(item.submission_id, f"id-{tracking}")

    def test_store_delivery_and_internal_tracking_still_send_one_entry(self):
        for tracking in [self.order.store_order_number, "55512345678901234567", "WM 55512345678901234567"]:
            with self.subTest(tracking=tracking):
                item = self.item(tracking)
                submit_item_tracking_to_group(self.group, item, self.order, tracking)
                self.assertEqual(list(self.entries()), [tracking])

    def test_other_stores_and_missing_order_numbers_still_send_one_entry(self):
        item = self.item("TRACK")
        self.store.name = "Amazon"
        submit_item_tracking_to_group(self.group, item, self.order, "TRACK")
        self.assertEqual(list(self.entries()), ["TRACK"])
        self.store.name = "Walmart"
        self.order.store_order_number = "  "
        submit_item_tracking_to_group(self.group, item, self.order, "TRACK")
        self.assertEqual(list(self.entries()), ["TRACK"])

    def test_manual_partial_success_leaves_item_pending(self):
        item = self.item("TRACK")
        for failed in ["TRACK", self.order.store_order_number]:
            with self.subTest(failed=failed):
                self.failed = {failed}
                with self.assertRaises(RuntimeError):
                    submit_item_tracking_to_group(self.group, item, self.order, "TRACK")
                self.assertEqual(item.status, ItemStatus.SHIPPED)

    def test_batch_requires_both_submissions_and_can_retry(self):
        item = self.item("TRACK")
        self.failed = {self.order.store_order_number}
        result = batch_submit_pending_trackings(self.db, buying_group_id=self.group.id)
        self.assertEqual(result.submitted_count, 0)
        self.assertIsNotNone(result.error)
        self.assertEqual(item.status, ItemStatus.SHIPPED)
        self.failed = set()
        result = batch_submit_pending_trackings(self.db, buying_group_id=self.group.id)
        self.assertEqual(result.submitted_count, 1)
        self.assertEqual(item.status, ItemStatus.SUBMITTED)

    def test_batch_partial_carrier_failure_only_marks_complete_items(self):
        first, second = self.item("TRACK-1"), self.item("TRACK-2")
        self.failed = {"TRACK-2"}
        result = batch_submit_pending_trackings(self.db, buying_group_id=self.group.id)
        self.assertEqual(result.submitted_count, 1)
        self.assertEqual(first.status, ItemStatus.SUBMITTED)
        self.assertEqual(second.status, ItemStatus.SHIPPED)

    def test_shared_carrier_tracking_has_separate_entry_for_each_walmart_order(self):
        first = self.item("TRACK")
        other = Order(store=self.store, buying_group=self.group, store_order_number="OTHER-ORDER")
        second = self.item("TRACK", order=other)
        submit_item_tracking_to_group(self.group, first, self.order, "TRACK", sibling_items=[first, second])
        self.assertEqual(set(self.entries()), {"TRACK", self.order.store_order_number, "OTHER-ORDER"})
        self.assertEqual(self.entries()["TRACK"].amount, Decimal("40"))
        self.assertEqual(self.entries()["OTHER-ORDER"].amount, Decimal("20"))
