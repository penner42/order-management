"""Exercise the automated browser bridge with the shared extension normalizer."""
import unittest
import json
from playwright.async_api import async_playwright
from app.browser_automation.costco_import import _normalize_and_merge, _inject_costco_lib


class CostcoImportUpcTests(unittest.IsolatedAsyncioTestCase):
    async def test_product_fetch_enriches_exact_item_and_tolerates_missing_page(self):
        product = {'id': '4000308504', 'upc': '499996263740',
                   'childCatalogData': [{'id': '1611943', 'upc': '195949689604'}]}
        flight = '1:' + json.dumps({'productData': product})
        html = '<script>self.__next_f.push(' + json.dumps([1, flight]) + ')</script>'
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                async def route(request):
                    if request.request.url.endswith('/p/-/1611943'):
                        await request.fulfill(status=200, content_type='text/html', body=html)
                    elif '/p/-/' in request.request.url:
                        await request.fulfill(status=404, content_type='text/html', body='Not found')
                    else:
                        await request.fulfill(status=200, content_type='text/html', body='<html></html>')
                await page.route('https://www.costco.com/**', route)
                await page.goto('https://www.costco.com/myaccount/')
                await _inject_costco_lib(page)
                orders = [{'items': [{'externalSku': '1611943'}, {'externalSku': '1889168'}]}]
                enriched = await page.evaluate(
                    'orders=>OrderManagerCostco.enrichCostcoOrdersWithProductUpcs(orders)', orders)
                self.assertEqual(enriched[0]['items'][0]['upc'], '195949689604')
                self.assertNotIn('upc', enriched[0]['items'][1])
            finally:
                await browser.close()

    async def test_automated_detail_enrichment_matches_extension(self):
        line = {'lineItemId': 'L1', 'itemNumber': '1234567', 'itemDescription': 'Product', 'quantity': 1}
        orders = {'data': {'getOnlineOrders': [{'bcOrders': [
            {'orderNumber': 'O1', 'orderLineItems': [line]}]}]}}
        details = {'O1': {'data': {'getOrderDetails': {'orderNumber': 'O1', 'uSTaxTotal1': 12.34,
                   'shipToAddress': [{'orderLineItems': [{**line, 'upc': '000012345678'}]}]}}}}
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                automated = await _normalize_and_merge(page, orders, None, details)
                extension = await page.evaluate('''({orders, details}) => {
                    const c = globalThis.OrderManagerCostco;
                    const result = c.normalizeCostcoOrdersGraphqlPayload(orders);
                    c.mergeCostcoOrderDetailsIntoNormalizedOrders(result, details);
                    return result;
                }''', {'orders': orders, 'details': details})
                self.assertEqual(automated[0]['source'], 'browser-automation')
                self.assertEqual(automated[0]['items'], extension[0]['items'])
                self.assertEqual(automated[0]['totals']['tax'], 12.34)
                self.assertEqual(automated[0]['items'][0]['upc'], '000012345678')
            finally:
                await browser.close()
