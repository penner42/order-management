import { test, expect } from './fixtures'
import type { Order, Payment } from '../../src/api/types'

function fixture() {
  return {
    capability: { buying_group_id: 170001, name: 'Example Parsefile', provider: 'parsefile', supported: true, ready: true, reason: null },
    receipts: [{ id: 2, external_id: 'receipt-1', covered: false, present: true,
      payload: { amount: '100.00', quantity: 1, description: 'Example received item', created_at: '2026-05-01T12:00:00Z', void: false, tracking_numbers: ['tracking-1'] } }],
    items: [{ id: 3, description: 'Example local item', quantity: 1, price_sold: '100.00', receipt_id: 'receipt-1', remaining: '100.00', status: 'scanned', order_id: 4, store_order_number: 'EXAMPLE-ORDER-1' }],
    corrections: [],
    reviews: [{ id: 1, version: 1, fingerprint: 'shown-evidence', payment_id: null as number | null, external_id: 'payment-1',
      source: { amount: '100.00', status: 'PAID', created_at: '2026-05-02T12:00:00Z', sent_at: '2026-05-05T12:00:00Z', memo: 'Example payment', warnings: [] },
      content: { receipt_ids: [2], allocations: [{ receipt_source_id: 2, item_id: 3, quantity: 1, amount: '100.00' }], corrections: [],
        window_start: null, window_end: '2026-05-02T12:00:00Z', note: '', manual: false, existing_payment_id: null as number | null },
      allocated_total: '100.00', difference: '0.00', issues: [] as string[], issue_links: {} as Record<string, { label: string; order_id: number }> }],
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
    await expect(section.getByRole('link', { name: 'View confirmed payment payment-1' })).toBeVisible()
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

test('Installment review shows receipt balance, provider share, and D&R date context', async ({ app }) => {
  const original = fixture()
  const group = {
    ...original,
    capability: { ...original.capability, name: 'Example D&R', provider: 'dealandrunner' },
    receipts: original.receipts.map(r => ({ ...r, baseline_amount: '100.00', paid_amount: '40.00', remaining_amount: '60.00' })),
    reviews: original.reviews.map(r => ({ ...r,
      source: { ...r.source, amount: '60.00', status: 'COMPLETED',
        created_at: '2026-05-02T12:00:00Z', sent_at: '2026-05-11T12:01:00Z',
        receipt_amounts: { 'receipt-1': '60.00' },
        memo: 'Request date uses the scheduled payout date. Sent date uses the latest matched payout transaction.',
        evidence: { scheduled_date: '2026-05-02T12:00:00Z', lock_date: '2026-05-03T12:00:00Z', transactions: [
          { reference: 'payout-a', amount: '30.00', posted_at: '2026-05-11T12:00:00Z' },
          { reference: 'payout-b', amount: '30.00', posted_at: '2026-05-11T12:01:00Z' },
        ] } },
      content: { ...r.content, allocations: [{ receipt_source_id: 2, item_id: 3, quantity: 1, amount: '60.00' }] },
      allocated_total: '60.00',
    })),
  }
  await app.route('**/api/payment-review/capabilities', route => route.fulfill({ json: [group.capability] }))
  await app.route('**/api/payment-review/groups/170001', route => route.fulfill({ json: group }))
  await app.route('**/api/payment-review/drafts/1/confirm', async route => {
    expect(route.request().postDataJSON()).toEqual({ version: 1, fingerprint: 'shown-evidence' })
    group.reviews[0].payment_id = 99
    group.receipts[0].paid_amount = '100.00'
    group.receipts[0].remaining_amount = '0.00'
    group.receipts[0].covered = true
    await route.fulfill({ json: group })
  })
  await app.goto('/payments/review?group=170001')
  const section = app.getByRole('region', { name: 'Payment payment-1', exact: true })
  await expect(section.getByText('Allocated across payments $40.00 · Remaining $60.00 · This payment $60.00 · Provider share $60.00')).toBeVisible()
  await expect(section.getByText(/2 matched payout transactions/)).toBeVisible()
  await expect(section.getByLabel('Allocation 1 quantity', { exact: true })).toHaveValue('1')
  await section.getByRole('button', { name: 'Confirm payment', exact: true }).click()
  await expect(section.getByRole('link', { name: 'View confirmed payment payment-1' })).toBeVisible()
  await expect(section.getByText(/Allocated across payments \$100.00 · Remaining \$0.00/)).toBeVisible()
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
  await expect(older.getByRole('link', { name: 'View confirmed payment payment-1' })).toBeVisible()
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

test('Recognizable item issues Ctrl-click to the order and selected payment editor', async ({ app, data }) => {
  const store = await data.create('/stores', { name: data.name('store') })
  const buyingGroup = await data.create('/buying-groups', { name: data.name('group') })
  const orderNumber = data.name('store-order')
  const description = data.name('received-item')
  const order = await data.create<Order>('/orders', {
    store_id: store.id, buying_group_id: buyingGroup.id, store_order_number: orderNumber,
    items: [{ description, status: 'scanned', quantity: 1, price_sold: 100, receipt_id: 'warehouse-receipt' }],
  })
  const payment = await data.create<Payment>('/payments', { buying_group_id: buyingGroup.id, payment_id: 'portal-payment' })
  const item = order.items[0]
  const label = `Order ${orderNumber} · ${description} · Receipt warehouse-receipt`
  const issue = `${label} has $100.00 in this review but is not on the selected local payment.`
  const group = fixture()
  group.capability.buying_group_id = buyingGroup.id
  group.items[0] = { id: item.id, description, quantity: 1, price_sold: '100.00', receipt_id: 'warehouse-receipt',
    remaining: '100.00', status: 'scanned', order_id: order.id, store_order_number: orderNumber }
  group.reviews[0].content.existing_payment_id = payment.id
  group.reviews[0].content.allocations[0].item_id = item.id
  group.reviews[0].issues = [issue]
  group.reviews[0].issue_links = { [issue]: { label, order_id: order.id } }
  await app.route('**/api/payment-review/capabilities', route => route.fulfill({ json: [group.capability] }))
  await app.route(`**/api/payment-review/groups/${buyingGroup.id}`, route => route.fulfill({ json: group }))
  await app.goto(`/payments/review?group=${buyingGroup.id}`)
  const section = app.getByRole('region', { name: 'Payment payment-1', exact: true })
  await expect(section).not.toContainText(`Item #${item.id}`)
  const orderLink = section.getByRole('link', { name: label, exact: true })
  await expect(orderLink).toHaveAttribute('href', `/?order_id=${order.id}`)
  const orderPopupPromise = app.context().waitForEvent('page')
  await orderLink.click({ modifiers: ['Control'] })
  const orderPopup = await orderPopupPromise
  await expect(orderPopup.getByRole('region', { name: `Order ${order.id}`, exact: true })).toBeVisible()
  await orderPopup.close()
  const paymentPopupPromise = app.context().waitForEvent('page')
  await section.getByRole('link', { name: 'Edit selected payment', exact: true }).click({ modifiers: ['Control'] })
  const editor = await paymentPopupPromise
  const dialog = editor.getByRole('dialog', { name: 'Edit payment', exact: true })
  await expect(dialog.getByRole('textbox', { name: 'Payment ID', exact: true })).toHaveValue('portal-payment')
  await dialog.getByRole('row').filter({ hasText: description }).getByRole('checkbox').check()
  const saved = editor.waitForResponse(response => new URL(response.url()).pathname === `/api/payments/${payment.id}/line-items` && response.request().method() === 'POST')
  await dialog.getByRole('button', { name: 'Add selected (1)', exact: true }).click()
  expect((await saved).ok()).toBeTruthy()
  const updated = await data.get<Payment>(`/payments/${payment.id}`)
  expect(updated.line_items.map(line => line.item_id)).toEqual([item.id])
  expect(updated.total_amount).toBe(100)
  await editor.close()
  await expect(section).toBeVisible()
})
