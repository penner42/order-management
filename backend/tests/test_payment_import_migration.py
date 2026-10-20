"""The additive migration preserves pre-existing payments on upgrade/downgrade."""
import importlib.util
from pathlib import Path
from unittest.mock import patch
import unittest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from app.database import Base
import app.models


class PaymentImportMigrationTests(unittest.TestCase):
    def test_additive_upgrade_and_downgrade(self):
        engine = create_engine("sqlite:///:memory:")
        new_tables = {"payment_sources", "payment_reviews", "payment_receipt_coverage", "payment_corrections"}
        Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in new_tables])
        path = Path(__file__).parents[1] / "alembic/versions/075_payment_imports.py"
        spec = importlib.util.spec_from_file_location("payment_import_migration", path)
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        try:
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO buying_groups (id, name, aliases) VALUES (1, 'Existing group', '[]')"))
                connection.execute(text("INSERT INTO payments (id, buying_group_id, payment_requested_at) VALUES (1, 1, '2026-01-01 12:00:00')"))
                with patch.object(migration, "op", Operations(MigrationContext.configure(connection))):
                    migration.upgrade()
                    self.assertTrue(new_tables <= set(inspect(connection).get_table_names()))
                    self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM payments")).scalar(), 1)
                    migration.downgrade()
                    self.assertFalse(new_tables & set(inspect(connection).get_table_names()))
                    self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM payments")).scalar(), 1)
        finally:
            engine.dispose()
