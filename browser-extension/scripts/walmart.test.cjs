const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const { join } = require('node:path')
const { test } = require('node:test')

require('node:vm').runInThisContext(readFileSync(join(__dirname, '../lib/walmart.js'), 'utf8'))
const normalize = globalThis.OrderManagerWalmart.normalizeWalmartOrderDetailPayload

function group(statusType, name, statusCode) {
  return {
    status: { statusType },
    shipment: { id: name },
    items: [{ id: name, productInfo: { name }, quantity: 1, statusCode }],
  }
}

function order(groups) {
  return normalize({ order: { id: 'ORDER-1', groups_2101: groups } })
}

for (const activeStatus of ['SHIPPED', 'DELIVERED', 'PROCESSING']) {
  for (const canceledFirst of [true, false]) {
    test(`a canceled group preserves ${activeStatus} sibling, canceled first: ${canceledFirst}`, () => {
      const groups = [group('CANCELED', 'canceled'), group(activeStatus, 'active', 'ACTIVE')]
      const normalized = order(canceledFirst ? groups : groups.reverse())
      assert.equal(normalized.externalOrder.statusType, 'partially canceled')
      assert.equal(normalized.items.find(i => i.name === 'canceled').status.normalizedStatus, 'canceled')
      assert.equal(normalized.items.find(i => i.name === 'active').status.normalizedStatus, null)
      assert.equal(normalized.items.find(i => i.name === 'active').status.rawStatusCode, 'ACTIVE')
    })
  }
}

test('all canceled groups produce a whole-order cancellation', () => {
  const normalized = order([group('CANCELED', 'one'), group('Cancelled', 'two')])
  assert.equal(normalized.externalOrder.statusType, 'canceled')
  assert.ok(normalized.items.every(i => i.status.normalizedStatus === 'canceled'))
})

test('a group without status prevents a whole-order cancellation', () => {
  const normalized = order([group('CANCELED', 'one'), group(null, 'two')])
  assert.equal(normalized.externalOrder.statusType, 'partially canceled')
  assert.equal(normalized.items[1].status.normalizedStatus, null)
})

test('item cancellation stays scoped within an active group', () => {
  const active = group('SHIPPED', 'active')
  active.items.push(group('CANCELED', 'canceled', 'CANCELED').items[0])
  const normalized = order([active])
  assert.equal(normalized.externalOrder.statusType, 'SHIPPED')
  assert.equal(normalized.items[1].status.rawStatusCode, 'CANCELED')
  assert.equal(normalized.items[0].status.normalizedStatus, null)
})

test('an order without groups has no inferred status', () => {
  assert.equal(order([]).externalOrder.statusType, null)
})
