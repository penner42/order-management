"""Costco software bundles settle without inventing received payments."""
import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Item, Order, Payment, PaymentLineItem, User
from app.models.item import ItemStatus
from app.routers.store_imports import _apply_items_and_shipments, apply_store_order_payload
from app.schemas.store_import import StoreOrderImportPayload
from app.routers.orders import _effective_item_status
from app.utils.costco_software import default_costco_payouts


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

    def test_reimport_preserves_existing_payouts_and_is_idempotent(self):
        items = self.apply(self.payload(), store="amazon", payouts=[1100, 50, 25])
        for item in items[1:]:
            item.status = ItemStatus.SCANNED
        self.apply(self.payload())
        self.apply(self.payload(tracked=True))
        self.assertEqual(self.db.query(Item).count(), 3)
        self.assertEqual([float(item.price_sold) for item in items], [1100, 50, 25])
        for item in items[1:]:
            self.assertEqual(item.status, ItemStatus.SCANNED)

    def test_reimport_preserves_canceled_returned_and_payment_linked_statuses(self):
        hardware, office, mcafee = self.apply(self.payload(), store="amazon", payouts=[1100, 50, 25])
        office.status = ItemStatus.CANCELED
        mcafee.status = ItemStatus.RETURN_STARTED
        self.apply(self.payload())
        self.assertEqual(office.status, ItemStatus.CANCELED)
        self.assertEqual(mcafee.status, ItemStatus.RETURN_STARTED)
        self.assertEqual(office.price_sold, 50)
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

    def test_future_import_discounts_hardware_after_software_credit(self):
        for tracked in (False, True):
            with self.subTest(tracked=tracked):
                self.db.query(Item).delete()
                self.db.flush()
                payload = self.payload(tracked=tracked)
                payload["orderDiscount"] = 100.04
                hardware, *software = self.apply(payload)
                self.assertEqual(hardware.price_sold, 950)
                self.assertTrue(all(item.price_sold == 0 for item in software))
                self.assertEqual(hardware.price_paid, 1000)

    def test_discount_uses_price_and_quantity_and_supports_line_totals(self):
        payload = {"orderDiscount": 100.03, "items": [
            {"name": "Laptop", "quantities": {"ordered": 2}, "pricing": {"unitPrice": 300}},
            {"name": "Monitor", "quantities": {"ordered": 1}, "pricing": {"lineTotal": 400}},
            {"name": "Microsoft 365", "quantities": {"ordered": 3}, "pricing": {"unitPrice": .01}},
        ]}
        self.assertEqual(default_costco_payouts(payload), [270, 360, 0])
        payload["orderDiscount"] = 0
        self.assertEqual(default_costco_payouts(payload), [300, 400, 0])
        payload["orderDiscount"] = 2000
        self.assertEqual(default_costco_payouts(payload), [0, 0, 0])

    def test_non_bundle_discounts_and_cent_rounding(self):
        payload = {"orderDiscount": .03, "items": [
            {"name": "Laptop", "pricing": {"unitPrice": 10}},
            {"name": "Monitor", "pricing": {"unitPrice": 20}},
        ]}
        self.assertEqual(default_costco_payouts(payload), [9.99, 19.98])
        payload["items"] = [{"name": "Microsoft 365", "pricing": {"unitPrice": 100}}]
        payload["orderDiscount"] = 10
        self.assertEqual(default_costco_payouts(payload), [90])

    def test_allstate_bundle_uses_actual_price_quantity_and_remaining_discount(self):
        for price in (34.99, 49.99):
            with self.subTest(price=price):
                self.db.query(Item).delete()
                self.db.flush()
                payload = {"orderDiscount": round(100 + 2 * price, 2), "items": [
                    {"name": "Samsung TV", "quantities": {"ordered": 2}, "pricing": {"unitPrice": 300}},
                    {"name": "Soundbar", "pricing": {"unitPrice": 400}},
                    {"name": "Allstate 3 Years (For TVs $100 to $500)",
                     "quantities": {"ordered": 2}, "pricing": {"unitPrice": price}},
                ]}
                tv, soundbar, plan = self.apply(payload)
                self.assertEqual([tv.price_sold, soundbar.price_sold, plan.price_sold], [270, 360, 0])
                self.assertAlmostEqual(float(plan.price_paid), price)
                self.assertEqual(plan.quantity, 2)
                self.assertEqual(_effective_item_status(plan), "no_payment_necessary")
                self.assertEqual(plan.payment_line_items, [])
                self.assertEqual(self.db.query(Payment).count(), 0)
                payload["items"][-1]["pricing"] = {"lineTotal": 2 * price}
                self.assertEqual(default_costco_payouts(payload), [270, 360, 0])
                payload["orderDiscount"] = 0
                self.assertEqual(default_costco_payouts(payload), [300, 400, 0])

    def test_sales_tax_is_saved_and_allocated_after_bundle_credit_and_discount(self):
        payload = {"store": "costco", "externalOrder": {"id": "tax-bundle"},
                   "orderDiscount": 169.99, "totals": {"tax": 90}, "items": [
            {"name": "TV", "quantities": {"ordered": 2}, "pricing": {"unitPrice": 300}},
            {"name": "Soundbar", "pricing": {"unitPrice": 400}},
            {"name": "Allstate Protection Plan", "quantities": {"ordered": 2}, "pricing": {"unitPrice": 34.99}},
            {"name": "Microsoft 365", "pricing": {"unitPrice": .01}},
        ]}
        order_id = apply_store_order_payload(
            self.db, StoreOrderImportPayload.model_validate(payload), User(id=1, username="test"))
        order = self.db.get(Order, order_id)
        self.assertEqual(float(order.sales_tax), 90)
        self.assertEqual(float(order.order_discount), 169.99)
        items = self.db.query(Item).filter_by(order_id=order_id).order_by(Item.id).all()
        self.assertEqual([float(item.price_sold) for item in items], [297, 396, 0, 0])
        self.assertEqual([float(item.sales_tax) for item in items], [27, 36, 0, 0])
        self.assertTrue(all(item.status == ItemStatus.NO_PAYMENT_NECESSARY for item in items[2:]))

    def test_sales_tax_without_discount_and_tax_free_orders(self):
        payload = {"items": [{"name": "TV", "pricing": {"unitPrice": 100}}]}
        for tax, expected in ((8.25, 108.25), (0, 100), (None, 100), ("NaN", 100)):
            with self.subTest(tax=tax):
                payload["totals"] = {"tax": tax}
                self.assertEqual(default_costco_payouts(payload), [expected])

    def test_tracked_import_saves_per_unit_tax_and_reimport_preserves_it(self):
        payload = self.payload(tracked=True)
        payload.update(orderDiscount=100.04, totals={"tax": 160})
        hardware, *included = self.apply(payload)
        self.assertEqual(hardware.sales_tax, 80)
        self.assertEqual(hardware.price_sold, 1030)
        self.assertTrue(all(item.sales_tax == 0 for item in included))
        payload["totals"]["tax"] = 200
        self.apply(payload)
        self.assertEqual(hardware.sales_tax, 80)
        self.assertEqual(hardware.price_sold, 1030)

    def test_allstate_supplied_payout_is_overridden_only_for_costco_bundles(self):
        plan = {"name": "ALLSTATE Protection Plan", "pricing": {"unitPrice": 34.99}}
        payload = {"orderDiscount": 134.99, "items": [
            {"name": "LG OLED TV", "pricing": {"unitPrice": 1000}}, plan,
        ]}
        tv, included = self.apply(payload, payouts=[900, 34.99])
        self.assertEqual(included.price_sold, 0)
        self.assertEqual(_effective_item_status(included), "no_payment_necessary")
        self.assertEqual(tv.price_sold, 900)
        self.db.query(Item).delete()
        self.db.flush()
        standalone, = self.apply({"items": [plan]})
        self.assertEqual(float(standalone.price_sold), 34.99)
        self.assertEqual(standalone.status, ItemStatus.PURCHASED)
        self.db.query(Item).delete()
        self.db.flush()
        _, other_store = self.apply(payload, store="amazon", payouts=[900, 34.99])
        self.assertEqual(float(other_store.price_sold), 34.99)
        self.assertEqual(other_store.status, ItemStatus.PURCHASED)
