"""Store schedule regressions use isolated databases and never open browsers."""
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import BrowserProfile, Store
from app.browser_automation.schedules import claim_profile_job, list_due_profile_jobs, rebuild_schedules
from app.buying_group_schedules import utc
from app.routers.browser_profiles import update_browser_profile_schedule
from app.schemas.browser_profile import BrowserProfileScheduleUpdate


class StoreScheduleTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.now = datetime(2026, 10, 20, tzinfo=timezone.utc)
        stores = [Store(name="A"), Store(name="B")]
        self.db.add_all(stores)
        self.db.flush()
        self.a = BrowserProfile(store_id=stores[0].id, retailer="amazon", status="ready",
                                full_check_enabled=True, unshipped_check_enabled=True,
                                unshipped_check_interval_minutes=10, full_check_max_pages=5)
        self.b = BrowserProfile(store_id=stores[1].id, retailer="walmart", status="ready",
                                unshipped_check_enabled=True, unshipped_check_interval_minutes=20)
        self.db.add_all([self.a, self.b])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def rebuild(self):
        rebuild_schedules(self.db, self.now)
        self.db.commit()

    def test_stagger_persists_both_modes_and_profiles_without_initial_burst(self):
        self.assertEqual(list_due_profile_jobs(self.db, self.now), [])
        self.db.expire_all()
        self.assertEqual(utc(self.a.full_check_next_run_at), self.now + timedelta(seconds=200))
        self.assertEqual(utc(self.a.unshipped_check_next_run_at), self.now + timedelta(seconds=400))
        self.assertEqual(utc(self.b.unshipped_check_next_run_at), self.now + timedelta(minutes=10))
        self.assertEqual(list_due_profile_jobs(self.db, self.now + timedelta(seconds=200)), [(self.a.id, "full", 5)])

    def test_full_is_daily_and_unshipped_uses_its_interval_and_skips_missed_runs(self):
        self.rebuild()
        full_due = utc(self.a.full_check_next_run_at)
        claim_profile_job(self.db, self.a.id, "full", full_due)
        self.assertEqual(utc(self.a.full_check_next_run_at), full_due + timedelta(days=1))
        self.assertIsNone(claim_profile_job(self.db, self.a.id, "full", full_due))
        due = utc(self.a.unshipped_check_next_run_at)
        claim_profile_job(self.db, self.a.id, "unshipped", due + timedelta(minutes=41))
        self.assertEqual(utc(self.a.unshipped_check_next_run_at), due + timedelta(minutes=50))
        # Claiming persists before a browser can fail; re-polling does not retry.
        self.assertIsNone(claim_profile_job(self.db, self.a.id, "unshipped", due + timedelta(minutes=41)))

    def test_daily_only_checks_spread_across_the_day(self):
        self.a.unshipped_check_enabled = False
        self.b.unshipped_check_enabled = False
        self.b.full_check_enabled = True
        self.db.commit()
        self.rebuild()
        self.assertEqual(utc(self.a.full_check_next_run_at), self.now + timedelta(hours=12))
        self.assertEqual(utc(self.b.full_check_next_run_at), self.now + timedelta(days=1))
        self.assertIsNone(self.a.unshipped_check_next_run_at)

    def test_toggles_are_independent_and_schedule_edits_rebuild_other_profiles(self):
        self.rebuild()
        before = self.b.unshipped_check_next_run_at
        updated = update_browser_profile_schedule(self.a.id, BrowserProfileScheduleUpdate(
            full_check_enabled=False, unshipped_check_interval_minutes=15), db=self.db, _=None)
        self.assertIsNone(updated.full_check_next_run_at)
        self.assertTrue(updated.unshipped_check_enabled)
        self.assertNotEqual(self.b.unshipped_check_next_run_at, before)
        before = self.b.unshipped_check_next_run_at
        update_browser_profile_schedule(self.a.id, BrowserProfileScheduleUpdate(full_check_max_pages=4), db=self.db, _=None)
        self.assertEqual(self.b.unshipped_check_next_run_at, before)
        updated = update_browser_profile_schedule(self.a.id, BrowserProfileScheduleUpdate(
            full_check_enabled=True, unshipped_check_enabled=False), db=self.db, _=None)
        self.assertIsNotNone(updated.full_check_next_run_at)
        self.assertIsNone(updated.unshipped_check_next_run_at)

    def test_busy_logged_out_and_integration_profiles_cannot_be_claimed(self):
        self.rebuild()
        now = self.now + timedelta(hours=1)
        for status in ("importing", "login_in_progress", "logged_out", "login_required", "error"):
            self.a.status = status
            self.db.commit()
            self.assertIsNone(claim_profile_job(self.db, self.a.id, "full", now))
            self.assertNotIn(self.a.id, [job[0] for job in list_due_profile_jobs(self.db, now)])
        monarch = BrowserProfile(integration="monarch", retailer="monarch", status="ready", full_check_enabled=True)
        self.db.add(monarch)
        self.db.commit()
        self.assertIsNone(claim_profile_job(self.db, monarch.id, "full", now))
        self.assertNotIn(monarch.id, [job[0] for job in list_due_profile_jobs(self.db, now)])

    def test_scheduled_start_claims_before_queueing_and_uses_current_page_limit(self):
        import asyncio
        from app.routers.browser_profiles import start_scheduled_import
        self.rebuild()
        self.a.full_check_next_run_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        self.db.commit()
        with patch("app.routers.browser_profiles.SessionLocal", return_value=self.db), patch(
            "app.routers.browser_profiles._queue_import", side_effect=RuntimeError("browser failed")
        ) as queue:
            with self.assertRaises(RuntimeError):
                asyncio.run(start_scheduled_import(self.a.id, mode="full", max_pages=3))
            self.assertEqual(queue.call_args.kwargs["max_pages"], 5)
            self.assertFalse(asyncio.run(start_scheduled_import(self.a.id, mode="full", max_pages=3)))
            self.assertEqual(queue.call_count, 1)

    def test_minute_interval_validation(self):
        for minutes in (0, -1, 525601, 1.5):
            with self.assertRaises(ValidationError):
                BrowserProfileScheduleUpdate(unshipped_check_interval_minutes=minutes)
