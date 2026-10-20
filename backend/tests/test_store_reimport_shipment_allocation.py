"""Regression tests for locked-item shipment allocation during store re-import."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Item, Order, Payment, PaymentLineItem, Shipment, ShipmentItem
from app.models.item import ItemStatus
from app.routers.store_imports import _apply_items_and_shipments, _build_order_diff
from app.routers.items import bulk_update_items
from app.schemas.item import ItemBulkUpdateRequest


class StoreReimportShipmentAllocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()
        self.order = Order(store_id=1, store_order_number="ORDER-1")
        self.session.add(self.order)
        self.session.flush()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _add_item(
        self,
        *,
        status: ItemStatus,
        quantity: int = 1,
        tracking: str | None = None,
    ) -> tuple[Item, Shipment | None]:
        item = Item(
            order_id=self.order.id,
            description="Product",
            status=status,
            quantity=quantity,
            price_paid=80,
            price_sold=100,
        )
        self.session.add(item)
        self.session.flush()
        shipment = None
        if tracking is not None:
            shipment = Shipment(
                tracking_number=tracking or None,
                notes="Existing shipment",
            )
            self.session.add(shipment)
            self.session.flush()
            self.session.add(
                ShipmentItem(shipment_id=shipment.id, item_id=item.id)
            )
        self.session.commit()
        return item, shipment

    def _payload(self, slices: list[tuple[str, str, int]]) -> dict:
        return {
            "items": [
                {
                    "name": "Product",
                    "pricing": {"unitPrice": 80},
                    "quantities": {"ordered": sum(qty for _, _, qty in slices)},
                    "shipments": [
                        {"shipmentId": shipment_id, "quantity": qty}
                        for shipment_id, _, qty in slices
                    ],
                }
            ],
            "shipments": [
                {
                    "shipmentId": shipment_id,
                    "trackingNumber": tracking,
                }
                for shipment_id, tracking, _ in slices
            ],
        }

    def _apply(self, slices: list[tuple[str, str, int]]) -> None:
        _apply_items_and_shipments(
            self.session,
            self._payload(slices),
            "amazon",
            self.order,
            external_order_id="ORDER-1",
            existing_order=True,
        )
        self.session.commit()
        self.session.expire_all()

    def _item_rows(self) -> list[tuple[int, ItemStatus, str | None]]:
        rows: list[tuple[int, ItemStatus, str | None]] = []
        for item in self.session.query(Item).order_by(Item.id):
            tracking = None
            if item.shipment_items:
                tracking = item.shipment_items[0].shipment.tracking_number
            rows.append((item.quantity, item.status, tracking))
        return rows

    def _assert_two_tracking_allocation(self, slices: list[tuple[str, str, int]]) -> None:
        locked, locked_shipment = self._add_item(
            status=ItemStatus.SHIPPED,
            tracking="TRACK-A",
        )
        mutable, _ = self._add_item(status=ItemStatus.PURCHASED)
        locked_id = locked.id
        mutable_id = mutable.id
        locked_shipment_id = locked_shipment.id

        self._apply(slices)

        locked = self.session.get(Item, locked_id)
        mutable = self.session.get(Item, mutable_id)
        self.assertEqual(locked.status, ItemStatus.SHIPPED)
        self.assertEqual(locked.quantity, 1)
        self.assertEqual(locked.shipment_items[0].shipment_id, locked_shipment_id)
        self.assertEqual(locked.shipment_items[0].shipment.tracking_number, "TRACK-A")
        self.assertEqual(mutable.status, ItemStatus.SHIPPED)
        self.assertEqual(mutable.shipment_items[0].shipment.tracking_number, "TRACK-B")
        self.assertEqual(self.session.query(Item).count(), 2)
        self.assertEqual(self.session.query(Shipment).count(), 2)

    def test_locked_exact_tracking_is_reserved_before_mutable_item(self) -> None:
        self._assert_two_tracking_allocation(
            [("shipment-a", "TRACK-A", 1), ("shipment-b", "TRACK-B", 1)]
        )

    def test_locked_reservation_is_independent_of_slice_order(self) -> None:
        self._assert_two_tracking_allocation(
            [("shipment-b", "TRACK-B", 1), ("shipment-a", "TRACK-A", 1)]
        )

    def test_reapply_does_not_duplicate_items_or_shipments(self) -> None:
        self._add_item(status=ItemStatus.SHIPPED, tracking="TRACK-A")
        self._add_item(status=ItemStatus.PURCHASED)
        slices = [("shipment-a", "TRACK-A", 1), ("shipment-b", "TRACK-B", 1)]

        self._apply(slices)
        first_rows = self._item_rows()
        self._apply(slices)

        self.assertEqual(self._item_rows(), first_rows)
        self.assertEqual(self.session.query(Item).count(), 2)
        self.assertEqual(self.session.query(Shipment).count(), 2)

    def test_scan_then_retitled_store_refresh_preserves_original_line(self) -> None:
        item, shipment = self._add_item(
            status=ItemStatus.SUBMITTED, quantity=2, tracking="ORDER-1"
        )
        original_id, shipment_id = item.id, shipment.id
        bulk_update_items(ItemBulkUpdateRequest(updates=[{
            "item_id": item.id, "status": "scanned", "receipt_id": "RECEIPT-1",
            "scanned_at": "2026-10-15T12:00:00Z",
        }]), db=self.session, _=None)
        self.assertEqual(self.session.query(Item).count(), 1)
        payload = self._payload([("shipment-a", "ORDER-1", 2)])
        payload["items"][0]["name"] = "Updated retailer title"
        for _ in range(2):
            diff = _build_order_diff(self.session, self.order, payload)
            self.assertEqual(diff["items"]["added"], [])
            _apply_items_and_shipments(
                self.session, payload, "walmart", self.order,
                external_order_id="ORDER-1", existing_order=True,
            )
            self.session.commit()
            self.session.expire_all()
        item = self.session.get(Item, original_id)
        self.assertEqual(self.session.query(Item).count(), 1)
        self.assertEqual((item.description, item.quantity, item.status),
                         ("Product", 2, ItemStatus.SCANNED))
        self.assertEqual((item.price_sold, item.receipt_id), (100, "RECEIPT-1"))
        self.assertIsNotNone(item.scanned_at)
        self.assertEqual(item.shipment_items[0].shipment_id, shipment_id)
        self.assertEqual(payload["items"][0]["name"], "Updated retailer title")

    def test_retitled_refresh_matches_walmart_original_tracking(self) -> None:
        _, shipment = self._add_item(status=ItemStatus.SCANNED, tracking="ORDER-1")
        shipment.notes = "Walmart tracking: 55512345678901234567"
        self.session.commit()
        payload = self._payload([("shipment-a", "55512345678901234567", 1)])
        payload["items"][0]["name"] = "Updated retailer title"
        _apply_items_and_shipments(
            self.session, payload, "walmart", self.order,
            external_order_id="ORDER-1", existing_order=True,
        )
        self.session.commit()
        self.assertEqual(self.session.query(Item).count(), 1)

    def test_shared_tracking_does_not_merge_distinct_products(self) -> None:
        self._add_item(status=ItemStatus.SCANNED, tracking="TRACK-A")
        other, _ = self._add_item(status=ItemStatus.SCANNED, tracking="TRACK-A")
        other.description = "Another product"
        self.session.commit()
        payload = self._payload([("shipment-a", "TRACK-A", 1)])
        payload["items"][0]["name"] = "New product"
        _apply_items_and_shipments(
            self.session, payload, "amazon", self.order, existing_order=True,
        )
        self.session.commit()
        self.assertEqual(self.session.query(Item).count(), 3)

    def test_locked_quantity_can_cover_multiple_slices_on_same_tracking(self) -> None:
        locked, shipment = self._add_item(
            status=ItemStatus.SUBMITTED,
            quantity=2,
            tracking="TRACK-A",
        )
        locked_id = locked.id
        shipment_id = shipment.id

        self._apply(
            [("shipment-a-1", "TRACK-A", 1), ("shipment-a-2", "TRACK-A", 1)]
        )

        locked = self.session.get(Item, locked_id)
        self.assertEqual(locked.status, ItemStatus.SUBMITTED)
        self.assertEqual(locked.quantity, 2)
        self.assertEqual(locked.shipment_items[0].shipment_id, shipment_id)
        self.assertEqual(self.session.query(Item).count(), 1)
        self.assertEqual(self.session.query(Shipment).count(), 1)

    def test_mutable_quantity_is_split_across_new_trackings(self) -> None:
        self._add_item(status=ItemStatus.PURCHASED, quantity=2)

        self._apply(
            [("shipment-a", "TRACK-A", 1), ("shipment-b", "TRACK-B", 1)]
        )

        self.assertCountEqual(
            self._item_rows(),
            [
                (1, ItemStatus.SHIPPED, "TRACK-A"),
                (1, ItemStatus.SHIPPED, "TRACK-B"),
            ],
        )

    def test_reimport_preserves_payment_linked_item_and_allocation(self) -> None:
        item, _ = self._add_item(status=ItemStatus.PURCHASED, quantity=2)
        payment = Payment(
            buying_group_id=1,
            payment_requested_at=datetime.now(timezone.utc),
        )
        self.session.add(payment)
        self.session.flush()
        self.session.add(
            PaymentLineItem(payment_id=payment.id, item_id=item.id, amount=200)
        )
        self.session.commit()

        self._apply(
            [("shipment-a", "TRACK-A", 1), ("shipment-b", "TRACK-B", 1)]
        )

        allocations = self.session.query(PaymentLineItem).order_by(
            PaymentLineItem.item_id
        ).all()
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].item_id, item.id)
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.status, ItemStatus.PURCHASED)
        self.assertEqual(item.shipment_items, [])
        self.assertEqual([row.amount for row in allocations], [200])
        self.assertEqual(sum(row.amount for row in allocations), 200)
        self.assertEqual({row.payment_id for row in allocations}, {payment.id})

    def test_tracking_replacement_without_mutable_item_preserves_locked_row(self) -> None:
        locked, shipment = self._add_item(
            status=ItemStatus.SHIPPED,
            tracking="TRACK-A",
        )
        locked_id = locked.id
        shipment_id = shipment.id

        self._apply([("shipment-b", "TRACK-B", 1)])

        locked = self.session.get(Item, locked_id)
        self.assertEqual(locked.status, ItemStatus.SHIPPED)
        self.assertEqual(locked.quantity, 1)
        self.assertEqual(locked.shipment_items[0].shipment_id, shipment_id)
        self.assertEqual(locked.shipment_items[0].shipment.tracking_number, "TRACK-A")
        self.assertEqual(self.session.query(Item).count(), 1)
        self.assertEqual(self.session.query(Shipment).count(), 1)

    def test_submitted_scanned_and_return_rows_remain_unchanged(self) -> None:
        locked_rows: list[tuple[int, ItemStatus, int]] = []
        for index, status in enumerate(
            [ItemStatus.SUBMITTED, ItemStatus.SCANNED, ItemStatus.NEEDS_RETURN],
            start=1,
        ):
            item, shipment = self._add_item(
                status=status,
                tracking=f"TRACK-{index}",
            )
            locked_rows.append((item.id, status, shipment.id))
        mutable, _ = self._add_item(status=ItemStatus.PURCHASED)
        mutable_id = mutable.id

        self._apply(
            [
                ("shipment-1", "TRACK-1", 1),
                ("shipment-2", "TRACK-2", 1),
                ("shipment-3", "TRACK-3", 1),
                ("shipment-4", "TRACK-4", 1),
            ]
        )

        for item_id, status, shipment_id in locked_rows:
            item = self.session.get(Item, item_id)
            self.assertEqual(item.status, status)
            self.assertEqual(item.quantity, 1)
            self.assertEqual(item.shipment_items[0].shipment_id, shipment_id)
        mutable = self.session.get(Item, mutable_id)
        self.assertEqual(mutable.status, ItemStatus.SHIPPED)
        self.assertEqual(mutable.shipment_items[0].shipment.tracking_number, "TRACK-4")

    def test_placeholder_shipment_is_upgraded_in_place(self) -> None:
        item, placeholder = self._add_item(
            status=ItemStatus.PURCHASED,
            tracking="",
        )
        item_id = item.id
        placeholder_id = placeholder.id

        self._apply([("shipment-a", "TRACK-A", 1)])

        item = self.session.get(Item, item_id)
        self.assertEqual(item.status, ItemStatus.SHIPPED)
        self.assertEqual(item.shipment_items[0].shipment_id, placeholder_id)
        self.assertEqual(item.shipment_items[0].shipment.tracking_number, "TRACK-A")
        self.assertEqual(self.session.query(Shipment).count(), 1)

    def test_walmart_original_tracking_alias_reserves_locked_quantity(self) -> None:
        locked, shipment = self._add_item(
            status=ItemStatus.SHIPPED,
            tracking="ORDER-1",
        )
        shipment.notes = "Existing shipment\nWalmart tracking: 55512345678901234567"
        self.session.commit()
        locked_id = locked.id
        shipment_id = shipment.id
        payload = self._payload(
            [("shipment-a", "55512345678901234567", 1)]
        )
        payload["shipments"][0]["status"] = {"rawStatusType": "SHIPPED"}

        _apply_items_and_shipments(
            self.session,
            payload,
            "walmart",
            self.order,
            external_order_id="ORDER-1",
            existing_order=True,
        )
        self.session.commit()
        self.session.expire_all()

        locked = self.session.get(Item, locked_id)
        self.assertEqual(locked.status, ItemStatus.SHIPPED)
        self.assertEqual(locked.quantity, 1)
        self.assertEqual(locked.shipment_items[0].shipment_id, shipment_id)
        self.assertEqual(locked.shipment_items[0].shipment.tracking_number, "ORDER-1")
        self.assertEqual(self.session.query(Item).count(), 1)
        self.assertEqual(self.session.query(Shipment).count(), 1)

    def test_walmart_store_delivery_waits_for_shipped_then_upgrades(self) -> None:
        self.session.commit()
        order_id = self.order.id
        for tracking in (None, "55512345678901234567", "WM123456"):
            for progress in (None, "PLACED", "PREPARING", "SHIPPED", "ON_THE_WAY", "DELIVERED"):
                with self.subTest(tracking=tracking, progress=progress):
                    payload = self._payload([("delivery", tracking, 1)])
                    payload.update(store="walmart", externalOrder={"id": "ORDER-1"})
                    shipment = payload["shipments"][0]
                    shipment["fulfillmentType"] = "SC_DELIVERY"
                    shipment["status"] = {"rawStatusType": progress}
                    _apply_items_and_shipments(
                        self.session, payload, "walmart", self.order,
                        external_order_id="ORDER-1",
                    )
                    self.session.flush()
                    shipped = progress in {"SHIPPED", "ON_THE_WAY", "DELIVERED"}
                    item = self.session.query(Item).one()
                    saved = self.session.query(Shipment).one()
                    self.assertEqual(saved.tracking_number, "ORDER-1" if shipped else None)
                    self.assertEqual(item.status, ItemStatus.SHIPPED if shipped else ItemStatus.PURCHASED)
                    if not shipped:
                        self.assertNotIn("Walmart tracking:", saved.notes or "")
                        diff = _build_order_diff(self.session, self.order, payload)
                        self.assertFalse(any(row.get("tracking_number") for row in diff["shipments"]["added"]))
                    shipment["status"] = {"rawStatusType": "SHIPPED"}
                    _apply_items_and_shipments(
                        self.session, payload, "walmart", self.order,
                        external_order_id="ORDER-1", existing_order=True,
                    )
                    self.session.flush()
                    self.assertEqual(self.session.query(Item).count(), 1)
                    self.assertEqual(self.session.query(Shipment).count(), 1)
                    self.assertEqual(saved.tracking_number, "ORDER-1")
                    self.assertEqual(item.status, ItemStatus.SHIPPED)
                    self.session.rollback()
                    self.session.expunge_all()
                    self.order = self.session.get(Order, order_id)


if __name__ == "__main__":
    unittest.main()
