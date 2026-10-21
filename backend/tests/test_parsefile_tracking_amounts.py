"""Exercise Parsefile amount calculation through the outgoing HTTP request."""
import io
import json
import unittest
from contextlib import ExitStack
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import BuyingGroup, Item, Order, Shipment, ShipmentItem, Store
from app.models.item import ItemStatus
from app.utils.buying_group_apis import parsefile
from app.utils.buying_group_apis.submit_tracking import (
    batch_submit_pending_trackings, submit_item_tracking_to_group,
)


class ParsefileTrackingAmountTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("app.config.settings.group_submissions_enabled", True))
        self.network = self.stack.enter_context(patch.object(parsefile, "_safe_urlopen"))
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.addCleanup(self.db.close)
        self.group = BuyingGroup(name="Example", api_framework="parsefile",
            base_url="https://example.test", bearer_token="test-token",
            api_email="user@example.test", api_user_id=1)
        store = Store(name="Store")
        self.db.add_all([self.group, store])
        self.db.flush()
        self.order = Order(store_id=store.id, buying_group_id=self.group.id,
                           store_order_number="ORDER-1")
        self.db.add(self.order)
        self.db.flush()
        self.items = []
        for price, qty, tracking in [("12.34", 2, "TRACK-1"),
                                     ("5.67", 3, "TRACK-1"),
                                     ("9.99", 1, "TRACK-2")]:
            item = Item(order_id=self.order.id, price_sold=Decimal(price),
                        quantity=qty, status=ItemStatus.SHIPPED)
            shipment = Shipment(tracking_number=tracking)
            self.db.add_all([item, shipment])
            self.db.flush()
            self.db.add(ShipmentItem(item_id=item.id, shipment_id=shipment.id))
            self.items.append(item)
        self.db.commit()

    def request_entries(self):
        req = self.network.call_args.args[0]
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.full_url,
                         "https://example.test/p/it@api@order-management/cmd/addtracking")
        return json.loads(req.data)["trackings"]

    def respond(self):
        self.network.return_value = io.BytesIO(b'{"success":true,"affected":2}')

    def test_individual_submission_sends_total_for_all_tracking_siblings(self):
        self.respond()
        result = submit_item_tracking_to_group(self.group, self.items[0], self.order,
            "TRACK-1", sibling_items=self.items[:2])
        self.assertEqual(self.request_entries(), [
            {"tracking": "TRACK-1", "order": "ORDER-1", "notes": "2; 3", "amount": 41.69}])
        self.assertEqual(result.item_ids, [item.id for item in self.items[:2]])

    def test_batch_sends_separate_totals_and_marks_items_submitted(self):
        self.respond()
        result = batch_submit_pending_trackings(self.db, buying_group_id=self.group.id)
        self.assertIsNone(result.error)
        self.assertEqual(result.submitted_count, 3)
        self.assertEqual({entry["tracking"]: entry["amount"] for entry in self.request_entries()},
                         {"TRACK-1": 41.69, "TRACK-2": 9.99})
        self.assertTrue(all(item.status == ItemStatus.SUBMITTED for item in self.items))

    def test_zero_sold_price_is_explicitly_sent(self):
        self.items[0].price_sold = Decimal("0")
        self.respond()
        submit_item_tracking_to_group(self.group, self.items[0], self.order, "TRACK-1")
        self.assertEqual(self.request_entries()[0]["amount"], 0)
