"""The additive migration preserves pre-existing payments on upgrade/downgrade."""
import importlib.util
from pathlib import Path
from unittest.mock import patch
import unittest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
import sqlalchemy as sa
from app.database import Base
import app.models


class PaymentImportMigrationTests(unittest.TestCase):
    def test_installment_upgrade_preserves_coverage_and_safe_downgrade(self):
        engine = create_engine("sqlite:///:memory:")
        metadata = sa.MetaData()
        sa.Table("payment_receipt_coverage", metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("receipt_source_id", sa.Integer, nullable=False),
            sa.Column("payment_id", sa.Integer, nullable=False),
            sa.Column("item_id", sa.Integer, nullable=False),
            sa.Column("amount", sa.Numeric(12,2), nullable=False),
            sa.UniqueConstraint("receipt_source_id", "item_id", name="uq_receipt_coverage_item"))
        metadata.create_all(engine)
        path = Path(__file__).parents[1] / "alembic/versions/078_partial_payment_coverage.py"
        spec = importlib.util.spec_from_file_location("installment_migration", path)
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        try:
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO payment_receipt_coverage VALUES (1, 1, 1, 1, 40)"))
                with patch.object(migration, "op", Operations(MigrationContext.configure(connection))):
                    migration.upgrade()
                    self.assertEqual(connection.execute(text("SELECT amount FROM payment_receipt_coverage")).scalar(),40)
                    connection.execute(text("INSERT INTO payment_receipt_coverage VALUES (2, 1, 2, 1, 60)"))
                    with self.assertRaises(RuntimeError):migration.downgrade()
                    self.assertEqual(connection.execute(text("SELECT SUM(amount) FROM payment_receipt_coverage")).scalar(),100)
                    connection.execute(text("DELETE FROM payment_receipt_coverage WHERE id=2"))
                    migration.downgrade()
                    self.assertIn("uq_receipt_coverage_item",{c["name"] for c in inspect(connection).get_unique_constraints("payment_receipt_coverage")})
        finally:
            engine.dispose()

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
