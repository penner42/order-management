import { test, expect } from './fixtures'

function fixture() {
  return {
    id: 99, buying_group_id: 170001, payment_id: 'payment-1', buying_group: {id: 170001, name: 'Example Parsefile'},
    ledger_state: 'imported', source_amount: 100, total_amount: 100, allocation_version: 1, allocation_manual: false,
    payment_requested_at: '2026-01-05T12:00:00Z', payment_sent_at: '2026-01-08T12:00:00Z', payment_received_at: null,
    allocation_details: {note: ''},
    allocation_breakdown: {linked_amount: '60', correction_amount: '0', historical_amount: '0', adjustment_amount: '0', unresolved_amount: '40'},
    source_details: {provider: 'parsefile', external_id: 'payment-1', present: true, source: {status: 'PAID', void: false},
      receipts: [{source_id: 2, item_id: 3, amount: '60', quantity: 1, snapshot: {external_id: 'receipt-1',description: 'Example item'}}]},
    sync_issues: ['Item allocations do not yet reconcile to the source payment.'],
    line_items: [{id: 1, item_id: 3, amount: '60', item: {description: 'Example local item',order_id: 4}}],
    monarch_receipt: null, receipt_conflict: false,
  }
}
const capability = {buying_group_id: 170001, name: 'Example Parsefile', ready: true, supported: true, reason: null,
  payment_sync_enabled: true, last_run_at: '2026-10-27T12:00:00Z', error: null}
const evidence = {
  receipts: [{id: 2, external_id: 'receipt-1',present: true,payload: {description: 'Example receipt',amount: '60',quantity: 1,void: false}}],
  items: [{id: 3,label: 'Example local item',quantity: 1,order_id: 4,remaining: '0'}],
}

for (const width of [1280,390]) {
  test(`Imported payments allow explained historical allocation at width ${width}`,async ({app}) => {
    await app.setViewportSize({width,height: 900})
    const payment = fixture()
    await app.route('**/api/payments',route => route.fulfill({json:[payment]}))
    await app.route('**/api/payment-review/capabilities',route => route.fulfill({json:[capability]}))
    await app.route('**/api/payment-review/groups/170001',route => route.fulfill({json:evidence}))
    await app.route('**/api/payments/99/allocations',async route => {
      const body = route.request().postDataJSON()
      expect(body.version).toBe(1)
      expect(body.historical_amount).toBe('40')
      expect(body.allocations).toEqual([{receipt_source_id:2,item_id:3,quantity:1,amount:'60'}])
      expect(body.note).toBe('Purchases from 2025')
      payment.allocation_version++
      payment.allocation_manual=true
      payment.allocation_breakdown.historical_amount='40';payment.allocation_breakdown.unresolved_amount='0'
      payment.sync_issues=[];payment.allocation_details.note=body.note
      await route.fulfill({json:payment})
    })
    await app.goto('/payments/review?group=170001')
    await expect(app.getByRole('heading',{name:'Payments',exact:true})).toBeVisible()
    await expect(app.getByRole('button',{name:'Add payment',exact:true})).toHaveCount(0)
    await expect(app.getByText('Source total $100.00')).toBeVisible()
    await app.getByRole('button',{name:'Adjust allocations'}).click()
    await app.getByLabel('Historical amount',{exact:true}).fill('40')
    await app.getByLabel('Adjustment explanation').fill('Purchases from 2025')
    await app.getByRole('button',{name:'Save adjustments'}).click()
    await expect(app.getByText('Manual allocation edits retained: Purchases from 2025')).toBeVisible()
    await expect(app.getByText(/Unresolved \$0.00/)).toBeVisible()
  })
}

test('Failed sync retains the imported ledger', async ({app}) => {
  await app.route('**/api/payments',r=>r.fulfill({json:[fixture()]}))
  await app.route('**/api/payment-review/capabilities',r=>r.fulfill({json:[capability]}))
  await app.route('**/api/payment-review/groups/170001/refresh',r=>r.fulfill({status:502,json:{detail:'Previous evidence was preserved.'}}))
  await app.goto('/payments?group=170001')
  await app.getByRole('button',{name:'Sync now'}).click()
  await expect(app.getByRole('alert')).toContainText('Previous evidence was preserved')
  await expect(app.getByText('Source total $100.00')).toBeVisible()
})

test('Reset uses the displayed allocation version', async ({app}) => {
  const payment=fixture();payment.allocation_manual=true
  await app.route('**/api/payments',r=>r.fulfill({json:[payment]}))
  await app.route('**/api/payment-review/capabilities',r=>r.fulfill({json:[capability]}))
  await app.route('**/api/payment-review/groups/170001',r=>r.fulfill({json:evidence}))
  await app.route('**/api/payments/99/allocations/reset',async r=>{
    expect(r.request().postDataJSON()).toEqual({version:1})
    payment.allocation_manual=false;payment.allocation_version++
    await r.fulfill({json:payment})
  })
  await app.goto('/payments?group=170001')
  await app.getByRole('button',{name:'Adjust allocations'}).click()
  await app.getByRole('button',{name:'Reset to automatic matching'}).click()
  await expect(app.getByText(/Manual allocation edits retained/)).toHaveCount(0)
})

test('Legacy records have a separate view and do not inflate source totals', async ({app}) => {
  await app.route('**/api/payments',r=>r.fulfill({json:[fixture(),{...fixture(),id:100,payment_id:'legacy',ledger_state:'legacy',total_amount:500}]}))
  await app.route('**/api/payment-review/capabilities',r=>r.fulfill({json:[capability]}))
  await app.goto('/payments?group=170001')
  await expect(app.getByText('Source total $100.00')).toBeVisible()
  await expect(app.getByRole('region',{name:'Payment legacy',exact:true})).toHaveCount(0)
  await app.getByLabel('Payment view').selectOption('legacy')
  await expect(app.getByRole('region',{name:'Payment legacy',exact:true})).toBeVisible()
  await expect(app.getByText('Source total $100.00')).toBeVisible()
  await expect(app.getByRole('button',{name:'Adjust allocations'})).toHaveCount(0)
})

test('Stale allocation errors keep the unsaved form visible', async ({app}) => {
  await app.route('**/api/payments',r=>r.fulfill({json:[fixture()]}))
  await app.route('**/api/payment-review/capabilities',r=>r.fulfill({json:[capability]}))
  await app.route('**/api/payment-review/groups/170001',r=>r.fulfill({json:evidence}))
  await app.route('**/api/payments/99/allocations',r=>r.fulfill({status:409,json:{detail:'Payment changed. Reload before adjusting allocations.'}}))
  await app.goto('/payments')
  await app.getByRole('button',{name:'Adjust allocations'}).click()
  await app.getByLabel('Adjustment explanation').fill('Keep this note')
  await app.getByRole('button',{name:'Save adjustments'}).click()
  await expect(app.getByRole('alert')).toContainText('Reload')
  await expect(app.getByLabel('Adjustment explanation')).toHaveValue('Keep this note')
})
