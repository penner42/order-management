import { test, expect } from './fixtures'

test('unknown receipts are visible within each buying group, including groups with no matches', async ({ app }) => {
  await app.route('**/api/buying-groups/receiving-review', route => route.fulfill({
    json: [
      {
        buying_group_id: 901, buying_group_name: 'Warehouse Alpha', matches: [],
        unknown_receipts: [{
          receipt_id: 'receipt-alpha', tracking_number: 'UNKNOWN-ALPHA',
          tracking_numbers: ['UNKNOWN-ALPHA', 'ALTERNATE-ALPHA'],
          bg_order_id: 'group-order-alpha', description: 'Wireless headphones', quantity: 2,
          received_price: '25.00', received_ext_total: '50.00', received_status: 'Received',
          created_dt: '2026-10-15', scanned_at: '2026-10-15T12:00:00Z',
          delivery: 'Main warehouse', note: 'Box needs review',
          source_data: { sku: 'HEADPHONE-SKU', warehouse_item_id: 'warehouse-123' },
        }],
      },
      {
        buying_group_id: 902, buying_group_name: 'Warehouse Beta', matches: [],
        unknown_receipts: [{ tracking_number: 'UNKNOWN-BETA', receipt_id: 'receipt-beta' }],
      },
    ],
  }))
  await app.goto('/receiving-review')
  const alpha = app.locator('section').filter({ hasText: 'Warehouse Alpha' })
  const beta = app.locator('section').filter({ hasText: 'Warehouse Beta' })
  await expect(alpha.getByRole('heading', { name: 'Unknown tracking numbers (1 receipt)', exact: true })).toBeVisible()
  for (const text of ['UNKNOWN-ALPHA', 'ALTERNATE-ALPHA', 'Wireless headphones', '$25.00', '$50.00',
    'receipt-alpha', 'group-order-alpha', 'Received', '2026-10-15T12:00:00Z', 'Main warehouse', 'Box needs review']) {
    await expect(alpha.getByText(text, { exact: false }).first()).toBeVisible()
  }
  await expect(alpha.getByText('UNKNOWN-BETA', { exact: true })).toHaveCount(0)
  await expect(beta.getByText('UNKNOWN-BETA', { exact: true })).toBeVisible()
  await alpha.getByText('All receipt details', { exact: true }).click()
  await expect(alpha.locator('pre')).toContainText('HEADPHONE-SKU')
  await expect(alpha.locator('pre')).toContainText('warehouse-123')
  await expect(app.getByRole('button', { name: /Apply \d+ selected/ })).toHaveCount(0)
  await app.reload()
  await expect(alpha.getByText('UNKNOWN-ALPHA', { exact: true })).toBeVisible()
  await expect(beta.getByText('UNKNOWN-BETA', { exact: true })).toBeVisible()
})
