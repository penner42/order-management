import { test, expect } from './fixtures'
import type { Order } from '../../src/api/types'

test('receiving audit shows excess quantities, uncertainty, and order links without writing data', async ({ app }) => {
  const writes: string[] = []
  app.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/') && !['GET', 'HEAD'].includes(request.method())) {
      writes.push(`${request.method()} ${new URL(request.url()).pathname}`)
    }
  })
  await app.route('**/api/buying-groups', route => route.fulfill({ json: [{ id: 77, name: 'Audit BG', api_framework: 'buyinggroup' }] }))
  const item = { item_id: 901, order_id: 123, store_order_number: 'WALMART-ORDER', description: 'Laptop',
    quantity: 1, status: 'scanned', receipt_id: 'R1', receipt_tracking_number: 'TRACK', tracking_numbers: ['TRACK'],
    scanned_at: '2026-10-20T00:00:00Z', reward: '0.05', payment_linked: false }
  const base = { receipt_id: 'R1', status: 'excess', bg_quantity: 1, local_quantity_min: 2, local_quantity_max: 2,
    excess_quantity: 1, notes: [], items: [item, { ...item, item_id: 902 }],
    bg_lines: [{ tracking_number: 'TRACK', alternate_tracking: 'WALMART-ORDER', quantity: 1, void: false,
      bg_order_id: 'BG-1', received_at: '2026-10-20', total: '199.00', note: 'Warehouse receipt' }] }
  await app.route('**/api/buying-groups/receiving-audit*', route => {
    expect(route.request().method()).toBe('GET')
    expect(new URL(route.request().url()).searchParams.get('group_id')).toBe('77')
    return route.fulfill({ json: { checked_at: '2026-10-21T12:00:00Z', groups: [{ group_id: 77, group_name: 'Audit BG',
      error: null, local_item_count: 4, receipt_row_count: 2, findings: [base,
        { ...base, receipt_id: null, status: 'unverified', bg_quantity: null, excess_quantity: null,
          items: [{ ...item, item_id: 903, receipt_id: null }], bg_lines: [],
          notes: ['Received locally without a receipt ID; compare tracking and BG history manually.'] },
        { ...base, receipt_id: 'R2', status: 'within_quantity', local_quantity_min: 1, local_quantity_max: 1,
          excess_quantity: 0, items: [{ ...item, item_id: 904 }] }],
    }] } })
  })
  await app.goto('/receiving-review')
  await app.getByRole('link', { name: 'Receiving audit', exact: true }).click()
  await expect(app.getByRole('heading', { name: 'Receiving audit', exact: true })).toBeVisible()
  await app.getByLabel('Buying group', { exact: true }).selectOption('77')
  await app.getByRole('button', { name: 'Run report', exact: true }).click()
  await expect(app.getByText('1 receipts with excess quantity', { exact: false })).toBeVisible()
  await expect(app.getByText('At least 1 excess', { exact: true })).toBeVisible()
  await expect(app.getByText('Cannot verify', { exact: true })).toBeVisible()
  await expect(app.getByText('Receipt R2', { exact: true })).not.toBeVisible()
  await app.getByText('BG receipt evidence', { exact: true }).click()
  await expect(app.getByText('Warehouse receipt', { exact: false })).toBeVisible()
  await expect(app.getByRole('link', { name: 'WALMART-ORDER', exact: true }).first()).toHaveAttribute('href', '/?order_id=123')
  await app.getByLabel('Include receipts within BG quantity').check()
  await expect(app.getByText('Receipt R2', { exact: true })).toBeVisible()
  expect(writes).toEqual([])
})

test('failed BG history retrieval is shown as an incomplete report', async ({ app }) => {
  await app.route('**/api/buying-groups', route => route.fulfill({ json: [{ id: 77, name: 'Offline BG', api_framework: 'buyinggroup' }] }))
  await app.route('**/api/buying-groups/receiving-audit*', route => route.fulfill({ json: {
    checked_at: '2026-10-21T12:00:00Z', groups: [{ group_id: 77, group_name: 'Offline BG', local_item_count: 2,
      receipt_row_count: 0, findings: [], error: 'Could not retrieve complete BG receipt history.' }],
  } }))
  await app.goto('/receiving-audit')
  await app.getByRole('button', { name: 'Run report', exact: true }).click()
  await expect(app.getByText('Report incomplete: some groups could not be checked.')).toBeVisible()
  await expect(app.getByRole('alert')).toContainText('Could not retrieve complete BG receipt history.')
  await expect(app.getByText('No excess quantities or unverifiable receipt links found.')).not.toBeVisible()
})

test('authenticated report endpoint leaves real order data unchanged when BG is unconfigured', async ({ app, data }) => {
  const group = await data.create('/buying-groups', { name: data.name('audit-bg'), api_framework: 'buyinggroup' })
  const store = await data.create('/stores', { name: data.name('audit-store') })
  const order = await data.create<Order>('/orders', { store_id: store.id, buying_group_id: group.id,
    store_order_number: data.name('audit-order'), items: [
      { description: 'Laptop', status: 'scanned', quantity: 1, price_sold: 199 },
    ] })
  const before = await data.get<Order>(`/orders/${order.id}`)
  await app.goto('/receiving-audit')
  await app.getByLabel('Buying group', { exact: true }).selectOption(String(group.id))
  const response = app.waitForResponse(response => new URL(response.url()).pathname === '/api/buying-groups/receiving-audit')
  await app.getByRole('button', { name: 'Run report', exact: true }).click()
  expect((await response).ok()).toBeTruthy()
  await expect(app.getByText('Report incomplete: some groups could not be checked.')).toBeVisible()
  await expect(app.getByRole('alert')).toBeVisible()
  expect(await data.get<Order>(`/orders/${order.id}`)).toEqual(before)
})
