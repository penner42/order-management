"""Payment confirmation row locks tested in a disposable PostgreSQL database."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import unittest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.config import settings
from app.database import Base
from app.models import BuyingGroup, Store, Order, Item, Payment, PaymentCoverage
from app.models.item import ItemStatus
from app.payment_providers import PaymentFetch
from app.payment_review import refresh, confirm_review
from tests.test_payment_review import payment, receipt


@unittest.skipUnless(settings.database_url.startswith("postgresql"), "requires Docker PostgreSQL")
class PaymentReviewConcurrencyTests(unittest.TestCase):
    def test_same_review_cannot_be_confirmed_twice(self):
        admin = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
        name = "test_payment_review_" + uuid4().hex
        engine = None
        with admin.connect() as conn: conn.execute(text(f'CREATE DATABASE "{name}"'))
        try:
            engine = create_engine(admin.url.set(database=name))
            Base.metadata.create_all(engine)
            sessions = sessionmaker(bind=engine)
            with sessions() as db:
                group, store = BuyingGroup(name="Test", api_framework="parsefile"), Store(name="Test")
                db.add_all([group, store]); db.flush()
                order = Order(store_id=store.id, buying_group_id=group.id)
                db.add(order); db.flush()
                db.add(Item(order_id=order.id, price_sold=100, quantity=1, receipt_id="r1", status=ItemStatus.SCANNED))
                db.commit()
                review = refresh(db, group.id, PaymentFetch([payment()], [receipt()]), "parsefile")["reviews"][0]
            barrier = Barrier(2)
            def attempt(_):
                with sessions() as db:
                    barrier.wait(timeout=10)
                    try:
                        confirm_review(db, review["id"], review["version"], review["fingerprint"])
                        return 200
                    except HTTPException as exc:
                        db.rollback()
                        return exc.status_code
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(sorted(pool.map(attempt, range(2))), [200, 409])
            with sessions() as db:
                self.assertEqual(db.query(Payment).count(), 1)
                self.assertEqual(db.query(PaymentCoverage).count(), 1)
        finally:
            if engine is not None: engine.dispose()
            with admin.connect() as conn: conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
            admin.dispose()
