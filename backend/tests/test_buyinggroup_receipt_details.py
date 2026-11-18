"""BG confirmed products, rather than header quantities, drive receiving."""
from datetime import datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from app.models import Item, ReceivingReviewPending, ShipmentItem
from app.models.item import ItemStatus
from app.routers.items import bulk_update_items
from app.schemas.item import ItemBulkUpdateEntry, ItemBulkUpdateRequest, ItemRead, ItemSplitRequest
from app.schemas.buying_group import ReceivingReceiptLinkRequest, ReceivingReviewSession
from app.utils.item_split import split_item_record
from app.utils.receiving_links import applied_receipt_pairs, link_receipt, receipt_is_applied, receipt_key
from app.utils.buying_group_apis import buyinggroup as api, check_receiving as receiving
from tests import test_buyinggroup_rewards as reward_tests


class ReceiptDetailsTests(unittest.TestCase):
    def setUp(self):
        reward_tests.BuyingGroupRewardTests.setUp(self)
        self.item.description = "HP laptop"
        self.db.commit()

    def test_receipt_without_tracking_can_be_linked_and_applied_using_receipt_id(self):
        self.item.quantity = 2
        self.db.commit()
        def check():
            return reward_tests.BuyingGroupRewardTests.check(
                self, receipt_id="R-123456", tracking_id=None, rewards={})
        result = check()
        receipt, = result.session_payload["unknown_receipts"]
        self.assertEqual(receipt["tracking_number"], "R-123456")
        self.assertEqual(receipt["tracking_numbers"], ["R-123456"])
        receiving.save_receiving_review_pending(self.db, result.session_payload)
        staged = link_receipt(self.db, ReceivingReceiptLinkRequest(
            buying_group_id=self.group.id, receipt_key=receipt_key(receipt),
            item_ids=[self.item.id], receipt_tracking_number="R-123456"))
        match, = staged["matches"]
        bulk_update_items(ItemBulkUpdateRequest(updates=[ItemBulkUpdateEntry(
            item_id=self.item.id, status=ItemStatus.SCANNED, receipt_id=match["receipt_id"],
            receipt_tracking_number=match["receipt_tracking_number"],
            receiving_group_id=self.group.id, receiving_row_id=match["row_id"],
        )]), db=self.db, _=None)
        self.db.refresh(self.item)
        self.assertEqual(self.item.receipt_tracking_number, "R-123456")
        self.assertEqual(self.item.shipment_items[0].shipment.tracking_number, "TRACK")
        receiving.save_receiving_review_pending(self.db, check().session_payload)
        self.assertEqual(receiving.list_receiving_review_pending_payloads(self.db), [])

    def test_alternate_tracking_is_used_before_receipt_id(self):
        result = reward_tests.BuyingGroupRewardTests.check(
            self, receipt_id="R-123456", tracking_id=None, alt_tracking="ALT", rewards={})
        receipt, = result.session_payload["unknown_receipts"]
        self.assertEqual(receipt["tracking_number"], "ALT")
        self.assertEqual(receipt["tracking_numbers"], ["ALT"])

    def test_saved_blank_tracking_is_replaced_without_merging_distinct_products(self):
        result = reward_tests.BuyingGroupRewardTests.check(
            self, receipt_id="R-123456", tracking_id=None, rewards={})
        receipt, = result.session_payload["unknown_receipts"]
        old = {**receipt, "tracking_number": "", "tracking_numbers": []}
        other = {**old, "description": "Other product", "receipt_upc": "other-upc"}
        receiving.save_receiving_review_pending(self.db, {
            **result.session_payload, "unknown_receipts": [old, other]})
        # Reproduce a persisted snapshot written before receipt-ID fallbacks.
        row = self.db.get(ReceivingReviewPending, self.group.id)
        row.payload = {**row.payload, "unknown_receipts": [old, other, receipt]}
        self.db.commit()
        for _ in range(2):
            receiving.save_receiving_review_pending(self.db, result.session_payload)
            saved, = receiving.list_receiving_review_pending_payloads(self.db)
            self.assertEqual(len(saved["unknown_receipts"]), 2)
            self.assertEqual({r["description"] for r in saved["unknown_receipts"]},
                             {receipt["description"], "Other product"})
            self.assertTrue(all(r["tracking_number"] == "R-123456"
                                for r in saved["unknown_receipts"]))

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

    def test_tracking_reward_correction_keeps_commission_in_confirmed_payout(self):
        self.laptop()
        self.item.status = ItemStatus.SCANNED
        self.item.receipt_id = "R1"
        self.item.price_sold = Decimal("201.05")
        self.db.commit()
        product = {**self.product(qty=2), "total": "402.00"}
        match, = self.check([product], reward="0.10").matches
        self.assertEqual(match.tracking_reward_adjusted_price, Decimal("201.00"))
        self.assertEqual(match.reward, Decimal("0.10"))

        self.item.price_sold = Decimal("203.00")
        self.db.commit()
        match, = self.check([product], reward="0.10").matches
        self.assertIsNone(match.tracking_reward_adjusted_price)

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

    def test_imported_upc_matches_brother_printer_despite_different_product_names(self):
        self.item.description = (
            "Brother Wireless MFC-L2760DW Compact Monochrome All-in-One Laser Printer "
            "with Copy, Scan and Fax, Duplex and Mobile Printing"
        )
        self.item.upc = "012502672661"
        self.item.quantity = 1
        self.item.price_sold = Decimal("100.00")
        self.db.commit()
        match, = self.check([self.product(
            name="Brother-Wireless-Printer-2661", upc="012502672661", price="99.00",
        )]).matches
        self.assertEqual(match.item_id, self.item.id)
        self.assertEqual(match.receipt_upc, self.item.upc)
        self.assertTrue(match.amount_discrepancy)
        self.assertEqual(match.expected_amount, Decimal("100.00"))
        self.assertEqual(match.received_ext_total, Decimal("99.00"))

    def test_conflicting_imported_upc_rejects_same_name_and_price(self):
        self.laptop()
        self.item.upc = "012502672661"
        self.db.commit()
        self.assertEqual(self.check([self.product(qty=2)]).matches, [])

    def test_missing_receipt_upc_still_requires_product_name_agreement(self):
        self.laptop()
        self.item.upc = "012502672661"
        self.db.commit()
        self.assertEqual(self.check([self.product(name="Apple Pencil Pro", upc=None)]).matches, [])

    def test_single_line_tracking_accepts_weak_name_overlap_on_check_and_refresh(self):
        self.laptop()
        self.item.description = (
            'ASUS Vivobook X1404 14" FHD Laptop, Intel Core i3-1315U, 8GB RAM, '
            "128GB SSD, Windows 11 Home, Blue"
        )
        self.db.commit()
        result = self.check([self.product(name="Asus-Vivobook-14-Laptop-8536", qty=2, price="198.00")],
                            alternate=True)
        match, = result.matches
        self.assertEqual(match.item_id, self.item.id)
        self.assertEqual(match.quantity, 2)
        self.assertTrue(match.amount_discrepancy)
        receiving.save_receiving_review_pending(self.db, result.session_payload)
        with patch.object(api, "get_receipt_details", side_effect=AssertionError("No network on refresh")):
            payload, = receiving.list_receiving_review_pending_payloads(self.db)
        self.assertEqual([m["item_id"] for m in payload["matches"]], [self.item.id])

    def test_second_unconfirmed_line_keeps_normal_name_threshold(self):
        self.laptop()
        self.item.description = "HP 14 inch HD Windows laptop Intel processor 128GB"
        self.db.commit()
        result = self.check([
            self.product(name="HP notebook ep2111wm", qty=2),
            self.product(name="Apple Pencil Pro", upc="other", verified=False),
        ])
        self.assertEqual(result.matches, [])

    def test_weak_name_match_cannot_take_supply_from_another_shipment_item(self):
        self.laptop()
        self.item.quantity = 1
        self.item.description = "HP 14 inch HD Windows laptop Intel processor 128GB"
        other = Item(order_id=self.item.order_id, description="HP notebook ep2111wm",
                     price_sold=199, quantity=1, status=ItemStatus.SUBMITTED,
                     shipment_items=[ShipmentItem(shipment_id=self.item.shipment_items[0].shipment_id)])
        self.db.add(other)
        self.db.commit()
        match, = self.check([self.product(name=other.description)]).matches
        self.assertEqual(match.item_id, other.id)

    def test_name_fallback_matches_despite_large_price_discrepancy(self):
        self.laptop()
        match, = self.check([self.product(qty=2, upc=None, price="150.00")]).matches
        self.assertTrue(match.amount_discrepancy)
        self.assertEqual(match.received_ext_total, Decimal("300.00"))

    def test_name_fallback_prefers_product_name_over_closer_price(self):
        self.item.description = "Brother Wireless Printer MFC-L2760DW"
        self.item.quantity = 1
        self.item.price_sold = Decimal("99.00")
        self.db.commit()
        match, = self.check([
            self.product(name=self.item.description, upc="012502672661", price="90.00"),
            self.product(name="Brother Wireless Printer Scanner", upc="other", price="99.00"),
        ]).matches
        self.assertEqual(match.receipt_upc, "012502672661")
        self.assertEqual(match.received_ext_total, Decimal("90.00"))
        self.assertTrue(match.amount_discrepancy)

    def test_ambiguous_name_fallback_is_not_resolved_by_price(self):
        self.item.description = "Brother Wireless Printer"
        self.item.quantity = 1
        self.item.price_sold = Decimal("99.00")
        self.db.commit()
        result = self.check([
            self.product(name=self.item.description, upc="012502672661", price="90.00"),
            self.product(name=self.item.description, upc="other", price="99.00"),
        ])
        self.assertEqual(result.matches, [])

    def test_price_alone_cannot_match_when_names_and_upcs_are_missing(self):
        self.item.description = None
        self.item.quantity = 1
        self.item.price_sold = Decimal("199.00")
        self.db.commit()
        self.assertEqual(self.check([self.product(name=None, upc=None)]).matches, [])

    def test_saved_receipt_upc_remains_authoritative_over_imported_upc(self):
        self.laptop()
        self.item.upc = "012502672661"
        self.item.receipt_upc = "199764359179"
        self.db.commit()
        self.assertEqual(len(self.check([self.product(qty=2)]).matches), 1)
        self.assertEqual(self.check([self.product(qty=2, upc=self.item.upc)]).matches, [])

    def test_editing_imported_upc_rematches_cached_receipt_without_network(self):
        self.laptop()
        result = self.check([self.product(name="Brother-Wireless-Printer-2661", upc="012502672661")])
        self.assertEqual(result.matches, [])
        receiving.save_receiving_review_pending(self.db, result.session_payload)
        self.item.upc = "012502672661"
        self.item.quantity = 1
        self.db.commit()
        with patch.object(api, "get_receipt_details", side_effect=AssertionError("No network on refresh")):
            payload, = receiving.list_receiving_review_pending_payloads(self.db)
        match, = payload["matches"]
        self.assertEqual(match["item_id"], self.item.id)
        self.assertEqual(match["receipt_upc"], self.item.upc)
        self.assertEqual(payload["unknown_receipts"], [])

    def test_same_price_different_product_is_not_received(self):
        self.laptop()
        result = self.check([self.product(name="Apple Pencil Pro", upc="other")])
        self.assertEqual(result.matches, [])
        self.assertEqual(result.session_payload["unknown_receipts"][0]["receipt_upc"], "other")

    def test_unmatched_product_on_known_tracking_survives_saved_review_and_refresh(self):
        self.laptop()
        result = self.check([self.product(name="Apple Pencil Pro", upc="other")])
        receiving.save_receiving_review_pending(self.db, result.session_payload)
        for _ in range(2):
            payload, = receiving.list_receiving_review_pending_payloads(self.db)
            self.assertEqual(payload["matches"], [])
            receipt, = payload["unknown_receipts"]
            self.assertEqual(receipt["tracking_number"], "TRACK")
            self.assertEqual(receipt["receipt_upc"], "other")
            self.assertTrue(receipt["linkable"])

    def test_extra_product_on_matched_tracking_stays_available_for_manual_link(self):
        self.laptop()
        result = self.check([
            self.product(qty=2),
            self.product(name="Apple Pencil Pro", upc="other", price="90.00"),
        ])
        receiving.save_receiving_review_pending(self.db, result.session_payload)
        payload, = receiving.list_receiving_review_pending_payloads(self.db)
        match, = payload["matches"]
        self.assertEqual(match["item_id"], self.item.id)
        receipt, = payload["unknown_receipts"]
        self.assertEqual(receipt["receipt_upc"], "other")
        self.assertEqual(receipt["quantity"], 1)
        self.assertEqual(receipt["received_ext_total"], "90.00")

    def test_unresolved_product_disappears_when_local_edit_creates_match(self):
        self.laptop()
        result = self.check([self.product(name="Apple Pencil Pro", upc="other")])
        receiving.save_receiving_review_pending(self.db, result.session_payload)
        self.item.description = "Apple Pencil Pro"
        self.item.quantity = 1
        self.db.commit()
        payload, = receiving.list_receiving_review_pending_payloads(self.db)
        self.assertEqual(len(payload["matches"]), 1)
        self.assertEqual(payload["unknown_receipts"], [])

    def test_current_leftover_quantity_survives_when_same_product_is_matched(self):
        self.laptop()
        self.item.quantity = 1
        self.db.commit()
        result = self.check([self.product(qty=2)])
        receiving.save_receiving_review_pending(self.db, result.session_payload)
        payload, = receiving.list_receiving_review_pending_payloads(self.db)
        self.assertEqual(payload["matches"][0]["quantity"], 1)
        receipt, = payload["unknown_receipts"]
        self.assertEqual(receipt["quantity"], 1)

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
            self.db.add(Item(order_id=self.item.order_id, description="HP laptop", quantity=1, price_sold=20,
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

    def test_upc_only_review_shows_saved_scan_date_and_confirmed_amounts(self):
        self.laptop()
        self.item.status = ItemStatus.SCANNED
        self.item.scanned_at = datetime(2026, 10, 12, tzinfo=timezone.utc)
        self.item.receipt_id = "R1"
        self.item.price_sold = Decimal("201.00")
        self.item.reward = Decimal("0.05")
        self.db.commit()
        result = self.check([{**self.product(qty=2), "total": "402.00"}], reward="0.05")
        match, = result.matches
        self.assertEqual(match.received_ext_total, Decimal("402.00"))
        self.assertEqual(match.confirmed_reward, Decimal("0.05"))
        self.assertIsNone(match.reward)
        self.assertIsNone(match.scanned_at)
        self.assertIsNone(match.tracking_reward_adjusted_price)
        review_match, = ReceivingReviewSession.model_validate(result.session_payload).matches
        self.assertEqual(review_match.saved_scanned_at[:10], "2026-10-12")


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
