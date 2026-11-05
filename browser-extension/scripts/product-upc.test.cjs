const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const { join } = require('node:path')
const { test } = require('node:test')
for (const lib of ['product-upc', 'amazon', 'walmart']) {
  require('node:vm').runInThisContext(readFileSync(join(__dirname, `../lib/${lib}.js`), 'utf8'))
}
const u = globalThis.OrderManagerProductUpc
const upc = '000012345678'
const asin = 'B012345678'
const walmartPage = products => `<script id="__NEXT_DATA__" type="application/json">${JSON.stringify({ props: { products } })}</script>`
const amazonPage = (id, value) => `<input id="ASIN" value="${id}"><table id="productDetails_detailBullets_sections1"><tr><th>ASIN</th><td>${id}</td></tr><tr><th>UPC</th><td>${value}</td></tr></table>`

test('normalizers retain explicit UPCs, leading zeros and reject conflicting fields', () => {
  for (const fields of [{ upc }, { UPC: upc }, { upcCode: ` ${upc} ` }, { gtin12: upc }]) {
    const amazon = OrderManagerAmazon.normalizeAmazonOrderPayload({ orderId: 'O1', items: [{ asin, ...fields }] })
    const walmart = OrderManagerWalmart.normalizeWalmartOrderDetailPayload({ order: { id: 'O1', groups_2101: [
      { items: [{ productInfo: { usItemId: '123', ...fields } }] },
    ] } })
    assert.equal(amazon.items[0].upc, upc)
    assert.equal(walmart.items[0].upc, upc)
  }
  for (const fields of [{ sku: upc }, { asin: upc }, { upc: 123456789012 }, { upc: true },
    { upc: 'invalid' }, { upc: '123' }, { upc, UPC: '999999999999' }]) assert.equal(u.explicit(fields), null)
})

test('Walmart lookup only reads the requested product, without borrowing variant UPCs', () => {
  const products = [{ usItemId: '123', upc, variants: [{ usItemId: '456', upc: '999999999999' }] }]
  assert.equal(u.extract(walmartPage(products), '123', 'walmart'), upc)
  assert.equal(u.extract(walmartPage(products), '789', 'walmart'), null)
  assert.equal(u.extract(walmartPage([...products, { usItemId: '123', UPC: '999999999999' }]), '123', 'walmart'), null)
  assert.equal(u.extract(walmartPage([{ usItemId: '123', upc, UPC: '999999999999' }]), '123', 'walmart'), null)
})

test('Amazon lookup requires matching ASIN and scoped UPC labels', () => {
  assert.equal(u.extract(amazonPage(asin, upc), asin, 'amazon'), upc)
  assert.equal(u.extract(amazonPage('B999999999', upc), asin, 'amazon'), null)
  assert.equal(u.extract(`<input id="ASIN" value="${asin}"><p>UPC ${upc}</p>`, asin, 'amazon'), null)
  const bullets = `<div id="detailBullets_feature_div"><ul><li><span>ASIN :</span> ${asin}</li><li><span>UPC :</span> ${upc}</li></ul></div>`
  assert.equal(u.extract(bullets, asin, 'amazon'), upc)
  const jsonld = `<script type="application/ld+json">${JSON.stringify({ '@type': 'Product', sku: asin, gtin12: upc })}</script>`
  assert.equal(u.extract(jsonld, asin, 'amazon'), upc)
  assert.equal(u.extract(jsonld + amazonPage(asin, '999999999999'), asin, 'amazon'), null)
})

test('Amazon expandable Item details tables expose UPCs without table IDs', () => {
  const page = (id, value) => `<div id="productDetails_expanderTables_depthRightSections">
    <div class="a-expander-content" style="display:none"><table class="a-keyvalue prodDetTable">
      <tbody><tr><th>Brand Name</th><td>Nilight</td></tr>
      <tr><th>UPC</th><td>${value}</td></tr>
      <tr><th>ASIN</th><td>${id}</td></tr></tbody>
    </table></div></div>`
  assert.equal(u.extract(page('B0748F1JK4', '600537013346'), 'B0748F1JK4', 'amazon'), '600537013346')
  assert.equal(u.extract(page('B0748F1JK4', '600537013346'), asin, 'amazon'), null)
  // Some listings associate several barcodes with one ASIN; do not choose one.
  assert.equal(u.extract(page('B074NBSF9N', '196188048030 740617268324'), 'B074NBSF9N', 'amazon'), null)
  assert.equal(u.extract(page(asin, upc) + page(asin, '999999999999'), asin, 'amazon'), null)
  assert.equal(u.extract(`<table class="comparisonTable"><tr><th>UPC</th><td>${upc}</td></tr>
    <tr><th>ASIN</th><td>${asin}</td></tr></table>`, asin, 'amazon'), null)
})

for (const store of ['amazon', 'walmart']) {
  test(`${store} lookup deduplicates products, preserves saved values and tolerates failures`, async () => {
    const sku = store === 'amazon' ? asin : '123'
    const missing = store === 'amazon' ? 'B999999999' : '456'
    const orders = [{ items: [{ externalSku: sku }, { externalSku: sku }, { externalSku: missing },
      { externalSku: sku, upc: '999999999999' }, { externalSku: '../bad' }] }]
    const calls = []
    await u.enrich(orders, store, async id => {
      calls.push(id)
      if (id === missing) throw new Error('blocked')
      return store === 'amazon' ? amazonPage(id, upc) : walmartPage([{ usItemId: id, upc }])
    })
    assert.deepEqual(calls.sort(), [sku, missing].sort())
    assert.deepEqual(orders[0].items.map(i => i.upc), [upc, upc, undefined, '999999999999', undefined])
  })
}
