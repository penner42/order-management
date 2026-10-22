import { test, expect } from './fixtures'

function session(receiptId: string) {
  return [{
    buying_group_id: 987654, buying_group_name: 'Refresh test group', unknown_receipts: [],
    matches: [{ item_id: 123, order_id: 123, description: 'Refresh headphones', quantity: 1,
      tracking_number: 'REFRESH-TRACK', price_sold: '20.00', expected_amount: '20.00',
      received_ext_total: '20.00', receipt_id: receiptId, scanned_at: '2026-10-21T12:00:00Z',
      selected: true, amount_discrepancy: false }],
  }]
}

test('Receiving reloads on navigation and deduplicates tab-return events', async ({ app }) => {
  let receipt = 'FIRST'
  let requests = 0
  await app.route('**/api/buying-groups/receiving-review', route => {
    requests++
    return route.fulfill({ json: session(receipt) })
  })
  await app.goto('/receiving-review')
  await expect(app.getByLabel('Receipt ID', { exact: true })).toHaveValue('FIRST')
  receipt = 'SECOND'
  await app.goto('/')
  await app.getByRole('link', { name: 'Receiving', exact: true }).click()
  await expect(app.getByLabel('Receipt ID', { exact: true })).toHaveValue('SECOND')
  const before = requests
  receipt = 'THIRD'
  await app.evaluate(() => {
    window.dispatchEvent(new Event('focus'))
    document.dispatchEvent(new Event('visibilitychange'))
  })
  await expect(app.getByLabel('Receipt ID', { exact: true })).toHaveValue('THIRD')
  expect(requests).toBe(before + 1)
})

test('tab return preserves unsaved edits until explicit reload', async ({ app }) => {
  let receipt = 'FIRST'
  await app.route('**/api/buying-groups/receiving-review', route => route.fulfill({ json: session(receipt) }))
  await app.goto('/receiving-review')
  const input = app.getByLabel('Receipt ID', { exact: true })
  await expect(input).toHaveValue('FIRST')
  await input.fill('UNSAVED')
  receipt = 'SECOND'
  await app.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect(app.getByText(/Reloading discards unsaved receiving edits/)).toBeVisible()
  await expect(input).toHaveValue('UNSAVED')
  await app.getByRole('button', { name: 'Reload current matches' }).click()
  await expect(input).toHaveValue('SECOND')
})

test('failed background refresh retains matches and allows retry', async ({ app }) => {
  let fail = false
  await app.route('**/api/buying-groups/receiving-review', route => fail
    ? route.fulfill({ status: 500, json: { detail: 'Refresh unavailable' } })
    : route.fulfill({ json: session('SAVED') }))
  await app.goto('/receiving-review')
  await expect(app.getByLabel('Receipt ID', { exact: true })).toHaveValue('SAVED')
  fail = true
  await app.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect(app.getByText(/Could not refresh matches/)).toBeVisible()
  await expect(app.getByLabel('Receipt ID', { exact: true })).toHaveValue('SAVED')
  fail = false
  await app.getByRole('button', { name: 'Reload current matches' }).click()
  await expect(app.getByText(/Could not refresh matches/)).toHaveCount(0)
})

test('edits made during a background refresh survive its response', async ({ app }) => {
  let refreshing = false
  let started = false
  let release = () => {}
  const waiting = new Promise<void>(resolve => { release = resolve })
  await app.route('**/api/buying-groups/receiving-review', async route => {
    if (refreshing) { started = true; await waiting }
    await route.fulfill({ json: session(refreshing ? 'SECOND' : 'FIRST') })
  })
  await app.goto('/receiving-review')
  const input = app.getByLabel('Receipt ID', { exact: true })
  await expect(input).toHaveValue('FIRST')
  refreshing = true
  await app.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect.poll(() => started).toBe(true)
  await input.fill('NEW EDIT')
  release()
  await expect(app.getByText(/Reloading discards unsaved receiving edits/)).toBeVisible()
  await expect(input).toHaveValue('NEW EDIT')
})
