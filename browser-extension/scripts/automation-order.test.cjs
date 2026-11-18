const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const { join } = require('node:path')
const { test } = require('node:test')
const { runInThisContext } = require('node:vm')

for (const source of ['amazon.js', 'automation-order.js']) {
  runInThisContext(readFileSync(join(__dirname, '../lib', source), 'utf8'))
}
const { resolveOrderContext, suggestedProfile } = globalThis.OrderManagerAutomationOrder

test('order detail URLs identify Amazon, Walmart and Costco orders before capture', () => {
  const pages = [
    ['amazon', 'https://www.amazon.com/your-orders/order-details?orderID=123-1234567-1234567', '123-1234567-1234567'],
    ['walmart', 'https://www.walmart.com/orders/123-456', '123-456'],
    ['walmart', 'https://www.walmart.com/orders/order-details?orderNumber=123-456', '123-456'],
    ['costco', 'https://www.costco.com/myaccount/#/app/abc/orderdetails/123456', '123456'],
  ]
  for (const [retailer, url, orderNumber] of pages) {
    assert.deepEqual(resolveOrderContext(retailer, url, null), { orderNumber, email: null })
  }
})

test('stale page or order data cannot suggest a profile', () => {
  const url = 'https://www.walmart.com/orders/123-456'
  const email = 'shopper@example.test'
  assert.equal(resolveOrderContext('walmart', url, {
    url: 'https://www.walmart.com/orders/other', orderNumber: '123-456', email,
  }).email, null)
  assert.equal(resolveOrderContext('walmart', url, { url, orderNumber: 'other', email }).email, null)
  assert.equal(resolveOrderContext('walmart', url, { url, orderNumber: '123-456', email }).email, email)
})

test('only an unambiguous email match or sole profile is preselected', () => {
  const account = { id: 1, store_account_name: 'shopper@example.test' }
  const store = { id: 2, store_account_name: null }
  assert.equal(suggestedProfile([store], null), store)
  assert.equal(suggestedProfile([account, store], ' SHOPPER@example.test '), account)
  assert.equal(suggestedProfile([account, store], null), null)
  assert.equal(suggestedProfile([account, { ...account, id: 3 }], account.store_account_name), null)
})
