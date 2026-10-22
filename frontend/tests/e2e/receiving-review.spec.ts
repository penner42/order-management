import { test, expect } from './fixtures'
import type { Item } from '../../src/api/types'

test('partial BG receipt stores UPC on received unit and splits missing unit for review', async ({ app, data }) => {
  const store = await data.create('/stores', { name: data.name('bg-upc-store') })
  const groupName = data.name('bg-upc-group')
  const group = await data.create('/buying-groups', { name: groupName })
  const order = await data.create<{ id: number; items: Item[] }>('/orders', {
    store_id: store.id, buying_group_id: group.id,
    items: [{ description: 'HP laptop', quantity: 2, status: 'submitted', price_sold: '199.00' }],
  })
  const item = order.items[0]
  const tracking = data.name('bg-upc-tracking')
  const shipment = await data.create('/shipments', { tracking_number: tracking, item_ids: [item.id] })
  await app.route('**/api/buying-groups/receiving-review', route => route.fulfill({ json: [{
    buying_group_id: group.id, buying_group_name: groupName,
    matches: [
      { item_id: item.id, order_id: order.id, description: 'HP laptop', quantity: 1,
        tracking_number: tracking, price_sold: '199.00', expected_amount: '199.00',
        received_ext_total: '199.00', reward: '6.00', receipt_id: 'BG-PARTIAL',
        receipt_upc: '199764359179', split_keep_quantity: 1, original_quantity: 2,
        match_kind: 'received', row_id: 'received', selected: true, amount_discrepancy: false,
        scanned_at: '2026-10-21T12:00:00Z' },
      { item_id: item.id, order_id: order.id, description: 'HP laptop', quantity: 1,
        tracking_number: tracking, price_sold: '199.00', expected_amount: '199.00',
        received_qty: 0, match_kind: 'not_received', row_id: 'missing', selected: false },
    ],
    unknown_receipts: [{ receipt_key: 'task', receipt_id: 'BG-PARTIAL', tracking_number: tracking,
      description: 'Task', receipt_upc: '987654321098', quantity: 1, received_status: 'Not confirmed', linkable: false }],
  }] }))
  await app.route('**/api/buying-groups/receiving-review/prune', route => route.fulfill({ json: { deleted: true } }))
  await app.goto('/receiving-review')
  await expect(app.getByText('Receipt UPC: 199764359179')).toBeVisible()
  await app.getByText('Unresolved receipt items (1 receipt)', { exact: true }).click()
  await expect(app.getByRole('button', { name: 'Link to order item' })).toBeDisabled()
  await app.getByRole('button', { name: 'Apply 1 selected' }).click()
  await expect(app.getByText('All listed rows were applied.')).toBeVisible()
  const saved = await data.get<{ items: Item[] }>(`/orders/${order.id}`)
  expect(saved.items).toHaveLength(2)
  expect(saved.items.find(i => i.id === item.id)).toMatchObject({
    quantity: 1, status: 'scanned', price_sold: '199.00', receipt_id: 'BG-PARTIAL',
    receipt_upc: '199764359179', reward: '6.00',
  })
  const missing = saved.items.find(i => i.id !== item.id)!
  expect(missing).toMatchObject({ quantity: 1, status: 'needs_review', receipt_id: null, receipt_upc: null, reward: null })
  const linked = await data.get<{ shipment_items: Array<{ item_id: number }> }>(`/shipments/${shipment.id}`)
  expect(linked.shipment_items.map(link => link.item_id).sort()).toEqual(saved.items.map(i => i.id).sort())
})

test('received reward is saved separately and shown beside payout on Orders', async ({ app, data }) => {
  const store = await data.create('/stores', { name: data.name('reward-store') })
  const group = await data.create('/buying-groups', { name: data.name('reward-group') })
  const orderNumber = data.name('reward-order')
  const order = await data.create<{ id: number; items: Item[] }>('/orders', {
    store_id: store.id, buying_group_id: group.id, store_order_number: orderNumber,
    items: [{ description: 'Reward headphones', quantity: 2, status: 'submitted', price_sold: '20.00' }],
  })
  const item = order.items[0]
  await app.route('**/api/buying-groups/receiving-review', route => route.fulfill({ json: [{
    buying_group_id: group.id, buying_group_name: data.name('reward-group'), unknown_receipts: [],
    matches: [{ item_id: item.id, order_id: order.id, description: item.description, quantity: 2,
      tracking_number: 'REWARD-TRACK', price_sold: '20.00', expected_amount: '40.00',
      received_ext_total: '40.00', reward: '6.50', receipt_id: 'REWARD-RECEIPT',
      scanned_at: '2026-10-15T12:00:00Z', selected: true, amount_discrepancy: false }],
  }] }))
  await app.route('**/api/buying-groups/receiving-review/prune', route => route.fulfill({ json: { deleted: true } }))
  await app.goto('/receiving-review')
  await expect(app.getByRole('columnheader', { name: 'Reward', exact: true })).toBeVisible()
  await expect(app.getByRole('cell', { name: '$6.50', exact: true })).toBeVisible()
  await app.getByRole('button', { name: 'Apply 1 selected' }).click()
  await expect(app.getByText('All listed rows were applied.')).toBeVisible()
  expect(await data.get<Item>(`/items/${item.id}`)).toMatchObject({ reward: '6.50', price_sold: '20.00', status: 'scanned' })
  await app.goto(`/?q=${encodeURIComponent(orderNumber)}`)
  await expect(app.getByRole('columnheader', { name: 'Reward', exact: true })).toBeVisible()
  await expect(app.locator('td[title="Total warehouse reward for this line"]')).toHaveText('$6.50')
  await expect(app.getByLabel('Payout price')).toHaveValue('20.00')
  await app.reload()
  await expect(app.locator('td[title="Total warehouse reward for this line"]')).toHaveText('$6.50')
})

test('receipt candidates only hide lines with receipts and show dates and expected payouts', async ({ app, data }) => {
  const store = await data.create('/stores', { name: data.name('matching-store') })
  const group = await data.create('/buying-groups', { name: data.name('matching-group') })
  await data.create('/orders', {
    store_id: store.id, buying_group_id: group.id, store_order_number: data.name('matching-order'),
    purchase_date: '2026-10-01T12:00:00Z',
    items: [
      { description: 'Unreceived headphones', quantity: 2, status: 'submitted', price_sold: '12.34',
        submitted_at: '2026-10-03T12:00:00Z' },
      { description: 'Received headphones', quantity: 2, status: 'scanned', price_sold: '23.45',
        submitted_at: '2026-10-04T12:00:00Z', scanned_at: '2026-10-05T12:00:00Z', receipt_id: 'EXISTING' },
      { description: 'Scanned without receipt', quantity: 1, status: 'scanned', price_sold: '10.00',
        scanned_at: '2026-10-06T12:00:00Z' },
    ],
  })
  await app.route('**/api/buying-groups/receiving-review', route => route.fulfill({ json: [{
    buying_group_id: group.id, buying_group_name: data.name('matching-group'), matches: [],
    unknown_receipts: [{ receipt_key: 'dates-payout', receipt_id: 'DATES', tracking_number: 'UNKNOWN', quantity: 2 }],
  }] }))
  await app.goto('/receiving-review')
  await app.getByText('Unknown tracking numbers (1 receipt)', { exact: true }).click()
  await app.getByRole('button', { name: 'Link to order item' }).click()
  const dialog = app.getByRole('dialog', { name: 'Link receipt to order items', exact: true })
  const showReceived = dialog.getByRole('checkbox', { name: 'Show lines with receipts', exact: true })
  await expect(showReceived).not.toBeChecked()
  await expect(dialog.getByText('2 order lines in this buying group')).toBeVisible()
  await expect(dialog.getByRole('checkbox', { name: /Scanned without receipt/ })).toBeVisible()
  await expect(dialog.getByRole('checkbox', { name: /Received headphones/ })).toHaveCount(0)
  await expect(dialog.getByText('Expected payout: $24.68 total · $12.34 per unit')).toBeVisible()
  await expect(dialog.getByText('Purchased: 2026-10-01 · Submitted: 2026-10-03 · Received: —')).toBeVisible()
  await showReceived.check()
  await expect(dialog.getByText('3 order lines in this buying group')).toBeVisible()
  await expect(dialog.getByRole('checkbox', { name: /Received headphones/ })).toBeVisible()
  await expect(dialog.getByText('Expected payout: $46.90 total · $23.45 per unit')).toBeVisible()
  await expect(dialog.getByText('Purchased: 2026-10-01 · Submitted: 2026-10-04 · Received: 2026-10-05')).toBeVisible()
  await showReceived.uncheck()
  await expect(dialog.getByText('2 order lines in this buying group')).toBeVisible()
  await expect(dialog.getByRole('checkbox', { name: /Received headphones/ })).toHaveCount(0)
})

test('unknown receipt selects whole items across searches and pages up to its total', async ({ app }) => {
  const receipt = { receipt_key: 'multi', receipt_id: 'MULTI', tracking_number: 'UNKNOWN', quantity: 3 }
  const candidates = [2, 1, 4].map((quantity, index) => ({
    item_id: 800 + index, order_id: 900 + index, store_order_number: `ORDER-${index}`,
    description: `Headphones ${index}`, quantity, status: 'submitted', receipt_id: null,
    receipt_tracking_number: null, tracking_numbers: [], payment_linked: false, store: 'Test store',
  }))
  const matches = candidates.slice(0, 2).map(item => ({
    ...item, tracking_number: receipt.tracking_number, receipt_tracking_number: receipt.tracking_number,
    receipt_id: receipt.receipt_id, row_id: `manual:multi:${item.item_id}`, manual_receipt_key: 'multi',
    received_ext_total: String(item.quantity * 25), expected_amount: String(item.quantity * 20),
  }))
  let staged = false
  const session = () => ({ buying_group_id: 901, buying_group_name: 'Multiple orders',
    matches: staged ? matches : [], unknown_receipts: staged ? [] : [receipt] })
  await app.route('**/api/buying-groups/receiving-review', route => route.fulfill({ json: [session()] }))
  await app.route('**/api/buying-groups/receiving-review/candidates?*', route => {
    const params = new URL(route.request().url()).searchParams
    const items = params.get('q') ? [candidates[1]] : params.get('page') === '2' ? [candidates[1]] : [candidates[0], candidates[2]]
    return route.fulfill({ json: { items, total: params.get('q') ? 1 : 26 } })
  })
  await app.route('**/api/buying-groups/receiving-review/link', route => {
    expect(route.request().postDataJSON().item_ids.sort()).toEqual([800, 801])
    expect(route.request().postDataJSON().split_quantity).toBeUndefined()
    staged = true
    return route.fulfill({ json: session() })
  })
  await app.route('**/api/buying-groups/receiving-review/unlink', route => {
    expect(route.request().postDataJSON().receipt_key).toBe('multi')
    staged = false
    return route.fulfill({ json: session() })
  })
  await app.goto('/receiving-review')
  await app.getByText('Unknown tracking numbers (1 receipt)', { exact: true }).click()
  await app.getByRole('button', { name: 'Link to order item' }).click()
  const dialog = app.getByRole('dialog', { name: 'Link receipt to order items', exact: true })
  const add = dialog.getByRole('button', { name: 'Add to review', exact: true })
  const candidateCheckboxes = dialog.getByRole('checkbox', { name: /ORDER-/ })
  await expect(candidateCheckboxes.nth(1)).toBeDisabled()
  await candidateCheckboxes.first().check()
  await expect(add).toBeDisabled()
  await dialog.getByRole('button', { name: 'Next', exact: true }).click()
  await expect(candidateCheckboxes).toHaveCount(1)
  await candidateCheckboxes.check()
  await expect(dialog.getByText('Selected 3 of 3 units across 2 order items.')).toBeVisible()
  await expect(add).toBeEnabled()
  await dialog.getByLabel('Search order number, description, or receipt ID').fill('Headphones 1')
  await expect(candidateCheckboxes).toBeChecked()
  await expect(add).toBeEnabled()
  await dialog.getByRole('button', { name: 'Remove Headphones 0', exact: true }).click()
  await expect(add).toBeDisabled()
  await dialog.getByLabel('Search order number, description, or receipt ID').fill('')
  await dialog.getByRole('checkbox', { name: /ORDER-0/ }).check()
  await add.click()
  const selections = app.getByRole('checkbox', { name: /Select item Headphones/ })
  await expect(selections).toHaveCount(2)
  await selections.first().uncheck()
  await expect(selections.nth(1)).not.toBeChecked()
  await selections.first().check()
  await expect(selections.nth(1)).toBeChecked()
  await app.getByRole('button', { name: 'Unlink', exact: true }).first().click()
  await app.getByText('Unknown tracking numbers (1 receipt)', { exact: true }).click()
  await expect(app.getByRole('button', { name: 'Link to order item' })).toBeVisible()
  await expect(selections).toHaveCount(0)
})

test('unknown receipts are visible within each buying group, including groups with no matches', async ({ app }) => {
  await app.route('**/api/buying-groups/receiving-review', route => route.fulfill({
    json: [
      {
        buying_group_id: 901, buying_group_name: 'Warehouse Alpha', matches: [],
        unknown_receipts: [{
          receipt_id: 'receipt-alpha', tracking_number: 'UNKNOWN-ALPHA',
          tracking_numbers: ['UNKNOWN-ALPHA', 'ALTERNATE-ALPHA'],
          bg_order_id: 'group-order-alpha', description: 'Wireless headphones', quantity: 2,
          received_price: '25.00', received_ext_total: '50.00', reward: '7.25', received_status: 'Received',
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
  await alpha.getByText('Unknown tracking numbers (1 receipt)', { exact: true }).click()
  await beta.getByText('Unknown tracking numbers (1 receipt)', { exact: true }).click()
  for (const text of ['UNKNOWN-ALPHA', 'ALTERNATE-ALPHA', 'Wireless headphones', '$25.00', '$50.00', '$7.25',
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
  await alpha.getByText('Unknown tracking numbers (1 receipt)', { exact: true }).click()
  await beta.getByText('Unknown tracking numbers (1 receipt)', { exact: true }).click()
  await expect(alpha.getByText('UNKNOWN-ALPHA', { exact: true })).toBeVisible()
  await expect(beta.getByText('UNKNOWN-BETA', { exact: true })).toBeVisible()
})

test('manual receipt review requires a manual split, then supports unlink and apply', async ({ app, data }) => {
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
      item_ids: [item.id], receipt_tracking_number: 'WRONG-SCAN' })
    expect(body.split_quantity).toBeUndefined()
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
  await app.getByText('Unknown tracking numbers (1 receipt)', { exact: true }).click()
  await app.getByRole('button', { name: 'Link to order item' }).click()
  const dialog = app.getByRole('dialog', { name: 'Link receipt to order items', exact: true })
  await dialog.getByLabel('Search order number, description, or receipt ID').fill(orderNumber)
  const candidateCheckbox = dialog.getByRole('checkbox', { name: /Receipt test headphones/ })
  await expect(candidateCheckbox).toBeDisabled()
  await expect(dialog.getByText('Exceeds the remaining receipt quantity.', { exact: false })).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Add to review', exact: true })).toBeDisabled()
  expect((await data.get<Item>(`/items/${item.id}`)).quantity).toBe(3)
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  const split = await data.request.post(`/api/items/${item.id}/split`, {
    headers: data.headers, data: { keep_quantity: 1, copy_tracking: true },
  })
  expect(split.ok()).toBeTruthy()
  await app.getByRole('button', { name: 'Link to order item' }).click()
  await candidateCheckbox.check()
  await dialog.getByRole('button', { name: 'Add to review', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await expect(app.getByText('Manually linked', { exact: false })).toBeVisible()
  expect((await data.get<Item>(`/items/${item.id}`)).receipt_id).toBeNull()
  await app.getByRole('button', { name: 'Unlink', exact: true }).click()
  await app.getByText('Unknown tracking numbers (1 receipt)', { exact: true }).click()
  await app.getByRole('button', { name: 'Link to order item' }).click()
  await candidateCheckbox.check()
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
  await expect(app.getByPlaceholder('Description').first()).toHaveValue('Receipt test headphones')
  await expect(app.getByLabel('Receipt ID', { exact: true })).toHaveCount(0)
  await expect(app.getByLabel('Receipt tracking number', { exact: true })).toHaveCount(0)
  expect(await data.get<Item>(`/items/${item.id}`)).toMatchObject({
    receipt_id: receipt.receipt_id,
    receipt_tracking_number: 'WRONG-SCAN',
  })
})

for (const provider of ['bfmr', 'usabg', 'dealandrunner', 'parsefile']) {
  test(`${provider} UPC-only receipt backfill preserves received item details`, async ({ app, data }) => {
    const store = await data.create('/stores', { name: data.name('upc-store') })
    const groupName = data.name(`${provider}-upc-group`)
    const group = await data.create('/buying-groups', { name: groupName, api_framework: provider })
    const order = await data.create<{ id: number; items: Item[] }>('/orders', {
      store_id: store.id, buying_group_id: group.id,
      items: [{ description: 'Received headphones', quantity: 2, status: 'scanned',
        receipt_id: 'UPC-RECEIPT', scanned_at: '2026-10-01T14:33:15Z',
        price_sold: '21.00', reward: '2.50' }],
    })
    const item = order.items[0]
    const before = await data.get<Item>(`/items/${item.id}`)
    await app.route('**/api/buying-groups/receiving-review', route => route.fulfill({ json: [{
      buying_group_id: group.id, buying_group_name: groupName, unknown_receipts: [],
      matches: [{ item_id: item.id, order_id: order.id, description: item.description, quantity: 2,
        tracking_number: 'UPC-TRACK', price_sold: '21.00', expected_amount: '42.00',
        receipt_id: 'UPC-RECEIPT', receipt_upc: '000012345678', scanned_at: null,
        received_ext_total: null, reward: null, update_receipt_only: true,
        selected: true, amount_discrepancy: false }],
    }] }))
    await app.route('**/api/buying-groups/receiving-review/prune', route => route.fulfill({ json: { deleted: true } }))
    await app.goto('/receiving-review')
    await expect(app.getByText('Receipt UPC: 000012345678')).toBeVisible()
    await app.getByRole('button', { name: 'Apply 1 selected' }).click()
    await expect(app.getByText('All listed rows were applied.')).toBeVisible()
    const after = await data.get<Item>(`/items/${item.id}`)
    expect(after).toMatchObject({ receipt_upc: '000012345678', receipt_id: before.receipt_id,
      quantity: before.quantity, status: before.status, price_sold: before.price_sold,
      reward: before.reward, scanned_at: before.scanned_at })
  })
}
