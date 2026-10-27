"""Disabled environments must never dispatch scheduled work or claim database jobs."""
import asyncio
import unittest
from unittest.mock import patch

from app.config import Settings, settings
from app.browser_automation import scheduler
from app.routers import buying_groups, browser_profiles, monarch
from app import bfmr_orders


class SchedulingPolicyTests(unittest.TestCase):
    def test_safe_default(self):
        self.assertFalse(Settings.model_fields['scheduled_jobs_enabled'].default)

    @patch.object(settings, 'scheduled_jobs_enabled', False)
    @patch.object(settings, 'group_submissions_enabled', True)
    def test_all_dispatchers_skip_even_when_submissions_and_database_schedules_are_enabled(self):
        # A production snapshot may enable every schedule; no DB reads/claims are allowed.
        with (patch.object(scheduler, 'SessionLocal') as scheduler_db,
              patch.object(scheduler.asyncio, 'create_task') as create_task,
              patch.object(monarch, 'scheduled_tick') as monarch_tick,
              patch.object(bfmr_orders, 'worker_tick') as bfmr_tick):
            scheduler.start_scheduler()
            asyncio.run(scheduler._tick())
            create_task.assert_not_called()
            monarch_tick.assert_not_called()
            bfmr_tick.assert_not_called()
            scheduler_db.assert_not_called()
        with (patch.object(buying_groups, 'SessionLocal') as groups_db,
              patch.object(browser_profiles, 'SessionLocal') as profiles_db,
              patch.object(monarch, 'SessionLocal') as monarch_db,
              patch.object(bfmr_orders, 'SessionLocal') as bfmr_db):
            self.assertFalse(buying_groups.run_scheduled_group(123))
            self.assertFalse(buying_groups.run_scheduled_tracking_submit(123))
            self.assertFalse(buying_groups.run_scheduled_received_check(123))
            self.assertFalse(asyncio.run(browser_profiles.start_scheduled_import(123, mode='full', max_pages=3)))
            asyncio.run(monarch.scheduled_tick())
            bfmr_orders.worker_tick()
            for db in (groups_db, profiles_db, monarch_db, bfmr_db):
                db.assert_not_called()
