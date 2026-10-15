"""Receiving checks should ignore warehouse history older than local orders."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Order
from app.utils.buying_group_apis import check_receiving as receiving
from app.utils.buying_group_apis.dealandrunner import DealandrunnerReceivedRow


class ReceivingDateCutoffTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def add_order(self, purchase_date, **kwargs):
        self.db.add(Order(store_id=1, purchase_date=purchase_date, **kwargs))
        self.db.flush()

    def test_cutoff_uses_earliest_purchase_day_across_orders(self):
        self.add_order(datetime(2026, 10, 12, 16, tzinfo=timezone.utc), buying_group_id=1)
        self.add_order(None)
        self.add_order(datetime(2026, 10, 10, 23), buying_group_id=2)
        dates = [
            "2026-10-09T23:59:59Z",
            "2026-10-10T00:00:00Z",
            "10/11/2026",
            None,
            "unknown",
            "2026-10-10T00:30:00+02:00",  # Still the previous day in UTC.
        ]
        for field in ("scanned_at", "timestamp", "created_date", "created_dt"):
            with self.subTest(field=field):
                rows = [SimpleNamespace(**{field: value}) for value in dates]
                actual = receiving._receipts_since_first_order(self.db, rows, date_field=field)
                self.assertEqual(actual, rows[1:5])

    def test_no_dated_orders_keeps_receipts(self):
        rows = [SimpleNamespace(timestamp="2020-01-01")]
        for add_undated_order in (False, True):
            if add_undated_order:
                self.add_order(None)
            self.assertEqual(
                receiving._receipts_since_first_order(self.db, rows, date_field="timestamp"),
                rows,
            )

    def test_old_dnr_receipt_does_not_create_unknown_receipt_review(self):
        self.add_order(datetime(2026, 10, 10))
        old = DealandrunnerReceivedRow(
            tracking_numbers=["OLD-TRACKING"],
            amount=Decimal("10"),
            product_name="Old product",
            system_transaction_id="old-receipt",
            timestamp="2026-10-09T23:59:59Z",
            entry_type_id=1,
        )
        group = SimpleNamespace(id=1, api_username="test", api_password="test", base_url=None)
        fetched = SimpleNamespace(rows=[old], total_records=1, request_urls=[])
        with (
            patch.object(receiving, "dealandrunner_group_ready", return_value=(True, None)),
            patch.object(receiving, "_collect_pending", return_value=([], [], [])),
            patch.object(receiving, "_collect_scanned_or_later", return_value=[]),
            patch.object(receiving.dealandrunner_api, "list_runner_transactions", return_value=fetched),
        ):
            result = receiving._check_receiving_dealandrunner(self.db, group=group, group_name="Test")
        self.assertIsNone(result.error)
        self.assertEqual(result.matches, [])
        self.assertEqual(result.session_payload["unknown_receipts"], [])
        self.assertEqual(result.session_payload["csv_row_count"], 0)
