"""Cover Amazon summary capture and download with isolated browser and database data."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from playwright.async_api import async_playwright
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.browser_automation.paths import amazon_script_paths
from app.config import settings
from app.database import Base
from app.models import Order, Store, User
from app.routers.orders import get_order_invoice
from app.routers.store_imports import apply_store_order_payload, compute_order_diff_bulk
from app.schemas.store_import import BulkStoreOrderDiffRequest, StoreOrderImportPayload


ORDER_ID = "111-1111111-1111111"
DETAIL_URL = f"https://www.amazon.com/your-orders/order-details?orderID={ORDER_ID}"
SUMMARY_PATH = f"/gp/css/summary/print.html?orderID={ORDER_ID}"
SUMMARY_URL = f"https://www.amazon.com{SUMMARY_PATH}"


class AmazonInvoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        playwright = await async_playwright().start()
        self.addAsyncCleanup(playwright.stop)
        browser = await playwright.chromium.launch()
        self.addAsyncCleanup(browser.close)
        self.page = await browser.new_page()
        await self.page.route(DETAIL_URL, lambda route: route.fulfill(
            content_type="text/html", body="<button aria-haspopup='true'>Invoice</button>",
        ))
        await self.page.goto(DETAIL_URL)
        for path in amazon_script_paths():
            await self.page.add_script_tag(content=path.read_text())

    async def capture(self):
        return await self.page.evaluate(
            """async id => {
                const invoice = await OrderManagerAmazonDom.capturePrintableOrderSummary(document, location.href, id)
                if (!invoice) return null
                return OrderManagerAmazon.normalizeAmazonOrderPayload({ orderId: id, ...invoice }, location.href)
            }""",
            ORDER_ID,
        )

    async def test_html_summary_from_invoice_menu_is_rendered_and_can_backfill_existing_order(self):
        await self.page.evaluate("""path => {
            document.querySelector('button').onclick = () => setTimeout(() => {
                const link = document.createElement('a')
                link.href = path
                link.textContent = 'Printable Order Summary'
                document.body.append(link)
            }, 10)
        }""", SUMMARY_PATH)
        await self.page.route(SUMMARY_URL, lambda route: route.fulfill(
            content_type="text/html",
            body=f"<h1>Printable Order Summary</h1><p>Order # {ORDER_ID}</p><script>window.print()</script>",
        ))
        payload = StoreOrderImportPayload.model_validate(await self.capture())
        self.assertIn("Printable Order Summary", payload.invoiceHtml)
        self.assertNotIn("<script>", payload.invoiceHtml)
        self.assertTrue(self.apply_and_download(payload).startswith(b"%PDF-"))

    async def test_direct_pdf_is_preserved_and_wrong_order_summary_is_skipped(self):
        pdf = await self.page.pdf()
        await self.page.set_content(
            f'<a href="{SUMMARY_PATH}">Printable Order Summary</a>',
        )
        await self.page.route(SUMMARY_URL, lambda route: route.fulfill(
            content_type="text/html",
            body="<h1>Printable Order Summary</h1><p>Order # 222-2222222-2222222</p>",
        ))
        self.assertIsNone(await self.capture())
        await self.page.unroute(SUMMARY_URL)
        await self.page.route(SUMMARY_URL, lambda route: route.fulfill(
            content_type="application/pdf", body=pdf,
        ))
        payload = StoreOrderImportPayload.model_validate(await self.capture())
        self.assertEqual(self.apply_and_download(payload), pdf)

    def apply_and_download(self, payload):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        with TemporaryDirectory() as directory, patch.object(settings, "invoice_dir", directory), Session(engine) as db:
            user = User(id=1, username="amazon-invoice-test", role="user")
            order = Order(id=1, store_id=1, store_order_number=ORDER_ID)
            db.add_all([Store(id=1, name="Amazon"), user, order])
            db.commit()
            request = BulkStoreOrderDiffRequest(orders=[payload])
            diff = compute_order_diff_bulk(request, db, user).diffs[0]
            self.assertTrue(diff["invoice_to_save"])
            self.assertTrue(diff["has_changes"])

            self.assertEqual(apply_store_order_payload(db, payload, user, store_id=1), order.id)
            self.assertFalse(compute_order_diff_bulk(request, db, user).diffs[0]["invoice_to_save"])
            response = get_order_invoice(order.id, db, user)
            self.assertEqual(response.media_type, "application/pdf")
            return Path(response.path).read_bytes()
