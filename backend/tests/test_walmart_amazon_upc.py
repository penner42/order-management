"""Exercise product lookups through automated imports and isolated persistence."""
import json
import unittest

from playwright.async_api import async_playwright
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.browser_automation.amazon_import import _normalize_with_upcs
from app.browser_automation.walmart_import import _normalize_detail
from app.bfmr_orders import capture_import_metadata
from app.database import Base
from app.models import Item, Order
from app.models.item import ItemStatus
from app.routers.store_imports import _apply_items_and_shipments


class WalmartAmazonUpcTests(unittest.IsolatedAsyncioTestCase):
    async def test_lookup_save_and_reimport_for_both_stores(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                for store, sku in [('amazon', 'B012345678'), ('walmart', '123456')]:
                    with self.subTest(store=store):
                        page = await browser.new_page()
                        origin = f'https://www.{store}.com'
                        upc = '000012345678'
                        html = (f'<input id="ASIN" value="{sku}"><table id="productDetails_detailBullets_sections1">'
                                f'<tr><th>UPC</th><td>{upc}</td></tr></table>' if store == 'amazon' else
                                '<script id="__NEXT_DATA__">' + json.dumps({'props': {'product': {
                                    'usItemId': sku, 'upc': upc}}}) + '</script>')
                        async def route(request):
                            url = request.request.url
                            if '/dp/' in url or '/ip/' in url:
                                await request.fulfill(status=200, content_type='text/html', body=html)
                            else:
                                await request.fulfill(status=200, content_type='text/html', body='<html></html>')
                        await page.route(origin + '/**', route)
                        await page.goto(origin + '/orders')
                        if store == 'amazon':
                            body = await _normalize_with_upcs(page, {'orderId': 'O1', 'items': [
                                {'asin': sku, 'name': 'Product', 'quantity': 2, 'unitPrice': 10},
                            ]}, page.url, None)
                        else:
                            body = await _normalize_detail(page, {'order': {'id': 'O1', 'groups_2101': [
                                {'items': [{'id': 'L1', 'quantity': 2, 'productInfo': {
                                    'usItemId': sku, 'name': 'Product'}}]},
                            ]}}, page.url)
                        self.assertEqual(body['items'][0]['upc'], upc)
                        self.assertEqual(body['source'], 'browser-automation')
                        engine = create_engine('sqlite:///:memory:')
                        Base.metadata.create_all(engine)
                        try:
                            with sessionmaker(bind=engine)() as db:
                                order = Order(store_id=1, store_order_number='O1')
                                db.add(order)
                                db.flush()
                                _apply_items_and_shipments(db, body, store, order,
                                                          external_order_id='O1', existing_order=False)
                                capture_import_metadata(db, order, body, store)
                                db.commit()
                                db.expire_all()
                                item = db.query(Item).one()
                                self.assertEqual(item.upc, upc)
                                item.status = ItemStatus.SHIPPED
                                item.receipt_upc = '999999999999'
                                for incoming in [None, '', '999999999999']:
                                    body['items'][0]['upc'] = incoming
                                    capture_import_metadata(db, order, body, store)
                                    db.commit()
                                    self.assertEqual(item.upc, upc)
                                    self.assertEqual(item.receipt_upc, '999999999999')
                                    self.assertEqual(item.status, ItemStatus.SHIPPED)
                                item.upc = None
                                body['items'][0]['upc'] = upc
                                capture_import_metadata(db, order, body, store)
                                db.commit()
                                self.assertEqual(item.upc, upc)
                        finally:
                            engine.dispose()
                        await page.close()
            finally:
                await browser.close()
