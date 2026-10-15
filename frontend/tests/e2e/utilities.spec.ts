import { test, expect } from '@playwright/test'
import { parseDecimal } from '../../src/utils/decimal'
import { stripIgnoredWalmartImportSlices } from '../../src/utils/stripIgnoredWalmartImport'

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
