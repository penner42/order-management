import { test, expect } from './fixtures'
import type { Item } from '../../src/api/types'

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

test('manual receipt review offers an explicit split, unlink, and apply, then displays receipt fields on Orders', async ({ app, data }) => {
  const store = await data.create('/stores', { name: data.name('receipt-store') })
  const group = await data.create('/buying-groups', { name: data.name('receipt-group') })
  const orderNumber = data.name('receipt-order')
  const order = await data.create<{ id: number; items: Item[] }>('/orders', {
    store_id: store.id, buying_group_id: group.id, store_order_number: orderNumber,
    items: [{ description: 'Receipt test headphones', quantity: 3, status: 'submitted', price_sold: '20.00' }],
  })
  const item = order.items[0]
  const originalTracking = data.name('original-tracking')
  const shipment = await data.create('/shipments', { tracking_number: originalTracking, item_ids: [item.id], notes: 'Original shipment notes' })
  const receipt = {
    receipt_key: 'test-receipt-key', receipt_id: 'TEST-RECEIPT', tracking_number: 'WRONG-SCAN',
    tracking_numbers: ['WRONG-SCAN', 'ALTERNATE-SCAN'], description: 'Warehouse headphones', quantity: 1,
    received_price: '25.00', received_ext_total: '25.00', scanned_at: '2026-10-15T12:00:00Z',
  }
  const match = {
    item_id: item.id, order_id: order.id, store_order_number: orderNumber, description: item.description,
    quantity: 1, tracking_number: 'WRONG-SCAN', receipt_tracking_number: 'WRONG-SCAN',
    price_sold: '20.00', expected_amount: '20.00', receipt_id: receipt.receipt_id,
    scanned_at: receipt.scanned_at, received_ext_total: '25.00', amount_discrepancy: true,
    selected: true, row_id: 'manual:test-receipt-key', manual_receipt_key: receipt.receipt_key,
  }
  let staged = false
  let applied = false
  let splitDone = false
  const session = () => ({
    buying_group_id: group.id, buying_group_name: data.name('receipt-group'),
    matches: staged && !applied ? [match] : [], unknown_receipts: staged || applied ? [] : [receipt],
  })
  // Isolate the warehouse queue; exercise real item persistence and splitting.
  // Server queue validation, atomic application, and refresh behavior have backend coverage.
  await app.route('**/api/buying-groups/receiving-review', route => route.fulfill({ json: applied ? [] : [session()] }))
  await app.route('**/api/buying-groups/receiving-review/candidates?*', async route => {
    const search = new URL(route.request().url()).searchParams.get('q') || ''
    const current = await data.get<Item>(`/items/${item.id}`)
    const items = orderNumber.includes(search) ? [{
      item_id: item.id, order_id: order.id, store_order_number: orderNumber, store: 'Receipt store',
      description: current.description, quantity: current.quantity, status: current.status,
      receipt_id: null, receipt_tracking_number: null, payment_linked: false, tracking_numbers: [originalTracking],
    }] : []
    await route.fulfill({ json: { items, total: items.length, page: 1 } })
  })
  await app.route('**/api/buying-groups/receiving-review/link', async route => {
    const body = route.request().postDataJSON()
    expect(body).toMatchObject({ buying_group_id: group.id, receipt_key: receipt.receipt_key,
      item_id: item.id, receipt_tracking_number: 'WRONG-SCAN' })
    if (!splitDone) {
      expect(body).toMatchObject({ split_quantity: 1, split_target: 'kept' })
      const split = await data.request.post(`/api/items/${item.id}/split`, {
        headers: data.headers, data: { keep_quantity: 1, copy_tracking: true },
      })
      expect(split.ok()).toBeTruthy()
      splitDone = true
    } else {
      expect(body.split_quantity).toBeUndefined()
    }
    staged = true
    await route.fulfill({ json: session() })
  })
  await app.route('**/api/buying-groups/receiving-review/unlink', route => {
    staged = false
    return route.fulfill({ json: session() })
  })
  await app.route('**/api/items/bulk-update', async route => {
    const body = route.request().postDataJSON()
    expect(body.updates[0]).toMatchObject({ item_id: item.id, receipt_id: receipt.receipt_id,
      receipt_tracking_number: 'WRONG-SCAN', receiving_group_id: group.id, receiving_row_id: match.row_id })
    const updates = body.updates.map(({ receiving_group_id: _group, receiving_row_id: _row, ...fields }: Record<string, unknown>) => fields)
    const saved = await data.request.post('/api/items/bulk-update', { headers: data.headers, data: { updates } })
    expect(saved.ok()).toBeTruthy()
    applied = true
    await route.fulfill({ response: saved })
  })
  await app.route('**/api/buying-groups/receiving-review/prune', route => route.fulfill({ json: { deleted: true } }))

  await app.goto('/receiving-review')
  await app.getByRole('button', { name: 'Link to order item' }).click()
  const dialog = app.getByRole('dialog', { name: 'Link receipt to order item' })
  await dialog.getByLabel('Search order number, description, or receipt ID').fill(orderNumber)
  await dialog.getByRole('radio').check()
  await expect(dialog.getByText('Explicitly split 3 units into 1 for this receipt and 2 remaining.', { exact: false })).toBeVisible()
  expect((await data.get<Item>(`/items/${item.id}`)).quantity).toBe(3)
  await dialog.getByRole('button', { name: 'Split and add to review' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(app.getByText('Manually linked', { exact: false })).toBeVisible()
  expect((await data.get<Item>(`/items/${item.id}`)).receipt_id).toBeNull()
  await app.getByRole('button', { name: 'Unlink', exact: true }).click()
  await app.getByRole('button', { name: 'Link to order item' }).click()
  await dialog.getByRole('radio').check()
  await dialog.getByRole('button', { name: 'Add to review', exact: true }).click()
  await app.reload()
  await expect(app.getByText('Manually linked', { exact: false })).toBeVisible()
  await app.getByRole('button', { name: 'Apply 1 selected' }).click()
  await expect(app.getByText('All listed rows were applied.')).toBeVisible()
  const savedItem = await data.get<Item>(`/items/${item.id}`)
  expect(savedItem.receipt_id).toBe(receipt.receipt_id)
  expect(savedItem.receipt_tracking_number).toBe('WRONG-SCAN')
  const savedShipment = await data.get<{ tracking_number: string; notes: string }>(`/shipments/${shipment.id}`)
  expect(savedShipment).toMatchObject({ tracking_number: originalTracking, notes: 'Original shipment notes' })
  await app.goto(`/?q=${encodeURIComponent(orderNumber)}`)
  const receiptFields = app.getByRole('group', { name: `Receipt details for item ${item.id}`, exact: true })
  await expect(receiptFields.getByLabel('Receipt ID', { exact: true })).toHaveValue(receipt.receipt_id)
  const trackingInput = receiptFields.getByLabel('Receipt tracking number', { exact: true })
  await expect(trackingInput).toHaveValue('WRONG-SCAN')
  await trackingInput.fill('CORRECTED-RECEIPT-SCAN')
  const patched = app.waitForResponse(response => response.url().endsWith(`/api/items/${item.id}`) && response.request().method() === 'PATCH')
  await trackingInput.blur()
  expect((await patched).ok()).toBeTruthy()
  await app.reload()
  await expect(receiptFields.getByLabel('Receipt tracking number', { exact: true })).toHaveValue('CORRECTED-RECEIPT-SCAN')
})
