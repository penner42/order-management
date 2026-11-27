import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from playwright.async_api import Error as PlaywrightError

from app.browser_automation import walmart_import
from app.browser_automation.common import LoginRequiredError
from app.browser_automation.session_manager import WALMART_ORDERS_URL


class WalmartImportRetryTests(unittest.IsolatedAsyncioTestCase):
    signin_url = "https://identity.walmart.com/authorize"

    def setUp(self):
        self.page = SimpleNamespace(url="about:blank", wait_for_timeout=AsyncMock())
        self.ensure_session = self.enterContext(
            patch.object(walmart_import.session_manager, "ensure_session", return_value=SimpleNamespace(page=self.page))
        )
        self.enterContext(patch.object(walmart_import.asyncio, "sleep"))

    async def test_full_check_retries_when_order_history_redirects(self):
        async def goto(url, **kwargs):
            self.page.url = url

        async def read_order_numbers(page, max_pages):
            if self.page.goto.await_count == 1:
                page.url = self.signin_url
                raise PlaywrightError("Execution context was destroyed by navigation")
            return []

        self.page.goto = AsyncMock(side_effect=goto)
        with patch.object(walmart_import, "_read_order_numbers", side_effect=read_order_numbers):
            self.assertEqual(await walmart_import.run_walmart_import(1), [])

        self.assertEqual([call.args[0] for call in self.page.goto.await_args_list], [WALMART_ORDERS_URL] * 2)
        self.ensure_session.assert_awaited_once()

    async def test_shipment_check_retries_only_the_affected_order(self):
        first_url = f"{WALMART_ORDERS_URL}/123"
        second_url = f"{WALMART_ORDERS_URL}/456"

        async def goto(url, **kwargs):
            self.page.url = self.signin_url if self.page.goto.await_count == 2 else url

        self.page.goto = AsyncMock(side_effect=goto)
        orders = [{"externalOrder": {"id": number}} for number in ("123", "456")]
        on_order = Mock()
        with (
            patch.object(walmart_import, "_read_order_detail", side_effect=orders),
            patch.object(walmart_import, "_try_capture_invoice_html", return_value=None),
            patch.object(walmart_import, "capture_order_screenshot"),
        ):
            self.assertEqual(await walmart_import.run_walmart_import(
                1, order_ids=["123", "456"], on_order=on_order,
            ), orders)

        self.assertEqual([call.args[0] for call in self.page.goto.await_args_list], [first_url, second_url, second_url])
        self.assertEqual([call.args[0] for call in on_order.call_args_list], orders)
        self.ensure_session.assert_awaited_once()

    async def test_persistent_signin_redirect_requires_login_after_one_retry(self):
        async def goto(url, **kwargs):
            self.page.url = self.signin_url

        self.page.goto = AsyncMock(side_effect=goto)
        with patch.object(walmart_import, "_read_order_detail") as read_detail:
            with self.assertRaisesRegex(LoginRequiredError, "Walmart session requires login"):
                await walmart_import.run_walmart_import(1, order_ids=["123"])

        self.assertEqual(self.page.goto.await_count, 2)
        read_detail.assert_not_awaited()
