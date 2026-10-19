"""Exercise schedule backfill in a disposable, transaction-scoped schema."""
import importlib.util
from pathlib import Path
import unittest
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.config import settings


@unittest.skipUnless(settings.database_url.startswith("postgresql"), "requires PostgreSQL in Docker")
class BuyingGroupScheduleMigrationTests(unittest.TestCase):
    def test_migration_preserves_cadence_and_effective_receiving_toggle(self):
        engine = create_engine(settings.database_url)
        try:
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    schema = "test_schedule_" + uuid4().hex
                    connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                    connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                    connection.execute(text("""CREATE TABLE buying_groups (
                        id integer PRIMARY KEY, name text,
                        tracking_submit_enabled boolean, received_check_enabled boolean,
                        tracking_submit_cron text, received_check_cron text)"""))
                    connection.execute(text("""INSERT INTO buying_groups VALUES
                        (1, 'Enabled', true, false, '*/5 * * * *', '0 */6 * * *'),
                        (2, 'Disabled', false, true, '0 */6 * * *', '*/5 * * * *'),
                        (3, 'Invalid', true, false, 'invalid', 'invalid')"""))
                    path = Path(__file__).parents[1] / "alembic/versions/073_buying_group_intervals.py"
                    spec = importlib.util.spec_from_file_location("schedule_migration", path)
                    migration = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(migration)
                    with Operations.context(MigrationContext.configure(connection)):
                        migration.upgrade()
                        rows = connection.execute(text("SELECT name, schedule_interval_minutes, received_check_enabled, schedule_next_run_at FROM buying_groups ORDER BY id")).all()
                        self.assertEqual(rows[0].schedule_interval_minutes, 5)
                        self.assertTrue(rows[0].received_check_enabled)
                        self.assertEqual(tuple(rows[1]), ("Disabled", 360, False, None))
                        self.assertEqual(tuple(rows[2]), ("Invalid", 360, True, None))
                        with self.assertRaises(IntegrityError), connection.begin_nested():
                            connection.execute(text("UPDATE buying_groups SET schedule_interval_minutes=0 WHERE id=1"))
                        migration.downgrade()
                        self.assertEqual(connection.scalar(text("SELECT count(*) FROM buying_groups")), 3)
                finally:
                    transaction.rollback()
        finally:
            engine.dispose()
