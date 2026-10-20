"""Duplicate signals are review hints; auditing never changes order data."""
from datetime import datetime, timezone
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

from app.database import Base
from app.models import Item, Order, OrderPaymentMethod, Shipment, ShipmentItem, Store
from app.models.item import ItemStatus
from app.routers.orders import list_orders_paged, review_duplicate_lines
from app.schemas.order import DuplicateAuditReview
from app.utils.duplicate_audit import audit_order


class DuplicateAuditTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.store = Store(name="Audit store")
        self.db.add(self.store)
        self.db.flush()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def order(self, number="ORDER", **changes):
        order = Order(store_id=self.store.id, store_order_number=number,
                      purchase_date=datetime.now(timezone.utc), **changes)
        self.db.add(order)
        self.db.flush()
        for name, status, payout in [
            ("HP 14 inch Windows Laptop Intel N150", ItemStatus.SCANNED, 199),
            ("HP 14-inch Windows 11 Laptop Intel N150 4GB RAM", ItemStatus.SUBMITTED, 0),
        ]:
            item = Item(order=order, description=name, quantity=2, price_paid=199,
                        price_sold=payout, status=status)
            self.db.add(item)
            self.db.flush()
            shipment = Shipment(tracking_number=number)
            self.db.add(shipment)
            self.db.flush()
            self.db.add(ShipmentItem(item_id=item.id, shipment_id=shipment.id))
        self.db.commit()
        self.db.expire_all()
        return self.db.get(Order, order.id)

    def page(self, **changes):
        return list_orders_paged(**{
            "db": self.db, "_": None, "order_status": None, "status": [],
            "buying_group_id": [], "store_id": [], "store_account_id": [],
            "date_from": None, "date_to": None, "date_from_utc": None,
            "date_to_utc": None, "search": None, "page": 1, "per_page": 25,
            "duplicate_audit": True, **changes,
        })

    def test_screenshot_pattern_is_likely_with_zero_payout_evidence(self):
        order = self.order()
        finding, = audit_order(order)
        self.assertEqual(finding["confidence"], "likely")
        self.assertEqual(set(finding["item_ids"]), {i.id for i in order.items})
        self.assertTrue(any("zero-payout" in reason for reason in finding["reasons"]))

    def test_shared_tracking_on_unrelated_products_is_only_possible(self):
        order = self.order()
        order.items[0].description = "Watch"
        order.items[1].description = "Pencil"
        self.assertEqual(audit_order(order)[0]["confidence"], "possible")

    def test_different_tracking_or_price_and_missing_cost_are_not_pair_matches(self):
        order = self.order()
        order.items[0].shipment_items[0].shipment.tracking_number = "OTHER"
        self.assertEqual(audit_order(order), [])
        order.items[0].shipment_items[0].shipment.tracking_number = "ORDER"
        order.items[0].price_paid = 200
        self.assertEqual(audit_order(order), [])
        order.items[0].price_paid = None
        self.assertEqual(audit_order(order), [])

    def test_canceled_and_refunded_lines_are_excluded(self):
        order = self.order()
        for status in (ItemStatus.CANCELED, ItemStatus.RETURN_REFUNDED):
            order.items[0].status = status
            self.assertEqual(audit_order(order), [])

    def test_payment_excess_signal_accounts_for_quantity_tax_and_discount(self):
        order = self.order()
        order.items[0].shipment_items[0].shipment.tracking_number = "OTHER"
        order.items[0].sales_tax = 1
        order.order_discount = 2
        self.db.add(OrderPaymentMethod(order=order, payment_method_id=1, amount=398))
        self.db.flush()
        findings = audit_order(order)
        self.assertEqual(len(findings), 1)
        self.assertIn("by 398.00", findings[0]["reasons"][0])
        order.order_payments[0].amount = None
        self.assertEqual(audit_order(order), [])

    def test_audit_returns_whole_orders_for_item_search_and_status_filter(self):
        order = self.order()
        result = self.page(search="4GB", status=["scanned"])
        self.assertEqual(result.total, 1)
        self.assertEqual(len(result.items[0].items), 2)
        self.assertIn(order.id, result.audit_findings)
        self.assertFalse(self.db.new or self.db.dirty or self.db.deleted)
        normal = self.page(search="4GB", duplicate_audit=False)
        self.assertEqual(len(normal.items[0].items), 1)
        self.assertEqual(normal.audit_findings, {})

    def test_detection_precedes_pagination_and_excludes_personal_orders(self):
        for index in range(26):
            self.order(str(index))
        self.order("PERSONAL", status="personal")
        first = self.page()
        second = self.page(page=2)
        self.assertEqual((first.total, first.pages, len(first.items)), (26, 2, 25))
        self.assertEqual(len(second.items), 1)
        self.assertEqual(len(second.audit_findings), 1)
        self.assertFalse({o.id for o in first.items} & {o.id for o in second.items})

    def test_filters_empty_results_and_exact_order_link(self):
        order = self.order()
        self.assertEqual(self.page(search="absent").total, 0)
        self.assertEqual(self.page(store_id=[self.store.id + 1]).total, 0)
        self.assertEqual(self.page(date_from="2099-01-01").total, 0)
        self.assertEqual(self.page(order_id=order.id).total, 1)
        self.assertEqual(self.page(order_id=order.id + 1).total, 0)

    def review(self, order, ids=None, not_duplicate=True):
        review_duplicate_lines(order.id, DuplicateAuditReview(
            item_ids=ids if ids is not None else [item.id for item in order.items],
            not_duplicate=not_duplicate,
        ), db=self.db, _=None)

    def test_review_persists_and_undo_restores_findings_without_changing_lines(self):
        order = self.order()
        before = [(i.id, i.quantity, i.status, i.price_paid, i.price_sold) for i in order.items]
        self.review(order)
        self.db.expire_all()
        self.assertEqual(self.page().total, 0)
        self.assertEqual([(i.id, i.quantity, i.status, i.price_paid, i.price_sold) for i in order.items], before)
        self.review(order)  # Repeated submissions are harmless.
        self.review(order, not_duplicate=False)
        self.assertEqual(self.page().total, 1)

    def test_individual_review_excludes_only_reviewed_line_from_findings(self):
        order = self.order()
        first, other = order.items
        self.review(order, [first.id])
        finding, = audit_order(order)
        self.assertEqual(finding["item_ids"], [other.id])
        self.review(order, [other.id])
        self.assertEqual(self.page().total, 0)

    def test_show_cleared_survives_reload_and_can_restore_individual_lines(self):
        order = self.order()
        first, other = order.items
        self.review(order)
        self.db.expire_all()
        self.assertEqual(self.page().total, 0)
        result = self.page(include_cleared=True)
        self.assertEqual(result.total, 1)
        cleared, = result.audit_findings[order.id]
        self.assertTrue(cleared.cleared)
        self.assertEqual(set(cleared.item_ids), {first.id, other.id})
        self.assertEqual(self.page(include_cleared=True, search="absent").total, 0)
        self.review(order, [first.id], not_duplicate=False)
        self.db.expire_all()
        result = self.page(include_cleared=True)
        active, cleared = result.audit_findings[order.id]
        self.assertFalse(active.cleared)
        self.assertEqual(active.item_ids, [first.id])
        self.assertEqual(cleared.item_ids, [other.id])
        self.assertEqual(self.page().total, 1)

    def test_show_cleared_includes_lines_even_if_signals_no_longer_match(self):
        order = self.order()
        self.review(order)
        order.items[0].status = ItemStatus.CANCELED
        order.items[1].price_paid = 999
        self.db.commit()
        result = self.page(include_cleared=True)
        self.assertEqual(result.total, 1)
        self.assertTrue(result.audit_findings[order.id][0].cleared)

    def test_review_does_not_mask_new_matching_lines_or_unrelated_orders(self):
        order = self.order()
        self.review(order)
        new = Item(order=order, description="HP 14 inch Windows Laptop Intel N150",
                   quantity=2, price_paid=199, price_sold=0, status=ItemStatus.SHIPPED)
        self.db.add(new)
        self.db.flush()
        self.db.add(ShipmentItem(item_id=new.id, shipment_id=order.items[0].shipment_items[0].shipment_id))
        self.db.commit()
        self.db.expire_all()
        self.assertTrue(audit_order(order))
        self.assertTrue(all(finding["item_ids"] == [new.id] for finding in audit_order(order)))
        self.order("UNRELATED")
        self.assertEqual(self.page().total, 2)

    def test_review_rejects_foreign_or_missing_lines_atomically(self):
        order = self.order()
        other = self.order("OTHER")
        for invalid in (other.items[0].id, 999999):
            with self.assertRaises(HTTPException) as error:
                self.review(order, [order.items[0].id, invalid])
            self.assertEqual(error.exception.status_code, 404)
            self.assertFalse(order.items[0].duplicate_audit_ignored)

    def test_all_reviewed_lines_are_excluded_from_payment_excess_finding(self):
        order = self.order()
        self.db.add(OrderPaymentMethod(order=order, payment_method_id=1, amount=398))
        self.db.commit()
        self.assertEqual(len(audit_order(order)), 2)
        self.review(order)
        self.assertEqual(self.page().total, 0)


if __name__ == "__main__":
    unittest.main()
