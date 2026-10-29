import { test, expect } from './fixtures'
import type { Page } from '@playwright/test'
import { availableQuantity, cents, decimal, remaining, validate } from '../../src/utils/paymentLinking'
import type { Workspace } from '../../src/utils/paymentLinking'

function fixture(id = 99) {
  return {
    id, buying_group_id: 170001, payment_id: id === 99 ? 'payment-1' : 'payment-2', buying_group: {id: 170001, name: 'Example Parsefile'},
    ledger_state: 'imported', source_amount: 100, total_amount: 100, allocation_version: 1, allocation_manual: false,
    payment_requested_at: '2026-01-05T12:00:00Z', payment_sent_at: '2026-01-08T12:00:00Z', payment_received_at: null,
    allocation_details: {note: ''},
    allocation_breakdown: {linked_amount: '60', correction_amount: '0', historical_amount: '0', adjustment_amount: '0', unresolved_amount: '40'},
    source_details: {provider: 'parsefile', external_id: 'payment-1', present: true, source: {status: 'PAID', void: false}, receipts: []},
    sync_issues: ['Item allocations do not yet reconcile to the source payment.'],
    line_items: [{id: 1, item_id: 3, amount: '60', item: {description: 'Example local item',order_id: 4}}],
    monarch_receipt: null, receipt_conflict: false,
  }
}
const capability = {buying_group_id: 170001, name: 'Example Parsefile', ready: true, supported: true, reason: null,
  payment_sync_enabled: true, last_run_at: '2026-10-27T12:00:00Z', error: null}
function workspace(): Workspace {
  return {
    payment: {id: 99, buying_group_id: 170001, payment_id: 'payment-1', group_name: 'Example Parsefile',
      allocation_version: 1, allocation_manual: false, payment_requested_at: '2026-01-05T12:00:00Z',
      payment_sent_at: '2026-01-08T12:00:00Z', status: 'PAID', total_amount: '100.00',
      allocation_details: {note: ''}, allocation_breakdown: {historical_amount: '0', adjustment_amount: '0', correction_amount: '0'},
      sync_issues: [], monarch_receipt: null, receipt_conflict: false, corrections: []},
    allocations: [{receipt_source_id: 2, item_id: 3, quantity: 1, amount: '60.00'}],
    receipts: [
      {id: 2, external_id: 'receipt-1', associated: true, description: 'Example receipt', quantity: 1,
        amount: '60', baseline_amount: '60', other_amount: '0', present: true, void: false, tracking_numbers: [], other_commitments: []},
      {id: 4, external_id: 'receipt-2', associated: true, description: 'Second receipt', quantity: 1,
        amount: '100', baseline_amount: '100', other_amount: '60', present: true, void: false, tracking_numbers: ['TRACK-2'], other_commitments: [{item_id: 5, quantity: 1}]},
      {id: 6, external_id: 'receipt-extra', associated: false, description: 'Extra group receipt', quantity: 1,
        amount: '10', baseline_amount: '10', other_amount: '0', present: true, void: false, tracking_numbers: [], other_commitments: []},
    ],
    items: [
      {id: 3, description: 'Example local item', quantity: 1, order_id: 4, order_number: 'ORDER-4', store: 'Store',
        receipt_id: 'receipt-1', tracking_numbers: [], eligible: true, status: 'scanned', payable_amount: '60', other_amount: '0', correction_amount: '0', other_commitments: []},
      {id: 5, description: 'Partial local item', quantity: 1, order_id: 8, order_number: 'ORDER-8', store: 'Store',
        receipt_id: 'receipt-2', tracking_numbers: ['TRACK-2'], eligible: true, status: 'scanned', payable_amount: '100', other_amount: '60', correction_amount: '0', other_commitments: [{receipt_source_id: 4, quantity: 1}]},
    ],
  }
}
async function mock(app: Page, w = workspace()) {
  await app.route('**/api/payments', r => r.fulfill({json: [fixture(), fixture(100)]}))
  await app.route('**/api/payment-review/capabilities', r => r.fulfill({json: [capability]}))
  await app.route('**/api/payments/*/linking-workspace', r => r.fulfill({json: r.request().url().includes('/100/') ? {...w, payment: {...w.payment, id: 100, payment_id: 'payment-2'}} : w}))
  return w
}

for (const width of [1280, 390]) {
  test(`Focus, search, link partial items and save at width ${width}`, async ({app}) => {
    await app.setViewportSize({width, height: 900})
    const w = await mock(app)
    await app.route('**/api/payments/99/allocations', async r => {
      const body = r.request().postDataJSON()
      expect(body.version).toBe(1)
      expect(body.allocations).toEqual([...w.allocations, {receipt_source_id: 4, item_id: 5, quantity: 1, amount: '40.00'}])
      expect(body.note).toContain('1 added or updated')
      w.allocations = body.allocations; w.payment.allocation_version++
      w.payment.allocation_details.note = body.note
      await r.fulfill({json: {allocation_version: 2}})
    })
    await app.goto('/payments?group=170001&view=attention')
    await app.getByRole('region', {name: 'Payment payment-1', exact: true}).getByRole('link', {name: 'Open payment'}).click()
    await expect(app.getByRole('heading', {name: 'Payment payment-1', exact: true})).toBeVisible()
    await expect(app.getByRole('region', {name: 'Payment payment-2', exact: true})).toHaveCount(0)
    await expect(app.getByRole('link', {name: 'Back to payments'})).toHaveAttribute('href', '/payments?group=170001&view=attention')
    await app.getByRole('radio', {name: 'Select receipt receipt-2', exact: true}).check()
    await app.getByRole('searchbox').fill('TRACK-2')
    await expect(app.getByText('Partially covered · Other payments $60.00')).toBeVisible()
    await app.getByRole('checkbox', {name: 'Select order ORDER-8'}).check()
    await app.getByRole('button', {name: 'Link selected (1)'}).click()
    await expect(app.getByText('Unresolved $0.00', {exact: true})).toBeVisible()
    await expect(app.getByRole('status')).toHaveText('Unsaved changes')
    await expect(app.getByRole('checkbox', {name: 'Select item Partial local item'})).toHaveCount(0)
    await app.getByRole('button', {name: 'Save changes', exact: true}).click()
    await expect(app.getByRole('status')).toHaveText('Payment changes saved.')
    await expect(app).toHaveURL(/\/payments\/99\/link/)
    expect(await app.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  })
}

test('Unlink releases draft capacity; editing a link retains exact quantity and amount', async ({app}) => {
  await mock(app)
  await app.goto('/payments/99/link')
  const left = app.getByRole('region', {name: 'Payment items', exact: true})
  await left.getByRole('button', {name: 'Unlink item'}).click()
  await expect(app.getByRole('checkbox', {name: 'Select item Example local item'})).toBeEnabled()
  await app.getByRole('checkbox', {name: 'Select item Example local item'}).check()
  await app.getByRole('button', {name: 'Link selected (1)'}).click()
  await left.getByRole('button', {name: 'Edit link'}).click()
  await app.getByLabel('Link 1 amount').fill('50.25')
  await app.getByRole('button', {name: 'Apply links to draft'}).click()
  await expect(app.getByText('Unresolved $49.75', {exact: true})).toBeVisible()
})

test('An oversized selection opens explicit amount editing without truncation', async ({app}) => {
  const w = workspace(); w.payment.total_amount = '80'
  await mock(app, w); await app.goto('/payments/99/link')
  await app.getByRole('radio', {name: 'Select receipt receipt-2', exact: true}).check()
  await app.getByRole('checkbox', {name: 'Select item Partial local item'}).check()
  await app.getByRole('button', {name: 'Link selected (1)'}).click()
  await expect(app.getByLabel('Link 1 amount')).toHaveValue('40.00')
  await expect(app.getByRole('button', {name: 'Save changes'})).toBeDisabled()
  await app.getByLabel('Link 1 amount').fill('20.00')
  await app.getByRole('button', {name: 'Apply links to draft'}).click()
  await expect(app.getByText('Unresolved $0.00', {exact: true})).toBeVisible()
})

test('Evidence loading can be retried and unrelated receipts can be explicitly added', async ({app}) => {
  const w = await mock(app)
  let calls = 0
  await app.route('**/api/payments/99/linking-workspace', r => ++calls <= 2
    ? r.fulfill({status: 503, json: {detail: 'Evidence temporarily unavailable'}}) : r.fulfill({json: w}))
  await app.goto('/payments/99/link')
  await expect(app.getByRole('alert')).toContainText('Evidence temporarily unavailable')
  await app.getByRole('button', {name: 'Retry loading payment'}).click()
  await expect(app.getByRole('heading', {name: 'Payment payment-1', exact: true})).toBeVisible()
  await expect(app.getByRole('radio', {name: 'Select receipt receipt-extra', exact: true})).toHaveCount(0)
  await app.getByLabel('Add group receipt', {exact: true}).selectOption('6')
  await expect(app.getByRole('radio', {name: 'Select receipt receipt-extra', exact: true})).toBeChecked()
})

test('Historical amounts require a written explanation and save with the version', async ({app}) => {
  const w = await mock(app)
  await app.route('**/api/payments/99/allocations', async r => {
    const body = r.request().postDataJSON()
    expect(body.version).toBe(1); expect(body.historical_amount).toBe('40')
    expect(body.allocations).toEqual(w.allocations); expect(body.note).toBe('Purchases from 2025')
    w.payment.allocation_breakdown.historical_amount = '40'; w.payment.allocation_details.note = body.note
    w.payment.allocation_version++
    await r.fulfill({json: {allocation_version: 2}})
  })
  await app.goto('/payments/99/link')
  await app.getByText('Other adjustments', {exact: true}).click()
  await app.getByLabel('Historical amount', {exact: true}).fill('40')
  await expect(app.getByRole('button', {name: 'Save changes'})).toBeDisabled()
  await app.getByLabel('Adjustment explanation').fill('Purchases from 2025')
  await app.getByRole('button', {name: 'Save changes'}).click()
  await expect(app.getByRole('status')).toHaveText('Payment changes saved.')
  await expect(app.getByText('Unresolved $0.00', {exact: true})).toBeVisible()
})

test('Reset confirms before restoring automatic matching', async ({app}) => {
  const w = await mock(app); w.payment.allocation_manual = true
  await app.route('**/api/payments/99/allocations/reset', async r => {
    expect(r.request().postDataJSON()).toEqual({version: 1})
    w.payment.allocation_manual = false; w.payment.allocation_version++
    await r.fulfill({json: {allocation_version: 2}})
  })
  await app.goto('/payments/99/link')
  await app.getByText('Other adjustments', {exact: true}).click()
  app.once('dialog', d => d.accept())
  await app.getByRole('button', {name: 'Reset to automatic matching'}).click()
  await expect(app.getByRole('status')).toHaveText('Automatic matching restored.')
})

test('Stale save keeps the draft and reload requires explicit discard', async ({app}) => {
  await mock(app)
  await app.route('**/api/payments/99/allocations', r => r.fulfill({status: 409, json: {detail: 'Payment changed. Reload before adjusting allocations.'}}))
  await app.goto('/payments/99/link')
  await app.getByRole('button', {name: 'Unlink item'}).click()
  await app.getByLabel('Adjustment explanation').fill('Keep this note')
  await app.getByRole('button', {name: 'Save changes'}).click()
  await expect(app.getByRole('alert')).toContainText('Reload')
  await expect(app.getByLabel('Adjustment explanation')).toHaveValue('Keep this note')
  await expect(app.getByRole('button', {name: 'Unlink item'})).toHaveCount(0)
  app.once('dialog', d => d.dismiss())
  await app.getByRole('button', {name: 'Reload payment'}).click()
  await expect(app.getByLabel('Adjustment explanation')).toHaveValue('Keep this note')
  app.once('dialog', d => d.accept())
  await app.getByRole('button', {name: 'Reload payment'}).click()
  await expect(app.getByRole('button', {name: 'Unlink item'})).toBeVisible()
})

test('Unsaved navigation warns on next payment, browser back and leaving the app', async ({app}) => {
  await mock(app)
  await app.goto('/payments?group=170001')
  await app.getByRole('region', {name: 'Payment payment-1', exact: true}).getByRole('link', {name: 'Open payment'}).click()
  await app.getByRole('button', {name: 'Unlink item'}).click()
  await Promise.all([
    app.waitForEvent('dialog').then(d => d.dismiss()),
    app.getByRole('link', {name: 'Next payment'}).click(),
  ])
  await expect(app).toHaveURL(/\/payments\/99\/link/)
  await Promise.all([
    app.waitForEvent('dialog').then(d => d.dismiss()),
    app.goBack(),
  ])
  await expect(app).toHaveURL(/\/payments\/99\/link/)
  await Promise.all([
    app.waitForEvent('dialog').then(async d => { expect(d.type()).toBe('beforeunload'); await d.dismiss() }),
    app.reload({timeout: 2000}).catch(() => {}),
  ])
  await expect(app.getByRole('status')).toHaveText('Unsaved changes')
  await Promise.all([
    app.waitForEvent('dialog').then(d => d.accept()),
    app.getByRole('link', {name: 'Back to payments'}).click(),
  ])
  await expect(app).toHaveURL(/\/payments\?group=170001$/)
})

test('Failed sync retains the imported ledger and legacy totals stay separate', async ({app}) => {
  await mock(app)
  await app.route('**/api/payments', r => r.fulfill({json: [fixture(), {...fixture(100), ledger_state: 'legacy', total_amount: 500}]}))
  await app.route('**/api/payment-review/groups/170001/refresh', r => r.fulfill({status: 502, json: {detail: 'Previous evidence was preserved.'}}))
  await app.goto('/payments/review?group=170001')
  await app.getByRole('button', {name: 'Sync now'}).click()
  await expect(app.getByRole('alert')).toContainText('Previous evidence was preserved')
  await expect(app.getByText('Source total $100.00')).toBeVisible()
  await app.getByLabel('Payment view').selectOption('legacy')
  await expect(app.getByRole('region', {name: 'Payment payment-2', exact: true})).toBeVisible()
  await expect(app.getByRole('link', {name: 'Open payment', exact: true})).toHaveCount(0)
})


test('Draft capacities retain installment units and signed corrections with exact cents', () => {
  const w = workspace()
  const item = w.items[1]
  expect(availableQuantity(item, 4, [])).toBe(1)
  expect(availableQuantity(item, 6, [])).toBe(0)
  expect(remaining(item, [])).toBe(4000)
  item.correction_amount = '-5.25'
  expect(remaining(item, [])).toBe(4525)
  const partial = {receipt_source_id: 4, item_id: 5, quantity: 1, amount: '20.00'}
  expect(validate(w, [...w.allocations, partial], '0', '0')).toBeNull()
  expect(availableQuantity(item, 4, [partial])).toBe(1)
  expect(validate(w, [...w.allocations, partial, partial], '0', '0')).toContain('already linked')
  expect(validate(w, [...w.allocations, {...partial, quantity: 2}], '0', '0')).toContain('positive quantity')
  expect(validate(w, [...w.allocations, {...partial, receipt_source_id: 6}], '0', '0')).toContain('remaining balance')
  expect(cents('0.10') + cents('0.20')).toBe(30)
  expect(decimal(cents('-2.01'))).toBe('-2.01')
  expect(Number.isNaN(cents('1.001'))).toBeTruthy()
  expect(Number.isNaN(cents(''))).toBeTruthy()
})


test('Preserved links without receipt evidence require explicit assignment or unlink', async ({app}) => {
  const w = workspace()
  w.allocations = []
  w.unassigned_links = [{item_id: 3, amount: '60.00', quantity: 1}]
  await mock(app, w); await app.goto('/payments/99/link')
  await expect(app.getByText('Linked items $60.00', {exact: true})).toBeVisible()
  await expect(app.getByRole('checkbox', {name: 'Select item Example local item'})).toHaveCount(0)
  await app.getByRole('button', {name: 'Assign to selected receipt'}).click()
  await app.getByRole('button', {name: 'Apply links to draft'}).click()
  await expect(app.getByRole('button', {name: 'Unlink item'})).toBeVisible()
  await expect(app.getByRole('button', {name: 'Save changes'})).toBeEnabled()
})


test('Previous and next payment preserve the overview filters and replace the workspace', async ({app}) => {
  await mock(app)
  await app.goto('/payments/99/link?group=170001&view=attention')
  await app.getByRole('link', {name: 'Next payment'}).click()
  await expect(app.getByRole('heading', {name: 'Payment payment-2', exact: true})).toBeVisible()
  await expect(app.getByRole('heading', {name: 'Payment payment-1', exact: true})).toHaveCount(0)
  await expect(app).toHaveURL(/\/payments\/100\/link\?group=170001&view=attention$/)
  await app.getByRole('link', {name: 'Previous payment'}).click()
  await expect(app.getByRole('heading', {name: 'Payment payment-1', exact: true})).toBeVisible()
})
