"""Verify profile migration on a disposable PostgreSQL database."""
import os
import subprocess
import sys
import unittest
from uuid import uuid4
from sqlalchemy import create_engine, text
from app.config import settings


@unittest.skipUnless(settings.database_url.startswith('postgresql'), 'requires PostgreSQL in Docker')
class BrowserProfileMigrationTests(unittest.TestCase):
    def test_backfill_preserves_profiles_and_database_constraints(self):
        admin = create_engine(settings.database_url, isolation_level='AUTOCOMMIT')
        name = 'test_profile_migration_' + uuid4().hex
        engine = None
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        try:
            url = admin.url.set(database=name)
            engine = create_engine(url)
            env = {**os.environ, 'DATABASE_URL': url.render_as_string(hide_password=False)}
            def migrate(target):
                result = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', target], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            # Historical enum migrations need a commit before later data migrations use new values.
            migrate('021')
            migrate('069')
            with engine.begin() as conn:
                store_id = conn.scalar(text("INSERT INTO stores(id,name) SELECT coalesce(max(id),0)+100,'migration store' FROM stores RETURNING id"))
                account_id = conn.scalar(text("INSERT INTO store_accounts(store_id,name) VALUES (:store,'migration account') RETURNING id"), {'store': store_id})
                profile_id = conn.scalar(text("INSERT INTO browser_profiles(store_account_id,retailer,status,full_check_enabled,full_check_cron) VALUES (:account,'amazon','ready',true,'5 2 * * *') RETURNING id"), {'account': account_id})
            migrate('head')
            with engine.connect() as conn:
                row = conn.execute(text('SELECT id,store_id,store_account_id,status,full_check_enabled,full_check_cron FROM browser_profiles')).one()
                self.assertEqual(tuple(row), (profile_id, store_id, account_id, 'ready', True, '5 2 * * *'))
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO browser_profiles(store_id,retailer) VALUES (:store,'walmart')"), {'store': store_id})
            from sqlalchemy.exc import IntegrityError
            # Integration profiles have their own owner and retain the existing profile namespace.
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO browser_profiles(integration,retailer,status) VALUES ('monarch','monarch','logged_out')"))
            for values in ["NULL, NULL, 'amazon'", "NULL, 'monarch', 'monarch'", f"{store_id}, 'monarch', 'monarch'"]:
                with self.assertRaises(IntegrityError), engine.begin() as conn:
                    conn.execute(text(f"INSERT INTO browser_profiles(store_id,integration,retailer) VALUES ({values})"))
            for values in [{'store': store_id, 'account': None}, {'store': store_id, 'account': account_id}]:
                with self.assertRaises(IntegrityError), engine.begin() as conn:
                    conn.execute(text("INSERT INTO browser_profiles(store_id,store_account_id,retailer) VALUES (:store,:account,'walmart')"), values)
        finally:
            if engine is not None:
                engine.dispose()
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
            admin.dispose()
