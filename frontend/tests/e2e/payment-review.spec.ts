import { test, expect } from './fixtures'

function fixture() {
  return {
    capability: { buying_group_id: 170001, name: 'Example Parsefile', provider: 'parsefile', supported: true, ready: true, reason: null },
    receipts: [{ id: 2, external_id: 'receipt-1', covered: false, present: true,
      payload: { amount: '100.00', quantity: 1, description: 'Example received item', created_at: '2026-05-01T12:00:00Z', void: false, tracking_numbers: ['tracking-1'] } }],
    items: [{ id: 3, description: 'Example local item', quantity: 1, price_sold: '100.00', receipt_id: 'receipt-1', remaining: '100.00', status: 'scanned', order_id: 4 }],
    corrections: [],
    reviews: [{ id: 1, version: 1, fingerprint: 'shown-evidence', payment_id: null as number | null, external_id: 'payment-1',
      source: { amount: '100.00', status: 'PAID', created_at: '2026-05-02T12:00:00Z', sent_at: '2026-05-05T12:00:00Z', memo: 'Example payment', warnings: [] },
      content: { receipt_ids: [2], allocations: [{ receipt_source_id: 2, item_id: 3, quantity: 1, amount: '100.00' }], corrections: [],
        window_start: null, window_end: '2026-05-02T12:00:00Z', note: '', manual: false },
      allocated_total: '100.00', difference: '0.00', issues: [] as string[] }],
  }
}

for (const width of [1280, 390]) {
  test(`Payment review edits and confirms displayed evidence at width ${width}`, async ({ app }) => {
    await app.setViewportSize({ width, height: 900 })
    const group = fixture()
    await app.route('**/api/payment-review/capabilities', route => route.fulfill({ json: [group.capability] }))
    await app.route('**/api/payment-review/groups/170001', route => route.fulfill({ json: group }))
    await app.route('**/api/payment-review/drafts/1', async route => {
      const body = route.request().postDataJSON()
      expect(body.version).toBe(group.reviews[0].version)
      group.reviews[0].content = body.content
      group.reviews[0].version += 1
      group.reviews[0].issues = body.content.allocations[0].amount === '100.00' ? [] : ['Receipt allocations do not equal its source amount.']
      await route.fulfill({ json: group })
    })
    let confirmed = false
    await app.route('**/api/payment-review/drafts/1/confirm', async route => {
      expect(route.request().postDataJSON()).toEqual({ version: group.reviews[0].version, fingerprint: 'shown-evidence' })
      confirmed = true
      group.reviews[0].payment_id = 99
      group.reviews[0].version += 1
      await route.fulfill({ json: group })
    })
    await app.goto('/payments/review?group=170001')
    await expect(app.getByRole('heading', { name: 'Payment review', exact: true })).toBeVisible()
    const section = app.getByRole('region', { name: 'Payment payment-1', exact: true })
    await expect(section.getByRole('button', { name: 'Confirm payment', exact: true })).toBeEnabled()
    await section.getByLabel('Allocation 1 amount', { exact: true }).fill('90.00')
    await expect(section.getByRole('button', { name: 'Confirm payment', exact: true })).toBeDisabled()
    await section.getByRole('button', { name: 'Save review', exact: true }).click()
    await expect(section.getByText('Receipt allocations do not equal its source amount.', { exact: true })).toBeVisible()
    await section.getByLabel('Allocation 1 amount', { exact: true }).fill('100.00')
    await section.getByRole('button', { name: 'Save review', exact: true }).click()
    await expect(section.getByRole('button', { name: 'Confirm payment', exact: true })).toBeEnabled()
    await section.getByRole('button', { name: 'Confirm payment', exact: true }).click()
    await expect(section.getByRole('link', { name: 'Confirmed as payment #99' })).toBeVisible()
    expect(confirmed).toBeTruthy()
  })
}

test('Payment review preserves its snapshot when refresh fails', async ({ app }) => {
  const group = fixture()
  await app.route('**/api/payment-review/capabilities', route => route.fulfill({ json: [group.capability] }))
  await app.route('**/api/payment-review/groups/170001', route => route.fulfill({ json: group }))
  await app.route('**/api/payment-review/groups/170001/refresh', route => route.fulfill({ status: 502, json: { detail: 'Could not read a complete payment history. Existing review data was preserved.' } }))
  await app.goto('/payments/review?group=170001')
  await app.getByRole('button', { name: 'Check payments', exact: true }).click()
  await expect(app.getByRole('alert')).toContainText('Existing review data was preserved')
  await expect(app.getByRole('heading', { name: 'Payment payment-1 · $100.00' })).toBeVisible()
})
