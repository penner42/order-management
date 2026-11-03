"""Costco repeats returned/delivered snapshots of the same purchase line."""
import copy
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Item, Order, Shipment, ShipmentItem, User
from app.models.item import ItemStatus
from app.routers.store_imports import apply_store_order_payload, compute_order_diff
from app.schemas.store_import import StoreOrderImportPayload
from app.utils.costco_import import deduplicate_costco_items


def duplicate_payload():
    line = {
        "logicalItemId": "L1", "externalSku": "1874532", "name": "Apple Watch",
        "quantities": {"ordered": 2},
        "pricing": {"unitPrice": 599.97, "lineTotal": 1199.94},
        "shipments": [{"shipmentId": "S1", "quantity": 2}],
    }
    return {
        "store": "costco", "externalOrder": {"id": "ORDER-1"},
        "items": [{**copy.deepcopy(line), "status": {"rawStatusCode": status}}
                  for status in ("Returned", "Delivered")],
        "shipments": [{"shipmentId": "S1", "trackingNumber": "TRACK1"}],
    }


class CostcoDuplicateLineTests(unittest.TestCase):
    def test_pending_new_order_payouts_follow_retained_lines(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with sessionmaker(bind=engine)() as db:
            payload = duplicate_payload()
            other = copy.deepcopy(payload["items"][1])
            other.update(logicalItemId="L2", externalSku="OTHER", name="Other product")
            payload["items"].append(other)
            order_id = apply_store_order_payload(
                db, StoreOrderImportPayload.model_validate(payload),
                User(id=1, username="test"), item_payouts=[600, 610, 700],
            )
            rows = db.query(Item).filter_by(order_id=order_id).order_by(Item.id).all()
            self.assertEqual(len(rows), 2)
            self.assertEqual([(i.description, float(i.price_sold)) for i in rows],
                             [("Apple Watch", 610), ("Other product", 700)])
        engine.dispose()

    def test_purchase_snapshot_wins_in_either_order_without_mutating_input(self):
        for reverse in (False, True):
            payload = duplicate_payload()
            if reverse:
                payload["items"].reverse()
            original = copy.deepcopy(payload)
            result = deduplicate_costco_items(payload)
            self.assertEqual(len(result["items"]), 1)
            self.assertEqual(result["items"][0]["status"]["rawStatusCode"], "Delivered")
            self.assertEqual(payload, original)

    def test_separate_lines_allocations_and_missing_identities_are_preserved(self):
        for change in ("identity", "missing_identity", "tracking", "quantity", "price", "store"):
            with self.subTest(change=change):
                payload = duplicate_payload()
                other = payload["items"][1]
                if change == "identity":
                    other["logicalItemId"] = "L2"
                elif change == "missing_identity":
                    for item in payload["items"]:
                        item.pop("logicalItemId")
                elif change == "tracking":
                    other["shipments"][0]["shipmentId"] = "S2"
                    payload["shipments"].append({"shipmentId": "S2", "trackingNumber": "TRACK2"})
                elif change == "quantity":
                    other["quantities"]["ordered"] = 1
                elif change == "price":
                    other["pricing"]["unitPrice"] = 600
                else:
                    payload["store"] = "amazon"
                self.assertEqual(len(deduplicate_costco_items(payload)["items"]), 2)

    def test_cached_review_diff_and_repeated_apply_preserve_split_return_and_scan(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with sessionmaker(bind=engine)() as db:
            order = Order(store_id=1, store_order_number="ORDER-1")
            shipment = Shipment(tracking_number="TRACK1")
            db.add_all([order, shipment])
            db.flush()
            for status in (ItemStatus.RETURN_STARTED, ItemStatus.SCANNED):
                item = Item(order_id=order.id, description="Apple Watch", quantity=1,
                            price_paid=599.97, price_sold=599.97, status=status,
                            receipt_id="RECEIPT-1")
                db.add(item)
                db.flush()
                db.add(ShipmentItem(item_id=item.id, shipment_id=shipment.id))
            db.commit()
            original_ids = {item.id for item in db.query(Item)}
            payload = StoreOrderImportPayload.model_validate(duplicate_payload())
            for _ in range(3):
                diff = compute_order_diff(payload, db=db, _=None).diff
                matched = diff["items"]["matched"][0]
                self.assertEqual(matched["incoming"]["quantity"], 2)
                self.assertNotIn("quantity", matched["changes"])
                self.assertEqual(diff["items"]["added"], [])
                apply_store_order_payload(db, payload, User(id=1, username="test"))
                db.expire_all()
            rows = db.query(Item).order_by(Item.id).all()
            self.assertEqual({item.id for item in rows}, original_ids)
            self.assertEqual([(i.quantity, i.status) for i in rows],
                             [(1, ItemStatus.RETURN_STARTED), (1, ItemStatus.SCANNED)])
            self.assertTrue(all(i.receipt_id == "RECEIPT-1" for i in rows))
            self.assertTrue(all(i.shipment_items[0].shipment_id == shipment.id for i in rows))
            self.assertEqual(db.query(Shipment).count(), 1)
        engine.dispose()
