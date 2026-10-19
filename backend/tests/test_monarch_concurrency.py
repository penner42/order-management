"""Real row-lock coverage on a disposable PostgreSQL database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from threading import Barrier
import unittest
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base
from app.models import (User, BuyingGroup, Store, Order, Item, BrowserProfile, Payment, PaymentLineItem,
                        MonarchConnection, MonarchDeposit, MonarchConfirmation, MonarchPaymentReceipt)
from app.monarch import confirm_receipt, payment_fingerprint, undo_receipt
from app.schemas.monarch import MonarchConfirmRequest


@unittest.skipUnless(settings.database_url.startswith("postgresql"), "requires PostgreSQL in Docker")
class MonarchConcurrencyTests(unittest.TestCase):
    def test_competing_confirmations_cannot_reuse_a_deposit_or_payment(self):
        admin = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
        name = "test_monarch_" + uuid4().hex
        engine = None
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        try:
            engine = create_engine(admin.url.set(database=name))
            Base.metadata.create_all(engine)
            sessions = sessionmaker(bind=engine)
            with sessions() as db:
                user = User(username="reviewer", role="user")
                group, store = BuyingGroup(name="Sender"), Store(name="Store")
                db.add_all([user, group, store]); db.flush()
                order = Order(store_id=store.id, buying_group_id=group.id)
                profile = BrowserProfile(integration="monarch", retailer="monarch", status="ready")
                db.add_all([order, profile]); db.flush()
                item = Item(order_id=order.id, price_sold=30, quantity=1)
                payment = Payment(buying_group_id=group.id, payment_requested_at=datetime(2026, 10, 1, 12, tzinfo=timezone.utc))
                db.add_all([item, payment]); db.flush()
                db.add(PaymentLineItem(payment_id=payment.id, item_id=item.id, amount=30))
                db.add(MonarchConnection(id=1, browser_profile_id=profile.id, selected_account_ids=["bank"]))
                deposits = [MonarchDeposit(transaction_id=f"tx{i}", account_id="bank", account_name="Bank", amount=30,
                    posted_date=date(2026, 10, 10), merchant="Sender", description="Sender", pending=False) for i in range(2)]
                db.add_all(deposits); db.commit()
                user_id, payment_id = user.id, payment.id
                deposit_ids = [d.id for d in deposits]
                selection = MonarchConfirmRequest(revision=1, payments=[{"id": payment_id, "fingerprint": payment_fingerprint(payment)}])

            for ids in ([deposit_ids[0], deposit_ids[0]], deposit_ids):
                barrier = Barrier(2)
                def attempt(deposit_id):
                    with sessions() as db:
                        barrier.wait(timeout=10)
                        try:
                            confirm_receipt(db, deposit_id, selection, user_id)
                            return 200
                        except HTTPException as exc:
                            db.rollback()
                            return exc.status_code
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(attempt, ids))
                self.assertEqual(sorted(results), [200, 409])
                with sessions() as db:
                    self.assertEqual(db.query(MonarchPaymentReceipt).count(), 1)
                    self.assertEqual(db.query(MonarchConfirmation).filter(MonarchConfirmation.active_deposit_id.isnot(None)).count(), 1)
                    receipt = db.get(MonarchPaymentReceipt, payment_id)
                    undo_receipt(db, receipt.confirmation_id, user_id)
                    self.assertIsNone(db.get(Payment, payment_id).payment_received_at)
        finally:
            if engine is not None:
                engine.dispose()
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
            admin.dispose()
