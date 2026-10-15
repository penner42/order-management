"""Regression tests for item edits that interact with payment allocations."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Item, Payment, PaymentLineItem
from app.models.item import ItemStatus
from app.routers.items import bulk_update_items, split_item, update_item
from app.schemas.item import (
    ItemBulkUpdateEntry,
    ItemBulkUpdateRequest,
    ItemSplitRequest,
    ItemUpdate,
)


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

    def test_manual_split_redistributes_full_payment_allocation(self) -> None:
        item = Item(
            order_id=1,
            description="Two units",
            status=ItemStatus.SCANNED,
            quantity=2,
            price_sold=100,
        )
        payment = Payment(
            buying_group_id=1,
            payment_requested_at=datetime.now(timezone.utc),
        )
        self.session.add_all([item, payment])
        self.session.flush()
        self.session.add(
            PaymentLineItem(payment_id=payment.id, item_id=item.id, amount=200)
        )
        self.session.commit()

        result = split_item(
            item.id,
            ItemSplitRequest(keep_quantity=1),
            db=self.session,
            _=None,
        )

        allocations = {
            row.item_id: row.amount
            for row in self.session.query(PaymentLineItem).all()
        }
        self.assertEqual(allocations[result.kept.id], 100)
        self.assertEqual(allocations[result.split_off.id], 100)
        self.assertEqual(sum(allocations.values()), 200)

    def test_manual_split_rounding_preserves_each_payment_total(self) -> None:
        item = Item(
            order_id=1,
            description="Three units",
            status=ItemStatus.SCANNED,
            quantity=3,
            price_sold=100,
        )
        payments = [
            Payment(
                buying_group_id=1,
                payment_requested_at=datetime.now(timezone.utc),
            )
            for _ in range(2)
        ]
        self.session.add_all([item, *payments])
        self.session.flush()
        self.session.add_all(
            [
                PaymentLineItem(
                    payment_id=payments[0].id,
                    item_id=item.id,
                    amount=100,
                ),
                PaymentLineItem(
                    payment_id=payments[1].id,
                    item_id=item.id,
                    amount=50,
                ),
            ]
        )
        self.session.commit()

        result = split_item(
            item.id,
            ItemSplitRequest(keep_quantity=1),
            db=self.session,
            _=None,
        )

        by_payment: dict[int, dict[int, Decimal]] = {}
        for row in self.session.query(PaymentLineItem).all():
            by_payment.setdefault(row.payment_id, {})[row.item_id] = row.amount
        self.assertEqual(
            by_payment[payments[0].id][result.kept.id], Decimal("33.33")
        )
        self.assertEqual(
            by_payment[payments[0].id][result.split_off.id], Decimal("66.67")
        )
        self.assertEqual(
            by_payment[payments[1].id][result.kept.id], Decimal("16.67")
        )
        self.assertEqual(
            by_payment[payments[1].id][result.split_off.id], Decimal("33.33")
        )
        self.assertEqual(
            sum(by_payment[payments[0].id].values()), Decimal("100.00")
        )
        self.assertEqual(
            sum(by_payment[payments[1].id].values()), Decimal("50.00")
        )


if __name__ == "__main__":
    unittest.main()
