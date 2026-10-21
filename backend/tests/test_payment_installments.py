"""Installments share physical units, but never share the same money twice."""
import unittest
from datetime import timedelta
from decimal import Decimal

from fastapi import HTTPException
from app import payment_review as service
from app.models import Payment, PaymentCoverage
from tests import test_payment_review as fixtures

payment, receipt = fixtures.payment, fixtures.receipt


class PaymentInstallmentTests(unittest.TestCase):
    setUp = fixtures.PaymentReviewTests.setUp
    tearDown = fixtures.PaymentReviewTests.tearDown
    local = fixtures.PaymentReviewTests.local
    load = fixtures.PaymentReviewTests.load
    confirm = fixtures.PaymentReviewTests.confirm
    edit = fixtures.PaymentReviewTests.edit

    def payments(self, second="60"):
        return [payment("p1",amount="40",day=2,receipt_ids=["r1"],receipt_amounts={"r1":"40"}),
                payment("p2",amount=second,day=3,receipt_ids=["r1"],receipt_amounts={"r1":"60"})]

    def test_two_installments_count_quantity_once_and_are_idempotent(self):
        reviews=self.load(self.payments())["reviews"]
        self.assertEqual(reviews[0]["issues"],[])
        self.assertEqual(reviews[0]["content"]["allocations"][0]["amount"],"40.00")
        view=self.confirm(reviews[0])
        self.assertEqual(view["receipts"][0]["remaining_amount"],"60.00")
        self.assertEqual(view["corrections"],[])
        second=view["reviews"][1]
        self.assertEqual(second["issues"],[])
        final=self.confirm(second)
        self.assertTrue(final["receipts"][0]["covered"])
        self.assertEqual(self.db.query(PaymentCoverage).count(),2)
        self.assertEqual(sum(p.line_items[0].amount for p in self.db.query(Payment)),Decimal(100))
        self.load(self.payments())
        self.assertEqual(self.db.query(Payment).count(),2)

    def test_preview_of_other_installment_becomes_stale(self):
        first,second=self.load(self.payments())["reviews"]
        self.confirm(first)
        with self.assertRaises(HTTPException) as error:self.confirm(second)
        self.assertEqual(error.exception.status_code,409)

    def test_excess_installment_amount_is_blocked(self):
        ps=self.payments();ps[1].amount=Decimal(70);ps[1].receipt_amounts={"r1":Decimal(70)}
        view=self.load(ps)
        after=self.confirm(view["reviews"][0])
        self.assertTrue(any("remaining" in issue for issue in after["reviews"][1]["issues"]))

    def test_increase_is_correction_and_unpaid_baseline_is_not(self):
        first=self.load(self.payments())["reviews"][0];self.confirm(first)
        self.item.price_sold=120;self.db.commit()
        view=self.load(self.payments("80"),[receipt(amount="120")])
        self.assertEqual(view["receipts"][0]["remaining_amount"],"60.00")
        self.assertEqual(view["corrections"][0]["amount"],"20.00")
        self.assertEqual(view["reviews"][1]["issues"],[])
        self.confirm(view["reviews"][1])
        self.assertEqual(service.outstanding_corrections(self.db,self.group.id),[])

    def test_decrease_offsets_remaining_installment(self):
        self.confirm(self.load(self.payments())["reviews"][0])
        self.item.price_sold=80;self.db.commit()
        view=self.load(self.payments("40"),[receipt(amount="80")])
        self.assertEqual(view["corrections"][0]["amount"],"-20.00")
        self.assertEqual(view["reviews"][1]["issues"],[])
        self.confirm(view["reviews"][1])

    def test_installment_cannot_reassign_physical_units(self):
        self.confirm(self.load(self.payments())["reviews"][0])
        other=self.local("r1","100");self.db.commit()
        second=service.group_view(self.db,self.group.id)["reviews"][1]
        content={**second["content"],"allocations":[{"receipt_source_id":second["content"]["receipt_ids"][0],"item_id":other.id,"quantity":1,"amount":"60"}]}
        changed=self.edit(second,**content)["reviews"][1]
        self.assertTrue(any("earlier installment" in issue for issue in changed["issues"]))

    def test_bg_reward_included_in_payable_but_not_twice(self):
        self.group.api_framework="buyinggroup";self.item.reward=Decimal("5");self.db.commit()
        self.assertEqual(service.item_payable(self.item),Decimal(105))
        self.group.api_framework="parsefile";self.db.commit()
        self.assertEqual(service.item_payable(self.item),Decimal(100))

    def test_void_partial_receipt_reverses_only_money_already_paid(self):
        self.confirm(self.load(self.payments())["reviews"][0])
        view=self.load(self.payments(),[receipt(void=True)])
        self.assertEqual(view["corrections"][0]["amount"],"-40.00")
        self.assertEqual(view["receipts"][0]["remaining_amount"],"0.00")
