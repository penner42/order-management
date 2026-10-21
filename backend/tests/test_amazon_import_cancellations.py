"""Exercise Amazon DOM capture, both normalizers, and applying cancellations."""
import unittest

from playwright.async_api import async_playwright
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.browser_automation.amazon_import import _normalize_order
from app.browser_automation.paths import amazon_script_paths
from app.database import Base
from app.models import Item, Order
from app.models.item import ItemStatus
from app.routers.store_imports import _apply_items_and_shipments


class AmazonImportCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.page = await self.browser.new_page()
        for path in amazon_script_paths():
            await self.page.add_script_tag(content=path.read_text())

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def normalize_both(self, raw):
        extension = await self.page.evaluate(
            "raw => OrderManagerAmazon.normalizeAmazonOrderPayload(raw, 'https://www.amazon.com/', null)",
            raw,
        )
        automated = _normalize_order(raw, 'https://www.amazon.com/', None)
        for key in ('externalOrder', 'items', 'shipments'):
            self.assertEqual(extension[key], automated[key], key)
        return extension, automated

    def assert_applied(self, payload, expected):
        engine = create_engine('sqlite:///:memory:')
        try:
            Base.metadata.create_all(engine)
            with Session(engine) as db:
                order = Order(store_id=1, store_order_number='111-1111111-1111111')
                db.add(order)
                db.flush()
                for existing in (False, True):
                    _apply_items_and_shipments(db, payload, 'amazon', order,
                                              external_order_id=order.store_order_number,
                                              existing_order=existing)
                    db.commit()
                    items = db.query(Item).filter_by(order_id=order.id).all()
                    self.assertEqual({i.description: i.status.value for i in items}, expected)
                    for item in items:
                        self.assertEqual(item.canceled_at is not None,
                                         item.status == ItemStatus.CANCELED)
        finally:
            engine.dispose()

    async def test_dom_mixed_shipments_cancel_only_their_own_items(self):
        for statuses in [('Canceled', 'Delivered'), ('Delivered', 'Canceled')]:
            with self.subTest(statuses=statuses):
                blocks = ''.join(
                    f'<div class="shipment"><div class="yohtmlc-shipment-status-primaryText">{status}</div>'
                    f'<div class="yohtmlc-item"><a href="https://www.amazon.com/dp/B00000000{index}">'
                    f'Product {index}</a><span class="a-color-price">$10.00</span></div></div>'
                    for index, status in enumerate(statuses)
                )
                await self.page.set_content(f'<div id="orderDetails">Order #111-1111111-1111111{blocks}</div>')
                raw = await self.page.evaluate(
                    "() => OrderManagerAmazonDom.parseOrderDetailPage(document, 'https://www.amazon.com/your-orders/order-details?orderID=111-1111111-1111111')"
                )
                self.assertEqual(raw['status'], 'partially canceled')
                self.assertEqual(len(raw['items']), 2)
                for payload in await self.normalize_both(raw):
                    self.assertEqual(payload['externalOrder']['statusType'], 'partially canceled')
                    self.assert_applied(payload, {
                        f'Product {index}': 'canceled' if status == 'Canceled' else 'purchased'
                        for index, status in enumerate(statuses)
                    })

    async def test_raw_status_does_not_spread_cancellation_to_other_shipments(self):
        for active_status in ('Shipped', 'Delivered', 'Preparing for shipment', None):
            with self.subTest(active_status=active_status):
                raw = {
                    'orderId': '111-1111111-1111111', 'status': 'Canceled',
                    'items': [
                        {'name': 'Canceled product', 'shipmentId': 'cancel'},
                        {'name': 'Active product', 'shipmentId': 'active'},
                        {'name': 'Unmatched product'},
                    ],
                    'shipments': [
                        {'shipmentId': 'cancel', 'status': {'message': 'Canceled'}},
                        {'shipmentId': 'active', 'status': {'message': active_status}},
                    ],
                }
                for payload in await self.normalize_both(raw):
                    self.assertEqual(payload['externalOrder']['statusType'], 'partially canceled')
                    self.assert_applied(payload, {
                        'Canceled product': 'canceled', 'Active product': 'purchased',
                        'Unmatched product': 'purchased',
                    })

    async def test_whole_order_cancellation_still_cancels_all_items(self):
        for shipments in ([], [{'shipmentId': 'one', 'status': {'message': 'Cancelled'}}]):
            with self.subTest(shipments=shipments):
                for payload in await self.normalize_both({
                    'orderId': '111-1111111-1111111', 'status': 'Canceled',
                    'items': [{'name': 'Product one'}, {'name': 'Product two'}],
                    'shipments': shipments,
                }):
                    self.assert_applied(payload, {'Product one': 'canceled', 'Product two': 'canceled'})

    async def test_unrecognized_shipment_layout_does_not_cancel_all_items(self):
        await self.page.set_content('''
            <div id="orderDetails">Order #111-1111111-1111111
              <div class="yohtmlc-shipment-status-primaryText">Canceled</div>
              <div class="yohtmlc-item"><a href="https://www.amazon.com/dp/B000000001">Product one</a></div>
              <div class="od-status-message">Delivered</div>
              <div class="yohtmlc-item"><a href="https://www.amazon.com/dp/B000000002">Product two</a></div>
            </div>
        ''')
        raw = await self.page.evaluate(
            "() => OrderManagerAmazonDom.parseOrderDetailPage(document, 'https://www.amazon.com/your-orders/order-details?orderID=111-1111111-1111111')"
        )
        self.assertEqual(raw['status'], 'partially canceled')
        for payload in await self.normalize_both(raw):
            self.assert_applied(payload, {'Product one': 'purchased', 'Product two': 'purchased'})
