"""Profile ownership and import regressions without live retailer requests."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import BrowserProfile, BrowserImportLog, Store, StoreAccount, User, Order, Item
from app.models.item import ItemStatus
from app.routers import browser_profiles as profiles
from app.routers.stores import delete_store, delete_store_account
from app.routers.store_imports import apply_store_order_payload, get_bulk_import_session, _bulk_sessions, _bulk_session_owners
from app.browser_automation.common import post_bulk_session
from app.browser_automation.jobs import create_job, get_job
from app.schemas.browser_profile import BrowserProfileCreate, BrowserProfileScheduleUpdate
from app.schemas.store_import import StoreOrderImportPayload


class BrowserProfileOwnerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        with self.engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA foreign_keys=ON')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.user = User(username='profile-test', role='user')
        self.store = Store(name='Custom retailer name')
        self.other = Store(name='Other store')
        self.db.add_all([self.user, self.store, self.other])
        self.db.flush()
        self.account = StoreAccount(store_id=self.store.id, name='Subaccount')
        self.db.add(self.account)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def create(self, **owner):
        return profiles.create_browser_profile(BrowserProfileCreate(retailer='walmart', **owner), db=self.db, _=self.user)

    def test_store_and_account_profiles_coexist_and_legacy_creation_derives_store(self):
        direct = self.create(store_id=self.store.id)
        account = self.create(store_account_id=self.account.id)
        self.assertEqual(account.store_id, self.store.id)
        self.assertEqual(direct.store_name, self.store.name)
        self.assertIsNone(direct.store_account_id)
        self.assertEqual(len(profiles.list_browser_profiles(db=self.db, _=self.user)), 2)

    def test_rejects_missing_mismatched_and_duplicate_owners(self):
        for owner in [{}, {'store_id': 9999}, {'store_account_id': 9999},
                      {'store_id': self.other.id, 'store_account_id': self.account.id}]:
            with self.subTest(owner=owner), self.assertRaises(HTTPException):
                self.create(**owner)
        self.create(store_id=self.store.id)
        self.create(store_account_id=self.account.id)
        for owner in [{'store_id': self.store.id}, {'store_account_id': self.account.id}]:
            with self.assertRaises(HTTPException):
                self.create(**owner)

    def test_database_enforces_direct_store_uniqueness(self):
        self.create(store_id=self.store.id)
        self.db.add(BrowserProfile(store_id=self.store.id, retailer='amazon'))
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_reassignment_preserves_id_and_schedule_and_rejects_busy_or_taken(self):
        p = self.create(store_account_id=self.account.id)
        updated = profiles.update_browser_profile_schedule(p.id, BrowserProfileScheduleUpdate(
            store_id=self.other.id, store_account_id=None, full_check_enabled=True), db=self.db, _=self.user)
        self.assertEqual(updated.id, p.id)
        self.assertEqual(updated.store_id, self.other.id)
        self.assertIsNone(updated.store_account_id)
        self.assertTrue(updated.full_check_enabled)
        taken = self.create(store_id=self.store.id)
        with self.assertRaises(HTTPException):
            profiles.update_browser_profile_schedule(p.id, BrowserProfileScheduleUpdate(store_id=self.store.id), db=self.db, _=self.user)
        self.db.get(BrowserProfile, p.id).status = 'importing'
        self.db.commit()
        with self.assertRaises(HTTPException):
            profiles.update_browser_profile_schedule(p.id, BrowserProfileScheduleUpdate(store_account_id=self.account.id), db=self.db, _=self.user)
        self.assertNotEqual(taken.id, p.id)

    def test_unshipped_checks_only_the_exact_owner(self):
        for store_id, account_id, number in [(self.store.id, None, 'direct'),
                (self.store.id, self.account.id, 'account'), (self.other.id, None, 'other')]:
            order = Order(store_id=store_id, store_account_id=account_id, store_order_number=number, status='imported')
            self.db.add(order)
            self.db.flush()
            self.db.add(Item(order_id=order.id, description='Item', status=ItemStatus.PURCHASED, quantity=1, price_sold=0))
        self.db.commit()
        self.assertEqual(profiles._unshipped_store_order_numbers(self.db, None, self.store.id), ['direct'])
        self.assertEqual(profiles._unshipped_store_order_numbers(self.db, self.account.id, self.store.id), ['account'])

    def test_import_uses_explicit_store_and_preserves_existing_owner(self):
        payload = StoreOrderImportPayload(store='walmart', externalOrder={'id': 'ownership-test'}, items=[])
        order_id = apply_store_order_payload(self.db, payload, self.user, store_id=self.store.id)
        order = self.db.get(Order, order_id)
        self.assertEqual(order.store_id, self.store.id)
        self.assertIsNone(order.store_account_id)
        apply_store_order_payload(self.db, payload, self.user, store_id=self.other.id)
        apply_store_order_payload(self.db, payload, self.user, store_account_id=self.account.id)
        self.assertEqual(order.store_id, self.store.id)
        self.assertIsNone(order.store_account_id)
        account_payload = payload.model_copy(update={'externalOrder': {'id': 'account-order'}})
        account_order = self.db.get(Order, apply_store_order_payload(self.db, account_payload, self.user, store_account_id=self.account.id))
        self.assertEqual((account_order.store_id, account_order.store_account_id), (self.store.id, self.account.id))

    def test_review_session_carries_both_owner_types(self):
        for account_id in [None, self.account.id]:
            token, _ = post_bulk_session([{'store': 'amazon', 'externalOrder': {'id': 'review'}, 'items': []}],
                store_id=self.store.id, store_account_id=account_id)
            try:
                result = get_bulk_import_session(token)
                self.assertEqual((result.store_id, result.store_account_id), (self.store.id, account_id))
            finally:
                _bulk_sessions.pop(token, None)
                _bulk_session_owners.pop(token, None)

    def test_delete_account_stops_only_its_profile_and_keeps_store_profile(self):
        direct = self.create(store_id=self.store.id)
        account = self.create(store_account_id=self.account.id)
        log = BrowserImportLog(browser_profile_id=account.id, retailer='walmart', level='info', event_type='check_finished', mode='full', message='Retain history')
        self.db.add(log)
        self.db.commit()
        log_id = log.id
        with patch.object(profiles.session_manager, 'close_session', new_callable=AsyncMock) as close, patch.object(profiles, 'all_profile_data_dirs', return_value=[]):
            asyncio.run(delete_store_account(self.store.id, self.account.id, db=self.db, _=self.user))
        close.assert_awaited_once_with(account.id)
        self.assertIsNotNone(self.db.get(BrowserProfile, direct.id))
        self.assertIsNone(self.db.get(BrowserProfile, account.id))
        self.db.expire_all()
        self.assertIsNone(self.db.get(BrowserImportLog, log_id).browser_profile_id)
        self.assertEqual(self.db.get(BrowserImportLog, log_id).message, 'Retain history')

    def test_delete_store_cancels_import_tasks_and_closes_all_profiles(self):
        direct = self.create(store_id=self.store.id)
        account = self.create(store_account_id=self.account.id)
        async def run():
            cancelled = asyncio.Event()
            async def work():
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            task = asyncio.create_task(work())
            profiles._import_tasks[direct.id] = task
            await asyncio.sleep(0)
            try:
                await delete_store(self.store.id, db=self.db, _=self.user)
                self.assertTrue(cancelled.is_set())
            finally:
                profiles._import_tasks.pop(direct.id, None)
        with patch.object(profiles.session_manager, 'close_session', new_callable=AsyncMock) as close, patch.object(profiles, 'all_profile_data_dirs', return_value=[]):
            asyncio.run(run())
        self.assertEqual({call.args[0] for call in close.await_args_list}, {direct.id, account.id})
        self.assertEqual(self.db.query(BrowserProfile).count(), 0)

    def test_manual_and_scheduled_jobs_keep_owner_context_without_retailer_requests(self):
        store_id, account_id = self.store.id, self.account.id
        for owner_account in [None, account_id]:
            p = self.create(store_id=store_id, store_account_id=owner_account)
            for scheduled in [False, True]:
                with self.subTest(account=owner_account, scheduled=scheduled):
                    raw = {'store': 'walmart', 'externalOrder': {'id': f'job-{p.id}-{scheduled}'}, 'items': []}
                    async def capture(*args, **kwargs):
                        if kwargs.get('on_order'):
                            kwargs['on_order'](raw)
                        return [raw]
                    job = create_job(p.id, 'import')
                    with patch.object(profiles, 'SessionLocal', return_value=self.db), patch.object(profiles, 'run_walmart_import', side_effect=capture), patch.object(profiles.session_manager, 'close_session', new_callable=AsyncMock):
                        asyncio.run(profiles._run_import_job(job.id, p.id, 'walmart', mode='full', max_pages=3,
                            auto_apply=scheduled, scheduled=scheduled, store_id=store_id, store_account_id=owner_account))
                    self.assertEqual(get_job(job.id).status, 'succeeded')
                    self.assertEqual(profiles.get_profile_job(p.id, db=self.db, _=None)['id'], job.id)
                    if scheduled:
                        order = self.db.query(Order).filter_by(store_order_number=raw['externalOrder']['id']).one()
                        self.assertEqual((order.store_id, order.store_account_id), (store_id, owner_account))
                    else:
                        token = get_job(job.id).token
                        try:
                            result = get_bulk_import_session(token)
                            self.assertEqual((result.store_id, result.store_account_id), (store_id, owner_account))
                        finally:
                            _bulk_sessions.pop(token, None)
                            _bulk_session_owners.pop(token, None)
                    log = self.db.query(BrowserImportLog).filter_by(job_id=job.id, event_type='check_finished').one()
                    self.assertEqual(log.store_name, 'Custom retailer name')
                    self.assertEqual(log.store_account_name, 'Subaccount' if owner_account is not None else None)

    def test_store_with_orders_keeps_profile_and_cookies_when_deletion_is_rejected(self):
        profile = self.create(store_id=self.store.id)
        self.db.add(Order(store_id=self.store.id, store_order_number='keep-order', status='imported'))
        self.db.commit()
        with patch.object(profiles.session_manager, 'close_session', new_callable=AsyncMock) as close, patch.object(profiles, 'all_profile_data_dirs') as paths:
            with self.assertRaises(HTTPException):
                asyncio.run(delete_store(self.store.id, db=self.db, _=self.user))
            close.assert_not_awaited()
            paths.assert_not_called()
        self.assertIsNotNone(self.db.get(BrowserProfile, profile.id))
