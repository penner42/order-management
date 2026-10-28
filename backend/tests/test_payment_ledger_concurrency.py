"""Source ledger edits serialize under real PostgreSQL row locks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from uuid import uuid4
import unittest
from fastapi import HTTPException
from sqlalchemy import create_engine,text
from sqlalchemy.orm import sessionmaker
from app.config import settings
from app.database import Base
from app.models import BuyingGroup,Payment,PaymentCoverage
from app.payment_evidence import PaymentFetch,PaymentEvidence
from app.payment_review import refresh
from app.payment_ledger import adjust,AllocationEdit


@unittest.skipUnless(settings.database_url.startswith('postgresql'),'requires Docker PostgreSQL')
class LedgerConcurrencyTests(unittest.TestCase):
    def test_two_adjustments_cannot_overwrite_the_same_version(self):
        admin=create_engine(settings.database_url,isolation_level='AUTOCOMMIT')
        name='test_source_ledger_'+uuid4().hex
        engine=None
        with admin.connect() as c:c.execute(text(f'CREATE DATABASE "{name}"'))
        try:
            engine=create_engine(admin.url.set(database=name));Base.metadata.create_all(engine)
            sessions=sessionmaker(bind=engine,autoflush=False)
            with sessions() as db:
                group=BuyingGroup(name='Example',api_framework='parsefile');db.add(group);db.commit()
                refresh(db,group.id,PaymentFetch([PaymentEvidence(external_id='p1',amount='100',status='PAID',
                    created_at=datetime(2026,1,5,tzinfo=timezone.utc))],[]),'parsefile',automate=True)
                p=db.query(Payment).one();pid,version=p.id,p.allocation_version
            barrier=Barrier(2)
            def attempt(amount):
                with sessions() as db:
                    barrier.wait(timeout=10)
                    try:
                        adjust(db,pid,AllocationEdit(version=version,historical_amount=amount,note='Verified old purchases'))
                        return 200
                    except HTTPException as e:
                        db.rollback();return e.status_code
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(sorted(pool.map(attempt,['90','100'])),[200,409])
            with sessions() as db:
                self.assertEqual(db.query(Payment).count(),1)
                self.assertEqual(db.query(PaymentCoverage).count(),0)
                self.assertEqual(db.get(Payment,pid).source_amount,100)
        finally:
            if engine is not None:engine.dispose()
            with admin.connect() as c:c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
            admin.dispose()
