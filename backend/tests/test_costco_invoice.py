"""Costco print invoice download through the storefront and extension bridge."""
from base64 import b64decode
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import quote

from playwright.async_api import async_playwright
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.browser_automation.costco_import import _inject_costco_lib, _try_capture_invoice
from app.browser_automation.paths import costco_script_paths
from app.config import settings
from app.database import Base
from app.models import Order, Store, User
from app.routers.orders import get_order_invoice
from app.routers.store_imports import apply_store_order_payload, compute_order_diff
from app.schemas.store_import import StoreOrderImportPayload


ORDER_NUMBER = "1234567890"
ACCOUNT_URL = "https://www.costco.com/myaccount/"
DETAIL_URL = f"{ACCOUNT_URL}#/app/test/orderdetails/{ORDER_NUMBER}"
PRINT_PATH = f"/OrderDetailPrintView?orderId={ORDER_NUMBER}"
PRINT_URL = f"https://www.costco.com{PRINT_PATH}"
STYLESHEET_PATH = "/wcsstore/invoice.css"
STYLESHEET_URL = f"https://www.costco.com{STYLESHEET_PATH}"
STYLESHEET = """
  .order-fields { display: grid; grid-template-columns: 1fr 1fr; }
  @media print { body { visibility: hidden; } }
"""
HIDDEN_BODY_STYLE = "body { filter: alpha(opacity=0); opacity: 0; }"


class CostcoInvoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        playwright = await async_playwright().start()
        self.addAsyncCleanup(playwright.stop)
        browser = await playwright.chromium.launch()
        self.addAsyncCleanup(browser.close)
        self.page = await browser.new_page()
        await self.page.route(ACCOUNT_URL, lambda route: route.fulfill(
            content_type="text/html", body="<main>Order details</main>",
        ))
        await self.page.goto(DETAIL_URL)
        await _inject_costco_lib(self.page)

    async def test_print_page_preserves_styles_and_replaces_legacy_blank_pdf(self):
        await self.page.route(STYLESHEET_URL, lambda route: route.fulfill(
            content_type="text/css", body=STYLESHEET,
        ))
        await self.page.route(PRINT_URL, lambda route: route.fulfill(
            content_type="text/html",
            body=(
                f'<link rel="preload" as="style" href="{STYLESHEET_PATH}">'
                f"<style>{HIDDEN_BODY_STYLE}</style>"
                f'<h1>Invoice</h1><div class="order-fields">'
                f"<p>Order Number {ORDER_NUMBER}</p><p>Shipping Address</p></div><script>print()</script>"
            ),
        ))
        await self.page.evaluate("""path => setTimeout(() => {
            document.querySelector('main').insertAdjacentHTML('beforeend',
              `<a id="odprintInvoice" href="${path}" target="_self">Print Invoice</a>`)
        }, 50)""", PRINT_PATH)
        result = await _try_capture_invoice(self.page, ORDER_NUMBER)
        self.assertIn(f"Order Number {ORDER_NUMBER}", result["invoiceHtml"])
        self.assertNotIn("<script>", result["invoiceHtml"])
        self.assertEqual(self.page.url, DETAIL_URL)

        await self.page.add_style_tag(content=HIDDEN_BODY_STYLE)
        blank_pdf = await self.page.pdf()
        await self.page.set_content(result["invoiceHtml"], wait_until="load")
        columns = await self.page.locator(".order-fields p").evaluate_all(
            "fields => fields.map(field => field.getBoundingClientRect().x)"
        )
        self.assertLess(columns[0], columns[1])
        # The backend renderer opens its own browser, outside this page's mocked routes.
        offline_html = result["invoiceHtml"].replace(STYLESHEET_PATH, "data:text/css," + quote(STYLESHEET))
        payload = StoreOrderImportPayload(store="costco", externalOrder={"id": ORDER_NUMBER}, invoiceHtml=offline_html)
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        with TemporaryDirectory() as directory, patch.object(settings, "invoice_dir", directory), Session(engine) as db:
            user = User(id=1, username="costco-invoice-test", role="user")
            order = Order(id=1, store_id=1, store_order_number=ORDER_NUMBER, invoice_pdf_path="1.pdf")
            Path(directory, order.invoice_pdf_path).write_bytes(blank_pdf)
            db.add_all([Store(id=1, name="Costco"), user, order])
            db.commit()
            self.assertTrue(compute_order_diff(payload, db, user).diff["invoice_to_save"])
            self.assertEqual(apply_store_order_payload(db, payload, user, store_id=1), order.id)
            response = get_order_invoice(order.id, db, user)
            pdf = Path(response.path).read_bytes()
            self.assertIn(b"/Type /Font", pdf)
            self.assertIn(b"/MediaBox [0 0 792 612]", pdf)
            self.assertFalse(compute_order_diff(payload, db, user).diff["invoice_to_save"])

    async def test_extension_rejects_wrong_order_html_and_captures_native_pdf(self):
        await self.page.evaluate("""path => {
            document.querySelector('main').innerHTML = `<a href="${path}">Print Invoice</a>`
            window.chrome = {
              storage: { local: {} },
              runtime: { onMessage: { addListener: fn => (window.invoiceListener = fn) } },
            }
        }""", PRINT_PATH)
        script = costco_script_paths()[-1].with_name("bulk_capture.js")
        await self.page.add_script_tag(content=script.read_text())
        detail_url = await self.page.evaluate("""orderNumber => new Promise(resolve =>
            window.invoiceListener({store: 'costco', type: 'costcoGetOrderDetailsUrl', orderNumber}, {}, resolve)
        )""", ORDER_NUMBER)
        self.assertEqual(detail_url["url"], DETAIL_URL)
        await self.page.route(PRINT_URL, lambda route: route.fulfill(
            content_type="text/html", body="<h1>Invoice</h1><p>Order Number 9999999999</p>",
        ))
        self.assertIsNone(await self.extension_capture())

        pdf = await self.page.pdf()
        await self.page.unroute(PRINT_URL)
        await self.page.route(PRINT_URL, lambda route: route.fulfill(
            content_type="application/pdf", body=pdf,
        ))
        result = await self.extension_capture()
        self.assertEqual(b64decode(result["invoicePdfBase64"]), pdf)
        self.assertEqual(self.page.url, DETAIL_URL)

    async def extension_capture(self):
        return await self.page.evaluate("""orderNumber => new Promise(resolve =>
            window.invoiceListener({store: 'costco', type: 'costcoCaptureInvoice', orderNumber}, {}, resolve)
        )""", ORDER_NUMBER)
