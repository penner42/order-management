"""Duplicate tracking submissions require a matching remote order before local updates."""
import json
import unittest
from contextlib import ExitStack
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


class BuyingGroupDuplicateTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("app.config.settings.group_submissions_enabled", True))
        self.stack.enter_context(patch.object(api, "_ensure_tokens", return_value=api._AuthTokens("access", "refresh")))
        self.stack.enter_context(patch.object(api.time, "sleep"))
        self.update = self.stack.enter_context(patch.object(api, "update_order"))
        self.network = self.stack.enter_context(patch.object(api, "_multipart_request", side_effect=self.response))
        self.duplicates = ["EXISTING"]
        self.added = []
        self.invalid = []
        self.rows = {"existing": [{"key": "api-existing", "order_id": "O-100", "tracking_id": "EXISTING"}]}

    def response(self, *, url, fields, **kwargs):
        if url.endswith("/add_trackings"):
            payload = {"status": "SUCCESS" if self.added else "FAIL",
                       "message": "Added" if self.added else "No trackings were added",
                       "payload": {"added_trackings": self.added, "duplicate_trackings": self.duplicates,
                                   "invalid_trackings": self.invalid, "invalid_otps": []}}
        else:
            self.assertTrue(url.endswith("/get_orders"))
            tracking = dict(fields)["tracking_id"].casefold()
            payload = {"status": "SUCCESS", "payload": {"orders": self.rows.get(tracking, [])}}
        return 200, json.dumps(payload), payload

    def submit(self, *trackings):
        return api.submit_trackings(base_url="https://example.test", api_url=None,
            username="user", password="password",
            trackings=[api.BuyinggroupTrackingEntry(tn, amount=20, order_num="LOCAL", note="Local")
                       for tn in trackings])

    def test_all_duplicates_recover_ids_without_updating_existing_remote_metadata(self):
        result = self.submit("existing")
        self.assertTrue(result.success)
        self.assertEqual(result.affected, 1)
        self.assertEqual(result.succeeded, ["existing"])
        self.assertEqual(result.failed, [])
        self.assertEqual(result.submission_ids["existing"].display_id, "O-100")
        self.assertEqual(result.submission_ids["existing"].api_id, "api-existing")
        self.assertIn("verified 1 already present", result.message)
        self.update.assert_not_called()

    def test_unverified_duplicate_stays_failed(self):
        self.rows = {}
        result = self.submit("EXISTING")
        self.assertFalse(result.success)
        self.assertEqual(result.affected, 0)
        self.assertEqual(result.succeeded, [])
        self.assertEqual(result.failed, ["EXISTING"])
        self.assertEqual(result.submission_ids, {})
        self.assertIn("left pending", result.message)
        self.update.assert_not_called()

    def test_unrelated_or_missing_tracking_in_lookup_does_not_confirm_duplicate(self):
        for row in ({"key": "wrong", "tracking_id": "OTHER"}, {"key": "no-tracking"}):
            with self.subTest(row=row):
                self.rows["existing"] = [row]
                result = self.submit("EXISTING")
                self.assertFalse(result.success)
                self.assertEqual(result.submission_ids, {})
        self.update.assert_not_called()

    def test_lookup_accepts_spacing_and_hyphen_variants(self):
        self.rows["existing"][0]["tracking_id"] = "exi-st ing"
        self.assertTrue(self.submit("EXISTING").success)

    def test_mixed_response_only_succeeds_added_and_verified_duplicates(self):
        self.added = ["NEW"]
        self.duplicates += ["UNKNOWN"]
        self.invalid = ["INVALID"]
        self.rows["new"] = [{"key": "api-new", "order_id": "O-200", "tracking_id": "NEW"}]
        result = self.submit("NEW", "EXISTING", "UNKNOWN", "INVALID", "UNREPORTED")
        self.assertEqual(result.succeeded, ["NEW", "EXISTING"])
        self.assertEqual(result.failed, ["UNKNOWN", "INVALID", "UNREPORTED"])
        self.assertEqual(result.affected, 2)
        self.assertEqual(set(result.submission_ids), {"NEW", "EXISTING"})
        self.assertEqual(self.update.call_count, 1)
        self.assertEqual(self.update.call_args.kwargs["order_id"], "api-new")

    def test_nonduplicate_failure_still_raises(self):
        self.duplicates = []
        self.invalid = ["INVALID"]
        with self.assertRaisesRegex(RuntimeError, "No trackings were added"):
            self.submit("INVALID")

    def test_five_duplicates_from_reported_response_are_verified(self):
        numbers = ["543123676788", "543123690450", "200015408984219",
                   "1LSCXLNA064961245", "1LSCXLNA064955676"]
        self.duplicates = numbers
        self.rows = {tn.casefold(): [{"key": f"api-{index}", "order_id": f"O-{index}",
                                     "tracking_id": tn}] for index, tn in enumerate(numbers)}
        result = self.submit(*numbers)
        self.assertTrue(result.success)
        self.assertEqual(result.affected, 5)
        self.assertEqual(set(result.submission_ids), set(numbers))
        self.assertEqual(result.failed, [])
        self.assertIn("verified 5 already present", result.message)

    def test_legacy_success_without_per_tracking_outcomes_still_works(self):
        original = self.network.side_effect

        def response(**kwargs):
            if kwargs["url"].endswith("/add_trackings"):
                return 200, '{"status":"SUCCESS"}', {"status": "SUCCESS"}
            return original(**kwargs)

        self.network.side_effect = response
        result = self.submit("EXISTING")
        self.assertEqual(result.succeeded, ["EXISTING"])
        self.assertEqual(self.update.call_count, 1)

    def test_auth_failure_is_not_accepted_as_a_duplicate_response(self):
        raw = {"status": "FAIL", "message": "invalid_auth_token",
               "payload": {"duplicate_trackings": ["EXISTING"]}}
        self.network.side_effect = None
        self.network.return_value = (200, json.dumps(raw), raw)
        with self.assertRaises(api._SessionExpired):
            api.add_trackings(access_token="old", tracking_list=["EXISTING"], base_url="https://example.test")

    def test_session_expiry_reauthenticates_before_verifying_duplicate(self):
        original = self.network.side_effect
        expired = True

        def response(**kwargs):
            nonlocal expired
            if kwargs["url"].endswith("/get_orders") and expired:
                expired = False
                raise api._SessionExpired("expired")
            return original(**kwargs)

        self.network.side_effect = response
        with patch.object(api, "_reauth_tokens", return_value=api._AuthTokens("new", "refresh")) as reauth:
            self.assertTrue(self.submit("EXISTING").success)
        reauth.assert_called_once()

    def database(self, *trackings):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        db = Session(engine)
        self.addCleanup(db.close)
        group = BuyingGroup(name="BG", api_framework="buyinggroup", base_url="https://example.test",
                            api_username="user", api_password="password")
        store = Store(name="Store")
        db.add_all([group, store])
        db.flush()
        order = Order(store_id=store.id, buying_group_id=group.id, store_order_number="ORDER")
        db.add(order)
        db.flush()
        items = []
        for tracking in trackings:
            item = Item(order_id=order.id, status=ItemStatus.SHIPPED, quantity=1, price_sold=20)
            shipment = Shipment(tracking_number=tracking)
            db.add_all([item, shipment])
            db.flush()
            db.add(ShipmentItem(item_id=item.id, shipment_id=shipment.id))
            items.append(item)
        db.commit()
        return db, group, order, items

    def test_batch_persists_ids_on_all_siblings_and_leaves_unverified_shipped(self):
        self.duplicates += ["UNKNOWN"]
        db, group, _, items = self.database("EXISTING", "EXISTING", "UNKNOWN")
        result = batch_submit_pending_trackings(db, buying_group_id=group.id)
        self.assertIsNone(result.error)
        self.assertEqual(result.submitted_count, 2)
        db.expire_all()
        for item in items[:2]:
            self.assertEqual(item.status, ItemStatus.SUBMITTED)
            self.assertEqual(item.submission_id, "O-100")
            self.assertEqual(item.submission_api_id, "api-existing")
            self.assertIsNotNone(item.submitted_at)
        self.assertEqual(items[2].status, ItemStatus.SHIPPED)
        self.assertIsNone(items[2].submission_id)
        again = batch_submit_pending_trackings(db, buying_group_id=group.id)
        self.assertEqual(again.submitted_count, 0)
        self.assertIsNotNone(again.error)
        add_requests = [call for call in self.network.call_args_list
                        if call.kwargs["url"].endswith("/add_trackings")]
        sent = json.loads(dict(add_requests[-1].kwargs["fields"])["tracking_list"])
        self.assertEqual(sent, ["UNKNOWN"])

    def test_single_item_submission_refuses_unverified_duplicate(self):
        self.rows = {}
        db, group, order, items = self.database("EXISTING")
        with self.assertRaisesRegex(RuntimeError, "Left pending"):
            submit_item_tracking_to_group(group, items[0], order, "EXISTING")
        self.assertEqual(items[0].status, ItemStatus.SHIPPED)


if __name__ == "__main__":
    unittest.main()
