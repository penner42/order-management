"""File logging preserves committed UI events and safe API failure diagnostics."""
import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.application_logging import configure_application_logging
from app.database import Base
from app.models import BrowserImportLog
from app.utils.buying_group_apis import buyinggroup


class ApplicationLoggingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "application.log"
        self.logger = logging.getLogger("app")
        self.handlers, self.level = self.logger.handlers[:], self.logger.level
        self.logger.handlers = []
        configure_application_logging(str(self.path), 1024 * 1024, 2)

    def tearDown(self):
        for handler in self.logger.handlers:
            handler.close()
        self.logger.handlers = self.handlers
        self.logger.setLevel(self.level)
        self.directory.cleanup()

    def records(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def test_screen_events_are_written_only_after_commit_with_database_id(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        try:
            with Session(engine) as session:
                row = BrowserImportLog(category="groups", level="error", mode="tracking_submit",
                    event_type="tracking_submit_error", retailer="buyinggroup", scheduled=True,
                    store_name="Warehouse", tracking_numbers='["TRACK-1"]',
                    message="No trackings were added")
                session.add(row)
                session.flush()
                self.assertEqual(self.records(), [])
                session.commit()
                record = self.records()[0]
                self.assertEqual(record["message"], "No trackings were added")
                self.assertEqual(record["details"]["id"], row.id)
                self.assertEqual(record["details"]["tracking_numbers"], ["TRACK-1"])
                session.add(BrowserImportLog(event_type="order_error", mode="full", retailer="amazon"))
                session.flush()
                session.rollback()
                session.commit()
                self.assertEqual(len(self.records()), 1)
        finally:
            engine.dispose()

    def test_http_200_failure_logs_response_and_redacts_credentials(self):
        response = Mock(status=200)
        response.read.return_value = json.dumps({"status": "FAIL", "message": "No trackings were added",
            "errors": [{"tracking": "TRACK-1", "reason": "Rejected"}],
            "access_token": "private-session"}).encode()
        connection = Mock()
        connection.getresponse.return_value = response
        with patch.object(buyinggroup.http.client, "HTTPSConnection", return_value=connection):
            with self.assertRaisesRegex(RuntimeError, "No trackings were added"):
                buyinggroup.add_trackings(access_token="private-request-token", tracking_list=["TRACK-1"],
                    base_url="https://example.test")
        record = self.records()[0]
        self.assertEqual(record["details"]["http_status"], 200)
        self.assertIn("Rejected", record["details"]["response"])
        self.assertNotIn("private-session", self.path.read_text())
        self.assertNotIn("private-request-token", self.path.read_text())

    def test_auth_responses_are_omitted_and_plaintext_secrets_are_redacted(self):
        buyinggroup._log_failed_response("https://example.test/v2/token/get", 401, {"data": "private-session"})
        self.logger.warning("password=private-password Authorization: Bearer private-token",
                            extra={"details": {"nested": [{"api_key": "private-key"}]}})
        self.assertNotIn("private-", self.path.read_text())

    def test_rotation_is_bounded_and_reconfiguration_does_not_duplicate(self):
        configure_application_logging(str(self.path), 300, 2)
        for index in range(20):
            self.logger.info("event %s %s", index, "x" * 100)
        self.assertTrue(Path(str(self.path) + ".1").exists())
        self.assertFalse(Path(str(self.path) + ".3").exists())
        self.assertEqual(len(self.logger.handlers), 1)


if __name__ == "__main__":
    unittest.main()
