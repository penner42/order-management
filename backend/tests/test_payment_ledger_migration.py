"""Upgrade a pre-ledger schema without changing payment or receipt history."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa
from app.database import Base
import app.models


class LedgerMigrationTests(unittest.TestCase):
    def test_upgrade_preserves_confirmed_and_legacy_payments(self):
        engine=sa.create_engine('sqlite:///:memory:')
        metadata=sa.MetaData()
        for table in Base.metadata.sorted_tables:table.to_metadata(metadata)
        removed={
            'payments':['source_amount','ledger_state','allocation_manual','allocation_version','allocation_details','sync_issues'],
            'buying_groups':['payment_sync_enabled','payment_sync_last_run_at','payment_sync_error'],
            'payment_sources':['revisions'],
        }
        for name,columns in removed.items():
            for column in columns:metadata.tables[name]._columns.remove(metadata.tables[name].c[column])
        metadata.create_all(engine)
        path=Path(__file__).parents[1]/'alembic/versions/082_source_payment_ledger.py'
        spec=importlib.util.spec_from_file_location('ledger_migration',path)
        migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
        with engine.begin() as c:
            c.execute(sa.text("INSERT INTO buying_groups (id,name,aliases) VALUES (1,'Example','[]')"))
            c.execute(sa.text("INSERT INTO payments (id,buying_group_id,payment_requested_at,payment_bonus) VALUES (1,1,'2026-01-05',5),(2,1,'2026-01-06',7)"))
            c.execute(sa.text('INSERT INTO payment_sources (id,buying_group_id,provider,kind,external_id,payload,revision,present) VALUES (1,1,\'parsefile\',\'payment\',\'p1\',:payload,\'revision\',true)'),{'payload':'{"amount":"100.00"}'})
            c.execute(sa.text("INSERT INTO payment_reviews (id,source_id,version,content,payment_id) VALUES (1,1,1,'{}',1)"))
            with patch.object(migration,'op',Operations(MigrationContext.configure(c))):
                migration.upgrade()
                rows=c.execute(sa.text('SELECT id,source_amount,ledger_state,allocation_manual,payment_bonus FROM payments ORDER BY id')).all()
                self.assertEqual(tuple(rows[0]),(1,100,'imported',1,5))
                self.assertEqual(tuple(rows[1]),(2,None,'legacy',0,7))
                self.assertEqual(c.execute(sa.text('SELECT COUNT(*) FROM payment_reviews')).scalar(),1)
                with self.assertRaises(RuntimeError):migration.downgrade()
        engine.dispose()
