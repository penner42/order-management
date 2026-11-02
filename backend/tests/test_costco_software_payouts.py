"""Costco software bundles settle without inventing received payments."""
import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Item, Order, Payment, PaymentLineItem
from app.models.item import ItemStatus
from app.routers.store_imports import _apply_items_and_shipments
from app.routers.orders import _effective_item_status


class CostcoSoftwarePayoutTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.order = Order(store_id=1, store_order_number="bundle", order_discount=100)
        self.db.add(self.order)
        self.db.flush()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def payload(self, *, tracked=False):
        names = ["Microsoft Surface Pro PC Bundle", "MICROSOFT OFFICE 365", "McAfee Total Protection, 1 Device, (E-Delivery)"]
        return {
            "items": [dict(name=name, pricing={"unitPrice": 1000 if i == 0 else .01},
                           quantities={"ordered": 2},
                           shipments=[{"shipmentId": "S1", "quantity": 2}] if tracked else [])
                      for i, name in enumerate(names)],
            "shipments": [{"shipmentId": "S1", "trackingNumber": "TRACK1"}] if tracked else [],
        }

    def apply(self, payload, store="costco", payouts=None):
        _apply_items_and_shipments(self.db, payload, store, self.order, payouts)
        self.db.flush()
        return self.db.query(Item).order_by(Item.id).all()

    def test_bundle_overrides_supplied_payout_and_preserves_costs(self):
        for tracked in (False, True):
            with self.subTest(tracked=tracked):
                if tracked:
                    self.db.query(Item).delete()
                    self.db.flush()
                hardware, *software = self.apply(self.payload(tracked=tracked), payouts=[1100, 50, 25])
                self.assertEqual(hardware.price_sold, 1100)
                self.assertEqual(hardware.status, ItemStatus.SHIPPED if tracked else ItemStatus.PURCHASED)
                for item in software:
                    self.assertEqual(item.price_sold, 0)
                    self.assertAlmostEqual(float(item.price_paid), .01)
                    self.assertEqual(item.quantity, 2)
                    self.assertEqual(_effective_item_status(item), "no_payment_necessary")
                    self.assertEqual(item.payment_line_items, [])
                self.assertEqual(self.order.order_discount, 100)
                self.assertEqual(self.db.query(Payment).count(), 0)

    def test_standalone_software_and_other_stores_keep_normal_payouts(self):
        payload = self.payload()
        payload["items"] = payload["items"][1:]
        items = self.apply(payload, payouts=[50, 25])
        self.assertEqual([i.price_sold for i in items], [50, 25])
        self.assertTrue(all(i.status == ItemStatus.PURCHASED for i in items))
        self.db.query(Item).delete()
        self.db.flush()
        items = self.apply(self.payload(), store="amazon", payouts=[1100, 50, 25])
        self.assertEqual([i.price_sold for i in items], [1100, 50, 25])

    def test_reimport_corrects_received_software_and_is_idempotent(self):
        items = self.apply(self.payload(), store="amazon", payouts=[1100, 50, 25])
        for item in items[1:]:
            item.status = ItemStatus.SCANNED
        self.apply(self.payload())
        self.apply(self.payload(tracked=True))
        self.assertEqual(self.db.query(Item).count(), 3)
        for item in items[1:]:
            self.assertEqual(item.status, ItemStatus.NO_PAYMENT_NECESSARY)
            self.assertEqual(item.price_sold, 0)

    def test_reimport_preserves_canceled_returned_and_payment_linked_statuses(self):
        hardware, office, mcafee = self.apply(self.payload(), store="amazon", payouts=[1100, 50, 25])
        office.status = ItemStatus.CANCELED
        mcafee.status = ItemStatus.RETURN_STARTED
        self.apply(self.payload())
        self.assertEqual(office.status, ItemStatus.CANCELED)
        self.assertEqual(mcafee.status, ItemStatus.RETURN_STARTED)
        self.assertEqual(office.price_sold, 0)
        office.status = ItemStatus.SCANNED
        office.price_sold = 50
        payment = Payment(buying_group_id=1, payment_requested_at=datetime.now(timezone.utc))
        payment.line_items.append(PaymentLineItem(item=office, amount=100))
        self.db.add(payment)
        self.db.flush()
        self.apply(self.payload())
        self.assertEqual(office.status, ItemStatus.SCANNED)
        self.assertEqual(office.price_sold, 50)

    def test_canceled_import_preserves_cancellation(self):
        payload = self.payload()
        payload["status"] = "canceled"
        items = self.apply(payload, payouts=[1100, 50, 25])
        self.assertTrue(all(i.status == ItemStatus.CANCELED for i in items))
        self.assertTrue(all(i.price_sold == 0 for i in items[1:]))
