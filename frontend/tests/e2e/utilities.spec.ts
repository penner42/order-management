import { test, expect } from '@playwright/test'
import { parseDecimal } from '../../src/utils/decimal'
import { stripIgnoredWalmartImportSlices } from '../../src/utils/stripIgnoredWalmartImport'
import { getDefaultItemPayout, getDefaultOrderTotal } from '../../src/utils/importDefaults'

test('Costco payouts allocate the discount by value after covering all software units', () => {
  const payload = { store: 'costco', orderDiscount: 100.03, items: [
    { name: 'Laptop', quantities: { ordered: 2 }, pricing: { unitPrice: 300 } },
    { name: 'Monitor', quantities: { ordered: 1 }, pricing: { lineTotal: 400 } },
    { name: 'Microsoft 365', quantities: { ordered: 3 }, pricing: { unitPrice: .01 } },
  ] }
  expect(payload.items.map(item => getDefaultItemPayout(item, payload))).toEqual([270, 360, 0])
  expect(getDefaultOrderTotal(payload)).toBeCloseTo(900)
  expect(getDefaultItemPayout(payload.items[0], { ...payload, store: 'amazon' })).toBe(300)
  expect(getDefaultItemPayout(payload.items[0], { ...payload, orderDiscount: 0 })).toBe(300)
  expect(getDefaultItemPayout(payload.items[0], { ...payload, orderDiscount: 2000 })).toBe(0)
})

test('Costco discount supports normal purchases, standalone software and cent rounding', () => {
  const payload = { store: 'costco', orderDiscount: .03, items: [
    { name: 'Laptop', pricing: { unitPrice: 10 } },
    { name: 'Monitor', pricing: { unitPrice: 20 } },
  ] }
  expect(payload.items.map(item => getDefaultItemPayout(item, payload))).toEqual([9.99, 19.98])
  const standalone = { store: 'costco', orderDiscount: 10, items: [
    { name: 'Microsoft 365', pricing: { unitPrice: 100 } },
  ] }
  expect(getDefaultItemPayout(standalone.items[0], standalone)).toBe(90)
})

test('decimal amounts accept API strings, numbers, commas and empty values', () => {
  for (const [input, expected] of [
    ['12.50', 12.5], [12.5, 12.5], ['1,234.56', 1234.56], [-3, -3],
    ['', 0], [null, 0], [undefined, 0], ['invalid', 0], [Infinity, 0], [NaN, 0],
  ] as const) expect(parseDecimal(input)).toBe(expected)
})

test('Walmart email delivery removes only linked items and preserves physical slices', () => {
  const payload = {
    store: ' Walmart ', externalOrder: { id: 'fixture' },
    shipments: [{ shipmentId: 'email', trackingNumber: ' SENT VIA EMAIL ' }, { shipmentId: 'box', trackingNumber: 'physical' }],
    items: [{ name: 'digital', shipments: [{ shipmentId: 'email' }] }, { name: 'physical', shipments: [{ shipmentId: 'box' }] }],
  }
  const result = stripIgnoredWalmartImportSlices(payload)
  expect(result.items.map(item => item.name)).toEqual(['physical'])
  expect(result.shipments.map(shipment => shipment.shipmentId)).toEqual(['box'])
  expect(result.externalOrder).toBe(payload.externalOrder)
  expect(payload.items).toHaveLength(2)
})

test('non-Walmart, empty arrays and missing optional arrays preserve identity', () => {
  const payloads = [
    { store: 'amazon', shipments: [{ trackingNumber: 'Sent via email' }] },
    { store: 'walmart' }, { store: 'walmart', shipments: [], items: [] },
    { store: null, shipments: null, items: null },
  ]
  for (const payload of payloads) expect(stripIgnoredWalmartImportSlices(payload)).toBe(payload)
})
