"""The additive BFMR migration preserves existing order data and defaults off."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


class BfmrOrderMigrationTests(unittest.TestCase):
    def test_upgrade_and_downgrade_preserve_orders(self):
        engine = create_engine("sqlite:///:memory:")
        spec = importlib.util.spec_from_file_location("bfmr_migration", Path(__file__).parents[1] / "alembic/versions/081_bfmr_order_numbers.py")
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE TABLE buying_groups (id INTEGER PRIMARY KEY, name TEXT)"))
                conn.execute(text("CREATE TABLE orders (id INTEGER PRIMARY KEY, store_order_number TEXT)"))
                conn.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY, description TEXT)"))
                conn.execute(text("INSERT INTO buying_groups VALUES (1, 'Existing BFMR')"))
                conn.execute(text("INSERT INTO orders VALUES (1, 'EXISTING-ORDER')"))
                conn.execute(text("INSERT INTO items VALUES (1, 'Existing item')"))
                with patch.object(migration, "op", Operations(MigrationContext.configure(conn))):
                    migration.upgrade()
                    self.assertEqual(conn.execute(text("SELECT order_number_submit_enabled FROM buying_groups")).scalar(), 0)
                    self.assertIn("bfmr_order_tasks", inspect(conn).get_table_names())
                    migration.downgrade()
                    self.assertNotIn("bfmr_order_tasks", inspect(conn).get_table_names())
                    self.assertEqual(conn.execute(text("SELECT store_order_number FROM orders")).scalar(), "EXISTING-ORDER")
                    self.assertEqual(conn.execute(text("SELECT description FROM items")).scalar(), "Existing item")
        finally:
            engine.dispose()
