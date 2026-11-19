"""GoBuyMe tracking tests use fixtures and never call the remote buyer API."""
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import BuyingGroup, Item, Order, Shipment, ShipmentItem, Store
from app.models.item import ItemStatus
from app.routers.items import submit_item_tracking
from app.schemas.item import GoBuyMeTrackingSelection, ItemSubmitTrackingRequest
from app.utils.buying_group_apis import gobuyme
from app.utils.buying_group_apis.submit_tracking import batch_submit_pending_trackings, submit_item_tracking_to_group

UPC = "012345678905"
OTHER_UPC = "012345678912"
ASIN = "b012345678"


def reservation(reservation_id, upc=UPC, *, units=3, allocated=0):
    return {
        "id": reservation_id, "status": "confirmed", "units_committed": units,
        "deal": {"deal_items": [{"role": "primary", "quantity": 1, "product": {"upc": upc}}]},
        "shipment_commitments": [{"units_in_shipment": allocated}] if allocated else [],
    }


def box(reservation_id, units=2):
    return {"id": "box-id", "shipment_commitments": [{"commitment_id": reservation_id, "units_in_shipment": units}]}


def amazon_reservation(reservation_id, upc=None, url=f"https://www.amazon.com/dp/{ASIN}"):
    row = reservation(reservation_id, upc)
    line = row["deal"]["deal_items"][0]
    line["links"] = [{"product_links": {"retailer": "amazon", "url": url}}]
    return row


@patch("app.config.settings.group_submissions_enabled", True)
class GoBuyMeTrackingTests(unittest.TestCase):
    def setUp(self):
        self.tracker = gobuyme.BuyerTracker.__new__(gobuyme.BuyerTracker)
        self.tracker.api_key = "fixture-key"
        self.tracker.buyer_id = "buyer-id"

    def submit(self, items=None, *, commitment_id=None):
        return self.tracker.submit_tracking(
            tracking="1z 12345", items=items or [gobuyme.TrackingItem(UPC, 2)], order_number="store-order",
            commitment_id=commitment_id,
        )

    def test_selected_deal_item_upc_wins_over_parent_and_writes_exact_link(self):
        selected = reservation("right", OTHER_UPC)
        selected.update(selected_deal_item_id="selected", selected_deal_item={"quantity": 1, "product": {"upc": UPC}})
        with (
            patch.object(self.tracker, "_shipment", side_effect=[None, box("right")]),
            patch.object(self.tracker, "_commitments", return_value=[reservation("wrong", OTHER_UPC), selected]),
            patch.object(gobuyme, "_json_request", return_value={"status": "created", "box_id": "box-id"}) as request,
        ):
            self.assertEqual(self.submit([gobuyme.TrackingItem("00" + UPC, 2)]), "box-id")
        request.assert_called_once_with(
            gobuyme.ORIGIN + "/functions/v1/buyer-api",
            headers={"x-api-key": "fixture-key", "Content-Type": "application/json"},
            body={"action": "submit_tracking", "buyer_id": "buyer-id", "payload": {
                "tracking_number": "1Z12345", "units": 2, "commitment_ids": ["right"], "order_id": "store-order",
            }},
        )

    def test_skips_missing_ambiguous_and_insufficient_reservations(self):
        cases = [
            ([(None, 2)], [], "No matching"),
            ([(UPC, 1), (OTHER_UPC, 1)], [], "multiple UPCs"),
            ([(UPC, 2)], [reservation("first"), reservation("second")], "Multiple matching"),
            ([(UPC, 2)], [reservation("full", allocated=2)], "No matching"),
        ]
        for items, reservations, error in cases:
            with (
                self.subTest(error=error),
                patch.object(self.tracker, "_shipment", return_value=None),
                patch.object(self.tracker, "_commitments", return_value=reservations),
                patch.object(gobuyme, "_json_request") as request,
            ):
                with self.assertRaisesRegex(ValueError, error):
                    self.submit([gobuyme.TrackingItem(*item) for item in items])
                request.assert_not_called()

    def test_matching_retailer_id_submits_when_upc_is_missing(self):
        cases = [
            (gobuyme.TrackingItem(None, 2, "Amazon.com", ASIN.upper()), UPC),
            (gobuyme.TrackingItem(UPC, 2, "amazon", product_url=f"https://amazon.com/gp/product/{ASIN}"), None),
        ]
        for item, remote_upc in cases:
            with (
                self.subTest(item=item),
                patch.object(self.tracker, "_shipment", side_effect=[None, box("right")]),
                patch.object(self.tracker, "_commitments", return_value=[
                    amazon_reservation("wrong", remote_upc, f"https://amazon.com.example/dp/{ASIN}"),
                    amazon_reservation("right", remote_upc),
                ]),
                patch.object(gobuyme, "_json_request", return_value={"status": "created"}) as request,
            ):
                self.assertEqual(self.submit([item]), "box-id")
                self.assertEqual(request.call_args.kwargs["body"]["payload"]["commitment_ids"], ["right"])

    def test_conflicting_upc_requires_a_manual_choice(self):
        item = gobuyme.TrackingItem(UPC, 2, "Amazon", ASIN)
        row = amazon_reservation("chosen", OTHER_UPC)
        with (
            patch.object(self.tracker, "_shipment", side_effect=[None, None, box("chosen")]),
            patch.object(self.tracker, "_commitments", return_value=[row]) as commitments,
            patch.object(gobuyme, "_json_request", return_value={"status": "created"}) as request,
        ):
            with self.assertRaises(gobuyme.TrackerChoiceRequired) as failed:
                self.submit([item])
            choice = failed.exception.detail["choices"][0]
            self.assertEqual(choice["id"], "chosen")
            self.assertFalse(choice["exact_match"])
            request.assert_not_called()
            self.assertEqual(self.submit([item], commitment_id="chosen"), "box-id")
            commitments.assert_called_with(commitment_id="chosen")

    def test_manual_selection_checks_current_capacity(self):
        with (
            patch.object(self.tracker, "_shipment", return_value=None),
            patch.object(self.tracker, "_commitments", return_value=[reservation("chosen", units=1)]),
            patch.object(gobuyme, "_json_request") as request,
        ):
            with self.assertRaisesRegex(ValueError, "no longer available"):
                self.submit(commitment_id="chosen")
            request.assert_not_called()

    def test_walmart_and_costco_urls_yield_retailer_specific_ids(self):
        links = {
            "walmart": "https://www.walmart.com/ip/Product-Name/12345",
            "costco": "https://www.costco.com/product-name.product.12345.html",
        }
        for retailer, url in links.items():
            with self.subTest(retailer=retailer):
                line = {"product": {"product_links": [{"retailer": retailer, "url": url}]}}
                self.assertEqual(gobuyme._line_identifiers(line), (None, {(retailer, "12345")}))

    def test_explicit_retailer_identifiers_work_without_resolving_short_links(self):
        line = {"product": {"asin": ASIN.upper(), "bestbuy_sku": 12345, "product_links": [
            {"retailer": "Amazon", "url": "https://ftc.cash/EXAMPLE"},
        ]}}
        self.assertEqual(gobuyme._line_identifiers(line), (None, {("amazon", ASIN), ("bestbuy", "12345")}))

    def test_retry_checks_existing_link_and_does_not_resubmit(self):
        with (
            patch.object(self.tracker, "_shipment", return_value=box("already-linked")),
            patch.object(self.tracker, "_commitments", return_value=[reservation("already-linked")]),
            patch.object(gobuyme, "_json_request") as request,
        ):
            self.assertEqual(self.submit(), "box-id")
            with self.assertRaisesRegex(gobuyme.TrackerChoiceRequired, "already linked"):
                self.submit([gobuyme.TrackingItem(OTHER_UPC, 2)])
            request.assert_not_called()

    def test_existing_box_falls_back_to_order_number_only_without_conflicting_product_data(self):
        with (
            patch.object(self.tracker, "_shipment", return_value={**box("linked"), "order_id": "STORE-ORDER"}),
            patch.object(self.tracker, "_commitments", return_value=[reservation("linked")]),
            patch.object(gobuyme, "_json_request") as request,
        ):
            self.assertEqual(self.submit([gobuyme.TrackingItem(None, 2)]), "box-id")
            with self.assertRaises(gobuyme.TrackerChoiceRequired):
                self.submit([gobuyme.TrackingItem(OTHER_UPC, 2)])
            request.assert_not_called()

    def test_success_response_with_wrong_tracker_link_is_rejected(self):
        with (
            patch.object(self.tracker, "_shipment", side_effect=[None, box("wrong")]),
            patch.object(self.tracker, "_commitments", return_value=[reservation("right")]),
            patch.object(gobuyme, "_json_request", return_value={"status": "created"}),
        ):
            with self.assertRaisesRegex(RuntimeError, "did not confirm the expected Tracker link"):
                self.submit()

    def test_single_and_batch_submission_use_group_and_only_mark_verified_items(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            group = BuyingGroup(name="GoBuyMe", api_framework="gobuyme", api_username="test@example.com",
                                api_password="fixture-password", bearer_token="fixture-key")
            order = Order(store=Store(name="Test store"), store_order_number="store-order", buying_group=group)
            items = [Item(order=order, status=ItemStatus.SHIPPED, upc=UPC, quantity=1) for _ in range(3)]
            for item, number in zip(items, ["1z 12345", "1Z12345", "1Z99999"]):
                item.shipment_items = [ShipmentItem(shipment=Shipment(tracking_number=number))]
            db.add_all(items)
            db.commit()
            choice = gobuyme.TrackerChoiceRequired(
                "Choose the correct reservation", tracking="1Z12345", units=2, choices=[{"id": "reservation-id"}],
            )
            bad_selection = ItemSubmitTrackingRequest(gobuyme_selection=GoBuyMeTrackingSelection(
                commitment_id="reservation-id", tracking="1Z12345", units=1,
            ))
            with (
                patch.object(gobuyme, "BuyerTracker", return_value=self.tracker),
                patch.object(self.tracker, "submit_tracking", side_effect=choice) as submit,
            ):
                with self.assertRaises(HTTPException) as caught:
                    submit_item_tracking(items[0].id, db=db, _=None)
                self.assertEqual(caught.exception.status_code, 409)
                self.assertEqual(caught.exception.detail, choice.detail)
                with self.assertRaisesRegex(HTTPException, "shipment changed"):
                    submit_item_tracking(items[0].id, db=db, _=None, body=bad_selection)
                submit.assert_called_once()
            self.assertIs(items[0].status, ItemStatus.SHIPPED)
            with (
                patch.object(gobuyme, "BuyerTracker", return_value=self.tracker),
                patch.object(self.tracker, "submit_tracking", side_effect=["box-id", "box-id", ValueError("No matching reservation")]) as submit,
            ):
                result = submit_item_tracking_to_group(group, items[0], order, "1z 12345", sibling_items=items[:2])
                self.assertCountEqual(result.item_ids, [item.id for item in items[:2]])
                batch = batch_submit_pending_trackings(db, buying_group_id=group.id)
            self.assertEqual(batch.submitted_count, 2)
            self.assertIn("No matching reservation", batch.message)
            self.assertEqual(submit.call_args_list[1].kwargs["items"], [gobuyme.TrackingItem(UPC, 1, "Test store")] * 2)
            self.assertEqual([item.submission_id for item in items], ["box-id", "box-id", None])
            self.assertEqual(items[2].status, ItemStatus.SHIPPED)
