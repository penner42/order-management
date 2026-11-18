"""Single-order Costco automation with mocked retailer responses."""
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.browser_automation import costco_import as costco


class CostcoSingleOrderTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_capture_uses_session_route_and_stages_screenshot(self):
        page = SimpleNamespace(url="https://www.costco.com/myaccount/#/app/session-id/account")
        page.evaluate = AsyncMock()
        page.wait_for_function = AsyncMock()
        detail = {"data": {"getOrderDetails": {"orderNumber": "ORDER-1"}}}
        body = {"store": "costco", "externalOrder": {"id": "ORDER-1"}}
        page.evaluate.side_effect = [body, {"invoiceHtml": "invoice"}, [body]]
        responses = SimpleNamespace(
            clear_orders=AsyncMock(),
            clear_details=AsyncMock(),
            wait_for_details=AsyncMock(return_value=(detail, None)),
            wait_for_orders=AsyncMock(),
            detach=MagicMock(),
        )

        async def screenshot(page, payload, profile_id):
            payload["screenshotToken"] = "image"
            self.assertEqual(profile_id, 42)

        with ExitStack() as mocks:
            mocks.enter_context(patch.object(costco.session_manager, "ensure_session", return_value=SimpleNamespace(page=page)))
            mocks.enter_context(patch.object(costco, "establish_costco_session", return_value=True))
            mocks.enter_context(patch.object(costco, "_CostcoGraphqlCapture", return_value=responses))
            mocks.enter_context(patch.object(costco, "_extract_app_id", return_value="session-id"))
            mocks.enter_context(patch.object(costco, "_inject_costco_lib"))
            navigate = mocks.enter_context(patch.object(costco, "_costco_spa_navigate"))
            mocks.enter_context(patch.object(costco, "capture_order_screenshot", side_effect=screenshot))
            result = await costco.run_costco_import(42, order_ids=["ORDER-1"], single_order=True)

        navigate.assert_awaited_once_with(page, "https://www.costco.com/myaccount/#/app/session-id/orderdetails/ORDER-1")
        self.assertTrue(responses.wait_for_details.await_args.kwargs["require_order_number"])
        responses.wait_for_orders.assert_not_awaited()
        self.assertEqual(result, [{
            "store": "costco", "externalOrder": {"id": "ORDER-1"},
            "source": "browser-automation", "screenshotToken": "image",
            "invoiceHtml": "invoice",
        }])
        responses.detach.assert_called_once()

    async def test_targeted_history_fallback_requires_matching_detail(self):
        for returned_number in ("ORDER-1", "OTHER"):
            with self.subTest(returned_number=returned_number):
                page = SimpleNamespace(url="https://www.costco.com/myaccount/#/app/session-id/ordersandpurchases")
                body = {"store": "costco", "externalOrder": {"id": "ORDER-1"}}
                page.evaluate = AsyncMock(side_effect=lambda _source, orders: orders)
                page.wait_for_function = AsyncMock()
                history = {"data": {"getOnlineOrders": [{"bcOrders": [
                    {"orderNumber": "OTHER", "orderHeaderId": "a"},
                    {"orderNumber": "ORDER-1", "orderHeaderId": "b"},
                ]}]}}
                responses = SimpleNamespace(
                    clear_orders=AsyncMock(),
                    wait_for_orders=AsyncMock(return_value=(history, None)),
                    detach=MagicMock(),
                )
                detail = {"data": {"getOrderDetails": {"orderNumber": returned_number}}}
                with ExitStack() as mocks:
                    mocks.enter_context(patch.object(costco.session_manager, "ensure_session", return_value=SimpleNamespace(page=page)))
                    mocks.enter_context(patch.object(costco, "establish_costco_session", return_value=True))
                    mocks.enter_context(patch.object(costco, "_CostcoGraphqlCapture", return_value=responses))
                    mocks.enter_context(patch.object(costco, "_capture_single_order_direct", side_effect=TimeoutError))
                    mocks.enter_context(patch.object(costco, "_ensure_orders_and_purchases_route"))
                    capture = mocks.enter_context(patch.object(costco, "_capture_order_details", return_value=detail))
                    mocks.enter_context(patch.object(costco, "_normalize_and_merge", return_value=[body]))
                    mocks.enter_context(patch.object(costco, "capture_order_screenshot"))
                    invoice = mocks.enter_context(patch.object(
                        costco, "_try_capture_invoice", return_value={"invoiceHtml": "invoice"},
                    ))
                    result = await costco.run_costco_import(42, order_ids=["ORDER-1"], single_order=True)

                capture.assert_awaited_once_with(page, responses, order_number="ORDER-1", header_id="b", require_order_number=True)
                if returned_number == "ORDER-1":
                    invoice.assert_awaited_once_with(page, "ORDER-1")
                    self.assertEqual(result[0]["invoiceHtml"], "invoice")
                else:
                    invoice.assert_not_awaited()
                    self.assertEqual(result, [])
