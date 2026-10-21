"""Sanitized portal fixtures for cash history, installments, and delayed batches."""
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from app.group_payment_providers import (HistoryPages, amount, date, normalize_buyinggroup,
    normalize_usabg, normalize_dealandrunner, BuyinggroupPaymentProvider, UsabgPaymentProvider, unique_receipt_subset)
from app.utils.buying_group_apis import buyinggroup as bg, usabg


def bg_receipt(rid, total="100", tracking=None, created="01-01-2026 12:00:00", **kw):
    return dict(receipt_id=rid, total=total, quantity=1, tracking={"tracking_id": tracking or rid},
                created_dt=created, verified=True, void=False, **kw)


def bg_activity(ref, value, created="01-01-2026 12:00:00", detail="New Receipt", **kw):
    return dict(reference_id=ref, type="Receipt", details=detail, amount=value, created_dt=created, **kw)


def bg_payment(pid, value, created="01-02-2026 12:00:00", **kw):
    return dict(payment_id=pid, amount=value, status="REQUESTED", created_dt=created, void=False, **kw)


def dnr_credit(wh, value, created="2026-01-01T12:00:00Z"):
    return dict(entryTypeId=1, referenceWarehouseItemId=wh, systemTransactionId="credit-"+wh,
                amount=value, trackingNumbers=["track-"+wh], timestamp=created, productName="Example")


def dnr_debit(wh, value, created):
    return dict(entryTypeId=2, referenceWarehouseItemId=wh, systemTransactionId="debit-"+wh,
                amount="-"+value, timestamp=created)


def dnr_payment(pid, value, scheduled="2026-01-02T12:00:00Z", lock="2026-01-03T12:00:00Z", **kw):
    return dict(disbursementOrderId=pid, disbursementTotal=value, dateScheduled=scheduled, dateUpdated=lock,
                disbursementOrderStatusId=3, disbursementOrderItemStatusId=2, **kw)


class GroupProviderTests(unittest.TestCase):
    def test_money_dates_and_invalid_fields(self):
        self.assertEqual(amount("$1,234.50"), Decimal("1234.50"))
        self.assertEqual(date("01/02/2026 4:26pm").hour, 16)
        self.assertEqual(date("2026-01-02T16:00:00-05:00").hour, 21)
        for value in ["NaN", "Infinity", "1.001", "---"]:
            with self.assertRaises(ValueError): amount(value)
        with self.assertRaises(ValueError): date("unknown date")

    def test_pagination_rejects_duplicates_changed_count_and_empty_page(self):
        pages = HistoryPages()
        self.assertFalse(pages.add([{"id": "r1"}], 2, lambda r:r["id"]))
        for rows, count in [([{"id":"r1"}], 2), ([{"id":"r2"}], 3), ([], 2)]:
            with self.assertRaises(ValueError): pages.add(rows, count, lambda r:r["id"])
        self.assertTrue(pages.add([{"id":"r2"}], 2, lambda r:r["id"]))

    def test_bg_carries_held_receipt_across_maximum_requests(self):
        result = normalize_buyinggroup([bg_payment("p1", "40"), bg_payment("p2", "60", "01-03-2026 12:00:00")],
            [bg_activity("held", "60"), bg_activity("ready", "40")],
            [bg_receipt("held", "60"), bg_receipt("ready", "40")], [])
        self.assertEqual(result.payments[0].receipt_amounts, {"ready": Decimal("40")})
        self.assertEqual(result.payments[1].receipt_amounts, {"held": Decimal("60")})

    def test_bg_cash_reward_once_per_tracking_and_points_excluded(self):
        result = normalize_buyinggroup([bg_payment("p", "205")],
            [bg_activity("r1", "105"), bg_activity("r2", "100"),
             dict(type="Bg_Points", details="New Receipt Bg Points Reward", amount="900", bg_points=900)],
            [bg_receipt("r1", tracking="tracking"), bg_receipt("r2", tracking="alias")],
            [dict(tracking_id="tracking", alt_tracking_id="alias", reward="5")])
        # Both aliases are reserved when assigning the one reward.
        self.assertEqual(sum(r.amount for r in result.receipts), Decimal("205"))
        self.assertEqual(sum(result.payments[0].receipt_amounts.values()), Decimal("205"))

    def test_bg_processing_date_is_not_receipt_boundary(self):
        payment = bg_payment("p", "100"); payment.update(status="PAID", paid_dt="01-05-2026 12:00:00", processed_dt="01-04-2026 12:00:00")
        result = normalize_buyinggroup([payment], [bg_activity("r1", "100"),
            bg_activity("r2", "200", "01-03-2026 12:00:00"),
            dict(type="Payment", details="Processed Payment", reference_id="p", amount="-100")],
            [bg_receipt("r1"), bg_receipt("r2", "200", created="01-03-2026 12:00:00")], [])
        self.assertEqual(result.payments[0].receipt_ids, ["r1"])
        self.assertEqual(result.payments[0].sent_at, date(payment["paid_dt"]))

    def test_bg_ambiguous_equal_receipts_require_review(self):
        self.assertIsNone(unique_receipt_subset({"a":Decimal(50), "b":Decimal(50)}, Decimal(50)))
        result = normalize_buyinggroup([bg_payment("p", "100")], [bg_activity("a", "100"), bg_activity("b", "100")],
            [bg_receipt("a"), bg_receipt("b")], [])
        self.assertIsNone(result.payments[0].receipt_amounts)
        self.assertTrue(result.payments[0].warnings)

    def test_bg_revision_is_not_a_second_receipt_credit(self):
        result = normalize_buyinggroup([bg_payment("p", "100")], [bg_activity("r", "100"), bg_activity("r", "120", detail="Revised Receipt")], [bg_receipt("r", "120")], [])
        self.assertEqual(result.payments[0].receipt_amounts, {"r":Decimal(100)})
        self.assertEqual(result.receipts[0].amount, Decimal(120))

    def test_bg_disagreement_with_processed_amount_fails_closed(self):
        with self.assertRaises(ValueError):
            normalize_buyinggroup([bg_payment("p", "100")],
                [dict(type="Payment", details="Processed Payment", reference_id="p", amount="-99")], [], [])

    def test_bg_fetch_reauthenticates_on_same_page(self):
        group = SimpleNamespace(base_url="https://example.test", api_username="user", api_password="password")
        empty = (200, "", {"status":"SUCCESS", "payload":{"count":0, "payments":[], "activities":[], "receipts":[], "orders":[]}})
        with (patch.object(bg,"_ensure_tokens",return_value=bg._AuthTokens("old")),
              patch.object(bg,"_reauth_tokens",return_value=bg._AuthTokens("new")) as reauth,
              patch.object(bg,"_multipart_request",side_effect=[bg._SessionExpired("expired"),empty,empty,empty,empty]) as req):
            self.assertEqual(BuyinggroupPaymentProvider().fetch(group).payments, [])
            reauth.assert_called_once()
            self.assertEqual(dict(req.call_args_list[0].kwargs["fields"])["page"], dict(req.call_args_list[1].kwargs["fields"])["page"])

    def test_usabg_split_po_and_signed_shares(self):
        line = usabg.UsabgReceivedRow(po_number="PO", purchase_id=None, tracking_number="track", quantity=1,
            unit_price=Decimal(100), ext_total=Decimal(100), received_status="Paid", receiving_status="Received",
            scanned_at="01-01-2026 12:00:00", received_item="Example", sku=None)
        def payment(pid,value):return dict(id=pid,value=value,added_date="01/02/2026 4:26pm",status={"name":"Approved"},
            check={"amount":value,"date_insert":"2026-01-03 12:00:00"},
            transactions=[{"value":"-"+value,"reference_id":"PO-ref","cancelled":False,"business_order":{"po_number":"PO"}}])
        result = normalize_usabg([payment("p1","40"),payment("p2","60")], {"PO":[line]})
        self.assertEqual([p.receipt_amounts for p in result.payments],[{"PO":Decimal(40)},{"PO":Decimal(60)}])
        self.assertEqual(result.receipts[0].amount, Decimal(100))
        self.assertIsNotNone(result.payments[0].sent_at)
        pending=payment("pending","40");pending.update(status={"name":"Pending"},check=None)
        self.assertIsNone(normalize_usabg([pending],{"PO":[line]}).payments[0].sent_at)
        bad=payment("bad","40");bad["transactions"][0]["value"]="40"
        with self.assertRaises(ValueError):normalize_usabg([bad],{"PO":[line]})

    def test_usabg_boolean_and_integer_envelopes_relogin_without_skipping_pages(self):
        group = SimpleNamespace(base_url="https://example.test", api_username="user", api_password="password")
        empty = lambda status: (200, {"status":status,"data":[],"totals":{"items":0}}, "")
        with (patch.object(usabg,"_ensure_token",return_value="old"),
              patch.object(usabg,"_rellogin_token",return_value="new") as reauth,
              patch.object(usabg,"_authorized_get_json",side_effect=[usabg._SessionExpired("expired"),empty(True),empty(200)]) as req):
            self.assertEqual(UsabgPaymentProvider().fetch(group).payments, [])
            reauth.assert_called_once()
            self.assertEqual(req.call_args_list[0].kwargs["url"], req.call_args_list[1].kwargs["url"])

    def test_dnr_delayed_two_batches_use_latest_posted_date(self):
        txs=[dnr_credit("a","100"),dnr_credit("b","50"),dnr_credit("c","70"),
             dnr_debit("a","100","2026-01-11T10:00:00Z"),dnr_debit("b","50","2026-01-11T10:01:00Z"),
             dnr_debit("c","70","2026-01-06T11:00:00Z")]
        ps=[dnr_payment("delayed","150"),dnr_payment("regular","70",scheduled="2026-01-05T12:00:00Z",lock="2026-01-06T12:00:00Z")]
        for batch,item in [(1,1),(2,1),(3,3),(3,4)]:
            p=dnr_payment("exclude-"+str(batch)+str(item),"100");p.update(disbursementOrderStatusId=batch,disbursementOrderItemStatusId=item);ps.append(p)
        result=normalize_dealandrunner(ps,txs)
        self.assertEqual(len(result.payments),2)
        delayed=result.payments[0]
        self.assertEqual(delayed.receipt_amounts,{"credit-a":Decimal(100),"credit-b":Decimal(50)})
        self.assertEqual(delayed.created_at,date(ps[0]["dateScheduled"]))
        self.assertEqual(delayed.sent_at,date("2026-01-11T10:01:00Z"))

    def test_dnr_ambiguous_batches_do_not_guess_sent_date(self):
        txs=[dnr_credit("a","50"),dnr_credit("b","50"),dnr_debit("a","50","2026-01-03T10:00:00Z"),dnr_debit("b","50","2026-01-03T11:00:00Z")]
        result=normalize_dealandrunner([dnr_payment("p1","50"),dnr_payment("p2","50")],txs)
        self.assertTrue(all(p.sent_at is None and not p.receipt_ids and p.warnings for p in result.payments))

    def test_dnr_mismatched_or_repeated_debit_fails(self):
        for debits in [[dnr_debit("a","99","2026-01-03T10:00:00Z")],
                       [dnr_debit("a","100","2026-01-03T10:00:00Z")]*2]:
            with self.assertRaises(ValueError):normalize_dealandrunner([dnr_payment("p","100")],[dnr_credit("a","100"),*debits])
