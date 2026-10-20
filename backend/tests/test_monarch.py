"""Receipt matching and transactions against isolated test data (no live Monarch)."""
import asyncio
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (User, Store, Order, Item, BuyingGroup, Payment, PaymentLineItem, BrowserProfile,
                        MonarchConnection, MonarchDeposit, MonarchConfirmation, MonarchPaymentReceipt)
from app.monarch import (confirm_receipt, undo_receipt, suggest_matches, payment_fingerprint,
                        payment_choice, payment_amount, next_daily_check, upsert_deposits)
from app.routers import monarch
from app.routers.payments import update_payment, delete_payment, add_payment_line_item, remove_payment_line_item, update_payment_line_item
from app.schemas.payment import PaymentUpdate, PaymentLineItemCreate, PaymentLineItemUpdate, PaymentRead
from app.schemas.monarch import MonarchConfirmRequest, MonarchDismissRequest, MonarchSettingsUpdate, MonarchCheckRequest
from app.browser_automation.monarch import parse_accounts, parse_transactions, MonarchCaptureError, capture_monarch, _get_captured
from app.browser_automation.common import LoginRequiredError
from app.browser_automation.scheduler import list_due_profile_jobs


class MonarchTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        with self.engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.user = User(username="monarch-test", role="user")
        self.group = BuyingGroup(name="Sender")
        store = Store(name="Test store")
        self.db.add_all([self.user, self.group, store])
        self.db.flush()
        self.order = Order(store_id=store.id, buying_group_id=self.group.id)
        self.db.add(self.order)
        self.db.flush()
        self.item = Item(order_id=self.order.id, description="Test", price_sold=1000, quantity=1)
        profile = BrowserProfile(integration="monarch", retailer="monarch", status="ready")
        self.db.add_all([self.item, profile])
        self.db.flush()
        self.connection = MonarchConnection(id=1, browser_profile_id=profile.id, accounts=[{"id": "bank", "name": "Bank", "type": "depository"}],
            selected_account_ids=["bank"], sender_rules={str(self.group.id): ["sender"]})
        self.db.add(self.connection)
        self.deposit = MonarchDeposit(transaction_id="tx1", account_id="bank", account_name="Bank", amount=Decimal("30"),
            posted_date=date(2026, 10, 10), pending=False, merchant="Sender LLC", description="ACH Sender", revision=1)
        self.db.add(self.deposit)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def payment(self, amount="30", bonus="0", days=5, group_id=None):
        p = Payment(buying_group_id=group_id or self.group.id, payment_bonus=Decimal(bonus),
            payment_requested_at=datetime(2026, 10, 10, 12, tzinfo=timezone.utc) - timedelta(days=days))
        self.db.add(p)
        self.db.flush()
        self.db.add(PaymentLineItem(payment_id=p.id, item_id=self.item.id, amount=Decimal(amount)))
        self.db.commit()
        return p

    def selection(self, payments, revision=None):
        return MonarchConfirmRequest(revision=revision or self.deposit.revision,
            payments=[{"id": p.id, "fingerprint": payment_fingerprint(p)} for p in payments])

    def confirm(self, payments):
        return confirm_receipt(self.db, self.deposit.id, self.selection(payments), self.user.id)

    def test_exact_combined_ambiguous_and_bonus_matches(self):
        a, b, c = self.payment("10", "0.01"), self.payment("19.99"), self.payment()
        result = suggest_matches(self.deposit, [a, b, c], self.connection.sender_rules)
        self.assertEqual({tuple(r["payment_ids"]) for r in result["suggestions"]}, {(a.id, b.id), (c.id,)})
        self.assertEqual(payment_amount(a), Decimal("10.01"))
        self.assertFalse(result["search_truncated"])
        self.assertTrue(suggest_matches(self.deposit, [a, b, c], self.connection.sender_rules, search_limit=1)["search_truncated"])

    def test_date_boundaries_sent_fallback_keywords_and_group_boundaries(self):
        p0, p30, p31, future = [self.payment(days=n) for n in (0, 30, 31, -1)]
        ids = {r["payment_ids"][0] for r in suggest_matches(self.deposit, [p0, p30, p31, future], self.connection.sender_rules)["suggestions"]}
        self.assertEqual(ids, {p0.id, p30.id})
        p31.payment_sent_at = p0.payment_requested_at
        self.assertTrue(suggest_matches(self.deposit, [p31], self.connection.sender_rules)["suggestions"])
        self.assertFalse(suggest_matches(self.deposit, [p0], {})["suggestions"])
        other = BuyingGroup(name="Other"); self.db.add(other); self.db.commit()
        a, b = self.payment("10"), self.payment("20", group_id=other.id)
        self.assertFalse(suggest_matches(self.deposit, [a, b], {str(self.group.id): ["Sender"], str(other.id): ["Sender"]})["suggestions"])

    def test_confirmation_evidence_protections_and_undo(self):
        a, b = self.payment("10"), self.payment("20")
        result = self.confirm([a, b])
        self.db.expire_all()
        self.assertEqual(self.db.get(Payment, a.id).payment_received_at.date(), self.deposit.posted_date)
        confirmation = self.db.get(MonarchConfirmation, result["confirmation_id"])
        self.assertEqual(confirmation.reviewer_id, self.user.id)
        self.assertEqual(len(confirmation.evidence["payments"]), 2)
        self.assertEqual(PaymentRead.model_validate(a).monarch_receipt["transaction_id"], "tx1")
        for operation in (
            lambda: update_payment(a.id, PaymentUpdate(payment_bonus=5), db=self.db, _=self.user),
            lambda: update_payment(a.id, PaymentUpdate(payment_received_at=None), db=self.db, _=self.user),
            lambda: delete_payment(a.id, db=self.db, _=self.user),
            lambda: add_payment_line_item(a.id, PaymentLineItemCreate(item_id=self.item.id), db=self.db, _=self.user),
            lambda: remove_payment_line_item(a.id, a.line_items[0].id, db=self.db, _=self.user),
            lambda: update_payment_line_item(a.id, a.line_items[0].id, PaymentLineItemUpdate(amount=2), db=self.db, _=self.user),
        ):
            with self.assertRaises(HTTPException) as caught:
                operation()
            self.assertEqual(caught.exception.status_code, 409)
        undo_receipt(self.db, confirmation.id, self.user.id)
        self.db.expire_all()
        self.assertIsNone(self.db.get(Payment, a.id).payment_received_at)
        self.assertEqual(self.db.query(MonarchPaymentReceipt).count(), 0)
        self.assertIsNotNone(self.db.get(MonarchConfirmation, confirmation.id).undone_at)
        self.assertIsNotNone(self.confirm([a, b])["confirmation_id"])

    def test_manual_selection_crosses_groups_and_date_window(self):
        other = BuyingGroup(name="Other"); self.db.add(other); self.db.commit()
        a, b = self.payment("10", days=60), self.payment("20", group_id=other.id)
        self.confirm([a, b])

    def test_rejects_pending_unselected_dismissed_and_inexact(self):
        p = self.payment()
        for attr, bad, original in [("pending", True, False), ("dismissed", True, False), ("amount", Decimal("31"), Decimal("30"))]:
            setattr(self.deposit, attr, bad); self.db.commit()
            with self.assertRaises(HTTPException): self.confirm([p])
            setattr(self.deposit, attr, original); self.db.commit()
        self.connection.selected_account_ids = []; self.db.commit()
        with self.assertRaises(HTTPException): self.confirm([p])

    def test_already_received_payment_can_be_reviewed_linked_and_undone(self):
        p = self.payment()
        p.payment_received_at = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
        self.db.commit(); self.db.refresh(p)
        original = p.payment_received_at
        review = monarch.review(db=self.db, _=self.user)
        choice = next(row for row in review["payments"] if row["id"] == p.id)
        self.assertEqual(choice["received_at"], original.isoformat())
        self.assertIn([p.id], [s["payment_ids"] for s in review["deposits"][0]["suggestions"]])
        result = self.confirm([p])
        self.db.expire_all()
        self.assertEqual(self.db.get(Payment, p.id).payment_received_at.date(), self.deposit.posted_date)
        self.assertIsNotNone(self.db.get(MonarchPaymentReceipt, p.id))
        self.assertNotIn(p.id, [row["id"] for row in monarch.review(db=self.db, _=self.user)["payments"]])
        with self.assertRaises(HTTPException): self.confirm([p])
        undo_receipt(self.db, result["confirmation_id"], self.user.id)
        self.db.expire_all()
        self.assertEqual(self.db.get(Payment, p.id).payment_received_at, original)
        self.assertIn(p.id, [row["id"] for row in monarch.review(db=self.db, _=self.user)["payments"]])

    def test_combined_received_and_unreceived_payments_restore_their_own_dates(self):
        paid, unpaid = self.payment("10"), self.payment("20")
        paid.payment_received_at = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
        self.db.commit(); self.db.refresh(paid)
        original = paid.payment_received_at
        result = self.confirm([paid, unpaid])
        self.db.expire_all()
        self.assertEqual(self.db.get(Payment, paid.id).payment_received_at.date(), self.deposit.posted_date)
        self.assertEqual(self.db.get(Payment, unpaid.id).payment_received_at.date(), self.deposit.posted_date)
        undo_receipt(self.db, result["confirmation_id"], self.user.id)
        self.db.expire_all()
        self.assertEqual(self.db.get(Payment, paid.id).payment_received_at, original)
        self.assertIsNone(self.db.get(Payment, unpaid.id).payment_received_at)

    def test_received_date_change_after_preview_is_still_rejected(self):
        p = self.payment()
        p.payment_received_at = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
        self.db.commit()
        selection = self.selection([p])
        p.payment_received_at = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
        self.db.commit()
        with self.assertRaises(HTTPException):
            confirm_receipt(self.db, self.deposit.id, selection, self.user.id)
        self.assertEqual(self.db.query(MonarchPaymentReceipt).count(), 0)

    def test_undo_legacy_confirmation_without_prior_received_dates(self):
        p = self.payment()
        result = self.confirm([p])
        confirmation = self.db.get(MonarchConfirmation, result["confirmation_id"])
        confirmation.evidence = {**confirmation.evidence, "payments": [
            {k: v for k, v in row.items() if k != "received_at"} for row in confirmation.evidence["payments"]]}
        self.db.commit()
        undo_receipt(self.db, confirmation.id, self.user.id)
        self.db.expire_all()
        self.assertIsNone(self.db.get(Payment, p.id).payment_received_at)

    def test_stale_review_and_duplicate_use_rejected(self):
        p = self.payment()
        stale = self.selection([p])
        p.line_items[0].amount = 29; self.db.commit()
        with self.assertRaises(HTTPException): confirm_receipt(self.db, self.deposit.id, stale, self.user.id)
        p.line_items[0].amount = 30; self.db.commit()
        self.confirm([p])
        with self.assertRaises(HTTPException): self.confirm([p])
        self.assertEqual(self.db.query(MonarchConfirmation).count(), 1)

    def test_upserts_preserve_dismissal_invalidate_review_and_keep_audit(self):
        self.deposit.dismissed = True; self.db.commit()
        row = {key: getattr(self.deposit, key) for key in ("transaction_id", "account_id", "account_name", "posted_date", "amount", "merchant", "description", "pending")}
        for _ in range(2): upsert_deposits(self.db, [row], date(2026, 1, 1), date(2026, 12, 31), ["bank"]); self.db.commit()
        self.assertEqual(self.db.query(MonarchDeposit).count(), 1)
        self.assertTrue(self.deposit.dismissed)
        self.assertEqual(self.deposit.revision, 1)
        row["amount"] = Decimal("31")
        upsert_deposits(self.db, [row], date(2026, 1, 1), date(2026, 12, 31), ["bank"]); self.db.commit()
        self.assertEqual(self.deposit.revision, 2)
        upsert_deposits(self.db, [], date(2026, 1, 1), date(2026, 12, 31), ["bank"]); self.db.commit()
        self.assertTrue(self.deposit.pending)

    def test_review_dismiss_restore_and_daily_configuration(self):
        self.payment()
        self.assertEqual(len(monarch.review(db=self.db, _=self.user)["deposits"][0]["suggestions"]), 1)
        monarch.dismiss(self.deposit.id, MonarchDismissRequest(dismissed=True), db=self.db, _=self.user)
        self.assertFalse(monarch.review(db=self.db, _=self.user)["deposits"][0]["eligible"])
        monarch.dismiss(self.deposit.id, MonarchDismissRequest(dismissed=False), db=self.db, _=self.user)
        value = monarch.update_settings(MonarchSettingsUpdate(selected_account_ids=["bank"], sender_rules={str(self.group.id): [" sender "]}, daily_enabled=True), db=self.db, _=self.user)
        self.assertIsNotNone(value["next_check_at"])
        self.connection.profile.full_check_enabled = True; self.db.commit()
        self.assertEqual(list_due_profile_jobs(self.db), [])
        self.assertEqual(next_daily_check(datetime(2026, 11, 1, 14, tzinfo=timezone.utc)).hour, 16)  # DST ended
        self.assertEqual(next_daily_check(datetime(2026, 10, 1, 14, tzinfo=timezone.utc)).hour, 15)

    def test_review_defaults_to_keyword_matches_with_show_all_override(self):
        # Matching is independent of payment availability or exact amount matches.
        self.deposit.merchant = "Unrelated merchant"
        self.deposit.description = "ACH sEnDeR transfer"
        unrelated = MonarchDeposit(transaction_id="unrelated", account_id="bank", account_name="Bank",
            amount=Decimal("25"), posted_date=self.deposit.posted_date, pending=False,
            merchant="Other deposit", description="Personal transfer", revision=1)
        self.connection.sender_rules = {str(self.group.id): ["", "   ", " sender "]}
        self.db.add(unrelated)
        self.db.commit()
        result = monarch.review(db=self.db, _=self.user)
        self.assertEqual([row["id"] for row in result["deposits"]], [self.deposit.id])
        self.assertEqual(result["deposits"][0]["suggestions"], [])
        self.assertEqual({row["id"] for row in monarch.review(show_all=True, db=self.db, _=self.user)["deposits"]},
                         {self.deposit.id, unrelated.id})
        self.connection.sender_rules = {str(self.group.id): ["", "   "]}
        self.db.commit()
        self.assertEqual(monarch.review(db=self.db, _=self.user)["deposits"], [])
        self.assertEqual(len(monarch.review(show_all=True, db=self.db, _=self.user)["deposits"]), 2)

    def test_owner_constraints_and_singleton(self):
        for attrs in ({"retailer": "amazon"}, {"retailer": "monarch", "integration": "monarch", "store_id": self.order.store_id}, {"retailer": "monarch", "store_id": self.order.store_id}):
            self.db.add(BrowserProfile(**attrs))
            with self.assertRaises(IntegrityError): self.db.commit()
            self.db.rollback()
        self.assertEqual(monarch.connect(db=self.db, _=self.user)["profile"].id, self.connection.browser_profile_id)

    def test_failed_browser_check_does_not_apply_partial_data_and_pauses_schedule(self):
        from app.browser_automation.jobs import create_job, get_job
        self.connection.daily_enabled = True
        self.connection.next_check_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.connection.profile.status = "importing"
        self.db.commit()
        profile_id = self.connection.browser_profile_id
        job = create_job(profile_id, "monarch_check")
        with patch.object(self.db, "close"), patch.object(monarch, "SessionLocal", return_value=self.db), \
                patch.object(monarch.session_manager, "ensure_session", AsyncMock(return_value=SimpleNamespace(page=object()))), \
                patch.object(monarch.session_manager, "close_session", AsyncMock()), \
                patch.object(monarch, "capture_monarch", AsyncMock(side_effect=LoginRequiredError("expired"))):
            asyncio.run(monarch.run_check(profile_id, job.id, date(2026, 1, 1), date(2026, 12, 31)))
        self.assertEqual(get_job(job.id).status, "failed")
        self.assertEqual(self.db.get(BrowserProfile, profile_id).status, "login_required")
        self.assertEqual(self.db.query(MonarchDeposit).count(), 1)
        self.assertIsNone(self.db.get(MonarchConnection, 1).last_check_at)
        with patch.object(monarch, "SessionLocal", return_value=self.db), patch.object(self.db, "close"), patch.object(monarch, "queue_check") as queued:
            asyncio.run(monarch.scheduled_tick())
            queued.assert_not_called()

    def test_overlapping_checks_and_login_are_rejected(self):
        self.connection.profile.status = "login_in_progress"; self.db.commit()
        with self.assertRaises(HTTPException) as caught:
            monarch.queue_check(self.db, MonarchCheckRequest())
        self.assertEqual(caught.exception.status_code, 409)
        self.connection.profile.status = "importing"; self.db.commit()
        with self.assertRaises(HTTPException) as caught:
            monarch.queue_check(self.db, MonarchCheckRequest())
        self.assertEqual(caught.exception.status_code, 409)

    def test_settings_hide_ineligible_accounts_and_reject_selecting_them(self):
        self.connection.accounts = [*self.connection.accounts, {"id": "card", "name": "Cash rewards", "type": "credit"}]
        self.connection.selected_account_ids = ["bank", "card"]
        self.db.commit()
        value = monarch.get_settings(db=self.db, _=self.user)
        self.assertEqual([row["id"] for row in value["accounts"]], ["bank"])
        self.assertEqual(value["selected_account_ids"], ["bank"])
        with self.assertRaises(HTTPException) as caught:
            monarch.update_settings(MonarchSettingsUpdate(selected_account_ids=["card"], sender_rules={}), db=self.db, _=self.user)
        self.assertEqual(caught.exception.status_code, 400)


class MonarchAdapterTests(unittest.TestCase):
    def payload(self, **changes):
        row = {"id": "tx", "account": {"id": "bank", "displayName": "Bank"}, "amount": "10.01", "date": "2026-10-10", "pending": False,
               "merchant": {"name": "Sender"}, "plaidName": "Original sender"}
        row.update(changes)
        return {"data": {"allTransactions": {"totalCount": 1, "results": [row]}}}

    def test_response_contract_and_decimal_precision(self):
        total, rows = parse_transactions(self.payload())
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["amount"], Decimal("10.01"))
        self.assertFalse(rows[0]["pending"])
        self.assertEqual(parse_accounts({"data": {"accounts": [{"id": "a", "displayName": "Bank", "type": {"name": "depository"}}]}}), [{"id": "a", "name": "Bank", "type": "depository"}])

    def test_accounts_include_cash_and_investments_only(self):
        types = ("depository", "brokerage", "credit", "vehicle", "loan", "real_estate", "other_asset")
        accounts = [{"id": kind, "displayName": "Checking", "type": {"name": kind}} for kind in types]
        result = parse_accounts({"data": {"accounts": accounts}})
        self.assertEqual([account["id"] for account in result], ["depository", "brokerage"])
        with self.assertRaises(MonarchCaptureError):
            parse_accounts({"data": {"accounts": [{"id": "missing", "displayName": "Bank"}]}})

    def test_malformed_pending_identifiers_amount_dates_and_errors(self):
        for changes in ({"pending": None}, {"pending": "false"}, {"id": None}, {"date": "bad"}, {"amount": "NaN"}, {"amount": "1.001"}, {"account": {}}, {"merchant": None}, {"plaidName": None}):
            with self.subTest(changes=changes), self.assertRaises(MonarchCaptureError): parse_transactions(self.payload(**changes))
        for payload in ({}, {"errors": [{"message": "private"}]}, {"data": {"allTransactions": {"results": []}}}):
            with self.assertRaises(MonarchCaptureError): parse_transactions(payload)


class CapturePage:
    """Recorded-contract page fixture; no bank data or external network calls."""
    def __init__(self, batches, total, *, pager=True, infinite=False):
        self.url = "https://app.monarch.com"
        self.batches, self.total, self.index, self.pager = batches, total, 0, pager
        self.handler = None
        self.filters = []
        self.infinite = infinite
        self.accounts = [{"id": "bank", "displayName": "Bank", "type": {"name": "depository"}}]

    def on(self, event, handler): self.handler = handler
    def remove_listener(self, event, handler): self.handler = None
    async def route(self, pattern, handler): self.route_handler = handler
    async def unroute(self, pattern, handler): pass

    async def emit(self, payload, query, offset=0):
        variables = {"filters": {}}
        if offset:
            variables["offset"] = offset
        request = SimpleNamespace(post_data_json={"query": query, "variables": variables})
        async def continued(**kwargs):
            import json
            if kwargs: self.filters.append(json.loads(kwargs["post_data"])["variables"]["filters"])
        await self.route_handler(SimpleNamespace(request=request, continue_=continued, abort=AsyncMock()))
        self.handler(SimpleNamespace(url="https://api.monarch.com/graphql", status=200, request=request, json=AsyncMock(return_value=payload)))

    async def goto(self, url, **kwargs):
        self.url = url
        if url.endswith("/accounts"):
            await self.emit({"data": {"accounts": self.accounts}}, "query { accounts { id displayName type { name } } }")
        else:
            await self.emit({"data": {"allTransactions": {"totalCount": self.total}}}, "query Web_GetTransactionsSummaryCard($filters: TransactionFilterInput) { allTransactions(filters: $filters) { totalCount } }")
            await self.emit_batch()

    async def emit_batch(self):
        payload = {"data": {"allTransactions": {"totalCount": self.total, "results": self.batches[self.index]}}}
        await self.emit(payload, "query GetTransactionsList($offset: Int, $filters: TransactionFilterInput) { allTransactions(filters: $filters) { totalCount results(offset: $offset) { id } } }", sum(len(batch) for batch in self.batches[:self.index]))

    def get_by_role(self, *args, **kwargs): return self
    async def count(self): return int(self.pager)
    async def is_enabled(self): return self.pager
    async def click(self):
        self.index += 1
        await self.emit_batch()

    async def evaluate(self, script):
        if not self.infinite:
            return False
        self.index += 1
        await self.emit_batch()
        return True


class MonarchBrowserCaptureTests(unittest.IsolatedAsyncioTestCase):
    def row(self, tx="one", account="bank"):
        return {"id": tx, "account": {"id": account, "displayName": "Bank"}, "amount": "30", "pending": False,
                "date": "2026-10-10", "merchant": {"name": "Sender"}, "plaidName": "ACH Sender"}

    async def test_pagination_and_account_date_filtering(self):
        page = CapturePage([[self.row()], [self.row("two")]], 2)
        accounts, rows = await capture_monarch(page, date(2026, 10, 1), date(2026, 10, 19), ["bank"])
        self.assertEqual([row["transaction_id"] for row in rows], ["one", "two"])
        self.assertEqual(accounts, [{"id": "bank", "name": "Bank", "type": "depository"}])
        self.assertEqual(page.filters, [{"startDate": "2026-10-01", "endDate": "2026-10-19", "accounts": ["bank"]}] * 2)
        self.assertIsNone(page.handler)

    async def test_incomplete_duplicate_or_changed_pagination_never_succeeds(self):
        for page in (CapturePage([[self.row()]], 2, pager=False), CapturePage([[self.row()], [self.row()]], 2), CapturePage([[self.row()]], 0)):
            with self.assertRaises(MonarchCaptureError):
                await capture_monarch(page, date(2026, 10, 1), date(2026, 10, 19), ["bank"])
            self.assertIsNone(page.handler)

    async def test_account_discovery_and_missing_selected_account(self):
        page = CapturePage([], 0)
        accounts, rows = await capture_monarch(page, date.today(), date.today(), [])
        self.assertEqual(rows, [])
        self.assertEqual(len(accounts), 1)
        with self.assertRaises(MonarchCaptureError):
            await capture_monarch(page, date.today(), date.today(), ["other-bank"])

    async def test_live_contract_infinite_scroll_and_omitted_initial_offset(self):
        page = CapturePage([[self.row()], [self.row("two")], [self.row("three")]], 3, pager=False, infinite=True)
        _, rows = await capture_monarch(page, date(2026, 10, 1), date(2026, 10, 19), ["bank"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(page.index, 2)

    async def test_previously_selected_credit_accounts_are_excluded_from_checks(self):
        page = CapturePage([[self.row()]], 1)
        page.accounts.append({"id": "card", "displayName": "Cash rewards", "type": {"name": "credit"}})
        accounts, _ = await capture_monarch(page, date(2026, 10, 1), date(2026, 10, 19), ["bank", "card"])
        self.assertEqual([account["id"] for account in accounts], ["bank"])
        self.assertEqual(page.filters[0]["accounts"], ["bank"])

    async def test_expired_login_is_distinguished_from_format_failure(self):
        page = SimpleNamespace(url="https://app.monarch.com/login")
        with self.assertRaises(LoginRequiredError): await _get_captured(asyncio.Queue(), page, timeout=0.001)
        page.url = "https://app.monarch.com/transactions"
        with self.assertRaises(MonarchCaptureError): await _get_captured(asyncio.Queue(), page, timeout=0.001)


if __name__ == "__main__":
    unittest.main()
