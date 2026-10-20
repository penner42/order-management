"""Payment imports use isolated SQLite data and sanitized provider fixtures."""
import gzip
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import Request

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import (BuyingGroup, Store, Order, Item, Payment, PaymentLineItem,
    PaymentSource, PaymentReview, PaymentCoverage, PaymentCorrection)
from app.models.item import ItemStatus
from app.payment_providers import (PaymentEvidence, ReceiptEvidence, PaymentFetch,
    ParsefilePaymentProvider, PROVIDERS)
from app import payment_review as service
from app.payment_totals import payment_amount, item_allocated
from app.monarch import payment_fingerprint
from app.schemas.payment import PaymentRead, PaymentLineItemCreate
from app.schemas.payment_review import ReviewEdit
from app.routers.payments import delete_payment, add_payment_line_item
from app.utils.buying_group_apis import parsefile

T = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)


def receipt(rid="r1", amount="100", day=1, **kw):
    return ReceiptEvidence(external_id=rid, amount=amount, created_at=T + timedelta(days=day), quantity=1, **kw)


def payment(pid="p1", amount="100", day=2, **kw):
    return PaymentEvidence(external_id=pid, amount=amount, created_at=T + timedelta(days=day), status="PAID",
        sent_at=T + timedelta(days=day + 4), **kw)


class FakeProvider:
    key = "fake"
    def ready(self, group): return True, None
    def fetch(self, group):
        # Explicit links override dates: this receipt was created after its payment.
        return PaymentFetch([payment(receipt_ids=["r1"])], [receipt(day=3)])


class PaymentReviewTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        with self.engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.group = BuyingGroup(name="Example", api_framework="parsefile")
        self.store = Store(name="Store")
        self.db.add_all([self.group, self.store]); self.db.flush()
        self.order = Order(store_id=self.store.id, buying_group_id=self.group.id)
        self.db.add(self.order); self.db.flush()
        self.item = self.local("r1", "100")
        self.db.commit()

    def tearDown(self):
        self.db.close(); self.engine.dispose()

    def local(self, rid, amount):
        item = Item(order_id=self.order.id, quantity=1, price_sold=Decimal(amount), receipt_id=rid,
                    status=ItemStatus.SCANNED, description="Sanitized item")
        self.db.add(item); self.db.flush()
        return item

    def load(self, payments=None, receipts=None, **kw):
        return service.refresh(self.db, self.group.id,
            PaymentFetch(payments or [payment()], receipts or [receipt()], **kw), "parsefile")

    def confirm(self, review):
        return service.confirm_review(self.db, review["id"], review["version"], review["fingerprint"])

    def edit(self, review, **changes):
        content = {**review["content"], **changes}
        return service.save_review(self.db, review["id"], ReviewEdit(version=review["version"], content=content))

    def test_import_confirm_idempotency_and_sent_not_received(self):
        first = self.load()["reviews"][0]
        self.assertEqual(first["issues"], [])
        view = self.confirm(first)
        p = self.db.get(Payment, view["reviews"][0]["payment_id"])
        self.assertEqual(payment_amount(p), Decimal("100"))
        self.assertIsNotNone(p.payment_sent_at)
        self.assertIsNone(p.payment_received_at)
        self.assertEqual(PaymentRead.model_validate(p).source_details["provider"], "parsefile")
        self.load()
        self.assertEqual(self.db.query(Payment).count(), 1)
        with self.assertRaises(HTTPException): self.confirm(first)
        with self.assertRaises(HTTPException): delete_payment(p.id, db=self.db, _=None)
        self.db.rollback()
        with self.assertRaises(HTTPException):
            add_payment_line_item(p.id, PaymentLineItemCreate(item_id=self.item.id), db=self.db, _=None)

    def test_groups_with_same_external_id_are_independent(self):
        self.load()
        other = BuyingGroup(name="Other", api_framework="parsefile")
        self.db.add(other); self.db.commit()
        view = service.refresh(self.db, other.id, PaymentFetch([payment()], [receipt()]), "parsefile")
        self.assertTrue(view["reviews"][0]["issues"])
        self.assertEqual(self.db.query(PaymentSource).count(), 4)

    def test_fake_provider_explicit_links_use_shared_confirmation(self):
        with patch.dict(PROVIDERS, fake=FakeProvider()):
            self.group.api_framework = "fake"; self.db.commit()
            result = FakeProvider().fetch(self.group)
            view = service.refresh(self.db, self.group.id, result, "fake")
            self.assertEqual(view["reviews"][0]["issues"], [])
            self.confirm(view["reviews"][0])
            self.assertEqual(self.db.query(PaymentCoverage).count(), 1)

    def test_partial_fetch_preserves_previous_snapshot(self):
        before = self.load()["reviews"][0]
        with self.assertRaises(HTTPException): self.load([payment(amount="500")], complete=False)
        self.db.rollback()
        after = service.group_view(self.db, self.group.id)["reviews"][0]
        self.assertEqual(after, before)

    def test_manual_overrides_survive_refresh_and_versions_are_checked(self):
        view = self.load()["reviews"][0]
        edited = self.edit(view, receipt_ids=[], allocations=[], note="Move coverage to another payment")["reviews"][0]
        with self.assertRaises(HTTPException): self.edit(view, note="Stale save")
        self.db.rollback()
        refreshed = self.load()["reviews"][0]
        self.assertEqual(refreshed["content"]["receipt_ids"], [])
        self.assertEqual(refreshed["content"]["note"], edited["content"]["note"])

    def test_missing_local_items_and_quantities_block_confirmation(self):
        self.item.receipt_id = None; self.db.commit()
        view = self.load()["reviews"][0]
        with self.assertRaises(HTTPException): self.confirm(view)
        self.assertEqual(self.db.query(Payment).count(), 0)
        self.db.rollback()
        view = self.edit(view, allocations=[{"receipt_source_id": view["content"]["receipt_ids"][0],
            "item_id": self.item.id, "quantity": 2, "amount": "100"}])["reviews"][0]
        self.assertTrue(any("quantity" in i or "quantit" in i for i in view["issues"]))

    def test_no_earliest_local_order_cutoff(self):
        self.order.purchase_date = T + timedelta(days=30); self.db.commit()
        self.assertEqual(len(self.load()["receipts"]), 1)

    def test_missing_payment_date_requires_an_explained_manual_boundary(self):
        source = payment()
        source.created_at = None
        view = self.load([source])["reviews"][0]
        rid = self.db.query(PaymentSource).filter_by(kind="receipt").one().id
        view = self.edit(view, window_end=(T + timedelta(days=2)).isoformat(), note="Request timestamp verified manually",
            receipt_ids=[rid], allocations=[{"receipt_source_id": rid, "item_id": self.item.id, "quantity": 1, "amount": "100.00"}])["reviews"][0]
        self.assertEqual(view["issues"], [])
        self.confirm(view)
        self.assertEqual(self.db.query(Payment).one().payment_requested_at.date(), (T + timedelta(days=2)).date())

    def test_carry_decrease_and_repeated_refresh_do_not_double_count(self):
        self.confirm(self.load()["reviews"][0])
        other = self.local("r2", "200"); self.db.commit()
        receipts = [receipt(amount="80"), receipt("r2", "200", day=3)]
        payments = [payment(), payment("p2", "180", day=4)]
        view = self.load(payments, receipts)
        second = view["reviews"][1]
        self.assertEqual(second["issues"], [])
        self.assertEqual(second["content"]["corrections"][0]["amount"], "-20.00")
        result = self.confirm(second)
        p = self.db.get(Payment, result["reviews"][1]["payment_id"])
        self.assertEqual(payment_amount(p), Decimal("180"))
        self.assertEqual(item_allocated(self.db, self.item.id), Decimal("80"))
        fingerprint = payment_fingerprint(p)
        self.load(payments, receipts)
        self.assertEqual(service.outstanding_corrections(self.db, self.group.id), [])
        self.assertEqual(self.db.query(PaymentCorrection).count(), 1)
        self.assertEqual(fingerprint, payment_fingerprint(p))
        self.assertEqual(self.db.get(Payment, result["reviews"][0]["payment_id"]).line_items[0].amount, Decimal("100"))

    def test_increase_void_and_no_destination_remain_queued(self):
        self.confirm(self.load()["reviews"][0])
        view = self.load(receipts=[receipt(amount="120")])
        self.assertEqual(view["corrections"][0]["amount"], "20.00")
        self.assertEqual(len(view["reviews"]), 1)
        view = self.load(receipts=[receipt(amount="120", void=True)])
        self.assertEqual(view["corrections"][0]["amount"], "-100.00")

    def test_changed_payment_and_missing_receipt_preserve_saved_payment(self):
        self.confirm(self.load()["reviews"][0])
        view = self.load([payment(amount="105")], [receipt("different", "105")])
        self.assertTrue(any("changed" in i for i in view["reviews"][0]["issues"]))
        self.assertFalse(view["corrections"][0]["present"])
        self.assertEqual(self.db.query(Payment).one().total_amount, Decimal("100"))

    def test_local_edit_between_preview_and_confirm_is_revalidated(self):
        view = self.load()["reviews"][0]
        self.item.price_sold = 50; self.db.commit()
        with self.assertRaises(HTTPException): self.confirm(view)
        self.assertEqual(self.db.query(Payment).count(), 0)

    def test_matching_another_item_after_preview_requires_new_fingerprint(self):
        view = self.load()["reviews"][0]
        self.item.receipt_id = None
        other = self.local("r1", "100"); self.db.commit()
        with self.assertRaises(HTTPException) as caught: self.confirm(view)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.db.query(Payment).count(), 0)

    def test_transaction_failure_rolls_back_payment_and_coverage(self):
        view = self.load()["reviews"][0]
        with patch.object(self.db, "commit", side_effect=RuntimeError("simulated transaction failure")):
            with self.assertRaises(RuntimeError): self.confirm(view)
        self.db.rollback()
        self.assertEqual(self.db.query(Payment).count(), 0)
        self.assertEqual(self.db.query(PaymentCoverage).count(), 0)
        self.assertIsNone(self.db.query(PaymentReview).one().payment_id)

    def test_positive_partial_correction_leaves_remainder_queued(self):
        self.confirm(self.load()["reviews"][0])
        self.local("r2", "200"); self.db.commit()
        receipts = [receipt(amount="130"), receipt("r2", "200", day=3)]
        view = self.load([payment(), payment("p2", "220", day=4)], receipts)
        second = view["reviews"][1]
        correction = {**second["content"]["corrections"][0], "amount": "20.00", "explanation": "Only twenty included in this payment"}
        second = self.edit(second, corrections=[correction])["reviews"][1]
        self.assertEqual(second["issues"], [])
        self.confirm(second)
        self.assertEqual(service.outstanding_corrections(self.db, self.group.id)[0]["amount"], "10.00")
        self.assertEqual(item_allocated(self.db, self.item.id), Decimal("120"))

    def test_explicit_bonus_requires_an_explanation(self):
        view = self.load([payment(amount="110")])["reviews"][0]
        view = self.edit(view, payment_bonus="10.00")["reviews"][0]
        self.assertTrue(any("explanation" in i for i in view["issues"]))
        view = self.edit(view, note="Portal explicitly includes a ten dollar bonus")["reviews"][0]
        result = self.confirm(view)
        self.assertEqual(self.db.query(Payment).one().payment_bonus, Decimal("10"))

    def test_manual_correction_destination_survives_refresh(self):
        self.confirm(self.load()["reviews"][0])
        self.local("r2", "200"); self.local("r3", "300"); self.db.commit()
        receipts = [receipt(amount="80"), receipt("r2", "200", day=3), receipt("r3", "300", day=5)]
        payments = [payment(), payment("p2", "200", day=4), payment("p3", "280", day=6)]
        view = self.load(payments, receipts)
        proposed = view["reviews"][1]["content"]["corrections"]
        view = self.edit(view["reviews"][1], corrections=[])
        view = self.edit(view["reviews"][2], corrections=proposed)
        view = self.load(payments, receipts)
        self.assertEqual(view["reviews"][1]["content"]["corrections"], [])
        self.assertEqual(view["reviews"][2]["content"]["corrections"], proposed)

    def test_explicit_link_existing_payment_and_protect_coverage(self):
        view = self.load()["reviews"][0]
        p = Payment(buying_group_id=self.group.id, payment_requested_at=T, payment_bonus=0)
        self.db.add(p); self.db.flush()
        self.db.add(PaymentLineItem(payment_id=p.id, item_id=self.item.id, amount=100)); self.db.commit()
        view = self.edit(view, existing_payment_id=p.id)["reviews"][0]
        self.assertEqual(view["issues"], [])
        self.confirm(view)
        self.assertEqual(self.db.query(Payment).count(), 1)
        self.assertEqual(self.db.query(PaymentReview).one().payment_id, p.id)

    def test_matching_total_reports_item_and_bonus_differences_before_confirmation(self):
        view = self.load()["reviews"][0]
        p = Payment(buying_group_id=self.group.id, payment_id="p1", payment_requested_at=T, payment_bonus=10)
        self.db.add(p); self.db.flush()
        line = PaymentLineItem(payment_id=p.id, item_id=self.item.id, amount=90)
        self.db.add(line); self.db.commit()
        view = self.edit(view, existing_payment_id=p.id)["reviews"][0]
        self.assertEqual(payment_amount(p), Decimal("100"))
        self.assertIn(f"Item #{self.item.id} amount differs: local payment $90.00; review $100.00.", view["issues"])
        self.assertIn("Bonus differs: local payment $10.00; review $0.00.", view["issues"])
        with self.assertRaises(HTTPException) as caught:
            self.confirm(view)
        self.assertIn("Bonus differs", str(caught.exception.detail))
        self.db.rollback()
        self.assertEqual(self.db.query(Payment).count(), 1)
        self.assertEqual(self.db.get(PaymentLineItem, line.id).amount, Decimal("90"))
        self.assertEqual(self.db.get(Payment, p.id).payment_bonus, Decimal("10"))
        self.assertEqual(self.db.query(PaymentCoverage).count(), 0)

    def test_same_total_with_different_items_reports_both_item_sets(self):
        view = self.load()["reviews"][0]
        other = self.local("other-receipt", "100")
        p = Payment(buying_group_id=self.group.id, payment_id="p1", payment_requested_at=T, payment_bonus=0)
        self.db.add(p); self.db.flush()
        self.db.add(PaymentLineItem(payment_id=p.id, item_id=other.id, amount=100)); self.db.commit()
        view = self.edit(view, existing_payment_id=p.id)["reviews"][0]
        self.assertIn(f"Item #{self.item.id} has $100.00 in this review but is not on the selected local payment.", view["issues"])
        self.assertIn(f"Item #{other.id} has $100.00 on the selected local payment but is not allocated in this review.", view["issues"])

    def test_boundary_timezone_and_equal_timestamp_sequence(self):
        r = receipt(); p = payment(day=1)
        r.created_at = datetime(2026, 5, 2, 8, tzinfo=timezone(timedelta(hours=-4)))
        r.sequence, p.sequence = 1, 2
        view = self.load([p], [r])["reviews"][0]
        self.assertEqual(view["issues"], [])
        r.sequence = None
        view = self.load([p], [r])["reviews"][0]
        self.assertEqual(view["content"]["receipt_ids"], [])

    def test_pfd_sanitized_history_reconciles_all_windows(self):
        amounts = [2060, 1230, 11465, 9624, 10409, 9627, 8153]
        counts = [2, 1, 7, 6, 8, 7, 18]
        payments, receipts = [], []
        for n, (amount, count) in enumerate(zip(amounts, counts)):
            payments.append(payment(f"p{n}", str(amount), day=n * 2 + 2))
            for j in range(count):
                # Preserve aggregate amounts and receipt counts without private order data.
                value = amount // count if j < count - 1 else amount - (amount // count) * (count - 1)
                receipts.append(receipt(f"r{n}-{j}", str(value), day=n * 2 + 1))
        receipts.extend([receipt("open1", "594", day=16), receipt("open2", "1602", day=17)])
        view = self.load(payments, receipts)
        self.assertEqual(len(view["receipts"]), 51)
        indexed = {r["id"]: Decimal(r["payload"]["amount"]) for r in view["receipts"]}
        used = set()
        for review, amount in zip(view["reviews"], amounts):
            ids = review["content"]["receipt_ids"]
            self.assertEqual(sum(indexed[i] for i in ids), amount)
            used.update(ids)
        self.assertEqual(sum(value for key, value in indexed.items() if key not in used), 2196)


class ProviderTests(unittest.TestCase):
    def test_ready_reports_missing_configuration(self):
        ready, reason = ParsefilePaymentProvider().ready(SimpleNamespace())
        self.assertFalse(ready); self.assertIn("api_password", reason)

    def test_gzip_decode_and_canonical_host_validation(self):
        self.assertTrue(parsefile._decode_portal_response([], gzip.compress(b'{"success": true}'))["success"])
        redirect = parsefile._SafeRedirect()
        request = Request("https://example.test/f/login")
        upgraded = redirect.redirect_request(request, None, 302, "", {}, "http://www.example.test/f/login")
        self.assertEqual(upgraded.full_url, "https://www.example.test/f/login")
        for target in ("https://attacker.test/login", "https://example.test:8443/login", "https://user:pw@example.test/login"):
            with self.assertRaises(RuntimeError): redirect.redirect_request(request, None, 302, "", {}, target)

    def test_pagination_and_session_expiry(self):
        client = ParsefilePaymentProvider()
        def data(rows, count): return {"loadtable": [{"table": "it@payments", "data": {"data": rows, "count": count}}]}
        with patch.object(parsefile, "_portal_post", side_effect=[parsefile._PortalSessionExpired(), ("s", data([{"f_id": 1}], 2)), ("s", data([{"f_id": 2}], 2))]) as post, patch.object(parsefile, "portal_login", return_value="new"):
            _, rows = client._table("https://example.test", "test@example.test", "secret", "old", "it@payments")
            self.assertEqual(len(rows), 2)
            self.assertEqual(post.call_args.kwargs["data"]["page"], 2)
        with patch.object(parsefile, "_portal_post", return_value=("s", data([{"f_id": 1}], 2))):
            with self.assertRaises(ValueError): client._table("https://example.test", "test@example.test", "secret", "s", "it@payments")
        with patch.object(parsefile, "_portal_post", return_value=("s", data([], 2))):
            with self.assertRaises(ValueError): client._table("https://example.test", "test@example.test", "secret", "s", "it@payments")

    def test_provider_uses_each_groups_host_and_credentials(self):
        client = ParsefilePaymentProvider()
        activity = [{"f_type": "PAYMENT", "f_id": 1, "f_amount": "-100", "f_num": 2},
            {"f_type": "RECEIPT", "f_id": 7, "f_amount": "100", "f_num": 1, "f_time": 1770000000, "f_verified": 1}]
        payments = [{"f_id": 1, "f_amount": "100", "f_created": "Wed May 06 2026 15:50:13 -0700",
            "f_status": "PAID", "f_paid_date": "05/18/2026"}]
        for host, email in (("https://one.example", "one@example.test"), ("https://two.example", "two@example.test")):
            group = SimpleNamespace(base_url=host, api_email=email, api_password="test-password", bearer_token="test-token", api_user_id=42)
            with patch("app.payment_providers.canonical_base", return_value=host), patch.object(parsefile, "portal_login", return_value="session") as login, patch.object(client, "_table", side_effect=[("session", activity), ("session", payments)]), patch.object(parsefile, "list_all_received_items", return_value=SimpleNamespace(rows=[])):
                result = client.fetch(group)
                self.assertEqual(len(result.payments), 1)
                self.assertEqual(login.call_args.kwargs["base_url"], host)
                self.assertEqual(login.call_args.kwargs["email"], email)


if __name__ == "__main__": unittest.main()
