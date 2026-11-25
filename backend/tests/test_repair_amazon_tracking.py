"""Exercise the repair command's database changes in an isolated database."""
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Item, Order, Shipment, ShipmentItem, Store
from app.models.item import ItemStatus
from app.repair_amazon_tracking import inspect_order


class RepairAmazonTrackingTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.session = sessionmaker(bind=engine)
        self.number = "111-1111111-1111111"
        self.old_tracking = "TBA12345678901"
        self.new_tracking = "TBA12345678900"
        self.capture = {"orderId": self.number, "shipments": [], "items": []}
        with self.session.begin() as db:
            store = Store(name="Amazon")
            old_shipment = Shipment(tracking_number=self.old_tracking)
            db.add_all([store, old_shipment])
            db.flush()
            order = Order(store_id=store.id, store_order_number=self.number)
            db.add(order)
            db.flush()
            self.order_id = order.id
            self.scope = dict(store_id=store.id, store_account_id=None)
            self.item_ids = []
            for index, tracking in enumerate((self.new_tracking, self.old_tracking)):
                item = Item(order_id=order.id, description=f"Product {index}", external_sku=f"sku{index}",
                            quantity=2, status=ItemStatus.SHIPPED)
                db.add(item)
                db.flush()
                db.add(ShipmentItem(item_id=item.id, shipment_id=old_shipment.id))
                self.item_ids.append(item.id)
                shipment_id = f"shared:package:{index}"
                self.capture["shipments"].append({"shipmentId": shipment_id, "trackingNumber": tracking})
                self.capture["items"].append({"shipmentId": shipment_id, "name": item.description,
                                              "quantity": item.quantity, "asin": item.external_sku})

    def check(self, *, apply=False):
        with self.session.begin() as db:
            return inspect_order(db, self.order_id, self.number, self.scope, self.capture, apply=apply)

    def tracking_by_item(self):
        with self.session() as db:
            return dict(db.query(ShipmentItem.item_id, Shipment.tracking_number).join(Shipment).all())

    def test_audit_then_repair_is_idempotent(self):
        original = self.tracking_by_item()
        result, moves = self.check()
        self.assertEqual(result, "repairable")
        self.assertEqual([(m.item_id, m.before, m.after) for m in moves],
                         [(self.item_ids[0], self.old_tracking, self.new_tracking)])
        self.assertEqual(self.tracking_by_item(), original)
        self.assertEqual(self.check(apply=True)[0], "repaired")
        self.assertEqual(self.check(apply=True)[0], "assignments already correct")
        self.assertEqual(self.tracking_by_item(), original | {self.item_ids[0]: self.new_tracking})
        with self.session() as db:
            self.assertEqual(db.query(Shipment).count(), 2)
            item = db.get(Item, self.item_ids[0])
            self.assertEqual((item.quantity, item.status), (2, ItemStatus.SHIPPED))

    def test_processed_line_is_left_for_manual_review(self):
        with self.session.begin() as db:
            db.get(Item, self.item_ids[0]).status = ItemStatus.SCANNED
        original = self.tracking_by_item()
        self.assertEqual(self.check(apply=True), ("manual review: an affected line has been processed", []))
        self.assertEqual(self.tracking_by_item(), original)
