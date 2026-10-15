"""Dev must reject remote group writes before network or database activity."""
import inspect
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.config import Settings
from app.routers import buying_groups, items
from app.submission_policy import require_group_submissions_enabled
from app.utils.buying_group_apis import bfmr, buyinggroup, dealandrunner, parsefile, submit_tracking, usabg


class SubmissionPolicyTests(unittest.TestCase):
    def test_default_is_disabled(self):
        self.assertFalse(Settings(_env_file=None, group_submissions_enabled=False).group_submissions_enabled)
        self.assertFalse(Settings.model_fields["group_submissions_enabled"].default)

    @patch("app.config.settings.group_submissions_enabled", False)
    def test_all_submission_entry_points_reject_before_side_effects(self):
        functions = [
            buying_groups.submit_buying_group_trackings,
            buying_groups._run_tracking_submit,
            items.submit_item_tracking,
            submit_tracking.submit_item_tracking_to_group,
            submit_tracking.batch_submit_pending_trackings,
            bfmr.submit_trackings, buyinggroup.submit_trackings,
            dealandrunner.submit_trackings, parsefile.submit_trackings,
            usabg.submit_trackings, usabg.upsert_tracking_note,
        ]
        with patch("urllib.request.urlopen") as network:
            for function in functions:
                with self.subTest(function=f"{function.__module__}.{function.__name__}"):
                    kwargs = {name: None for name, parameter in inspect.signature(function).parameters.items()
                              if parameter.default is inspect.Parameter.empty}
                    if "db" in inspect.signature(function).parameters:
                        kwargs["db"] = Mock()
                    with self.assertRaises(HTTPException) as caught:
                        function(**kwargs)
                    self.assertEqual(caught.exception.status_code, 403)
                    if "db" in kwargs:
                        self.assertEqual(kwargs["db"].mock_calls, [])
            network.assert_not_called()

    @patch("app.config.settings.group_submissions_enabled", False)
    def test_restored_enabled_schedules_are_ignored(self):
        db = Mock()
        self.assertEqual(buying_groups.list_due_tracking_submit_group_ids(db), [])
        self.assertEqual(db.mock_calls, [])
        with patch.object(buying_groups, "SessionLocal") as sessions:
            self.assertFalse(buying_groups.run_scheduled_tracking_submit(123))
            sessions.assert_not_called()

    @patch("app.config.settings.group_submissions_enabled", True)
    def test_production_can_explicitly_enable_submissions(self):
        require_group_submissions_enabled()


if __name__ == "__main__":
    unittest.main()
