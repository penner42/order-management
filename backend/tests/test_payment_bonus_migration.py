"""Payment refresh works with the historical schema and the repaired default."""
import importlib.util
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base
from app.models import BuyingGroup, Payment
from app.payment_evidence import PaymentEvidence, PaymentFetch
from app.payment_review import refresh


@unittest.skipUnless(settings.database_url.startswith("postgresql"), "requires Docker PostgreSQL")
class PaymentBonusMigrationTests(unittest.TestCase):
    def test_refresh_and_migration_preserve_bonus_on_historical_schema(self):
        admin = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
        name = "test_payment_bonus_" + uuid4().hex
        engine = None
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        try:
            engine = create_engine(admin.url.set(database=name))
            Base.metadata.create_all(engine)
            # Migration 046 removed this default; metadata-created test DBs hide that.
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE payments ALTER COLUMN payment_bonus DROP DEFAULT"))
            with sessionmaker(bind=engine, autoflush=False)() as db:
                group = BuyingGroup(name="Bonus regression", api_framework="parsefile")
                db.add(group)
                db.flush()
                group_id = group.id
                legacy = Payment(buying_group_id=group_id, payment_id="legacy",
                                 payment_requested_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
                                 payment_bonus=Decimal("7.50"))
                db.add(legacy)
                db.commit()
                legacy_id = legacy.id
                evidence = PaymentFetch([PaymentEvidence(external_id="imported", amount="100",
                    created_at=datetime(2026, 1, 10, tzinfo=timezone.utc), status="PAID")], [])
                refresh(db, group_id, evidence, "parsefile", automate=True)
                imported = db.query(Payment).filter_by(payment_id="imported").one()
                self.assertEqual(imported.payment_bonus, Decimal("0"))
                self.assertEqual(imported.source_amount, Decimal("100"))

            path = Path(__file__).parents[1] / "alembic/versions/083_payment_bonus_default.py"
            spec = importlib.util.spec_from_file_location("bonus_migration", path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            with engine.begin() as connection:
                with patch.object(migration, "op", Operations(MigrationContext.configure(connection))):
                    migration.upgrade()
                    bonus = connection.execute(text("""INSERT INTO payments
                        (buying_group_id, payment_id, payment_requested_at)
                        VALUES (:group_id, 'default-check', now()) RETURNING payment_bonus"""),
                        {"group_id": group_id}).scalar_one()
                    self.assertEqual(bonus, Decimal("0"))
                    migration.downgrade()
                    self.assertIsNone(connection.execute(text("""SELECT column_default
                        FROM information_schema.columns WHERE table_schema='public'
                        AND table_name='payments' AND column_name='payment_bonus'""")).scalar_one())
                    migration.upgrade()
                self.assertEqual(connection.execute(text(
                    "SELECT payment_bonus FROM payments WHERE id=:id"),
                    {"id": legacy_id}).scalar_one(), Decimal("7.50"))
                self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM payments")).scalar_one(), 3)
        finally:
            if engine is not None:
                engine.dispose()
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
            admin.dispose()
