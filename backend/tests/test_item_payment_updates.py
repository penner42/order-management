"""Item operations must preserve payments created on the Payments page."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Item, Order, Payment, PaymentLineItem
from app.models.item import ItemStatus
from app.routers.items import bulk_delete_items, bulk_update_items, delete_item, split_item, update_item
from app.routers.orders import delete_order
from app.schemas.item import (
    ItemBulkDeleteRequest, ItemBulkUpdateEntry, ItemBulkUpdateRequest,
    ItemCreate, ItemSplitRequest, ItemUpdate,
)


class ItemPaymentUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()
        self.session.add(Order(id=1, store_id=1))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _paid_item(self) -> Item:
        item = Item(order_id=1, description="Original", status=ItemStatus.SCANNED, quantity=2, price_sold=100)
        self.session.add(item)
        self.session.flush()
        for amount in (125, 75):
            payment = Payment(buying_group_id=1, payment_requested_at=datetime.now(timezone.utc))
            self.session.add(payment)
            self.session.flush()
            self.session.add(PaymentLineItem(payment_id=payment.id, item_id=item.id, amount=amount))
        self.session.commit()
        return item

    def assert_payments_preserved(self, item_id: int) -> None:
        self.session.expire_all()
        allocations = self.session.query(PaymentLineItem).order_by(PaymentLineItem.id).all()
        self.assertEqual([(row.item_id, row.amount) for row in allocations], [(item_id, 125), (item_id, 75)])
        self.assertEqual(self.session.query(Payment).count(), 2)

    def test_patch_edits_and_earlier_status_preserve_payments(self) -> None:
        item = self._paid_item()
        for data in (ItemUpdate(description="Corrected"), ItemUpdate(status=ItemStatus.PURCHASED)):
            update_item(item.id, data, db=self.session, _=None)
            self.assert_payments_preserved(item.id)

    def test_bulk_edits_and_earlier_status_preserve_payments(self) -> None:
        item = self._paid_item()
        for changes in ({"description": "Corrected"}, {"status": ItemStatus.SCANNED}):
            bulk_update_items(
                ItemBulkUpdateRequest(updates=[ItemBulkUpdateEntry(item_id=item.id, **changes)]),
                db=self.session, _=None,
            )
            self.assert_payments_preserved(item.id)

    def test_payment_statuses_cannot_be_assigned_to_items(self) -> None:
        for status in ("payment_requested", "payment_sent", "payment_received"):
            for schema, fields in (
                (ItemCreate, {"order_id": 1, "status": status}),
                (ItemUpdate, {"status": status}),
                (ItemBulkUpdateEntry, {"item_id": 1, "status": status}),
                (ItemSplitRequest, {"keep_quantity": 1, "split_off_status": status}),
            ):
                with self.subTest(schema=schema.__name__, status=status), self.assertRaises(ValidationError):
                    schema(**fields)

    def test_linked_item_cannot_be_split_or_deleted(self) -> None:
        item = self._paid_item()
        for action in (
            lambda: split_item(item.id, ItemSplitRequest(keep_quantity=1), db=self.session, _=None),
            lambda: delete_item(item.id, db=self.session, _=None),
            lambda: delete_order(1, db=self.session, _=None),
        ):
            with self.assertRaises(HTTPException) as caught:
                action()
            self.assertEqual(caught.exception.status_code, 409)
            self.assert_payments_preserved(item.id)
            self.assertEqual(self.session.get(Item, item.id).quantity, 2)
            self.assertIsNotNone(self.session.get(Order, 1))

    def test_bulk_delete_rejects_entire_selection_when_one_line_has_payments(self) -> None:
        paid = self._paid_item()
        unpaid = Item(order_id=1, quantity=1)
        self.session.add(unpaid)
        self.session.commit()
        with self.assertRaises(HTTPException) as caught:
            bulk_delete_items(ItemBulkDeleteRequest(item_ids=[unpaid.id, paid.id]), db=self.session, _=None)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIsNotNone(self.session.get(Item, unpaid.id))
        self.assert_payments_preserved(paid.id)

    def test_unlinked_items_can_be_split_and_deleted(self) -> None:
        item = Item(order_id=1, quantity=2)
        self.session.add(item)
        self.session.commit()
        result = split_item(item.id, ItemSplitRequest(keep_quantity=1), db=self.session, _=None)
        self.assertEqual(result.kept.quantity, 1)
        self.assertEqual(result.split_off.quantity, 1)
        delete_item(result.split_off.id, db=self.session, _=None)
        bulk_delete_items(ItemBulkDeleteRequest(item_ids=[item.id]), db=self.session, _=None)
        delete_order(1, db=self.session, _=None)
        self.assertEqual(self.session.query(Item).count(), 0)
        self.assertEqual(self.session.query(Order).count(), 0)


if __name__ == "__main__":
    unittest.main()
