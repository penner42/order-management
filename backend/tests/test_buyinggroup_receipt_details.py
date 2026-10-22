"""BG confirmed products, rather than header quantities, drive receiving."""
from decimal import Decimal
import unittest
from unittest.mock import patch

from app.models import Item, ShipmentItem
from app.models.item import ItemStatus
from app.routers.items import bulk_update_items
from app.schemas.item import ItemBulkUpdateEntry, ItemBulkUpdateRequest, ItemRead, ItemSplitRequest
from app.utils.item_split import split_item_record
from app.utils.receiving_links import applied_receipt_pairs, receipt_is_applied
from app.utils.buying_group_apis import buyinggroup as api, check_receiving as receiving
from tests import test_buyinggroup_rewards as reward_tests


class ReceiptDetailsTests(unittest.TestCase):
    setUp = reward_tests.BuyingGroupRewardTests.setUp

    def product(self, *, name="HP laptop", upc="199764359179", qty=1,
                price="199.00", verified=True):
        return {"item_id": name, "upc": upc, "qty": qty, "price": price,
                "total": str(Decimal(price) * qty), "verified": verified}

    def check(self, lines, *, error=None, alternate=False, reward="6.00"):
        receipt = api._parse_receipt_row({
            "key": "internal-key", "receipt_id": "R1", "tracking_id": "ALT" if alternate else "TRACK",
            "alt_tracking": "TRACK" if alternate else None,
            "quantity": 2, "total": "199.00", "status": "Partially Verified",
        })
        with (patch.object(api, "list_receipts", return_value=[receipt]),
              patch.object(api, "list_tracking_rewards", return_value={"track": Decimal(reward)}),
              patch.object(api, "get_receipt_details", return_value={"receipt_id": "R1", "received_items": lines},
                           side_effect=error) as fetch):
            result = receiving.check_receiving_for_group(self.db, buying_group_id=self.group.id)
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.kwargs["receipt_key"], "internal-key")
        return result

    def laptop(self):
        self.item.quantity = 2
        self.item.price_sold = Decimal("199.00")
        self.item.description = "HP laptop"
        self.db.commit()

    def test_partial_receipt_splits_missing_unit_and_persists_upc(self):
        self.laptop()
        lines = [self.product(), self.product(name="Task", upc="987654321098", price="0.01", verified=False)]
        result = self.check(lines)
        received, missing = result.matches
        self.assertEqual((received.quantity, received.received_ext_total, received.receipt_upc),
                         (1, Decimal("199.00"), "199764359179"))
        self.assertFalse(received.amount_discrepancy)
        self.assertEqual(received.split_keep_quantity, 1)
        self.assertEqual((missing.match_kind, missing.quantity), ("not_received", 1))
        self.assertEqual(received.reward, Decimal("6.00"))
        unresolved, = result.session_payload["unknown_receipts"]
        self.assertEqual(unresolved["description"], "Task")
        self.assertFalse(unresolved["linkable"])
        remainder = split_item_record(self.db, self.item, ItemSplitRequest(
            keep_quantity=received.split_keep_quantity, copy_tracking=True, split_off_status=ItemStatus.NEEDS_REVIEW))
        bulk_update_items(ItemBulkUpdateRequest(updates=[ItemBulkUpdateEntry(
            item_id=self.item.id, status=ItemStatus.SCANNED, receipt_id=received.receipt_id,
            receipt_tracking_number=received.tracking_number, receipt_upc=received.receipt_upc,
            reward=received.reward,
        )]), db=self.db, _=None)
        self.db.refresh(self.item)
        self.assertEqual(ItemRead.model_validate(self.item).receipt_upc, "199764359179")
        self.assertEqual(self.item.price_sold, Decimal("199.00"))
        self.assertIsNone(remainder.receipt_upc)
        self.assertIsNone(remainder.receipt_id)
        self.assertEqual(remainder.status, ItemStatus.NEEDS_REVIEW)
        self.assertEqual(remainder.shipment_items[0].shipment_id, self.item.shipment_items[0].shipment_id)
        self.assertEqual(self.check(lines).matches, [])

    def test_full_receipt_and_alternate_tracking(self):
        self.laptop()
        match, = self.check([self.product(qty=2)], alternate=True).matches
        self.assertEqual(match.quantity, 2)
        self.assertIsNone(match.split_keep_quantity)
        self.assertFalse(match.amount_discrepancy)

    def test_product_prices_are_not_averaged_and_reward_is_not_duplicated(self):
        self.laptop()
        self.item.quantity = 1
        other = Item(order_id=self.item.order_id, description="Apple pencil", price_sold=90,
                     quantity=1, status=ItemStatus.SUBMITTED,
                     shipment_items=[ShipmentItem(shipment_id=self.item.shipment_items[0].shipment_id)])
        self.db.add(other)
        self.db.commit()
        matches = self.check([self.product(), self.product(name="Apple pencil", upc="000012345678", price="90.00")]).matches
        self.assertEqual({m.item_id: m.received_ext_total for m in matches},
                         {self.item.id: Decimal("199.00"), other.id: Decimal("90.00")})
        self.assertEqual(sum(m.reward for m in matches), Decimal("6.00"))

    def test_real_price_difference_remains_discrepancy(self):
        self.laptop()
        match, = self.check([self.product(qty=2, price="198.00")]).matches
        self.assertTrue(match.amount_discrepancy)
        self.assertIsNone(match.split_keep_quantity)

    def test_same_upc_at_two_prices_keeps_both_product_allocations(self):
        self.laptop()
        result = self.check([self.product(), self.product(price="198.00")])
        self.assertEqual(sum(m.received_qty or 0 for m in result.matches), 2)
        self.assertEqual({m.received_ext_total for m in result.matches}, {Decimal("199.00"), Decimal("198.00")})
        self.assertEqual(sum(m.amount_discrepancy for m in result.matches), 1)

    def test_equal_price_products_without_names_are_ambiguous(self):
        self.item.description = None
        self.db.commit()
        result = self.check([self.product(price="20.00"), self.product(name="Pencil", upc="other", price="20.00")])
        self.assertEqual(result.matches, [])
        self.assertEqual(len(result.session_payload["unmatched"]), 1)

    def test_saved_upc_overrides_ambiguous_price(self):
        self.item.receipt_upc = "199764359179"
        self.item.description = None
        self.item.quantity = 1
        self.db.commit()
        result = self.check([self.product(price="20.00"), self.product(name="Pencil", upc="other", price="20.00")])
        self.assertEqual(result.matches[0].receipt_upc, "199764359179")

    def test_same_price_different_product_is_not_received(self):
        self.laptop()
        result = self.check([self.product(name="Apple Pencil Pro", upc="other")])
        self.assertEqual(result.matches, [])
        self.assertEqual(result.session_payload["unknown_receipts"][0]["receipt_upc"], "other")

    def test_other_product_on_applied_receipt_stays_available(self):
        self.item.receipt_id = "R1"
        self.item.receipt_tracking_number = "TRACK"
        self.item.receipt_upc = "199764359179"
        self.db.commit()
        pairs = applied_receipt_pairs(self.db, self.group.id)
        receipt = {"receipt_id": "R1", "tracking_number": "TRACK", "receipt_upc": "other"}
        self.assertFalse(receipt_is_applied(receipt, pairs))
        self.assertTrue(receipt_is_applied({**receipt, "receipt_upc": self.item.receipt_upc}, pairs))

    def test_reward_rounding_preserves_tracking_total_across_three_lines(self):
        self.item.quantity = 1
        for _ in range(2):
            self.db.add(Item(order_id=self.item.order_id, quantity=1, price_sold=20,
                             status=ItemStatus.SUBMITTED, shipment_items=[ShipmentItem(
                                 shipment_id=self.item.shipment_items[0].shipment_id)]))
        self.db.commit()
        matches = self.check([self.product(qty=3, price="20.00")], reward="0.02").matches
        self.assertEqual(len(matches), 3)
        self.assertEqual(sum(m.reward for m in matches), Decimal("0.02"))

    def test_unconfirmed_evidence_is_removed_when_source_confirms_it(self):
        self.laptop()
        first = self.check([self.product(verified=False)])
        receiving.save_receiving_review_pending(self.db, first.session_payload)
        second = self.check([self.product(qty=2)])
        receiving.save_receiving_review_pending(self.db, second.session_payload)
        payload, = receiving.list_receiving_review_pending_payloads(self.db)
        self.assertEqual(payload["unknown_receipts"], [])

    def test_detail_failure_and_invalid_confirmation_never_use_header_quantity(self):
        for error, lines in [(RuntimeError("unavailable"), []),
                             (None, [{**self.product(), "verified": "false"}])]:
            with self.subTest(error=error):
                result = self.check(lines, error=error)
                self.assertEqual(result.matches, [])
                self.assertFalse(result.session_payload["unknown_receipts"][0]["linkable"])

    def test_upc_backfill_for_received_line_does_not_split_or_change_status(self):
        self.laptop()
        self.item.status = ItemStatus.SCANNED
        self.item.receipt_id = "R1"
        self.item.reward = Decimal("6.00")
        self.db.commit()
        match, = self.check([self.product()]).matches
        self.assertTrue(match.update_receipt_only)
        # Backfills cannot offer a split on a received or payment-linked line.
        self.assertIsNone(match.split_keep_quantity)


class ReceiptDetailsApiTests(unittest.TestCase):
    def fetch(self, response):
        with (patch.object(api, "_ensure_tokens", return_value=api._AuthTokens("access")),
              patch.object(api, "_multipart_request", return_value=(200, "", response)) as request):
            result = api.get_receipt_details(base_url="https://example.test", username="u", password="p", receipt_key="internal")
        self.assertTrue(request.call_args.kwargs["url"].endswith("/v1/receipt/get_details"))
        self.assertEqual(request.call_args.kwargs["fields"], [("receipt", "internal")])
        return result

    def test_read_details_and_reject_missing_product_list(self):
        self.assertEqual(self.fetch({"status": "SUCCESS", "payload": {"received_items": []}}), {"received_items": []})
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            self.fetch({"status": "SUCCESS", "payload": {}})
