"""Purchase-registration regressions. All provider requests are mocked."""
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import bfmr_orders as service
from app.config import settings
from app.database import Base
from app.models import BfmrOrderTask, BuyingGroup, Item, Order, Shipment, ShipmentItem, Store, User
from app.models.item import ItemStatus
from app.routers import bfmr_orders as routes
from app.routers.orders import update_order
from app.routers.store_imports import apply_store_order_payload
from app.schemas.bfmr_orders import BfmrAllocateRequest
from app.schemas.item import ItemSplitRequest
from app.schemas.order import OrderUpdate
from app.schemas.store_import import StoreOrderImportPayload
from app.utils.buying_group_apis import bfmr
from app.utils.item_split import split_item_record

create_purchase = bfmr.create_purchase


def tracker(*, rid="opaque+RID==", pid=None, qty=5, sku="ASIN1", retailer="Amazon", status="reserved", order_no=None, tracking=None, **extra):
    return bfmr._parse_tracker_row({
        "reserve_id": rid, "purchase_id": pid, "shipment_id": None, "qty": str(qty),
        "order_id": order_no, "tracking_number": tracking, "status": status, "item_name": "Widget",
        "retailer_links": [{"retailer": retailer, "identifier_id": sku}], **extra,
    })


class BfmrOrderNumberTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        self.db = self.factory()
        self.user = User(username="bfmr-test-user", hashed_password="unused", role="user")
        self.group = BuyingGroup(name="BFMR test", api_framework="bfmr", api_username="test-key", api_password="test-secret",
                                 order_number_submit_enabled=True)
        self.store = Store(name="Amazon")
        self.db.add_all([self.user, self.group, self.store])
        self.db.flush()
        self.group.user_id = self.user.id
        self.db.commit()
        self.policy = patch.object(settings, "group_submissions_enabled", True)
        self.policy.start()
        self.fetch = patch.object(service, "fetch_rows", return_value=[]).start()
        patch.object(routes, "fetch_rows", side_effect=lambda group: self.fetch(group)).start()
        patch("urllib.request.urlopen", side_effect=AssertionError("Tests must not access the network")).start()
        self.create = patch.object(bfmr, "create_purchase", return_value={"reservations_response": {"valid_items": [{}]}}).start()

    def tearDown(self):
        patch.stopall()
        self.policy.stop()
        self.db.close()
        self.engine.dispose()

    def order(self, *, sku="ASIN1", qty=2, order_no="ORDER-1", slices=1):
        order = Order(store_id=self.store.id, user_id=self.user.id, buying_group_id=self.group.id,
                      store_order_number=order_no, imported_from_store="amazon", status="imported")
        self.db.add(order)
        self.db.flush()
        for index in range(slices):
            self.db.add(Item(order_id=order.id, description="Widget", quantity=qty // slices,
                             external_sku=sku, import_line_key="line-1", product_url="https://amazon.com/dp/ASIN1",
                             status=ItemStatus.PURCHASED, price_sold=0))
        self.db.flush()
        service.enqueue_order(self.db, order)
        self.db.commit()
        return order

    def task(self, order):
        return self.db.query(BfmrOrderTask).filter_by(order_id=order.id).one()

    def fulfill(self, *, fail_after_write=False):
        def create(**kwargs):
            row = next(r for r in self.fetch.return_value if r.reserve_id == kwargs["reserve_id"] and r.status == "reserved")
            row.qty -= kwargs["qty"]
            self.fetch.return_value.append(tracker(rid=kwargs["reserve_id"], pid=f"PID-{len(self.fetch.return_value)}",
                                                  qty=kwargs["qty"], status="purchased", order_no=kwargs["order_no"],
                                                  sku=next(iter(bfmr.retailer_identifiers(row)))[1]))
            if fail_after_write:
                raise urllib.error.URLError("simulated lost response")
            return {"reservations_response": {"valid_items": [{}]}}
        self.create.side_effect = create

    def process(self, task):
        service.process_task(self.db, task, self.group)
        self.db.refresh(task)

    def test_verified_retailer_identifiers_match_exactly(self):
        for name, sku in [("Amazon", "B0D3J71RM7"), ("Walmart", "5762417070"), ("Costco", "4000277042")]:
            with self.subTest(retailer=name):
                snapshot = {"retailer": name.lower(), "sku": sku}
                self.assertTrue(service.matches_product(tracker(retailer=name, sku=sku.lower()), snapshot))
                self.assertFalse(service.matches_product(tracker(retailer="Other", sku=sku), snapshot))
                self.assertFalse(service.matches_product(tracker(retailer=name, sku=sku + "x"), snapshot))

    def test_parse_quantity_opaque_ids_and_reserved_eligibility(self):
        row = tracker()
        self.assertEqual(row.qty, 5)
        self.assertEqual(row.reserve_id, "opaque+RID==")
        self.assertTrue(service.active_reservation(row))
        for row in [tracker(status="deadline"), tracker(pid="PID"), tracker(order_no="ORDER"), tracker(qty=0),
                    tracker(order_deadline="2000-01-01"), tracker(order_deadline="unparseable")]:
            self.assertFalse(service.active_reservation(row))
        self.assertTrue(service.active_reservation(tracker(tracking_deadline="2000-01-01")))

    def test_purchase_payload_has_null_tracking_and_purchase_ids(self):
        with patch.object(bfmr, "_post_tracker_data", return_value={}) as post:
            create_purchase(base_url=None, api_key="test", api_secret="secret", reserve_id="opaque+RID==", order_no="ORDER", qty=2)
            self.assertEqual(post.call_args.kwargs["tracker_data"], [{"reserve_id": "opaque+RID==", "order_no": "ORDER", "qty": 2,
                                                                   "purchase_id": None, "shipment_id": None, "tracking_number": None}])

    def test_unique_match_registers_without_changing_item_status(self):
        order = self.order()
        task = self.task(order)
        self.fetch.return_value = [tracker()]
        self.fulfill()
        self.process(task)
        self.assertEqual(task.status, "submitted")
        self.assertEqual(task.allocations[0]["purchase_id"], "PID-1")
        self.assertEqual(order.items[0].status, ItemStatus.PURCHASED)
        self.assertEqual(self.create.call_args.kwargs["qty"], 2)
        self.assertGreaterEqual(self.fetch.call_count, 3)

    def test_no_product_id_never_uses_title_match(self):
        task = self.task(self.order(sku=None))
        self.fetch.return_value = [tracker()]
        self.process(task)
        self.assertEqual(task.status, "review")
        self.create.assert_not_called()

    def test_ambiguous_or_insufficient_commitments_need_review(self):
        task = self.task(self.order())
        for rows in [[tracker(), tracker(rid="another")], [tracker(qty=1)], []]:
            self.fetch.return_value = rows
            self.process(task)
            self.assertEqual(task.status, "review")
        self.create.assert_not_called()

    def test_shipment_slices_aggregate_and_reimport_deduplicates(self):
        order = self.order(qty=4, slices=2)
        service.enqueue_order(self.db, order)
        self.db.commit()
        self.assertEqual(self.db.query(BfmrOrderTask).count(), 1)
        self.assertEqual(self.task(order).snapshot["quantity"], 4)
        split_item_record(self.db, order.items[0], ItemSplitRequest(keep_quantity=1))
        self.db.commit()
        self.db.expire(order, ["items"])
        self.assertTrue(all(i.external_sku == "ASIN1" and i.import_line_key == "line-1" for i in order.items))
        service.enqueue_order(self.db, order)
        self.assertEqual(self.task(order).snapshot["quantity"], 4)

    def test_one_commitment_across_several_orders_preserves_remainder(self):
        first = self.task(self.order(qty=2))
        second = self.task(self.order(qty=1, order_no="ORDER-2"))
        self.fetch.return_value = [tracker(qty=5)]
        self.fulfill()
        self.process(first)
        self.process(second)
        self.assertEqual((first.status, second.status), ("submitted", "submitted"))
        self.assertEqual(self.fetch.return_value[0].qty, 2)
        self.assertEqual(self.create.call_count, 2)

    def test_timeout_after_success_reconciles_without_duplicate_post(self):
        task = self.task(self.order())
        self.fetch.return_value = [tracker()]
        self.fulfill(fail_after_write=True)
        self.process(task)
        self.assertEqual(task.status, "retry")
        self.assertEqual(task.allocations[0]["status"], "sending")
        self.process(task)
        self.assertEqual(task.status, "submitted")
        self.assertEqual(self.create.call_count, 1)

    def test_rate_limit_on_verification_read_retains_sending_journal(self):
        task = self.task(self.order())
        self.fetch.return_value = [tracker()]
        self.fulfill()
        reads = 0
        def fetch(group):
            nonlocal reads
            reads += 1
            if reads == 3:
                raise urllib.error.HTTPError("https://example.invalid", 429, "rate limited", {}, None)
            return self.fetch.return_value
        self.fetch.side_effect = fetch
        self.process(task)
        self.assertEqual(task.status, "retry")
        self.assertEqual(task.allocations[0]["status"], "sending")
        self.process(task)
        self.assertEqual(task.status, "submitted")
        self.assertEqual(self.create.call_count, 1)

    def test_rejected_post_can_retry_after_rate_limit(self):
        task = self.task(self.order())
        self.fetch.return_value = [tracker()]
        self.create.side_effect = urllib.error.HTTPError("https://example.invalid", 429, "rate limited", {}, None)
        self.process(task)
        self.assertEqual(task.status, "retry")
        self.assertEqual(task.allocations[0]["status"], "planned")
        self.fulfill()
        self.process(task)
        self.assertEqual(task.status, "submitted")
        self.assertEqual(self.create.call_count, 2)

    def test_manual_purchase_between_selection_and_submission_is_reconciled(self):
        task = self.task(self.order())
        task.allocations = [{"reserve_id": "opaque+RID==", "qty": 2, "status": "planned"}]
        self.db.commit()
        self.fetch.return_value = [tracker(qty=3), tracker(pid="manual", qty=2, status="purchased", order_no="ORDER-1")]
        self.process(task)
        self.assertEqual(task.status, "submitted")
        self.create.assert_not_called()

    def test_uncertain_timeout_without_visible_purchase_requires_review(self):
        task = self.task(self.order())
        self.fetch.return_value = [tracker()]
        self.create.side_effect = urllib.error.URLError("timeout")
        self.process(task)
        self.process(task)
        self.assertEqual(task.status, "review")
        self.assertEqual(self.create.call_count, 1)

    def test_restart_recovers_sending_journal(self):
        task = self.task(self.order())
        task.allocations = [{"reserve_id": "opaque+RID==", "qty": 2, "status": "sending", "before_purchase_ids": []}]
        task.status = "processing"
        self.db.commit()
        self.fetch.return_value = [tracker(pid="recovered", qty=2, status="purchased", order_no="ORDER-1")]
        self.process(task)
        self.assertEqual(task.status, "submitted")
        self.assertEqual(task.allocations[0]["purchase_id"], "recovered")
        self.create.assert_not_called()

    def test_existing_manual_purchase_is_adopted_without_post(self):
        task = self.task(self.order())
        self.fetch.return_value = [tracker(pid="manual", qty=2, status="purchased", order_no="ORDER-1")]
        self.process(task)
        self.assertEqual(task.status, "submitted")
        self.create.assert_not_called()

    def test_existing_purchase_with_different_quantity_needs_review(self):
        task = self.task(self.order())
        self.fetch.return_value = [tracker(pid="manual", qty=1, status="purchased", order_no="ORDER-1")]
        self.process(task)
        self.assertEqual(task.status, "review")
        self.create.assert_not_called()

    def test_read_failure_has_bounded_backoff_and_no_remote_intent(self):
        task = self.task(self.order())
        self.fetch.side_effect = RuntimeError("unavailable")
        for attempt in range(5):
            self.process(task)
            self.assertEqual(task.attempts, attempt + 1)
        self.assertEqual(task.status, "review")
        self.assertEqual(task.allocations, [])
        self.create.assert_not_called()

    def test_manual_allocations_split_across_commitments_and_partial_failure(self):
        task = self.task(self.order(qty=3))
        self.fetch.return_value = [tracker(qty=2), tracker(rid="RID-2", qty=1)]
        body = BfmrAllocateRequest(allocations=[{"reserve_id": "opaque+RID==", "qty": 2}, {"reserve_id": "RID-2", "qty": 1}])
        routes.allocate(self.group.id, task.id, body, self.db, self.user)
        self.fulfill()
        success = self.create.side_effect
        self.create.side_effect = lambda **kw: success(**kw) if kw["reserve_id"] != "RID-2" else {"reservations_response": {"invalid_items": [{"error": "expired"}]}}
        self.process(task)
        self.assertEqual(task.status, "review")
        self.assertEqual([a["status"] for a in task.allocations], ["submitted", "rejected"])
        self.fetch.return_value.append(tracker(rid="RID-3", qty=1))
        routes.allocate(self.group.id, task.id, BfmrAllocateRequest(allocations=[{"reserve_id": "RID-3", "qty": 1}]), self.db, self.user)
        self.create.side_effect = success
        self.process(task)
        self.assertEqual(task.status, "submitted")
        self.assertEqual([call.kwargs["reserve_id"] for call in self.create.call_args_list].count("opaque+RID=="), 1)

    def test_ownership_and_manual_availability_validation(self):
        task = self.task(self.order())
        stranger = SimpleNamespace(id=self.user.id + 100, role="user")
        for fn, args in [(routes.list_tasks, (self.group.id, self.db, stranger)),
                         (routes.candidates, (self.group.id, task.id, self.db, stranger))]:
            with self.assertRaises(HTTPException) as error:
                fn(*args)
            self.assertEqual(error.exception.status_code, 404)
        self.fetch.return_value = [tracker(qty=1)]
        with self.assertRaises(HTTPException) as error:
            routes.allocate(self.group.id, task.id, BfmrAllocateRequest(allocations=[{"reserve_id": "opaque+RID==", "qty": 2}]), self.db, self.user)
        self.assertEqual(error.exception.status_code, 409)

    def test_disabled_group_and_environment_block_writes(self):
        task = self.task(self.order())
        self.group.order_number_submit_enabled = False
        self.process(task)
        self.fetch.assert_not_called()
        with patch.object(settings, "group_submissions_enabled", False):
            with self.assertRaises(HTTPException):
                self.process(task)
            with patch.object(service, "SessionLocal") as factory:
                service.worker_tick()
                factory.assert_not_called()
            with self.assertRaises(HTTPException):
                routes.retry(self.group.id, task.id, self.db, self.user)
        self.create.assert_not_called()

    def test_registered_quantity_edit_and_personal_status_require_review(self):
        order = self.order()
        task = self.task(order)
        task.allocations = [{"reserve_id": "RID", "qty": 2, "purchase_id": "PID", "status": "submitted"}]
        task.status = "submitted"
        self.db.commit()
        order.items[0].quantity = 1
        service.enqueue_order(self.db, order)
        self.assertEqual(task.status, "review")
        self.db.commit()
        self.process(task)
        self.create.assert_not_called()

    def test_disable_during_lookup_prevents_purchase_post(self):
        task = self.task(self.order())
        def fetch(group):
            group.order_number_submit_enabled = False
            self.db.commit()
            return [tracker()]
        self.fetch.side_effect = fetch
        self.process(task)
        self.assertEqual(task.status, "pending")
        self.create.assert_not_called()

    def test_quantity_change_during_post_keeps_confirmed_purchase_in_review(self):
        order = self.order()
        task = self.task(order)
        self.fetch.return_value = [tracker()]
        self.fulfill()
        create = self.create.side_effect
        def changed(**kw):
            response = create(**kw)
            order.items[0].quantity = 1
            service.enqueue_order(self.db, order)
            self.db.commit()
            return response
        self.create.side_effect = changed
        self.process(task)
        self.assertEqual(task.status, "review")
        self.assertEqual(task.allocations[0]["status"], "submitted")
        self.assertEqual(task.snapshot["quantity"], 2)

    def test_worker_persists_review_when_order_changes_without_reenqueue(self):
        order = self.order()
        task = self.task(order)
        order.status = "personal"
        self.db.commit()
        self.process(task)
        self.assertEqual(task.status, "review")
        self.fetch.assert_not_called()

    def test_canceled_and_returning_units_are_not_queued(self):
        order = self.order()
        order.items[0].status = ItemStatus.CANCELED
        service.enqueue_order(self.db, order)
        self.assertEqual(self.task(order).status, "skipped")
        order.items[0].status = ItemStatus.NEEDS_RETURN
        self.assertEqual(service.snapshots(order, self.group), {})
        order.items[0].status = ItemStatus.PURCHASED
        order.status = "personal"
        self.assertEqual(service.snapshots(order, self.group), {})

    def test_import_transaction_persists_metadata_and_rolls_back_task(self):
        payload = StoreOrderImportPayload(store="amazon", externalOrder={"id": "IMPORTED-ORDER"},
                                         items=[{"logicalItemId": "ASIN1", "externalSku": "ASIN1", "name": "Widget",
                                                 "productUrl": "https://amazon.com/dp/ASIN1", "quantities": {"ordered": 2}}])
        order_id = apply_store_order_payload(self.db, payload, self.user, store_id=self.store.id, buying_group_id=self.group.id, commit=False)
        order = self.db.get(Order, order_id)
        self.assertEqual(order.items[0].external_sku, "ASIN1")
        self.assertEqual(self.task(order).snapshot["quantity"], 2)
        self.db.rollback()
        self.assertEqual(self.db.query(BfmrOrderTask).count(), 0)
        self.create.assert_not_called()

    def test_later_bfmr_assignment_and_toggle_do_not_scan_historical_orders(self):
        self.group.order_number_submit_enabled = False
        order = self.order()
        self.assertEqual(self.db.query(BfmrOrderTask).count(), 0)
        self.group.order_number_submit_enabled = True
        self.db.commit()
        self.assertEqual(self.db.query(BfmrOrderTask).count(), 0)
        order.buying_group_id = None
        self.db.commit()
        update_order(order.id, OrderUpdate(buying_group_id=self.group.id), self.db, self.user)
        self.assertEqual(self.task(order).status, "pending")

    def test_account_lock_is_nonblocking_and_serializes_alias_groups(self):
        alias = BuyingGroup(name="same account", api_username=self.group.api_username, base_url=self.group.base_url)
        with service.account_lock(self.db, self.group) as first:
            self.assertTrue(first)
            with service.account_lock(self.db, alias) as second:
                self.assertFalse(second)

    def test_multiple_products_sharing_tracking_route_to_different_purchases(self):
        order = self.order(sku="ASIN1", qty=2)
        second = Item(order_id=order.id, external_sku="ASIN2", import_line_key="line-2", description="Other", quantity=1,
                      status=ItemStatus.SHIPPED, price_sold=0)
        self.db.add(second)
        self.db.flush()
        self.db.expire(order, ["items"])
        service.enqueue_order(self.db, order)
        tasks = self.db.query(BfmrOrderTask).filter_by(order_id=order.id).all()
        for task in tasks:
            pid = "P1" if task.snapshot["sku"] == "ASIN1" else "P2"
            task.allocations = [{"reserve_id": "R" + pid, "purchase_id": pid, "qty": task.snapshot["quantity"], "status": "submitted"}]
            task.status = "submitted"
        shipment = Shipment(tracking_number="TRACK-SHARED")
        self.db.add(shipment)
        self.db.flush()
        for item in order.items:
            item.status = ItemStatus.SHIPPED
            self.db.add(ShipmentItem(item_id=item.id, shipment_id=shipment.id))
        self.db.commit()
        self.fetch.return_value = [tracker(rid="RP1", pid="P1", qty=2, status="purchased", order_no="ORDER-1"),
                                   tracker(rid="RP2", pid="P2", qty=1, sku="ASIN2", status="purchased", order_no="ORDER-1")]
        with patch.object(bfmr, "submit_trackings", side_effect=[
            bfmr.BfmrSubmitResult(success=True, message="OK", succeeded=["TRACK-SHARED"]),
            bfmr.BfmrSubmitResult(success=False, message="rejected", failed=["TRACK-SHARED"]),
        ]) as send:
            result = service.submit_tracking(self.db, self.group, [(item, order, "TRACK-SHARED") for item in order.items])
        self.assertEqual([call.kwargs["trackings"][0].purchase_id for call in send.call_args_list], ["P1", "P2"])
        self.assertEqual(len(result.item_ids), 1)
        first = next(i for i in order.items if i.external_sku == "ASIN1")
        self.assertEqual(first.status, ItemStatus.SUBMITTED)
        self.assertEqual(first.submission_api_id, "P1")
        self.assertEqual(second.status, ItemStatus.SHIPPED)


@unittest.skipUnless(settings.database_url.startswith("postgresql"), "requires Docker PostgreSQL")
class BfmrWorkerConcurrencyTests(unittest.TestCase):
    def test_competing_workers_serialize_the_same_account_without_losing_remainder(self):
        admin = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
        name = "test_bfmr_" + uuid4().hex
        engine = None
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        try:
            engine = create_engine(admin.url.set(database=name))
            Base.metadata.create_all(engine)
            sessions = sessionmaker(bind=engine)
            with sessions() as db:
                group = BuyingGroup(name="BFMR test", api_framework="bfmr", api_username="fixture-key", api_password="fixture-secret",
                                     order_number_submit_enabled=True)
                store = Store(name="Amazon")
                db.add_all([group, store])
                db.flush()
                for index in range(2):
                    order = Order(store_id=store.id, buying_group_id=group.id, imported_from_store="amazon",
                                  store_order_number=f"ORDER-{index}", status="imported")
                    db.add(order)
                    db.flush()
                    db.add(Item(order_id=order.id, description="Widget", quantity=1, external_sku="ASIN1", price_sold=0))
                    db.flush()
                    service.enqueue_order(db, order)
                db.commit()
            started, release = Event(), Event()
            remote = [tracker(qty=3)]
            def create(**kw):
                started.set()
                self.assertTrue(release.wait(timeout=10))
                remote[0].qty -= kw["qty"]
                remote.append(tracker(pid=f"P{len(remote)}", qty=kw["qty"], status="purchased", order_no=kw["order_no"]))
                return {}
            with patch.object(settings, "group_submissions_enabled", True), patch.object(service, "SessionLocal", sessions), \
                    patch.object(service, "fetch_rows", side_effect=lambda group: remote), \
                    patch.object(bfmr, "create_purchase", side_effect=create) as post, \
                    patch("urllib.request.urlopen", side_effect=AssertionError("No network in tests")):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(service.worker_tick)
                    try:
                        self.assertTrue(started.wait(timeout=10))
                        second = pool.submit(service.worker_tick)
                        second.result(timeout=10)
                    finally:
                        release.set()
                    first.result(timeout=10)
                self.assertEqual(post.call_count, 2)
                self.assertEqual(remote[0].qty, 1)
            with sessions() as db:
                self.assertEqual([task.status for task in db.query(BfmrOrderTask).all()], ["submitted", "submitted"])
        finally:
            if engine:
                engine.dispose()
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
            admin.dispose()
