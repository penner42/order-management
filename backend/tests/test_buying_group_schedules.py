"""Interval scheduling uses isolated databases and never contacts group APIs."""
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import BuyingGroup
from app.buying_group_schedules import claim_group_job, list_due_group_ids, rebuild_schedules, utc
from app.routers import buying_groups
from app.schemas.buying_group import BuyingGroupCreate, BuyingGroupUpdate


class BuyingGroupScheduleTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.now = datetime(2026, 10, 19, tzinfo=timezone.utc)
        self.a = BuyingGroup(name="A", tracking_submit_enabled=True,
                             schedule_interval_minutes=10, received_check_enabled=True)
        self.b = BuyingGroup(name="B", received_check_enabled=True, schedule_interval_minutes=20)
        self.db.add_all([self.a, self.b])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def rebuild(self):
        rebuild_schedules(self.db, self.now)
        self.db.commit()

    def test_stagger_is_persisted_and_no_group_is_immediately_due(self):
        self.rebuild()
        self.assertEqual(utc(self.a.schedule_next_run_at), self.now + timedelta(minutes=5))
        self.assertEqual(utc(self.b.schedule_next_run_at), self.now + timedelta(minutes=10))
        self.db.expire_all()
        self.assertEqual(list_due_group_ids(self.db, self.now), [])
        self.assertEqual(list_due_group_ids(self.db, self.now + timedelta(minutes=5)), [self.a.id])

    def test_claim_preserves_cadence_and_skips_missed_runs(self):
        self.rebuild()
        now = self.now + timedelta(minutes=42)
        group = claim_group_job(self.db, self.a.id, now)
        self.assertIsNotNone(group)
        self.assertEqual(utc(group.schedule_next_run_at), self.now + timedelta(minutes=45))
        self.assertIsNone(claim_group_job(self.db, self.a.id, now))

    def test_schedule_edit_rebuilds_all_but_name_edit_preserves_schedule(self):
        self.rebuild()
        before = self.b.schedule_next_run_at
        buying_groups.update_buying_group(self.a.id, BuyingGroupUpdate(name="Renamed"), self.db, None)
        self.assertEqual(self.b.schedule_next_run_at, before)
        buying_groups.update_buying_group(self.a.id, BuyingGroupUpdate(schedule_interval_minutes=30), self.db, None)
        self.assertNotEqual(self.b.schedule_next_run_at, before)
        buying_groups.update_buying_group(self.a.id, BuyingGroupUpdate(tracking_submit_enabled=False, received_check_enabled=False), self.db, None)
        self.assertIsNone(self.a.schedule_next_run_at)

    def test_manual_runs_do_not_move_schedule(self):
        self.rebuild()
        before = self.a.schedule_next_run_at
        buying_groups._stamp_received_check_last_run(self.db, self.a.id)
        self.assertEqual(self.a.schedule_next_run_at, before)

    def test_enabled_actions_run_in_order_even_after_submission_failure(self):
        self.rebuild()
        self.a.schedule_next_run_at = self.now
        self.db.commit()
        calls = []
        with patch("app.config.settings.group_submissions_enabled", True), \
             patch.object(buying_groups, "SessionLocal", return_value=self.db), \
             patch.object(buying_groups, "run_scheduled_tracking_submit", side_effect=lambda _: calls.append("submit") or False), \
             patch.object(buying_groups, "run_scheduled_received_check", side_effect=lambda _: calls.append("receive")):
            self.assertTrue(buying_groups.run_scheduled_group(self.a.id))
        self.assertEqual(calls, ["submit", "receive"])

    def test_submit_only_does_not_check_receiving(self):
        self.a.received_check_enabled = False
        self.a.schedule_next_run_at = self.now
        self.db.commit()
        with patch("app.config.settings.group_submissions_enabled", True), \
             patch.object(buying_groups, "SessionLocal", return_value=self.db), \
             patch.object(buying_groups, "run_scheduled_tracking_submit") as submit, \
             patch.object(buying_groups, "run_scheduled_received_check") as receive:
            self.assertTrue(buying_groups.run_scheduled_group(self.a.id))
            submit.assert_called_once_with(self.a.id)
            receive.assert_not_called()

    def test_disabled_submission_only_group_is_not_due(self):
        self.a.received_check_enabled = False
        self.a.schedule_next_run_at = self.now
        self.db.commit()
        with patch("app.config.settings.group_submissions_enabled", False):
            self.assertNotIn(self.a.id, list_due_group_ids(self.db, self.now))
            self.assertIsNone(claim_group_job(self.db, self.a.id, self.now))

    def test_receiving_only_does_not_submit(self):
        self.rebuild()
        self.b.schedule_next_run_at = self.now
        self.db.commit()
        with patch.object(buying_groups, "SessionLocal", return_value=self.db), \
             patch.object(buying_groups, "run_scheduled_tracking_submit") as submit, \
             patch.object(buying_groups, "run_scheduled_received_check") as receive:
            self.assertTrue(buying_groups.run_scheduled_group(self.b.id))
            submit.assert_not_called()
            receive.assert_called_once_with(self.b.id)

    def test_initialization_staggers_instead_of_running_everything(self):
        self.assertEqual(list_due_group_ids(self.db, self.now), [])
        self.assertIsNotNone(self.a.schedule_next_run_at)

    def test_invalid_intervals_are_rejected(self):
        for schema in (BuyingGroupCreate, BuyingGroupUpdate):
            for value in (0, -1, 1.5, None, True, 525601):
                with self.subTest(schema=schema, value=value), self.assertRaises(ValidationError):
                    schema(name="Test", schedule_interval_minutes=value)


if __name__ == "__main__":
    unittest.main()
