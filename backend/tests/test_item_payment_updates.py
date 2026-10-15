"""Regression tests for item edits that interact with payment allocations."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Item, Payment, PaymentLineItem
from app.models.item import ItemStatus
from app.routers.items import bulk_update_items, update_item
from app.schemas.item import ItemBulkUpdateEntry, ItemBulkUpdateRequest, ItemUpdate


class ItemPaymentUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _paid_scanned_item(self) -> Item:
        item = Item(
            order_id=1,
            description="Original description",
            status=ItemStatus.SCANNED,
            quantity=1,
            price_sold=100,
        )
        payment = Payment(
            buying_group_id=1,
            payment_requested_at=datetime.now(timezone.utc),
        )
        self.session.add_all([item, payment])
        self.session.flush()
        self.session.add(
            PaymentLineItem(payment_id=payment.id, item_id=item.id, amount=100)
        )
        self.session.commit()
        return item

    def assert_payment_preserved(self) -> None:
        self.assertEqual(self.session.query(PaymentLineItem).count(), 1)
        self.assertEqual(self.session.query(Payment).count(), 1)

    def test_patch_non_status_edit_preserves_payment(self) -> None:
        item = self._paid_scanned_item()

        update_item(
            item.id,
            ItemUpdate(description="Corrected description"),
            db=self.session,
            _=None,
        )

        self.assert_payment_preserved()

    def test_bulk_non_status_edit_preserves_payment(self) -> None:
        item = self._paid_scanned_item()

        bulk_update_items(
            ItemBulkUpdateRequest(
                updates=[
                    ItemBulkUpdateEntry(
                        item_id=item.id,
                        description="Corrected description",
                    )
                ]
            ),
            db=self.session,
            _=None,
        )

        self.assert_payment_preserved()

    def test_explicit_earlier_status_removes_payment(self) -> None:
        item = self._paid_scanned_item()

        update_item(
            item.id,
            ItemUpdate(status=ItemStatus.SCANNED),
            db=self.session,
            _=None,
        )

        self.assertEqual(self.session.query(PaymentLineItem).count(), 0)
        self.assertEqual(self.session.query(Payment).count(), 0)


if __name__ == "__main__":
    unittest.main()
