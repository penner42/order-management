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

    def check(self, *, rewards=None, reward_error=None, extra_receipts=(), **changes):
        receipt = api._parse_receipt_row({"key": "R1", "receipt_id": "R1", "tracking_id": "TRACK", "quantity": 2,
                                          "total": "40.00", **changes})
        rows = [receipt, *extra_receipts]
        for row in rows:
            row.raw.setdefault("key", row.receipt_id)
        def details(**kwargs):
            row = next(r for r in rows if r.raw["key"] == kwargs["receipt_key"])
            return {"receipt_id": row.receipt_id, "received_items": [{
                "verified": True, "qty": row.quantity, "price": str(row.total / row.quantity),
                "total": str(row.total), "item_id": "Test product",
            }]}
        if rewards is None:
            rewards = {api.tracking_reward_key(receipt.tracking_id): Decimal("6.00")}
        with (patch.object(api, "list_receipts", return_value=[receipt, *extra_receipts]),
              patch.object(api, "get_receipt_details", side_effect=details),
              patch.object(api, "list_tracking_rewards", return_value=rewards, side_effect=reward_error),
              patch.object(api, "list_receipt_analytics", return_value=[])):
            return receiving.check_receiving_for_group(self.db, buying_group_id=self.group.id)

    def test_reward_comes_from_tracking_even_when_receipt_has_no_reward(self):
        self.assertEqual(self.check().matches[0].reward, Decimal("6.00"))
        self.assertIsNone(self.check(rewards={}).matches[0].reward)
        self.assertEqual(self.check(rewards={"track": Decimal(0)}).matches[0].reward, Decimal("0.00"))

    def test_reward_failure_keeps_receiving_available_without_overwriting_saved_reward(self):
        self.item.reward = Decimal("5.00")
        self.db.commit()
        result = self.check(reward_error=RuntimeError("tracking service unavailable"))
        self.assertIsNone(result.error)
        self.assertIsNone(result.matches[0].reward)
        self.assertIn("Tracking rewards unavailable", result.message)
        self.assertEqual(self.item.reward, Decimal("5.00"))

    def test_alt_tracking_can_supply_reward(self):
        self.assertEqual(self.check(alt_tracking="ALT", rewards={"alt": Decimal("7.00")}).matches[0].reward, Decimal("7.00"))

    def test_multiple_receipts_do_not_duplicate_tracking_reward(self):
        extra = api._parse_receipt_row({"receipt_id": "R2", "tracking_id": "TRACK", "quantity": 1, "total": "20.00"})
        self.assertEqual(sum(m.reward or 0 for m in self.check(extra_receipts=[extra]).matches), Decimal("6.00"))

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

    def walmart(self):
        self.item.order.store.name = "Walmart"
        self.item.order.store_order_number = "200015408984219"
        self.db.commit()
        return self.item.order.store_order_number

    def test_walmart_receipt_matches_either_tracking_or_order_number_and_applies(self):
        order_number = self.walmart()
        for number in ["TRACK", order_number]:
            with self.subTest(number=number):
                result = self.check(tracking_id=number, quantity=3, total="60.00")
                self.assertIsNone(result.error)
                self.assertEqual(len(result.matches), 1)
                self.assertEqual(result.matches[0].item_id, self.item.id)
                self.assertEqual(result.matches[0].received_qty, 3)
                self.assertEqual(result.session_payload["unknown_receipts"], [])
        bulk_update_items(ItemBulkUpdateRequest(updates=[ItemBulkUpdateEntry(
            item_id=self.item.id, status=ItemStatus.SCANNED, receipt_id="R1",
        )]), db=self.db, _=None)
        self.assertEqual(self.item.status, ItemStatus.SCANNED)
        self.assertEqual(self.item.receipt_id, "R1")

    def test_walmart_order_in_alternate_tracking_matches(self):
        number = self.walmart()
        result = self.check(tracking_id="WAREHOUSE", alt_tracking=number, quantity=3, total="60.00")
        self.assertEqual(len(result.matches), 1)
        self.assertEqual(result.matches[0].received_qty, 3)
        self.assertEqual(result.session_payload["unknown_receipts"], [])

    def test_walmart_order_receipt_quantity_is_shared_across_packages(self):
        number = self.walmart()
        self.item.quantity = 1
        other = Item(order=self.item.order, quantity=1, price_sold=20, status=ItemStatus.SUBMITTED,
                     shipment_items=[ShipmentItem(shipment=Shipment(tracking_number="TRACK-2"))])
        self.db.add(other)
        self.db.commit()
        result = self.check(tracking_id=number, quantity=1, total="20.00")
        received = [match for match in result.matches if match.received_qty]
        self.assertEqual(sum(match.received_qty for match in received), 1)
        self.assertEqual(len(received), 1)

    def test_walmart_receipt_with_both_identifiers_does_not_double_quantity(self):
        number = self.walmart()
        result = self.check(tracking_id="TRACK", alt_tracking=number, quantity=2)
        self.assertEqual(sum(match.received_qty or 0 for match in result.matches), 2)

    def test_repeated_check_does_not_reuse_received_quantity(self):
        for walmart in (False, True):
            with self.subTest(walmart=walmart):
                number = self.walmart() if walmart else "TRACK"
                self.item.quantity = 1
                self.item.status = ItemStatus.SCANNED
                self.item.receipt_id = "R1"
                self.item.reward = Decimal("6.00")
                other = Item(order=self.item.order, quantity=2, price_sold=20,
                             status=ItemStatus.SUBMITTED, shipment_items=[ShipmentItem(
                                 shipment=Shipment(tracking_number="PACKAGE-2" if walmart else "TRACK"))])
                self.db.add(other)
                self.db.commit()
                result = self.check(tracking_id=number, quantity=1, total="20.00")
                self.assertEqual(result.matches, [])
                # When the warehouse receives a second unit, only that new unit
                # can be allocated; the other unit remains an under-received split.
                result = self.check(tracking_id=number, quantity=2, total="40.00")
                received = [m for m in result.matches if not m.update_receipt_only and m.received_qty]
                self.assertEqual(sum(m.received_qty for m in received), 1)
                self.assertEqual(received[0].item_id, other.id)
                self.assertEqual(received[0].split_keep_quantity, 1)
                self.db.delete(other)
                self.db.commit()

    def test_walmart_order_receipt_does_not_match_another_order(self):
        self.walmart()
        result = self.check(tracking_id="OTHER-ORDER", quantity=3, total="60.00")
        self.assertEqual(result.matches, [])
        self.assertEqual(len(result.session_payload["unknown_receipts"]), 1)

    def test_other_store_order_number_is_not_a_receiving_alias(self):
        self.item.order.store_order_number = "OTHER-ORDER"
        self.db.commit()
        self.assertEqual(self.check(tracking_id="OTHER-ORDER").matches, [])

    def test_walmart_scanned_item_can_update_receipt_details_by_order_number(self):
        number = self.walmart()
        self.item.quantity = 2
        self.item.status = ItemStatus.SCANNED
        self.item.receipt_id = "R1"
        self.db.commit()
        result = self.check(tracking_id=number)
        self.assertEqual(len(result.matches), 1)
        self.assertTrue(result.matches[0].update_receipt_only)
        self.assertEqual(result.matches[0].reward, Decimal("6.00"))

    def test_walmart_package_and_order_receipts_stay_applied(self):
        number = self.walmart()
        self.item.quantity = 1
        self.item.status = ItemStatus.SCANNED
        self.item.receipt_id = "R1"
        self.item.receipt_tracking_number = "TRACK"
        self.item.reward = Decimal(0)
        other = Item(order=self.item.order, quantity=1, price_sold=20,
                     status=ItemStatus.SCANNED, receipt_id="R2", receipt_tracking_number=number,
                     reward=Decimal(0), shipment_items=[ShipmentItem(
                         shipment=Shipment(tracking_number=number))])
        self.db.add(other)
        self.db.commit()
        extra = api._parse_receipt_row({"receipt_id": "R2", "tracking_id": number,
                                       "quantity": 1, "total": "20.00"})
        kwargs = dict(quantity=1, total="20.00", rewards={"track": Decimal(0), number: Decimal(0)},
                      extra_receipts=[extra])
        for saved_tracking in (True, False):
            with self.subTest(saved_tracking=saved_tracking):
                if not saved_tracking:
                    self.item.receipt_tracking_number = None
                    other.receipt_tracking_number = None
                    self.db.commit()
                self.assertEqual(self.check(**kwargs).matches, [])
                self.assertEqual(self.check(**kwargs).matches, [])
                self.assertEqual(self.item.receipt_id, "R1")
                self.assertEqual(other.receipt_id, "R2")

        # A real reward change remains actionable on the assigned receipt,
        # and applying it must not expose the sibling receipt on the next check.
        kwargs["rewards"] = {"track": Decimal("2.00"), number: Decimal(0)}
        matches = self.check(**kwargs).matches
        self.assertEqual([(m.item_id, m.receipt_id) for m in matches], [(self.item.id, "R1")])
        bulk_update_items(ItemBulkUpdateRequest(updates=[ItemBulkUpdateEntry(
            item_id=m.item_id, receipt_id=m.receipt_id,
            receipt_tracking_number=m.tracking_number, reward=m.reward,
        ) for m in matches]), db=self.db, _=None)
        self.assertEqual(self.check(**kwargs).matches, [])


class TrackingRewardApiTests(unittest.TestCase):
    def fetch(self, responses, page_size=2):
        def response(**kwargs):
            self.assertTrue(kwargs["url"].endswith("/v1/order/get_orders"))
            page = int(dict(kwargs["fields"])["page"])
            raw = responses[page - 1]
            return 200, "", raw

        with (patch.object(api, "_ensure_tokens", return_value=api._AuthTokens("access", "refresh")),
              patch.object(api, "_multipart_request", side_effect=response)):
            return api.list_tracking_rewards(base_url="https://example.test", username="user", password="pass", page_size=page_size)

    def test_tracking_api_paginates_by_count_and_maps_primary_and_alternate(self):
        rewards = self.fetch([
            {"status": "SUCCESS", "payload": {"count": 3, "orders": [
                {"tracking_id": "Tr- ack", "alt_tracking_id": "ALT", "reward": "$6.50"},
                {"tracking_id": "ZERO", "reward": 0}]}},
            {"status": "SUCCESS", "payload": {"count": 3, "orders": [
                {"tracking_id": "LAST", "reward": "1,234.50"}]}},
        ])
        self.assertEqual(rewards, {"track": Decimal("6.50"), "alt": Decimal("6.50"), "zero": Decimal(0), "last": Decimal("1234.50")})

    def test_missing_invalid_and_nonfinite_rewards_are_not_zero(self):
        rows = [{"tracking_id": str(i), "reward": value} for i, value in enumerate([None, "", "bad", "NaN", "Infinity"])]
        self.assertEqual(self.fetch([{"payload": {"pages": 1, "orders": rows}}]), {})

    def test_expired_session_refreshes_and_retries_same_page(self):
        with (patch.object(api, "_ensure_tokens", return_value=api._AuthTokens("old")),
              patch.object(api, "_reauth_tokens", return_value=api._AuthTokens("new")) as refresh,
              patch.object(api, "_multipart_request", side_effect=[
                  api._SessionExpired("expired"),
                  (200, "", {"payload": {"count": 1, "orders": [{"tracking_id": "TRACK", "reward": "2.00"}]}}),
              ]) as request):
            self.assertEqual(api.list_tracking_rewards(base_url="https://example.test", username="user", password="pass"), {"track": Decimal("2.00")})
        refresh.assert_called_once()
        self.assertEqual(dict(request.call_args.kwargs["fields"])["page"], "1")

    def test_failed_tracking_response_is_reported(self):
        with self.assertRaisesRegex(RuntimeError, "tracking unavailable"):
            self.fetch([{"status": "FAIL", "message": "tracking unavailable"}])
