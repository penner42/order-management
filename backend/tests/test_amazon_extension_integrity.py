"""Amazon extension regressions using synthetic pages, with no shared DB or profiles."""
import unittest

from playwright.async_api import async_playwright

from app.browser_automation.paths import amazon_script_paths


ORDER_ID = '111-1111111-1111111'
OTHER_ID = '222-2222222-2222222'
URL = f'https://www.amazon.com/your-orders/order-details?orderID={ORDER_ID}'
ITEM = '<div class="yohtmlc-item"><a href="https://www.amazon.com/dp/B000000001">Purchased item</a></div>'


class AmazonExtensionIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.page = await self.browser.new_page()
        self.root = amazon_script_paths()[0].parents[1]
        for path in amazon_script_paths():
            await self.page.add_script_tag(content=path.read_text())

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def detail(self, extra='', order_id=ORDER_ID):
        await self.page.set_content(f'<div id="orderDetails">'
                                    f'<span data-component="orderId">{order_id}</span>{ITEM}</div>{extra}')

    async def parse(self):
        return await self.page.evaluate(
            'url => OrderManagerAmazonDom.parseOrderDetailPage(document, url)', URL)

    async def test_dom_and_normalizer_reject_different_order(self):
        await self.detail(order_id=OTHER_ID)
        self.assertIsNone(await self.parse())
        ready = await self.page.evaluate(
            'url => OrderManagerAmazonDom.isOrderDetailContentReadyInDocument(document, url)', URL)
        self.assertFalse(ready)
        error = await self.page.evaluate('''({url, id}) => {
            try { OrderManagerAmazon.normalizeAmazonOrderPayload({orderId:id}, url, null) }
            catch (e) { return e.message }
        }''', {'url': URL, 'id': OTHER_ID})
        self.assertIn('does not match', error)

    async def test_order_text_must_match_url_and_url_alone_is_not_identity(self):
        await self.page.set_content(f'<div id="orderDetails">Order #{OTHER_ID}{ITEM}</div>')
        self.assertIsNone(await self.parse())
        await self.page.set_content(f'<div id="orderDetails">{ITEM}</div>')
        self.assertIsNone(await self.parse())

    async def test_recognized_shipment_fallback_keeps_purchased_items(self):
        await self.page.set_content(f'<main>Order #{ORDER_ID}<div class="shipment">'
                                    '<div class="a-row"><a href="https://www.amazon.com/dp/B000000001">'
                                    'Purchased item</a></div></div></main>')
        self.assertEqual([i['name'] for i in (await self.parse())['items']], ['Purchased item'])

    async def test_recommendations_and_generic_product_grids_are_not_items(self):
        for wrapper in ('<section aria-label="Recommendations">{}</section>',
                        '<div class="a-fixed-left-grid">{}</div>',
                        '<div id="sims-products"><div class="yohtmlc-item">{}</div></div>'):
            with self.subTest(wrapper=wrapper):
                product = '<a href="https://www.amazon.com/dp/B000000009">Unrelated item</a>'
                await self.page.set_content(f'<main>Order #{ORDER_ID}{wrapper.format(product)}</main>')
                self.assertEqual((await self.parse())['items'], [])
                self.assertFalse(await self.page.evaluate(
                    'url => OrderManagerAmazonDom.isOrderDetailContentReadyInDocument(document,url)', URL))

    async def test_purchased_items_survive_recommendation_filter(self):
        await self.detail('<aside aria-label="Recommendations">'
                          '<div class="yohtmlc-item"><a href="/dp/B000000009">Unrelated item</a></div></aside>')
        self.assertEqual([i['name'] for i in (await self.parse())['items']], ['Purchased item'])

    async def test_payment_sibling_of_details_is_captured(self):
        await self.detail('<section data-component="paymentMethod">Visa ending in 1234</section>')
        self.assertTrue(await self.page.evaluate(
            '() => OrderManagerAmazonDom.waitForPaymentMethodsInDocument(document, 50)'))
        self.assertEqual((await self.parse())['paymentMethods'][0]['last4'], '1234')

    async def test_legacy_payment_heading_is_scoped_to_its_column(self):
        await self.detail('<div class="a-column"><h5>Payment method</h5>'
                          '<div>Visa ending in 1234</div></div>'
                          '<aside aria-label="Recommendations"><section><h5>Payment method</h5>'
                          '<p>Visa ending in 9876</p></section></aside>')
        self.assertEqual([m['last4'] for m in (await self.parse())['paymentMethods']], ['1234'])

    async def test_payment_waits_for_delayed_digits(self):
        await self.detail('<section data-testid="payment-instrument-name">Visa</section>')
        self.assertFalse(await self.page.evaluate(
            '() => OrderManagerAmazonDom.waitForPaymentMethodsInDocument(document, 20)'))
        ready = await self.page.evaluate('''async () => {
            const pending = OrderManagerAmazonDom.waitForPaymentMethodsInDocument(document, 1000)
            setTimeout(() => {
                const digits = document.createElement('span')
                digits.dataset.testid = 'payment-instrument-number'
                digits.innerHTML = '<span class="a-offscreen">ending in 1234</span>'
                document.body.appendChild(digits)
            }, 30)
            return await pending
        }''')
        self.assertTrue(ready)
        self.assertEqual((await self.parse())['paymentMethods'][0]['last4'], '1234')

    async def test_non_card_payment_and_unrelated_card_text(self):
        for method in ('Pay by Invoice', 'Gift Card'):
            await self.detail(f'<section data-component="paymentMethod">{method}</section>')
            self.assertTrue(await self.page.evaluate(
                '() => OrderManagerAmazonDom.waitForPaymentMethodsInDocument(document, 20)'))
            self.assertEqual((await self.parse())['paymentMethods'][0]['description'], method)
        await self.detail('<aside>Try Visa ending in 9876</aside>')
        self.assertEqual((await self.parse())['paymentMethods'], [])

    async def test_email_requires_selected_or_authoritative_account(self):
        cases = [
            ('<div class="cvf-account-switcher-profile-details">other@example.com</div>'
             '<div class="cvf-account-switcher-profile-details" aria-selected="true">current@example.com</div>',
             'current@example.com'),
            ('<div id="ap-account-switcher-container">'
             '<div aria-selected="false">other@example.com</div>'
             '<div aria-selected="true">current@example.com</div></div>', 'current@example.com'),
            ('<div class="cvf-account-switcher-profile-details">other@example.com</div>', None),
            ('<script>{"buyerEmail":"other@example.com","email":"seller@example.com"}</script>'
             '<div data-email="someone@example.com">Help: support@example.com</div>', None),
            ('<div class="a-row"><label>Email</label>recipient@example.com</div>', None),
            ('<div id="email-section">current@example.com</div>', 'current@example.com'),
            ('<input id="ap_email" value="other@example.com">', None),
        ]
        for html, expected in cases:
            with self.subTest(html=html):
                result = await self.page.evaluate(
                    'html => OrderManagerAmazonDom.parseEmailFromAccountHtml(html)', html)
                self.assertEqual(result, expected)

    async def mock_capture(self):
        await self.page.evaluate('''() => {
            window.chrome = {
                storage: {local: {
                    get: () => { throw new Error('Must not read cached identity') },
                    set: (row, cb) => cb()
                }},
                runtime: {sendMessage: () => {}, onMessage: {addListener: fn => window.captureListener = fn}}
            }
        }''')
        await self.page.add_script_tag(content=(self.root / 'stores/amazon/capture.js').read_text())

    async def test_content_script_resolves_email_again_after_account_switch(self):
        await self.mock_capture()
        result = await self.page.evaluate('''async () => {
            const d = OrderManagerAmazonDom
            let current = 'first@example.com'
            d.fetchAccountEmail = async () => current
            const request = () => new Promise(resolve => captureListener(
                {store:'amazon', type:'amazonFetchAccountEmail'}, {}, resolve))
            const first = await request()
            current = 'second@example.com'
            const second = await request()
            current = null
            return [first, second, await request()]
        }''')
        self.assertEqual([r['email'] for r in result], ['first@example.com', 'second@example.com', None])

    async def popup(self, capture_ok=True, captured_id=ORDER_ID, fresh_email='current@example.com'):
        await self.page.set_content('<section id="amazonOrderDetailSection">'
                                    '<button id="amazonImportThisOrder">Import</button>'
                                    '<div id="amazonOrderDetailResults"></div></section>')
        await self.page.evaluate('''({url, captureOk, capturedId, freshEmail}) => {
            window.isAmazonOrderDetailUrl = () => true
            window.withAmazonDisableCsdUrl = u => u
            window.getOrderManagerApiBaseUrl = cb => cb('https://example.test')
            window.openBulkReviewForOrders = orders => window.imported = orders
            window.escapeHtml = s => s
            window.storageReads = 0
            window.chrome = {runtime:{},tabs:{
                query: (q,cb) => cb([{id:1,url,cookieStoreId:'firefox-container-1'}]),
                sendMessage: (id,msg,cb) => cb(msg.type === 'amazonFetchAccountEmail'
                    ? {success:true,email:freshEmail}
                    : captureOk ? {success:true,order:{orderId:capturedId}}
                                : {success:false,error:'Capture failed'})
            },storage:{local:{get:(key,cb)=>{
                window.storageReads++
                cb({amazonOrderDetail:{url,payload:{orderId:'222-2222222-2222222'}},
                    amazonAccountEmail:{email:'stale@example.com',origin:'https://www.amazon.com',
                        cookieStoreId:'firefox-container-2'}})
            }}}}
        }''', {'url': URL, 'captureOk': capture_ok, 'capturedId': captured_id, 'freshEmail': fresh_email})
        source = (self.root / 'popup/popup.js').read_text()
        section = source[source.index('(function setupAmazonOrderDetailSection()'):
                         source.index('(function setupWalmartBulkSection()')]
        await self.page.add_script_tag(content=section)
        await self.page.click('#amazonImportThisOrder')

    async def test_popup_failed_capture_does_not_import_global_cache(self):
        await self.popup(capture_ok=False)
        self.assertIsNone(await self.page.evaluate('window.imported'))
        self.assertEqual(await self.page.evaluate('window.storageReads'), 0)
        self.assertIn('Capture failed', await self.page.locator('#amazonOrderDetailResults').inner_text())

    async def test_popup_rejects_wrong_order_response(self):
        await self.popup(captured_id=OTHER_ID)
        self.assertIsNone(await self.page.evaluate('window.imported'))
        self.assertIn('does not match', await self.page.locator('#amazonOrderDetailResults').inner_text())

    async def test_popup_uses_fresh_email_and_leaves_unknown_email_empty(self):
        for fresh in ('current@example.com', None):
            with self.subTest(fresh=fresh):
                await self.popup(fresh_email=fresh)
                orders = await self.page.evaluate('window.imported')
                self.assertEqual(orders[0]['customer']['email'], fresh)
                self.assertEqual(await self.page.evaluate('window.storageReads'), 0)

    async def test_iframe_capture_validates_requested_order_and_isolates_tracking(self):
        async def respond(route):
            if f'orderID={ORDER_ID}' in route.request.url:
                await route.fulfill(status=302, headers={'location': URL.replace(ORDER_ID, OTHER_ID)})
            else:
                await route.fulfill(content_type='text/html', body=f'<div id="orderDetails">'
                                    f'<span data-component="orderId">{OTHER_ID}</span>{ITEM}</div>')

        await self.page.route('https://www.amazon.com/**', respond)
        await self.page.goto('https://www.amazon.com/your-orders/orders')
        for path in amazon_script_paths():
            await self.page.add_script_tag(content=path.read_text())
        await self.mock_capture()
        # Skip optional enrichment waits so this test exercises capture validation directly.
        await self.page.evaluate('''() => {
            OrderManagerAmazonDom.waitForPaymentMethodsInDocument = async () => false
            OrderManagerAmazonDom.waitForOrderDetailReadyInDocument = async () => true
        }''')
        result = await self.page.evaluate('''url => new Promise(resolve => captureListener(
            {store:'amazon',type:'amazonCaptureOrderDetailFromUrl',detailUrl:url,
             skipTrackingEnrichment:true}, {}, resolve))''', URL)
        self.assertFalse(result['success'])
        result = await self.page.evaluate('''() => {
            document.body.innerHTML = '<a href="https://www.amazon.com/gp/your-account/ship-track?trackingId=TBA9999999999">Track</a>'
            const frameDoc = new DOMParser().parseFromString('<div></div>', 'text/html')
            const parsed = {items:[],shipments:[{shipmentId:'shipment-0'}]}
            OrderManagerAmazonDom.repairShipmentTracking(parsed, frameDoc)
            return parsed.shipments[0]
        }''')
        self.assertNotIn('trackingUrl', result)

    async def test_bulk_rejects_failed_and_mismatched_details_but_keeps_success(self):
        source = (self.root / 'background.js').read_text()
        start = source.index('function attachAmazonBulkPortHandlers(')
        handler = source[start:source.index('\nif (chrome && chrome.runtime && chrome.runtime.onConnect)', start)]
        await self.page.add_script_tag(content=handler)
        result = await self.page.evaluate('''async () => {
            const events = [], imported = []
            const url = 'https://www.amazon.com/your-orders/orders?disableCsd=missing-library'
            window.getOrderManagerApiBaseUrlAsync = async () => 'https://example.test'
            window.ensureTabExists = async () => ({id:1,url,cookieStoreId:'firefox-container-1'})
            window.isPlausibleAmazonEmail = OrderManagerAmazon.isPlausibleAccountEmail
            window.ensureAmazonCsdDisabledCookie = async () => {}
            window.withAmazonDisableCsdUrl = u => u
            window.isAmazonSpaHashListUrl = () => false
            window.waitForAmazonContentScript = async () => true
            window.waitForAmazonTabSignal = async () => true
            window.clearAmazonDetailStorage = async () => {}
            window.keepaliveSleep = async () => {}
            window.closeTab = async () => {}
            window.openReviewTab = async () => {}
            window.normalizeAmazonOrderPayloadSafe = OrderManagerAmazon.normalizeAmazonOrderPayload
            window.postBulkSession = async (base,orders) => {
                imported.push(...orders)
                return {appBase:base,token:'test'}
            }
            const ids = ['111-1111111-1111111','222-2222222-2222222','333-3333333-3333333']
            window.sendAmazonTabMessage = async (id,msg) => {
                if (msg.type === 'amazonFetchAccountEmail') return {success:false}
                if (msg.type === 'amazonParseCurrentListPage') return {success:true,orders:ids.map(orderId=>({
                    orderId,detailUrl:'https://www.amazon.com/your-orders/order-details?orderID='+orderId,
                    items:[{name:'List summary should not be imported'}]
                }))}
                if (msg.type === 'amazonGetNextPageUrl') return {success:true,url:null}
                if (msg.type === 'amazonCaptureOrderDetailFromUrl') {
                    if (msg.detailUrl.includes(ids[0])) return {success:false,error:'Detail failed'}
                    return {success:true,order:{orderId:ids[2],items:[{name:'Captured item',quantity:2}]}}
                }
                throw new Error('Unexpected request: '+msg.type)
            }
            let run
            attachAmazonBulkPortHandlers({onMessage:{addListener:fn=>run=fn},postMessage:msg=>events.push(msg)})
            await run({store:'amazon',type:'start',sourceTabId:1,maxPages:1})
            return {events,imported}
        }''')
        statuses = [e['status'] for e in result['events'] if e['type'] == 'orderStatus']
        self.assertEqual(statuses, ['pending', 'error', 'pending', 'error', 'pending', 'ok'])
        self.assertEqual(len(result['imported']), 1)
        order = result['imported'][0]
        self.assertEqual(order['externalOrder']['id'], '333-3333333-3333333')
        self.assertEqual(order['items'][0]['quantities']['ordered'], 2)
        self.assertIsNone(order['customer']['email'])
