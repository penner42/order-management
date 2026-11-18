"""Exercise Walmart invoice capture, replacement, and download with isolated data."""
import re
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from playwright.async_api import async_playwright
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.browser_automation.walmart_import import _ensure_orders_hook, _try_capture_invoice_html
from app.config import settings
from app.database import Base
from app.models import Order, Store, User
from app.routers.orders import get_order_invoice
from app.routers.store_imports import apply_store_order_payload
from app.schemas.order import OrderRead
from app.schemas.store_import import StoreOrderImportPayload


class WalmartInvoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_reimport_restores_missing_pdf_from_walmarts_print_layout(self):
        order_number = "123456789012345"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                url = f"https://www.walmart.com/orders/{order_number}"
                await page.route(url, lambda route: route.fulfill(content_type="text/html; charset=utf-8", body="""
                  <style>@media print {
                    [data-testid="print-invoice-layout"] { border: 1px solid #ccc; padding: 36px; }
                  }</style>
                  <main>
                    <nav>Account sidebar</nav>
                    <div data-testid="digital-invoice"><button>Print invoice</button></div>
                    <div class="print-bill-body">
                      <h1>Order# 1234567-89012345</h1>
                      <button aria-expanded="false" aria-label="Show items">2 items</button>
                      <aside>Delivery status and account help</aside>
                    </div>
                    <div class="print-portal-root" style="display:none">
                      <div data-testid="print-invoice-layout">Invoice for Order# 1234567-89012346</div>
                      <div data-testid="print-invoice-layout">
                        <header><h1>Walmart Invoice</h1></header>
                        <p>Order# 1234567-89012345</p>
                        <p>Buyer</p>
                        <p>First product · Qty 1 · $10</p>
                        <p>Second product · Qty 2 · $30</p>
                        <p>Total $40</p>
                      </div>
                    </div>
                  </main>
                """))
                await page.goto(url)
                await _ensure_orders_hook(page)
                self.assertIsNone(await _try_capture_invoice_html(page, order_number + "3", timeout_ms=150))
                html = await _try_capture_invoice_html(page, order_number)
                self.assertEqual(await page.get_by_role("button", name="Show items")
                                 .get_attribute("aria-expanded"), "false")
            finally:
                await browser.close()

        self.assertIsNotNone(html)
        self.assertIn("Walmart Invoice", html)
        self.assertIn("First product · Qty 1 · $10", html)
        self.assertIn("Second product · Qty 2 · $30", html)
        self.assertIn("Total $40", html)
        self.assertNotIn("1234567-89012346", html)
        self.assertNotIn("Account sidebar", html)
        self.assertNotIn("Delivery status", html)

        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        with TemporaryDirectory() as directory, patch.object(settings, "invoice_dir", directory), Session(engine) as db:
            store = Store(id=1, name="Walmart")
            user = User(id=1, username="invoice-test", role="user")
            order = Order(id=1, store_id=store.id, store_order_number=order_number, invoice_pdf_path="1.pdf")
            db.add_all([store, user, order])
            db.commit()
            self.assertFalse(OrderRead.model_validate(order).has_invoice)
            payload = StoreOrderImportPayload(store="walmart", externalOrder={"id": order_number}, invoiceHtml=html)

            self.assertEqual(apply_store_order_payload(db, payload, user, store_id=store.id), order.id)

            self.assertTrue(OrderRead.model_validate(order).has_invoice)
            response = get_order_invoice(order.id, db, user)
            pdf = Path(response.path).read_bytes()
            self.assertEqual(response.media_type, "application/pdf")
            self.assertTrue(pdf.startswith(b"%PDF-"))
            self.assertEqual(len(re.findall(rb"/Type\s*/Page\b", pdf)), 1)
