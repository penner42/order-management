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

test('Existing payments can be selected by payment ID and amount and the link persists', async ({ app }) => {
  const group = fixture()
  const localPayment = {
    id: 42, buying_group_id: 170001, payment_id: 'existing-reference', total_amount: 100,
    payment_requested_at: '2026-05-02T12:00:00Z', line_items: [],
  }
  await app.route('**/api/payment-review/capabilities', route => route.fulfill({ json: [group.capability] }))
  await app.route('**/api/payment-review/groups/170001', route => route.fulfill({ json: group }))
  await app.route('**/api/payments?buying_group_id=170001', route => route.fulfill({ json: [
    localPayment,
    { ...localPayment, id: 43, payment_id: 'linked-reference', source_details: { provider: 'parsefile', review_id: 2 } },
    { ...localPayment, id: 44, payment_id: 'confirmed-reference', monarch_receipt: { confirmation_id: 3 } },
    { ...localPayment, id: 45, buying_group_id: 170002, payment_id: 'different-group-reference' },
    { ...localPayment, id: 46, payment_id: null },
  ] }))
  const selections: Array<number | null> = []
  await app.route('**/api/payment-review/drafts/1', async route => {
    const body = route.request().postDataJSON()
    selections.push(body.content.existing_payment_id)
    group.reviews[0].content = body.content
    group.reviews[0].version += 1
    await route.fulfill({ json: group })
  })
  await app.goto('/payments/review?group=170001')
  const selector = app.getByRole('combobox', { name: 'Link an existing local payment', exact: true })
  await expect(selector.locator('option[value="42"]')).toHaveText(/existing-reference · \$100\.00/)
  await expect(selector.locator('option[value="43"]')).toHaveJSProperty('disabled', true)
  await expect(selector.locator('option[value="44"]')).toHaveJSProperty('disabled', true)
  await expect(selector.locator('option').filter({ hasText: 'different-group-reference' })).toHaveCount(0)
  await expect(selector.locator('option[value="46"]')).toHaveText(/No payment ID · \$100\.00/)
  await selector.selectOption({ label: await selector.locator('option[value="42"]').innerText() })
  await app.getByRole('button', { name: 'Save review', exact: true }).click()
  await expect(selector).toHaveValue('42')
  await app.reload()
  await expect(selector).toHaveValue('42')
  await selector.selectOption({ label: 'Create a new payment' })
  await app.getByRole('button', { name: 'Save review', exact: true }).click()
  await expect(selector).toHaveValue('')
  expect(selections).toEqual([42, null])
})

test('Saving and confirming preserve open payments and other unsaved review edits', async ({ app }) => {
  const group = fixture()
  group.reviews.push({ ...structuredClone(group.reviews[0]), id: 2, external_id: 'payment-2' })
  await app.route('**/api/payment-review/capabilities', route => route.fulfill({ json: [group.capability] }))
  await app.route('**/api/payment-review/groups/170001', route => route.fulfill({ json: group }))
  await app.route('**/api/payment-review/drafts/1', async route => {
    group.reviews[0].content = route.request().postDataJSON().content
    for (const review of group.reviews) review.version += 1
    await route.fulfill({ json: group })
  })
  await app.route('**/api/payment-review/drafts/1/confirm', async route => {
    group.reviews[0].payment_id = 99
    for (const review of group.reviews) review.version += 1
    await route.fulfill({ json: group })
  })
  await app.goto('/payments/review?group=170001')
  const older = app.locator('details').filter({ hasText: 'Payment payment-1' })
  const newer = app.locator('details').filter({ hasText: 'Payment payment-2' })
  await expect(older).toHaveJSProperty('open', false)
  await expect(newer).toHaveJSProperty('open', true)
  await older.locator('summary').click()
  await newer.getByRole('textbox', { name: 'Review explanation', exact: true }).fill('Unsaved edits in another payment')
  await older.getByRole('textbox', { name: 'Review explanation', exact: true }).fill('Save this explanation')
  await older.getByLabel('Search local items', { exact: true }).fill('Example')
  await older.getByLabel('Show other receipts to include', { exact: true }).check()
  await older.getByRole('button', { name: 'Save review', exact: true }).click()
  await expect(older.getByRole('button', { name: 'Save review', exact: true })).toBeDisabled()
  await expect(older).toHaveJSProperty('open', true)
  await expect(newer).toHaveJSProperty('open', true)
  await expect(newer.getByRole('textbox', { name: 'Review explanation', exact: true })).toHaveValue('Unsaved edits in another payment')
  await expect(older.getByLabel('Search local items', { exact: true })).toHaveValue('Example')
  await expect(older.getByLabel('Show other receipts to include', { exact: true })).toBeChecked()
  await newer.locator('summary').click()
  await older.getByRole('button', { name: 'Confirm payment', exact: true }).click()
  await expect(older.getByRole('link', { name: 'Confirmed as payment #99' })).toBeVisible()
  await expect(older).toHaveJSProperty('open', true)
  await expect(newer).toHaveJSProperty('open', false)
  await newer.locator('summary').click()
  await expect(newer.getByRole('textbox', { name: 'Review explanation', exact: true })).toHaveValue('Unsaved edits in another payment')
})

test('Save and confirmation failures appear beside the payment action buttons', async ({ app }) => {
  const group = fixture()
  let failSave = true
  await app.route('**/api/payment-review/capabilities', route => route.fulfill({ json: [group.capability] }))
  await app.route('**/api/payment-review/groups/170001', route => route.fulfill({ json: group }))
  await app.route('**/api/payment-review/drafts/1', async route => {
    if (failSave) {
      await route.fulfill({ status: 409, json: { detail: 'Could not save this review.' } })
    } else {
      group.reviews[0].content = route.request().postDataJSON().content
      group.reviews[0].version += 1
      await route.fulfill({ json: group })
    }
  })
  await app.route('**/api/payment-review/drafts/1/confirm', route => route.fulfill({ status: 409, json: { detail: 'Selected payment allocations changed.' } }))
  await app.goto('/payments/review?group=170001')
  const section = app.getByRole('region', { name: 'Payment payment-1', exact: true })
  await section.getByRole('textbox', { name: 'Review explanation', exact: true }).fill('Keep this draft after an error')
  await section.getByRole('button', { name: 'Save review', exact: true }).click()
  await expect(section.getByRole('alert')).toHaveText('Could not save this review.')
  failSave = false
  await section.getByRole('button', { name: 'Save review', exact: true }).click()
  await expect(section.getByRole('alert')).toHaveCount(0)
  await section.getByRole('button', { name: 'Confirm payment', exact: true }).click()
  await expect(section.getByRole('alert')).toHaveText('Selected payment allocations changed.')
  const action = await section.getByRole('button', { name: 'Confirm payment', exact: true }).boundingBox()
  const error = await section.getByRole('alert').boundingBox()
  expect(error!.y).toBeGreaterThanOrEqual(action!.y + action!.height)
  await expect(section.getByRole('textbox', { name: 'Review explanation', exact: true })).toHaveValue('Keep this draft after an error')
})
