const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const { join } = require('node:path')
const { test } = require('node:test')
require('node:vm').runInThisContext(readFileSync(join(__dirname, '../lib/costco.js'), 'utf8'))
const c = globalThis.OrderManagerCostco
const line = { lineItemId: 'L1', itemNumber: '1234567', itemDescription: 'Product', quantity: 2 }
const list = item => ({ data: { getOnlineOrders: [{ bcOrders: [{ orderNumber: 'O1', orderLineItems: [item] }] }] } })
const details = item => ({ data: { getOrderDetails: { orderNumber: 'O1', shipToAddress: [{ orderLineItems: [item] }] } } })

test('returned and delivered snapshots count as one purchase in list, detail and merged capture', () => {
  const purchase = { ...line, price: 599.97, merchandiseTotalAmount: 1199.94,
    status: 'Delivered', shipment: [{ shipmentId: 'S1', trackingNumber: 'TRACK1' }] }
  const returned = { ...purchase, status: 'Returned' }
  for (const raw of [[returned, purchase], [purchase, returned]]) {
    const orders = list(purchase)
    orders.data.getOnlineOrders[0].bcOrders[0].orderLineItems = raw
    const detail = details(purchase)
    detail.data.getOrderDetails.shipToAddress[0].orderLineItems = raw
    const normalized = c.normalizeCostcoOrdersGraphqlPayload(orders)
    assert.equal(normalized[0].items.length, 1)
    assert.equal(normalized[0].items[0].status.rawStatusCode, 'Delivered')
    assert.equal(c.normalizeCostcoOrderDetailsGraphqlPayload(detail).items.length, 1)
    c.mergeCostcoOrderDetailsIntoNormalizedOrders(normalized, { O1: detail })
    assert.equal(normalized[0].items.length, 1)
    assert.equal(normalized[0].items[0].quantities.ordered, 2)
    assert.equal(normalized[0].items[0].pricing.lineTotal, 1199.94)
  }
})

test('matching SKUs with distinct line IDs or shipment allocations remain separate', () => {
  const purchase = { ...line, shipment: [{ shipmentId: 'S1', trackingNumber: 'TRACK1' }] }
  for (const other of [{ ...purchase, lineItemId: 'L2' },
    { ...purchase, shipment: [{ shipmentId: 'S2', trackingNumber: 'TRACK2' }] },
    { ...purchase, lineItemId: null }]) {
    const raw = list(purchase)
    raw.data.getOnlineOrders[0].bcOrders[0].orderLineItems.push(other)
    assert.equal(c.normalizeCostcoOrdersGraphqlPayload(raw)[0].items.length, 2)
  }
})

test('list, single-order, and bulk detail capture preserve leading zeros', () => {
  for (const key of ['upc', 'UPC', 'upcCode', 'upcNumber']) {
    const item = { ...line, [key]: ' 000012345678 ' }
    assert.equal(c.normalizeCostcoOrdersGraphqlPayload(list(item))[0].items[0].upc, '000012345678')
    assert.equal(c.normalizeCostcoOrderDetailsGraphqlPayload(details(item)).items[0].upc, '000012345678')
    const orders = c.normalizeCostcoOrdersGraphqlPayload(list(line))
    c.mergeCostcoOrderDetailsIntoNormalizedOrders(orders, { O1: details(item) })
    assert.equal(orders[0].items[0].upc, '000012345678')
  }
})

test('missing, malformed, numeric, and conflicting UPCs are omitted; SKU is not a UPC', () => {
  for (const fields of [{}, { upc: 123456789012 }, { upc: 'invalid' }, { upc: '123' },
    { upc: '000012345678', UPC: '123456789012' }, { upc: true }]) {
    assert.equal(c.normalizeCostcoOrderDetailsGraphqlPayload(details({ ...line, ...fields })).items[0].upc, null)
  }
})

test('a detail response without a UPC does not erase the list UPC or affect a sibling', () => {
  const orders = c.normalizeCostcoOrdersGraphqlPayload(list({ ...line, upc: '000012345678' }))
  c.mergeCostcoOrderDetailsIntoNormalizedOrders(orders, { O1: details(line) })
  assert.equal(orders[0].items[0].upc, '000012345678')
  c.mergeCostcoOrderDetailsIntoNormalizedOrders(orders, { O1: details({ ...line, lineItemId: 'OTHER', upc: '123456789012' }) })
  assert.equal(orders[0].items[0].upc, '000012345678')
})

function catalogPage(products, split = false) {
  const text = '1:' + JSON.stringify({ productDetailsData: { productData: products[0] },
    recommendations: products.slice(1).map(productData => ({ productData })) }) + '\n'
  const parts = split ? [text.slice(0, text.length / 2), text.slice(text.length / 2)] : [text]
  return parts.map(part => '<script>self.__next_f.push(' + JSON.stringify([1, part]) + ')</script>').join('')
}
const airpods = { id: '4000308504', upc: '499996263740', childCatalogData: [{ id: '1611943', upc: '195949689604' }] }

test('catalog lookup matches the exact SKU, handles split Flight JSON and ignores recommendations', () => {
  const other = { id: 'OTHER', upc: '199896922524', childCatalogData: [] }
  assert.equal(c.extractCostcoProductUpc(catalogPage([airpods, other], true), '1611943'), '195949689604')
  assert.equal(c.extractCostcoProductUpc(catalogPage([airpods]), '9999999'), null)
})

test('a single child with an internal code can use its parent manufacturer UPC', () => {
  const asus = { id: '4201036168', upc: '199291557024', childCatalogData: [{ id: '2040144', upc: '499993365102' }] }
  assert.equal(c.extractCostcoProductUpc(catalogPage([asus]), '2040144'), '199291557024')
  asus.childCatalogData.push({ id: 'OTHER', upc: '199896922524' })
  assert.equal(c.extractCostcoProductUpc(catalogPage([asus]), '2040144'), null)
})

test('missing, conflicting, invalid-checksum and internal-only barcodes stay empty', () => {
  const conflicting = { ...airpods, childCatalogData: [{ id: '1611943', upc: '199896922524' }] }
  assert.equal(c.extractCostcoProductUpc(catalogPage([airpods, conflicting]), '1611943'), null)
  for (const upc of [null, '499996263740', '195949689605', 195949689604]) {
    assert.equal(c.extractCostcoProductUpc(catalogPage([{ id: '1611943', upc }]), '1611943'), null)
  }
  globalThis.costcoScriptExecuted = false
  assert.equal(c.extractCostcoProductUpc('<script>globalThis.costcoScriptExecuted = true</script>', '1611943'), null)
  assert.equal(globalThis.costcoScriptExecuted, false)
})

test('enrichment deduplicates products, preserves existing UPCs and tolerates failed lookups', async () => {
  const calls = []
  const orders = [{ items: [{ externalSku: '1611943' }, { externalSku: '1611943' },
    { externalSku: '1234567' }, { externalSku: 'bad/sku' }, { externalSku: '7654321', upc: '000012345678' }] }]
  await c.enrichCostcoOrdersWithProductUpcs(orders, async sku => {
    calls.push(sku)
    if (sku === '1234567') throw new Error('Unavailable')
    return catalogPage([airpods])
  })
  assert.deepEqual(calls.sort(), ['1234567', '1611943'])
  assert.equal(orders[0].items[0].upc, '195949689604')
  assert.equal(orders[0].items[1].upc, '195949689604')
  assert.equal(orders[0].items[2].upc, undefined)
  assert.equal(orders[0].items[4].upc, '000012345678')
})
