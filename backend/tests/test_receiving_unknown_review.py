"""Unknown warehouse receipts survive normal receiving review operations."""
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import BuyingGroup, ReceivingReviewPending, Shipment
from app.routers.buying_groups import check_buying_group_receiving
from app.schemas.buying_group import ReceivingReviewSession
from app.utils.buying_group_apis import check_receiving as receiving
from app.utils.buying_group_apis.dealandrunner import DealandrunnerReceivedRow, DealandrunnerRunnerTransactionsResult


class UnknownReceivingReviewTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.group = BuyingGroup(name="Test group")
        self.db.add(self.group)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def receipt(self, **changes):
        receipt = {
            "receipt_id": "receipt-1", "tracking_number": "UNKNOWN",
            "tracking_numbers": ["UNKNOWN", "ALTERNATE"],
            "description": "Headphones", "quantity": 1,
            "received_ext_total": "20.00", **changes,
        }

        return {**receipt, "receipt_key": receiving.receipt_key(receipt)}

    def payload(self, *, receipts=None, matches=None):
        return {
            "buying_group_id": self.group.id, "buying_group_name": self.group.name,
            "unknown_receipts": receipts if receipts is not None else [self.receipt()],
            "matches": matches or [],
        }

    def test_unknown_only_review_is_saved_listed_and_opened(self):
        payload = self.payload()
        self.assertEqual(receiving.save_receiving_review_pending(self.db, payload), 0)
        self.assertEqual(receiving.list_receiving_review_pending_payloads(self.db), [payload])
        result = receiving.CheckReceivingResult(self.group.id, self.group.name, session_payload=payload)
        with (
            patch("app.routers.buying_groups.receiving_check_group_ready", return_value=(True, None)),
            patch("app.routers.buying_groups._execute_received_check", return_value=(result, 0)),
        ):
            response = check_buying_group_receiving(self.group.id, db=self.db, _=None)
        self.assertEqual(response.matched_count, 0)
        self.assertEqual(response.review_path, f"/receiving-review?highlight={self.group.id}")

    def test_applying_last_match_keeps_unknown_review(self):
        receiving.save_receiving_review_pending(
            self.db, self.payload(matches=[{"item_id": 1}, {"item_id": 1, "match_kind": "not_received"}])
        )
        self.assertEqual(receiving.prune_receiving_review_pending(
            self.db, buying_group_id=self.group.id, item_ids=[1]
        ), (0, False))
        pending = receiving.list_receiving_review_pending_payloads(self.db)[0]
        self.assertEqual(pending["matches"], [])
        self.assertEqual(pending["unknown_receipts"], [self.receipt()])

    def test_checks_merge_update_and_preserve_unknowns_without_duplicates(self):
        receiving.save_receiving_review_pending(self.db, self.payload())
        updated = self.receipt(received_ext_total="25.00")
        second = self.receipt(receipt_id="receipt-2", tracking_number="SECOND")
        receiving.save_receiving_review_pending(self.db, self.payload(receipts=[updated, second]))
        receiving.save_receiving_review_pending(self.db, self.payload(receipts=[]))
        pending = receiving.list_receiving_review_pending_payloads(self.db)[0]
        self.assertEqual(pending["unknown_receipts"], [updated, second])

    def test_later_known_tracking_alias_removes_unknown_on_next_check(self):
        receiving.save_receiving_review_pending(self.db, self.payload())
        self.db.add(Shipment(tracking_number="alternate"))
        self.db.commit()
        receiving.save_receiving_review_pending(self.db, self.payload())
        self.assertEqual(receiving.list_receiving_review_pending_payloads(self.db), [])
        self.assertIsNone(self.db.get(ReceivingReviewPending, self.group.id))

    def test_applying_last_match_without_unknowns_clears_snapshot(self):
        receiving.save_receiving_review_pending(self.db, self.payload(receipts=[], matches=[{"item_id": 1}]))
        self.assertEqual(receiving.prune_receiving_review_pending(
            self.db, buying_group_id=self.group.id, item_ids=[1]
        ), (0, True))

    def test_unknowns_remain_in_their_buying_group(self):
        other = BuyingGroup(name="Another group")
        self.db.add(other)
        self.db.commit()
        receiving.save_receiving_review_pending(self.db, self.payload())
        receiving.save_receiving_review_pending(self.db, {
            **self.payload(), "buying_group_id": other.id, "buying_group_name": other.name,
        })
        sessions = receiving.list_receiving_review_pending_payloads(self.db)
        self.assertEqual([s["buying_group_id"] for s in sessions], [other.id, self.group.id])

    def test_dnr_fetch_preserves_all_tracking_numbers_and_details(self):
        row = DealandrunnerReceivedRow(
            tracking_numbers=["UNKNOWN", "ALTERNATE"], amount=Decimal("20.00"),
            product_name="Headphones", system_transaction_id="receipt-1",
            timestamp="2026-10-15T10:00:00Z", reference_warehouse_item_id="warehouse-1",
            raw={"additionalField": "additional detail"},
        )
        group = SimpleNamespace(id=self.group.id, api_username="test", api_password="test", base_url=None)
        with (
            patch.object(receiving, "dealandrunner_group_ready", return_value=(True, None)),
            patch.object(receiving, "_collect_pending", return_value=([], [], [])),
            patch.object(receiving, "_collect_scanned_or_later", return_value=[]),
            patch.object(receiving.dealandrunner_api, "list_runner_transactions", return_value=DealandrunnerRunnerTransactionsResult(
                rows=[row], total_records=1, request_urls=[],
            )),
        ):
            result = receiving._check_receiving_dealandrunner(self.db, group=group, group_name="Test group")
        self.assertIsNone(result.error)
        receiving.save_receiving_review_pending(self.db, result.session_payload)
        session = ReceivingReviewSession.model_validate(receiving.list_receiving_review_pending_payloads(self.db)[0])
        receipt = session.unknown_receipts[0]
        self.assertEqual(receipt.tracking_numbers, row.tracking_numbers)
        self.assertEqual(receipt.received_ext_total, "20.00")
        self.assertEqual(receipt.source_data["reference_warehouse_item_id"], "warehouse-1")
        self.assertEqual(receipt.source_data["raw"]["additionalField"], "additional detail")
