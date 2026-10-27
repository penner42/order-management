"""Database replacement tests use their own PostgreSQL database, never dev data."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import Base, get_db
from app.models import User, BuyingGroup, ReceivingReceiptCache, PaymentMethod
from app.auth import get_current_user
from app.routers import admin
from app.utils.database_transfer import encode, snapshot, restore


@unittest.skipUnless(settings.database_url.startswith('postgresql'), 'requires Docker PostgreSQL')
class DatabaseTransferTests(unittest.TestCase):
    def setUp(self):
        self.admin_engine = create_engine(settings.database_url, isolation_level='AUTOCOMMIT')
        self.name = 'test_database_transfer_' + uuid4().hex
        with self.admin_engine.connect() as c:
            c.execute(text(f'CREATE DATABASE "{self.name}"'))
        self.engine = create_engine(self.admin_engine.url.set(database=self.name))
        self.addCleanup(self.cleanup_database)
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as c:
            c.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
            c.execute(text("INSERT INTO alembic_version VALUES ('test-revision')"))
        with Session(self.engine) as db:
            db.add(User(id=40, username='prod-admin', hashed_password='hash', role='admin'))
            db.add(BuyingGroup(id=70, name='BG', tracking_submit_enabled=True, received_check_enabled=True))
            db.flush()
            db.add(ReceivingReceiptCache(buying_group_id=70, provider_identity='BG',
                                        payload={'receipts': [{'tracking': '543161235300'}]},
                                        fetched_at=datetime.now(timezone.utc)))
            db.add(PaymentMethod(id=90, label='Parent card'))
            db.flush()
            db.add(PaymentMethod(id=20, label='Child card', parent_id=90))
            db.commit()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def cleanup_database(self):
        self.engine.dispose()
        with self.admin_engine.connect() as c:
            c.execute(text(f'DROP DATABASE "{self.name}" WITH (FORCE)'))
        self.admin_engine.dispose()

    def export(self):
        with self.engine.connect() as c:
            return json.loads(encode(snapshot(c)))

    def test_roundtrip_includes_receipt_cache_and_self_references_and_reseeds_ids(self):
        payload = self.export()
        with self.engine.begin() as c:
            c.execute(text("UPDATE users SET username='dev-admin'"))
        with self.engine.begin() as c:
            result = restore(c, payload, self.tmp.name)
        saved = json.loads((Path(self.tmp.name) / result['backup_filename']).read_text())
        self.assertEqual(saved['tables']['users'][0]['username'], 'dev-admin')
        with Session(self.engine) as db:
            self.assertEqual(db.get(User, 40).username, 'prod-admin')
            self.assertEqual(db.get(PaymentMethod, 20).parent_id, 90)
            self.assertEqual(db.get(ReceivingReceiptCache, 70).payload['receipts'][0]['tracking'], '543161235300')
            self.assertFalse(db.get(BuyingGroup, 70).received_check_enabled)
            row = User(username='new-admin', hashed_password='hash', role='admin')
            db.add(row)
            db.flush()
            self.assertGreater(row.id, 40)

    def test_invalid_foreign_key_rolls_back_all_changes(self):
        payload = self.export()
        payload['tables']['payment_methods'][0]['parent_id'] = 9999
        before = self.export()['tables']
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as c:
                restore(c, payload, self.tmp.name)
        self.assertEqual(self.export()['tables'], before)
        with Session(self.engine) as db:
            self.assertTrue(db.get(BuyingGroup, 70).received_check_enabled)
            self.assertEqual(db.get(PaymentMethod, 20).parent_id, 90)

    def test_incomplete_or_wrong_version_export_is_rejected_before_backup(self):
        for mutate in (lambda p: p['tables'].pop('receiving_receipt_cache'),
                       lambda p: p.update(revisions=['older']), lambda p: p.update(format='legacy')):
            payload = self.export()
            mutate(payload)
            with self.assertRaises(ValueError):
                with self.engine.begin() as c:
                    restore(c, payload, self.tmp.name)
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_routes_require_admin_and_environment_opt_in_and_import_upload(self):
        app = FastAPI()
        app.include_router(admin.router)
        def db_session():
            with Session(self.engine) as db:
                yield db
        app.dependency_overrides[get_db] = db_session
        client = TestClient(app)
        self.assertEqual(client.get('/admin/backup').status_code, 401)
        app.dependency_overrides[get_current_user] = lambda: User(role='user')
        self.assertEqual(client.get('/admin/backup').status_code, 403)
        app.dependency_overrides[get_current_user] = lambda: User(role='admin')
        response = client.get('/admin/backup')
        self.assertEqual(response.status_code, 200)
        with patch.object(settings, 'database_import_enabled', False):
            self.assertEqual(client.post('/admin/import', files={'file': ('export.json', response.content)}).status_code, 403)
        # Mimic the real auth dependency holding a read lock on users.
        def authenticated_user(db=Depends(get_db)):
            return db.get(User, 40)
        app.dependency_overrides[get_current_user] = authenticated_user
        with patch.object(settings, 'database_import_enabled', True), patch.object(settings, 'invoice_dir', self.tmp.name):
            imported = client.post('/admin/import', files={'file': ('export.json', response.content)})
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertGreater(imported.json()['rows_imported'], 0)
