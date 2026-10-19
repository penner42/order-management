"""Manual receiving links preserve original tracking and apply atomically."""
from datetime import datetime, timezone
import unittest
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.config import settings
from app.models import BuyingGroup, Item, Order, Payment, PaymentLineItem, ReceivingReviewPending, Shipment, ShipmentItem, Store
from app.models.item import ItemStatus
from app.routers.items import bulk_update_items, split_item, update_item
from app.schemas.buying_group import ReceivingReceiptLinkRequest
from app.schemas.item import ItemBulkUpdateRequest, ItemSplitRequest, ItemUpdate
from app.utils.buying_group_apis.check_receiving import save_receiving_review_pending, prune_receiving_review_pending
from app.utils.receiving_links import candidate_lines, link_receipt, receipt_key, unlink_receipt


class ReceiptLinkTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.store = Store(name="Store")
        self.group = BuyingGroup(name="Warehouse")
        self.other_group = BuyingGroup(name="Other warehouse")
        self.db.add_all([self.store, self.group, self.other_group])
        self.db.flush()
        self.order = Order(store_id=self.store.id, buying_group_id=self.group.id, store_order_number="ORDER-123")
        self.db.add(self.order)
        self.db.flush()
        self.item = Item(order_id=self.order.id, description="Headphones", quantity=2, status=ItemStatus.SUBMITTED, price_sold=20)
        self.shipment = Shipment(tracking_number="ORIGINAL", notes="Keep these notes")
        self.db.add_all([self.item, self.shipment])
        self.db.flush()
        self.db.add(ShipmentItem(item_id=self.item.id, shipment_id=self.shipment.id))
        self.db.commit()
        self.receipt = {"receipt_id": "R-1", "tracking_number": "INCORRECT", "tracking_numbers": ["INCORRECT", "ALT"],
                        "quantity": 2, "description": "Warehouse headphones", "received_price": "25.00",
                        "received_ext_total": "50.00", "scanned_at": "2026-10-15T12:00:00Z"}
        self.save()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def save(self, receipts=None, group=None, matches=None):
        group = group or self.group
        save_receiving_review_pending(self.db, {"buying_group_id": group.id, "buying_group_name": group.name,
            "unknown_receipts": [self.receipt] if receipts is None else receipts, "matches": matches or []})

    def link(self, **changes):
        return link_receipt(self.db, ReceivingReceiptLinkRequest(**{
            "buying_group_id": self.group.id, "receipt_key": receipt_key(self.receipt),
            "item_id": self.item.id, "receipt_tracking_number": "INCORRECT", **changes,
        }))

    def apply(self, match, **changes):
        return bulk_update_items(ItemBulkUpdateRequest(updates=[{
            "item_id": match["item_id"], "receipt_id": match["receipt_id"], "status": "scanned",
            "scanned_at": "2026-10-15T12:00:00Z", "price_sold": "25.00",
            "receiving_group_id": self.group.id, "receiving_row_id": match["row_id"], **changes,
        }]), db=self.db, _=None)

    def test_stage_survives_checks_and_prune_then_unlinks(self):
        result = self.link()
        self.assertFalse(result["unknown_receipts"])
        self.assertIsNone(self.item.receipt_id)
        self.assertEqual(self.item.status, ItemStatus.SUBMITTED)
        self.save(matches=[{"item_id": self.item.id, "receipt_id": "automatic"}])
        self.save(receipts=[])
        prune_receiving_review_pending(self.db, buying_group_id=self.group.id, item_ids=[self.item.id])
        pending = self.db.get(ReceivingReviewPending, self.group.id).payload
        self.assertEqual(len(pending["matches"]), 1)
        self.assertEqual(pending["matches"][0]["manual_receipt_key"], receipt_key(self.receipt))
        self.assertEqual(unlink_receipt(self.db, self.group.id, receipt_key(self.receipt))["unknown_receipts"][0]["receipt_id"], "R-1")

    def test_apply_preserves_tracking_and_deduplicates_only_same_receipt_in_group(self):
        match = self.link()["matches"][0]
        self.apply(match)
        self.db.expire_all()
        self.assertEqual((self.item.receipt_id, self.item.receipt_tracking_number), ("R-1", "INCORRECT"))
        self.assertEqual(self.item.status, ItemStatus.SCANNED)
        self.assertEqual(float(self.item.price_sold), 25)
        self.assertEqual(self.shipment.tracking_number, "ORIGINAL")
        self.assertEqual(self.shipment.notes, "Keep these notes")
        self.assertIsNone(self.db.get(ReceivingReviewPending, self.group.id))
        self.save(receipts=[self.receipt, {**self.receipt, "receipt_id": "R-2"}])
        remaining = self.db.get(ReceivingReviewPending, self.group.id).payload["unknown_receipts"]
        self.assertEqual([r["receipt_id"] for r in remaining], ["R-2"])
        self.save(group=self.other_group)
        self.assertEqual(len(self.db.get(ReceivingReviewPending, self.other_group.id).payload["unknown_receipts"]), 1)

    def test_staged_receipt_takes_precedence_over_other_automatic_destination(self):
        manual = self.link()["matches"][0]
        automatic = {"item_id": self.item.id + 100, "receipt_id": "R-1", "tracking_number": "ALT",
                     "received_item": self.receipt["description"]}
        other_receipt = {**automatic, "receipt_id": "R-2"}
        self.save(matches=[automatic, other_receipt])
        matches = self.db.get(ReceivingReviewPending, self.group.id).payload["matches"]
        self.assertEqual(matches, [other_receipt, manual])

    def test_explicit_split_retains_shipment_and_clears_new_receipt_fields(self):
        self.item.receipt_id = "OLD"
        self.item.receipt_tracking_number = "OLD-SCAN"
        self.item.quantity = 5
        self.db.commit()
        result = self.link(split_quantity=2, split_target="split_off")
        match = result["matches"][0]
        new = self.db.get(Item, match["item_id"])
        self.assertNotEqual(new.id, self.item.id)
        self.assertEqual((self.item.quantity, new.quantity), (3, 2))
        self.assertEqual((self.item.receipt_id, self.item.receipt_tracking_number), ("OLD", "OLD-SCAN"))
        self.assertIsNone(new.receipt_id)
        self.assertIsNone(new.receipt_tracking_number)
        self.assertEqual(new.shipment_items[0].shipment_id, self.shipment.id)
        unlink_receipt(self.db, self.group.id, receipt_key(self.receipt))
        self.assertEqual(self.db.get(Item, new.id).quantity, 2)

    def test_normal_split_also_clears_receipts_on_new_line(self):
        self.item.receipt_id = "OLD"
        self.item.receipt_tracking_number = "OLD-SCAN"
        self.db.commit()
        result = split_item(self.item.id, ItemSplitRequest(keep_quantity=1), db=self.db, _=None)
        self.assertEqual(result.kept.receipt_id, "OLD")
        self.assertIsNone(result.split_off.receipt_id)
        self.assertIsNone(result.split_off.receipt_tracking_number)

    def test_rejects_conflicting_receipt_and_mismatched_quantity_without_splitting(self):
        self.item.receipt_id = "OLD"
        self.db.commit()
        with self.assertRaises(HTTPException):
            self.link()
        self.db.rollback()
        self.item.receipt_id = None
        self.item.quantity = 4
        self.db.commit()
        with self.assertRaises(HTTPException):
            self.link()
        self.db.rollback()
        self.assertEqual(self.item.quantity, 4)
        self.assertEqual(self.db.query(Item).count(), 1)

    def test_rejects_duplicate_and_wrong_group_or_tracking(self):
        for changes in ({"buying_group_id": self.other_group.id}, {"receipt_tracking_number": "INVENTED"}):
            with self.assertRaises(HTTPException):
                self.link(**changes)
            self.db.rollback()
        self.link()
        with self.assertRaises(HTTPException):
            self.link()
        self.db.rollback()
        second = {**self.receipt, "receipt_id": "R-2"}
        self.save(receipts=[second])
        with self.assertRaises(HTTPException):
            self.link(receipt_key=receipt_key(second))

    def test_missing_quantity_requires_confirmation(self):
        self.receipt["quantity"] = None
        self.save()
        with self.assertRaises(HTTPException):
            self.link()
        self.db.rollback()
        self.assertEqual(self.link(confirm_full_quantity=True)["matches"][0]["quantity"], 2)

    def test_stale_apply_does_not_consume_queue_or_overwrite_receipt(self):
        match = self.link()["matches"][0]
        self.item.receipt_id = "CORRECTED"
        self.db.commit()
        with self.assertRaises(HTTPException):
            self.apply(match)
        self.db.rollback()
        self.assertEqual(self.item.receipt_id, "CORRECTED")
        self.assertEqual(len(self.db.get(ReceivingReviewPending, self.group.id).payload["matches"]), 1)
        self.item.receipt_id = None
        self.item.quantity = 3
        self.db.commit()
        with self.assertRaises(HTTPException):
            self.apply(match)

    def test_all_terminal_statuses_are_preserved(self):
        for status in set(ItemStatus) - {ItemStatus.PURCHASED, ItemStatus.SHIPPED, ItemStatus.SUBMITTED, ItemStatus.NEEDS_REVIEW}:
            with self.subTest(status=status):
                self.item.status = status
                self.item.receipt_id = None
                self.item.receipt_tracking_number = None
                self.db.commit()
                self.save()
                self.apply(self.link()["matches"][0])
                self.assertEqual(self.item.status, status)

    def test_paid_line_can_link_but_cannot_split_and_payment_is_preserved(self):
        payment = Payment(buying_group_id=self.group.id, payment_requested_at=datetime.now(timezone.utc))
        self.db.add(payment)
        self.db.flush()
        self.db.add(PaymentLineItem(payment_id=payment.id, item_id=self.item.id, amount=40))
        self.item.quantity = 4
        self.db.commit()
        with self.assertRaises(HTTPException):
            self.link(split_quantity=2)
        self.db.rollback()
        self.item.quantity = 2
        self.db.commit()
        self.apply(self.link()["matches"][0])
        self.assertEqual(self.item.status, ItemStatus.SUBMITTED)
        self.assertEqual(self.db.query(PaymentLineItem).one().amount, 40)

    def test_bulk_failure_rolls_back_item_and_queue_together(self):
        match = self.link()["matches"][0]
        with self.assertRaises(HTTPException):
            bulk_update_items(ItemBulkUpdateRequest(updates=[{
                "item_id": self.item.id, "receipt_id": "R-1", "receiving_group_id": self.group.id,
                "receiving_row_id": match["row_id"],
            }, {"item_id": 999999}]), db=self.db, _=None)
        self.db.rollback()
        self.assertIsNone(self.item.receipt_id)
        self.assertIsNotNone(self.db.get(ReceivingReviewPending, self.group.id))

    def test_normal_receiving_client_compatibility_writes_item_not_notes(self):
        bulk_update_items(ItemBulkUpdateRequest(updates=[{
            "item_id": self.item.id, "receipt_id": "R-1", "warehouse_tracking": "INCORRECT",
        }]), db=self.db, _=None)
        self.assertEqual(self.item.receipt_tracking_number, "INCORRECT")
        self.assertEqual(self.shipment.notes, "Keep these notes")
        update_item(self.item.id, ItemUpdate(receipt_tracking_number="EDITED"), db=self.db, _=None)
        self.assertEqual(self.item.receipt_tracking_number, "EDITED")

    def test_candidate_search_includes_all_statuses_but_only_same_group(self):
        self.item.status = ItemStatus.CANCELED
        other_order = Order(store_id=self.store.id, buying_group_id=self.other_group.id)
        self.db.add(other_order)
        self.db.flush()
        self.db.add(Item(order_id=other_order.id, description="Headphones", quantity=1))
        self.db.commit()
        results = candidate_lines(self.db, self.group.id, "ORDER-123", 1)
        self.assertEqual(results["total"], 1)
        self.assertEqual(results["items"][0]["status"], "canceled")
        self.assertEqual(results["items"][0]["tracking_numbers"], ["ORIGINAL"])


@unittest.skipUnless(settings.database_url.startswith("postgresql"), "requires PostgreSQL in Docker")
class PostgresReceiptLinkTests(unittest.TestCase):
    def test_split_stage_and_apply_with_real_row_locks(self):
        """SQLite cannot check FOR UPDATE with the eager-loaded item relationships."""
        admin = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
        name = "test_receipt_links_" + uuid4().hex
        engine = None
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        try:
            engine = create_engine(admin.url.set(database=name))
            Base.metadata.create_all(engine)
            with sessionmaker(bind=engine, autoflush=False)() as db:
                store, group = Store(name="Test store"), BuyingGroup(name="Test warehouse")
                db.add_all([store, group])
                db.flush()
                order = Order(store_id=store.id, buying_group_id=group.id)
                db.add(order)
                db.flush()
                item = Item(order_id=order.id, quantity=3, status=ItemStatus.SUBMITTED, price_sold=20)
                db.add(item)
                db.commit()
                receipt = {"receipt_id": "PG-RECEIPT", "tracking_number": "BAD-SCAN", "quantity": 1}
                save_receiving_review_pending(db, {"buying_group_id": group.id, "buying_group_name": group.name,
                    "matches": [], "unknown_receipts": [receipt]})
                session = link_receipt(db, ReceivingReceiptLinkRequest(buying_group_id=group.id,
                    receipt_key=receipt_key(receipt), item_id=item.id, receipt_tracking_number="BAD-SCAN", split_quantity=1))
                match = session["matches"][0]
                item_id = item.id
                group_id = group.id
                # Confirmation is a separate HTTP request, with a fresh identity map.
                # Reusing the staged item hides refresh's joined eager-load behavior.
                db.expunge_all()
                bulk_update_items(ItemBulkUpdateRequest(updates=[{"item_id": item_id,
                    "receipt_id": "PG-RECEIPT", "receiving_group_id": group_id,
                    "receiving_row_id": match["row_id"]}]), db=db, _=None)
                item = db.get(Item, item_id)
                self.assertEqual(item.receipt_tracking_number, "BAD-SCAN")
                self.assertEqual(item.quantity, 1)
                self.assertEqual(item.status, ItemStatus.SCANNED)
                self.assertEqual(db.query(Item).count(), 2)
                self.assertIsNone(db.get(ReceivingReviewPending, group_id))
        finally:
            if engine is not None:
                engine.dispose()
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
            admin.dispose()


if __name__ == "__main__":
    unittest.main()
