"""Regression tests for cancellation scope during store re-import."""
from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Item, Order, Shipment, ShipmentItem
from app.models.item import ItemStatus
from app.routers.store_imports import _apply_items_and_shipments, _build_order_diff


class StoreImportCancellationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()
        self.order = Order(store_id=1, store_order_number="ORDER-1")
        self.session.add(self.order)
        self.session.flush()
        self.canceled_item = self._add_item("Canceled product", ItemStatus.PURCHASED)
        self.shipped_item = self._add_item("Shipped product", ItemStatus.SHIPPED)
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _add_item(self, description: str, status: ItemStatus) -> Item:
        item = Item(
            order_id=self.order.id,
            description=description,
            status=status,
            quantity=1,
            price_paid=10,
        )
        self.session.add(item)
        self.session.flush()
        return item

    def _payload(
        self,
        *,
        order_status: str | None = None,
        canceled_item: bool = False,
        canceled_shipment: bool = False,
    ) -> dict:
        payload = {
            "externalOrder": {"id": "ORDER-1"},
            "items": [
                {
                    "name": "Canceled product",
                    "status": {
                        "normalizedStatus": (
                            "canceled" if canceled_item else "purchased"
                        )
                    },
                    "pricing": {"unitPrice": 10},
                    "quantities": {"ordered": 1},
                },
                {
                    "name": "Shipped product",
                    "status": {"normalizedStatus": "shipped"},
                    "pricing": {"unitPrice": 10},
                    "quantities": {"ordered": 1},
                },
            ],
            "shipments": [
                {
                    "shipmentId": "shipment-1",
                    "status": {
                        "normalizedStatus": (
                            "canceled" if canceled_shipment else "shipped"
                        )
                    },
                }
            ],
        }
        if order_status is not None:
            payload["status"] = order_status
        return payload

    def _apply(self, payload: dict) -> None:
        _apply_items_and_shipments(
            self.session,
            payload,
            payload.get("store", "amazon"),
            self.order,
            external_order_id="ORDER-1",
            existing_order=True,
        )
        self.session.commit()
        self.session.expire_all()

    def test_item_cancellation_does_not_cancel_shipped_sibling(self) -> None:
        self._apply(self._payload(canceled_item=True))

        canceled = self.session.get(Item, self.canceled_item.id)
        shipped = self.session.get(Item, self.shipped_item.id)
        self.assertEqual(canceled.status, ItemStatus.CANCELED)
        self.assertIsNotNone(canceled.canceled_at)
        self.assertEqual(shipped.status, ItemStatus.SHIPPED)
        self.assertIsNone(shipped.canceled_at)

    def test_canceled_shipment_does_not_cancel_order_items(self) -> None:
        self._apply(self._payload(canceled_shipment=True))

        canceled = self.session.get(Item, self.canceled_item.id)
        shipped = self.session.get(Item, self.shipped_item.id)
        self.assertEqual(canceled.status, ItemStatus.PURCHASED)
        self.assertEqual(shipped.status, ItemStatus.SHIPPED)

    def test_partially_canceled_order_only_uses_item_statuses(self) -> None:
        self._apply(
            self._payload(order_status="partially canceled", canceled_item=True)
        )

        canceled = self.session.get(Item, self.canceled_item.id)
        shipped = self.session.get(Item, self.shipped_item.id)
        self.assertEqual(canceled.status, ItemStatus.CANCELED)
        self.assertEqual(shipped.status, ItemStatus.SHIPPED)

    def test_explicit_order_cancellation_cancels_every_item(self) -> None:
        self._apply(self._payload(order_status="canceled"))

        canceled = self.session.get(Item, self.canceled_item.id)
        shipped = self.session.get(Item, self.shipped_item.id)
        self.assertEqual(canceled.status, ItemStatus.CANCELED)
        self.assertEqual(shipped.status, ItemStatus.CANCELED)
        self.assertIsNotNone(canceled.canceled_at)
        self.assertIsNotNone(shipped.canceled_at)

    def _legacy_walmart_payload(self) -> dict:
        payload = self._payload(canceled_item=True, canceled_shipment=True)
        payload["store"] = "walmart"
        payload["externalOrder"]["statusType"] = "CANCELED"
        payload["shipments"].append({
            "shipmentId": "shipment-2",
            "status": {"normalizedStatus": "SHIPPED"},
        })
        return payload

    def test_legacy_walmart_group_cancellation_does_not_cancel_sibling(self) -> None:
        payload = self._legacy_walmart_payload()
        diff = _build_order_diff(self.session, self.order, payload)
        shipped_diff = next(i for i in diff["items"]["matched"] if i["name"] == "Shipped product")
        self.assertNotIn("status", shipped_diff["changes"])

        self._apply(payload)
        self.assertEqual(self.canceled_item.status, ItemStatus.CANCELED)
        self.assertEqual(self.shipped_item.status, ItemStatus.SHIPPED)

    def test_legacy_walmart_fully_canceled_order_still_cancels_all_items(self) -> None:
        payload = self._legacy_walmart_payload()
        payload["shipments"][1]["status"]["normalizedStatus"] = "CANCELED"
        self._apply(payload)
        self.assertEqual(self.canceled_item.status, ItemStatus.CANCELED)
        self.assertEqual(self.shipped_item.status, ItemStatus.CANCELED)

    def test_legacy_walmart_partial_sibling_does_not_cancel_order(self) -> None:
        payload = self._legacy_walmart_payload()
        payload["shipments"][1]["status"]["normalizedStatus"] = "PARTIALLY_CANCELED"
        self._apply(payload)
        self.assertEqual(self.canceled_item.status, ItemStatus.CANCELED)
        self.assertEqual(self.shipped_item.status, ItemStatus.SHIPPED)

    def test_walmart_reimport_restores_incorrectly_canceled_tracked_sibling(self) -> None:
        self._apply(self._payload(order_status="canceled"))
        shipment = Shipment(tracking_number="TRACK-1")
        self.session.add(shipment)
        self.session.flush()
        self.session.add(ShipmentItem(
            shipment_id=shipment.id, item_id=self.shipped_item.id,
        ))
        self.session.commit()

        self._apply(self._legacy_walmart_payload())
        self.assertEqual(self.canceled_item.status, ItemStatus.CANCELED)
        self.assertEqual(self.shipped_item.status, ItemStatus.SHIPPED)
        self.assertIsNone(self.shipped_item.canceled_at)


if __name__ == "__main__":
    unittest.main()
