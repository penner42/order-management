"""Tracking-level rewards, conserved line shares, and safe historical repair."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from app.bg_reward_math import reward_share, tracking_credits, tracking_key
from app.models import (Item, Order, Payment, PaymentLineItem, PaymentReview, PaymentSource,
                        ReceivingReceiptCache, Shipment, ShipmentItem, TrackingReward)
from app.models.item import ItemStatus
from app.payment_review import item_payable, review_token
from app.repair_tracking_rewards import repair, payment_snapshot
from app.schemas.item import ItemSplitRequest
from app.schemas.order import OrderRead
from app.tracking_rewards import order_reward_summaries, save_credits, sync_payment_rewards, saved_credit_values
from app.utils.buying_group_apis import buyinggroup as api
from app.utils.item_split import split_item_record
from app.utils.receiving_cache import encode, provider_identity
from tests import test_buyinggroup_rewards as fixtures


class TrackingRewardTests(unittest.TestCase):
    setUp = fixtures.BuyingGroupRewardTests.setUp
    check = fixtures.BuyingGroupRewardTests.check

    def cache(self, *, quantity=3, reward="0.05"):
        row = api._parse_receipt_row({"key": "key", "receipt_id": "R1", "tracking_id": "TRACK",
                                     "alt_tracking": "ALT", "quantity": quantity, "total": str(20 * quantity)})
        payload = {"version": 1, "requests": {
            "rows": {"value": encode([row])},
            "rewards": {"value": encode({"track": Decimal(reward)})},
            "detail:key": {"value": encode({"receipt_id": "R1", "received_items": [
                {"verified": True, "qty": quantity, "price": "20.00", "total": str(20 * quantity)}]})},
        }}
        self.db.add(ReceivingReceiptCache(buying_group_id=self.group.id,
            provider_identity=provider_identity(self.group), fetched_at=datetime.now(timezone.utc), payload=payload))
        self.db.commit()

    def scanned(self, quantity=3, reward="0.05"):
        self.item.quantity = quantity
        self.item.status = ItemStatus.SCANNED
        self.item.receipt_id = "R1"
        self.item.receipt_tracking_number = "TRACK"
        self.item.reward = Decimal(reward)
        self.db.commit()

    def test_quantity_does_not_multiply_reward(self):
        self.scanned()
        self.assertEqual(item_payable(self.item), Decimal("60.05"))
        self.group.api_framework = "parsefile"
        self.assertEqual(item_payable(self.item), Decimal("60.00"))

    def test_aliases_credit_one_receipt_and_keep_zero_distinct_from_unknown(self):
        self.assertEqual(tracking_key(" Tr- ack "), api.tracking_reward_key(" Tr- ack "))
        rows = [{"receipt_id": "R1", "trackings": ["TRACK"], "sort_date": "1"},
                {"receipt_id": "R2", "trackings": ["ALT"], "sort_date": "2"}]
        credits = tracking_credits(rows, {"track": Decimal("0.15"), "alt": Decimal("0.15")}, [["track", "alt"]])
        self.assertEqual(credits["R1"], credits["R2"])
        self.assertEqual(credits["R2"]["receipt_id"], "R1")
        self.assertEqual(tracking_credits(rows, {}), {})
        self.assertEqual(tracking_credits(rows, {"track": 0})["R1"]["amount"], "0")
        rows[0]["void"] = True
        self.assertEqual(tracking_credits(rows, {"track": 0}, [["track", "alt"]])["R2"]["receipt_id"], "R2")

    def test_receiving_credits_only_first_receipt(self):
        extra = api._parse_receipt_row({"receipt_id": "R2", "tracking_id": "TRACK", "quantity": 1, "total": "20.00"})
        matches = self.check(quantity=2, rewards={"track": Decimal("0.05")}, extra_receipts=[extra]).matches
        self.assertEqual({m.receipt_id: m.reward for m in matches}, {"R1": Decimal("0.05"), "R2": Decimal("0.00")})

    def test_partial_receiving_reserves_unmatched_cents(self):
        self.item.quantity = 1
        self.db.commit()
        result = self.check(quantity=3, total="60.00", rewards={"track": Decimal("0.05")})
        self.assertEqual(result.matches[0].reward, Decimal("0.02"))
        self.assertEqual(sum(Decimal(r["reward"]) for r in result.session_payload["unknown_receipts"]), Decimal("0.03"))
        self.assertEqual(sum(reward_share("0.05", n, 1, 3) for n in range(3)), Decimal("0.05"))

    def test_shared_orders_duplicate_shipments_and_account_scoping(self):
        self.check(rewards={"track": Decimal("0.05")})
        other_order = Order(store_id=self.item.order.store_id, buying_group_id=self.group.id)
        other = Item(order=other_order, quantity=1, price_sold=20,
            shipment_items=[ShipmentItem(shipment=Shipment(tracking_number=" T R A C K "))])
        self.db.add(other)
        self.db.commit()
        order_reward_summaries([self.item.order, other_order])
        first = OrderRead.model_validate(self.item.order)
        second = OrderRead.model_validate(other_order)
        self.assertEqual(first.tracking_rewards[0].amount, Decimal("0.05"))
        self.assertEqual(first.tracking_rewards[0].shared_order_count, 2)
        self.assertEqual(first.tracking_rewards[0].id, second.tracking_rewards[0].id)
        self.assertEqual(first.model_copy(update={"items": []}).tracking_rewards[0].amount, Decimal("0.05"))
        self.assertEqual(self.db.query(TrackingReward).count(), 1)
        self.group.api_username = "another-account"
        order_reward_summaries([self.item.order])
        self.assertEqual(self.item.order.tracking_rewards, [])

    def test_split_reserves_reward_for_unreceived_remainder(self):
        self.scanned()
        remainder = split_item_record(self.db, self.item, ItemSplitRequest(keep_quantity=2, copy_tracking=True))
        self.assertEqual(self.item.reward, Decimal("0.03"))
        self.assertIsNone(remainder.reward)
        self.assertIsNone(remainder.receipt_id)

    def test_later_alias_evidence_merges_previously_separate_tracking_records(self):
        first = {"tracking_number": "TRACK", "aliases": ["track"], "amount": "0.05", "receipt_id": "R1"}
        second = {"tracking_number": "ALT", "aliases": ["alt"], "amount": "0.05", "receipt_id": "R2"}
        save_credits(self.db, self.group, {"R1": first, "R2": second})
        self.assertEqual(self.db.query(TrackingReward).count(), 2)
        merged = {**first, "aliases": ["alt", "track"]}
        save_credits(self.db, self.group, {"R1": merged, "R2": merged})
        self.assertEqual(self.db.query(TrackingReward).count(), 1)
        self.assertEqual(self.db.query(TrackingReward).one().amount, Decimal("0.05"))

    def test_cached_receiving_cannot_overwrite_newer_payment_reward(self):
        credit = {"tracking_number": "TRACK", "aliases": ["track"], "amount": "0.15", "receipt_id": "R1"}
        now = datetime.now(timezone.utc)
        save_credits(self.db, self.group, {"R1": credit}, observed_at=now)
        old = {"R1": {**credit, "amount": "0.05"}}
        saved = save_credits(self.db, self.group, old, observed_at=now - timedelta(hours=1))
        self.assertEqual(saved_credit_values(old, saved)["R1"]["amount"], "0.15")
        self.assertEqual(self.db.query(TrackingReward).one().amount, Decimal("0.15"))

    def test_payment_fetch_persists_tracking_amount_and_repairs_line_share(self):
        self.cache()
        self.scanned(reward="0.15")
        credit = {"tracking_number": "TRACK", "aliases": ["track"], "amount": "0.05", "receipt_id": "R1"}
        receipt = PaymentSource(buying_group_id=self.group.id, provider="buyinggroup", kind="receipt", external_id="R1",
            revision="r1", payload={"details": [{"tracking_reward": credit}]})
        sync_payment_rewards(self.db, self.group, [receipt])
        self.assertEqual(self.item.reward, Decimal("0.05"))
        self.assertEqual(self.db.query(TrackingReward).one().amount, Decimal("0.05"))
        sync_payment_rewards(self.db, self.group, [])
        self.assertEqual(self.item.reward, Decimal("0.05"))

    def test_preview_apply_rerun_preserve_payments_and_invalidate_tokens(self):
        self.cache()
        self.scanned(reward="0.15")
        payment = Payment(buying_group_id=self.group.id, payment_requested_at=datetime.now(timezone.utc),
                          line_items=[PaymentLineItem(item_id=self.item.id, amount=Decimal("60.05"))])
        source = PaymentSource(buying_group_id=self.group.id, provider="buyinggroup", kind="payment", external_id="P1",
            revision="source-v1", payload={"amount": "60.05"})
        self.db.add_all([payment, source])
        self.db.flush()
        review = PaymentReview(source_id=source.id, payment_id=payment.id, content={"allocations": [{"item_id": self.item.id}]})
        self.db.add(review)
        self.db.commit()
        before = payment_snapshot(self.db)
        token = review_token(self.db, review, review.content)
        preview = repair(self.db)
        self.assertEqual(len(preview["groups"][0]["line_changes"]), 1)
        self.assertEqual(self.db.get(Item, self.item.id).reward, Decimal("0.15"))
        self.assertEqual(self.db.query(TrackingReward).count(), 0)
        repair(self.db, apply=True)
        self.assertEqual(self.db.get(Item, self.item.id).reward, Decimal("0.05"))
        self.assertNotEqual(review_token(self.db, review, review.content), token)
        self.assertEqual(payment_snapshot(self.db), before)
        rerun = repair(self.db)
        self.assertEqual(rerun["groups"][0]["line_changes"], [])
        self.assertEqual(rerun["groups"][0]["tracking_records_created"], 0)
        self.assertEqual(rerun["groups"][0]["tracking_records_updated"], 0)

    def test_account_mismatch_and_overassignment_are_reported(self):
        self.cache(quantity=1)
        self.scanned(quantity=3, reward="0.15")
        report = repair(self.db)
        self.assertTrue(any("over-assigned" in i["reason"] for i in report["groups"][0]["unresolved"]))
        self.group.api_username = "changed"
        self.db.commit()
        report = repair(self.db)
        self.assertEqual(report["groups"][0]["tracking_records_created"], 0)
        self.assertTrue(report["groups"][0]["unresolved"])

    def test_pending_reviews_refresh_but_manual_choices_are_preserved(self):
        from app.payment_evidence import PaymentEvidence, ReceiptEvidence, PaymentFetch
        from app.payment_review import refresh
        self.cache()
        self.scanned(reward="0.15")
        now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        refresh(self.db, self.group.id, PaymentFetch([
            PaymentEvidence(external_id=pid, amount="60.05", created_at=now, status="PAID", receipt_ids=["R1"])
            for pid in ("P1", "P2")], [ReceiptEvidence(external_id="R1", amount="60.05", quantity=3,
                created_at=now, tracking_numbers=["TRACK"], details=[{"cash_reward": "0.05"}])]), "buyinggroup")
        reviews = self.db.query(PaymentReview).order_by(PaymentReview.id).all()
        receipt = self.db.query(PaymentSource).filter_by(kind="receipt").one()
        manual = {**reviews[1].content, "manual": True, "note": "Keep my allocation", "allocations": [
            {"receipt_source_id": receipt.id, "item_id": self.item.id, "quantity": 3, "amount": "60.15"}]}
        reviews[1].content = manual
        self.db.commit()
        report = repair(self.db, apply=True)
        self.assertEqual(self.db.get(PaymentReview, reviews[1].id).content, manual)
        self.assertEqual(report["groups"][0]["reviews_updated"], [reviews[0].id])
        self.assertEqual(report["groups"][0]["manual_review_issues"][0]["review_id"], reviews[1].id)
        self.assertEqual(repair(self.db)["groups"][0]["reviews_updated"], [])
