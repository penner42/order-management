"""Verify schedule conversion in a disposable, transaction-scoped schema."""
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
class StoreScheduleMigrationTests(unittest.TestCase):
    def test_preserves_toggles_and_unshipped_cadence_and_roundtrips(self):
        engine = create_engine(settings.database_url)
        try:
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    schema = "test_store_schedule_" + uuid4().hex
                    connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                    connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                    connection.execute(text("""CREATE TABLE browser_profiles (
                        id integer PRIMARY KEY, integration text, full_check_enabled boolean,
                        unshipped_check_enabled boolean, unshipped_check_cron text)"""))
                    connection.execute(text("""INSERT INTO browser_profiles VALUES
                        (1, NULL, true, false, '*/5 * * * *'),
                        (2, NULL, false, true, '0 */6 * * *'),
                        (3, NULL, true, true, 'invalid'),
                        (4, 'monarch', false, false, '*/5 * * * *')"""))
                    path = Path(__file__).parents[1] / "alembic/versions/076_store_check_intervals.py"
                    spec = importlib.util.spec_from_file_location("store_schedule_migration", path)
                    migration = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(migration)
                    with Operations.context(MigrationContext.configure(connection)):
                        migration.upgrade()
                        rows = connection.execute(text("""SELECT full_check_enabled, unshipped_check_enabled,
                            unshipped_check_interval_minutes, full_check_next_run_at, unshipped_check_next_run_at
                            FROM browser_profiles ORDER BY id""")).all()
                        self.assertEqual([tuple(row) for row in rows], [
                            (True, False, 5, None, None), (False, True, 360, None, None),
                            (True, True, 360, None, None), (False, False, 360, None, None)])
                        with self.assertRaises(IntegrityError), connection.begin_nested():
                            connection.execute(text("UPDATE browser_profiles SET unshipped_check_interval_minutes=0 WHERE id=1"))
                        migration.downgrade()
                        self.assertEqual(connection.scalar(text("SELECT count(*) FROM browser_profiles")), 4)
                finally:
                    transaction.rollback()
        finally:
            engine.dispose()
