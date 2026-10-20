"""BuyingGroup receipt rewards survive review and stay separate from payout."""
from decimal import Decimal
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import BuyingGroup, Item, Order, Shipment, ShipmentItem, Store
from app.models.item import ItemStatus
from app.routers.items import bulk_update_items
from app.schemas.buying_group import ReceivingReceiptLinkRequest, ReceivingReviewSession
from app.schemas.item import ItemBulkUpdateEntry, ItemBulkUpdateRequest, ItemRead
from app.utils.buying_group_apis import buyinggroup as api, check_receiving as receiving
from app.utils.receiving_links import link_receipt, receipt_key


class BuyingGroupRewardTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.group = BuyingGroup(name="BG", api_framework="buyinggroup", api_username="user", api_password="pass")
        store = Store(name="Store")
        self.db.add_all([self.group, store])
        self.db.flush()
        order = Order(store_id=store.id, buying_group_id=self.group.id)
        self.db.add(order)
        self.db.flush()
        self.item = Item(order_id=order.id, quantity=3, price_sold=20, status=ItemStatus.SUBMITTED)
        shipment = Shipment(tracking_number="TRACK")
        self.db.add_all([self.item, shipment])
        self.db.flush()
        self.db.add(ShipmentItem(item_id=self.item.id, shipment_id=shipment.id))
        self.db.commit()

    def check(self, **changes):
        receipt = api._parse_receipt_row({"receipt_id": "R1", "tracking_id": "TRACK", "quantity": 2,
                                          "total": "40.00", "reward": "$6.00", **changes})
        with patch.object(api, "list_receipts", return_value=[receipt]), patch.object(api, "list_receipt_analytics", return_value=[]):
            return receiving.check_receiving_for_group(self.db, buying_group_id=self.group.id)

    def test_reward_parses_without_conflating_missing_and_zero(self):
        for raw, expected in [(None, None), ("", None), ("bad", None), (0, Decimal(0)), ("$1,234.50", Decimal("1234.50"))]:
            with self.subTest(raw=raw):
                self.assertEqual(api._parse_receipt_row({"reward": raw}).reward, expected)

    def test_under_received_reward_only_belongs_to_received_line(self):
        result = self.check()
        self.assertIsNone(result.error)
        received, remainder = result.matches
        self.assertEqual(received.reward, Decimal("6.00"))
        self.assertEqual(received.received_ext_total, Decimal("40.00"))
        self.assertIsNone(remainder.reward)
        session = ReceivingReviewSession.model_validate(result.session_payload)
        self.assertEqual(session.matches[0].reward, "6.00")

    def test_shared_tracking_reward_is_allocated_by_quantity(self):
        self.item.quantity = 1
        self.db.add(Item(order_id=self.item.order_id, quantity=1, price_sold=20, status=ItemStatus.SUBMITTED,
                         shipment_items=[ShipmentItem(shipment_id=self.item.shipment_items[0].shipment_id)]))
        self.db.commit()
        result = self.check()
        self.assertEqual([m.reward for m in result.matches], [Decimal("3.00"), Decimal("3.00")])

    def test_existing_receipt_can_backfill_reward_and_stops_reappearing(self):
        self.item.quantity = 2
        self.item.status = ItemStatus.SCANNED
        self.item.receipt_id = "R1"
        self.db.commit()
        result = self.check()
        self.assertEqual(len(result.matches), 1)
        self.assertTrue(result.matches[0].update_receipt_only)
        bulk_update_items(ItemBulkUpdateRequest(updates=[ItemBulkUpdateEntry(item_id=self.item.id, reward="6.00")]), db=self.db, _=None)
        self.assertEqual(ItemRead.model_validate(self.item).reward, Decimal("6.00"))
        self.assertEqual(self.item.price_sold, Decimal("20.00"))
        self.assertEqual(self.check().matches, [])

    def test_unknown_receipt_manual_link_and_apply_preserve_reward(self):
        receipt = self.check(tracking_id="UNKNOWN", quantity=3, total="60.00").session_payload["unknown_receipts"][0]
        self.assertEqual(receipt["reward"], "6.00")
        receiving.save_receiving_review_pending(self.db, {"buying_group_id": self.group.id, "matches": [], "unknown_receipts": [receipt]})
        linked = link_receipt(self.db, ReceivingReceiptLinkRequest(buying_group_id=self.group.id,
                              receipt_key=receipt_key(receipt), item_ids=[self.item.id], receipt_tracking_number="UNKNOWN"))
        match = linked["matches"][0]
        self.assertEqual(match["reward"], "6.00")
        bulk_update_items(ItemBulkUpdateRequest(updates=[ItemBulkUpdateEntry(item_id=self.item.id,
            receipt_id="R1", receiving_group_id=self.group.id, receiving_row_id=match["row_id"])]), db=self.db, _=None)
        self.assertEqual(self.item.reward, Decimal("6.00"))
        self.assertEqual(self.item.price_sold, Decimal("20.00"))
